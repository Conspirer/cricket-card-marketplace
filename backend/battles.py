"""Battle rules. Pure functions and constants, no DB: shared by the API and the
balance harness (scripts/battle_balance.py), so both play the same game.

Each round both players secretly call a stat. Every call compares the two
cards on that stat, and whichever card wins earns its owner a point, so a bad
call can hand the opponent a point. Most points after 6 rounds wins. A tie
goes to one sudden-death round: the server draws one stat from the round's
theme and reveals it before the card pick; both cards are compared on it once
and the winner takes the battle. An exact tie (or no data on both) is a draw.
"""

import secrets

from backend.themes import STATS, THEME_WEIGHTS, THEMES

DECK_SIZE = 6
CREDIT_CAP = 100
# Cost is set by the player's tier (the highest rarity that player exists in),
# not by the card's own rarity. Rarity has no effect on battles.
TIER_CREDITS = {"Common": 10, "Rare": 15, "Epic": 22, "Legendary": 30}
TIER_ORDER = ["Common", "Rare", "Epic", "Legendary"]

REGULATION_ROUNDS = 6
MAX_SUDDEN_DEATH_ROUNDS = 1
MAX_ROUNDS = REGULATION_ROUNDS + MAX_SUDDEN_DEATH_ROUNDS

CHALLENGE_EXPIRY_SECONDS = 5 * 60
CARD_PICK_SECONDS = 30
CALL_SECONDS = 15
# Pause between rounds so both players see the reveal before the next pick.
REVEAL_SECONDS = 6

_rng = secrets.SystemRandom()


def credits_for_tier(tier):
    return TIER_CREDITS[tier]


def is_sudden_death(round_number):
    return round_number > REGULATION_ROUNDS


def has_theme_data(card, theme):
    return any(v is not None for v in ((card.get("stats") or {}).get(theme) or {}).values())


def has_stat(card, theme, stat):
    return stat_value(card, theme, stat) is not None


def playable_themes(*hands):
    """Themes where every hand still has at least one card with data for it."""
    return {theme for theme in THEMES if all(any(has_theme_data(c, theme) for c in hand) for hand in hands)}


def draw_round_theme(hand_a, hand_b, previous=None, rng=_rng):
    """This round's theme, weighted by tier, drawn only from themes both
    players can still play a card with data for. Never the previous round's
    theme when another one is possible."""
    eligible = playable_themes(hand_a, hand_b) or set(THEMES)  # no shared theme: anything goes
    candidates = sorted(t for t in eligible if t != previous) or sorted(eligible)
    weights = [THEME_WEIGHTS[THEMES[t]["tier"]] for t in candidates]
    return rng.choices(candidates, weights=weights, k=1)[0]


def pickable(cards, theme, stat=None):
    """Cards that may be played: those with data for the theme (or, in sudden
    death, for the drawn stat). If none have it, any card, so a hand never locks."""
    with_data = [c for c in cards if (has_stat(c, theme, stat) if stat else has_theme_data(c, theme))]
    return with_data or list(cards)


def stat_value(card, theme, stat):
    """A card's value for a stat, or None for no data (or below the minimum sample)."""
    return ((card.get("stats") or {}).get(theme) or {}).get(stat)


def compare(stat, a, b):
    """1 if a wins, -1 if b wins, 0 no point. No data loses; both no data or equal: no point."""
    if a is None and b is None:
        return 0
    if a is None:
        return -1
    if b is None:
        return 1
    if a == b:
        return 0
    higher_wins = not STATS[stat]["lower_wins"]
    return 1 if (a > b) == higher_wins else -1


def score_round(theme, challenger_card, opponent_card, challenger_stat, opponent_stat):
    """Resolve both calls. Each call awards 1 point to whichever card wins it."""
    out = {"challenger_points": 0, "opponent_points": 0}
    for side, stat in (("challenger", challenger_stat), ("opponent", opponent_stat)):
        c_value = stat_value(challenger_card, theme, stat)
        o_value = stat_value(opponent_card, theme, stat)
        result = compare(stat, c_value, o_value)
        if result > 0:
            out["challenger_points"] += 1
        elif result < 0:
            out["opponent_points"] += 1
        out[f"{side}_call"] = {"stat": stat, "challenger_value": c_value, "opponent_value": o_value, "result": result}
    return out


def draw_sudden_death_stat(theme, deck_a, deck_b, rng=_rng):
    """A stat from the theme that both decks have a card for, if there is one."""
    stats = THEMES[theme]["stats"]
    shared = [st for st in stats if any(has_stat(c, theme, st) for c in deck_a) and any(has_stat(c, theme, st) for c in deck_b)]
    return rng.choice(shared or stats)


def sudden_death(theme, stat, challenger_card, opponent_card):
    """The one comparison that settles a tied battle: 1 challenger, -1 opponent, 0 draw."""
    c_value = stat_value(challenger_card, theme, stat)
    o_value = stat_value(opponent_card, theme, stat)
    return {"stat": stat, "challenger_value": c_value, "opponent_value": o_value,
            "result": compare(stat, c_value, o_value)}


def battle_over(round_number, challenger_points, opponent_points):
    """After resolving a round: over once regulation ends with the points apart, or after sudden death."""
    if round_number >= REGULATION_ROUNDS and challenger_points != opponent_points:
        return True
    return round_number >= MAX_ROUNDS


def outcome(rounds_played, challenger_points, opponent_points):
    """(winner, decided_by): winner is "challenger", "opponent" or None (drawn).

    The sudden-death winner is given its point before this is called, so the
    points always decide; decided_by says in which part of the battle.
    """
    if challenger_points == opponent_points:
        return None, "draw"
    winner = "challenger" if challenger_points > opponent_points else "opponent"
    return winner, "sudden_death" if is_sudden_death(rounds_played) else "regulation"
