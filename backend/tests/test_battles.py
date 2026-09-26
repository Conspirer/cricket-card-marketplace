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


def winning_stat(theme, caller_index, other_index):
    """The stat the caller wins with, given every stat rises with the index."""
    for stat in THEMES[theme]["stats"]:
        caller_higher = caller_index > other_index
        if STATS[stat]["lower_wins"] != caller_higher:
            return stat
    raise AssertionError("no winning stat")


def expire_phase(db, battle_id, seconds_ago=1):
    db.execute(
        "UPDATE battles SET phase_deadline = clock_timestamp() - %s * interval '1 second' WHERE id = %s",
        (seconds_ago, battle_id),
    )


# ---------------------------------------------------------------------------

def test_full_battle_to_sudden_death(api, db, setup):
    """Each caller wins their own rounds -> 3-3 -> sudden death decides it."""
    s = setup
    battle_id = start_battle(api, s)
    users = {"challenger": s["alice"], "opponent": s["bob"]}

    for round_number in range(1, 8):
        view = get(api, battle_id, s["alice"])
        assert view["current"]["round"] == round_number
        assert view["current"]["sudden_death"] == (round_number == 7)
        # Each player plays their cards in order (any card in sudden death).
        picks = {}
        for side, user in users.items():
            hand = get(api, battle_id, user)["hand"]
            card = next(c for c in hand if not c["used"] or round_number == 7)["card_id"]
            r = api.post(f"/battles/{battle_id}/pick", json={"user_id": user, "round": round_number, "card_id": card})
            assert r.status_code == 200, r.text
            picks[user] = card

        view = get(api, battle_id, s["alice"])
        assert view["phase"] == "STAT_CALL"
        caller = s["alice"] if view["current"]["caller"] == "you" else s["bob"]
        if round_number < 7:
            assert caller == (s["alice"] if round_number % 2 else s["bob"])
        other = s["bob"] if caller == s["alice"] else s["alice"]
        theme = view["current"]["theme"]["key"]
        stat = winning_stat(theme, s["index_of"][picks[caller]], s["index_of"][picks[other]])

        r = api.post(f"/battles/{battle_id}/call", json={"user_id": caller, "round": round_number, "stat": stat})
        assert r.status_code == 200, r.text
        result = r.json()["rounds"][-1]
        assert result["round"] == round_number and result["result"] == "you"
        assert result["your_value"] == secret(s["index_of"][picks[caller]], theme, stat)

        if round_number < 7:
            assert r.json()["status"] == "ACTIVE"
            assert r.json()["phase"] == "REVEAL"
            expire_phase(db, battle_id)  # skip the reveal pause

    final = get(api, battle_id, s["alice"])
    assert final["status"] == "FINISHED"
    assert len(final["rounds"]) == 7
    assert final["score"]["you"] + final["score"]["them"] == 7
    sudden_death_caller = final["rounds"][6]["caller"]
    assert final["winner"] == sudden_death_caller  # caller won every round, including sudden death


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
        assert (row["phase"], row["current_round"]) == ("STAT_CALL", 1)
        assert db.execute("SELECT count(*) AS n FROM battle_moves WHERE battle_id = %s", (battle_id,)).fetchone()["n"] == 2


def test_timeouts_resolve_exactly_once(api, base_url, db, setup):
    s = setup
    battle_id = start_battle(api, s)

    # Missed card picks: many concurrent polls, one resolution.
    expire_phase(db, battle_id)
    polls = run_parallel([
        lambda u=u: httpx.get(f"{base_url}/battles/{battle_id}", params={"user_id": u}, timeout=30)
        for u in [s["alice"], s["bob"]] * 5
    ])
    assert all(p.status_code == 200 for p in polls)
    moves = db.execute("SELECT * FROM battle_moves WHERE battle_id = %s", (battle_id,)).fetchall()
    assert len(moves) == 2 and all(m["pick_timed_out"] for m in moves)
    assert db.execute("SELECT phase FROM battles WHERE id = %s", (battle_id,)).fetchone()["phase"] == "STAT_CALL"

    # Missed stat call: again one resolution, with a random stat from the theme.
    expire_phase(db, battle_id)
    run_parallel([
        lambda u=u: httpx.get(f"{base_url}/battles/{battle_id}", params={"user_id": u}, timeout=30)
        for u in [s["alice"], s["bob"]] * 5
    ])
    rounds = db.execute("SELECT * FROM battle_rounds WHERE battle_id = %s", (battle_id,)).fetchall()
    assert len(rounds) == 1 and rounds[0]["call_timed_out"]
    theme = db.execute("SELECT themes[1] AS t FROM battles WHERE id = %s", (battle_id,)).fetchone()["t"]
    assert rounds[0]["stat"] in THEMES[theme]["stats"]


def test_abandoned_battle_plays_out_on_one_poll(api, db, setup):
    s = setup
    battle_id = start_battle(api, s)
    expire_phase(db, battle_id, seconds_ago=3600)
    view = get(api, battle_id, s["alice"])
    assert view["status"] == "FINISHED"
    rounds = db.execute("SELECT round FROM battle_rounds WHERE battle_id = %s ORDER BY round", (battle_id,)).fetchall()
    assert [r["round"] for r in rounds][:6] == [1, 2, 3, 4, 5, 6]
    # Each regulation card used exactly once per player.
    for user in (s["alice"], s["bob"]):
        cards = db.execute(
            "SELECT card_id FROM battle_moves WHERE battle_id = %s AND player_id = %s AND round <= 6",
            (battle_id, user),
        ).fetchall()
        assert sorted(c["card_id"] for c in cards) == sorted(s["decks"][user])


def test_no_unrevealed_stat_values_in_any_response(api, db, setup):
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

    # Round 1, CARD_PICK: nothing may be visible.
    assert_hidden(everything())
    theme_stats = api.get(f"/players/{player_ids[0]}/theme-stats").json()
    assert theme_stats["hidden"] is True and theme_stats["themes"] == []

    alice_card, bob_card = s["decks"][s["alice"]][0], s["decks"][s["bob"]][0]
    pick = api.post(f"/battles/{battle_id}/pick", json={"user_id": s["alice"], "round": 1, "card_id": alice_card})
    # Bob hasn't picked: Alice must not see Bob's card, and Bob must not see Alice's.
    assert pick.json()["current"]["their_pick"] is None
    assert get(api, battle_id, s["bob"])["current"]["their_pick"] is None
    assert get(api, battle_id, s["bob"])["current"]["their_pick_made"] is True
    pick2 = api.post(f"/battles/{battle_id}/pick", json={"user_id": s["bob"], "round": 1, "card_id": bob_card})

    # STAT_CALL: both cards named, still no numbers anywhere.
    assert pick2.json()["current"]["their_pick"]["card_id"] == alice_card
    assert_hidden(everything([pick.text, pick2.text]))

    # After the call: only the two played cards' stats for this round's theme.
    theme = get(api, battle_id, s["alice"])["current"]["theme"]["key"]
    stat = THEMES[theme]["stats"][0]
    call = api.post(f"/battles/{battle_id}/call", json={"user_id": s["alice"], "round": 1, "stat": stat})
    assert call.status_code == 200, call.text
    a_i, b_i = s["index_of"][alice_card], s["index_of"][bob_card]
    revealed = {(i, theme, st) for i in (a_i, b_i) for st in THEMES[theme]["stats"]}
    assert_hidden(everything([call.text]), allowed=revealed)
    assert str(secret(a_i, theme, stat)) in call.text and str(secret(b_i, theme, stat)) in call.text


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
    # Round 1 is the challenger's call.
    assert api.post(f"/battles/{battle_id}/call", json={"user_id": s["bob"], "round": 1, "stat": stat}).status_code == 403
    assert api.post(f"/battles/{battle_id}/call", json={"user_id": s["alice"], "round": 1, "stat": "nonsense"}).status_code == 400
    assert api.post(f"/battles/{battle_id}/call", json={"user_id": s["alice"], "round": 1, "stat": stat}).status_code == 200

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


def test_no_data_loses_and_both_no_data_draws():
    assert rules.compare("runs", None, 5) == -1
    assert rules.compare("runs", 5, None) == 1
    assert rules.compare("runs", None, None) == 0
    assert rules.compare("runs", 7, 7) == 0
    assert rules.compare("economy", 6.5, 7.1) == 1   # lower wins
    assert rules.compare("bowling_average", 30.0, 25.0) == -1


def test_theme_draws_never_repeat_back_to_back():
    for _ in range(500):
        themes = rules.draw_themes()
        assert len(themes) == 7
        assert all(a != b for a, b in zip(themes, themes[1:]))
