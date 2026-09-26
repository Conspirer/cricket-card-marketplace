"""scripts/reset_economy: refuses without --confirm, wipes the economy, keeps the catalogue."""

import os
import subprocess
import sys
from pathlib import Path

from psycopg.types.json import Jsonb

from backend.tests.conftest import TEST_DB_URL, grant, make_pool, make_user, mint

REPO = Path(__file__).resolve().parents[2]
EMPTIED = ["users", "sessions", "card_instances", "listings", "pack_openings", "currency_ledger",
           "card_ownership_events", "battles", "battle_moves", "battle_rounds"]


def reset(*args):
    return subprocess.run(
        [sys.executable, "-m", "backend.scripts.reset_economy", *args], cwd=REPO,
        env={**os.environ, "DATABASE_URL": TEST_DB_URL}, capture_output=True, text=True, timeout=60,
    )


def count(db, table):
    return db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_reset_economy(api, db):
    # An economy with every kind of row: users, pulls, a sale, a battle.
    make_pool(db, {"Common": 12, "Rare": 2, "Epic": 1, "Legendary": 1})
    db.execute(
        """
        INSERT INTO player_theme_stats (player_id, theme, stats, matches, source, verified)
        SELECT id, 'IPL', %s, 20, 'test', true FROM players
        """,
        (Jsonb({"runs": 100, "strike_rate": 130.0, "sixes": 5, "wickets": 3, "economy": 8.1, "catches": 4}),),
    )
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    for u in (alice, bob):
        grant(api, u, 5000)
        for _ in range(4):
            assert api.post("/packs/open", json={"user_id": u, "pack_type": "standard"}).status_code == 200
    card = api.get(f"/users/{alice}/cards").json()[0]["id"]
    listing = api.post("/listings", json={"card_instance_id": card, "seller_id": alice, "price": "50"}).json()["id"]
    assert api.post(f"/listings/{listing}/buy", params={"buyer_id": bob}).status_code == 200
    # Battle decks from minted cards: pulled cards are random and may not give
    # each player six 10-credit cards, which made this test flaky.
    commons = [r["id"] for r in db.execute(
        "SELECT d.id FROM card_definitions d WHERE d.rarity = 'Common' ORDER BY d.id").fetchall()]
    decks = {u: [mint(api, commons[i], u) for i in range(6)] for u in (alice, bob)}
    battle = api.post("/battles", json={"challenger_id": alice, "opponent_id": bob, "card_ids": decks[alice]}).json()["id"]
    assert api.post(f"/battles/{battle}/accept", json={"user_id": bob, "card_ids": decks[bob]}).status_code == 200

    before = {t: count(db, t) for t in EMPTIED}
    assert all(before[t] > 0 for t in ("users", "card_instances", "listings", "pack_openings",
                                       "currency_ledger", "card_ownership_events", "battles"))
    kept = {t: count(db, t) for t in ("players", "player_theme_stats", "card_definitions")}
    active = count(db, "card_definitions WHERE is_active")

    # Without --confirm: report only.
    dry = reset()
    assert dry.returncode == 1 and "pass --confirm" in dry.stdout
    assert {t: count(db, t) for t in EMPTIED} == before

    done = reset("--confirm")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "audit: all invariants hold" in done.stdout
    assert all(count(db, t) == 0 for t in EMPTIED)
    assert {t: count(db, t) for t in kept} == kept
    assert count(db, "card_definitions WHERE is_active") == active
    assert count(db, "card_definitions WHERE minted_count <> 0") == 0

    # A fresh start: ids and serials begin again.
    first = make_user(api, "newcomer")
    assert first == 1
    grant(api, first, 1000)
    pulled = api.post("/packs/open", json={"user_id": first, "pack_type": "standard"}).json()["cards"]
    assert all(c["serial_number"] == 1 for c in pulled if sum(p["card_definition_id"] == c["card_definition_id"] for p in pulled) == 1)
