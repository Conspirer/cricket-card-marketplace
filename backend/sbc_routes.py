"""Squad building challenges: submit qualifying cards, they're burned, you get a reward.

Lock order for a submission (see the lock-order table):
    user -> submitted cards (ascending id) -> reward definitions (ascending id)
Buy locks listing -> users -> card, so taking users before cards here keeps
the two consistent; pack opening is user -> definitions, also consistent.
Listing and battle-deck membership are re-read after the card locks are held,
so a card listed or entered into a battle a moment earlier is caught.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend import sbc
from backend.auth import COOKIE, current_user_id
from backend.database import get_connection

router = APIRouter()


class CardsBody(BaseModel):
    card_ids: list[int]


def optional_user_id(request: Request):
    try:
        return current_user_id(request) if request.cookies.get(COOKIE) else None
    except HTTPException:
        return None


def challenge_open(challenge, now):
    return (challenge["active"]
            and (challenge["starts_at"] is None or challenge["starts_at"] <= now)
            and (challenge["ends_at"] is None or challenge["ends_at"] > now))


def eval_cards(cursor, card_ids):
    """Cards as the engine sees them, keyed by id (verified theme stats included)."""
    cursor.execute(
        """
        SELECT ci.id AS card_id, ci.owner_id, ci.burned_at, d.rarity, p.id AS player_id,
               p.name AS player_name, p.country, p.role,
               COALESCE((SELECT jsonb_object_agg(t.theme, jsonb_build_object(
                             'verified', t.verified, 'matches', t.matches, 'stats', t.stats))
                         FROM player_theme_stats t WHERE t.player_id = p.id), '{}'::jsonb) AS theme_stats
        FROM card_instances ci
        JOIN card_definitions d ON d.id = ci.card_definition_id
        JOIN players p ON p.id = d.player_id
        WHERE ci.id = ANY(%s);
        """,
        (list(card_ids),),
    )
    return {r["card_id"]: r for r in cursor.fetchall()}


def card_blockers(cursor, card_ids):
    """{card_id: reason} for cards that can't leave their owner's hands right now."""
    cursor.execute(
        """
        SELECT ci.id,
               ci.burned_at IS NOT NULL AS burned,
               EXISTS (SELECT 1 FROM listings l WHERE l.card_instance_id = ci.id AND l.status = 'ACTIVE') AS listed,
               EXISTS (SELECT 1 FROM battles b
                       WHERE b.status IN ('PENDING', 'ACTIVE')
                         AND ci.id = ANY (b.challenger_card_ids || COALESCE(b.opponent_card_ids, '{}'))) AS in_battle
        FROM card_instances ci WHERE ci.id = ANY(%s);
        """,
        (list(card_ids),),
    )
    out = {}
    for r in cursor.fetchall():
        if r["burned"]:
            out[r["id"]] = "already destroyed"
        elif r["listed"]:
            out[r["id"]] = "listed on the market"
        elif r["in_battle"]:
            out[r["id"]] = "in a pending or active battle deck"
    return out


def reward_view(cursor, reward):
    keys = reward.get("cards", [])
    cards = []
    if keys:
        cursor.execute(
            """
            SELECT d.edition_key, d.edition_label, d.rarity, d.max_supply, d.minted_count,
                   p.name AS player_name, p.role AS player_role, p.country AS player_country
            FROM card_definitions d JOIN players p ON p.id = d.player_id
            WHERE d.edition_key = ANY(%s);
            """,
            (keys,),
        )
        by_key = {r["edition_key"]: r for r in cursor.fetchall()}
        cards = [by_key[k] for k in keys if k in by_key]
    return {"cards": cards, "runs": reward.get("runs", 0)}


def challenge_view(cursor, c, user_id, now):
    completed = 0
    if user_id:
        cursor.execute("SELECT count(*) AS n FROM sbc_completions WHERE user_id = %s AND challenge_id = %s;", (user_id, c["id"]))
        completed = cursor.fetchone()["n"]
    return {
        "slug": c["slug"],
        "title": c["title"],
        "description": c["description"],
        "requirements": [sbc.describe(r) for r in c["requirements"]] + ["Each card a different player"],
        "card_count": sbc.required_count(c["requirements"]),
        "reward": reward_view(cursor, c["reward"]),
        "starts_at": c["starts_at"],
        "ends_at": c["ends_at"],
        "open": challenge_open(c, now),
        "max_completions": c["max_completions_per_user"],
        "completed": completed,
    }


def load_challenge(cursor, slug):
    cursor.execute("SELECT * FROM sbc_challenges WHERE slug = %s;", (slug,))
    c = cursor.fetchone()
    if c is None:
        raise HTTPException(404, "No such challenge")
    return c


@router.get("/sbcs")
def list_challenges(user_id: int | None = Depends(optional_user_id)):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT now() AS now;")
            now = cursor.fetchone()["now"]
            cursor.execute("SELECT * FROM sbc_challenges WHERE active ORDER BY id;")
            return [challenge_view(cursor, c, user_id, now) for c in cursor.fetchall()]


@router.get("/sbcs/{slug}")
def get_challenge(slug: str, user_id: int | None = Depends(optional_user_id)):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT now() AS now;")
            now = cursor.fetchone()["now"]
            return challenge_view(cursor, load_challenge(cursor, slug), user_id, now)


def _checklist(cursor, challenge, me, card_ids):
    cards = eval_cards(cursor, card_ids)
    missing = [c for c in card_ids if c not in cards]
    if missing:
        raise HTTPException(404, f"Card #{missing[0]} not found")
    not_mine = [c for c in card_ids if cards[c]["owner_id"] != me]
    if not_mine:
        raise HTTPException(403, f"You don't own card #{not_mine[0]}")
    checklist = sbc.evaluate(challenge["requirements"], [cards[c] for c in card_ids])
    return cards, checklist


@router.post("/sbcs/{slug}/check")
def check_submission(slug: str, body: CardsBody, me: int = Depends(current_user_id)):
    """Live checklist for the builder. Read-only; submit re-checks everything."""
    if len(set(body.card_ids)) != len(body.card_ids):
        raise HTTPException(400, "Each card can be used once")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            challenge = load_challenge(cursor, slug)
            _, checklist = _checklist(cursor, challenge, me, body.card_ids)
            blockers = card_blockers(cursor, body.card_ids)
            return {
                "checklist": checklist,
                "blocked": {str(k): v for k, v in blockers.items()},
                "ready": sbc.all_met(checklist) and not blockers,
            }


@router.post("/sbcs/{slug}/submit")
def submit(slug: str, body: CardsBody, me: int = Depends(current_user_id)):
    from backend.main import apply_balance_change, mint_card_instance, record_card_event  # avoid an import cycle

    if len(set(body.card_ids)) != len(body.card_ids):
        raise HTTPException(400, "Each card can be used once")
    card_ids = sorted(body.card_ids)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # 1. The user: serialises this player's submissions (double submits).
            cursor.execute("SELECT id FROM users WHERE id = %s FOR NO KEY UPDATE;", (me,))
            challenge = load_challenge(cursor, slug)
            cursor.execute("SELECT now() AS now;")
            if not challenge_open(challenge, cursor.fetchone()["now"]):
                raise HTTPException(409, "This challenge isn't open")

            # 2. The submitted cards, ascending id.
            cursor.execute(
                "SELECT id, owner_id, burned_at FROM card_instances WHERE id = ANY(%s) ORDER BY id FOR NO KEY UPDATE;",
                (card_ids,),
            )
            locked = {r["id"]: r for r in cursor.fetchall()}
            if len(locked) != len(card_ids):
                raise HTTPException(404, "Card not found")
            for cid in card_ids:
                if locked[cid]["owner_id"] != me:
                    raise HTTPException(403, f"You don't own card #{cid}")
            blockers = card_blockers(cursor, card_ids)
            if blockers:
                cid, reason = next(iter(blockers.items()))
                raise HTTPException(409, f"Card #{cid} is {reason}")

            cursor.execute(
                "SELECT count(*) AS n FROM sbc_completions WHERE user_id = %s AND challenge_id = %s;",
                (me, challenge["id"]),
            )
            if cursor.fetchone()["n"] >= challenge["max_completions_per_user"]:
                raise HTTPException(409, "You've already completed this challenge")

            _, checklist = _checklist(cursor, challenge, me, card_ids)
            if not sbc.all_met(checklist):
                unmet = next(item["rule"] for item in checklist if not item["ok"])
                raise HTTPException(400, f"Requirement not met: {unmet}")

            # Burn: guarded, so a card that changed hands since the lock can't be burned.
            cursor.execute(
                "UPDATE card_instances SET burned_at = now() WHERE id = ANY(%s) AND owner_id = %s AND burned_at IS NULL;",
                (card_ids, me),
            )
            if cursor.rowcount != len(card_ids):
                raise HTTPException(409, "Your cards changed; try again")
            for cid in card_ids:
                record_card_event(cursor, cid, "BURNED", from_user_id=me)

            cursor.execute(
                """
                INSERT INTO sbc_completions (user_id, challenge_id, submitted_card_ids, reward_refs)
                VALUES (%s, %s, %s, '{}') RETURNING id;
                """,
                (me, challenge["id"], card_ids),
            )
            completion_id = cursor.fetchone()["id"]

            # 3. Reward definitions, ascending id, through the shared guarded mint counter.
            reward = challenge["reward"]
            minted = []
            keys = reward.get("cards", [])
            if keys:
                cursor.execute("SELECT id, edition_key FROM card_definitions WHERE edition_key = ANY(%s) ORDER BY id;", (keys,))
                definitions = cursor.fetchall()
                if len(definitions) != len(set(keys)):
                    raise HTTPException(409, "This challenge's reward isn't available")
                for d in definitions:
                    card = mint_card_instance(cursor, d["id"], me)
                    if card is None:
                        raise HTTPException(409, "The reward card has sold out")
                    minted.append(card["id"])
            runs = reward.get("runs", 0)
            if runs:
                apply_balance_change(cursor, me, runs, "MINT_SBC_REWARD", related_sbc_completion_id=completion_id)

            from psycopg.types.json import Jsonb
            cursor.execute("UPDATE sbc_completions SET reward_refs = %s WHERE id = %s;",
                           (Jsonb({"cards": minted, "runs": runs}), completion_id))
            return {"completion_id": completion_id, "burned": card_ids, "reward_cards": minted, "reward_runs": runs}
