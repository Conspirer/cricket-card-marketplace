"""Battles: rules, concurrency, timeouts and secrecy."""

import json

import httpx
import pytest
from psycopg.types.json import Jsonb

from backend import battles as rules
from backend.tests.conftest import make_definition, make_player, make_user, mint, run_parallel
from backend.themes import STATS, THEMES

THEME_KEYS = list(THEMES)


def secret(player_index, theme, stat):
    """A distinctive value per (player, theme, stat): easy to spot in any response.

    Every stat rises with player_index, so for any two cards the higher one
    wins "higher is better" stats and the lower one wins economy/bowling
    average. That lets a test steer each round's winner.
    """
    return 900000 + player_index * 1000 + THEME_KEYS.index(theme) * 20 + list(STATS).index(stat)


@pytest.fixture
def setup(api, db):
    """Two users with a 6-card deck each and fully verified theme stats."""
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    decks, index_of = {alice: [], bob: []}, {}
    for i in range(12):
        player = make_player(db, f"Player {i:02d}")
        for theme, config in THEMES.items():
            db.execute(
                """
                INSERT INTO player_theme_stats (player_id, theme, stats, matches, source, verified, as_of)
                VALUES (%s, %s, %s, 50, 'test', true, '2026-01-01')
                """,
                (player, theme, Jsonb({s: secret(i, theme, s) for s in config["stats"]})),
            )
        owner = alice if i < 6 else bob
        card = mint(api, make_definition(db, player, "Common"), owner)
        decks[owner].append(card)
        index_of[card] = i
    return {"alice": alice, "bob": bob, "decks": decks, "index_of": index_of, "player_of": {}}


def start_battle(api, s):
    r = api.post("/battles", json={"challenger_id": s["alice"], "opponent_id": s["bob"], "card_ids": s["decks"][s["alice"]]})
    assert r.status_code == 200, r.text
    battle_id = r.json()["id"]
    r = api.post(f"/battles/{battle_id}/accept", json={"user_id": s["bob"], "card_ids": s["decks"][s["bob"]]})
    assert r.status_code == 200, r.text
    return battle_id


def get(api, battle_id, user_id):
    r = api.get(f"/battles/{battle_id}", params={"user_id": user_id})
    assert r.status_code == 200, r.text
    return r.json()


def stat_won_by(theme, winner_index, loser_index):
    """A stat on which the card with winner_index beats loser_index (all stats rise with the index)."""
    for stat in THEMES[theme]["stats"]:
        winner_higher = winner_index > loser_index
        if STATS[stat]["lower_wins"] != winner_higher:
            return stat
    raise AssertionError("no such stat")


def expire_phase(db, battle_id, seconds_ago=1):
    db.execute(
        "UPDATE battles SET phase_deadline = clock_timestamp() - %s * interval '1 second' WHERE id = %s",
        (seconds_ago, battle_id),
    )


def play_round(api, db, s, battle_id, round_number, bad_call_by=None):
    """Both pick their next card; each calls a stat their own card wins, unless
    bad_call_by names a player, who then calls a stat the opponent's card wins."""
    alice, bob = s["alice"], s["bob"]
    picks = {}
    for user in (alice, bob):
        hand = get(api, battle_id, user)["hand"]
        sudden = round_number > rules.REGULATION_ROUNDS
        card = next(c for c in hand if sudden or not c["used"])["card_id"]
        r = api.post(f"/battles/{battle_id}/pick", json={"user_id": user, "round": round_number, "card_id": card})
        assert r.status_code == 200, r.text
        picks[user] = card

    view = get(api, battle_id, alice)
    assert view["phase"] == "CALL" and view["current"]["round"] == round_number
    theme = view["current"]["theme"]["key"]
    other = {alice: bob, bob: alice}
    last = None
    for user in (alice, bob):
        me, them = s["index_of"][picks[user]], s["index_of"][picks[other[user]]]
        stat = stat_won_by(theme, them, me) if user == bad_call_by else stat_won_by(theme, me, them)
        last = api.post(f"/battles/{battle_id}/call", json={"user_id": user, "round": round_number, "stat": stat})
        assert last.status_code == 200, last.text
    body = last.json()
    if body["status"] == "ACTIVE":
        assert body["phase"] == "REVEAL"
        expire_phase(db, battle_id)  # skip the reveal pause
    return body


# ---------------------------------------------------------------------------

def test_regulation_tie_then_sudden_death_decides(api, db, setup):
    s = setup
    battle_id = start_battle(api, s)
    for round_number in range(1, 7):
        body = play_round(api, db, s, battle_id, round_number)
        assert body["rounds"][-1]["points"] == {"you": 1, "them": 1}  # each call won by its caller
    assert get(api, battle_id, s["alice"])["score"] == {"you": 6, "them": 6}

    # Sudden death: Bob calls a stat Alice's card wins, handing her both points.
    final = play_round(api, db, s, battle_id, 7, bad_call_by=s["bob"])
    assert final["status"] == "FINISHED"
    assert final["rounds"][-1]["sudden_death"] is True
    assert final["rounds"][-1]["points"] == {"you": 0, "them": 2}  # Bob's view: 0, Alice 2
    alice_view = get(api, battle_id, s["alice"])
    assert alice_view["score"] == {"you": 8, "them": 6}
    assert alice_view["winner"] == "you"
    assert len(alice_view["rounds"]) == 7


def test_three_sudden_deaths_then_draw(api, db, setup):
    s = setup
    battle_id = start_battle(api, s)
    for round_number in range(1, rules.MAX_ROUNDS + 1):
        body = play_round(api, db, s, battle_id, round_number)
    assert body["status"] == "FINISHED"
    view = get(api, battle_id, s["alice"])
    assert view["winner"] is None
    assert view["score"] == {"you": 9, "them": 9}
    assert [r["round"] for r in view["rounds"]] == list(range(1, 10))
    # Sudden death may reuse any card.
    assert db.execute("SELECT count(*) AS n FROM battle_moves WHERE battle_id = %s", (battle_id,)).fetchone()["n"] == 18


def pick_both(api, s, battle_id, round_number=1, offset=0):
    for user in (s["alice"], s["bob"]):
        r = api.post(f"/battles/{battle_id}/pick",
                     json={"user_id": user, "round": round_number, "card_id": s["decks"][user][offset]})
        assert r.status_code == 200, r.text


def test_simultaneous_final_picks_resolve_once(api, base_url, db, setup):
    s = setup
    for attempt in range(8):
        db.execute("DELETE FROM battle_rounds; DELETE FROM battle_moves; DELETE FROM battles;")
        battle_id = start_battle(api, s)
        results = run_parallel([
            lambda u=u: httpx.post(
                f"{base_url}/battles/{battle_id}/pick",
                json={"user_id": u, "round": 1, "card_id": s["decks"][u][attempt % 6]},
                timeout=30,
            )
            for u in (s["alice"], s["bob"])
        ])
        assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
        row = db.execute("SELECT phase, current_round FROM battles WHERE id = %s", (battle_id,)).fetchone()
        assert (row["phase"], row["current_round"]) == ("CALL", 1)
        assert db.execute("SELECT count(*) AS n FROM battle_moves WHERE battle_id = %s", (battle_id,)).fetchone()["n"] == 2


def test_simultaneous_calls_resolve_once(api, base_url, db, setup):
    s = setup
    for attempt in range(8):
        db.execute("DELETE FROM battle_rounds; DELETE FROM battle_moves; DELETE FROM battles;")
        battle_id = start_battle(api, s)
        pick_both(api, s, battle_id, offset=attempt % 6)
        theme = get(api, battle_id, s["alice"])["current"]["theme"]["key"]
        stats = THEMES[theme]["stats"]
        results = run_parallel([
            lambda u=u, st=st: httpx.post(
                f"{base_url}/battles/{battle_id}/call", json={"user_id": u, "round": 1, "stat": st}, timeout=30,
            )
            for u, st in ((s["alice"], stats[0]), (s["bob"], stats[-1]))
        ])
        assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
        rounds = db.execute("SELECT * FROM battle_rounds WHERE battle_id = %s", (battle_id,)).fetchall()
        assert len(rounds) == 1
        assert (rounds[0]["challenger_stat"], rounds[0]["opponent_stat"]) == (stats[0], stats[-1])
        battle = db.execute("SELECT phase, challenger_points, opponent_points FROM battles WHERE id = %s", (battle_id,)).fetchone()
        assert battle["phase"] == "REVEAL"
        assert battle["challenger_points"] + battle["opponent_points"] == rounds[0]["challenger_points"] + rounds[0]["opponent_points"]


def test_timeouts_resolve_exactly_once(api, base_url, db, setup):
    s = setup
    battle_id = start_battle(api, s)
    polls = lambda: run_parallel([
        lambda u=u: httpx.get(f"{base_url}/battles/{battle_id}", params={"user_id": u}, timeout=30)
        for u in [s["alice"], s["bob"]] * 5
    ])

    # Missed card picks: many concurrent polls, one resolution.
    expire_phase(db, battle_id)
    assert all(p.status_code == 200 for p in polls())
    moves = db.execute("SELECT * FROM battle_moves WHERE battle_id = %s", (battle_id,)).fetchall()
    assert len(moves) == 2 and all(m["pick_timed_out"] for m in moves)
    assert db.execute("SELECT phase FROM battles WHERE id = %s", (battle_id,)).fetchone()["phase"] == "CALL"

    # Alice calls, Bob misses the CALL deadline: one resolution, Bob's call random.
    theme = db.execute("SELECT themes[1] AS t FROM battles WHERE id = %s", (battle_id,)).fetchone()["t"]
    stat = THEMES[theme]["stats"][0]
    assert api.post(f"/battles/{battle_id}/call", json={"user_id": s["alice"], "round": 1, "stat": stat}).status_code == 200
    expire_phase(db, battle_id)
    assert all(p.status_code == 200 for p in polls())
    rounds = db.execute("SELECT * FROM battle_rounds WHERE battle_id = %s", (battle_id,)).fetchall()
    assert len(rounds) == 1
    r = rounds[0]
    assert r["challenger_stat"] == stat and not r["challenger_call_timed_out"]
    assert r["opponent_call_timed_out"] and r["opponent_stat"] in THEMES[theme]["stats"]


def test_abandoned_battle_plays_out_on_one_poll(api, db, setup):
    s = setup
    battle_id = start_battle(api, s)
    expire_phase(db, battle_id, seconds_ago=3600)
    view = get(api, battle_id, s["alice"])
    assert view["status"] == "FINISHED"
    rounds = db.execute("SELECT round FROM battle_rounds WHERE battle_id = %s ORDER BY round", (battle_id,)).fetchall()
    assert [r["round"] for r in rounds][:6] == [1, 2, 3, 4, 5, 6]
    for user in (s["alice"], s["bob"]):
        cards = db.execute(
            "SELECT card_id FROM battle_moves WHERE battle_id = %s AND player_id = %s AND round <= 6",
            (battle_id, user),
        ).fetchall()
        assert sorted(c["card_id"] for c in cards) == sorted(s["decks"][user])


def test_no_unrevealed_values_or_calls_in_any_response(api, db, setup):
    s = setup
    all_secrets = {
        (i, theme, stat): secret(i, theme, stat)
        for i in range(12) for theme, config in THEMES.items() for stat in config["stats"]
    }
    player_ids = [r["id"] for r in db.execute("SELECT id FROM players").fetchall()]
    battle_id = start_battle(api, s)

    def everything(extra=()):
        bodies = list(extra)
        for u in (s["alice"], s["bob"]):
            bodies.append(api.get(f"/battles/{battle_id}", params={"user_id": u}).text)
            bodies.append(api.get(f"/users/{u}/battles").text)
            bodies.append(api.get(f"/users/{u}/cards").text)
        for card in s["index_of"]:
            bodies.append(api.get(f"/card-instances/{card}").text)
        for pid in player_ids:
            bodies.append(api.get(f"/players/{pid}").text)
            bodies.append(api.get(f"/players/{pid}/theme-stats").text)
        bodies.append(api.get("/marketplace").text)
        bodies.append(api.get("/card-instances").text)
        return bodies

    def assert_hidden(bodies, allowed=frozenset()):
        text = "\n".join(bodies)
        leaked = [k for k, v in all_secrets.items() if k not in allowed and str(v) in text]
        assert not leaked, f"unrevealed stat values in responses: {leaked[:5]}"

    # CARD_PICK: nothing may be visible.
    assert_hidden(everything())
    theme_stats = api.get(f"/players/{player_ids[0]}/theme-stats").json()
    assert theme_stats["hidden"] is True and theme_stats["themes"] == []

    alice_card, bob_card = s["decks"][s["alice"]][0], s["decks"][s["bob"]][0]
    pick = api.post(f"/battles/{battle_id}/pick", json={"user_id": s["alice"], "round": 1, "card_id": alice_card})
    assert pick.json()["current"]["their_pick"] is None
    assert get(api, battle_id, s["bob"])["current"]["their_pick"] is None
    pick2 = api.post(f"/battles/{battle_id}/pick", json={"user_id": s["bob"], "round": 1, "card_id": bob_card})
    assert pick2.json()["current"]["their_pick"]["card_id"] == alice_card
    assert_hidden(everything([pick.text, pick2.text]))

    # CALL: Alice calls. Bob's view must not change except "their_call_made",
    # whichever stat Alice picked, and still no numbers anywhere.
    def bob_view_without_clock():
        v = get(api, battle_id, s["bob"])
        v.pop("server_now")
        return v

    before = bob_view_without_clock()
    theme = before["current"]["theme"]["key"]
    alice_stat = THEMES[theme]["stats"][-1]
    call = api.post(f"/battles/{battle_id}/call", json={"user_id": s["alice"], "round": 1, "stat": alice_stat})
    assert call.status_code == 200 and call.json()["current"]["your_call"] == alice_stat
    after = bob_view_without_clock()
    assert before["current"]["their_call_made"] is False and after["current"]["their_call_made"] is True
    before["current"].pop("their_call_made")
    after["current"].pop("their_call_made")
    assert before == after, "Bob's view changed beyond their_call_made after Alice's call"
    assert after["current"]["your_call"] is None
    assert_hidden(everything([call.text]))

    # Both calls in: only the two played cards' stats for this theme appear.
    bob_stat = THEMES[theme]["stats"][0]
    call2 = api.post(f"/battles/{battle_id}/call", json={"user_id": s["bob"], "round": 1, "stat": bob_stat})
    assert call2.status_code == 200, call2.text
    a_i, b_i = s["index_of"][alice_card], s["index_of"][bob_card]
    revealed = {(i, theme, st) for i in (a_i, b_i) for st in THEMES[theme]["stats"]}
    assert_hidden(everything([call2.text]), allowed=revealed)
    last = call2.json()["rounds"][-1]
    assert last["your_call"]["stat"] == bob_stat and last["their_call"]["stat"] == alice_stat


def test_deck_rules(api, db, setup):
    s = setup
    deck = s["decks"][s["alice"]]

    def challenge(card_ids):
        return api.post("/battles", json={"challenger_id": s["alice"], "opponent_id": s["bob"], "card_ids": card_ids})

    assert challenge(deck[:5]).status_code == 400
    assert challenge(deck[:5] + deck[:1]).status_code == 400
    assert challenge(deck[:5] + s["decks"][s["bob"]][:1]).status_code == 403

    # Six Legendary-tier players cost 180 credits: over the cap.
    legends = []
    for i in range(6):
        player = make_player(db, f"Legend {i}")
        make_definition(db, player, "Legendary", 10)
        legends.append(mint(api, make_definition(db, player, "Common"), s["alice"]))
    r = challenge(legends)
    assert r.status_code == 400 and "cap" in r.json()["detail"]


def test_turn_rules_and_expiry(api, db, setup):
    s = setup
    battle_id = start_battle(api, s)
    alice_card = s["decks"][s["alice"]][0]
    pick = lambda user, card, rnd=1: api.post(f"/battles/{battle_id}/pick", json={"user_id": user, "round": rnd, "card_id": card})

    assert pick(s["alice"], s["decks"][s["bob"]][0]).status_code == 400   # not in my deck
    assert pick(s["alice"], alice_card, rnd=2).status_code == 409          # wrong round
    assert pick(s["alice"], alice_card).status_code == 200
    assert pick(s["alice"], s["decks"][s["alice"]][1]).status_code == 409  # already picked
    assert pick(s["bob"], s["decks"][s["bob"]][0]).status_code == 200

    theme = get(api, battle_id, s["alice"])["current"]["theme"]["key"]
    stat = THEMES[theme]["stats"][0]
    call = lambda user, st: api.post(f"/battles/{battle_id}/call", json={"user_id": user, "round": 1, "stat": st})
    assert call(s["alice"], "nonsense").status_code == 400
    assert call(s["alice"], stat).status_code == 200
    assert call(s["alice"], stat).status_code == 409   # already called
    assert call(s["bob"], stat).status_code == 200     # same stat: counts twice

    # Round 2: a card already played can't be picked again.
    expire_phase(db, battle_id)
    assert pick(s["alice"], alice_card, rnd=2).status_code == 409

    # Challenges expire.
    r = api.post("/battles", json={"challenger_id": s["alice"], "opponent_id": s["bob"], "card_ids": s["decks"][s["alice"]]})
    pending = r.json()["id"]
    db.execute("UPDATE battles SET expires_at = clock_timestamp() - interval '1 second' WHERE id = %s", (pending,))
    r = api.post(f"/battles/{pending}/accept", json={"user_id": s["bob"], "card_ids": s["decks"][s["bob"]]})
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert db.execute("SELECT status FROM battles WHERE id = %s", (pending,)).fetchone()["status"] == "EXPIRED"


def test_no_data_loses_and_both_no_data_scores_nothing():
    assert rules.compare("runs", None, 5) == -1
    assert rules.compare("runs", 5, None) == 1
    assert rules.compare("runs", None, None) == 0
    assert rules.compare("runs", 7, 7) == 0
    assert rules.compare("economy", 6.5, 7.1) == 1   # lower wins
    assert rules.compare("bowling_average", 30.0, 25.0) == -1


def test_each_call_scores_separately():
    a = {"stats": {"IPL": {"runs": 500, "economy": 9.0}}}
    b = {"stats": {"IPL": {"runs": 300, "economy": 7.5}}}
    # Each calls their own strength: 1-1.
    r = rules.score_round("IPL", a, b, "runs", "economy")
    assert (r["challenger_points"], r["opponent_points"]) == (1, 1)
    # Same stat called twice counts twice.
    r = rules.score_round("IPL", a, b, "runs", "runs")
    assert (r["challenger_points"], r["opponent_points"]) == (2, 0)
    # A bad call hands the opponent a point; no data scores nothing.
    r = rules.score_round("IPL", a, {"stats": {}}, "runs", "sixes")
    assert (r["challenger_points"], r["opponent_points"]) == (1, 0)


def test_battle_end_conditions():
    assert not rules.battle_over(5, 7, 3)          # regulation always runs 6 rounds
    assert rules.battle_over(6, 7, 5)
    assert not rules.battle_over(6, 6, 6)          # tied: sudden death
    assert rules.battle_over(7, 8, 7)
    assert not rules.battle_over(8, 8, 8)
    assert rules.battle_over(9, 9, 9)               # three sudden deaths, still level: draw


def test_theme_draws_never_repeat_back_to_back():
    for _ in range(500):
        themes = rules.draw_themes()
        assert len(themes) == rules.MAX_ROUNDS
        assert all(a != b for a, b in zip(themes, themes[1:]))
