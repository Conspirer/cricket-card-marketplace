from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from backend.database import get_connection
from backend.auth import current_user_id, require_self, router as auth_router
from backend.social import router as social_router
from backend.sbc_routes import router as sbc_router
from backend.trades import router as trade_router
from backend.schemas import UserResponse, PlayerCreate, PlayerResponse, CardDefinitionCreate, CardDefinitionResponse, CardInstanceCreate, CardInstanceResponse, CardInstanceDetailResponse, ListingCreate, ListingResponse, GrantCreate, PackOpenRequest, PackOpenResponse, CollectionCardResponse, MarketplaceListingResponse, CardEventResponse, SaleResponse, PlayerDetailResponse, PlayerThemeStatsResponse
from backend.packs import PACK_TYPES, pick_definitions
from backend.themes import THEMES
from backend.battle_routes import router as battle_router
from backend.battles import TIER_ORDER, credits_for_tier
from psycopg.errors import UniqueViolation
from decimal import Decimal, ROUND_HALF_UP
import os

DEV_FAUCET_ENABLED = os.environ.get("DEV_FAUCET_ENABLED", "").lower() in ("1", "true", "yes")

# The JSON API. It's mounted at /api by the root app (backend/web.py), which
# also serves the built React app, so the whole site is one origin.
api = FastAPI(title="Crease API")

# The Vite dev server runs on a different origin, so browsers need CORS headers.
api.add_middleware(
    CORSMiddleware,
    # Any local dev origin (localhost or 127.0.0.1, any port).
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

api.include_router(battle_router)
api.include_router(auth_router)
api.include_router(social_router)
api.include_router(sbc_router)
api.include_router(trade_router)

SIGNUP_GRANT = Decimal("10000.00")
MARKET_FEE_RATE = Decimal("0.05")


def apply_balance_change(
    cursor,
    user_id,
    delta,
    reason,
    related_listing_id=None,
    related_pack_opening_id=None,
    related_sbc_completion_id=None,
):
    # The only place balances change: the balance update and its ledger row are
    # written together, inside the caller's transaction, so they can never drift.
    cursor.execute(
        """
        UPDATE users
        SET balance = balance + %s
        WHERE id = %s
        RETURNING id, balance;
        """,
        (delta, user_id),
    )
    updated_user = cursor.fetchone()

    cursor.execute(
        """
        INSERT INTO currency_ledger
            (user_id, delta, reason, related_listing_id, related_pack_opening_id, related_sbc_completion_id)
        VALUES (%s, %s, %s, %s, %s, %s);
        """,
        (user_id, delta, reason, related_listing_id, related_pack_opening_id, related_sbc_completion_id),
    )

    return updated_user


def mint_card_instance(cursor, card_definition_id, owner_id, pack_opening_id=None):
    # The only place cards are created. Bumping the counter with a guarded UPDATE
    # row-locks the definition, so concurrent mints queue up and each gets the
    # next serial; the WHERE clause enforces max_supply in the same statement.
    cursor.execute(
        """
        UPDATE card_definitions
        SET minted_count = minted_count + 1
        WHERE id = %s AND minted_count < max_supply
        RETURNING minted_count;
        """,
        (card_definition_id,),
    )

    counter = cursor.fetchone()

    if counter is None:
        return None

    cursor.execute(
        """
        INSERT INTO card_instances
            (card_definition_id, serial_number, owner_id, pack_opening_id)
        VALUES (%s, %s, %s, %s)
        RETURNING id, card_definition_id, serial_number, owner_id;
        """,
        (card_definition_id, counter["minted_count"], owner_id, pack_opening_id),
    )

    created = cursor.fetchone()

    record_card_event(
        cursor,
        created["id"],
        "PULLED" if pack_opening_id else "MINTED",
        to_user_id=owner_id,
        related_pack_opening_id=pack_opening_id,
    )

    return created


def record_card_event(
    cursor,
    card_instance_id,
    event_type,
    from_user_id=None,
    to_user_id=None,
    price=None,
    related_listing_id=None,
    related_pack_opening_id=None,
    related_trade_id=None,
):
    # Called inside the same transaction as the change it describes, so a card
    # can never change hands (or get listed/delisted) without a history row.
    cursor.execute(
        """
        INSERT INTO card_ownership_events (
            card_instance_id,
            event_type,
            from_user_id,
            to_user_id,
            price,
            related_listing_id,
            related_pack_opening_id,
            related_trade_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
        """,
        (
            card_instance_id,
            event_type,
            from_user_id,
            to_user_id,
            price,
            related_listing_id,
            related_pack_opening_id,
            related_trade_id,
        ),
    )

@api.get("/health")
def health_check():
    return {"status": "ok"}

@api.get("/users", response_model=list[UserResponse])
def get_users():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, username, balance
                FROM users
                ORDER BY id
                """
            )
            users = cursor.fetchall()
    return users

# Dev tools: the faucet (mints Runs from nothing) and the admin endpoints that
# create players, card definitions and cards. Off unless DEV_FAUCET_ENABLED is
# set; when off the routes aren't registered at all (plain 404).
def dev_grant(user_id: int, grant: GrantCreate):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM users WHERE id = %s FOR NO KEY UPDATE;
                """,
                (user_id,),
            )

            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="User not found")

            apply_balance_change(cursor, user_id, grant.amount, "MINT_DEV_GRANT")

            cursor.execute(
                """
                SELECT id, username, balance FROM users WHERE id = %s;
                """,
                (user_id,),
            )
            user = cursor.fetchone()
    return user


def register_dev_tools():
    api.post("/dev/users/{user_id}/grant", response_model=UserResponse)(dev_grant)
    api.post("/players", response_model=PlayerResponse)(create_player)
    api.post("/card-definitions", response_model=CardDefinitionResponse)(create_card_definition)
    api.post("/card-instances", response_model=CardInstanceResponse)(create_card_instance)

def create_player(player:PlayerCreate):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO players(name, country, role)
                VALUES(%s, %s, %s)
                RETURNING id, name, country, role;
                """,
                (player.name, player.country, player.role)
            )

            created_player=cursor.fetchone();
    return created_player

@api.get("/players", response_model=list[PlayerResponse])
def get_players(
    search: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, country, role
                FROM players
                WHERE %(search)s::text IS NULL OR name ILIKE '%%' || %(search)s || '%%'
                ORDER BY id
                LIMIT %(limit)s OFFSET %(offset)s;
                """,
                {"search": search, "limit": limit, "offset": offset},
            )

            players = cursor.fetchall()
    return players;

@api.get("/players/{player_id}", response_model=PlayerDetailResponse)
def get_player(player_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, country, role, batting_hand, bowling_type, is_keeper
                FROM players
                WHERE id = %s;
                """,
                (player_id,),
            )

            player = cursor.fetchone()

    if player is None:
        raise HTTPException(
            status_code=404,
            detail="Player not found",
        )

    return player

def player_in_active_battle(cursor, player_id):
    # Battles hide every stat of every deck card until it is called, so while
    # a player is in any active deck, their stats aren't served anywhere.
    # (Without auth we can't tell who is asking, so this applies to everyone.)
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM battles b
            JOIN card_instances ci
                ON ci.id = ANY (b.challenger_card_ids || COALESCE(b.opponent_card_ids, '{}'))
            JOIN card_definitions d ON d.id = ci.card_definition_id
            WHERE b.status = 'ACTIVE' AND d.player_id = %s
        ) AS hidden;
        """,
        (player_id,),
    )
    return cursor.fetchone()["hidden"]


@api.get("/players/{player_id}/theme-stats", response_model=PlayerThemeStatsResponse)
def get_player_theme_stats(player_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM players WHERE id = %s;", (player_id,))
            if cursor.fetchone() is None:
                raise HTTPException(status_code=404, detail="Player not found")

            if player_in_active_battle(cursor, player_id):
                return {"hidden": True, "themes": []}

            cursor.execute(
                """
                SELECT theme, stats, matches, source, verified, as_of, notes
                FROM player_theme_stats
                WHERE player_id = %s;
                """,
                (player_id,),
            )
            rows = {row["theme"]: row for row in cursor.fetchall()}

    themes = []
    for key, config in THEMES.items():
        row = rows.get(key)
        if row is None:
            continue
        themes.append({
            "theme": key,
            "label": config["label"],
            "tier": config["tier"],
            "matches": row["matches"],
            # Unverified figures are never shown: a wrong number is worse than none.
            "stats": row["stats"] if row["verified"] else None,
            "source": row["source"],
            "verified": row["verified"],
            "as_of": row["as_of"],
            "notes": row["notes"],
        })
    return {"hidden": False, "themes": themes}


def create_card_definition(card: CardDefinitionCreate):
    with get_connection() as connection:
        with connection.cursor() as cursor:
                cursor.execute(
                """
                SELECT id
                FROM players
                WHERE id = %s;
                """,
                (card.player_id,),
                )

                player = cursor.fetchone()

                if player is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Player not found",
                    )
                cursor.execute(
                    """
                    INSERT INTO card_definitions
                        (player_id, rarity, max_supply, is_active)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, player_id, rarity, max_supply, minted_count, is_active;
                    """,
                    (
                        card.player_id,
                        card.rarity,
                        card.max_supply,
                        card.is_active,
                    ),
                )

                created_card = cursor.fetchone()
    return created_card


def create_card_instance(card: CardInstanceCreate):
    with get_connection() as connection:
        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT id
                FROM card_definitions
                WHERE id = %s;
                """,
                (card.card_definition_id,),
            )

            card_definition = cursor.fetchone()

            if card_definition is None:
                raise HTTPException(
                    status_code=404,
                    detail="Card definition not found",
                )

            cursor.execute(
                """
                SELECT id
                FROM users
                WHERE id = %s;
                """,
                (card.owner_id,),
            )

            owner = cursor.fetchone()

            if owner is None:
                raise HTTPException(
                    status_code=404,
                    detail="User not found",
                )

            created_card = mint_card_instance(
                cursor, card.card_definition_id, card.owner_id
            )

            if created_card is None:
                raise HTTPException(
                    status_code=409,
                    detail="This card definition has reached its max supply",
                )

    return created_card

@api.get(
    "/card-instances",
    response_model=list[CardInstanceDetailResponse],
)
def get_card_instances():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    card_instances.id,
                    card_instances.card_definition_id,
                    card_instances.serial_number,
                    card_definitions.rarity,
                    players.name AS player_name,
                    players.role AS player_role,
                    players.country AS player_country,
                    card_definitions.max_supply,
                    card_definitions.edition_label,
                    card_instances.burned_at,
                    players.id AS player_id,
                    users.username AS owner_username
                FROM card_instances
                JOIN card_definitions
                    ON card_instances.card_definition_id = card_definitions.id
                JOIN players
                    ON card_definitions.player_id = players.id
                JOIN users
                    ON card_instances.owner_id = users.id
                WHERE card_instances.burned_at IS NULL
                ORDER BY card_instances.id;
                """
            )

            cards = cursor.fetchall()

    return cards

@api.get(
    "/card-instances/{card_id}",
    response_model=CardInstanceDetailResponse,
)
def get_card_instance(card_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    card_instances.id,
                    card_instances.card_definition_id,
                    card_instances.serial_number,
                    card_definitions.rarity,
                    players.name AS player_name,
                    players.role AS player_role,
                    players.country AS player_country,
                    card_definitions.max_supply,
                    card_definitions.edition_label,
                    card_instances.burned_at,
                    players.id AS player_id,
                    users.username AS owner_username
                FROM card_instances
                JOIN card_definitions
                    ON card_instances.card_definition_id = card_definitions.id
                JOIN players
                    ON card_definitions.player_id = players.id
                JOIN users
                    ON card_instances.owner_id = users.id
                WHERE card_instances.id = %s;
                """,
                (card_id,),
            )

            card = cursor.fetchone()

    if card is None:
        raise HTTPException(
            status_code=404,
            detail="Card instance not found",
        )

    return card

@api.post("/listings", response_model=ListingResponse)
def create_listing(listing:ListingCreate, me: int = Depends(current_user_id)):
    require_self(me, listing.seller_id)
    with get_connection() as connection:
        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT owner_id, burned_at
                FROM card_instances
                WHERE id = %s
                FOR NO KEY UPDATE;
                """,
                (listing.card_instance_id,),
            )

            card = cursor.fetchone()

            if card is None:
                raise HTTPException(status_code=404, detail="Card instance not found")

            if card["burned_at"] is not None:
                raise HTTPException(status_code=409, detail="That card was destroyed in an SBC")

            if card["owner_id"] != listing.seller_id:
                raise HTTPException(
                    status_code=403,
                    detail="Seller does not own this card",
                )

            try:
                cursor.execute(
                    """
                    INSERT INTO listings (
                        card_instance_id,
                        seller_id,
                        price,
                        status
                    )
                    VALUES (%s, %s, %s, 'ACTIVE')
                    RETURNING *
                    """,
                    (
                        listing.card_instance_id,
                        listing.seller_id,
                        listing.price,
                    ),
                )

                created = cursor.fetchone()

            except UniqueViolation:
                raise HTTPException(
                    status_code=409,
                    detail="Card already has an active listing",
                )

            record_card_event(
                cursor,
                listing.card_instance_id,
                "LISTED",
                from_user_id=listing.seller_id,
                price=listing.price,
                related_listing_id=created["id"],
            )

        connection.commit()
    return created

@api.post("/listings/{listing_id}/cancel", response_model=ListingResponse)
def cancel_listing(listing_id: int, seller_id: int, me: int = Depends(current_user_id)):
    require_self(me, seller_id)
    with get_connection() as connection:
        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    id,
                    card_instance_id,
                    seller_id,
                    price,
                    status,
                    created_at,
                    resolved_at
                FROM listings
                WHERE id = %s
                FOR NO KEY UPDATE;
                """,
                (listing_id,),
            )

            listing = cursor.fetchone()

            if listing is None:
                raise HTTPException(
                    status_code=404,
                    detail="Listing not found",
                )

            if listing["seller_id"] != seller_id:
                raise HTTPException(
                    status_code=403,
                    detail="Only the seller can cancel this listing",
                )

            if listing["status"] != "ACTIVE":
                raise HTTPException(
                    status_code=409,
                    detail="Listing is not active",
                )

            cursor.execute(
                """
                UPDATE listings
                SET
                    status = 'CANCELLED',
                    resolved_at = NOW()
                WHERE id = %s AND status = 'ACTIVE'
                RETURNING
                    id,
                    card_instance_id,
                    seller_id,
                    price,
                    status,
                    created_at,
                    resolved_at;
                """,
                (listing_id,),
            )

            cancelled = cursor.fetchone()

            record_card_event(
                cursor,
                listing["card_instance_id"],
                "DELISTED",
                from_user_id=seller_id,
                price=listing["price"],
                related_listing_id=listing_id,
            )

        connection.commit()

    return cancelled


@api.post("/listings/{listing_id}/buy", response_model=ListingResponse)
def buy_listing(listing_id: int, buyer_id: int, me: int = Depends(current_user_id)):
    require_self(me, buyer_id)
    with get_connection() as connection:
        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    id,
                    card_instance_id,
                    seller_id,
                    price,
                    status,
                    created_at,
                    resolved_at
                FROM listings
                WHERE id = %s
                FOR NO KEY UPDATE;
                """,
                (listing_id,),
            )

            listing = cursor.fetchone()

            if listing is None:
                raise HTTPException(
                    status_code=404,
                    detail="Listing not found",
                )

            if listing["status"] != "ACTIVE":
                raise HTTPException(
                    status_code=409,
                    detail="Listing is not active",
                )

            if buyer_id == listing["seller_id"]:
                raise HTTPException(
                    status_code=400,
                    detail="Seller cannot buy their own listing",
                )

            first_user_id = min(buyer_id, listing["seller_id"])
            second_user_id = max(buyer_id, listing["seller_id"])

            cursor.execute(
                """
                SELECT id, balance
                FROM users
                WHERE id IN (%s, %s)
                ORDER BY id
                FOR NO KEY UPDATE;
                """,
                (first_user_id, second_user_id),
            )

            users = cursor.fetchall()

            buyer = next(
                (user for user in users if user["id"] == buyer_id),
                None,
            )

            if buyer is None:
                raise HTTPException(
                    status_code=404,
                    detail="Buyer not found",
                )

            if buyer["balance"] < listing["price"]:
                raise HTTPException(
                    status_code=400,
                    detail="Insufficient balance",
            )

            # Guarded transfer: only moves the card if the seller still owns it.
            # 0 rows means the listing is stale, so abort before any money moves.
            cursor.execute(
                """
                UPDATE card_instances
                SET owner_id = %s
                WHERE id = %s AND owner_id = %s
                RETURNING id, owner_id;
                """,
                (
                    buyer_id,
                    listing["card_instance_id"],
                    listing["seller_id"],
                ),
            )

            if cursor.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail="Seller no longer owns this card",
                )

            record_card_event(
                cursor,
                listing["card_instance_id"],
                "SOLD",
                from_user_id=listing["seller_id"],
                to_user_id=buyer_id,
                price=listing["price"],
                related_listing_id=listing_id,
            )

            price = listing["price"]
            fee = (price * MARKET_FEE_RATE).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            # Matched TRANSFER pair (equal and opposite), then the fee is burned
            # from the seller with no offsetting credit to anyone.
            apply_balance_change(
                cursor, buyer_id, -price, "TRANSFER_PURCHASE_DEBIT", listing_id
            )
            apply_balance_change(
                cursor, listing["seller_id"], price, "TRANSFER_SALE_CREDIT", listing_id
            )
            if fee > 0:
                apply_balance_change(
                    cursor, listing["seller_id"], -fee, "BURN_MARKET_FEE", listing_id
                )

            cursor.execute(
                """
                UPDATE listings
                SET
                    status = 'SOLD',
                    resolved_at = NOW()
                WHERE id = %s AND status = 'ACTIVE'
                RETURNING
                    id,
                    card_instance_id,
                    seller_id,
                    price,
                    status,
                    created_at,
                    resolved_at;
                """,
                (listing_id,),
            )

            completed_listing = cursor.fetchone()

            connection.commit()

    return completed_listing

@api.get("/packs")
def get_pack_types():
    return [
        {
            "pack_type": pack_type,
            "price": config["price"],
            "cards": config["cards"],
            "odds": config["odds"],
        }
        for pack_type, config in PACK_TYPES.items()
    ]


@api.post("/packs/open", response_model=PackOpenResponse)
def open_pack(request: PackOpenRequest, me: int = Depends(current_user_id)):
    require_self(me, request.user_id)
    config = PACK_TYPES[request.pack_type]

    with get_connection() as connection:
        with connection.cursor() as cursor:

            # Lock order: user row first, then card_definitions in id order.
            # Every pack opening follows the same order, so two can't deadlock.
            cursor.execute(
                """
                SELECT id, balance
                FROM users
                WHERE id = %s
                FOR NO KEY UPDATE;
                """,
                (request.user_id,),
            )

            user = cursor.fetchone()

            if user is None:
                raise HTTPException(
                    status_code=404,
                    detail="User not found",
                )

            if user["balance"] < config["price"]:
                raise HTTPException(
                    status_code=400,
                    detail="Insufficient balance",
                )

            # Plain read (no lock): just a snapshot of the active pool to roll
            # against. The guarded UPDATE inside mint_card_instance is what
            # actually enforces supply.
            cursor.execute(
                """
                SELECT id, rarity, max_supply - minted_count AS remaining
                FROM card_definitions
                WHERE is_active AND edition = 'BASE' AND minted_count < max_supply;
                """
            )

            available = {
                row["id"]: {"rarity": row["rarity"], "remaining": row["remaining"]}
                for row in cursor.fetchall()
            }

            picks = pick_definitions(request.pack_type, available)

            if picks is None:
                raise HTTPException(
                    status_code=409,
                    detail="Not enough cards left to fill this pack",
                )

            cursor.execute(
                """
                INSERT INTO pack_openings (user_id, pack_type, price)
                VALUES (%s, %s, %s)
                RETURNING id;
                """,
                (request.user_id, request.pack_type, config["price"]),
            )

            pack_opening_id = cursor.fetchone()["id"]

            # Burn: the price leaves circulation, nobody is credited.
            updated_user = apply_balance_change(
                cursor,
                request.user_id,
                -config["price"],
                "BURN_PACK_PURCHASE",
                related_pack_opening_id=pack_opening_id,
            )

            minted_ids = []

            for card_definition_id in sorted(picks):
                minted = mint_card_instance(
                    cursor, card_definition_id, request.user_id, pack_opening_id
                )

                # Another pack took the last copy between our snapshot and now.
                # Raising rolls back everything, including the charge.
                if minted is None:
                    raise HTTPException(
                        status_code=409,
                        detail="A card sold out while opening this pack, please try again",
                    )

                minted_ids.append(minted["id"])

            cursor.execute(
                """
                SELECT
                    card_instances.id,
                    card_instances.card_definition_id,
                    card_instances.serial_number,
                    card_definitions.rarity,
                    players.name AS player_name,
                    players.role AS player_role,
                    players.country AS player_country,
                    card_definitions.max_supply,
                    card_definitions.edition_label,
                    card_instances.burned_at,
                    players.id AS player_id,
                    users.username AS owner_username
                FROM card_instances
                JOIN card_definitions
                    ON card_instances.card_definition_id = card_definitions.id
                JOIN players
                    ON card_definitions.player_id = players.id
                JOIN users
                    ON card_instances.owner_id = users.id
                WHERE card_instances.id = ANY(%s)
                ORDER BY card_instances.id;
                """,
                (minted_ids,),
            )

            cards = cursor.fetchall()

    return {
        "pack_opening_id": pack_opening_id,
        "pack_type": request.pack_type,
        "price": config["price"],
        "balance": updated_user["balance"],
        "cards": cards,
    }


@api.get("/card-definitions", response_model=list[CardDefinitionResponse])
def get_card_definitions(active: bool | None = None):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, player_id, rarity, max_supply, minted_count, is_active
                FROM card_definitions
                WHERE %(active)s::boolean IS NULL OR is_active = %(active)s
                ORDER BY id;
                """,
                {"active": active},
            )

            definitions = cursor.fetchall()

    return definitions


@api.get("/users/{user_id}/cards", response_model=list[CollectionCardResponse])
def get_user_collection(user_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM users WHERE id = %s;
                """,
                (user_id,),
            )

            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="User not found",
                )

            # LEFT JOIN on the partial unique index's condition: at most one
            # ACTIVE listing per card, so this can't duplicate rows.
            cursor.execute(
                """
                SELECT
                    card_instances.id,
                    card_instances.card_definition_id,
                    card_instances.serial_number,
                    card_definitions.rarity,
                    players.name AS player_name,
                    players.role AS player_role,
                    players.country AS player_country,
                    card_definitions.max_supply,
                    card_definitions.edition_label,
                    card_instances.burned_at,
                    players.id AS player_id,
                    users.username AS owner_username,
                    listings.id AS active_listing_id,
                    listings.price AS listed_price,
                    EXISTS (SELECT 1 FROM battles b
                            WHERE b.status IN ('PENDING', 'ACTIVE')
                              AND card_instances.id = ANY (b.challenger_card_ids || COALESCE(b.opponent_card_ids, '{}'))) AS in_battle,
                    (SELECT COALESCE(max(array_position(ARRAY['Common', 'Rare', 'Epic', 'Legendary'], d2.rarity)) FILTER (WHERE d2.is_active),
                                 max(array_position(ARRAY['Common', 'Rare', 'Epic', 'Legendary'], d2.rarity)))
                       FROM card_definitions d2 WHERE d2.player_id = players.id) AS tier_rank
                FROM card_instances
                JOIN card_definitions
                    ON card_instances.card_definition_id = card_definitions.id
                JOIN players
                    ON card_definitions.player_id = players.id
                JOIN users
                    ON card_instances.owner_id = users.id
                LEFT JOIN listings
                    ON listings.card_instance_id = card_instances.id
                    AND listings.status = 'ACTIVE'
                WHERE card_instances.owner_id = %s AND card_instances.burned_at IS NULL
                ORDER BY card_instances.id;
                """,
                (user_id,),
            )

            cards = cursor.fetchall()

    # Battle credits come from the player's tier (highest rarity they exist in).
    for card in cards:
        card["player_tier"] = TIER_ORDER[card.pop("tier_rank") - 1]
        card["credits"] = credits_for_tier(card["player_tier"])

    return cards


@api.get("/marketplace", response_model=list[MarketplaceListingResponse])
def get_marketplace(
    card_definition_id: int | None = None,
    player_id: int | None = None,
    rarity: str | None = None,
):
    # Each filter is skipped when its parameter is NULL, so one query covers
    # every combination of filters.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    listings.id AS listing_id,
                    listings.card_instance_id,
                    card_instances.card_definition_id,
                    card_instances.serial_number,
                    card_definitions.rarity,
                    players.name AS player_name,
                    players.role AS player_role,
                    players.country AS player_country,
                    card_definitions.max_supply,
                    card_definitions.edition_label,
                    card_instances.burned_at,
                    players.id AS player_id,
                    listings.seller_id,
                    users.username AS seller_username,
                    listings.price,
                    listings.created_at AS listed_at
                FROM listings
                JOIN card_instances
                    ON listings.card_instance_id = card_instances.id
                JOIN card_definitions
                    ON card_instances.card_definition_id = card_definitions.id
                JOIN players
                    ON card_definitions.player_id = players.id
                JOIN users
                    ON listings.seller_id = users.id
                WHERE listings.status = 'ACTIVE'
                    AND (%(card_definition_id)s::bigint IS NULL
                         OR card_instances.card_definition_id = %(card_definition_id)s)
                    AND (%(player_id)s::bigint IS NULL
                         OR card_definitions.player_id = %(player_id)s)
                    AND (%(rarity)s::text IS NULL
                         OR card_definitions.rarity = %(rarity)s)
                ORDER BY listings.price, listings.created_at;
                """,
                {
                    "card_definition_id": card_definition_id,
                    "player_id": player_id,
                    "rarity": rarity,
                },
            )

            listings = cursor.fetchall()

    return listings


@api.get("/card-instances/{card_id}/history", response_model=list[CardEventResponse])
def get_card_history(card_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM card_instances WHERE id = %s;
                """,
                (card_id,),
            )

            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Card instance not found",
                )

            cursor.execute(
                """
                SELECT
                    card_ownership_events.event_type,
                    from_user.username AS from_username,
                    to_user.username AS to_username,
                    card_ownership_events.price,
                    card_ownership_events.related_listing_id,
                    card_ownership_events.related_pack_opening_id,
                    card_ownership_events.related_trade_id,
                    card_ownership_events.created_at
                FROM card_ownership_events
                LEFT JOIN users AS from_user
                    ON card_ownership_events.from_user_id = from_user.id
                LEFT JOIN users AS to_user
                    ON card_ownership_events.to_user_id = to_user.id
                WHERE card_ownership_events.card_instance_id = %s
                ORDER BY card_ownership_events.created_at, card_ownership_events.id;
                """,
                (card_id,),
            )

            events = cursor.fetchall()

    return events


@api.get(
    "/card-definitions/{card_definition_id}/price-history",
    response_model=list[SaleResponse],
)
def get_price_history(card_definition_id: int, limit: int = 50):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM card_definitions WHERE id = %s;
                """,
                (card_definition_id,),
            )

            if cursor.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="Card definition not found",
                )

            cursor.execute(
                """
                SELECT
                    card_ownership_events.card_instance_id,
                    card_instances.serial_number,
                    card_ownership_events.price,
                    card_ownership_events.created_at AS sold_at
                FROM card_ownership_events
                JOIN card_instances
                    ON card_ownership_events.card_instance_id = card_instances.id
                WHERE card_ownership_events.event_type = 'SOLD'
                    AND card_instances.card_definition_id = %s
                ORDER BY card_ownership_events.created_at DESC
                LIMIT %s;
                """,
                (card_definition_id, limit),
            )

            sales = cursor.fetchall()

    return sales


if DEV_FAUCET_ENABLED:
    register_dev_tools()

from backend.web import build_app  # noqa: E402  (needs `api` defined above)

app = build_app(api)
