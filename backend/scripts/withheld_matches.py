"""Check the hand-entered scorecards of matches Cricsheet withholds.

    python -m backend.scripts.withheld_matches          # check the files
    python -m backend.scripts.withheld_matches --db     # also check player ids against the database

Cricsheet leaves out every match involving Afghanistan, so a player who played
one can't be verified from Cricsheet alone. India's June 2026 tour (one Test,
three ODIs) is the gap behind Gill, Jaiswal, Pant, Siraj, KL Rahul and others.
These files hold those matches' figures, entered from the official scorecards:

data/withheld_matches/matches.csv (filled in)
    match_key, format (TEST/ODI/T20I), date, team, opponent, venue, cricinfo_match_id

data/withheld_matches/performances.csv (one row per player per match, for
everyone in the team's XI, including anyone who didn't bat or bowl)
    match_key        from matches.csv
    player_id        Crease player id (see data/rarity_review.csv)
    player           name, for readability only
    innings_batted   innings they batted in this match (0, 1, or 2 in a Test)
    runs             runs scored, all innings
    balls_faced      balls faced, all innings
    dismissals       times out (not "not out", not "retired hurt")
    fours, sixes     boundaries hit
    hundreds         innings of 100 or more
    balls_bowled     legal deliveries bowled (overs x 6 + balls)
    runs_conceded    runs conceded bowling
    wickets          wickets taken
    catches          catches (including as keeper and caught-and-bowled)
    stumpings        stumpings
    note             optional

Totals only: these are all the figures a player's format stats are built
from (averages, economy and strike rate are derived). Nothing reads these
files into the stats yet; once they're complete, the plan is to add each
match to the player's Cricsheet record in date order and re-run the usual
cross-check against the Wikipedia infobox, so a row is verified only if the
two then agree.
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "data" / "withheld_matches"
MATCHES_FILE = DIR / "matches.csv"
PERFORMANCES_FILE = DIR / "performances.csv"

FORMATS = {"TEST", "ODI", "T20I"}
MAX_INNINGS = {"TEST": 2, "ODI": 1, "T20I": 1}
COUNTS = ["innings_batted", "runs", "balls_faced", "dismissals", "fours", "sixes", "hundreds",
          "balls_bowled", "runs_conceded", "wickets", "catches", "stumpings"]
MAX_XI = 12  # an XI plus one concussion replacement


def load(matches_path=MATCHES_FILE, performances_path=PERFORMANCES_FILE):
    with open(matches_path, newline="") as f:
        matches = {r["match_key"]: r for r in csv.DictReader(f)}
    with open(performances_path, newline="") as f:
        rows = list(csv.DictReader(f))
    return matches, rows


def check(matches, rows):
    """Returns (errors, warnings), each a list of strings naming the line."""
    errors, warnings = [], []
    for key, m in matches.items():
        if m["format"] not in FORMATS:
            errors.append(f"matches.csv {key}: format must be one of {sorted(FORMATS)}")
    seen = Counter()
    per_match = Counter()
    for line, r in enumerate(rows, start=2):
        where = f"performances.csv:{line} ({r.get('player') or r.get('player_id')})"
        match = matches.get(r.get("match_key", ""))
        if match is None:
            errors.append(f"{where}: unknown match_key {r.get('match_key')!r}")
            continue
        try:
            pid = int(r["player_id"])
            v = {k: int(r[k] or 0) for k in COUNTS}
        except (TypeError, ValueError):
            errors.append(f"{where}: player_id and every count must be whole numbers")
            continue
        if any(x < 0 for x in v.values()):
            errors.append(f"{where}: negative count")
        seen[(r["match_key"], pid)] += 1
        per_match[r["match_key"]] += 1
        fmt = match["format"]
        if v["innings_batted"] > MAX_INNINGS[fmt]:
            errors.append(f"{where}: {v['innings_batted']} innings is too many for a {fmt}")
        if v["dismissals"] > v["innings_batted"] or v["hundreds"] > v["innings_batted"]:
            errors.append(f"{where}: more dismissals or hundreds than innings batted")
        if v["runs"] < 100 * v["hundreds"]:
            errors.append(f"{where}: {v['hundreds']} hundreds but only {v['runs']} runs")
        if v["innings_batted"] == 0 and (v["runs"] or v["balls_faced"]):
            errors.append(f"{where}: runs or balls faced without batting")
        if 4 * v["fours"] + 6 * v["sixes"] > v["runs"]:
            errors.append(f"{where}: boundaries add up to more than the runs")
        if v["balls_bowled"] == 0 and (v["runs_conceded"] or v["wickets"]):
            errors.append(f"{where}: runs conceded or wickets without bowling")
        if v["wickets"] > 10 * MAX_INNINGS[fmt]:
            errors.append(f"{where}: too many wickets")
    for (key, pid), n in seen.items():
        if n > 1:
            errors.append(f"player {pid} appears {n} times in {key}")
    for key in matches:
        n = per_match[key]
        if n == 0:
            warnings.append(f"{key}: no players entered yet")
        elif n < 11:
            warnings.append(f"{key}: only {n} players (the whole XI is needed, including anyone who didn't bat or bowl)")
        elif n > MAX_XI:
            errors.append(f"{key}: {n} players, more than an XI plus a replacement")
    return errors, warnings


def check_players(rows):
    from backend.database import get_connection

    ids = {int(r["player_id"]) for r in rows if (r.get("player_id") or "").isdigit()}
    with get_connection() as connection:
        found = {r["id"]: r for r in connection.execute(
            "SELECT id, name, country FROM players WHERE id = ANY(%s);", (sorted(ids),)).fetchall()}
    problems = [f"player {pid}: not in the database" for pid in sorted(ids - set(found))]
    problems += [f"player {pid} ({p['name']}): country is {p['country']}, not India"
                 for pid, p in found.items() if p["country"] != "India"]
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", action="store_true", help="also check player ids against the database")
    args = parser.parse_args()
    matches, rows = load()
    errors, warnings = check(matches, rows)
    if args.db:
        errors += check_players(rows)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    print(f"{len(rows)} performances across {len(matches)} matches: "
          f"{'OK' if not errors else f'{len(errors)} errors'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
