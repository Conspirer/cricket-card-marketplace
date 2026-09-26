"""Test harness: a throwaway database built from schema.sql, and the real app
served by uvicorn in a background thread so concurrency tests make genuinely
parallel HTTP requests."""

import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://cricket:cricket_dev_password@localhost:5432/cricket_test",
)
# Must be set before backend.database is imported: the pool reads it once.
os.environ["DATABASE_URL"] = TEST_DB_URL

SCHEMA = Path(__file__).resolve().parents[1] / "schema.sql"

def _recreate_database():
    name = psycopg.conninfo.conninfo_to_dict(TEST_DB_URL)["dbname"]
    admin = psycopg.conninfo.make_conninfo(TEST_DB_URL, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute(SCHEMA.read_text())


_recreate_database()

import uvicorn  # noqa: E402
from backend.main import app  # noqa: E402


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def api(base_url):
    with httpx.Client(base_url=base_url, timeout=30) as client:
        yield client


@pytest.fixture
def db():
    with psycopg.connect(TEST_DB_URL, row_factory=dict_row, autocommit=True) as conn:
        yield conn


@pytest.fixture(autouse=True)
def clean_and_check(db):
    """Every test starts empty and must leave every invariant intact."""
    tables = [r["tablename"] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    db.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
    yield
    assert_invariants(db)


def assert_invariants(db):
    ledger = db.execute(
        """
        SELECT u.id, u.balance, COALESCE(SUM(l.delta), 0) AS total
        FROM users u LEFT JOIN currency_ledger l ON l.user_id = u.id
        GROUP BY u.id HAVING u.balance <> COALESCE(SUM(l.delta), 0)
        """
    ).fetchall()
    assert not ledger, f"ledger sum != balance: {ledger}"

    owners = db.execute(
        """
        SELECT i.id, i.owner_id, last.to_user_id
        FROM card_instances i
        LEFT JOIN LATERAL (
            SELECT to_user_id FROM card_ownership_events e
            WHERE e.card_instance_id = i.id AND e.event_type IN ('MINTED', 'PULLED', 'SOLD')
            ORDER BY e.created_at DESC, e.id DESC LIMIT 1
        ) last ON true
        WHERE i.owner_id IS DISTINCT FROM last.to_user_id
        """
    ).fetchall()
    assert not owners, f"owner != latest event: {owners}"

    serials = db.execute(
        """
        SELECT d.id, d.minted_count, count(i.id) AS n, count(DISTINCT i.serial_number) AS distinct_serials,
               COALESCE(max(i.serial_number), 0) AS max_serial
        FROM card_definitions d LEFT JOIN card_instances i ON i.card_definition_id = d.id
        GROUP BY d.id
        HAVING NOT (d.minted_count = count(i.id)
                    AND count(i.id) = count(DISTINCT i.serial_number)
                    AND COALESCE(max(i.serial_number), 0) = d.minted_count)
        """
    ).fetchall()
    assert not serials, f"serials not unique and gap-free: {serials}"

    transfers = db.execute(
        """
        SELECT c.related_listing_id
        FROM currency_ledger c
        LEFT JOIN currency_ledger d
            ON d.related_listing_id = c.related_listing_id AND d.reason = 'TRANSFER_PURCHASE_DEBIT'
        WHERE c.reason = 'TRANSFER_SALE_CREDIT' AND (d.id IS NULL OR d.delta <> -c.delta)
        """
    ).fetchall()
    assert not transfers, f"unmatched transfer pairs: {transfers}"


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def make_user(api, name):
    r = api.post("/users", json={"username": name, "email": f"{name}@test.local"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def grant(api, user_id, amount):
    r = api.post(f"/dev/users/{user_id}/grant", json={"amount": str(amount)})
    assert r.status_code == 200, r.text


def make_player(db, name, role="Batter", country="India"):
    return db.execute(
        "INSERT INTO players (name, country, role) VALUES (%s, %s, %s) RETURNING id",
        (name, country, role),
    ).fetchone()["id"]


def make_definition(db, player_id, rarity="Common", max_supply=500, active=True):
    return db.execute(
        """
        INSERT INTO card_definitions (player_id, rarity, max_supply, is_active)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (player_id, rarity, max_supply, active),
    ).fetchone()["id"]


def mint(api, definition_id, owner_id):
    r = api.post("/card-instances", json={"card_definition_id": definition_id, "owner_id": owner_id})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def make_pool(db, per_rarity=None):
    """A small active pool across all four rarities."""
    per_rarity = per_rarity or {"Common": 6, "Rare": 3, "Epic": 2, "Legendary": 1}
    supply = {"Common": 500, "Rare": 100, "Epic": 25, "Legendary": 10}
    ids = []
    for rarity, n in per_rarity.items():
        for i in range(n):
            player = make_player(db, f"{rarity} Player {i}")
            ids.append(make_definition(db, player, rarity, supply[rarity]))
    return ids


def run_parallel(calls):
    """Run zero-arg callables at the same moment; return their results in order."""
    barrier = threading.Barrier(len(calls))

    def go(fn):
        barrier.wait()
        return fn()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(go, calls))
