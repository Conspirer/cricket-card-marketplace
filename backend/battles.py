"""Battle rules. Pure functions and constants, no DB: shared by the API and the
balance harness (scripts/battle_balance.py), so both play the same game."""

import secrets

from backend.themes import STATS, THEME_WEIGHTS, THEMES

DECK_SIZE = 6
CREDIT_CAP = 100
# Cost is set by the player's tier (the highest rarity that player exists in),
# not by the card's own rarity. Rarity has no effect on battles.
TIER_CREDITS = {"Common": 10, "Rare": 15, "Epic": 22, "Legendary": 30}
TIER_ORDER = ["Common", "Rare", "Epic", "Legendary"]

REGULATION_ROUNDS = 6
SUDDEN_DEATH_ROUND = 7

CHALLENGE_EXPIRY_SECONDS = 5 * 60
CARD_PICK_SECONDS = 30
STAT_CALL_SECONDS = 15
# Pause between rounds so both players see the reveal before the next pick.
REVEAL_SECONDS = 6

_rng = secrets.SystemRandom()


def credits_for_tier(tier):
    return TIER_CREDITS[tier]


def draw_themes(count=REGULATION_ROUNDS + 1, rng=_rng):
    """Weighted theme per round (the last is the sudden-death theme).

    Draws are independent (a theme can recur), except that a theme never
    repeats in consecutive rounds.
    """
    keys = list(THEMES)
    weights = [THEME_WEIGHTS[THEMES[k]["tier"]] for k in keys]
    themes = []
    while len(themes) < count:
        theme = rng.choices(keys, weights=weights, k=1)[0]
        if themes and themes[-1] == theme:
            continue
        themes.append(theme)
    return themes


def caller_for_round(round_number, challenger_id, opponent_id, sudden_death_caller_id):
    """Challenger calls odd rounds, opponent even; sudden death by coin flip."""
    if round_number == SUDDEN_DEATH_ROUND:
        return sudden_death_caller_id
    return challenger_id if round_number % 2 == 1 else opponent_id


def stat_value(card, theme, stat):
    """A card's value for a stat, or None for no data (or below the minimum sample)."""
    return ((card.get("stats") or {}).get(theme) or {}).get(stat)


def compare(stat, a, b):
    """1 if a wins, -1 if b wins, 0 drawn. No data loses; both no data or equal draws."""
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


def battle_winner(challenger_wins, opponent_wins):
    if challenger_wins > opponent_wins:
        return "challenger"
    if opponent_wins > challenger_wins:
        return "opponent"
    return None
