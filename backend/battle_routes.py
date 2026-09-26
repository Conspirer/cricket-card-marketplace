"""Themed PvP battles: challenge, accept, secret picks, stat calls, reveals.

Every mutating request locks the battle row, applies any deadlines that have
passed (lazy timeouts), validates, and resolves the phase in the same
transaction, so simultaneous submissions and timeouts resolve exactly once.

Secrecy: deck snapshots (with every stat) never leave the server. The only
numbers any response contains are values from battle_rounds, i.e. rounds
whose stat has already been called, plus those two cards' theme stats.
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from backend import battles as rules
from backend.database import get_connection
from backend.themes import STATS, THEMES

router = APIRouter()


class ChallengeCreate(BaseModel):
    challenger_id: int
    opponent_id: int
    card_ids: list[int]


class AcceptBody(BaseModel):
    user_id: int
    card_ids: list[int]


class UserBody(BaseModel):
    user_id: int


class PickBody(BaseModel):
    user_id: int
    round: int
    card_id: int


class CallBody(BaseModel):
    user_id: int
    round: int
    stat: str


# ---------------------------------------------------------------------------
# Decks
# ---------------------------------------------------------------------------

_CARD_SELECT = """
    SELECT ci.id AS card_id, ci.owner_id, ci.serial_number, d.rarity, d.max_supply,
           p.id AS player_id, p.name, p.role, p.country,
           (SELECT max(array_position(ARRAY['Common', 'Rare', 'Epic', 'Legendary'], d2.rarity))
              FROM card_definitions d2 WHERE d2.player_id = p.id) AS tier_rank
"""
# Only verified rows: an unverified stat counts as no data rather than risk a wrong number.
_STATS_COLUMN = """,
           COALESCE((SELECT jsonb_object_agg(t.theme, t.stats)
                       FROM player_theme_stats t
                      WHERE t.player_id = p.id AND t.verified), '{}'::jsonb) AS stats
"""
_CARD_FROM = """
    FROM card_instances ci
    JOIN card_definitions d ON d.id = ci.card_definition_id
    JOIN players p ON p.id = d.player_id
    WHERE ci.id = ANY(%s);
"""


def load_deck(cursor, user_id, card_ids, with_stats=False):
    """Validate a deck and return its cards in the order given."""
    if len(card_ids) != rules.DECK_SIZE or len(set(card_ids)) != rules.DECK_SIZE:
        raise HTTPException(400, f"A deck is exactly {rules.DECK_SIZE} different cards")

    cursor.execute(_CARD_SELECT + (_STATS_COLUMN if with_stats else "") + _CARD_FROM, (card_ids,))
    found = {row["card_id"]: row for row in cursor.fetchall()}
    missing = [c for c in card_ids if c not in found]
    if missing:
        raise HTTPException(404, f"Card #{missing[0]} not found")

    cards = []
    for card_id in card_ids:
        row = found[card_id]
        if row["owner_id"] != user_id:
            raise HTTPException(403, f"You don't own card #{card_id}")
        tier = rules.TIER_ORDER[row["tier_rank"] - 1]
        card = {
            "card_id": card_id,
            "player_id": row["player_id"],
            "name": row["name"],
            "role": row["role"],
            "country": row["country"],
            "rarity": row["rarity"],
            "serial_number": row["serial_number"],
            "max_supply": row["max_supply"],
            "tier": tier,
            "credits": rules.credits_for_tier(tier),
        }
        if with_stats:
            card["stats"] = row["stats"]
        cards.append(card)

    cost = sum(c["credits"] for c in cards)
    if cost > rules.CREDIT_CAP:
        raise HTTPException(400, f"Deck costs {cost} credits; the cap is {rules.CREDIT_CAP}")
    return cards


PUBLIC_CARD_KEYS = ("card_id", "name", "role", "country", "rarity", "serial_number", "max_supply", "credits")


def public_card(card):
    """A card as shown during a battle: identity only, never a number from its stats."""
    return {k: card[k] for k in PUBLIC_CARD_KEYS}


# ---------------------------------------------------------------------------
# Battle state machine
# ---------------------------------------------------------------------------

class Battle:
    """A locked battle row plus the transaction that holds it."""

    def __init__(self, cursor, battle_id):
        self.cursor = cursor
        cursor.execute("SELECT * FROM battles WHERE id = %s FOR NO KEY UPDATE;", (battle_id,))
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(404, "Battle not found")
        self.b = row
        # Read the clock after the lock is held, so waiting for the lock
        # doesn't make a late submission look early.
        cursor.execute("SELECT clock_timestamp() AS now;")
        self.now = cursor.fetchone()["now"]

    # -- helpers -----------------------------------------------------------

    def side(self, user_id):
        if user_id == self.b["challenger_id"]:
            return "challenger"
        if user_id == self.b["opponent_id"]:
            return "opponent"
        return None

    def other(self, user_id):
        return self.b["opponent_id"] if user_id == self.b["challenger_id"] else self.b["challenger_id"]

    def deck(self, user_id):
        return self.b["challenger_deck"] if user_id == self.b["challenger_id"] else self.b["opponent_deck"]

    def card(self, user_id, card_id):
        return next((c for c in self.deck(user_id) or [] if c["card_id"] == card_id), None)

    def theme(self, round_number=None):
        return self.b["themes"][(round_number or self.b["current_round"]) - 1]

    def caller(self, round_number=None):
        return rules.caller_for_round(
            round_number or self.b["current_round"],
            self.b["challenger_id"], self.b["opponent_id"], self.b["sudden_death_caller_id"],
        )

    def moves(self, round_number=None):
        self.cursor.execute(
            "SELECT * FROM battle_moves WHERE battle_id = %s AND round = %s;",
            (self.b["id"], round_number or self.b["current_round"]),
        )
        return {m["player_id"]: m for m in self.cursor.fetchall()}

    def used_cards(self, user_id):
        """Cards this player has played in earlier regulation rounds."""
        self.cursor.execute(
            "SELECT card_id FROM battle_moves WHERE battle_id = %s AND player_id = %s AND round < %s;",
            (self.b["id"], user_id, min(self.b["current_round"], rules.SUDDEN_DEATH_ROUND)),
        )
        return {r["card_id"] for r in self.cursor.fetchall()}

    def playable(self, user_id):
        deck = self.deck(user_id)
        if self.b["current_round"] == rules.SUDDEN_DEATH_ROUND:
            return [c["card_id"] for c in deck]  # any card may be reused
        used = self.used_cards(user_id)
        return [c["card_id"] for c in deck if c["card_id"] not in used]

    def start_phase(self, phase, seconds, base):
        self.b["phase"] = phase
        self.b["phase_deadline"] = base + timedelta(seconds=seconds)

    def begin_round(self, number, base):
        self.b["current_round"] = number
        self.start_phase("CARD_PICK", rules.CARD_PICK_SECONDS, base)

    def finish(self, at, status="FINISHED", winner_id=None):
        self.b["status"] = status
        self.b["phase"] = None
        self.b["phase_deadline"] = None
        self.b["finished_at"] = at
        if status == "FINISHED":
            outcome = rules.battle_winner(self.b["challenger_wins"], self.b["opponent_wins"])
            winner_id = {"challenger": self.b["challenger_id"], "opponent": self.b["opponent_id"]}.get(outcome)
        self.b["winner_id"] = winner_id

    # -- transitions -------------------------------------------------------

    def record_pick(self, user_id, card_id, timed_out=False):
        self.cursor.execute(
            """
            INSERT INTO battle_moves (battle_id, round, player_id, card_id, pick_timed_out)
            VALUES (%s, %s, %s, %s, %s);
            """,
            (self.b["id"], self.b["current_round"], user_id, card_id, timed_out),
        )

    def after_picks(self, base):
        self.start_phase("STAT_CALL", rules.STAT_CALL_SECONDS, base)

    def record_call(self, stat, timed_out=False):
        self.cursor.execute(
            """
            UPDATE battle_moves SET stat = %s, call_timed_out = %s
            WHERE battle_id = %s AND round = %s AND player_id = %s AND stat IS NULL;
            """,
            (stat, timed_out, self.b["id"], self.b["current_round"], self.caller()),
        )

    def resolve_round(self, base):
        b = self.b
        number, theme, caller = b["current_round"], self.theme(), self.caller()
        moves = self.moves()
        stat = moves[caller]["stat"]
        c_move, o_move = moves[b["challenger_id"]], moves[b["opponent_id"]]
        c_value = rules.stat_value(self.card(b["challenger_id"], c_move["card_id"]), theme, stat)
        o_value = rules.stat_value(self.card(b["opponent_id"], o_move["card_id"]), theme, stat)

        result = rules.compare(stat, c_value, o_value)
        winner_id = b["challenger_id"] if result > 0 else b["opponent_id"] if result < 0 else None
        if result > 0:
            b["challenger_wins"] += 1
        elif result < 0:
            b["opponent_wins"] += 1
        else:
            b["draws"] += 1

        self.cursor.execute(
            """
            INSERT INTO battle_rounds (
                battle_id, round, theme, caller_id, challenger_card_id, opponent_card_id, stat,
                challenger_value, opponent_value, winner_id,
                challenger_pick_timed_out, opponent_pick_timed_out, call_timed_out
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                b["id"], number, theme, caller, c_move["card_id"], o_move["card_id"], stat,
                c_value, o_value, winner_id,
                c_move["pick_timed_out"], o_move["pick_timed_out"], moves[caller]["call_timed_out"],
            ),
        )

        tied = b["challenger_wins"] == b["opponent_wins"]
        if number == rules.SUDDEN_DEATH_ROUND or (number == rules.REGULATION_ROUNDS and not tied):
            self.finish(base)
        else:
            self.start_phase("REVEAL", rules.REVEAL_SECONDS, base)

    def after_reveal(self, base):
        number = self.b["current_round"]
        # Round 6 only reaches REVEAL when tied (otherwise it finished).
        self.begin_round(number + 1 if number < rules.REGULATION_ROUNDS else rules.SUDDEN_DEATH_ROUND, base)

    def advance(self):
        """Apply every deadline that has passed. Idempotent under the row lock."""
        b = self.b
        if b["status"] == "PENDING" and b["expires_at"] <= self.now:
            b["status"] = "EXPIRED"
            return
        while b["status"] == "ACTIVE" and b["phase_deadline"] <= self.now:
            # Chain from the missed deadline, not from now, so a battle left
            # unattended plays out on the same schedule it would have.
            deadline = b["phase_deadline"]
            if b["phase"] == "CARD_PICK":
                moves = self.moves()
                for user_id in (b["challenger_id"], b["opponent_id"]):
                    if user_id not in moves:
                        self.record_pick(user_id, rules._rng.choice(self.playable(user_id)), timed_out=True)
                self.after_picks(deadline)
            elif b["phase"] == "STAT_CALL":
                self.record_call(rules._rng.choice(THEMES[self.theme()]["stats"]), timed_out=True)
                self.resolve_round(deadline)
            elif b["phase"] == "REVEAL":
                self.after_reveal(deadline)

    def save(self):
        b = self.b
        self.cursor.execute(
            """
            UPDATE battles SET
                status = %s, opponent_card_ids = %s, challenger_deck = %s, opponent_deck = %s,
                themes = %s, current_round = %s, phase = %s, phase_deadline = %s,
                sudden_death_caller_id = %s, challenger_wins = %s, opponent_wins = %s, draws = %s,
                winner_id = %s, started_at = %s, finished_at = %s
            WHERE id = %s;
            """,
            (
                b["status"], b["opponent_card_ids"],
                Jsonb(b["challenger_deck"]) if b["challenger_deck"] is not None else None,
                Jsonb(b["opponent_deck"]) if b["opponent_deck"] is not None else None,
                b["themes"], b["current_round"], b["phase"], b["phase_deadline"],
                b["sudden_death_caller_id"], b["challenger_wins"], b["opponent_wins"], b["draws"],
                b["winner_id"], b["started_at"], b["finished_at"], b["id"],
            ),
        )

    def reject(self, connection, status, detail):
        """Refuse the action but keep any deadline progress already applied."""
        self.save()
        connection.commit()
        raise HTTPException(status, detail)


# ---------------------------------------------------------------------------
# Views: the only place battle data leaves the server
# ---------------------------------------------------------------------------

def theme_meta(theme):
    config = THEMES[theme]
    return {
        "key": theme,
        "label": config["label"],
        "tier": config["tier"],
        "stats": [{"key": s, "label": STATS[s]["label"], "lower_wins": STATS[s]["lower_wins"]} for s in config["stats"]],
    }


def usernames(cursor, *ids):
    cursor.execute("SELECT id, username FROM users WHERE id = ANY(%s);", (list(ids),))
    return {r["id"]: r["username"] for r in cursor.fetchall()}


def view(battle, viewer_id):
    b, cursor = battle.b, battle.cursor
    me = battle.side(viewer_id)
    if me is None:
        raise HTTPException(403, "Only the two players can view this battle")
    them_id = battle.other(viewer_id)
    names = usernames(cursor, viewer_id, them_id)
    mine = "challenger_wins" if me == "challenger" else "opponent_wins"
    theirs = "opponent_wins" if me == "challenger" else "challenger_wins"

    def who(user_id):
        return None if user_id is None else ("you" if user_id == viewer_id else "them")

    out = {
        "id": b["id"],
        "status": b["status"],
        "you_are": me,
        "you": names.get(viewer_id),
        "opponent": {"id": them_id, "username": names.get(them_id)},
        "server_now": battle.now,
        "created_at": b["created_at"],
        "expires_at": b["expires_at"],
        "current_round": b["current_round"],
        "phase": b["phase"],
        "phase_deadline": b["phase_deadline"],
        "score": {"you": b[mine], "them": b[theirs], "draws": b["draws"]},
        "winner": who(b["winner_id"]),
        "hand": [],
        "current": None,
        "rounds": [],
    }

    if b["status"] == "PENDING" or b["challenger_deck"] is None:
        if me == "challenger":
            cursor.execute(_CARD_SELECT + _CARD_FROM, (b["challenger_card_ids"],))
            by_id = {r["card_id"]: r for r in cursor.fetchall()}
            out["hand"] = [
                {**{k: by_id[c][k] for k in ("card_id", "name", "role", "country", "rarity", "serial_number", "max_supply")},
                 "credits": rules.credits_for_tier(rules.TIER_ORDER[by_id[c]["tier_rank"] - 1]), "used": False}
                for c in b["challenger_card_ids"] if c in by_id
            ]
        return out

    # Resolved rounds: the called values and both cards' theme stats are public now.
    cursor.execute("SELECT * FROM battle_rounds WHERE battle_id = %s ORDER BY round;", (b["id"],))
    resolved = cursor.fetchall()
    my_col = "challenger" if me == "challenger" else "opponent"
    their_col = "opponent" if me == "challenger" else "challenger"
    for r in resolved:
        theme = r["theme"]
        my_card = battle.card(viewer_id, r[f"{my_col}_card_id"])
        their_card = battle.card(them_id, r[f"{their_col}_card_id"])
        out["rounds"].append({
            "round": r["round"],
            "theme": theme_meta(theme),
            "caller": who(r["caller_id"]),
            "stat": r["stat"],
            "your_card": public_card(my_card),
            "their_card": public_card(their_card),
            "your_value": r[f"{my_col}_value"],
            "their_value": r[f"{their_col}_value"],
            "result": "draw" if r["winner_id"] is None else who(r["winner_id"]),
            "your_pick_timed_out": r[f"{my_col}_pick_timed_out"],
            "their_pick_timed_out": r[f"{their_col}_pick_timed_out"],
            "call_timed_out": r["call_timed_out"],
            "your_card_stats": {s: rules.stat_value(my_card, theme, s) for s in THEMES[theme]["stats"]},
            "their_card_stats": {s: rules.stat_value(their_card, theme, s) for s in THEMES[theme]["stats"]},
        })

    used = {r[f"{my_col}_card_id"] for r in resolved if r["round"] <= rules.REGULATION_ROUNDS}
    out["hand"] = [dict(public_card(c), used=c["card_id"] in used) for c in battle.deck(viewer_id)]

    if b["status"] == "ACTIVE" and b["phase"] in ("CARD_PICK", "STAT_CALL"):
        moves = battle.moves()
        mine_move, their_move = moves.get(viewer_id), moves.get(them_id)
        both_in = mine_move is not None and their_move is not None
        out["current"] = {
            "round": b["current_round"],
            "sudden_death": b["current_round"] == rules.SUDDEN_DEATH_ROUND,
            "theme": theme_meta(battle.theme()),  # only the current theme; later ones stay hidden
            "caller": who(battle.caller()),
            "your_pick": public_card(battle.card(viewer_id, mine_move["card_id"])) if mine_move else None,
            "their_pick_made": their_move is not None,
            # The opponent's card is shown only once both picks are in.
            "their_pick": public_card(battle.card(them_id, their_move["card_id"])) if both_in else None,
        }
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/battles")
def create_challenge(body: ChallengeCreate):
    if body.challenger_id == body.opponent_id:
        raise HTTPException(400, "You can't challenge yourself")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE id = ANY(%s);", ([body.challenger_id, body.opponent_id],))
            if len(cursor.fetchall()) != 2:
                raise HTTPException(404, "User not found")
            load_deck(cursor, body.challenger_id, body.card_ids)
            cursor.execute(
                """
                INSERT INTO battles (challenger_id, opponent_id, challenger_card_ids, expires_at)
                VALUES (%s, %s, %s, clock_timestamp() + %s * interval '1 second')
                RETURNING id;
                """,
                (body.challenger_id, body.opponent_id, body.card_ids, rules.CHALLENGE_EXPIRY_SECONDS),
            )
            battle = Battle(cursor, cursor.fetchone()["id"])
            return view(battle, body.challenger_id)


@router.post("/battles/{battle_id}/accept")
def accept_challenge(battle_id: int, body: AcceptBody):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            battle = Battle(cursor, battle_id)
            battle.advance()
            b = battle.b
            if battle.side(body.user_id) != "opponent":
                battle.reject(connection, 403, "Only the challenged player can accept")
            if b["status"] != "PENDING":
                battle.reject(connection, 409, f"This challenge is {b['status'].lower()}")

            opponent_deck = load_deck(cursor, body.user_id, body.card_ids, with_stats=True)
            try:
                challenger_deck = load_deck(cursor, b["challenger_id"], b["challenger_card_ids"], with_stats=True)
            except HTTPException as error:
                raise HTTPException(409, f"The challenger's deck is no longer valid: {error.detail}")

            b.update(
                status="ACTIVE",
                opponent_card_ids=body.card_ids,
                challenger_deck=challenger_deck,
                opponent_deck=opponent_deck,
                themes=rules.draw_themes(),
                sudden_death_caller_id=rules._rng.choice([b["challenger_id"], b["opponent_id"]]),
                started_at=battle.now,
            )
            battle.begin_round(1, battle.now)
            battle.save()
            return view(battle, body.user_id)


@router.post("/battles/{battle_id}/decline")
def decline_challenge(battle_id: int, body: UserBody):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            battle = Battle(cursor, battle_id)
            battle.advance()
            if battle.side(body.user_id) is None:
                battle.reject(connection, 403, "Only the two players can decline")
            if battle.b["status"] != "PENDING":
                battle.reject(connection, 409, f"This challenge is {battle.b['status'].lower()}")
            battle.b["status"] = "DECLINED"
            battle.b["finished_at"] = battle.now
            battle.save()
            return view(battle, body.user_id)


@router.post("/battles/{battle_id}/forfeit")
def forfeit_battle(battle_id: int, body: UserBody):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            battle = Battle(cursor, battle_id)
            battle.advance()
            if battle.side(body.user_id) is None:
                battle.reject(connection, 403, "Only the two players can forfeit")
            if battle.b["status"] != "ACTIVE":
                battle.reject(connection, 409, f"This battle is {battle.b['status'].lower()}")
            battle.finish(battle.now, status="FORFEIT", winner_id=battle.other(body.user_id))
            battle.save()
            return view(battle, body.user_id)


def _check_turn(battle, connection, user_id, round_number, phase):
    b = battle.b
    if battle.side(user_id) is None:
        battle.reject(connection, 403, "You're not in this battle")
    if b["status"] != "ACTIVE":
        battle.reject(connection, 409, f"This battle is {b['status'].lower()}")
    if round_number != b["current_round"]:
        battle.reject(connection, 409, f"It's round {b['current_round']}, not round {round_number}")
    if b["phase"] != phase:
        battle.reject(connection, 409, f"Round {round_number} is in {b['phase'].replace('_', ' ').lower()}")


@router.post("/battles/{battle_id}/pick")
def pick_card(battle_id: int, body: PickBody):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            battle = Battle(cursor, battle_id)
            battle.advance()
            _check_turn(battle, connection, body.user_id, body.round, "CARD_PICK")

            if body.user_id in battle.moves():
                battle.reject(connection, 409, "You've already picked for this round")
            if battle.card(body.user_id, body.card_id) is None:
                battle.reject(connection, 400, "That card isn't in your deck")
            if body.card_id not in battle.playable(body.user_id):
                battle.reject(connection, 409, "You've already played that card")

            battle.record_pick(body.user_id, body.card_id)
            if len(battle.moves()) == 2:
                battle.after_picks(battle.now)
            battle.save()
            return view(battle, body.user_id)


@router.post("/battles/{battle_id}/call")
def call_stat(battle_id: int, body: CallBody):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            battle = Battle(cursor, battle_id)
            battle.advance()
            _check_turn(battle, connection, body.user_id, body.round, "STAT_CALL")

            if body.user_id != battle.caller():
                battle.reject(connection, 403, "Your opponent calls this round")
            if body.stat not in THEMES[battle.theme()]["stats"]:
                battle.reject(connection, 400, f"{body.stat} isn't a stat in {THEMES[battle.theme()]['label']}")

            battle.record_call(body.stat)
            battle.resolve_round(battle.now)
            battle.save()
            return view(battle, body.user_id)


@router.get("/battles/{battle_id}")
def get_battle(battle_id: int, user_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            battle = Battle(cursor, battle_id)
            battle.advance()
            battle.save()
            return view(battle, user_id)


@router.get("/users/{user_id}/battles")
def list_battles(user_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE battles SET status = 'EXPIRED'
                WHERE status = 'PENDING' AND expires_at <= clock_timestamp()
                  AND (challenger_id = %s OR opponent_id = %s);
                """,
                (user_id, user_id),
            )
            cursor.execute(
                """
                SELECT b.id, b.status, b.challenger_id, b.opponent_id, b.current_round, b.phase,
                       b.challenger_wins, b.opponent_wins, b.draws, b.winner_id,
                       b.created_at, b.expires_at, b.finished_at,
                       c.username AS challenger_username, o.username AS opponent_username
                FROM battles b
                JOIN users c ON c.id = b.challenger_id
                JOIN users o ON o.id = b.opponent_id
                WHERE b.challenger_id = %s OR b.opponent_id = %s
                ORDER BY b.created_at DESC
                LIMIT 100;
                """,
                (user_id, user_id),
            )
            rows = cursor.fetchall()

    out = []
    for r in rows:
        me_challenger = r["challenger_id"] == user_id
        out.append({
            "id": r["id"],
            "status": r["status"],
            "you_are": "challenger" if me_challenger else "opponent",
            "opponent": r["opponent_username"] if me_challenger else r["challenger_username"],
            "current_round": r["current_round"],
            "phase": r["phase"],
            "score": {
                "you": r["challenger_wins"] if me_challenger else r["opponent_wins"],
                "them": r["opponent_wins"] if me_challenger else r["challenger_wins"],
                "draws": r["draws"],
            },
            "winner": None if r["winner_id"] is None else ("you" if r["winner_id"] == user_id else "them"),
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "finished_at": r["finished_at"],
        })
    return out
