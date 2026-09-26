"""Accounts and sessions: registration, login, logout, and acting only as yourself."""

import httpx
import pytest

from backend.auth import COOKIE, hash_password, verify_password
from backend.main import SIGNUP_GRANT
from backend.tests.conftest import make_definition, make_player, make_user, mint


@pytest.fixture
def browser(base_url):
    """A client with its own cookie jar, like one person's browser."""
    with httpx.Client(base_url=base_url, timeout=30) as client:
        yield client


def register(client, name, password="correct horse battery"):
    return client.post("/auth/register", json={"username": name, "password": password})


def test_register_logs_you_in_with_the_signup_grant(browser, db):
    r = register(browser, "alice")
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "alice" and float(r.json()["balance"]) == float(SIGNUP_GRANT)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert browser.get("/auth/me").json()["username"] == "alice"
    ledger = db.execute("SELECT reason, delta FROM currency_ledger").fetchall()
    assert [(row["reason"], float(row["delta"])) for row in ledger] == [("MINT_SIGNUP", float(SIGNUP_GRANT))]


def test_registration_rules(browser):
    assert register(browser, "al").status_code == 400             # too short
    assert register(browser, "bad name!").status_code == 400      # characters
    assert register(browser, "alice", "short").status_code == 400  # password length
    assert register(browser, "alice").status_code == 200
    assert register(browser, "ALICE").status_code == 409           # names are case-insensitive


def test_login_logout_and_errors(base_url, browser, db):
    register(browser, "alice")
    browser.post("/auth/logout")
    assert browser.get("/auth/me").status_code == 401

    wrong = browser.post("/auth/login", json={"username": "alice", "password": "nope-nope-nope"})
    unknown = browser.post("/auth/login", json={"username": "nobody", "password": "nope-nope-nope"})
    # Same answer either way: no telling which usernames exist.
    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json()) == (401, {"detail": "Wrong username or password"})

    assert browser.post("/auth/login", json={"username": "Alice", "password": "correct horse battery"}).status_code == 200
    assert browser.get("/auth/me").json()["username"] == "alice"

    # Expired sessions stop working.
    db.execute("UPDATE sessions SET expires_at = now() - interval '1 second'")
    assert browser.get("/auth/me").status_code == 401


def test_passwords_are_hashed():
    stored = hash_password("correct horse battery")
    assert "correct horse" not in stored and stored.startswith("scrypt$")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("anything", None)


def test_you_can_only_act_as_yourself(api, base_url, db):
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    definition = make_definition(db, make_player(db, "Contested"))
    card = mint(api, definition, alice)
    listing = api.post("/listings", json={"card_instance_id": card, "seller_id": alice, "price": "10"}).json()["id"]

    # A browser logged in as Bob tries to act for Alice.
    with httpx.Client(base_url=base_url, timeout=30) as bobs_browser:
        bobs_browser.post("/auth/login", json={"username": "bob", "password": "test-password-123"})
        attempts = [
            bobs_browser.post("/packs/open", json={"user_id": alice, "pack_type": "standard"}),
            bobs_browser.post(f"/listings/{listing}/cancel", params={"seller_id": alice}),
            bobs_browser.post("/listings", json={"card_instance_id": card, "seller_id": alice, "price": "1"}),
            bobs_browser.get(f"/users/{alice}/battles"),
            bobs_browser.post("/battles", json={"challenger_id": alice, "opponent_id": bob, "card_ids": [card] * 6}),
        ]
        assert [a.status_code for a in attempts] == [403] * len(attempts), [a.text for a in attempts]
        # And buying as himself works.
        assert bobs_browser.post(f"/listings/{listing}/buy", params={"buyer_id": bob}).status_code == 200

    # No session at all: 401 on anything that acts.
    with httpx.Client(base_url=base_url, timeout=30) as anonymous:
        assert anonymous.post("/packs/open", json={"user_id": alice, "pack_type": "standard"}).status_code == 401
        assert anonymous.get("/battles/1", params={"user_id": alice}).status_code == 401
        # Browsing stays public.
        assert anonymous.get("/marketplace").status_code == 200


def test_public_user_list_has_no_emails(api):
    make_user(api, "alice")
    users = api.get("/users").json()
    assert users and all(set(u) == {"id", "username", "balance"} for u in users)
