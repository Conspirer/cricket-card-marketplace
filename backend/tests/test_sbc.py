"""SBCs: the requirement engine, submissions, and races against the market and battles."""

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from backend import sbc
from backend.main import SIGNUP_GRANT
from backend.tests.conftest import acting, grant, make_definition, make_player, make_user, mint, run_parallel
from backend.tests.sessions import session_cookie
from backend.themes import THEMES


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def card(i, rarity="Common", country="India", role="Batter", themes=None):
    return {"card_id": i, "player_id": i, "rarity": rarity, "country": country, "role": role,
            "theme_stats": themes or {}}


def verified(matches=10, **stats):
    return {"verified": True, "matches": matches, "stats": stats}


def ok(rule, cards):
    return sbc.check_rule(rule, cards)[0]


def test_count_rule():
    rule = {"type": "count", "n": 3}
    assert ok(rule, [card(1), card(2), card(3)])
    assert not ok(rule, [card(1), card(2)])
    assert not ok(rule, [card(i) for i in range(4)])


def test_min_rarity_every_card_and_at_least_n():
    every = {"type": "min_rarity", "rarity": "Rare"}
    assert ok(every, [card(1, "Rare"), card(2, "Legendary")])
    assert not ok(every, [card(1, "Rare"), card(2, "Common")])
    assert not ok(every, [])
    some = {"type": "min_rarity", "rarity": "Epic", "min": 1}
    assert ok(some, [card(1, "Common"), card(2, "Epic")])
    assert not ok(some, [card(1, "Rare"), card(2, "Rare")])


def test_country_and_distinct_countries():
    assert ok({"type": "country", "country": "Pakistan", "min": 1}, [card(1), card(2, country="Pakistan")])
    assert not ok({"type": "country", "country": "Pakistan", "min": 2}, [card(1), card(2, country="Pakistan")])
    rule = {"type": "distinct_countries", "min": 3}
    assert ok(rule, [card(1, country="India"), card(2, country="England"), card(3, country="Australia")])
    assert not ok(rule, [card(1, country="India"), card(2, country="India"), card(3, country="Australia")])


def test_role():
    rule = {"type": "role", "role": "Bowler", "min": 2}
    assert ok(rule, [card(1, role="Bowler"), card(2, role="Bowler"), card(3)])
    assert not ok(rule, [card(1, role="Bowler"), card(2, role="All-rounder")])


def test_played_in_needs_verified_stats_with_matches():
    rule = {"type": "played_in", "theme": "ODI_WC", "min": 2}
    played = {"ODI_WC": verified(matches=9)}
    assert ok(rule, [card(1, themes=played), card(2, themes=played)])
    unverified = {"ODI_WC": {"verified": False, "matches": 9, "stats": {}}}
    no_matches = {"ODI_WC": verified(matches=0)}
    assert not ok(rule, [card(1, themes=played), card(2, themes=unverified)])
    assert not ok(rule, [card(1, themes=played), card(2, themes=no_matches)])
    assert not ok(rule, [card(1, themes=played), card(2)])


def test_combined_stat_sums_only_verified_values():
    rule = {"type": "combined_stat", "theme": "TEST", "stat": "runs", "min": 10000}
    assert ok(rule, [card(1, themes={"TEST": verified(runs=6000)}), card(2, themes={"TEST": verified(runs=4000)})])
    assert not ok(rule, [card(1, themes={"TEST": verified(runs=6000)}), card(2, themes={"TEST": verified(runs=3999)})])
    fake = {"TEST": {"verified": False, "matches": 100, "stats": {"runs": 9000}}}
    assert not ok(rule, [card(1, themes={"TEST": verified(runs=6000)}), card(2, themes=fake)])
    assert sbc.check_rule(rule, [card(1, themes={"TEST": verified(runs=None)})]) == (False, "0/10,000")


def test_evaluate_requires_different_players():
    rules = [{"type": "count", "n": 2}]
    a, b = card(1), card(2)
    assert sbc.all_met(sbc.evaluate(rules, [a, b]))
    dup = dict(b, player_id=1)
    checklist = sbc.evaluate(rules, [a, dup])
    assert not sbc.all_met(checklist) and checklist[-1]["rule"] == "Each card a different player"


@pytest.mark.parametrize("rules", [
    [],
    [{"type": "role", "role": "Bowler", "min": 1}],                             # no count
    [{"type": "count", "n": 1}, {"type": "keeper"}],                            # unknown type
    [{"type": "count", "n": 1}, {"type": "min_rarity", "rarity": "Mythic"}],
    [{"type": "count", "n": 1}, {"type": "played_in", "theme": "NOPE", "min": 1}],
    [{"type": "count", "n": 1}, {"type": "combined_stat", "theme": "TEST", "stat": "nope", "min": 1}],
])
def test_invalid_rules_are_rejected(rules):
    with pytest.raises(sbc.InvalidRule):
        sbc.validate_rules(rules)


def test_every_rule_type_describes_itself():
    for rule in [{"type": "count", "n": 3}, {"type": "min_rarity", "rarity": "Rare"},
                 {"type": "min_rarity", "rarity": "Epic", "min": 1}, {"type": "country", "country": "India", "min": 1},
                 {"type": "distinct_countries", "min": 5}, {"type": "role", "role": "Bowler", "min": 3},
                 {"type": "played_in", "theme": "ODI_WC", "min": 3},
                 {"type": "combined_stat", "theme": "TEST", "stat": "runs", "min": 10000}]:
        assert sbc.describe(rule)


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------

def make_challenge(db, slug="duo", requirements=None, reward=None, max_completions=1, **extra):
    requirements = requirements or [{"type": "count", "n": 2}]
    sbc.validate_rules(requirements)
    return db.execute(
        """
        INSERT INTO sbc_challenges (slug, title, description, requirements, reward, max_completions_per_user,
                                    starts_at, ends_at, active)
        VALUES (%s, %s, 'test', %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (slug, slug.title(), Jsonb(requirements), Jsonb(reward or {"runs": 100}), max_completions,
         extra.get("starts_at"), extra.get("ends_at"), extra.get("active", True)),
    ).fetchone()["id"]


def make_edition(db, key, player_name="Reward Guy", rarity="Legendary", supply=5):
    player = make_player(db, player_name)
    return db.execute(
        """
        INSERT INTO card_definitions (player_id, rarity, max_supply, is_active, edition, edition_key, edition_label)
        VALUES (%s, %s, %s, false, 'SBC', %s, 'Special') RETURNING id
        """,
        (player, rarity, supply, key),
    ).fetchone()["id"]


def cards_for(api, db, owner, n, prefix="P", **player):
    return [mint(api, make_definition(db, make_player(db, f"{prefix}{i}", **player)), owner) for i in range(n)]


def submit(api, user, slug, ids):
    return api.post(f"/sbcs/{slug}/submit", params={"user_id": user}, json={"card_ids": ids})


def burned(db, ids):
    return [r["burned_at"] is not None for r in
            db.execute("SELECT burned_at FROM card_instances WHERE id = ANY(%s) ORDER BY id", (ids,)).fetchall()]


def balance(db, user):
    return db.execute("SELECT balance FROM users WHERE id = %s", (user,)).fetchone()["balance"]


def test_submission_burns_cards_and_grants_card_and_runs(api, db):
    alice = make_user(api, "alice")
    reward_def = make_edition(db, "special")
    make_challenge(db, reward={"cards": ["special"], "runs": 50})
    ids = cards_for(api, db, alice, 2)

    check = api.post("/sbcs/duo/check", params={"user_id": alice}, json={"card_ids": ids}).json()
    assert check["ready"] and all(item["ok"] for item in check["checklist"])

    r = submit(api, alice, "duo", ids)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["burned"] == sorted(ids) and len(body["reward_cards"]) == 1 and body["reward_runs"] == 50
    assert burned(db, ids) == [True, True]
    new = db.execute("SELECT owner_id, card_definition_id, serial_number FROM card_instances WHERE id = %s",
                     (body["reward_cards"][0],)).fetchone()
    assert new == {"owner_id": alice, "card_definition_id": reward_def, "serial_number": 1}
    assert balance(db, alice) == SIGNUP_GRANT + 50
    assert db.execute("SELECT reason FROM currency_ledger WHERE user_id = %s ORDER BY id DESC LIMIT 1",
                      (alice,)).fetchone()["reason"] == "MINT_SBC_REWARD"

    # Burned cards are gone from the collection and the all-cards list; history shows BURNED.
    collection = [c["id"] for c in api.get(f"/users/{alice}/cards", params={"user_id": alice}).json()]
    assert not set(ids) & set(collection) and body["reward_cards"][0] in collection
    events = [e["event_type"] for e in api.get(f"/card-instances/{ids[0]}/history").json()]
    assert events[-1] == "BURNED"

    listing = api.get("/sbcs/duo", params={"user_id": alice}).json()
    assert listing["reward"]["cards"][0]["minted_count"] == 1


def test_completion_limit_and_requirements_enforced(api, db):
    alice = make_user(api, "alice")
    make_challenge(db, requirements=[{"type": "count", "n": 2}, {"type": "country", "country": "Pakistan", "min": 1}])
    indians = cards_for(api, db, alice, 4, prefix="In")
    pakistanis = cards_for(api, db, alice, 2, prefix="Pk", country="Pakistan")

    r = submit(api, alice, "duo", indians[:2])
    assert r.status_code == 400 and "Pakistan" in r.json()["detail"]
    assert burned(db, indians[:2]) == [False, False]

    assert submit(api, alice, "duo", [indians[0], pakistanis[0]]).status_code == 200
    r = submit(api, alice, "duo", [indians[1], pakistanis[1]])
    assert r.status_code == 409 and "already completed" in r.json()["detail"]
    assert burned(db, [indians[1], pakistanis[1]]) == [False, False]


def test_blocked_cards_are_refused(api, db, base_url):
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    make_challenge(db)
    mine = cards_for(api, db, alice, 8, prefix="A")
    bobs = cards_for(api, db, bob, 1, prefix="B")

    assert submit(api, alice, "duo", [mine[0], bobs[0]]).status_code == 403            # not owned
    assert submit(api, alice, "duo", [mine[0], mine[0]]).status_code == 400            # same card twice
    assert httpx.post(f"{base_url}/sbcs/duo/submit", json={"card_ids": mine[:2]}).status_code == 401

    api.post("/listings", json={"card_instance_id": mine[0], "seller_id": alice, "price": "10"})
    r = submit(api, alice, "duo", [mine[0], mine[1]])
    assert r.status_code == 409 and "listed" in r.json()["detail"]

    r = api.post("/battles", json={"challenger_id": alice, "opponent_id": bob, "card_ids": mine[2:8]})
    assert r.status_code == 200, r.text
    r = submit(api, alice, "duo", [mine[1], mine[2]])
    assert r.status_code == 409 and "battle" in r.json()["detail"]
    check = api.post("/sbcs/duo/check", params={"user_id": alice}, json={"card_ids": [mine[1], mine[2]]}).json()
    assert not check["ready"] and str(mine[2]) in check["blocked"]

    assert burned(db, mine) == [False] * 8


def test_closed_challenges_refuse_submissions(api, db):
    alice = make_user(api, "alice")
    make_challenge(db, slug="old", ends_at="2020-01-01")
    make_challenge(db, slug="off", active=False)
    ids = cards_for(api, db, alice, 2)
    assert submit(api, alice, "old", ids).status_code == 409
    assert submit(api, alice, "off", ids).status_code == 409
    assert submit(api, alice, "missing", ids).status_code == 404
    assert [c["slug"] for c in api.get("/sbcs").json()] == ["old"]


def test_sold_out_reward_rolls_everything_back(api, db):
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    make_edition(db, "tiny", supply=1)
    make_challenge(db, reward={"cards": ["tiny"]})
    assert submit(api, alice, "duo", cards_for(api, db, alice, 2, prefix="A")).status_code == 200
    bobs = cards_for(api, db, bob, 2, prefix="B")
    r = submit(api, bob, "duo", bobs)
    assert r.status_code == 409 and "sold out" in r.json()["detail"]
    assert burned(db, bobs) == [False, False]
    assert db.execute("SELECT count(*) AS n FROM sbc_completions").fetchone()["n"] == 1


def test_packs_never_roll_sbc_editions(api, db):
    make_definition(db, make_player(db, "Base"), "Common", 500)
    special = make_edition(db, "special", supply=500)
    with pytest.raises(psycopg.errors.CheckViolation):  # an SBC edition can't be made active
        db.execute("UPDATE card_definitions SET is_active = true WHERE id = %s", (special,))
    user = make_user(api, "opener")
    grant(api, user, 10000)
    for _ in range(10):
        assert api.post("/packs/open", json={"user_id": user, "pack_type": "standard"}).status_code == 200
    assert db.execute("SELECT count(*) AS n FROM card_instances WHERE card_definition_id = %s",
                      (special,)).fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# Burned cards stay burned
# ---------------------------------------------------------------------------

def test_burned_cards_cant_be_listed_pinned_or_battled(api, db, base_url):
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    make_challenge(db)
    ids = cards_for(api, db, alice, 7)
    assert submit(api, alice, "duo", ids[:2]).status_code == 200
    gone = ids[0]

    r = api.post("/listings", json={"card_instance_id": gone, "seller_id": alice, "price": "10"})
    assert r.status_code == 409, r.text
    r = httpx.put(f"{base_url}/me/showcase", json={"card_ids": [gone]}, cookies=session_cookie(alice))
    assert r.status_code in (403, 409), r.text
    r = api.post("/battles", json={"challenger_id": alice, "opponent_id": bob, "card_ids": [gone] + ids[2:7]})
    assert r.status_code == 409, r.text
    assert submit(api, alice, "duo", [gone, ids[2]]).status_code == 409  # can't burn twice (after the limit check, too)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def sbc_post(base_url, user, slug, ids):
    return lambda: acting.post(f"{base_url}/sbcs/{slug}/submit", params={"user_id": user},
                               json={"card_ids": ids}, timeout=30)


def test_sbc_racing_a_buy_exactly_one_wins(api, base_url, db):
    """The seller cancels their listing and submits the card to an SBC while a
    buyer buys it. Either the buy lands (and the SBC is refused) or the cancel
    and SBC land (and the buy is refused), never both, never a 500."""
    seller, buyer = make_user(api, "seller"), make_user(api, "buyer")
    make_challenge(db, max_completions=100)
    outcomes = []
    for attempt in range(15):
        target, partner = cards_for(api, db, seller, 2, prefix=f"R{attempt}-")
        listing = api.post("/listings", json={"card_instance_id": target, "seller_id": seller, "price": "10"}).json()["id"]

        def cancel_then_submit():
            acting.post(f"{base_url}/listings/{listing}/cancel", params={"seller_id": seller}, timeout=30)
            return sbc_post(base_url, seller, "duo", [target, partner])()

        buy, sub = run_parallel([
            lambda: acting.post(f"{base_url}/listings/{listing}/buy", params={"buyer_id": buyer}, timeout=30),
            cancel_then_submit,
        ])
        codes = (buy.status_code, sub.status_code)
        assert sorted(codes) in ([200, 403], [200, 409]), (attempt, codes, buy.text, sub.text)
        owner, is_burned = db.execute("SELECT owner_id, burned_at IS NOT NULL AS b FROM card_instances WHERE id = %s",
                                      (target,)).fetchone().values()
        if buy.status_code == 200:
            assert owner == buyer and not is_burned
        else:
            assert owner == seller and is_burned
        outcomes.append(codes)
    # Also the plain case: an SBC on a card that's listed is always refused.
    target, partner = cards_for(api, db, seller, 2, prefix="L")
    listing = api.post("/listings", json={"card_instance_id": target, "seller_id": seller, "price": "10"}).json()["id"]
    buy, sub = run_parallel([
        lambda: acting.post(f"{base_url}/listings/{listing}/buy", params={"buyer_id": buyer}, timeout=30),
        sbc_post(base_url, seller, "duo", [target, partner]),
    ])
    assert buy.status_code == 200 and sub.status_code in (403, 409), (buy.text, sub.text)


def test_sbc_racing_a_listing_exactly_one_wins(api, base_url, db):
    alice = make_user(api, "alice")
    make_challenge(db, max_completions=100)
    wins = {"sbc": 0, "listing": 0}
    for attempt in range(20):
        target, partner = cards_for(api, db, alice, 2, prefix=f"R{attempt}-")
        sub, listing = run_parallel([
            sbc_post(base_url, alice, "duo", [target, partner]),
            lambda: acting.post(f"{base_url}/listings", json={"card_instance_id": target, "seller_id": alice, "price": "10"},
                                timeout=30),
        ])
        codes = sorted([sub.status_code, listing.status_code])
        assert codes == [200, 409], (attempt, sub.text, listing.text)
        wins["sbc" if sub.status_code == 200 else "listing"] += 1
        active = db.execute("SELECT count(*) AS n FROM listings WHERE card_instance_id = %s AND status = 'ACTIVE'",
                            (target,)).fetchone()["n"]
        assert burned(db, [target]) == [sub.status_code == 200]
        assert active == (1 if listing.status_code == 200 else 0)


def test_sbc_racing_a_battle_challenge_exactly_one_wins(api, base_url, db):
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    make_challenge(db, max_completions=100)
    for attempt in range(10):
        deck = cards_for(api, db, alice, 6, prefix=f"D{attempt}-")
        sub, battle = run_parallel([
            sbc_post(base_url, alice, "duo", deck[:2]),
            lambda: acting.post(f"{base_url}/battles", json={"challenger_id": alice, "opponent_id": bob, "card_ids": deck},
                                timeout=30),
        ])
        assert sorted([sub.status_code, battle.status_code]) == [200, 409], (attempt, sub.text, battle.text)
        if battle.status_code == 200:  # free the deck's other cards for the next round's accounting
            api.post(f"/battles/{battle.json()['id']}/decline", json={"user_id": bob})
    # "no burned card in a live battle deck" is asserted by assert_invariants().


def test_double_submit_completes_once(api, base_url, db):
    alice = make_user(api, "alice")
    make_challenge(db, reward={"runs": 100})
    same = cards_for(api, db, alice, 2, prefix="S")
    results = run_parallel([sbc_post(base_url, alice, "duo", same) for _ in range(5)])
    assert sorted(r.status_code for r in results) == [200] + [409] * 4, [r.text for r in results]

    # Different cards, same challenge: the completion limit still holds.
    make_challenge(db, slug="trio", requirements=[{"type": "count", "n": 2}], reward={"runs": 100})
    other = cards_for(api, db, alice, 6, prefix="O")
    results = run_parallel([sbc_post(base_url, alice, "trio", other[i:i + 2]) for i in (0, 2, 4)])
    assert sorted(r.status_code for r in results) == [200, 409, 409], [r.text for r in results]
    assert db.execute("SELECT count(*) AS n FROM sbc_completions").fetchone()["n"] == 2
    assert balance(db, alice) == SIGNUP_GRANT + 200
