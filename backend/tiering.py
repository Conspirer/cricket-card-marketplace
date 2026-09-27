"""Card tiers from whole careers. Pure functions, no DB.

Career score = sum over formats of FORMAT_WEIGHT[f] * (batting_f + bowling_f), where

    batting_f = runs / RUNS_REF[f]     * quality(batting average / BAT_AVG_REF[f])
    bowling_f = wickets / WKTS_REF[f]  * quality(BOWL_QUALITY_REF[f] / bowler's figure)

The bowler's figure is the bowling average in Tests and the economy rate in
the limited-overs formats (those are the verified stats each theme carries).
quality() clamps the ratio to [QUALITY_MIN, QUALITY_MAX], so volume (a long
career) drives the score and quality adjusts it by up to +/-50%. A missing
rate stat (below the minimum sample) counts as average quality (1.0).
Each *_REF is roughly "a very good career" in that format, so 5,000 Test runs
and 200 Test wickets are worth about the same. Tests and ODIs weigh at least
as much as T20Is; IPL counts least. Only verified theme stats are used.

No per-role percentile: an all-time great batter outranks an average bowler.
"""

FORMATS = ["TEST", "ODI", "T20I", "IPL"]

FORMAT_WEIGHT = {"TEST": 1.0, "ODI": 1.0, "T20I": 0.6, "IPL": 0.4}

RUNS_REF = {"TEST": 5000, "ODI": 5000, "T20I": 2000, "IPL": 3000}
BAT_AVG_REF = {"TEST": 40.0, "ODI": 35.0, "T20I": 28.0, "IPL": 28.0}

WKTS_REF = {"TEST": 200, "ODI": 200, "T20I": 80, "IPL": 100}
# Lower is better for both, so quality = reference / actual.
BOWL_QUALITY_STAT = {"TEST": "bowling_average", "ODI": "economy", "T20I": "economy", "IPL": "economy"}
BOWL_QUALITY_REF = {"TEST": 30.0, "ODI": 5.0, "T20I": 7.5, "IPL": 8.0}

QUALITY_MIN, QUALITY_MAX = 0.5, 1.5

RARITIES = ["Common", "Rare", "Epic", "Legendary"]  # low to high
# Tier sizes: how many players hold each rarity as their top tier. Everyone in
# the pool gets a Common card; the best also get Rare, Epic and Legendary.
LEGENDARY_SIZE = 8
EPIC_SIZE = 12       # 20 players at Epic or above
RARE_SIZE = 20       # 40 players at Rare or above
TIER_SIZES = {"Legendary": LEGENDARY_SIZE, "Epic": EPIC_SIZE, "Rare": RARE_SIZE}


def _quality(ratio):
    return max(QUALITY_MIN, min(QUALITY_MAX, ratio))


def format_score(fmt, stats):
    """(batting, bowling) contribution for one format's verified stats."""
    stats = stats or {}
    runs = stats.get("runs") or 0
    wickets = stats.get("wickets") or 0
    avg = stats.get("batting_average")
    batting = runs / RUNS_REF[fmt] * (_quality(avg / BAT_AVG_REF[fmt]) if avg else 1.0)
    figure = stats.get(BOWL_QUALITY_STAT[fmt])
    bowling = wickets / WKTS_REF[fmt] * (_quality(BOWL_QUALITY_REF[fmt] / figure) if figure else 1.0)
    return batting, bowling


def career_score(theme_stats):
    """theme_stats: {theme: stats dict} of verified rows only."""
    total = 0.0
    for fmt in FORMATS:
        batting, bowling = format_score(fmt, theme_stats.get(fmt))
        total += FORMAT_WEIGHT[fmt] * (batting + bowling)
    return round(total, 3)


def rarity_for_rank(rank):
    """0-based rank in the pool -> top rarity."""
    if rank < LEGENDARY_SIZE:
        return "Legendary"
    if rank < LEGENDARY_SIZE + EPIC_SIZE:
        return "Epic"
    if rank < LEGENDARY_SIZE + EPIC_SIZE + RARE_SIZE:
        return "Rare"
    return "Common"


def assign_tiers(scores, overrides=None):
    """scores: {player_id: score}. overrides: {player_id: rarity}.

    Returns ({player_id: final rarity}, {player_id: formula rarity}, [warnings]).
    The formula ranks everyone; overrides then replace individual results and
    always win. A warning is raised when the final count of a tier exceeds its size.
    """
    overrides = overrides or {}
    ranked = sorted(scores, key=lambda pid: (-scores[pid], pid))
    formula = {pid: rarity_for_rank(i) for i, pid in enumerate(ranked)}
    final = {**formula, **{pid: r for pid, r in overrides.items() if pid in scores}}
    warnings = []
    for rarity, size in TIER_SIZES.items():
        n = sum(1 for r in final.values() if r == rarity)
        if n > size:
            warnings.append(f"{rarity}: {n} players, over the tier size of {size} (overrides push it past)")
    return final, formula, warnings
