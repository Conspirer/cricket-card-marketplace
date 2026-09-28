"""Card tiers from whole careers. Pure functions, no DB.

Each format gets a score from its verified stats:

    format_f  = FORMAT_WEIGHT[f] * (batting_f + bowling_f)
    batting_f = (runs / RUNS_REF[f])^VOLUME_EXPONENT    * quality(batting average / BAT_AVG_REF[f])^QUALITY_EXPONENT
    bowling_f = (wickets / WKTS_REF[f])^VOLUME_EXPONENT * quality(BOWL_QUALITY_REF[f] / bowler's figure)^QUALITY_EXPONENT

With the square root of volume and quality squared, a long career still
counts but with diminishing returns, and how good a player was counts for
more: a great short career (Bumrah) isn't outranked by a long ordinary one.

and the career score blends them, best first:

    score = BLEND[0] * best + BLEND[1] * second best + BLEND[2] * third best

Scoring on the best format (not the sum) means a great Test career isn't
outranked by a lesser player who simply played more formats: older players
had no T20Is or IPL, and specialists rarely play every format. The best
format needs at least MIN_BEST_FORMAT_MATCHES, so a short hot streak can't
rank high; a smaller sample can still add as a second or third format.

Missing data never counts as zero: a format with no verified row is left out
of the blend entirely (the player is scored on the formats that are verified),
and a missing rate stat (below its minimum sample) counts as average quality.

The bowler's figure is the bowling average in Tests and the economy rate in
the limited-overs formats (those are the verified stats each theme carries).
quality() clamps the ratio to [QUALITY_MIN, QUALITY_MAX] before it's squared.
Each *_REF is roughly "a very good career" in that format, so 5,000 Test runs
and 200 Test wickets are worth about the same. No per-role percentile: an
all-time great batter outranks an average bowler.

Legends are a separate edition, like FIFA's Icons: a player whose last
international match was before LEGEND_CUTOFF (plus the pre-Cricsheet greats in
data/legends.json). They're ranked only against each other and never take a
current player's tier slot: the top LEGEND_LEGENDARY_SIZE are Legendary, the
next LEGEND_EPIC_SIZE Epic and the rest Rare; a legend is never below
LEGEND_FLOOR. Current players fill TIER_SIZES on their own.
"""

from datetime import date

FORMATS = ["TEST", "ODI", "T20I", "IPL"]

# Tests and ODIs count in full. T20I and IPL careers count a little less, but
# not so little that a T20 specialist is capped far below everyone else.
FORMAT_WEIGHT = {"TEST": 1.0, "ODI": 1.0, "T20I": 0.8, "IPL": 0.5}

RUNS_REF = {"TEST": 5000, "ODI": 5000, "T20I": 2000, "IPL": 3000}
BAT_AVG_REF = {"TEST": 40.0, "ODI": 35.0, "T20I": 28.0, "IPL": 28.0}

WKTS_REF = {"TEST": 200, "ODI": 200, "T20I": 80, "IPL": 100}
# Lower is better for both, so quality = reference / actual.
BOWL_QUALITY_STAT = {"TEST": "bowling_average", "ODI": "economy", "T20I": "economy", "IPL": "economy"}
BOWL_QUALITY_REF = {"TEST": 30.0, "ODI": 5.0, "T20I": 7.5, "IPL": 8.0}

QUALITY_MIN, QUALITY_MAX = 0.5, 1.5
VOLUME_EXPONENT = 0.5    # square root: twice the runs is worth ~1.4x, not 2x
QUALITY_EXPONENT = 2.0   # quality squared: a 1.5x average is worth 2.25x

# Best format in full, then the next two at a fraction.
BLEND_BEST, BLEND_SECOND, BLEND_THIRD = 1.0, 0.3, 0.1
MIN_BEST_FORMAT_MATCHES = {"TEST": 20, "ODI": 40, "T20I": 40, "IPL": 50}

RARITIES = ["Common", "Rare", "Epic", "Legendary"]  # low to high
# Tier sizes for current players: how many hold each rarity as their top
# tier. Every current player gets a Common card; the best also get Rare, Epic
# and Legendary. Sized for the POOL_SIZE current players.
LEGENDARY_SIZE = 10
EPIC_SIZE = 20       # 30 players at Epic or above
RARE_SIZE = 40       # 70 players at Rare or above
TIER_SIZES = {"Legendary": LEGENDARY_SIZE, "Epic": EPIC_SIZE, "Rare": RARE_SIZE}

# Current players in the pool; legends are their own edition on top of this.
POOL_SIZE = 120

# Legends: last international match before the cutoff. About LEGEND_POOL_SIZE
# of them (everyone in data/legends.json, then the best of the rest). The top
# LEGEND_LEGENDARY_SIZE are Legendary, the next LEGEND_EPIC_SIZE Epic, the
# rest Rare; their cards start at LEGEND_FLOOR (no Common legend cards).
LEGEND_CUTOFF = date(2024, 1, 1)
LEGEND_POOL_SIZE = 50
LEGEND_LEGENDARY_SIZE = 14
LEGEND_EPIC_SIZE = 18
LEGEND_FLOOR = "Rare"


def _quality(ratio):
    return max(QUALITY_MIN, min(QUALITY_MAX, ratio))


def format_parts(fmt, stats):
    """(batting, bowling) for one format's verified stats, before the format weight."""
    stats = stats or {}
    runs = stats.get("runs") or 0
    wickets = stats.get("wickets") or 0
    avg = stats.get("batting_average")
    bat_quality = _quality(avg / BAT_AVG_REF[fmt]) if avg else 1.0
    batting = (runs / RUNS_REF[fmt]) ** VOLUME_EXPONENT * bat_quality ** QUALITY_EXPONENT if runs > 0 else 0.0
    figure = stats.get(BOWL_QUALITY_STAT[fmt])
    bowl_quality = _quality(BOWL_QUALITY_REF[fmt] / figure) if figure else 1.0
    bowling = (wickets / WKTS_REF[fmt]) ** VOLUME_EXPONENT * bowl_quality ** QUALITY_EXPONENT if wickets > 0 else 0.0
    return batting, bowling


def format_score(fmt, stats):
    batting, bowling = format_parts(fmt, stats)
    return FORMAT_WEIGHT[fmt] * (batting + bowling)


def career(formats):
    """formats: {fmt: {"stats": {...}, "matches": n}}, verified rows only.

    Returns {"score", "best", "parts"}: parts is each played format's score,
    best the format the blend starts from, and score None when no format has
    enough matches to be anyone's best (the player can't be ranked).
    """
    parts = {
        f: round(format_score(f, row["stats"]), 3)
        for f, row in formats.items()
        if f in FORMATS and (row.get("matches") or 0) > 0
    }
    eligible = [f for f in parts if formats[f]["matches"] >= MIN_BEST_FORMAT_MATCHES[f]]
    if not eligible:
        return {"score": None, "best": None, "parts": parts}
    best = max(eligible, key=lambda f: (parts[f], -FORMATS.index(f)))
    rest = sorted((parts[f] for f in parts if f != best), reverse=True)
    score = BLEND_BEST * parts[best] + sum(w * s for w, s in zip((BLEND_SECOND, BLEND_THIRD), rest))
    return {"score": round(score, 3), "best": best, "parts": parts}


def career_score(formats):
    return career(formats)["score"]


def rarity_for_rank(rank):
    """0-based rank among current players -> top rarity."""
    if rank < LEGENDARY_SIZE:
        return "Legendary"
    if rank < LEGENDARY_SIZE + EPIC_SIZE:
        return "Epic"
    if rank < LEGENDARY_SIZE + EPIC_SIZE + RARE_SIZE:
        return "Rare"
    return "Common"


def legend_rarity_for_rank(rank):
    """0-based rank among legends -> top rarity."""
    if rank < LEGEND_LEGENDARY_SIZE:
        return "Legendary"
    if rank < LEGEND_LEGENDARY_SIZE + LEGEND_EPIC_SIZE:
        return "Epic"
    return LEGEND_FLOOR


def is_legend(last_international, cutoff=LEGEND_CUTOFF):
    """Retired by the cutoff: last international match before it. An unknown
    date isn't enough to call someone a legend (they stay current)."""
    return last_international is not None and last_international < cutoff


def _fill(ranked, pinned, sizes, bottom):
    """Tiers for one edition: pinned players (overrides) take a slot in their
    tier; the rest fill the remaining slots best-first, top tier down."""
    taken = {r: sum(1 for x in pinned.values() if x == r) for r in sizes}
    out, tiers = dict(pinned), list(sizes)
    for pid in (p for p in ranked if p not in pinned):
        while tiers and taken[tiers[0]] >= sizes[tiers[0]]:
            tiers.pop(0)
        rarity = tiers[0] if tiers else bottom
        if tiers:
            taken[rarity] += 1
        out[pid] = rarity
    return out


def assign_tiers(scores, overrides=None, legends=frozenset()):
    """scores: {player_id: score}. overrides: {player_id: rarity}. legends:
    the ids in scores that are legends (ranked apart from everyone else).

    Returns ({player_id: final rarity}, {player_id: formula rarity}, [warnings]).
    formula is the plain ranking. In final, overrides always win and take a
    slot in their tier; everyone else fills the remaining slots in score
    order, so the tier sizes still hold (demoting a player promotes the next
    one). A legend is never put below LEGEND_FLOOR. Warnings: a legend
    override below the floor, or overrides alone filling a tier past its size.
    """
    overrides = overrides or {}
    legends = set(legends) & set(scores)
    ranked = lambda group: sorted(group, key=lambda pid: (-scores[pid], pid))
    current_ranked, legend_ranked = ranked(set(scores) - legends), ranked(legends)
    formula = {pid: rarity_for_rank(i) for i, pid in enumerate(current_ranked)}
    formula.update({pid: legend_rarity_for_rank(i) for i, pid in enumerate(legend_ranked)})

    warnings = []
    pinned = {"current": {}, "legend": {}}
    floor = RARITIES.index(LEGEND_FLOOR)
    for pid, rarity in overrides.items():
        if pid not in scores:
            continue
        if pid in legends and RARITIES.index(rarity) < floor:
            warnings.append(f"player {pid}: legends are never below {LEGEND_FLOOR}; override {rarity} ignored")
            rarity = LEGEND_FLOOR
        pinned["legend" if pid in legends else "current"][pid] = rarity

    current_sizes = {"Legendary": LEGENDARY_SIZE, "Epic": EPIC_SIZE, "Rare": RARE_SIZE}
    legend_sizes = {"Legendary": LEGEND_LEGENDARY_SIZE, "Epic": LEGEND_EPIC_SIZE}
    final = _fill(current_ranked, pinned["current"], current_sizes, "Common")
    final.update(_fill(legend_ranked, pinned["legend"], legend_sizes, LEGEND_FLOOR))

    for edition, sizes in (("current", current_sizes), ("legend", legend_sizes)):
        for rarity, size in sizes.items():
            n = sum(1 for r in pinned[edition].values() if r == rarity)
            if n > size:
                warnings.append(f"{rarity}: overrides alone put {n} {edition} players there, over the size of {size}")
    return final, formula, warnings
