"""Trading: the flow, eligibility and limits, and races against the market and SBCs."""

import httpx
import pytest
from psycopg.types.json import Jsonb

from backend import trades as trade_rules
from backend.tests.conftest import acting, make_definition, make_player, make_user, mint, run_parallel
from backend.tests.sessions import session_cookie


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_eligible(db, *users):
    """Old enough, with enough finished battles (against a sparring account)."""
    sparring = db.execute(
        "INSERT INTO users (username) VALUES ('sparring_' || substr(md5(random()::text), 1, 8)) RETURNING id"
    ).fetchone()["id"]
    for user in users:
        db.execute("UPDATE users SET created_at = now() - interval '4 days' WHERE id = %s", (user,))
        for _ in range(trade_rules.MIN_BATTLES_FINISHED):
            db.execute(
                """
                INSERT INTO battles (challenger_id, opponent_id, status, challenger_card_ids, expires_at, winner_id, finished_at)
                VALUES (%s, %s, 'FINISHED', '{}', now(), %s, now())
                """,
                (user, sparring, user),
            )


def cards_for(api, db, owner, n, prefix):
    return [mint(api, make_definition(db, make_player(db, f"{prefix}{i}")), owner) for i in range(n)]


def username(db, user):
    return db.execute("SELECT username FROM users WHERE id = %s", (user,)).fetchone()["username"]


def propose(api, db, proposer, recipient, offered, requested):
    return api.post("/trades", params={"user_id": proposer},
                    json={"recipient": username(db, recipient), "offered_card_ids": offered, "requested_card_ids": requested})


def act(api, user, trade_id, action):
    return api.post(f"/trades/{trade_id}/{action}", params={"user_id": user})


def owners(db, ids):
    rows = db.execute("SELECT id, owner_id FROM card_instances WHERE id = ANY(%s)", (ids,)).fetchall()
    return {r["id"]: r["owner_id"] for r in rows}


def status(db, trade_id):
    return db.execute("SELECT status FROM trades WHERE id = %s", (trade_id,)).fetchone()["status"]


def burn(api, db, user, ids):
    """Destroy cards the legitimate way: an SBC submission."""
    slug = f"burn-{ids[0]}"
    db.execute(
        """
        INSERT INTO sbc_challenges (slug, title, description, requirements, reward, max_completions_per_user)
        VALUES (%s, 'Burn', 'test', %s, %s, 1)
        """,
        (slug, Jsonb([{"type": "count", "n": len(ids)}]), Jsonb({"runs": 1})),
    )
    r = api.post(f"/sbcs/{slug}/submit", params={"user_id": user}, json={"card_ids": ids})
    assert r.status_code == 200, r.text


@pytest.fixture
def pair(api, db):
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    make_eligible(db, alice, bob)
    return alice, bob


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

def test_accepted_trade_swaps_cards_and_records_provenance(api, db, base_url, pair):
    alice, bob = pair
    a1, a2 = cards_for(api, db, alice, 2, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")
    assert httpx.put(f"{base_url}/me/showcase", json={"card_ids": [a1]}, cookies=session_cookie(alice)).status_code == 200

    r = propose(api, db, alice, bob, [a1, a2], [b1])
    assert r.status_code == 200, r.text
    trade = r.json()
    assert trade["status"] == "PENDING" and trade["role"] == "proposer"
    assert [c["card_instance_id"] for c in trade["offered"]] == [a1, a2]
    assert [c["card_instance_id"] for c in trade["requested"]] == [b1]
    assert owners(db, [a1, a2, b1]) == {a1: alice, a2: alice, b1: bob}  # offering doesn't move anything

    assert [t["id"] for t in api.get("/trades", params={"user_id": bob}).json()["incoming"]] == [trade["id"]]
    assert [t["id"] for t in api.get("/trades", params={"user_id": alice}).json()["outgoing"]] == [trade["id"]]

    r = act(api, bob, trade["id"], "accept")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ACCEPTED"
    assert owners(db, [a1, a2, b1]) == {a1: bob, a2: bob, b1: alice}

    history = api.get(f"/card-instances/{a1}/history").json()
    assert history[-1]["event_type"] == "TRADED" and history[-1]["related_trade_id"] == trade["id"]
    assert (history[-1]["from_username"], history[-1]["to_username"]) == ("alice", "bob")
    assert api.get("/profiles/alice").json()["showcase"] == []  # the pinned card left her hands
    mine = api.get("/trades", params={"user_id": alice}).json()
    assert mine["outgoing"] == [] and mine["history"][0]["status"] == "ACCEPTED"


def test_decline_cancel_and_who_may_do_what(api, db, base_url, pair):
    alice, bob = pair
    carol = make_user(api, "carol")
    (a1,) = cards_for(api, db, alice, 1, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")

    t1 = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
    assert act(api, alice, t1, "accept").status_code == 403       # proposer can't accept their own offer
    assert act(api, alice, t1, "decline").status_code == 403
    assert act(api, bob, t1, "cancel").status_code == 403
    assert act(api, carol, t1, "accept").status_code == 404       # outsiders can't even see it
    assert api.get(f"/trades/{t1}", params={"user_id": carol}).status_code == 404
    assert httpx.post(f"{base_url}/trades/{t1}/accept").status_code == 401
    assert act(api, bob, t1, "decline").json()["status"] == "DECLINED"
    assert act(api, bob, t1, "accept").status_code == 409

    t2 = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
    assert act(api, alice, t2, "cancel").json()["status"] == "CANCELLED"
    assert act(api, bob, t2, "accept").status_code == 409
    assert owners(db, [a1, b1]) == {a1: alice, b1: bob}


def test_offers_expire(api, db, pair):
    alice, bob = pair
    (a1,) = cards_for(api, db, alice, 1, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")
    trade = propose(api, db, alice, bob, [a1], [b1]).json()
    assert trade["expires_at"]
    db.execute("UPDATE trades SET expires_at = now() - interval '1 second' WHERE id = %s", (trade["id"],))
    assert api.get("/trades", params={"user_id": bob}).json()["history"][0]["status"] == "EXPIRED"
    r = act(api, bob, trade["id"], "accept")
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert status(db, trade["id"]) == "EXPIRED"


def test_proposals_are_validated(api, db, base_url, pair):
    alice, bob = pair
    a = cards_for(api, db, alice, 5, "A")
    b = cards_for(api, db, bob, 4, "B")

    assert propose(api, db, alice, bob, [], [b[0]]).status_code == 400                 # nothing offered
    assert propose(api, db, alice, bob, a[:4], [b[0]]).status_code == 400              # more than 3
    assert propose(api, db, alice, bob, [a[0]], b[:4]).status_code == 400
    assert propose(api, db, alice, bob, [a[0], a[0]], [b[0]]).status_code == 400       # same card twice
    assert propose(api, db, alice, alice, [a[0]], [a[1]]).status_code == 400           # yourself
    assert propose(api, db, alice, bob, [b[1]], [b[0]]).status_code == 403             # not yours to offer
    assert propose(api, db, alice, bob, [a[0]], [a[1]]).status_code == 400             # not theirs to give
    assert api.post("/trades", params={"user_id": alice}, json={
        "recipient": "nobody", "offered_card_ids": [a[0]], "requested_card_ids": [b[0]]}).status_code == 404
    # Card-for-card only: Runs can't be slipped in.
    r = api.post("/trades", params={"user_id": alice}, json={
        "recipient": "bob", "offered_card_ids": [a[0]], "requested_card_ids": [b[0]], "runs": 500})
    assert r.status_code == 422
    assert httpx.post(f"{base_url}/trades", json={
        "recipient": "bob", "offered_card_ids": [a[0]], "requested_card_ids": [b[0]]}).status_code == 401

    burn(api, db, alice, [a[3], a[4]])
    r = propose(api, db, alice, bob, [a[3]], [b[0]])
    assert r.status_code == 409 and "destroyed" in r.json()["detail"]
    assert db.execute("SELECT count(*) AS n FROM trades").fetchone()["n"] == 0


# ---------------------------------------------------------------------------
# Eligibility and limits
# ---------------------------------------------------------------------------

def test_new_or_battle_less_accounts_cant_trade(api, db):
    alice, bob = make_user(api, "alice"), make_user(api, "bob")
    (a1,) = cards_for(api, db, alice, 1, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")

    r = propose(api, db, alice, bob, [a1], [b1])
    assert r.status_code == 403 and "You can't trade yet" in r.json()["detail"]
    e = api.get("/trades/eligibility", params={"user_id": alice}).json()
    assert not e["eligible"] and len(e["reasons"]) == 2

    make_eligible(db, alice)
    r = propose(api, db, alice, bob, [a1], [b1])
    assert r.status_code == 403 and "bob can't trade yet" in r.json()["detail"]    # the other side counts too

    # Old enough but short of battles.
    db.execute("UPDATE users SET created_at = now() - interval '10 days' WHERE id = %s", (bob,))
    r = propose(api, db, alice, bob, [a1], [b1])
    assert r.status_code == 403 and "finished battles" in r.json()["detail"]

    # Enough battles but too new.
    make_eligible(db, bob)
    db.execute("UPDATE users SET created_at = now() - interval '2 days' WHERE id = %s", (bob,))
    r = propose(api, db, alice, bob, [a1], [b1])
    assert r.status_code == 403 and "days old" in r.json()["detail"]

    db.execute("UPDATE users SET created_at = now() - interval '4 days' WHERE id = %s", (bob,))
    assert propose(api, db, alice, bob, [a1], [b1]).status_code == 200
    assert api.get("/trades/eligibility", params={"user_id": alice}).json()["eligible"]


def test_eligibility_is_rechecked_on_accept(api, db, pair):
    alice, bob = pair
    (a1,) = cards_for(api, db, alice, 1, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")
    trade = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
    db.execute("DELETE FROM battles WHERE %s IN (challenger_id, opponent_id)", (alice,))
    r = act(api, bob, trade, "accept")
    assert r.status_code == 403 and "alice can't trade yet" in r.json()["detail"]
    assert status(db, trade) == "PENDING"


def test_daily_accept_limit(api, db):
    alice = make_user(api, "alice")
    partners = [make_user(api, f"partner{i}") for i in range(trade_rules.MAX_ACCEPTED_TRADES_PER_DAY + 1)]
    make_eligible(db, alice, *partners)
    mine = cards_for(api, db, alice, len(partners), "A")

    trade_ids = []
    for i, partner in enumerate(partners):
        (theirs,) = cards_for(api, db, partner, 1, f"P{i}-")
        trade_ids.append(propose(api, db, alice, partner, [mine[i]], [theirs]).json()["id"])
    for partner, trade in zip(partners[:-1], trade_ids[:-1]):
        assert act(api, partner, trade, "accept").status_code == 200

    r = act(api, partners[-1], trade_ids[-1], "accept")
    assert r.status_code == 409 and "alice already made 5 trades" in r.json()["detail"]
    assert status(db, trade_ids[-1]) == "PENDING"                      # try again tomorrow
    assert api.get("/trades/eligibility", params={"user_id": alice}).json()["at_daily_limit"]

    # A day later the window has moved on.
    db.execute("UPDATE trades SET resolved_at = resolved_at - interval '25 hours' WHERE status = 'ACCEPTED'")
    assert act(api, partners[-1], trade_ids[-1], "accept").status_code == 200


# ---------------------------------------------------------------------------
# Cards that are busy or gone
# ---------------------------------------------------------------------------

def test_listing_blocks_accept_without_voiding_the_trade(api, db, pair):
    alice, bob = pair
    (a1,) = cards_for(api, db, alice, 1, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")
    trade = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
    listing = api.post("/listings", json={"card_instance_id": a1, "seller_id": alice, "price": "50"}).json()["id"]

    view = api.get(f"/trades/{trade}", params={"user_id": bob}).json()
    assert view["problems"] == [{"permanent": False, "message": "A0 #1 is listed on the market"}]
    r = act(api, bob, trade, "accept")
    assert r.status_code == 409 and "listed" in r.json()["detail"]
    assert status(db, trade) == "PENDING"

    api.post(f"/listings/{listing}/cancel", params={"seller_id": alice})
    assert act(api, bob, trade, "accept").status_code == 200


def test_battle_deck_blocks_accept_and_isnt_revealed(api, db, pair):
    alice, bob = pair
    deck = cards_for(api, db, alice, 6, "D")
    (b1,) = cards_for(api, db, bob, 1, "B")
    trade = propose(api, db, alice, bob, [deck[0]], [b1]).json()["id"]
    carol = make_user(api, "carol")
    assert api.post("/battles", json={"challenger_id": alice, "opponent_id": carol, "card_ids": deck}).status_code == 200

    view = api.get(f"/trades/{trade}", params={"user_id": bob}).json()
    assert "battle" not in str(view)  # bob mustn't learn what's in alice's pending deck
    r = act(api, bob, trade, "accept")
    assert r.status_code == 409 and "battle" not in r.json()["detail"]
    assert status(db, trade) == "PENDING"
    own = api.get(f"/trades/{trade}", params={"user_id": alice}).json()
    assert "battle deck" in own["problems"][0]["message"]  # alice may see her own card's reason


def test_burned_card_voids_the_trade(api, db, pair):
    alice, bob = pair
    a1, a2 = cards_for(api, db, alice, 2, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")
    trade = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
    burn(api, db, alice, [a1, a2])
    r = act(api, bob, trade, "accept")
    assert r.status_code == 409 and "destroyed" in r.json()["detail"]
    assert status(db, trade) == "INVALID"
    assert owners(db, [b1]) == {b1: bob}


def test_card_in_two_pending_trades_moves_once(api, db):
    alice, bob, carol = make_user(api, "alice"), make_user(api, "bob"), make_user(api, "carol")
    make_eligible(db, alice, bob, carol)
    (a1,) = cards_for(api, db, alice, 1, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")
    (c1,) = cards_for(api, db, carol, 1, "C")
    t_bob = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
    t_carol = propose(api, db, alice, carol, [a1], [c1]).json()["id"]

    assert act(api, bob, t_bob, "accept").status_code == 200
    view = api.get(f"/trades/{t_carol}", params={"user_id": carol}).json()
    assert view["problems"][0]["permanent"]                          # carol can see it's dead...
    r = act(api, carol, t_carol, "accept")                           # ...and accepting marks it so
    assert r.status_code == 409 and "changed hands" in r.json()["detail"]
    assert status(db, t_carol) == "INVALID"
    assert owners(db, [a1, b1, c1]) == {a1: bob, b1: alice, c1: carol}


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

@pytest.fixture
def no_daily_limit(monkeypatch):
    # The race tests accept many trades in a row; the limit has its own test.
    monkeypatch.setattr(trade_rules, "MAX_ACCEPTED_TRADES_PER_DAY", 10_000)


def accept_call(base_url, user, trade_id):
    return lambda: acting.post(f"{base_url}/trades/{trade_id}/accept", params={"user_id": user}, timeout=30)


def test_double_accept_completes_once(api, base_url, db, pair):
    alice, bob = pair
    (a1,) = cards_for(api, db, alice, 1, "A")
    (b1,) = cards_for(api, db, bob, 1, "B")
    trade = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
    results = run_parallel([accept_call(base_url, bob, trade) for _ in range(5)])
    assert sorted(r.status_code for r in results) == [200] + [409] * 4, [r.text for r in results]
    assert owners(db, [a1, b1]) == {a1: bob, b1: alice}
    # assert_invariants(): exactly one TRADED event per card for this trade.


def test_card_in_two_trades_accepted_at_once_moves_once(api, base_url, db, no_daily_limit):
    alice, bob, carol = make_user(api, "alice"), make_user(api, "bob"), make_user(api, "carol")
    make_eligible(db, alice, bob, carol)
    for attempt in range(10):
        (a1,) = cards_for(api, db, alice, 1, f"A{attempt}-")
        (b1,) = cards_for(api, db, bob, 1, f"B{attempt}-")
        (c1,) = cards_for(api, db, carol, 1, f"C{attempt}-")
        t_bob = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
        t_carol = propose(api, db, alice, carol, [a1], [c1]).json()["id"]

        r_bob, r_carol = run_parallel([accept_call(base_url, bob, t_bob), accept_call(base_url, carol, t_carol)])
        assert sorted([r_bob.status_code, r_carol.status_code]) == [200, 409], (attempt, r_bob.text, r_carol.text)
        winner, loser = (t_bob, t_carol) if r_bob.status_code == 200 else (t_carol, t_bob)
        assert (status(db, winner), status(db, loser)) == ("ACCEPTED", "INVALID")
        assert owners(db, [a1])[a1] == (bob if winner == t_bob else carol)


def test_accept_racing_a_buy_exactly_one_wins(api, base_url, db, pair, no_daily_limit):
    """Alice's card is listed and also offered to Bob. Carol buys it while
    Alice delists it so Bob can accept. Either the buy lands or the trade
    does, never both, and never a deadlock."""
    alice, bob = pair
    carol = make_user(api, "carol")
    outcomes = {"buy": 0, "trade": 0}
    for attempt in range(15):
        (a1,) = cards_for(api, db, alice, 1, f"A{attempt}-")
        (b1,) = cards_for(api, db, bob, 1, f"B{attempt}-")
        trade = propose(api, db, alice, bob, [a1], [b1]).json()["id"]
        listing = api.post("/listings", json={"card_instance_id": a1, "seller_id": alice, "price": "10"}).json()["id"]

        def delist_then_accept():
            acting.post(f"{base_url}/listings/{listing}/cancel", params={"seller_id": alice}, timeout=30)
            return accept_call(base_url, bob, trade)()

        buy, acc = run_parallel([
            lambda: acting.post(f"{base_url}/listings/{listing}/buy", params={"buyer_id": carol}, timeout=30),
            delist_then_accept,
        ])
        codes = sorted([buy.status_code, acc.status_code])
        assert codes == [200, 409], (attempt, buy.text, acc.text)
        owner = owners(db, [a1])[a1]
        if buy.status_code == 200:
            outcomes["buy"] += 1
            assert owner == carol and status(db, trade) == "INVALID"
        else:
            outcomes["trade"] += 1
            assert owner == bob and status(db, trade) == "ACCEPTED"


def test_accept_racing_an_sbc_exactly_one_wins(api, base_url, db, pair, no_daily_limit):
    alice, bob = pair
    db.execute(
        """
        INSERT INTO sbc_challenges (slug, title, description, requirements, reward, max_completions_per_user)
        VALUES ('duo', 'Duo', 'test', %s, %s, 1000)
        """,
        (Jsonb([{"type": "count", "n": 2}]), Jsonb({"runs": 1})),
    )
    for attempt in range(15):
        a1, a2 = cards_for(api, db, alice, 2, f"A{attempt}-")
        (b1,) = cards_for(api, db, bob, 1, f"B{attempt}-")
        trade = propose(api, db, alice, bob, [a1], [b1]).json()["id"]

        acc, sub = run_parallel([
            accept_call(base_url, bob, trade),
            lambda: acting.post(f"{base_url}/sbcs/duo/submit", params={"user_id": alice},
                                json={"card_ids": [a1, a2]}, timeout=30),
        ])
        ok = [r.status_code == 200 for r in (acc, sub)]
        assert ok.count(True) == 1, (attempt, acc.status_code, acc.text, sub.status_code, sub.text)
        assert {acc.status_code, sub.status_code} <= {200, 403, 409}
        burned = db.execute("SELECT burned_at IS NOT NULL AS b FROM card_instances WHERE id = %s", (a1,)).fetchone()["b"]
        if acc.status_code == 200:
            assert owners(db, [a1])[a1] == bob and not burned
        else:
            assert burned and status(db, trade) == "INVALID"
