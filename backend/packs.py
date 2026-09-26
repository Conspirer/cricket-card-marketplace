import secrets
from decimal import Decimal

# Ordered lowest to highest; used for "nearest available rarity" fallback.
RARITIES = ["Common", "Rare", "Epic", "Legendary"]

# Odds are relative weights per card slot, not per pack.
PACK_TYPES = {
    "standard": {
        "price": Decimal("100.00"),
        "cards": 3,
        "odds": {"Common": 70, "Rare": 22, "Epic": 7, "Legendary": 1},
    },
    "premium": {
        "price": Decimal("300.00"),
        "cards": 3,
        "odds": {"Common": 40, "Rare": 35, "Epic": 18, "Legendary": 7},
    },
}

# OS-backed CSPRNG: pack outcomes must not be predictable (unlike random.random).
_rng = secrets.SystemRandom()


def roll_rarity(odds):
    rarities = list(odds.keys())
    weights = list(odds.values())
    return _rng.choices(rarities, weights=weights, k=1)[0]


def fallback_order(rarity):
    # Nearest rarity first, preferring the lower one on ties:
    # Epic -> Epic, Rare, Legendary, Common
    index = RARITIES.index(rarity)
    return sorted(
        RARITIES,
        key=lambda r: (abs(RARITIES.index(r) - index), RARITIES.index(r) > index),
    )


def pick_definitions(pack_type, available):
    """Roll every slot of a pack against the currently available supply.

    `available` maps definition_id -> {"rarity": str, "remaining": int} and is
    decremented locally so one pack never picks more copies than exist.
    Returns a list of definition ids, or None if the whole catalogue is sold out.
    """
    config = PACK_TYPES[pack_type]
    picks = []

    for _ in range(config["cards"]):
        rolled = roll_rarity(config["odds"])

        chosen = None
        for rarity in fallback_order(rolled):
            pool = [
                definition_id
                for definition_id, definition in available.items()
                if definition["rarity"] == rarity and definition["remaining"] > 0
            ]
            if pool:
                chosen = _rng.choice(pool)
                break

        if chosen is None:
            return None

        available[chosen]["remaining"] -= 1
        picks.append(chosen)

    return picks
