"""Link players to ESPNcricinfo, Wikidata and English Wikipedia; fix names.

    python -m backend.scripts.link_players            # players that have card definitions
    python -m backend.scripts.link_players --all      # every imported player
    python -m backend.scripts.link_players --dry-run

The Cricsheet register gives each player's ESPNcricinfo id; Wikidata maps that
id (property P2697) to an entity with an English label and a Wikipedia
article. Names become the article title without its disambiguator (Wikipedia
titles use the common name: "Babar Azam", where the Wikidata label is the
formal "Mohammad Babar Azam"), else the label. "SA Yadav" -> "Suryakumar
Yadav". Players that can't be resolved are listed, not guessed.
"""

import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request

from backend.database import get_connection
from backend.scripts.import_cricsheet import DATA_DIR
from backend.scripts.wikipedia import open_json

SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = "CreaseCardGame/0.1 (personal project; contact via github.com/Conspirer)"
BATCH = 200


def cricinfo_ids():
    ids = {}
    with open(DATA_DIR / "people.csv", newline="") as f:
        for row in csv.DictReader(f):
            if row["key_cricinfo"]:
                ids[row["identifier"]] = row["key_cricinfo"]
    return ids


def wikidata_lookup(cids):
    """{cricinfo_id: (qid, label, wikipedia_title | None)}"""
    found = {}
    cids = sorted(set(cids))
    for start in range(0, len(cids), BATCH):
        values = " ".join(f'"{c}"' for c in cids[start:start + BATCH])
        query = f"""
            SELECT ?item ?itemLabel ?cid ?article WHERE {{
              VALUES ?cid {{ {values} }}
              ?item wdt:P2697 ?cid .
              OPTIONAL {{ ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }}
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }}"""
        request = urllib.request.Request(
            SPARQL + "?" + urllib.parse.urlencode({"query": query}),
            headers={"Accept": "application/sparql-results+json", "User-Agent": USER_AGENT},
        )
        rows = open_json(request)["results"]["bindings"]
        for row in rows:
            cid = row["cid"]["value"]
            qid = row["item"]["value"].rsplit("/", 1)[-1]
            label = row["itemLabel"]["value"]
            title = None
            if "article" in row:
                title = urllib.parse.unquote(row["article"]["value"].rsplit("/wiki/", 1)[-1]).replace("_", " ")
            # A label equal to the QID means Wikidata has no English label.
            if label == qid:
                label = None
            if cid in found and found[cid][0] != qid:
                found[cid] = None  # ambiguous: two entities claim the same id
            elif cid not in found:
                found[cid] = (qid, label, title)
        time.sleep(1)  # be polite to the public endpoint
    return found


def link(cursor, players, register=None):
    """Link players (rows with id, name, cricsheet_id) to ESPNcricinfo,
    Wikidata and Wikipedia, renaming them to their common name.
    Returns (renamed, unresolved, missing_article)."""
    register = register or cricinfo_ids()
    lookup = wikidata_lookup(register[p["cricsheet_id"]] for p in players if p["cricsheet_id"] in register)

    renamed, unresolved = [], []
    for p in players:
        cid = register.get(p["cricsheet_id"])
        hit = lookup.get(cid) if cid else None
        if not hit:
            reason = "no ESPNcricinfo id in register" if not cid else (
                "ambiguous Wikidata match" if cid in lookup else "not found on Wikidata")
            unresolved.append((p["name"], reason))
            cursor.execute("UPDATE players SET cricinfo_id = %s WHERE id = %s;", (cid, p["id"]))
            continue

        qid, label, title = hit
        common = re.sub(r"\s*\([^)]*\)$", "", title) if title else None
        new_name = common or label or p["name"]
        if new_name != p["name"]:
            renamed.append((p["name"], new_name))
        cursor.execute(
            """
            UPDATE players
            SET name = %s, cricinfo_id = %s, wikidata_id = %s, wikipedia_title = %s
            WHERE id = %s;
            """,
            (new_name, cid, qid, title, p["id"]),
        )
    missing_article = [p["name"] for p in players if (lookup.get(register.get(p["cricsheet_id"])) or (0, 0, 1))[2] is None]
    return renamed, unresolved, missing_article


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="every player with a Cricsheet id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, cricsheet_id FROM players
                WHERE cricsheet_id IS NOT NULL
                  AND (%s OR id IN (SELECT player_id FROM card_definitions))
                ORDER BY id;
                """,
                (args.all,),
            )
            players = cursor.fetchall()
            renamed, unresolved, missing_article = link(cursor, players)

            print(f"{len(players)} players checked, {len(renamed)} renamed, {len(unresolved)} unresolved")
            for old, new in renamed:
                print(f"  renamed  {old!r} -> {new!r}")
            for name, reason in unresolved:
                print(f"  UNRESOLVED {name!r}: {reason}")
            for name in missing_article:
                print(f"  no English Wikipedia article: {name!r}")

            if args.dry_run:
                connection.rollback()
                print("dry run: rolled back")


if __name__ == "__main__":
    main()
