"""SBC requirement engine. Pure functions, no DB: evaluated server-side on the
locked cards at submission (and for the live checklist).

A challenge's requirements are a list of rules; every rule must pass. Cards are
dicts: {card_id, player_id, rarity, country, role,
        theme_stats: {theme: {"verified": bool, "matches": int, "stats": {...}}}}.
Only verified theme stats count, and keeper status / bowling type are mostly
empty, so there are deliberately no rules on them.

Rule types:
  {"type": "count", "n": 3}                                   exactly n cards
  {"type": "min_rarity", "rarity": "Rare"}                    every card at least this rarity
  {"type": "min_rarity", "rarity": "Epic", "min": 1}          at least `min` cards at least this rarity
  {"type": "country", "country": "India", "min": 1}           at least `min` cards from a country
  {"type": "distinct_countries", "min": 5}                    cards from at least `min` countries
  {"type": "role", "role": "Bowler", "min": 3}                at least `min` cards with this role
  {"type": "played_in", "theme": "ODI_WC", "min": 3}          at least `min` with verified stats (matches > 0) in a theme
  {"type": "combined_stat", "theme": "TEST", "stat": "runs", "min": 10000}
                                                              verified values summed across the cards
Every submission also needs each card to be a different player.
"""

from backend.themes import STATS, THEMES

RARITY_ORDER = ["Common", "Rare", "Epic", "Legendary"]
RULE_TYPES = {"count", "min_rarity", "country", "distinct_countries", "role", "played_in", "combined_stat"}


class InvalidRule(ValueError):
    pass


def validate_rules(rules):
    """Reject malformed challenge definitions when they're loaded, not when played."""
    if not isinstance(rules, list) or not rules:
        raise InvalidRule("requirements must be a non-empty list")
    for rule in rules:
        kind = rule.get("type")
        if kind not in RULE_TYPES:
            raise InvalidRule(f"unknown rule type {kind!r}")
        if kind == "min_rarity" and rule.get("rarity") not in RARITY_ORDER:
            raise InvalidRule(f"unknown rarity {rule.get('rarity')!r}")
        if kind in ("played_in", "combined_stat") and rule.get("theme") not in THEMES:
            raise InvalidRule(f"unknown theme {rule.get('theme')!r}")
        if kind == "combined_stat" and rule.get("stat") not in THEMES[rule["theme"]]["stats"]:
            raise InvalidRule(f"{rule.get('stat')!r} isn't a stat in {rule['theme']}")
    if not any(r["type"] == "count" for r in rules):
        raise InvalidRule("every challenge needs a count rule")


def required_count(rules):
    return next(r["n"] for r in rules if r["type"] == "count")


def _rank(rarity):
    return RARITY_ORDER.index(rarity)


def _played(card, theme):
    row = (card.get("theme_stats") or {}).get(theme)
    return bool(row and row["verified"] and row["matches"] > 0)


def _stat(card, theme, stat):
    row = (card.get("theme_stats") or {}).get(theme)
    if not row or not row["verified"]:
        return 0
    return (row["stats"] or {}).get(stat) or 0


def describe(rule):
    kind = rule["type"]
    if kind == "count":
        return f"Exactly {rule['n']} cards"
    if kind == "min_rarity":
        return (f"At least {rule['min']} {rule['rarity']} or better" if rule.get("min")
                else f"Every card {rule['rarity']} or better")
    if kind == "country":
        return f"At least {rule['min']} from {rule['country']}"
    if kind == "distinct_countries":
        return f"Players from at least {rule['min']} countries"
    if kind == "role":
        return f"At least {rule['min']} {rule['role'].lower()}{'s' if rule['min'] != 1 else ''}"
    if kind == "played_in":
        return f"At least {rule['min']} who played in {THEMES[rule['theme']]['label']}"
    if kind == "combined_stat":
        return f"Combined {THEMES[rule['theme']]['label']} {STATS[rule['stat']]['label'].lower()} of {rule['min']:,}+"
    raise InvalidRule(kind)


def check_rule(rule, cards):
    """(ok, progress text)"""
    kind = rule["type"]
    if kind == "count":
        return len(cards) == rule["n"], f"{len(cards)}/{rule['n']}"
    if kind == "min_rarity":
        good = sum(1 for c in cards if _rank(c["rarity"]) >= _rank(rule["rarity"]))
        if rule.get("min"):
            return good >= rule["min"], f"{good}/{rule['min']}"
        return bool(cards) and good == len(cards), f"{good}/{len(cards)}"
    if kind == "country":
        n = sum(1 for c in cards if c["country"] == rule["country"])
        return n >= rule["min"], f"{n}/{rule['min']}"
    if kind == "distinct_countries":
        n = len({c["country"] for c in cards})
        return n >= rule["min"], f"{n}/{rule['min']}"
    if kind == "role":
        n = sum(1 for c in cards if c["role"] == rule["role"])
        return n >= rule["min"], f"{n}/{rule['min']}"
    if kind == "played_in":
        n = sum(1 for c in cards if _played(c, rule["theme"]))
        return n >= rule["min"], f"{n}/{rule['min']}"
    if kind == "combined_stat":
        total = sum(_stat(c, rule["theme"], rule["stat"]) for c in cards)
        return total >= rule["min"], f"{total:,}/{rule['min']:,}"
    raise InvalidRule(kind)


def evaluate(rules, cards):
    """Checklist for a set of cards: [{"rule", "ok", "progress"}], plus the
    implicit different-players rule. Everything must be ok to submit."""
    checklist = [{"rule": describe(r), "ok": ok, "progress": progress}
                 for r in rules for ok, progress in [check_rule(r, cards)]]
    players = [c["player_id"] for c in cards]
    distinct = len(set(players)) == len(players)
    checklist.append({"rule": "Each card a different player", "ok": distinct,
                      "progress": f"{len(set(players))}/{len(players)}"})
    return checklist


def all_met(checklist):
    return all(item["ok"] for item in checklist)
