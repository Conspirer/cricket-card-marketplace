"""Write data/rarity_review.csv: the proposed card pool and tiers, for review.

    python -m backend.scripts.rarity_review

Who's considered: every player from a Full Member nation (backend.ratings.
FULL_MEMBERS) with a real international career in Cricsheet (see PREFILTER),
plus anyone Cricsheet only saw in a composite side (Rashid Khan: Cricsheet
withholds Afghanistan's matches, so it only has him for the World XI; his
country and formats come from Wikipedia), plus today's card holders and the
legends in data/legends.json.

Each is linked to Wikipedia, their verified theme stats are built (stale
infoboxes rolled forward with Cricsheet where that can be proven), and they're
scored on their best formats (backend/tiering.py).

Two editions, tiered separately:
  legend   last international match before LEGEND_CUTOFF (the later of
           Cricsheet's last match and the Wikipedia infobox's last Test/ODI/
           T20I date), or listed in data/legends.json (the must-include
           pre-Cricsheet greats). Everyone in legends.json goes in, then the
           best of the rest up to LEGEND_POOL_SIZE.
  current  everyone else; the top POOL_SIZE go in.
data/pool_overrides.csv (player_id, include yes/no, edition legend/current,
note; blank = no override) always wins and survives reruns: include=yes
forces a player in (on top of the pool sizes), no keeps one out, and edition
moves an edge case to the other edition.

Legends are resolved by Wikipedia article -> Wikidata -> ESPNcricinfo id ->
Cricsheet register (names alone are ambiguous: there are two Shahid Afridis,
and "RS Gavaskar" is Rohan, not Sunil). A player row is created only when a
legend has none (careers before Cricsheet).

Rows: both pools, the next BENCH_SIZE of each edition (include=no, to swap
in by hand), current card holders who dropped out and the SANITY_CHECK players
who didn't make it (include=no).

Columns:
  status                 legend or current (the edition)
  include                yes = in the pool (chosen by rank each run;
                         data/pool_overrides.csv wins)
  current_rarity         top rarity of the player's active definitions today
  proposed_rarity        formula tier within the player's edition
  proposed_if_no_legends same as proposed_rarity for current players (legends
                         no longer share their tiers); blank for legends
  override               from data/rarity_overrides.csv (always wins)
  final_rarity           what build_card_pool would give now (include + overrides)
  unverified_formats     formats they played that couldn't be verified: scored
                         on the other formats, never as zero
"""

import csv
import json
import time
from datetime import date
import urllib.parse
import urllib.request

import mwparserfromhell

from backend.database import get_connection
from backend.ratings import FULL_MEMBERS
from backend.scripts import build_theme_stats
from backend.scripts.build_card_pool import (
    OVERRIDES_FILE, POOL_OVERRIDES_FILE, REVIEW_FILE, load_overrides, load_pool_overrides, verified_theme_stats,
)
from backend.scripts.build_theme_stats import infobox_last_match
from backend.scripts.import_cricsheet import COMPOSITE_TEAMS
from backend.scripts.link_players import SPARQL, USER_AGENT, cricinfo_ids, link
from backend.scripts.wikipedia import fetch_wikitext, open_json
from backend.tiering import (
    FORMATS, LEGEND_CUTOFF, LEGEND_EPIC_SIZE, LEGEND_LEGENDARY_SIZE, LEGEND_POOL_SIZE, MIN_BEST_FORMAT_MATCHES,
    POOL_SIZE, RARITIES, TIER_SIZES, assign_tiers, career, is_legend,
)

LEGENDS_FILE = REVIEW_FILE.parent / "legends.json"
ORDER = {r: i for i, r in enumerate(reversed(RARITIES))}  # Legendary first

# Cricsheet career needed to be looked at at all (Cricsheet undercounts older
# careers, so this is looser than the real bar, MIN_BEST_FORMAT_MATCHES).
PREFILTER = {"Test": 10, "ODI": 20, "T20": 20}
BENCH_SIZE = 20
# Each must be in the pool, or the report says why not.
SANITY_CHECK = [
    "Joe Root", "Steve Smith", "Kane Williamson", "Ben Stokes", "James Anderson", "Stuart Broad",
    "Pat Cummins", "Mitchell Starc", "Ravichandran Ashwin", "Ravindra Jadeja", "Kagiso Rabada",
    "Shakib Al Hasan", "Mohammed Shami", "Mohammed Siraj", "Cheteshwar Pujara", "Rashid Khan",
]
# Where these land is shown in the report.
WATCH = ["Jasprit Bumrah", "Ben Stokes", "Mitchell Starc", "Pat Cummins", "Kagiso Rabada", "Ravindra Jadeja",
         "Rashid Khan", "Suryakumar Yadav", "MS Dhoni", "Rahul Dravid", "Cheteshwar Pujara"]
# Classifications worth a second look: legends who played this recently, and
# current players whose last match is this close after the cutoff.
RECENT_LEGEND_SINCE = date(2022, 1, 1)
BARELY_CURRENT_BEFORE = date(2024, 7, 1)


def wikidata_for_titles(titles):
    """{title: (qid, cricinfo_id or None)}"""
    values = " ".join('"' + t.replace('"', '\\"') + '"@en' for t in titles)
    query = f"""
        SELECT ?title ?item ?cid WHERE {{
          VALUES ?title {{ {values} }}
          ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?title .
          OPTIONAL {{ ?item wdt:P2697 ?cid . }}
        }}"""
    request = urllib.request.Request(
        SPARQL + "?" + urllib.parse.urlencode({"query": query}),
        headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT},
    )
    rows = open_json(request)["results"]["bindings"]
    time.sleep(1)
    return {
        r["title"]["value"]: (r["item"]["value"].rsplit("/", 1)[-1], r.get("cid", {}).get("value"))
        for r in rows
    }


def resolve_legends(cursor):
    """Make sure every legend has a linked player row; returns {player_id: title}."""
    legends = json.loads(LEGENDS_FILE.read_text())["legends"]
    wiki = wikidata_for_titles([l["title"] for l in legends])
    register = {cid: sid for sid, cid in cricinfo_ids().items()}  # cricinfo -> cricsheet
    resolved = {}
    for legend in legends:
        title = legend["title"]
        if title not in wiki:
            print(f"  WARNING {title}: not found on Wikidata; skipped")
            continue
        qid, cid = wiki[title]
        cricsheet_id = register.get(cid) if cid else None
        cursor.execute(
            """
            SELECT id FROM players
            WHERE (cricsheet_id IS NOT NULL AND cricsheet_id = %s) OR (cricinfo_id IS NOT NULL AND cricinfo_id = %s)
               OR wikidata_id = %s
            ORDER BY id LIMIT 1;
            """,
            (cricsheet_id, cid, qid),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE players SET name = %s, cricinfo_id = %s, wikidata_id = %s, wikipedia_title = %s WHERE id = %s;",
                (title, cid, qid, title, row["id"]),
            )
            player_id = row["id"]
        else:
            cursor.execute(
                """
                INSERT INTO players (name, country, role, cricsheet_id, cricinfo_id, wikidata_id, wikipedia_title)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;
                """,
                (title, legend["country"], legend["role"], cricsheet_id, cid, qid, title),
            )
            player_id = cursor.fetchone()["id"]
            print(f"  created player row for {title} (no Cricsheet international data)")
        resolved[player_id] = title
    return resolved


def candidate_ids(cursor):
    """Full Member players with a real Cricsheet career, Afghanistan players
    and composite-side-only players (Cricsheet has none of their international
    matches, so its volume says nothing), and today's card holders."""
    cursor.execute(
        """
        SELECT id FROM players
        WHERE (country = ANY(%(full)s) AND (
                  COALESCE((career_stats->'Test'->>'matches')::int, 0) >= %(test)s
               OR COALESCE((career_stats->'ODI'->>'matches')::int, 0) >= %(odi)s
               OR COALESCE((career_stats->'T20'->>'matches')::int, 0) >= %(t20)s))
           OR country = ANY(%(composite)s) OR country = 'Afghanistan'
           OR id IN (SELECT player_id FROM card_definitions WHERE edition = 'BASE')
        ORDER BY id;
        """,
        {"full": sorted(FULL_MEMBERS), "composite": sorted(COMPOSITE_TEAMS | {"Unknown"}),
         "test": PREFILTER["Test"], "odi": PREFILTER["ODI"], "t20": PREFILTER["T20"]},
    )
    return [r["id"] for r in cursor.fetchall()]


def infobox_country(wikitext):
    code = mwparserfromhell.parse(wikitext or "")
    for box in code.filter_templates():
        if box.name.strip().lower().startswith("infobox cricket") and box.has("country"):
            return build_theme_stats.clean(box.get("country").value) or None
    return None


def fix_countries(cursor, ids):
    """Players Cricsheet can't place (composite sides only) get their country
    from their Wikipedia infobox, if it's a Full Member."""
    cursor.execute(
        "SELECT id, name, country, wikipedia_title FROM players WHERE id = ANY(%s) AND NOT (country = ANY(%s));",
        (ids, sorted(FULL_MEMBERS)),
    )
    rows = [r for r in cursor.fetchall() if r["wikipedia_title"]]
    texts = fetch_wikitext([r["wikipedia_title"] for r in rows]) if rows else {}
    fixed = []
    for r in rows:
        country = infobox_country(texts.get(r["wikipedia_title"]))
        if country in FULL_MEMBERS and country != r["country"]:
            cursor.execute("UPDATE players SET country = %s WHERE id = %s;", (country, r["id"]))
            fixed.append((r["name"], r["country"], country))
    return fixed


def last_internationals(cursor, ids, texts):
    """{player_id: (last international date or None, source)}: the later of
    Cricsheet's last Test/ODI/T20I and the infobox's last Test/ODI/T20I."""
    cursor.execute("SELECT id, wikipedia_title, career_stats FROM players WHERE id = ANY(%s);", (list(ids),))
    out = {}
    for r in cursor.fetchall():
        candidates = []
        for fmt in ("Test", "ODI", "T20"):
            last = ((r["career_stats"] or {}).get(fmt) or {}).get("last_match")
            if last:
                candidates.append((date.fromisoformat(last), "Cricsheet"))
        infobox = infobox_last_match(texts.get(r["wikipedia_title"]))
        if infobox:
            candidates.append((infobox, "Wikipedia"))
        out[r["id"]] = max(candidates) if candidates else (None, None)
    return out


def main():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            print("resolving legends")
            must_legends = resolve_legends(cursor)
            ids = candidate_ids(cursor)
            cursor.execute("SELECT id, name, cricsheet_id FROM players WHERE id = ANY(%s) AND wikipedia_title IS NULL;", (ids,))
            unlinked = cursor.fetchall()
            print(f"{len(ids)} candidates; linking {len(unlinked)} to Wikipedia")
            _, unresolved, _ = link(cursor, unlinked)
            fixed = fix_countries(cursor, ids)
            everyone = sorted(set(ids) | set(must_legends))
            cursor.execute("SELECT wikipedia_title FROM players WHERE id = ANY(%s) AND wikipedia_title IS NOT NULL;", (everyone,))
            print("fetching Wikipedia articles")
            texts = fetch_wikitext([r["wikipedia_title"] for r in cursor.fetchall()])
            last_intl = last_internationals(cursor, everyone, texts)
            print("building verified theme stats")
            build_theme_stats.build(cursor, extra_player_ids=everyone, texts=texts)

            cursor.execute(
                """
                SELECT p.id, p.name, p.country, p.role,
                       max(array_position(ARRAY['Common','Rare','Epic','Legendary'], d.rarity)) FILTER (WHERE d.is_active) AS active_rank,
                       max(array_position(ARRAY['Common','Rare','Epic','Legendary'], d.rarity)) AS any_rank
                FROM players p LEFT JOIN card_definitions d ON d.player_id = p.id AND d.edition = 'BASE'
                WHERE p.id = ANY(%s)
                GROUP BY p.id;
                """,
                (everyone,),
            )
            players = {r["id"]: r for r in cursor.fetchall()}
            cursor.execute(
                "SELECT player_id, theme, stats, matches, verified, source, notes FROM player_theme_stats WHERE player_id = ANY(%s);",
                (everyone,),
            )
            theme_rows = {}
            for r in cursor.fetchall():
                theme_rows.setdefault(r["player_id"], {})[r["theme"]] = r
            stats = verified_theme_stats(cursor, everyone)

    careers = {pid: career(stats[pid]) for pid in everyone}
    scores = {pid: c["score"] for pid, c in careers.items()}
    overrides = load_overrides()
    pool_overrides = load_pool_overrides()
    unknown = sorted(set(pool_overrides) - set(everyone))
    if unknown:
        raise SystemExit(f"{POOL_OVERRIDES_FILE}: player ids that aren't candidates or legends: {unknown}")

    # Editions: legends.json, then the cutoff, then pool_overrides.csv (wins).
    edition = {pid: "legend" if pid in must_legends or is_legend(last_intl[pid][0]) else "current" for pid in everyone}
    for pid, o in pool_overrides.items():
        if o["edition"]:
            edition[pid] = o["edition"]
    legend_ids = {pid for pid, e in edition.items() if e == "legend"}

    def why_not_eligible(pid):
        p = players[pid]
        if p["country"] not in FULL_MEMBERS:
            return f"country {p['country']!r} isn't a Full Member"
        if scores[pid] is None:
            played = {f: theme_rows.get(pid, {}).get(f) for f in FORMATS}
            best = ", ".join(f"{f} {r['matches']}" for f, r in played.items() if r and r["verified"] and r["matches"])
            return (f"no verified format with enough matches to be their best "
                    f"(needs {MIN_BEST_FORMAT_MATCHES}; has {best or 'none'})")
        return None

    by_score = lambda group: sorted(group, key=lambda p: (-(scores[p] or 0), p))
    eligible = {e: by_score(pid for pid in everyone if edition[pid] == e and why_not_eligible(pid) is None)
                for e in ("current", "legend")}
    # Must-include legends go in even when unrankable; the best of the rest fill up to the size.
    must = [pid for pid in by_score(set(must_legends) & legend_ids)]
    auto = [pid for pid in eligible["legend"] if pid not in must]
    chosen = {"current": eligible["current"][:POOL_SIZE],
              "legend": must + auto[:max(0, LEGEND_POOL_SIZE - len(must))]}
    bench = {"current": eligible["current"][POOL_SIZE:POOL_SIZE + BENCH_SIZE],
             "legend": auto[max(0, LEGEND_POOL_SIZE - len(must)):][:BENCH_SIZE]}
    cut_rank = {pid: i + 1 for e in eligible for i, pid in enumerate(eligible[e])}

    include = {pid: "yes" for e in chosen for pid in chosen[e]}
    forced = []
    for pid, o in pool_overrides.items():  # always wins
        if o["include"] is True and include.get(pid) != "yes":
            forced.append(pid)
        if o["include"] is not None:
            include[pid] = "yes" if o["include"] else "no"
    included = [pid for pid, v in include.items() if v == "yes"]
    ranked_scores = {pid: scores[pid] or 0.0 for pid in include}

    proposed, _, _ = assign_tiers(ranked_scores, legends=legend_ids & set(include))
    final, _, warnings = assign_tiers({pid: ranked_scores[pid] for pid in included}, overrides,
                                      legend_ids & set(included))
    unranked = [pid for pid in included if scores[pid] is None]
    if unranked:
        warnings.append("included but not rankable (no format with enough verified matches), placed last: "
                        + ", ".join(players[p]["name"] for p in unranked))

    had_cards = {pid for pid in everyone if players[pid]["active_rank"]}
    by_name = {}
    for pid in everyone:
        by_name.setdefault(players[pid]["name"].lower(), []).append(pid)

    def lookup(name):
        """Best-scoring Full Member player with this name (there are three Rashid Khans)."""
        pids = by_name.get(name.lower(), [])
        pids.sort(key=lambda p: (players[p]["country"] not in FULL_MEMBERS, -(scores[p] or 0)))
        return pids[0] if pids else None

    sanity = {name: lookup(name) for name in SANITY_CHECK}
    shown = (set(include) | set(bench["current"]) | set(bench["legend"]) | (had_cards - set(include))
             | {p for p in sanity.values() if p} | {p for p in map(lookup, WATCH) if p})
    rank = {pid: i + 1 for i, pid in enumerate(by_score(shown))}

    def current_rarity(p):
        if p["active_rank"]:
            return RARITIES[p["active_rank"] - 1]
        if p["any_rank"]:
            return f"{RARITIES[p['any_rank'] - 1]} (inactive)"
        return ""

    def unverified(pid):
        return [f for f in FORMATS
                if (t := theme_rows.get(pid, {}).get(f)) and not t["verified"] and (t["matches"] or 0) > 0]

    out = []
    for pid in shown:
        p = players[pid]
        row = {
            "rank": rank[pid], "player_id": pid, "player": p["name"], "country": p["country"], "role": p["role"],
            "status": edition[pid], "include": include.get(pid, "no"),
            "current_rarity": current_rarity(p), "proposed_rarity": proposed.get(pid, ""),
            "proposed_if_no_legends": proposed.get(pid, "") if edition[pid] == "current" else "",
            "override": overrides.get(pid, ""), "final_rarity": final.get(pid, ""),
            "score": "" if scores[pid] is None else scores[pid],
        }
        for fmt in FORMATS:
            t = theme_rows.get(pid, {}).get(fmt)
            key = fmt.lower()
            if t and t["verified"] and t["matches"]:
                row[f"{key}_matches"] = t["matches"]
                row[f"{key}_runs"] = t["stats"].get("runs")
                row[f"{key}_wickets"] = t["stats"].get("wickets")
            else:
                row[f"{key}_matches"] = row[f"{key}_runs"] = row[f"{key}_wickets"] = ""
        row["unverified_formats"] = " ".join(unverified(pid))
        out.append(row)
    out.sort(key=lambda r: (r["include"] != "yes", r["status"] != "current", ORDER.get(r["proposed_rarity"], 9), r["rank"]))

    REVIEW_FILE.parent.mkdir(exist_ok=True)
    with open(REVIEW_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out[0]))
        writer.writeheader()
        writer.writerows(out)
    if not OVERRIDES_FILE.exists():
        OVERRIDES_FILE.write_text("player_id,rarity,note\n")

    report(locals())


def report(ctx):
    players, scores, careers, edition = ctx["players"], ctx["scores"], ctx["careers"], ctx["edition"]
    include, proposed, last_intl, cut_rank = ctx["include"], ctx["proposed"], ctx["last_intl"], ctx["cut_rank"]
    final, overrides = ctx["final"], ctx["overrides"]
    tier = lambda pid: final.get(pid, proposed[pid]) + ("*" if pid in overrides else "")
    by_score = ctx["by_score"]
    name = lambda pid: players[pid]["name"]
    pool = {e: by_score(pid for pid, v in include.items() if v == "yes" and edition[pid] == e) for e in ("current", "legend")}

    def when(pid):
        d, src = last_intl.get(pid, (None, None))
        return f"last intl {d} ({src})" if d else "last intl unknown"

    print(f"\nwrote {REVIEW_FILE}: {len(pool['current'])} current + {len(pool['legend'])} legends "
          f"({len(ctx['forced'])} forced in by {POOL_OVERRIDES_FILE.name}; from {len(ctx['everyone'])} candidates)")
    print(f"editions: {sum(1 for e in edition.values() if e == 'legend')} legends "
          f"(last international before {LEGEND_CUTOFF}, or in legends.json), "
          f"{sum(1 for e in edition.values() if e == 'current')} current")
    if ctx["fixed"]:
        print("countries set from Wikipedia: " + ", ".join(f"{n} ({a} -> {b})" for n, a, b in ctx["fixed"]))
    if ctx["unresolved"]:
        print(f"not linked to Wikipedia ({len(ctx['unresolved'])}): " + ", ".join(n for n, _ in ctx["unresolved"][:30]))

    def line(i, pid):
        c = careers[pid]
        parts = " ".join(f"{f} {v:.2f}" for f, v in sorted(c["parts"].items(), key=lambda kv: -kv[1]))
        score = "-" if c["score"] is None else f"{c['score']:.3f}"
        return f"  {i:>3}. {name(pid):<24} {tier(pid):<10} {score:>6}  best {c['best'] or '-':<4}  [{parts}]  {when(pid)}"

    print("\ntiers are final (rarity_overrides.csv applied; * = overridden)")
    print("\ntop 25 current players:")
    for i, pid in enumerate(pool["current"][:25], 1):
        print(line(i, pid))
    print(f"\ntop 25 legends ({LEGEND_LEGENDARY_SIZE} Legendary, {LEGEND_EPIC_SIZE} Epic, the rest Rare):")
    for i, pid in enumerate(pool["legend"][:25], 1):
        print(line(i, pid) + ("  (legends.json)" if pid in ctx["must_legends"] else ""))

    def where(pid):
        if pid is None:
            return "not in the database"
        e = edition[pid]
        if include.get(pid) == "yes":
            forced = " (forced in)" if pid in ctx["forced"] else ""
            return f"{e} #{pool[e].index(pid) + 1} of {len(pool[e])}, {tier(pid)}{forced}; {when(pid)}"
        reason = ctx["why_not_eligible"](pid)
        if reason:
            return f"{e}, out, not eligible: {reason}; {when(pid)}"
        return f"{e}, out, ranked {cut_rank.get(pid)} among {e}s; {when(pid)}"

    print("\nwatched players:")
    for n in WATCH:
        print(f"  {n:<20} {where(ctx['lookup'](n))}")
    print("\nsanity list:")
    for n, pid in ctx["sanity"].items():
        print(f"  {n:<20} {'IN   ' if include.get(pid) == 'yes' else 'OUT  '}{where(pid)}")

    recent = [pid for pid in pool["legend"] if (d := last_intl[pid][0]) and d >= RECENT_LEGEND_SINCE]
    print(f"\nlegends in the pool who played since {RECENT_LEGEND_SINCE} (check they've really finished):")
    for pid in sorted(recent, key=lambda p: last_intl[p][0], reverse=True):
        print(f"  {name(pid):<24} {tier(pid):<10} {when(pid)}")
    barely = [pid for pid in pool["current"] if (d := last_intl[pid][0]) and d < BARELY_CURRENT_BEFORE]
    print(f"\ncurrent players in the pool whose last international is before {BARELY_CURRENT_BEFORE} (maybe retired):")
    for pid in sorted(barely, key=lambda p: last_intl[p][0]):
        print(f"  {name(pid):<24} {tier(pid):<10} {when(pid)}")
    undated = [pid for pid in pool["current"] if last_intl[pid][0] is None]
    if undated:
        print("current players with no last-match date (treated as current): " + ", ".join(map(name, undated)))
    disagree = [pid for pid in ctx["everyone"] if pid in ctx["must_legends"] and not is_legend(last_intl[pid][0])
                and last_intl[pid][0]]
    if disagree:
        print("in legends.json but played after the cutoff: " + ", ".join(f"{name(p)} ({when(p)})" for p in disagree))

    print("\nin the pool with an unverified format (scored on their verified formats only):")
    for e in ("current", "legend"):
        names = [f"{name(p)} ({' '.join(ctx['unverified'](p))})" for p in pool[e] if ctx["unverified"](p)]
        print(f"  {e}: " + (", ".join(names) or "none"))
    print("\nbench (next in line): current: " + ", ".join(name(p) for p in ctx["bench"]["current"] if include.get(p) != "yes"))
    print("                      legends: " + ", ".join(name(p) for p in ctx["bench"]["legend"] if include.get(p) != "yes"))
    for w in ctx["warnings"]:
        print(f"WARNING {w}")
    print(f"tier sizes: current {TIER_SIZES} (Common for the rest); legends {LEGEND_LEGENDARY_SIZE} Legendary, "
          f"{LEGEND_EPIC_SIZE} Epic, the rest Rare; pool {POOL_SIZE} current + about {LEGEND_POOL_SIZE} legends")


if __name__ == "__main__":
    main()
