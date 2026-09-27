"""Write data/rarity_review.csv: proposed card tiers for review.

    python -m backend.scripts.rarity_review

Candidates are every player that has a card definition today plus the
legends in data/legends.json. Legends are resolved by Wikipedia article ->
Wikidata -> ESPNcricinfo id -> Cricsheet register (names alone are ambiguous:
there are two Shahid Afridis, and "RS Gavaskar" is Rohan, not Sunil). A player
row is created only when a legend has none (careers before Cricsheet). Their
verified theme stats are then built like everyone else's.

Columns:
  include                yes = in the pool; legends start as "proposed" and go in
                         only when you change that to "yes". Kept when regenerating.
  current_rarity         top rarity of the player's active definitions today
  proposed_rarity        formula tier if every listed legend were approved
  proposed_if_no_legends formula tier among current players only
  override               from data/rarity_overrides.csv (always wins)
  final_rarity           what build_card_pool would give now (include + overrides)
"""

import csv
import json
import time
import urllib.parse
import urllib.request

from backend.database import get_connection
from backend.scripts import build_theme_stats
from backend.scripts.build_card_pool import OVERRIDES_FILE, REVIEW_FILE, load_overrides, verified_theme_stats
from backend.scripts.import_cricsheet import DATA_DIR
from backend.scripts.link_players import SPARQL, USER_AGENT, cricinfo_ids
from backend.tiering import FORMATS, RARITIES, TIER_SIZES, assign_tiers, career_score

LEGENDS_FILE = REVIEW_FILE.parent / "legends.json"
ORDER = {r: i for i, r in enumerate(reversed(RARITIES))}  # Legendary first


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
    with urllib.request.urlopen(request, timeout=60) as response:
        rows = json.load(response)["results"]["bindings"]
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


def previous_includes():
    if not REVIEW_FILE.exists():
        return {}
    with open(REVIEW_FILE, newline="") as f:
        return {int(r["player_id"]): r["include"] for r in csv.DictReader(f)}


def main():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            print("resolving legends")
            legends = resolve_legends(cursor)
            print("building verified theme stats")
            build_theme_stats.build(cursor, extra_player_ids=legends)

            cursor.execute(
                """
                SELECT p.id, p.name, p.country, p.role,
                       max(array_position(ARRAY['Common','Rare','Epic','Legendary'], d.rarity)) FILTER (WHERE d.is_active) AS active_rank,
                       max(array_position(ARRAY['Common','Rare','Epic','Legendary'], d.rarity)) AS any_rank
                FROM players p LEFT JOIN card_definitions d ON d.player_id = p.id
                WHERE p.id IN (SELECT player_id FROM card_definitions) OR p.id = ANY(%s)
                GROUP BY p.id;
                """,
                (list(legends),),
            )
            players = {r["id"]: r for r in cursor.fetchall()}
            cursor.execute(
                "SELECT player_id, theme, stats, matches, verified FROM player_theme_stats WHERE player_id = ANY(%s);",
                (list(players),),
            )
            theme_rows = {}
            for r in cursor.fetchall():
                theme_rows.setdefault(r["player_id"], {})[r["theme"]] = r
            stats = verified_theme_stats(cursor, players)

    overrides = load_overrides()
    previous = previous_includes()
    current_ids = [pid for pid in players if pid not in legends]
    include = {pid: previous.get(pid, "proposed" if pid in legends else "yes") for pid in players}

    scores = {pid: career_score(stats[pid]) for pid in players}
    _, with_legends, _ = assign_tiers(scores)
    _, without_legends, _ = assign_tiers({pid: scores[pid] for pid in current_ids})
    included = [pid for pid in players if include[pid].lower() == "yes"]
    final, _, warnings = assign_tiers({pid: scores[pid] for pid in included}, overrides)
    rank = {pid: i + 1 for i, pid in enumerate(sorted(players, key=lambda p: (-scores[p], p)))}

    def current_rarity(p):
        if p["active_rank"]:
            return RARITIES[p["active_rank"] - 1]
        if p["any_rank"]:
            return f"{RARITIES[p['any_rank'] - 1]} (inactive)"
        return ""

    out = []
    for pid, p in players.items():
        row = {
            "rank": rank[pid], "player_id": pid, "player": p["name"], "country": p["country"], "role": p["role"],
            "status": "legend" if pid in legends else "current", "include": include[pid],
            "current_rarity": current_rarity(p), "proposed_rarity": with_legends[pid],
            "proposed_if_no_legends": without_legends.get(pid, ""),
            "override": overrides.get(pid, ""), "final_rarity": final.get(pid, ""), "score": scores[pid],
        }
        unverified = []
        for fmt in FORMATS:
            t = theme_rows.get(pid, {}).get(fmt)
            key = fmt.lower()
            if t and t["verified"] and t["matches"]:
                row[f"{key}_matches"] = t["matches"]
                row[f"{key}_runs"] = t["stats"].get("runs")
                row[f"{key}_wickets"] = t["stats"].get("wickets")
            else:
                row[f"{key}_matches"] = row[f"{key}_runs"] = row[f"{key}_wickets"] = ""
                if t and not t["verified"]:
                    unverified.append(fmt)
        row["unverified_formats"] = " ".join(unverified)
        out.append(row)
    out.sort(key=lambda r: (ORDER[r["proposed_rarity"]], r["rank"]))

    REVIEW_FILE.parent.mkdir(exist_ok=True)
    with open(REVIEW_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out[0]))
        writer.writeheader()
        writer.writerows(out)
    if not OVERRIDES_FILE.exists():
        OVERRIDES_FILE.write_text("player_id,rarity,note\n")

    report(out, warnings)


def report(rows, warnings):
    steps = {r: i for i, r in enumerate(RARITIES)}
    print(f"\nwrote {REVIEW_FILE} ({len(rows)} candidates)")
    movers = [
        r for r in rows
        if r["status"] == "current" and r["current_rarity"] in steps
        and abs(steps[r["proposed_if_no_legends"]] - steps[r["current_rarity"]]) >= 2
    ]
    print(f"\nbiggest movers (current players, 2+ tiers, legends not counted): {len(movers)}")
    for r in sorted(movers, key=lambda r: r["rank"]):
        print(f"  {r['player']:<24} {r['current_rarity']:>9} -> {r['proposed_if_no_legends']:<9}  (score {r['score']})")
    print("\ntop tiers if every legend is approved:")
    for rarity in ("Legendary", "Epic"):
        names = [f"{r['player']}{'*' if r['status'] == 'legend' else ''}" for r in rows if r["proposed_rarity"] == rarity]
        print(f"  {rarity}: {', '.join(names)}")
    print("  (* = legend)")
    for w in warnings:
        print(f"WARNING {w}")
    print(f"tier sizes: {TIER_SIZES} (Common for everyone else)")


if __name__ == "__main__":
    main()
