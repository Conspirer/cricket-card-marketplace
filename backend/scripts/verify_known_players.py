"""Spot-check stored per-theme stats against independent records.

    python -m backend.scripts.verify_known_players

Two independent references (neither is the source the stats were built from):
  * IPL: Wikipedia's "List of Indian Premier League records and statistics"
    career leaderboards (compiled from ESPNcricinfo), compared with our
    Cricsheet ball-by-ball aggregates.
  * Formats: published career records of finished international careers
    (these figures no longer change), compared with the stored Wikipedia
    infobox values.
"""

import re
import zipfile
import json
from collections import defaultdict

import mwparserfromhell

from backend.database import get_connection
from backend.scripts.import_cricsheet import DATA_DIR, new_stats, process_match
from backend.scripts.wikipedia import fetch_wikitext

IPL_PAGE = "List of Indian Premier League records and statistics"
IPL_TABLES = {
    "Most career runs": "runs",
    "Most career wickets": "wickets",
    "Most career sixes": "sixes",
    "Most career fours": "fours",
}

# Finished careers: (player, theme, stat) -> published figure.
FINISHED_CAREERS = {
    ("Virat Kohli", "TEST"): {"matches": 123, "runs": 9230, "batting_average": 46.85, "hundreds": 30},
    ("Virat Kohli", "T20I"): {"matches": 125, "runs": 4188, "batting_average": 48.69},
    ("Rohit Sharma", "TEST"): {"matches": 67, "runs": 4301, "batting_average": 40.57, "hundreds": 12},
    ("Rohit Sharma", "T20I"): {"matches": 159, "runs": 4231, "batting_average": 32.05},
    ("MS Dhoni", "TEST"): {"matches": 90, "runs": 4876, "batting_average": 38.09, "hundreds": 6},
    ("MS Dhoni", "ODI"): {"matches": 350, "runs": 10773, "batting_average": 50.57, "hundreds": 10},
    ("MS Dhoni", "T20I"): {"matches": 98, "runs": 1617, "batting_average": 37.60},
    ("Rahul Dravid", "TEST"): {"matches": 164, "runs": 13288, "batting_average": 52.31, "hundreds": 36},
    ("Rahul Dravid", "ODI"): {"matches": 344, "runs": 10889, "batting_average": 39.16, "hundreds": 12},
    ("David Warner", "TEST"): {"matches": 112, "runs": 8786, "batting_average": 44.59, "hundreds": 26},
    ("David Warner", "ODI"): {"matches": 161, "runs": 6932, "batting_average": 45.30, "hundreds": 22},
    ("David Warner", "T20I"): {"matches": 110, "runs": 3277, "batting_average": 33.43},
}


def leaderboards():
    text = fetch_wikitext([IPL_PAGE])[IPL_PAGE] or ""
    boards = {}
    for heading, stat in IPL_TABLES.items():
        start = text.find(f"==={heading}===")
        if start < 0:
            continue
        section = text[start:text.find("\n===", start + 5)]
        rows = []
        for chunk in section.split("\n|-")[1:]:
            value = re.search(r"\|\s*(\d[\d,]*)\s*\n", "\n" + chunk.strip() + "\n")
            names = [t for t in mwparserfromhell.parse(chunk).filter_templates() if t.name.strip() == "sortname"]
            if names:
                t = names[0]
                name = f"{t.get(1).value.strip()} {t.get(2).value.strip()}"
            else:
                link = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", chunk)
                name = (link.group(2) or link.group(1)) if link else None
            number = re.search(r"!\s*(?:scope=row[^|]*\|\s*)?(\d[\d,]*)", chunk)
            if name and number:
                rows.append((name.strip(), int(number.group(1).replace(",", ""))))
        boards[stat] = rows
    return boards


def ipl_counts():
    stats = defaultdict(lambda: defaultdict(new_stats))
    with zipfile.ZipFile(DATA_DIR / "ipl_json.zip") as z:
        for name in z.namelist():
            if name.endswith(".json"):
                process_match(json.loads(z.read(name)), "IPL", stats, defaultdict(lambda: defaultdict(int)))
    return stats


def main():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, name, cricsheet_id FROM players WHERE cricsheet_id IS NOT NULL;")
            by_name = defaultdict(list)
            for p in cursor.fetchall():
                by_name[p["name"]].append(p)

            print(f"IPL career leaderboards vs Cricsheet ({IPL_PAGE}):")
            counts = ipl_counts()
            ok = bad = 0
            for stat, rows in leaderboards().items():
                for name, published in rows:
                    candidates = [p for p in by_name.get(name, []) if p["cricsheet_id"] in counts]
                    if not candidates:
                        print(f"  ?  {stat:<8} {name}: not found in players (name differs)")
                        continue
                    c = counts[candidates[0]["cricsheet_id"]]["IPL"]
                    ours = c["fours"] if stat == "fours" else c[stat]
                    mark = "ok" if ours == published else "MISMATCH"
                    ok, bad = (ok + 1, bad) if ours == published else (ok, bad + 1)
                    print(f"  {mark:<8} {stat:<8} {name:<22} published {published:>5}  ours {ours:>5}")
            print(f"  -> {ok} match, {bad} mismatch")

            print("\nFinished international careers vs stored theme stats:")
            ok = bad = 0
            for (name, theme), published in FINISHED_CAREERS.items():
                cursor.execute(
                    """
                    SELECT t.stats, t.matches, t.verified, t.notes FROM player_theme_stats t
                    JOIN players p ON p.id = t.player_id WHERE p.name = %s AND t.theme = %s;
                    """,
                    (name, theme),
                )
                row = cursor.fetchone()
                if not row:
                    print(f"  ?  {name} {theme}: no stored row")
                    continue
                stored = dict(row["stats"], matches=row["matches"])
                diffs = []
                for key, value in published.items():
                    ours = stored.get(key)
                    if ours is None and key == "hundreds":
                        continue  # not in this theme's stat set
                    if ours is None or abs(float(ours) - value) > 0.005:
                        diffs.append(f"{key} published {value} ours {ours}")
                status = "ok" if not diffs else "MISMATCH"
                ok, bad = (ok + 1, bad) if not diffs else (ok, bad + 1)
                flag = "" if row["verified"] else f"  [unverified: {row['notes']}]"
                print(f"  {status:<8} {name:<14} {theme:<5} {'; '.join(diffs) or 'all figures match'}{flag}")
            print(f"  -> {ok} match, {bad} mismatch")


if __name__ == "__main__":
    main()
