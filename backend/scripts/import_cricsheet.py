"""Import real players from Cricsheet: career stats, country, role, ratings.

    python -m backend.scripts.import_cricsheet            # uses cached downloads
    python -m backend.scripts.import_cricsheet --refresh  # re-download first

Source: men's international Tests, ODIs and T20Is (ball-by-ball JSON) plus the
Cricsheet people register. Idempotent: players are upserted on cricsheet_id,
so re-running refreshes stats and ratings in place. Name, batting hand,
bowling type and keeper status are never overwritten once set (they may have
been edited by hand).
"""

import argparse
import csv
import json
import re
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from psycopg.types.json import Jsonb

from backend.database import get_connection
from backend.ratings import FORMATS, compute_ratings

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "cricsheet"
DOWNLOADS = "https://cricsheet.org/downloads/"
REGISTER = "https://cricsheet.org/register/"
MATCH_FILES = {"Test": "tests_male_json.zip", "ODI": "odis_male_json.zip", "T20": "t20s_male_json.zip"}
REGISTER_FILES = ["people.csv", "names.csv"]

BOWLER_WICKETS = {"bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket"}
NOT_DISMISSALS = {"retired hurt", "retired not out"}

# Role from share of balls bowled among balls faced + bowled, across formats.
BATTER_BELOW = 0.25
BOWLER_ABOVE = 0.80

COUNTERS = [
    "matches", "innings", "runs", "balls_faced", "dismissals", "fours", "sixes",
    "highest", "fifties", "hundreds",
    "balls_bowled", "runs_conceded", "wickets", "dot_balls",
    "catches", "stumpings",
]


def download(refresh):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, base in [(f, DOWNLOADS) for f in MATCH_FILES.values()] + [(f, REGISTER) for f in REGISTER_FILES]:
        path = DATA_DIR / name
        if refresh or not path.exists():
            print(f"  downloading {name}")
            urllib.request.urlretrieve(base + name, path)


def new_stats():
    stats = {key: 0 for key in COUNTERS}
    stats["first_match"] = None
    stats["last_match"] = None
    return stats


def process_match(match, fmt, stats, teams):
    info = match["info"]
    registry = info["registry"]["people"]
    date = info["dates"][0]

    def s(name):
        pid = registry.get(name)
        return stats[pid][fmt] if pid else None

    for team, names in info["players"].items():
        for name in names:
            pid = registry.get(name)
            if not pid:
                continue
            player = stats[pid][fmt]
            player["matches"] += 1
            if player["first_match"] is None or date < player["first_match"]:
                player["first_match"] = date
            if player["last_match"] is None or date > player["last_match"]:
                player["last_match"] = date
            teams[pid][team] += 1

    for innings in match.get("innings", []):
        if innings.get("super_over"):
            continue

        # Runs per batter this innings; anyone at the crease has batted.
        innings_runs = {}

        for over in innings.get("overs", []):
            for d in over["deliveries"]:
                batter, bowler = s(d["batter"]), s(d["bowler"])
                for name in (d["batter"], d["non_striker"]):
                    innings_runs.setdefault(name, 0)

                extras = d.get("extras", {})
                wide, no_ball = "wides" in extras, "noballs" in extras
                bat_runs = d["runs"]["batter"]
                non_boundary = d["runs"].get("non_boundary", False)

                if batter:
                    batter["runs"] += bat_runs
                    if not wide:
                        batter["balls_faced"] += 1
                    if not non_boundary and bat_runs == 4:
                        batter["fours"] += 1
                    if not non_boundary and bat_runs == 6:
                        batter["sixes"] += 1
                innings_runs[d["batter"]] += bat_runs

                if bowler:
                    # Byes and leg-byes aren't charged to the bowler.
                    conceded = bat_runs + extras.get("wides", 0) + extras.get("noballs", 0)
                    bowler["runs_conceded"] += conceded
                    if not wide and not no_ball:
                        bowler["balls_bowled"] += 1
                        if conceded == 0:
                            bowler["dot_balls"] += 1

                for wicket in d.get("wickets", []):
                    kind = wicket["kind"]
                    out = s(wicket["player_out"])
                    if out and kind not in NOT_DISMISSALS:
                        out["dismissals"] += 1
                    if bowler and kind in BOWLER_WICKETS:
                        bowler["wickets"] += 1
                    if kind == "caught and bowled" and bowler:
                        bowler["catches"] += 1
                    for fielder in wicket.get("fielders", []):
                        if fielder.get("substitute") or "name" not in fielder:
                            continue
                        f = s(fielder["name"])
                        if not f:
                            continue
                        if kind == "caught":
                            f["catches"] += 1
                        elif kind == "stumped":
                            f["stumpings"] += 1

        for name, runs in innings_runs.items():
            player = s(name)
            if not player:
                continue
            player["innings"] += 1
            player["highest"] = max(player["highest"], runs)
            if runs >= 100:
                player["hundreds"] += 1
            elif runs >= 50:
                player["fifties"] += 1


def with_derived(stats):
    """Add the display figures (averages, rates) to the raw counts."""
    def ratio(a, b, scale=1):
        return round(a / b * scale, 2) if b else None

    out = dict(stats)
    out["boundaries"] = stats["fours"] + stats["sixes"]
    out["batting_average"] = ratio(stats["runs"], stats["dismissals"])
    out["strike_rate"] = ratio(stats["runs"], stats["balls_faced"], 100)
    out["boundary_pct"] = ratio(out["boundaries"], stats["balls_faced"], 100)
    out["economy"] = ratio(stats["runs_conceded"], stats["balls_bowled"], 6)
    out["bowling_average"] = ratio(stats["runs_conceded"], stats["wickets"])
    out["bowling_strike_rate"] = ratio(stats["balls_bowled"], stats["wickets"])
    out["dot_pct"] = ratio(stats["dot_balls"], stats["balls_bowled"], 100)
    return out


def derive_role(formats):
    faced = sum(f["balls_faced"] for f in formats.values())
    bowled = sum(f["balls_bowled"] for f in formats.values())
    if faced + bowled == 0:
        return "Batter"
    share = bowled / (faced + bowled)
    if share < BATTER_BELOW:
        return "Batter"
    if share > BOWLER_ABOVE:
        return "Bowler"
    return "All-rounder"


INITIALS = re.compile(r"^[A-Z]{1,4}\.?$|\.")


def display_names():
    """Best human-readable name per Cricsheet id ("Virat Kohli" over "V Kohli")."""
    register = {}
    with open(DATA_DIR / "people.csv", newline="") as f:
        for row in csv.DictReader(f):
            register[row["identifier"]] = row["name"]

    variants = defaultdict(set)
    with open(DATA_DIR / "names.csv", newline="") as f:
        for row in csv.DictReader(f):
            variants[row["identifier"]].add(row["name"].strip())

    def looks_full(name):
        words = name.split()
        return len(words) >= 2 and not INITIALS.search(words[0])

    names = {}
    for pid, fallback in register.items():
        full = sorted(
            (n for n in variants[pid] | {fallback} if looks_full(n)),
            key=lambda n: (len(n.split()) != 2, len(n.split()), len(n)),
        )
        names[pid] = full[0] if full else fallback
    return names, variants, register


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download Cricsheet files")
    args = parser.parse_args()

    print("1/4 fetching Cricsheet data")
    download(args.refresh)

    print("2/4 parsing matches")
    stats = defaultdict(lambda: defaultdict(new_stats))
    teams = defaultdict(Counter)
    for fmt, filename in MATCH_FILES.items():
        with zipfile.ZipFile(DATA_DIR / filename) as archive:
            files = [n for n in archive.namelist() if n.endswith(".json")]
            for i, name in enumerate(files, 1):
                process_match(json.loads(archive.read(name)), fmt, stats, teams)
                if i % 500 == 0 or i == len(files):
                    print(f"  {fmt}: {i}/{len(files)} matches", end="\r")
        print()

    print("3/4 computing ratings")
    raw = {pid: {fmt: dict(s) for fmt, s in formats.items()} for pid, formats in stats.items()}
    for formats in raw.values():
        for s in formats.values():
            s["boundaries"] = s["fours"] + s["sixes"]
    roles = {pid: derive_role(formats) for pid, formats in raw.items()}
    countries = {pid: teams[pid].most_common(1)[0][0] for pid in raw if teams[pid]}
    ratings = compute_ratings(raw, roles, countries)
    names, variants, register = display_names()

    rows = []
    for pid, formats in raw.items():
        country = countries.get(pid, "Unknown")
        career = {fmt: with_derived(formats[fmt]) for fmt in FORMATS if fmt in formats}
        rows.append((pid, names.get(pid, register.get(pid, pid)), country, roles[pid], Jsonb(career), Jsonb(ratings[pid])))

    print(f"4/4 writing {len(rows)} players")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            link_existing_players(cursor, raw, variants, register)

            # Name/hand/bowling type/keeper are left alone on conflict: they may
            # have been set by hand, and the import has nothing better for them.
            cursor.executemany(
                """
                INSERT INTO players (cricsheet_id, name, country, role, career_stats, ratings)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (cricsheet_id) DO UPDATE SET
                    country = EXCLUDED.country,
                    role = EXCLUDED.role,
                    career_stats = EXCLUDED.career_stats,
                    ratings = EXCLUDED.ratings;
                """,
                rows,
            )

            cursor.execute("SELECT count(*) AS n FROM players WHERE cricsheet_id IS NOT NULL;")
            print(f"done: {cursor.fetchone()['n']} players with Cricsheet data")


def link_existing_players(cursor, raw, variants, register):
    """Attach cricsheet_id to hand-made players (the seed set) by exact name."""
    cursor.execute("SELECT id, name FROM players WHERE cricsheet_id IS NULL;")
    unlinked = cursor.fetchall()
    if not unlinked:
        return

    cursor.execute("SELECT cricsheet_id FROM players WHERE cricsheet_id IS NOT NULL;")
    taken = {row["cricsheet_id"] for row in cursor.fetchall()}

    by_name = defaultdict(set)
    for pid in raw:
        for name in variants[pid] | {register.get(pid, "")}:
            by_name[name.lower()].add(pid)

    def total_matches(pid):
        return sum(f["matches"] for f in raw[pid].values())

    for player in unlinked:
        candidates = [pid for pid in by_name.get(player["name"].lower(), ()) if pid not in taken]
        if not candidates:
            print(f"  no Cricsheet match for existing player {player['name']!r}; left unlinked")
            continue
        # Same-name players exist; the one with the longest international career wins.
        pid = max(candidates, key=total_matches)
        if len(candidates) > 1:
            print(f"  {player['name']!r}: {len(candidates)} candidates, picked {pid} ({total_matches(pid)} matches)")
        cursor.execute("UPDATE players SET cricsheet_id = %s WHERE id = %s;", (pid, player["id"]))
        taken.add(pid)
        print(f"  linked {player['name']!r} -> {pid}")


if __name__ == "__main__":
    sys.exit(main())
