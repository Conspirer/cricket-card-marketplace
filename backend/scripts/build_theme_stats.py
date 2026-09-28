"""Build verified per-theme battle stats for every player that has cards.

    python -m backend.scripts.build_theme_stats             # write + report
    python -m backend.scripts.build_theme_stats --dry-run

Format themes (Test, ODI, T20I) take career totals from the player's
Wikipedia infobox, because Cricsheet's ball-by-ball record is incomplete for
older careers and for any match against Afghanistan (withheld by policy).
Each infobox figure is cross-checked against Cricsheet's subset:
  * Cricsheet can never exceed the infobox; if it does, the infobox is out
    of date and the row is flagged, not used.
  * When Cricsheet has every match (same match count), all shared figures
    must agree exactly, and Cricsheet's exact runs conceded give economy.
Tournament themes come straight from Cricsheet ball-by-ball, restricted to
editions it covers completely (see themes.py).

Infoboxes are often a few matches behind (and edited piecemeal: runs updated,
match count not). A stale row is rolled forward with Cricsheet when every
infobox figure equals Cricsheet's own running total after some match, all
within ROLL_WINDOW matches of each other: that proves Cricsheet had every
match the infobox counted up to there. The later matches can then come from
Cricsheet only if none of them could be against Afghanistan, which Cricsheet
withholds (India's June 2026 Afghanistan Test is missing, for one). So this
is done only for Tests, and only for countries that have never played
Afghanistan in a Test (read live from Wikipedia's Afghanistan Test records).
Every Full Member has played Afghanistan in ODIs and T20Is, and the matches
can't be dated from these sources, so those rows are never rolled forward.

A row is verified only when every stat in its theme's set is known (a value,
or legitimately "no data" such as a bowling average below the minimum
sample). Unverified rows are kept with a note explaining why and are treated
as "no data" by battles.
"""

import argparse
import html
import json
import re
import zipfile
from pathlib import Path
from collections import defaultdict
from datetime import date, datetime

import mwparserfromhell
from psycopg.types.json import Jsonb

from backend.database import get_connection
from backend.scripts.import_cricsheet import COUNTERS, DATA_DIR, MATCH_FILES, new_stats, process_match
from backend.scripts.wikipedia import fetch_wikitext
from backend.themes import (
    FORMAT_MIN_BALLS_BOWLED,
    FORMAT_MIN_BATTING_MATCHES,
    THEMES,
)

FORMAT_COLUMNS = {"Test": "TEST", "Tests": "TEST", "ODI": "ODI", "ODIs": "ODI", "T20I": "T20I", "T20Is": "T20I"}
CRICSHEET_FORMAT = {"TEST": "Test", "ODI": "ODI", "T20I": "T20"}
UNKNOWN = "unknown"  # sentinel: the stat exists but can't be made complete


def ratio(a, b, scale=1):
    return round(a / b * scale, 2) if b else None


# ---------------------------------------------------------------------------
# Wikipedia infobox parsing
# ---------------------------------------------------------------------------

DASHES = {"", "-", "–", "—", "‒", "n/a", "N/A"}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def clean(value):
    text = html.unescape(mwparserfromhell.parse(str(value)).strip_code()).strip()
    return re.sub(r"\s+", " ", text)


def number(text):
    text = text.replace(",", "").replace("*", "").strip()
    if text in DASHES:
        return None
    match = re.match(r"^(-?\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def pair(text):
    """'30/31' -> (30, 31); '121/–' -> (121, None)."""
    parts = re.split(r"\s*/\s*", text, maxsplit=1)
    first = number(parts[0])
    second = number(parts[1]) if len(parts) > 1 else None
    return first, second


def parse_as_of(text):
    text = clean(text).replace(",", " ")
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return date(int(iso[1]), int(iso[2]), int(iso[3]))
    words = text.lower().split()
    day = month = year = None
    for w in words:
        if w in MONTHS:
            month = MONTHS[w]
        elif w.isdigit() and len(w) == 4:
            year = int(w)
        elif w.isdigit() and int(w) <= 31:
            day = int(w)
    if year and month:
        return date(year, month, day or 1)
    return None  # e.g. "8 March" with no year


def infobox_formats(wikitext):
    """{theme: {...figures...}} plus the infobox as-of date."""
    code = mwparserfromhell.parse(wikitext or "")
    boxes = [t for t in code.filter_templates() if t.name.strip().lower().startswith("infobox cricket")]
    if not boxes:
        return None, None
    box = boxes[0]
    as_of = parse_as_of(box.get("date").value) if box.has("date") else None

    formats = {}
    for i in range(1, 7):
        if not box.has(f"column{i}"):
            continue
        theme = FORMAT_COLUMNS.get(clean(box.get(f"column{i}").value))
        if not theme:
            continue

        def field(name):
            return clean(box.get(f"{name}{i}").value) if box.has(f"{name}{i}") else ""

        hundreds, fifties = pair(field("100s/50s"))
        catches, stumpings = pair(field("catches/stumpings"))
        formats[theme] = {
            "matches": number(field("matches")),
            "runs": number(field("runs")),
            "batting_average": number(field("bat avg")),
            "hundreds": hundreds,
            "deliveries": number(field("deliveries")),
            "wickets": number(field("wickets")),
            "bowling_average": number(field("bowl avg")),
            "catches": catches,
        }
    return formats, as_of


LAST_MATCH_FIELDS = [("lasttestdate", "lasttestyear"), ("lastodidate", "lastodiyear"), ("lastT20Idate", "lastT20Iyear")]


def infobox_last_match(wikitext):
    """Latest "last Test/ODI/T20I" date in the infobox, or None."""
    code = mwparserfromhell.parse(wikitext or "")
    boxes = [t for t in code.filter_templates() if t.name.strip().lower().startswith("infobox cricket")]
    if not boxes:
        return None
    box, dates = boxes[0], []
    for day_field, year_field in LAST_MATCH_FIELDS:
        day = clean(box.get(day_field).value) if box.has(day_field) else ""
        year = clean(box.get(year_field).value) if box.has(year_field) else ""
        if day or year:
            parsed = parse_as_of(f"{day} {year}")
            if parsed:
                dates.append(parsed)
    return max(dates) if dates else None


def format_row(theme, infobox, as_of, cricsheet, sequence=None, data_end=None, country=None, roll_safe=frozenset()):
    """Stats row for a Test/ODI/T20I theme from infobox + Cricsheet cross-check.
    sequence: the player's Cricsheet matches in this format, oldest first, for
    rolling a stale infobox forward; data_end: the archive's last match date."""
    wanted = THEMES[theme]["stats"]
    cs = cricsheet or {}
    cs_matches = cs.get("matches", 0)
    source = "Wikipedia infobox, cross-checked with Cricsheet"

    if infobox is None:
        if cs_matches == 0:
            return empty_row(theme, source, verified=True, as_of=as_of, note="did not play")
        return empty_row(theme, source, verified=False, as_of=as_of,
                         note=f"Cricsheet has {cs_matches} matches but the infobox has no {theme} column")

    ib = {k: (0 if v is None and k in ("matches", "runs", "wickets", "catches", "hundreds", "deliveries") else v)
          for k, v in infobox.items()}
    notes = []

    # Cricsheet is a subset: if it has more, the infobox is out of date.
    for key, cs_key in [("matches", "matches"), ("runs", "runs"), ("wickets", "wickets"), ("catches", "catches")]:
        if cs.get(cs_key, 0) > (ib[key] or 0):
            notes.append(f"infobox out of date: {key} {int(ib[key] or 0)} < Cricsheet {cs.get(cs_key, 0)}")

    complete = cs_matches > 0 and cs_matches == ib["matches"]
    if complete:
        for key in ("runs", "wickets", "catches", "hundreds"):
            if cs.get(key, 0) != (ib[key] or 0):
                notes.append(f"sources disagree on {key}: infobox {int(ib[key] or 0)} vs Cricsheet {cs.get(key, 0)}")

    matches = int(ib["matches"] or 0)
    balls = int(ib["deliveries"] or 0)
    wickets = int(ib["wickets"] or 0)

    stats = {
        "runs": int(ib["runs"] or 0),
        "hundreds": int(ib["hundreds"] or 0),
        "wickets": wickets,
        "catches": int(ib["catches"] or 0),
        "batting_average": ib["batting_average"] if matches >= FORMAT_MIN_BATTING_MATCHES else None,
        "bowling_average": (ib["bowling_average"] if balls >= FORMAT_MIN_BALLS_BOWLED and wickets else None),
        "strike_rate": UNKNOWN,
        "sixes": UNKNOWN,
    }

    if balls < FORMAT_MIN_BALLS_BOWLED:
        stats["economy"] = None
    elif complete and cs.get("balls_bowled") == balls:
        stats["economy"] = ratio(cs["runs_conceded"], balls, 6)
    elif wickets and ib["bowling_average"] is not None:
        # Runs conceded = bowling average x wickets (the average is rounded
        # to 2 dp, so this is exact to the run for fewer than 100 wickets).
        stats["economy"] = ratio(round(ib["bowling_average"] * wickets), balls, 6)
    else:
        stats["economy"] = UNKNOWN

    if complete:
        stats["strike_rate"] = cs.get("strike_rate") if matches >= FORMAT_MIN_BATTING_MATCHES else None
        stats["sixes"] = cs.get("sixes", 0)

    unknown = [k for k in wanted if stats.get(k) == UNKNOWN]
    if unknown:
        notes.append(f"incomplete: {', '.join(unknown)}")
    fatal = [n for n in notes if not n.startswith("infobox as-of")]
    if fatal and sequence:
        rolled = rolled_row(theme, ib, sequence, data_end, country, roll_safe)
        if rolled:
            return rolled
    if as_of is None:
        notes.append("infobox as-of date missing or has no year")

    return {
        "theme": theme,
        "stats": {k: (None if stats.get(k) == UNKNOWN else stats.get(k)) for k in wanted},
        "all_stats": stats,
        "matches": matches,
        "source": source,
        "verified": not [n for n in notes if not n.startswith("infobox as-of")],
        "as_of": as_of,
        "notes": "; ".join(notes) or None,
    }


def empty_row(theme, source, verified, as_of=None, note=None):
    return {
        "theme": theme,
        "stats": {k: None for k in THEMES[theme]["stats"]},
        "all_stats": {},
        "matches": 0,
        "source": source,
        "verified": verified,
        "as_of": as_of,
        "notes": note,
    }


# ---------------------------------------------------------------------------
# Rolling a stale infobox forward with Cricsheet
# ---------------------------------------------------------------------------

# Infobox figure -> Cricsheet counter compared against the running totals.
ROLL_KEYS = {"runs": "runs", "wickets": "wickets", "catches": "catches", "hundreds": "hundreds",
             "deliveries": "balls_bowled"}
# Infobox figures may have been updated up to this many matches apart.
ROLL_WINDOW = 3
ADDITIVE = [c for c in COUNTERS if c != "highest"]


def snapshot_window(infobox, sequence):
    """(lo, hi) if the infobox is a (possibly piecemeal) snapshot of Cricsheet.

    The match count must equal Cricsheet's after exactly `matches` matches, and
    every other figure must equal Cricsheet's running total after some match;
    all of those matches must fit in a window of ROLL_WINDOW. Running totals
    only rise, so each figure's hits are one contiguous run of matches.
    Returns None when there's no such window (e.g. Cricsheet is missing a
    match the infobox counts, or the infobox was edited mid-match).
    """
    k = int(infobox.get("matches") or 0)
    n = len(sequence)
    if k == 0 or k > n:
        return None
    totals, running = [], dict.fromkeys(ROLL_KEYS.values(), 0)
    for match in sequence:
        running = {key: running[key] + match[key] for key in running}
        totals.append(running)
    intervals = [(k, k)]
    for ib_key, cs_key in ROLL_KEYS.items():
        value = int(infobox.get(ib_key) or 0)
        hits = [i + 1 for i, t in enumerate(totals) if t[cs_key] == value]
        if not hits:
            return None
        intervals.append((hits[0], hits[-1]))
    lo, hi = max(a for a, _ in intervals), min(b for _, b in intervals)
    if lo - hi > ROLL_WINDOW:
        return None
    return min(lo, hi), max(lo, hi)


def cricsheet_totals(sequence):
    """Theme stats from a complete run of Cricsheet matches."""
    t = {key: sum(m[key] for m in sequence) for key in ADDITIVE}
    matches = len(sequence)
    bat_ok = matches >= FORMAT_MIN_BATTING_MATCHES
    bowl_ok = t["balls_bowled"] >= FORMAT_MIN_BALLS_BOWLED
    return {
        "runs": t["runs"],
        "hundreds": t["hundreds"],
        "wickets": t["wickets"],
        "catches": t["catches"],
        "batting_average": ratio(t["runs"], t["dismissals"]) if bat_ok else None,
        "bowling_average": ratio(t["runs_conceded"], t["wickets"]) if bowl_ok and t["wickets"] else None,
        "economy": ratio(t["runs_conceded"], t["balls_bowled"], 6) if bowl_ok else None,
        "strike_rate": ratio(t["runs"], t["balls_faced"], 100) if bat_ok else None,
        "sixes": t["sixes"],
    }


AFGHANISTAN_TEST_RECORDS = "List of Afghanistan Test cricket records"
CR_CODES = {"AUS": "Australia", "BAN": "Bangladesh", "ENG": "England", "IND": "India", "IRE": "Ireland",
            "NZ": "New Zealand", "PAK": "Pakistan", "RSA": "South Africa", "SA": "South Africa",
            "SRI": "Sri Lanka", "SL": "Sri Lanka", "WIN": "West Indies", "WI": "West Indies", "ZIM": "Zimbabwe"}


def never_played_afghanistan_in_tests(wikitext):
    """Countries marked "YTP" (yet to play) in the results-by-opponent table
    of Afghanistan's Test records. Empty if the table can't be read, which
    turns rolling forward off rather than guessing."""
    safe = set()
    for code, rest in re.findall(r"\{\{cr\|([A-Z]{2,3})\}\}\}*\s*\n(.*)", wikitext or ""):
        if "YTP" in rest and code in CR_CODES:
            safe.add(CR_CODES[code])
    return safe


def rolled_row(theme, infobox, sequence, data_end, country=None, roll_safe=frozenset()):
    if theme != "TEST" or country not in roll_safe:
        return None  # a later match against Afghanistan could be missing from Cricsheet
    window = snapshot_window(infobox, sequence)
    if window is None:
        return None
    lo, hi = window
    stats = cricsheet_totals(sequence)
    behind = len(sequence) - int(infobox["matches"])
    span = f"match {lo}" if lo == hi else f"matches {lo}-{hi}"
    return {
        "theme": theme,
        "stats": {k: stats[k] for k in THEMES[theme]["stats"]},
        "all_stats": stats,
        "matches": len(sequence),
        "source": "Wikipedia infobox, rolled forward with Cricsheet",
        "verified": True,
        "as_of": data_end,
        "notes": (f"infobox {behind} matches behind; its figures equal Cricsheet's running totals after {span} "
                  f"({sequence[hi - 1]['date']}), so later matches come from Cricsheet ({country} has never "
                  f"played Afghanistan in a Test, the one side Cricsheet withholds)."),
    }


def cricsheet_sequences(cricsheet_ids):
    """({cricsheet_id: {theme: [per-match counters with "date", oldest first]}},
    {theme: last match date in the archive}) for the Test/ODI/T20I themes."""
    wanted = set(cricsheet_ids)
    theme_of = {fmt: theme for theme, fmt in CRICSHEET_FORMAT.items()}
    out = defaultdict(lambda: defaultdict(list))
    data_end = {}
    for fmt, filename in MATCH_FILES.items():
        theme = theme_of[fmt]
        with zipfile.ZipFile(DATA_DIR / filename) as archive:
            for name in archive.namelist():
                if not name.endswith(".json"):
                    continue
                match = json.loads(archive.read(name))
                info = match["info"]
                data_end[theme] = max(data_end.get(theme, ""), info["dates"][-1])
                registry = info["registry"]["people"]
                present = {registry[p] for names in info["players"].values() for p in names if p in registry} & wanted
                if not present:
                    continue
                stats = defaultdict(lambda: defaultdict(new_stats))
                process_match(match, fmt, stats, defaultdict(lambda: defaultdict(int)))
                for cid in present:
                    out[cid][theme].append(dict(stats[cid][fmt], date=info["dates"][0]))
    for per_theme in out.values():
        for seq in per_theme.values():
            seq.sort(key=lambda m: m["date"])
    return out, {t: datetime.strptime(d, "%Y-%m-%d").date() for t, d in data_end.items()}


# ---------------------------------------------------------------------------
# Cricsheet tournaments
# ---------------------------------------------------------------------------

def tournament_stats():
    """{theme: {cricsheet_id: counters}}, {theme: last match date}."""
    per_theme = {t: defaultdict(lambda: defaultdict(new_stats)) for t, c in THEMES.items() if c["source"] == "cricsheet"}
    last_date = {}
    by_archive = defaultdict(list)
    for theme in per_theme:
        by_archive[THEMES[theme]["archive"]].append(theme)

    for archive, themes in by_archive.items():
        with zipfile.ZipFile(DATA_DIR / archive) as z:
            for name in z.namelist():
                if not name.endswith(".json"):
                    continue
                match = json.loads(z.read(name))
                info = match["info"]
                event = info.get("event", {}).get("name")
                season = str(info["season"])
                for theme in themes:
                    config = THEMES[theme]
                    if event in config["events"] and (config["seasons"] is None or season in config["seasons"]):
                        process_match(match, theme, per_theme[theme], defaultdict(lambda: defaultdict(int)))
                        last_date[theme] = max(last_date.get(theme, ""), info["dates"][-1])
    return per_theme, last_date


def tournament_row(theme, counters, last):
    config = THEMES[theme]
    source = f"Cricsheet ball-by-ball ({config['label']})"
    as_of = datetime.strptime(last, "%Y-%m-%d").date() if last else None
    if not counters or counters["matches"] == 0:
        return empty_row(theme, source, verified=True, as_of=as_of, note="did not play")

    c = counters
    bat_ok = c["innings"] >= config["min_innings"]
    bowl_ok = c["balls_bowled"] >= config["min_balls"]
    stats = {
        "runs": c["runs"],
        "batting_average": ratio(c["runs"], c["dismissals"]) if bat_ok else None,
        "strike_rate": ratio(c["runs"], c["balls_faced"], 100) if bat_ok else None,
        "hundreds": c["hundreds"],
        "sixes": c["sixes"],
        "wickets": c["wickets"],
        "bowling_average": ratio(c["runs_conceded"], c["wickets"]) if bowl_ok else None,
        "economy": ratio(c["runs_conceded"], c["balls_bowled"], 6) if bowl_ok else None,
        "catches": c["catches"],
    }
    return {
        "theme": theme,
        "stats": {k: stats[k] for k in config["stats"]},
        "all_stats": stats,
        "matches": c["matches"],
        "source": source,
        "verified": True,
        "as_of": as_of,
        "notes": None,
    }


# ---------------------------------------------------------------------------

LEGENDS_FILE = Path(__file__).resolve().parents[2] / "data" / "legends.json"


def legend_titles():
    if not LEGENDS_FILE.exists():
        return []
    return [entry["title"] for entry in json.loads(LEGENDS_FILE.read_text())["legends"]]


def build(cursor, extra_player_ids=(), texts=None):
    """Build and upsert theme stats; returns the rows. Covers every player with a
    card definition, every legend in data/legends.json that has a player row,
    and any extra ids given."""
    cursor.execute(
        """
        SELECT p.id, p.name, p.country, p.cricsheet_id, p.wikipedia_title, p.career_stats,
               COALESCE(bool_or(d.is_active), false) AS active
        FROM players p LEFT JOIN card_definitions d ON d.player_id = p.id
        WHERE p.id IN (SELECT player_id FROM card_definitions)
           OR p.wikipedia_title = ANY(%s)
           OR p.id = ANY(%s)
        GROUP BY p.id ORDER BY p.id;
        """,
        (legend_titles(), list(extra_player_ids)),
    )
    players = cursor.fetchall()

    texts = dict(texts or {})
    missing = [t for t in [p["wikipedia_title"] for p in players if p["wikipedia_title"]] + [AFGHANISTAN_TEST_RECORDS]
               if t not in texts]
    print(f"fetching {len(missing)} Wikipedia articles")
    texts.update(fetch_wikitext(missing))
    roll_safe = never_played_afghanistan_in_tests(texts.get(AFGHANISTAN_TEST_RECORDS))
    print(f"never played Afghanistan in a Test (Test rows may be rolled forward): {sorted(roll_safe) or 'none found'}")
    print("aggregating Cricsheet tournaments")
    tournaments, last_dates = tournament_stats()
    print("collecting Cricsheet match-by-match records")
    sequences, data_end = cricsheet_sequences(p["cricsheet_id"] for p in players if p["cricsheet_id"])

    rows = []
    for p in players:
        infobox, as_of = infobox_formats(texts.get(p["wikipedia_title"]))
        career = p["career_stats"] or {}
        for theme, config in THEMES.items():
            if config["source"] == "wikipedia":
                if infobox is None:
                    row = empty_row(theme, "Wikipedia infobox", verified=False, note="no Wikipedia infobox found")
                else:
                    row = format_row(theme, infobox.get(theme), as_of, career.get(CRICSHEET_FORMAT[theme]),
                                     sequence=sequences.get(p["cricsheet_id"], {}).get(theme), data_end=data_end.get(theme),
                                     country=p["country"], roll_safe=roll_safe)
            else:
                counters = tournaments[theme].get(p["cricsheet_id"], {}).get(theme)
                row = tournament_row(theme, counters, last_dates.get(theme))
            row["player"] = p
            rows.append(row)

    cursor.executemany(
        """
        INSERT INTO player_theme_stats (player_id, theme, stats, matches, source, verified, as_of, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (player_id, theme) DO UPDATE SET
            stats = EXCLUDED.stats, matches = EXCLUDED.matches, source = EXCLUDED.source,
            verified = EXCLUDED.verified, as_of = EXCLUDED.as_of, notes = EXCLUDED.notes,
            updated_at = now();
        """,
        [
            (r["player"]["id"], r["theme"], Jsonb(r["stats"]), r["matches"], r["source"],
             r["verified"], r["as_of"], r["notes"])
            for r in rows
        ],
    )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            rows = build(cursor)
            report(rows)
            if args.dry_run:
                connection.rollback()
                print("dry run: rolled back")


def report(rows):
    active = [r for r in rows if r["player"]["active"]]
    print("\nper theme (active pool):")
    for theme in THEMES:
        theme_rows = [r for r in active if r["theme"] == theme]
        played = [r for r in theme_rows if r["matches"] > 0]
        unverified = [r for r in theme_rows if not r["verified"]]
        print(f"  {theme:<7} played {len(played):>3}/{len(theme_rows)}  unverified {len(unverified)}")

    # Which candidate stats could be made complete for every active player?
    print("\nstat completeness, format themes (active players who played):")
    for theme in ("TEST", "ODI", "T20I"):
        played = [r for r in active if r["theme"] == theme and r["matches"] > 0]
        for stat in ("strike_rate", "sixes", "economy"):
            unknown = sum(1 for r in played if r["all_stats"].get(stat) == UNKNOWN)
            print(f"  {theme:<5} {stat:<12} incomplete for {unknown}/{len(played)}")

    flagged = defaultdict(list)
    for r in rows:
        if not r["verified"]:
            flagged[r["player"]["name"]].append(f"{r['theme']}: {r['notes']}")
    print(f"\nflagged players: {len(flagged)}")
    for name, problems in sorted(flagged.items()):
        print(f"  {name}")
        for problem in problems:
            print(f"      {problem}")


if __name__ == "__main__":
    main()
