"""Production plumbing: password gate, HTTPS redirect, SPA serving, faucet
switch and the migration runner."""

import base64
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tests.conftest import TEST_DB_URL
from backend.web import build_app

REPO = Path(__file__).resolve().parents[2]


def tiny_api():
    api = FastAPI()

    @api.get("/battles/{battle_id}")
    def battle(battle_id: int):
        return {"battle": battle_id}

    return api


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root>shell</div>")
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log('app')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    return tmp_path


def basic(password, user="anyone"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_password_gate_covers_api_and_frontend(dist):
    client = TestClient(build_app(tiny_api(), password="open-sesame", dist=dist, force_https=False))
    for path in ("/", "/battles/3", "/api/battles/3", "/assets/app-1a2b.js"):
        r = client.get(path)
        assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"], path
        assert client.get(path, headers=basic("wrong")).status_code == 401
        assert client.get(path, headers=basic("open-sesame")).status_code == 200, path
    assert client.get("/", headers={"Authorization": "Basic !!!not-base64"}).status_code == 401


def test_no_password_means_open(dist):
    client = TestClient(build_app(tiny_api(), password="", dist=dist, force_https=False))
    assert client.get("/api/battles/3").json() == {"battle": 3}


def test_frontend_routes_get_the_shell_and_api_stays_json(dist):
    client = TestClient(build_app(tiny_api(), password="", dist=dist, force_https=False))
    # Same path shape on both sides: /battles/3 is a React route, /api/battles/3 the API.
    assert "shell" in client.get("/battles/3").text
    assert client.get("/battles/3").headers["cache-control"] == "no-cache"
    assert client.get("/api/battles/3").json() == {"battle": 3}
    assert "console.log" in client.get("/assets/app-1a2b.js").text
    assert client.get("/favicon.svg").text == "<svg/>"
    assert "shell" in client.get("/../../etc/passwd").text  # no escaping the dist folder


def test_https_redirect_before_password(dist):
    client = TestClient(build_app(tiny_api(), password="pw", dist=dist, force_https=True))
    r = client.get("/market", headers={"X-Forwarded-Proto": "http"}, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"].startswith("https://")
    assert client.get("/market", headers={"X-Forwarded-Proto": "https", **basic("pw")}).status_code == 200


def run(code, **env):
    full = {**os.environ, "DATABASE_URL": TEST_DB_URL, **env}
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=full, capture_output=True, text=True, timeout=60)


def test_dev_faucet_absent_unless_enabled():
    code = ("from backend.main import api; "
            "print(any(getattr(r, 'path', '') == '/dev/users/{user_id}/grant' for r in api.routes))")
    assert run(code, DEV_FAUCET_ENABLED="").stdout.strip() == "False"
    assert run(code, DEV_FAUCET_ENABLED="1").stdout.strip() == "True"


def test_database_url_is_required():
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    r = subprocess.run([sys.executable, "-c", "import backend.database"], cwd=REPO, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "DATABASE_URL is not set" in r.stderr


def test_migrate_builds_fresh_database_and_is_idempotent():
    name = "cricket_migrate_test"
    admin = psycopg.conninfo.make_conninfo(TEST_DB_URL, dbname="postgres")
    url = psycopg.conninfo.make_conninfo(TEST_DB_URL, dbname=name)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    migrate = lambda *args: subprocess.run(
        [sys.executable, "-m", "backend.scripts.migrate", *args], cwd=REPO,
        env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True, timeout=60,
    )
    try:
        first = migrate()
        assert first.returncode == 0 and "fresh database" in first.stdout, first.stderr
        again = migrate()
        assert again.returncode == 0 and "up to date" in again.stdout
        migrations = sorted(p.stem for p in (REPO / "backend" / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
        with psycopg.connect(url) as conn:
            recorded = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
            tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
            # Forget the history: an existing DB without it must not be guessed at.
            conn.execute("DELETE FROM schema_migrations")
        assert recorded == migrations
        assert {"users", "battles", "battle_rounds", "player_theme_stats"} <= tables
        refused = migrate()
        assert refused.returncode == 1 and "--baseline" in refused.stderr
        assert migrate("--baseline").returncode == 0
        assert "up to date" in migrate().stdout
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_healthz_is_open_behind_the_password(dist):
    client = TestClient(build_app(tiny_api(), password="pw", dist=dist, force_https=True))
    r = client.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"
    assert client.get("/api/battles/3").status_code == 401  # nothing else is


def test_no_redirect_without_a_proxy_header(dist):
    # Local requests and the host's own health checks carry no X-Forwarded-Proto.
    client = TestClient(build_app(tiny_api(), password="", dist=dist, force_https=True))
    assert client.get("/api/battles/3", follow_redirects=False).status_code == 200


def test_connection_options_for_neon_and_local(monkeypatch):
    from backend.database import connection_options
    monkeypatch.delenv("DATABASE_SSLMODE", raising=False)
    neon = "postgresql://u:p@ep-cool-name-123456.eu-central-1.aws.neon.tech/crease"
    assert connection_options(neon) == {"sslmode": "require"}
    assert connection_options(neon + "?sslmode=verify-full") == {}  # the URL's own choice wins
    pooled = "postgresql://u:p@ep-cool-name-123456-pooler.eu-central-1.aws.neon.tech/crease?sslmode=require"
    assert connection_options(pooled) == {"prepare_threshold": None}
    assert connection_options("postgresql://cricket:x@localhost:5432/cricket_marketplace") == {"sslmode": "prefer"}
    monkeypatch.setenv("DATABASE_SSLMODE", "disable")
    assert connection_options(neon) == {"sslmode": "disable"}
