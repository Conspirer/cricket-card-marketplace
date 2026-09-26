"""Concurrency guarantees of the marketplace and pack opening."""

import httpx

from backend.main import SIGNUP_GRANT

from backend.tests.conftest import acting, grant, make_definition, make_player, make_pool, make_user, mint, run_parallel


def test_concurrent_buys_exactly_one_succeeds(api, base_url, db):
    seller = make_user(api, "seller")
    card = mint(api, make_definition(db, make_player(db, "Hot Card")), seller)
    listing = api.post("/listings", json={"card_instance_id": card, "seller_id": seller, "price": "300"}).json()["id"]
    buyers = [make_user(api, f"buyer{i}") for i in range(10)]

    results = run_parallel([
        lambda b=b: acting.post(f"{base_url}/listings/{listing}/buy", params={"buyer_id": b}, timeout=30)
        for b in buyers
    ])

    codes = sorted(r.status_code for r in results)
    assert codes == [200] + [409] * 9, codes
    winner = buyers[[r.status_code for r in results].index(200)]
    assert db.execute("SELECT owner_id FROM card_instances WHERE id = %s", (card,)).fetchone()["owner_id"] == winner
    # Seller got the price minus the 5% burn, exactly once.
    assert db.execute("SELECT balance FROM users WHERE id = %s", (seller,)).fetchone()["balance"] == SIGNUP_GRANT + 300 - 15


def test_concurrent_pack_openings_no_deadlocks(api, base_url, db):
    make_pool(db)
    users = [make_user(api, f"opener{i}") for i in range(2)]
    for u in users:
        grant(api, u, 5000)

    results = run_parallel([
        lambda i=i: acting.post(
            f"{base_url}/packs/open",
            json={"user_id": users[i % 2], "pack_type": "premium" if i % 3 == 0 else "standard"},
            timeout=60,
        )
        for i in range(20)
    ])

    assert [r.status_code for r in results] == [200] * 20, [r.text for r in results if r.status_code != 200]
    assert db.execute("SELECT count(*) AS n FROM card_instances").fetchone()["n"] == 60
    # Serials unique and gap-free is asserted for every test by assert_invariants().


def test_packs_only_roll_active_definitions(api, db):
    make_pool(db)
    inactive = make_definition(db, make_player(db, "Retired"), "Common", 500, active=False)
    user = make_user(api, "opener")
    grant(api, user, 10000)
    for _ in range(30):
        assert api.post("/packs/open", json={"user_id": user, "pack_type": "standard"}).status_code == 200
    assert db.execute(
        "SELECT count(*) AS n FROM card_instances WHERE card_definition_id = %s", (inactive,)
    ).fetchone()["n"] == 0


def test_concurrent_cancel_and_relist_same_card(api, base_url, db):
    """Cancel listing A while the seller lists the same card again (listing B).

    create-listing locks the card row, then its INSERT waits on the one-active-
    listing index for A. cancel updates A, then its ownership-event INSERT
    needs a key-share lock on the same card row. With FOR UPDATE on the card
    those two wait on each other: a deadlock.
    """
    seller = make_user(api, "seller")
    definition = make_definition(db, make_player(db, "Relisted"))
    outcomes = []
    for attempt in range(25):
        card = mint(api, definition, seller)
        first = api.post("/listings", json={"card_instance_id": card, "seller_id": seller, "price": "100"}).json()["id"]

        cancel, relist = run_parallel([
            lambda: acting.post(f"{base_url}/listings/{first}/cancel", params={"seller_id": seller}, timeout=30),
            lambda: acting.post(
                f"{base_url}/listings",
                json={"card_instance_id": card, "seller_id": seller, "price": "150"},
                timeout=30,
            ),
        ])

        assert cancel.status_code == 200, (attempt, cancel.text)
        assert relist.status_code in (200, 409), (attempt, relist.status_code, relist.text)
        outcomes.append(relist.status_code)
        active = db.execute(
            "SELECT count(*) AS n FROM listings WHERE card_instance_id = %s AND status = 'ACTIVE'", (card,)
        ).fetchone()["n"]
        assert active == (1 if relist.status_code == 200 else 0)


def test_relist_racing_a_buy_never_lists_someone_elses_card(api, base_url, db):
    seller = make_user(api, "seller")
    buyer = make_user(api, "buyer")
    definition = make_definition(db, make_player(db, "Contested"))
    for attempt in range(15):
        card = mint(api, definition, seller)
        listing = api.post("/listings", json={"card_instance_id": card, "seller_id": seller, "price": "10"}).json()["id"]

        buy, relist = run_parallel([
            lambda: acting.post(f"{base_url}/listings/{listing}/buy", params={"buyer_id": buyer}, timeout=30),
            lambda: acting.post(
                f"{base_url}/listings",
                json={"card_instance_id": card, "seller_id": seller, "price": "20"},
                timeout=30,
            ),
        ])

        assert buy.status_code == 200, (attempt, buy.text)
        assert relist.status_code in (403, 409), (attempt, relist.status_code, relist.text)
        bad = db.execute(
            """
            SELECT l.id FROM listings l JOIN card_instances c ON c.id = l.card_instance_id
            WHERE l.status = 'ACTIVE' AND l.seller_id <> c.owner_id
            """
        ).fetchall()
        assert not bad
