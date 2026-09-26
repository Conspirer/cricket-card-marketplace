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

# A rare theme is only drawn when both decks have at least this many cards
# with verified data for it; otherwise that round is redrawn from common themes.
MIN_RARE_THEME_CARDS = 2

_rng = secrets.SystemRandom()


def credits_for_tier(tier):
    return TIER_CREDITS[tier]


def is_sudden_death(round_number):
    return round_number > REGULATION_ROUNDS


def has_theme_data(card, theme):
    return any(v is not None for v in ((card.get("stats") or {}).get(theme) or {}).values())


def eligible_rare_themes(*decks):
    """Rare themes where every deck has MIN_RARE_THEME_CARDS cards with data."""
    return {
        theme for theme, config in THEMES.items()
        if config["tier"] == "rare"
        and all(sum(has_theme_data(c, theme) for c in deck) >= MIN_RARE_THEME_CARDS for deck in decks)
    }


def draw_themes(count=MAX_ROUNDS, rng=_rng, rare_allowed=None):
    """Weighted theme per round, including the sudden-death round.

    A rare theme that isn't in rare_allowed (None = all allowed) is replaced by
    a draw from the common themes. Draws are independent (a theme can recur),
    except that a theme never repeats in consecutive rounds.
    """
    keys = list(THEMES)
    weights = [THEME_WEIGHTS[THEMES[k]["tier"]] for k in keys]
    commons = [k for k in keys if THEMES[k]["tier"] == "common"]
    common_weights = [THEME_WEIGHTS["common"]] * len(commons)
    themes = []
    while len(themes) < count:
        theme = rng.choices(keys, weights=weights, k=1)[0]
        if THEMES[theme]["tier"] == "rare" and rare_allowed is not None and theme not in rare_allowed:
            theme = rng.choices(commons, weights=common_weights, k=1)[0]
        if themes and themes[-1] == theme:
            continue
        themes.append(theme)
    return themes


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


def draw_sudden_death_stat(theme, rng=_rng):
    return rng.choice(THEMES[theme]["stats"])


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
