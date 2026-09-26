"""Player ratings (0-99) derived from career stats. Pure functions, no DB.

Internal only: used to choose the card pool and player tiers, never shown.

Each rating is a percentile within its format: a player's value is ranked
against the *qualified* Full Member specialists for that skill in that format
(batting skills against batters and all-rounders, bowling skills against
bowlers and all-rounders, fielding against everyone). Everyone is rated, but
against that one reference: most T20Is since 2019 involve associate nations,
whose low-scoring games would otherwise make every Full Member batter look
elite and every Full Member bowler look ordinary.

Players with a small sample are pulled toward the median, harder the further
short they are, so a debutant with one big innings can't rate 99. A player
with no sample at all for a skill (a batter who has never bowled) gets None.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict

FORMATS = ["T20", "ODI", "Test"]

# ICC Full Members: the reference group for percentiles, and the card pool.
FULL_MEMBERS = {
    "Afghanistan", "Australia", "Bangladesh", "England", "India", "Ireland",
    "New Zealand", "Pakistan", "South Africa", "Sri Lanka", "West Indies", "Zimbabwe",
}
SKILLS = ["technique", "power", "threat", "control", "fielding"]

# Which roles form the reference distribution for each skill.
REFERENCE_ROLES = {
    "technique": {"Batter", "All-rounder"},
    "power": {"Batter", "All-rounder"},
    "threat": {"Bowler", "All-rounder"},
    "control": {"Bowler", "All-rounder"},
    "fielding": {"Batter", "Bowler", "All-rounder"},
}

# Sample needed before a rating is taken at face value.
MIN_BATTING_INNINGS = 10
MIN_BALLS_BOWLED = 300
MIN_FIELDING_MATCHES = 10

# Overall rating weights. Fielding is a small bonus for everyone; all-rounders
# are weighted toward their stronger discipline.
FIELDING_WEIGHT = 0.1
ALLROUNDER_STRONG_WEIGHT = 0.6


def _skill_inputs(stats):
    """(value, sample_size, higher_is_better) for each skill's components."""
    innings = stats.get("innings", 0)
    balls_faced = stats.get("balls_faced", 0)
    balls_bowled = stats.get("balls_bowled", 0)
    matches = stats.get("matches", 0)

    average = stats["runs"] / max(stats.get("dismissals", 0), 1) if innings else None
    strike_rate = stats["runs"] / balls_faced * 100 if balls_faced else None
    boundary_pct = stats["boundaries"] / balls_faced if balls_faced else None
    wickets_per_ball = stats["wickets"] / balls_bowled if balls_bowled else None
    economy = stats["runs_conceded"] / balls_bowled * 6 if balls_bowled else None
    dot_pct = stats["dot_balls"] / balls_bowled if balls_bowled else None
    catches_per_match = stats["catches"] / matches if matches else None

    return {
        "technique": ([(average, True)], innings, MIN_BATTING_INNINGS),
        "power": ([(strike_rate, True), (boundary_pct, True)], innings, MIN_BATTING_INNINGS),
        "threat": ([(wickets_per_ball, True)], balls_bowled, MIN_BALLS_BOWLED),
        "control": ([(economy, False), (dot_pct, True)], balls_bowled, MIN_BALLS_BOWLED),
        "fielding": ([(catches_per_match, True)], matches, MIN_FIELDING_MATCHES),
    }


def _percentile(sorted_values, x):
    """Fraction of values below x, counting ties as half. 0.5 if no reference."""
    if not sorted_values:
        return 0.5
    below = bisect_left(sorted_values, x)
    at_or_below = bisect_right(sorted_values, x)
    return (below + at_or_below) / 2 / len(sorted_values)


def compute_ratings(stats_by_player, roles, countries):
    """stats_by_player: {pid: {format: stats}} -> {pid: {format: ratings}}."""
    ratings = {pid: {} for pid in stats_by_player}

    for fmt in FORMATS:
        inputs = {
            pid: _skill_inputs(formats[fmt])
            for pid, formats in stats_by_player.items()
            if fmt in formats
        }

        # Reference distributions come from qualified players only.
        reference = {}
        for skill in SKILLS:
            columns = None
            for pid, inp in inputs.items():
                components, sample, minimum = inp[skill]
                if columns is None:
                    columns = [[] for _ in components]
                if (
                    sample >= minimum
                    and roles.get(pid) in REFERENCE_ROLES[skill]
                    and countries.get(pid) in FULL_MEMBERS
                ):
                    for i, (value, _) in enumerate(components):
                        if value is not None:
                            columns[i].append(value)
            reference[skill] = [sorted(c) for c in (columns or [])]

        for pid, inp in inputs.items():
            skill_ratings = {}
            for skill in SKILLS:
                components, sample, minimum = inp[skill]
                values = [(v, better) for v, better in components if v is not None]
                if sample == 0 or not values:
                    skill_ratings[skill] = None
                    continue

                percentiles = []
                for i, (value, higher_is_better) in enumerate(components):
                    if value is None:
                        continue
                    p = _percentile(reference[skill][i], value)
                    percentiles.append(p if higher_is_better else 1 - p)
                p = sum(percentiles) / len(percentiles)

                # Shrink toward the median (0.5) when the sample is short. The
                # squared ramp bites hard well below the minimum and fades out
                # as the sample approaches it; qualified players are untouched.
                weight = min(1.0, sample / minimum) ** 2
                p = 0.5 + weight * (p - 0.5)
                skill_ratings[skill] = round(p * 99)

            ratings[pid][fmt] = skill_ratings

        _add_overall(ratings, inputs, roles, countries, fmt)

    return ratings


def _add_overall(ratings, inputs, roles, countries, fmt):
    """Overall = percentile of the role's core skill score within that role.

    Raw cores aren't comparable across roles: elite bowlers are rarely elite at
    both wicket-taking and economy, so averaging the two caps them far below
    the best batters. Ranking within the role makes 99 mean "best in role".
    """
    def qualified(pid):
        batting = inputs[pid]["technique"][1] >= MIN_BATTING_INNINGS
        bowling = inputs[pid]["threat"][1] >= MIN_BALLS_BOWLED
        role = roles.get(pid)
        return {"Batter": batting, "Bowler": bowling}.get(role, batting and bowling)

    cores = {pid: core_score(ratings[pid][fmt], roles.get(pid)) for pid in inputs}
    reference = defaultdict(list)
    for pid, core in cores.items():
        if countries.get(pid) in FULL_MEMBERS and qualified(pid):
            reference[roles.get(pid)].append(core)
    for values in reference.values():
        values.sort()

    for pid, core in cores.items():
        p = _percentile(reference[roles.get(pid)], core)
        ratings[pid][fmt]["overall"] = round(p * 99)


def _mean(*values):
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else 0


def core_score(r, role):
    """The role's headline skill score, before ranking within the role.

    Fielding is folded in here (not added after ranking) so bowlers, who take
    fewer catches in their fielding positions, are compared with other bowlers.
    """
    batting = _mean(r["technique"], r["power"])
    bowling = _mean(r["threat"], r["control"])
    if role == "Batter":
        skill = batting
    elif role == "Bowler":
        skill = bowling
    else:
        strong, weak = max(batting, bowling), min(batting, bowling)
        skill = ALLROUNDER_STRONG_WEIGHT * strong + (1 - ALLROUNDER_STRONG_WEIGHT) * weak
    return (1 - FIELDING_WEIGHT) * skill + FIELDING_WEIGHT * (r["fielding"] or 0)
