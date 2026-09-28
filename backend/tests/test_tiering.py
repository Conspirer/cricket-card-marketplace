"""Career tiers, overrides that stick, credits from the final rarity, and the season reset."""

import httpx
import pytest
from psycopg.types.json import Jsonb

from backend import tiering
from backend.scripts.build_card_pool import apply_edition_overrides, apply_pool_overrides, build_pool, load_overrides, load_pool_overrides
from backend.scripts.reset_season import RESET_GRANT, reset
from backend.tests.conftest import grant, make_definition, make_player, make_user, mint
from backend.tests.sessions import session_cookie

from backend.scripts import build_theme_stats as bts


def fmt(matches=50, **stats):
    return {"stats": stats, "matches": matches}


GREAT_BATTER = {"TEST": fmt(150, runs=12000, batting_average=55.0), "ODI": fmt(300, runs=10000, batting_average=45.0)}
AVERAGE_BOWLER = {"TEST": fmt(40, runs=400, wickets=120, bowling_average=32.0),
                  "ODI": fmt(80, runs=200, wickets=90, economy=5.3)}


def test_career_score_ranks_greats_above_average_players():
    assert tiering.career_score(GREAT_BATTER) > 2 * tiering.career_score(AVERAGE_BOWLER)


def test_score_is_best_format_plus_a_fraction_of_the_next_two():
    formats = {"TEST": fmt(100, runs=8000, batting_average=48.0), "ODI": fmt(150, runs=5000, batting_average=40.0),
               "T20I": fmt(60, runs=1500, batting_average=30.0), "IPL": fmt(80, runs=2000, strike_rate=130.0)}
    parts = {f: tiering.format_score(f, r["stats"]) for f, r in formats.items()}
    ordered = sorted(parts.values(), reverse=True)
    c = tiering.career(formats)
    assert c["best"] == "TEST"
    assert c["score"] == pytest.approx(ordered[0] + tiering.BLEND_SECOND * ordered[1] + tiering.BLEND_THIRD * ordered[2], abs=1e-3)
    # A fourth format adds nothing: an older player isn't behind for missing T20s.
    three = {f: formats[f] for f in ("TEST", "ODI", "T20I")}
    assert tiering.career_score(three) == c["score"]


def test_a_specialist_isnt_outranked_by_a_lesser_all_format_player():
    specialist = {"TEST": fmt(120, runs=10000, batting_average=50.0)}
    everything = {f: fmt(60, runs=r, batting_average=30.0) for f, r in
                  (("TEST", 3500), ("ODI", 3500), ("T20I", 1400), ("IPL", 2100))}
    assert tiering.career_score(specialist) > tiering.career_score(everything)


def test_missing_formats_are_left_out_never_zero():
    # An unverified format is absent from the input: the score is the same as
    # for someone who never played it, not dragged down by a zero.
    odi_only = {"ODI": fmt(200, runs=7000, batting_average=45.0)}
    assert tiering.career_score(odi_only) == pytest.approx(tiering.format_score("ODI", odi_only["ODI"]["stats"]), abs=1e-3)
    assert tiering.career_score({**odi_only, "TEST": fmt(0)}) == tiering.career_score(odi_only)


def test_best_format_needs_enough_matches():
    streak = {"TEST": fmt(tiering.MIN_BEST_FORMAT_MATCHES["TEST"] - 1, runs=2500, batting_average=80.0)}
    assert tiering.career(streak)["score"] is None                      # can't be ranked on a small sample
    with_odis = {**streak, "ODI": fmt(tiering.MIN_BEST_FORMAT_MATCHES["ODI"], runs=1500, batting_average=35.0)}
    c = tiering.career(with_odis)
    assert c["best"] == "ODI"                                           # the bigger Test score can't be the best...
    assert c["score"] == pytest.approx(c["parts"]["ODI"] + tiering.BLEND_SECOND * c["parts"]["TEST"], abs=1e-3)  # ...but still adds


def test_test_and_odi_weigh_at_least_as_much_as_t20i():
    for f in ("TEST", "ODI"):
        assert tiering.FORMAT_WEIGHT[f] >= tiering.FORMAT_WEIGHT["T20I"] >= tiering.FORMAT_WEIGHT["IPL"]


def test_quality_is_clamped_and_missing_quality_is_neutral():
    huge = tiering.format_parts("TEST", {"runs": 5000, "batting_average": 400.0})[0]
    assert huge == pytest.approx(tiering.QUALITY_MAX ** tiering.QUALITY_EXPONENT)
    neutral = tiering.format_parts("TEST", {"runs": 5000, "batting_average": None})[0]
    assert neutral == pytest.approx(1.0)


def test_volume_has_diminishing_returns_and_quality_counts_more():
    # Four times the runs is worth twice as much, not four times.
    short = tiering.format_parts("TEST", {"runs": 2500, "batting_average": 40.0})[0]
    long_ = tiering.format_parts("TEST", {"runs": 10000, "batting_average": 40.0})[0]
    assert long_ == pytest.approx(2 * short)
    # A great short bowling career beats a long ordinary one.
    great = {"TEST": fmt(50, wickets=230, bowling_average=20.0)}
    ordinary = {"TEST": fmt(110, wickets=400, bowling_average=33.0)}
    assert tiering.career_score(great) > tiering.career_score(ordinary)


def test_legends_are_tiered_apart_and_never_below_the_floor():
    L, E = tiering.LEGEND_LEGENDARY_SIZE, tiering.LEGEND_EPIC_SIZE
    current = {pid: 100.0 - pid for pid in range(80)}
    legends = {1000 + i: 500.0 - i for i in range(L + E + 5)}
    final, formula, warnings = tiering.assign_tiers({**current, **legends}, legends=set(legends))
    # Current players fill their own tiers exactly as if no legend existed.
    alone, _, _ = tiering.assign_tiers(current)
    assert {pid: final[pid] for pid in current} == alone
    ranked = sorted(legends, key=lambda p: -legends[p])
    assert [final[p] for p in ranked] == ["Legendary"] * L + ["Epic"] * E + [tiering.LEGEND_FLOOR] * 5
    assert tiering.LEGEND_FLOOR == "Rare" and not warnings
    # Overrides can take a legend down to Rare, never to Common.
    final, _, warnings = tiering.assign_tiers({**current, **legends}, {ranked[0]: "Rare", ranked[-1]: "Common"}, set(legends))
    assert final[ranked[0]] == "Rare"
    assert final[ranked[-1]] == "Rare" and "never below Rare" in warnings[0]


def test_legend_cutoff():
    from datetime import date, timedelta
    cutoff = tiering.LEGEND_CUTOFF
    assert tiering.is_legend(cutoff - timedelta(days=1))
    assert not tiering.is_legend(cutoff)              # played on the cutoff day: current
    assert not tiering.is_legend(None)                # unknown date isn't enough to call someone retired
    assert tiering.is_legend(date(2019, 7, 9))


# ---------------------------------------------------------------------------
# Rolling a stale infobox forward with Cricsheet
# ---------------------------------------------------------------------------

def match(runs=0, wickets=0, catches=0, hundreds=0, balls=0, **extra):
    m = {k: 0 for k in bts.ADDITIVE}
    m.update(runs=runs, wickets=wickets, catches=catches, hundreds=hundreds, balls_bowled=balls, **extra)
    m["date"] = "2026-01-01"
    return m


SEQ = [match(runs=40, wickets=2, catches=1, balls=120, dismissals=2, runs_conceded=60),
       match(runs=110, hundreds=1, wickets=0, catches=0, balls=90, dismissals=1, runs_conceded=40),
       match(runs=5, wickets=3, catches=2, balls=150, dismissals=1, runs_conceded=70)]


def infobox(matches, runs, wickets, catches, hundreds, deliveries):
    return {"matches": matches, "runs": runs, "wickets": wickets, "catches": catches, "hundreds": hundreds,
            "deliveries": deliveries}


def test_snapshot_window_accepts_an_exact_or_piecemeal_snapshot():
    assert bts.snapshot_window(infobox(2, 150, 2, 1, 1, 210), SEQ) == (2, 2)       # exactly after match 2
    # Runs updated after match 3, the match count still says 2: within the window.
    assert bts.snapshot_window(infobox(2, 155, 2, 1, 1, 210), SEQ) == (2, 3)


def test_snapshot_window_rejects_what_cricsheet_cant_explain():
    assert bts.snapshot_window(infobox(2, 170, 2, 1, 1, 210), SEQ) is None         # runs Cricsheet never had
    assert bts.snapshot_window(infobox(4, 155, 5, 3, 1, 360), SEQ) is None         # more matches than Cricsheet
    assert bts.snapshot_window(infobox(2, 150, 2, 1, 1, 999), SEQ) is None         # balls don't fit


def test_rolled_rows_only_for_tests_of_countries_that_never_met_afghanistan():
    ib = infobox(2, 150, 2, 1, 1, 210)
    safe = {"England"}
    row = bts.rolled_row("TEST", ib, SEQ, None, "England", safe)
    assert row and row["verified"] and row["matches"] == 3 and row["stats"]["runs"] == 155
    assert row["stats"]["wickets"] == 5 and row["stats"]["hundreds"] == 1
    assert bts.rolled_row("TEST", ib, SEQ, None, "India", safe) is None            # has played Afghanistan
    assert bts.rolled_row("ODI", ib, SEQ, None, "England", safe) is None           # never for ODIs/T20Is


def test_afghanistan_test_opponents_parser():
    wikitext = """
! scope=row | {{cr|AUS}}
! colspan=7|YTP
|-
! scope=row | {{cr|IND}}
| {{nts|2}} || {{nts|0}}
|-
! scope=row | {{nobr|{{cr|NZ}}}}
! colspan=7|YTP
"""
    assert bts.never_played_afghanistan_in_tests(wikitext) == {"Australia", "New Zealand"}
    assert bts.never_played_afghanistan_in_tests(None) == set()


def test_infobox_last_match_takes_the_latest_format():
    box = ("{{Infobox cricketer | lasttestdate = 2 January | lasttestyear = 2025 | lastodidate = 12 August"
           " | lastodiyear = 2026 | lastT20Idate = 19 February | lastT20Iyear = 2026}}")
    assert str(bts.infobox_last_match(box)) == "2026-08-12"
    assert str(bts.infobox_last_match("{{Infobox cricketer | lasttestdate = 26 December 2014}}")) == "2014-12-26"
    assert bts.infobox_last_match("no infobox") is None


def test_assign_tiers_sizes_and_overrides():
    sizes = tiering.TIER_SIZES
    n = sum(sizes.values()) + 10
    scores = {pid: 1000 - pid for pid in range(n)}
    final, formula, warnings = tiering.assign_tiers(scores)
    counts = {r: sum(1 for v in final.values() if v == r) for r in tiering.RARITIES}
    assert counts == {**sizes, "Common": 10}
    assert not warnings

    # Overrides always win, even against the formula, and oversize tiers are reported.
    last = n - 1
    final, formula, warnings = tiering.assign_tiers(scores, {last: "Legendary", 0: "Common"})
    assert final[last] == "Legendary" and final[0] == "Common" and formula[0] == "Legendary"
    assert not warnings  # one in, one out: Legendary still at its size
    # Overrides take a slot: two promoted means the formula's last two Legendaries drop to Epic.
    final, formula, warnings = tiering.assign_tiers(scores, {last: "Legendary", last - 1: "Legendary"})
    counts = {r: sum(1 for v in final.values() if v == r) for r in tiering.RARITIES}
    assert counts == {**sizes, "Common": 10} and not warnings
    top = sorted(scores, key=lambda p: -scores[p])
    assert [final[p] for p in top[sizes["Legendary"] - 2: sizes["Legendary"]]] == ["Epic", "Epic"]
    # Demoting one promotes the next in line.
    final, _, _ = tiering.assign_tiers(scores, {top[0]: "Epic"})
    assert final[top[0]] == "Epic" and final[top[sizes["Legendary"]]] == "Legendary"
    # Only overrides alone overfilling a tier are warned about.
    too_many = {p: "Legendary" for p in top[-(sizes["Legendary"] + 1):]}
    final, _, warnings = tiering.assign_tiers(scores, too_many)
    assert warnings and "overrides alone" in warnings[0]


def test_pool_override_file_parsing_and_precedence(tmp_path):
    f = tmp_path / "pool_overrides.csv"
    f.write_text("player_id,include,edition,note\n7,yes,,forced in\n8,No,,kept out\n9,,Legend,edge case\n"
                 "10,yes,current,still playing\n,,,\n")
    overrides = load_pool_overrides(f)
    assert overrides == {7: {"include": True, "edition": None}, 8: {"include": False, "edition": None},
                         9: {"include": None, "edition": "legend"}, 10: {"include": True, "edition": "current"}}
    assert apply_pool_overrides([1, 8, 9], overrides) == [1, 7, 9, 10]      # yes adds, no removes, blank keeps
    assert apply_edition_overrides({10, 11}, overrides) == {9, 11}          # edition always wins
    for bad in ("7,maybe,,typo", "7,yes,icon,typo"):
        f.write_text("player_id,include,edition,note\n" + bad + "\n")
        with pytest.raises(ValueError):
            load_pool_overrides(f)


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
            (pid, Jsonb({"runs": 10000 - i * 100, "batting_average": 45.0})),  # stays positive up to n=99
        )
        ids.append(pid)
    return ids


def active_top_rarity(db, pid):
    rows = db.execute("SELECT rarity FROM card_definitions WHERE player_id = %s AND is_active", (pid,)).fetchall()
    return {r["rarity"] for r in rows}


def test_overrides_survive_a_reimport_and_pool_rebuild(api, db):
    ids = seed_players(db, n=sum(tiering.TIER_SIZES.values()) + 10)  # enough that some are Common
    low, top = ids[-1], ids[0]
    overrides = {low: "Legendary"}
    with db.cursor() as cursor:
        summary = build_pool(cursor, candidates=ids, overrides=overrides, verbose=False)
    assert summary["tiers"]["Legendary"] == tiering.LEGENDARY_SIZE and not summary["warnings"]  # override took a slot
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
    assert credits == {f"Player {len(ids) - 1:02d}": 30, "Player 00": 10}


def test_legend_cards_start_at_rare_and_take_no_current_slots(db):
    ids = seed_players(db, n=12)
    legends = seed_players(db, n=3)  # same stats as the best current players
    with db.cursor() as cursor:
        summary = build_pool(cursor, candidates=ids + legends, overrides={}, verbose=False, legends=legends)
    assert summary["legends"] == 3 and summary["legend_tiers"]["Legendary"] == 3
    for pid in legends:
        assert active_top_rarity(db, pid) == {"Rare", "Epic", "Legendary"}             # no Common legend cards
    # The best current player is still Legendary: legends didn't take the slot.
    assert active_top_rarity(db, ids[0]) == {"Common", "Rare", "Epic", "Legendary"}
    assert summary["tiers"]["Legendary"] == min(len(ids), tiering.LEGENDARY_SIZE)


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
