"""Pull feed, public profiles with showcases, and page-view logging.

Lock order for the writes here (see the brief's lock-order table):
  showcase update  users (the session user only); card ownership is read
                   unlocked, because the showcase is filtered by ownership
                   whenever it's read
  page-view ping   page_views (own row, upsert); nothing else
Neither takes a lock another path holds while waiting on this one.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import current_user_id
from backend.database import get_connection

router = APIRouter()

FEED_SIZE = 20
SHOWCASE_SIZE = 5
NOTABLE_RARITIES = ("Epic", "Legendary")

_PUBLIC_CARD = """
    ci.id AS card_instance_id, ci.serial_number, d.id AS card_definition_id, d.rarity, d.max_supply,
    p.name AS player_name, p.role AS player_role, p.country AS player_country
"""


@router.get("/feed")
def feed():
    """Recent notable pulls: Epic or Legendary, or the first or last serial of a run."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT e.id AS event_id, e.created_at, u.username, {_PUBLIC_CARD}
                FROM card_ownership_events e
                JOIN card_instances ci ON ci.id = e.card_instance_id
                JOIN card_definitions d ON d.id = ci.card_definition_id
                JOIN players p ON p.id = d.player_id
                JOIN users u ON u.id = e.to_user_id
                WHERE e.event_type = 'PULLED'
                  AND (d.rarity = ANY(%s) OR ci.serial_number = 1 OR ci.serial_number = d.max_supply)
                ORDER BY e.created_at DESC, e.id DESC
                LIMIT %s;
                """,
                (list(NOTABLE_RARITIES), FEED_SIZE),
            )
            return cursor.fetchall()


def showcase_cards(cursor, user_id):
    """Pinned cards the user still owns, in their chosen order."""
    cursor.execute(
        f"""
        SELECT s.position, {_PUBLIC_CARD}
        FROM user_showcase s
        JOIN card_instances ci ON ci.id = s.card_instance_id
        JOIN card_definitions d ON d.id = ci.card_definition_id
        JOIN players p ON p.id = d.player_id
        WHERE s.user_id = %s AND ci.owner_id = s.user_id AND ci.burned_at IS NULL
        ORDER BY s.position;
        """,
        (user_id,),
    )
    return cursor.fetchall()


@router.get("/profiles/{username}")
def profile(username: str):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, username, created_at FROM users WHERE lower(username) = lower(%s);", (username,))
            user = cursor.fetchone()
            if user is None:
                raise HTTPException(404, "No such player")
            uid = user["id"]

            cursor.execute(
                """
                SELECT d.rarity, count(*) AS n
                FROM card_instances ci JOIN card_definitions d ON d.id = ci.card_definition_id
                WHERE ci.owner_id = %s AND ci.burned_at IS NULL
                GROUP BY d.rarity;
                """,
                (uid,),
            )
            by_rarity = {r["rarity"]: r["n"] for r in cursor.fetchall()}

            # Forfeits count as a loss for the player who forfeited.
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (WHERE winner_id = %(u)s) AS wins,
                    count(*) FILTER (WHERE winner_id IS NOT NULL AND winner_id <> %(u)s) AS losses,
                    count(*) FILTER (WHERE winner_id IS NULL AND status = 'FINISHED') AS draws
                FROM battles
                WHERE status IN ('FINISHED', 'FORFEIT') AND %(u)s IN (challenger_id, opponent_id);
                """,
                {"u": uid},
            )
            record = cursor.fetchone()

            return {
                "username": user["username"],
                "joined": user["created_at"],
                "collection": {
                    "total": sum(by_rarity.values()),
                    **{r: by_rarity.get(r, 0) for r in ("Common", "Rare", "Epic", "Legendary")},
                },
                "battles": record,
                "showcase": showcase_cards(cursor, uid),
            }


class ShowcaseBody(BaseModel):
    card_ids: list[int]


@router.put("/me/showcase")
def set_showcase(body: ShowcaseBody, me: int = Depends(current_user_id)):
    """Replace your showcase with these cards (in order); an empty list clears it."""
    if len(body.card_ids) > SHOWCASE_SIZE:
        raise HTTPException(400, f"A showcase holds at most {SHOWCASE_SIZE} cards")
    if len(set(body.card_ids)) != len(body.card_ids):
        raise HTTPException(400, "Each card can be pinned once")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Serialise this user's showcase edits; nothing else is locked.
            cursor.execute("SELECT id FROM users WHERE id = %s FOR NO KEY UPDATE;", (me,))
            if body.card_ids:
                cursor.execute(
                    "SELECT id FROM card_instances WHERE id = ANY(%s) AND owner_id = %s AND burned_at IS NULL;",
                    (body.card_ids, me),
                )
                owned = {r["id"] for r in cursor.fetchall()}
                missing = [c for c in body.card_ids if c not in owned]
                if missing:
                    raise HTTPException(403, f"You don't own card #{missing[0]}")
            cursor.execute("DELETE FROM user_showcase WHERE user_id = %s;", (me,))
            cursor.executemany(
                "INSERT INTO user_showcase (user_id, card_instance_id, position) VALUES (%s, %s, %s);",
                [(me, card_id, i + 1) for i, card_id in enumerate(body.card_ids)],
            )
            return showcase_cards(cursor, me)


@router.post("/me/visit")
def visit(me: int = Depends(current_user_id)):
    """Called by the app on each page view: one row per user per UTC day."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO page_views (user_id, day) VALUES (%s, (now() AT TIME ZONE 'UTC')::date)
                ON CONFLICT (user_id, day) DO UPDATE
                    SET views = page_views.views + 1, last_seen = now();
                """,
                (me,),
            )
    return {"ok": True}
