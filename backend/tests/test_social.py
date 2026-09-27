"""Pull feed, profiles, showcases, page views and the playtest report."""

import httpx

from backend.scripts.playtest_report import build_report
from backend.tests.conftest import grant, make_definition, make_player, make_user, mint
from backend.tests.sessions import session_cookie


def only_active(db, *definition_ids):
    db.execute("UPDATE card_definitions SET is_active = (id = ANY(%s))", (list(definition_ids),))


def open_pack(api, user):
    r = api.post("/packs/open", json={"user_id": user, "pack_type": "standard"})
    assert r.status_code == 200, r.text
    return r.json()["cards"]


def test_feed_shows_only_notable_pulls_newest_first(api, db):
    user = make_user(api, "rohan")
    grant(api, user, 5000)

    common = make_definition(db, make_player(db, "Common Guy"), "Common", 500)
    only_active(db, common)
    open_pack(api, user)                        # serials 1, 2, 3: only #1 is notable

    epic = make_definition(db, make_player(db, "Epic Guy"), "Epic", 25)
    only_active(db, epic)
    open_pack(api, user)                        # three Epic pulls: all notable

    short = make_definition(db, make_player(db, "Short Run"), "Common", 3)
    only_active(db, short)
    open_pack(api, user)                        # serials 1, 2, 3 of 3: #1 and the last

    feed = api.get("/feed").json()
    seen = [(f["player_name"], f["serial_number"]) for f in feed]
    assert sorted(seen) == sorted([
        ("Common Guy", 1),
        ("Epic Guy", 1), ("Epic Guy", 2), ("Epic Guy", 3),
        ("Short Run", 1), ("Short Run", 3),
    ])
    assert seen[0][0] == "Short Run"  # newest first
    assert all(set(f) >= {"username", "player_name", "rarity", "serial_number", "max_supply", "created_at", "card_instance_id"} for f in feed)
    assert all(f["username"] == "rohan" for f in feed)
    assert "email" not in str(feed)


def test_profile_counts_record_and_showcase(api, base_url, db):
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    rare = make_definition(db, make_player(db, "Rare One"), "Rare", 100)
    common = make_definition(db, make_player(db, "Common One"), "Common", 500)
    cards = [mint(api, rare, alice), mint(api, common, alice), mint(api, common, alice)]

    # Battle record: 1 win, 1 loss by forfeit, 1 draw.
    for status, winner in (("FINISHED", alice), ("FORFEIT", bob), ("FINISHED", None)):
        db.execute(
            """
            INSERT INTO battles (challenger_id, opponent_id, status, challenger_card_ids, expires_at, winner_id, finished_at)
            VALUES (%s, %s, %s, '{}', now(), %s, now())
            """,
            (alice, bob, status, winner),
        )

    # Showcase rules.
    put = lambda user, ids: httpx.put(f"{base_url}/me/showcase", json={"card_ids": ids}, cookies=session_cookie(user))
    assert put(alice, cards[:2]).status_code == 200
    assert put(alice, cards[:1] * 2).status_code == 400                           # duplicates
    assert put(alice, cards + [999, 998, 997]).status_code == 400                 # more than 5
    assert put(bob, [cards[0]]).status_code == 403                                # not Bob's card
    assert httpx.put(f"{base_url}/me/showcase", json={"card_ids": []}).status_code == 401

    profile = api.get("/profiles/ALICE").json()  # case-insensitive lookup
    assert profile["username"] == "alice"
    assert profile["collection"] == {"total": 3, "Common": 2, "Rare": 1, "Epic": 0, "Legendary": 0}
    assert profile["battles"] == {"wins": 1, "losses": 1, "draws": 1}
    assert [c["card_instance_id"] for c in profile["showcase"]] == cards[:2]
    assert "email" not in str(profile)

    # A pinned card that leaves Alice's hands drops off her showcase.
    listing = api.post("/listings", json={"card_instance_id": cards[0], "seller_id": alice, "price": "5"}).json()["id"]
    assert api.post(f"/listings/{listing}/buy", params={"buyer_id": bob}).status_code == 200
    assert [c["card_instance_id"] for c in api.get("/profiles/alice").json()["showcase"]] == [cards[1]]
    assert api.get("/profiles/nobody").status_code == 404


def test_page_views_one_row_per_user_per_day(api, base_url, db):
    alice = make_user(api, "alice")
    for _ in range(3):
        assert httpx.post(f"{base_url}/me/visit", cookies=session_cookie(alice)).status_code == 200
    assert httpx.post(f"{base_url}/me/visit").status_code == 401
    rows = db.execute("SELECT user_id, views FROM page_views").fetchall()
    assert [(r["user_id"], r["views"]) for r in rows] == [(alice, 3)]


def test_playtest_report(api, db):
    alice, bob, carol = make_user(api, "alice"), make_user(api, "bob"), make_user(api, "carol")
    make_definition(db, make_player(db, "Filler"), "Common", 500)
    for _ in range(2):
        open_pack(api, alice)

    # Alice: a battle finished at 10:00 and another started at 10:20 -> rematch.
    # Bob: in both. Carol: one battle, next one started 2 hours later -> no rematch.
    def battle(a, b, started, finished):
        db.execute(
            """
            INSERT INTO battles (challenger_id, opponent_id, status, challenger_card_ids, expires_at,
                                 created_at, started_at, finished_at)
            VALUES (%s, %s, 'FINISHED', '{}', now(), %s, %s, %s)
            """,
            (a, b, started, started, finished),
        )
    battle(alice, bob, "2026-09-20 09:50Z", "2026-09-20 10:00Z")
    battle(alice, bob, "2026-09-20 10:20Z", "2026-09-20 10:30Z")
    battle(carol, bob, "2026-09-21 09:00Z", "2026-09-21 09:10Z")
    battle(carol, bob, "2026-09-21 11:10Z", "2026-09-21 11:20Z")
    # Carol came back on a later day.
    db.execute("INSERT INTO page_views (user_id, day) VALUES (%s, '2026-09-25')", (carol,))

    rows = {r["username"]: r for r in build_report(db)}
    assert rows["alice"]["packs_opened"] == 2 and rows["bob"]["packs_opened"] == 0
    assert (rows["alice"]["battles_played"], rows["bob"]["battles_played"], rows["carol"]["battles_played"]) == (2, 4, 2)
    assert rows["alice"]["rematched"] and rows["bob"]["rematched"]
    assert not rows["carol"]["rematched"]  # 2 hours later is outside the 30-minute window
    assert rows["carol"]["returned"] and rows["carol"]["days_active"] >= 3
    assert rows["alice"]["returned"]       # signed up today, battled on 2026-09-20
