"""Career tiers, overrides that stick, credits from the final rarity, and the season reset."""

import httpx
import pytest
from psycopg.types.json import Jsonb

from backend import tiering
from backend.scripts.build_card_pool import build_pool, load_overrides
from backend.scripts.reset_season import RESET_GRANT, reset
from backend.tests.conftest import grant, make_definition, make_player, make_user, mint
from backend.tests.sessions import session_cookie

GREAT_BATTER = {"TEST": {"runs": 12000, "batting_average": 55.0}, "ODI": {"runs": 10000, "batting_average": 45.0}}
AVERAGE_BOWLER = {"TEST": {"runs": 400, "wickets": 120, "bowling_average": 32.0},
                  "ODI": {"runs": 200, "wickets": 90, "economy": 5.3}}


def test_career_score_ranks_greats_above_average_players():
    assert tiering.career_score(GREAT_BATTER) > 2 * tiering.career_score(AVERAGE_BOWLER)


def test_test_and_odi_weigh_at_least_as_much_as_t20i():
    for fmt in ("TEST", "ODI"):
        assert tiering.FORMAT_WEIGHT[fmt] >= tiering.FORMAT_WEIGHT["T20I"] >= tiering.FORMAT_WEIGHT["IPL"]
    same = {"runs": 2000, "batting_average": 30.0}
    # The same raw record is worth more in the longer formats' weighting.
    assert tiering.career_score({"TEST": same}) < tiering.career_score({"T20I": same}) / tiering.FORMAT_WEIGHT["T20I"] * 1.0 + 1


def test_quality_is_clamped_and_missing_quality_is_neutral():
    huge = tiering.format_score("TEST", {"runs": 5000, "batting_average": 400.0})[0]
    assert huge == pytest.approx(5000 / 5000 * tiering.QUALITY_MAX)
    neutral = tiering.format_score("TEST", {"runs": 5000, "batting_average": None})[0]
    assert neutral == pytest.approx(1.0)


def test_assign_tiers_sizes_and_overrides():
    scores = {pid: 100 - pid for pid in range(50)}
    final, formula, warnings = tiering.assign_tiers(scores)
    counts = {r: sum(1 for v in final.values() if v == r) for r in tiering.RARITIES}
    assert counts == {"Legendary": 8, "Epic": 12, "Rare": 20, "Common": 10}
    assert not warnings

    # Overrides always win, even against the formula, and oversize tiers are reported.
    final, formula, warnings = tiering.assign_tiers(scores, {49: "Legendary", 0: "Common"})
    assert final[49] == "Legendary" and final[0] == "Common" and formula[0] == "Legendary"
    assert not warnings  # one in, one out: Legendary still 8
    final, _, warnings = tiering.assign_tiers(scores, {49: "Legendary", 48: "Legendary"})
    assert warnings and "Legendary: 10" in warnings[0]


def test_override_file_parsing(tmp_path):
    f = tmp_path / "overrides.csv"
    f.write_text("player_id,rarity,note\n12,legendary,fan favourite\n,,\n")
    assert load_overrides(f) == {12: "Legendary"}
    f.write_text("player_id,rarity,note\n12,Mythic,typo\n")
    with pytest.raises(ValueError):
        load_overrides(f)


def seed_players(db, n=45):
    """n players with falling career scores; returns their ids best-first."""
    ids = []
    for i in range(n):
        pid = make_player(db, f"Player {i:02d}")
        db.execute(
            "INSERT INTO player_theme_stats (player_id, theme, stats, matches, source, verified) VALUES (%s, 'TEST', %s, 50, 'test', true)",
            (pid, Jsonb({"runs": 10000 - i * 200, "batting_average": 45.0})),
        )
        ids.append(pid)
    return ids


def active_top_rarity(db, pid):
    rows = db.execute("SELECT rarity FROM card_definitions WHERE player_id = %s AND is_active", (pid,)).fetchall()
    return {r["rarity"] for r in rows}


def test_overrides_survive_a_reimport_and_pool_rebuild(api, db):
    ids = seed_players(db)
    low, top = ids[-1], ids[0]
    overrides = {low: "Legendary"}
    with db.cursor() as cursor:
        summary = build_pool(cursor, candidates=ids, overrides=overrides, verbose=False)
    assert summary["tiers"]["Legendary"] == 9 and summary["warnings"]  # override pushed Legendary past 8
    assert active_top_rarity(db, low) == {"Common", "Rare", "Epic", "Legendary"}
    assert active_top_rarity(db, top) == {"Common", "Rare", "Epic", "Legendary"}

    # A re-import changes everyone's numbers: the override doesn't move.
    db.execute("UPDATE player_theme_stats SET stats = jsonb_set(stats, '{runs}', to_jsonb(1)) WHERE player_id = %s", (low,))
    db.execute("UPDATE player_theme_stats SET stats = jsonb_set(stats, '{runs}', to_jsonb(1)) WHERE player_id = %s", (top,))
    with db.cursor() as cursor:
        build_pool(cursor, candidates=ids, overrides=overrides, verbose=False)
    assert active_top_rarity(db, low) == {"Common", "Rare", "Epic", "Legendary"}   # override kept
    assert active_top_rarity(db, top) == {"Common"}                                # formula moved him down

    # Battle credits follow the final rarity.
    user = make_user(api, "collector")
    legendary = db.execute("SELECT id FROM card_definitions WHERE player_id = %s AND rarity = 'Common' AND is_active", (low,)).fetchone()["id"]
    common = db.execute("SELECT id FROM card_definitions WHERE player_id = %s AND rarity = 'Common' AND is_active", (top,)).fetchone()["id"]
    mint(api, legendary, user)
    mint(api, common, user)
    credits = {c["player_name"]: c["credits"] for c in api.get(f"/users/{user}/cards").json()}
    assert credits == {"Player 44": 30, "Player 00": 10}


def test_season_reset_keeps_accounts_and_rebuilds_the_pool(api, base_url, db):
    ids = seed_players(db, 12)
    with db.cursor() as cursor:
        build_pool(cursor, candidates=ids, overrides={}, verbose=False)
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    for u in (alice, bob):
        grant(api, u, 3000)
        for _ in range(3):
            assert api.post("/packs/open", json={"user_id": u, "pack_type": "standard"}).status_code == 200
    card = api.get(f"/users/{alice}/cards").json()[0]["id"]
    listing = api.post("/listings", json={"card_instance_id": card, "seller_id": alice, "price": "40"}).json()["id"]
    assert api.post(f"/listings/{listing}/buy", params={"buyer_id": bob}).status_code == 200
    pinned = httpx.put(f"{base_url}/me/showcase", json={"card_ids": [c["id"] for c in api.get(f"/users/{bob}/cards").json()[:2]]},
                       cookies=session_cookie(bob))
    assert pinned.status_code == 200

    summary = reset(db, candidates=ids, overrides={ids[-1]: "Epic"})
    assert summary["overridden"] == {ids[-1]: "Epic"}

    for table in ("card_instances", "listings", "pack_openings", "card_ownership_events", "battles", "user_showcase"):
        assert db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0, table
    users = db.execute("SELECT id, balance FROM users ORDER BY id").fetchall()
    assert [(u["id"], u["balance"]) for u in users] == [(alice, RESET_GRANT), (bob, RESET_GRANT)]
    ledger = db.execute("SELECT user_id, reason, delta FROM currency_ledger ORDER BY user_id").fetchall()
    assert [(r["user_id"], r["reason"], r["delta"]) for r in ledger] == [
        (alice, "MINT_RESET_GRANT", RESET_GRANT), (bob, "MINT_RESET_GRANT", RESET_GRANT)]
    assert db.execute("SELECT count(*) AS n FROM card_definitions WHERE minted_count <> 0").fetchone()["n"] == 0
    assert "Epic" in active_top_rarity(db, ids[-1])

    # Accounts still work: the same session logs in, and new pulls start at #1.
    me = httpx.get(f"{base_url}/auth/me", cookies=session_cookie(alice))
    assert me.status_code == 200 and me.json()["username"] == "alice"
    pulled = api.post("/packs/open", json={"user_id": alice, "pack_type": "standard"}).json()["cards"]
    first_of_each = {}
    for c in pulled:
        first_of_each.setdefault(c["card_definition_id"], c["serial_number"])
    assert set(first_of_each.values()) == {1}
