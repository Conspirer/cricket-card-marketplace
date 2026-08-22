from fastapi import FastAPI, HTTPException
from backend.database import get_connection
from backend.schemas import UserResponse, UserCreate, PlayerCreate, PlayerResponse, CardDefinitionCreate, CardDefinitionResponse, CardInstanceCreate, CardInstanceResponse, CardInstanceDetailResponse, ListingCreate, ListingResponse
from psycopg.errors import UniqueViolation

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/users", response_model=list[UserResponse])
def get_users():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, username, email
                FROM users
                ORDER BY id
                """
            )
            users = cursor.fetchall()
    return users

@app.post("/users", response_model=UserResponse)
def create_user(user: UserCreate):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users(username, email)
                VALUES (%s, %s)
                RETURNING id, username, email;
                """,
                (user.username, user.email)
            )

            created_user = cursor.fetchone()
    return created_user

@app.post("/players", response_model=PlayerResponse)
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

@app.get("/players", response_model=list[PlayerResponse])
def get_players():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, country, role
                FROM players
                ORDER BY id;
                """
            )

            players = cursor.fetchall()
    return players;

@app.get("/players/{player_id}", response_model=PlayerResponse)
def get_player(player_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, country, role
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

@app.post("/card-definitions", response_model=CardDefinitionResponse)
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
                        (player_id, rarity, max_supply)
                    VALUES (%s, %s, %s)
                    RETURNING id, player_id, rarity, max_supply;
                    """,
                    (
                        card.player_id,
                        card.rarity,
                        card.max_supply,
                    ),
                )

                created_card = cursor.fetchone()
    return created_card


@app.post("/card-instances", response_model=CardInstanceResponse)
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

            try:
                cursor.execute(
                    """
                    INSERT INTO card_instances
                        (card_definition_id, serial_number, owner_id)
                    VALUES (%s, %s, %s)
                    RETURNING id, card_definition_id, serial_number, owner_id;
                    """,
                    (
                        card.card_definition_id,
                        card.serial_number,
                        card.owner_id,
                    ),
                )

                created_card = cursor.fetchone()

            except UniqueViolation:
                raise HTTPException(
                    status_code=409,
                    detail="This serial number already exists for this card definition",
                )

    return created_card

@app.get(
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
                    card_instances.serial_number,
                    card_definitions.rarity,
                    players.name AS player_name,
                    users.username AS owner_username
                FROM card_instances
                JOIN card_definitions
                    ON card_instances.card_definition_id = card_definitions.id
                JOIN players
                    ON card_definitions.player_id = players.id
                JOIN users
                    ON card_instances.owner_id = users.id
                ORDER BY card_instances.id;
                """
            )

            cards = cursor.fetchall()

    return cards

@app.get(
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
                    card_instances.serial_number,
                    card_definitions.rarity,
                    players.name AS player_name,
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

@app.post("/listings", response_model=ListingResponse)
def create_listing(listing:ListingCreate):
    with get_connection() as connection:
        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT owner_id
                FROM card_instances
                WHERE id = %s
                """,
                (listing.card_instance_id,),
            )

            card = cursor.fetchone()

            if card is None:
                raise HTTPException(status_code=404, detail="Card instance not found")

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

        connection.commit()
    return created

@app.post("/listings/{listing_id}/cancel", response_model=ListingResponse)
def cancel_listing(listing_id: int):
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
                WHERE id = %s;
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

            cursor.execute(
                """
                UPDATE listings
                SET
                    status = 'CANCELLED',
                    resolved_at = NOW()
                WHERE id = %s
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

        connection.commit()

    return cancelled


@app.post("/listings/{listing_id}/buy", response_model=ListingResponse)
def buy_listing(listing_id: int, buyer_id: int):
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
                FOR UPDATE;
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
                FOR UPDATE;
                """,
                (first_user_id, second_user_id),
            )

            users = cursor.fetchall()

            buyer = next(user for user in users if user["id"] == buyer_id)
            seller = next(
                user for user in users
                if user["id"] == listing["seller_id"]
            )

            if buyer["balance"] < listing["price"]:
                raise HTTPException(
                    status_code=400,
                    detail="Insufficient balance",
            )

            cursor.execute(
                """
                UPDATE users
                SET balance = balance - %s
                WHERE id = %s
                RETURNING id, balance;
                """,
                (listing["price"], buyer_id),
            )

            updated_buyer = cursor.fetchone()


            cursor.execute(
                """
                UPDATE users
                SET balance = balance + %s
                WHERE id = %s
                RETURNING id, balance;
                """,
                (listing["price"], listing["seller_id"]),
            )

            updated_seller = cursor.fetchone()

            cursor.execute(
                """
                UPDATE card_instances
                SET owner_id = %s
                WHERE id = %s
                RETURNING id, owner_id;
                """,
                (
                    buyer_id,
                    listing["card_instance_id"],
                ),
            )

            updated_card = cursor.fetchone()



            cursor.execute(
                """
                UPDATE listings
                SET
                    status = 'SOLD',
                    resolved_at = NOW()
                WHERE id = %s
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