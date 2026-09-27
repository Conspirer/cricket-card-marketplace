"""Trading: card-for-card swaps between two players.

1 to 3 cards a side and never Runs, so the market's 5% fee can't be dodged.
Both players must be established (account age and finished battles) and each
can accept only so many trades a day, which stops free pulls on alt accounts
being funnelled into a main account.

Offering a card doesn't lock it: the owner can still list, burn or battle
with it. Accepting re-checks everything under locks.

Lock order (see the lock-order table):
  propose           nothing (reads only, then inserts the trade)
  decline / cancel  trade row
  accept            trade row -> both users (ascending id) -> all cards (ascending id)
Every other path that locks users and cards takes users first (buy, SBC), or
locks only cards (create listing, battles) or only one user (packs,
showcase), and none of them locks a trade row, so accept can't close a cycle.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from backend.auth import current_user_id
from backend.database import get_connection

router = APIRouter()

MIN_CARDS_PER_SIDE = 1
MAX_CARDS_PER_SIDE = 3
TRADE_EXPIRY_HOURS = 48
MIN_ACCOUNT_AGE_DAYS = 3
MIN_BATTLES_FINISHED = 3
MAX_ACCEPTED_TRADES_PER_DAY = 5     # per user, over the last 24 hours
MAX_PENDING_OUTGOING = 10           # open offers a player can have out at once
HISTORY_SIZE = 50


class TradeCreate(BaseModel):
    # Only cards: an unknown field (say, "runs") is a 422, not silently dropped.
    model_config = ConfigDict(extra="forbid")
    recipient: str
    offered_card_ids: list[int]
    requested_card_ids: list[int]


_CARD = """
    ci.id AS card_instance_id, ci.serial_number, ci.owner_id, ci.burned_at,
    d.id AS card_definition_id, d.rarity, d.max_supply, d.edition_label,
    p.name AS player_name, p.role AS player_role, p.country AS player_country,
    EXISTS (SELECT 1 FROM listings l WHERE l.card_instance_id = ci.id AND l.status = 'ACTIVE') AS listed,
    EXISTS (SELECT 1 FROM battles b WHERE b.status IN ('PENDING', 'ACTIVE')
              AND ci.id = ANY (b.challenger_card_ids || COALESCE(b.opponent_card_ids, '{}'))) AS in_battle
"""
_CARD_FROM = """
    FROM card_instances ci
    JOIN card_definitions d ON d.id = ci.card_definition_id
    JOIN players p ON p.id = d.player_id
"""


def load_cards(cursor, card_ids):
    cursor.execute(f"SELECT {_CARD} {_CARD_FROM} WHERE ci.id = ANY(%s);", (list(card_ids),))
    return {r["card_instance_id"]: r for r in cursor.fetchall()}


def public_card(card):
    """What either player may see. in_battle is left out: a pending battle's
    deck is secret, so a card being in one isn't shown to anyone else."""
    return {k: card[k] for k in ("card_instance_id", "serial_number", "card_definition_id", "rarity", "max_supply",
                                 "edition_label", "player_name", "player_role", "player_country", "listed")}


def eligibility(cursor, user_id):
    cursor.execute(
        """
        SELECT u.username, u.created_at,
               now() - u.created_at >= %(age)s * interval '1 day' AS old_enough,
               (SELECT count(*) FROM battles b
                WHERE b.status IN ('FINISHED', 'FORFEIT') AND u.id IN (b.challenger_id, b.opponent_id)) AS battles,
               (SELECT count(*) FROM trades t
                WHERE t.status = 'ACCEPTED' AND u.id IN (t.proposer_id, t.recipient_id)
                  AND t.resolved_at > now() - interval '24 hours') AS trades_today
        FROM users u WHERE u.id = %(u)s;
        """,
        {"u": user_id, "age": MIN_ACCOUNT_AGE_DAYS},
    )
    row = cursor.fetchone()
    reasons = []
    if not row["old_enough"]:
        reasons.append(f"account must be at least {MIN_ACCOUNT_AGE_DAYS} days old")
    if row["battles"] < MIN_BATTLES_FINISHED:
        reasons.append(f"needs {MIN_BATTLES_FINISHED} finished battles ({row['battles']} so far)")
    return {
        "username": row["username"],
        "eligible": not reasons,
        "reasons": reasons,
        "joined": row["created_at"],
        "battles_finished": row["battles"],
        "trades_today": row["trades_today"],
        "at_daily_limit": row["trades_today"] >= MAX_ACCEPTED_TRADES_PER_DAY,
    }


def rules():
    return {
        "min_cards_per_side": MIN_CARDS_PER_SIDE,
        "max_cards_per_side": MAX_CARDS_PER_SIDE,
        "expiry_hours": TRADE_EXPIRY_HOURS,
        "min_account_age_days": MIN_ACCOUNT_AGE_DAYS,
        "min_battles_finished": MIN_BATTLES_FINISHED,
        "max_accepted_per_day": MAX_ACCEPTED_TRADES_PER_DAY,
    }


def card_problem(card, expected_owner, is_viewers):
    """Why this card can't move right now: (permanent?, message) or None.
    Permanent problems (it changed hands, it was destroyed) void the trade;
    a listing or a battle only blocks it until the owner frees the card."""
    label = f"{card['player_name']} #{card['serial_number']}"
    if card["burned_at"] is not None:
        return True, f"{label} was destroyed in a squad challenge"
    if card["owner_id"] != expected_owner:
        return True, f"{label} has changed hands"
    if card["listed"]:
        return False, f"{label} is listed on the market"
    if card["in_battle"]:
        return False, f"{label} is in a battle deck" if is_viewers else f"{label} isn't available right now"
    return None


def trade_problems(trade, cards, viewer_id):
    out = []
    for tc in trade["cards"]:
        problem = card_problem(cards[tc["card_instance_id"]], tc["from_user_id"], tc["from_user_id"] == viewer_id)
        if problem:
            out.append({"permanent": problem[0], "message": problem[1]})
    return out


def trade_views(cursor, trade_ids, viewer_id):
    if not trade_ids:
        return []
    cursor.execute(
        """
        SELECT t.id, t.proposer_id, t.recipient_id, t.invalid_reason, t.created_at, t.expires_at, t.resolved_at,
               CASE WHEN t.status = 'PENDING' AND t.expires_at <= now() THEN 'EXPIRED' ELSE t.status END AS status,
               pu.username AS proposer, ru.username AS recipient
        FROM trades t JOIN users pu ON pu.id = t.proposer_id JOIN users ru ON ru.id = t.recipient_id
        WHERE t.id = ANY(%s);
        """,
        (list(trade_ids),),
    )
    trades = {t["id"]: dict(t, cards=[]) for t in cursor.fetchall()}
    cursor.execute("SELECT * FROM trade_cards WHERE trade_id = ANY(%s) ORDER BY card_instance_id;", (list(trade_ids),))
    for tc in cursor.fetchall():
        trades[tc["trade_id"]]["cards"].append(tc)
    cards = load_cards(cursor, {tc["card_instance_id"] for t in trades.values() for tc in t["cards"]})

    views = []
    for tid in trade_ids:
        t = trades[tid]
        views.append({
            "id": t["id"],
            "status": t["status"],
            "invalid_reason": t["invalid_reason"],
            "proposer": t["proposer"],
            "recipient": t["recipient"],
            "role": "proposer" if viewer_id == t["proposer_id"] else "recipient",
            "created_at": t["created_at"],
            "expires_at": t["expires_at"],
            "resolved_at": t["resolved_at"],
            "offered": [public_card(cards[c["card_instance_id"]]) for c in t["cards"] if c["from_user_id"] == t["proposer_id"]],
            "requested": [public_card(cards[c["card_instance_id"]]) for c in t["cards"] if c["from_user_id"] == t["recipient_id"]],
            "problems": trade_problems(t, cards, viewer_id) if t["status"] == "PENDING" else [],
        })
    return views


def load_trade_for(cursor, trade_id, me):
    cursor.execute("SELECT * FROM trades WHERE id = %s FOR NO KEY UPDATE;", (trade_id,))
    trade = cursor.fetchone()
    if trade is None or me not in (trade["proposer_id"], trade["recipient_id"]):
        raise HTTPException(404, "No such trade")  # don't confirm other people's trades exist
    return trade


def close(connection, cursor, trade_id, status, code, detail, reason=None):
    """Resolve a trade and commit that before refusing the request, so the
    status sticks even though the request fails."""
    cursor.execute(
        "UPDATE trades SET status = %s, invalid_reason = %s, resolved_at = now() WHERE id = %s AND status = 'PENDING';",
        (status, reason, trade_id),
    )
    connection.commit()
    raise HTTPException(code, detail)


def ensure_pending(connection, cursor, trade):
    if trade["status"] != "PENDING":
        raise HTTPException(409, f"This trade is {trade['status'].lower()}")
    cursor.execute("SELECT %s <= now() AS expired;", (trade["expires_at"],))
    if cursor.fetchone()["expired"]:
        close(connection, cursor, trade["id"], "EXPIRED", 409, "This trade has expired")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/trades/rules")
def trade_rules():
    return rules()


@router.get("/trades/eligibility")
def my_eligibility(me: int = Depends(current_user_id)):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            return {**eligibility(cursor, me), "rules": rules()}


@router.get("/profiles/{username}/cards")
def tradeable_cards(username: str, me: int = Depends(current_user_id)):
    """A player's cards for the trade builder (identity only, no stats)."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE lower(username) = lower(%s);", (username,))
            user = cursor.fetchone()
            if user is None:
                raise HTTPException(404, "No such player")
            cursor.execute(f"SELECT {_CARD} {_CARD_FROM} WHERE ci.owner_id = %s AND ci.burned_at IS NULL ORDER BY ci.id;",
                           (user["id"],))
            rows = cursor.fetchall()
            # Your own cards may say they're in a battle; someone else's may not.
            return [dict(public_card(r), in_battle=r["in_battle"]) if user["id"] == me else public_card(r) for r in rows]


@router.post("/trades")
def propose(body: TradeCreate, me: int = Depends(current_user_id)):
    offered, requested = body.offered_card_ids, body.requested_card_ids
    for side, ids in (("offer", offered), ("ask for", requested)):
        if not MIN_CARDS_PER_SIDE <= len(ids) <= MAX_CARDS_PER_SIDE:
            raise HTTPException(400, f"You can {side} {MIN_CARDS_PER_SIDE} to {MAX_CARDS_PER_SIDE} cards")
    if len(set(offered) | set(requested)) != len(offered) + len(requested):
        raise HTTPException(400, "Each card can appear once")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, username FROM users WHERE lower(username) = lower(%s);", (body.recipient,))
            recipient = cursor.fetchone()
            if recipient is None:
                raise HTTPException(404, "No such player")
            if recipient["id"] == me:
                raise HTTPException(400, "You can't trade with yourself")

            for uid in (me, recipient["id"]):
                e = eligibility(cursor, uid)
                if not e["eligible"]:
                    who = "You" if uid == me else e["username"]
                    raise HTTPException(403, f"{who} can't trade yet: {'; '.join(e['reasons'])}")

            cursor.execute("SELECT count(*) AS n FROM trades WHERE proposer_id = %s AND status = 'PENDING' AND expires_at > now();",
                           (me,))
            if cursor.fetchone()["n"] >= MAX_PENDING_OUTGOING:
                raise HTTPException(409, f"You already have {MAX_PENDING_OUTGOING} open offers; cancel one first")

            cards = load_cards(cursor, offered + requested)
            for cid in offered + requested:
                if cid not in cards:
                    raise HTTPException(404, f"Card #{cid} not found")
                if cards[cid]["burned_at"] is not None:
                    raise HTTPException(409, f"Card #{cid} was destroyed in a squad challenge")
            for cid in offered:
                if cards[cid]["owner_id"] != me:
                    raise HTTPException(403, f"You don't own card #{cid}")
            for cid in requested:
                if cards[cid]["owner_id"] != recipient["id"]:
                    raise HTTPException(400, f"Card #{cid} isn't {recipient['username']}'s")

            cursor.execute(
                """
                INSERT INTO trades (proposer_id, recipient_id, expires_at)
                VALUES (%s, %s, now() + %s * interval '1 hour') RETURNING id;
                """,
                (me, recipient["id"], TRADE_EXPIRY_HOURS),
            )
            trade_id = cursor.fetchone()["id"]
            cursor.executemany(
                "INSERT INTO trade_cards (trade_id, card_instance_id, from_user_id) VALUES (%s, %s, %s);",
                [(trade_id, c, me) for c in offered] + [(trade_id, c, recipient["id"]) for c in requested],
            )
            return trade_views(cursor, [trade_id], me)[0]


@router.get("/trades")
def my_trades(me: int = Depends(current_user_id)):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, proposer_id = %(me)s AS outgoing,
                       status = 'PENDING' AND expires_at > now() AS open
                FROM trades WHERE %(me)s IN (proposer_id, recipient_id)
                ORDER BY COALESCE(resolved_at, created_at) DESC, id DESC;
                """,
                {"me": me},
            )
            rows = cursor.fetchall()
            incoming = [r["id"] for r in rows if r["open"] and not r["outgoing"]]
            outgoing = [r["id"] for r in rows if r["open"] and r["outgoing"]]
            history = [r["id"] for r in rows if not r["open"]][:HISTORY_SIZE]
            views = {v["id"]: v for v in trade_views(cursor, incoming + outgoing + history, me)}
            return {
                "incoming": [views[i] for i in incoming],
                "outgoing": [views[i] for i in outgoing],
                "history": [views[i] for i in history],
            }


@router.get("/trades/{trade_id}")
def get_trade(trade_id: int, me: int = Depends(current_user_id)):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM trades WHERE id = %s AND %s IN (proposer_id, recipient_id);", (trade_id, me))
            if cursor.fetchone() is None:
                raise HTTPException(404, "No such trade")
            return trade_views(cursor, [trade_id], me)[0]


@router.post("/trades/{trade_id}/decline")
def decline(trade_id: int, me: int = Depends(current_user_id)):
    return _resolve(trade_id, me, "recipient", "DECLINED")


@router.post("/trades/{trade_id}/cancel")
def cancel(trade_id: int, me: int = Depends(current_user_id)):
    return _resolve(trade_id, me, "proposer", "CANCELLED")


def _resolve(trade_id, me, who, status):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            trade = load_trade_for(cursor, trade_id, me)
            if trade[f"{who}_id"] != me:
                raise HTTPException(403, f"Only the {who} can do that")
            ensure_pending(connection, cursor, trade)
            cursor.execute(
                "UPDATE trades SET status = %s, resolved_at = now() WHERE id = %s AND status = 'PENDING';",
                (status, trade_id),
            )
            return trade_views(cursor, [trade_id], me)[0]


@router.post("/trades/{trade_id}/accept")
def accept(trade_id: int, me: int = Depends(current_user_id)):
    from backend.main import record_card_event  # avoid an import cycle

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # 1. The trade row: serialises accepts of this trade (double accept).
            trade = load_trade_for(cursor, trade_id, me)
            if trade["recipient_id"] != me:
                raise HTTPException(403, "Only the player it was offered to can accept")
            ensure_pending(connection, cursor, trade)
            proposer, recipient = trade["proposer_id"], trade["recipient_id"]

            # 2. Both users, ascending id: serialises each player's daily count.
            cursor.execute("SELECT id FROM users WHERE id = ANY(%s) ORDER BY id FOR NO KEY UPDATE;",
                           ([proposer, recipient],))
            for uid in sorted((proposer, recipient)):
                e = eligibility(cursor, uid)
                who = "You" if uid == me else e["username"]
                if not e["eligible"]:
                    raise HTTPException(403, f"{who} can't trade yet: {'; '.join(e['reasons'])}")
                if e["at_daily_limit"]:
                    raise HTTPException(
                        409, f"{who} already made {MAX_ACCEPTED_TRADES_PER_DAY} trades in the last 24 hours; try later")

            # 3. All cards, ascending id; everything re-read under the locks.
            cursor.execute("SELECT card_instance_id, from_user_id FROM trade_cards WHERE trade_id = %s ORDER BY card_instance_id;",
                           (trade_id,))
            legs = cursor.fetchall()
            ids = [leg["card_instance_id"] for leg in legs]
            cursor.execute("SELECT id FROM card_instances WHERE id = ANY(%s) ORDER BY id FOR NO KEY UPDATE;", (ids,))
            cards = load_cards(cursor, ids)
            for leg in legs:
                problem = card_problem(cards[leg["card_instance_id"]], leg["from_user_id"], leg["from_user_id"] == me)
                if problem and problem[0]:
                    close(connection, cursor, trade_id, "INVALID", 409,
                          f"This trade can't go through any more: {problem[1]}", reason=problem[1])
                if problem:
                    raise HTTPException(409, problem[1])

            # 4. Swap owners, guarded on the expected owner.
            for giver, taker in ((proposer, recipient), (recipient, proposer)):
                side = [leg["card_instance_id"] for leg in legs if leg["from_user_id"] == giver]
                cursor.execute(
                    "UPDATE card_instances SET owner_id = %s WHERE id = ANY(%s) AND owner_id = %s AND burned_at IS NULL;",
                    (taker, side, giver),
                )
                if cursor.rowcount != len(side):
                    raise HTTPException(409, "The cards changed; try again")
                for cid in side:
                    record_card_event(cursor, cid, "TRADED", from_user_id=giver, to_user_id=taker, related_trade_id=trade_id)

            cursor.execute(
                "UPDATE trades SET status = 'ACCEPTED', resolved_at = now() WHERE id = %s AND status = 'PENDING';",
                (trade_id,),
            )
            if cursor.rowcount != 1:
                raise HTTPException(409, "This trade is no longer pending")
            return trade_views(cursor, [trade_id], me)[0]
