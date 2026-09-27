"""Seed SBC challenges and their reward editions from data/sbc_challenges.json.

    python -m backend.scripts.seed_sbcs            # upsert, then check completability
    python -m backend.scripts.seed_sbcs --check    # only check, change nothing

Challenges are upserted by slug and reward editions by key, so re-running is
safe. Completability: for each challenge, search the active BASE pool (every
card a pack can produce) for a set of cards that meets every requirement, and
print an example. A challenge nobody can complete from the pool is reported.
"""

import argparse
import itertools
import json
import random
from pathlib import Path

from psycopg.types.json import Jsonb

from backend import sbc
from backend.database import get_connection

FILE = Path(__file__).resolve().parents[2] / "data" / "sbc_challenges.json"
SEARCH_TOP = 18          # try every combination of the most promising cards...
RANDOM_TRIES = 20000     # ...then random samples of the filtered pool


def upsert(cursor, data):
    for e in data["editions"]:
        cursor.execute("SELECT id FROM players WHERE wikipedia_title = %s OR name = %s ORDER BY (wikipedia_title = %s) DESC, id LIMIT 1;",
                       (e["player"], e["player"], e["player"]))
        player = cursor.fetchone()
        if player is None:
            raise SystemExit(f"edition {e['key']}: no player {e['player']!r}")
        cursor.execute(
            """
            INSERT INTO card_definitions (player_id, rarity, max_supply, is_active, edition, edition_key, edition_label)
            VALUES (%s, %s, %s, false, 'SBC', %s, %s)
            ON CONFLICT (edition_key) DO UPDATE SET
                edition_label = EXCLUDED.edition_label,
                -- never below what's already been minted
                max_supply = GREATEST(card_definitions.minted_count, EXCLUDED.max_supply);
            """,
            (player["id"], e["rarity"], e["print_run"], e["key"], e["label"]),
        )
    for c in data["challenges"]:
        sbc.validate_rules(c["requirements"])
        cursor.execute(
            """
            INSERT INTO sbc_challenges (slug, title, description, requirements, reward, starts_at, ends_at,
                                        max_completions_per_user, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET
                title = EXCLUDED.title, description = EXCLUDED.description,
                requirements = EXCLUDED.requirements, reward = EXCLUDED.reward,
                starts_at = EXCLUDED.starts_at, ends_at = EXCLUDED.ends_at,
                max_completions_per_user = EXCLUDED.max_completions_per_user, active = EXCLUDED.active;
            """,
            (c["slug"], c["title"], c["description"], Jsonb(c["requirements"]), Jsonb(c["reward"]),
             c.get("starts_at"), c.get("ends_at"), c.get("max_completions_per_user", 1), c.get("active", True)),
        )


def pool_cards(cursor):
    """One engine card per active BASE definition (what packs can give you)."""
    cursor.execute(
        """
        SELECT d.id AS card_id, d.rarity, p.id AS player_id, p.name AS player_name, p.country, p.role,
               COALESCE((SELECT jsonb_object_agg(t.theme, jsonb_build_object(
                             'verified', t.verified, 'matches', t.matches, 'stats', t.stats))
                         FROM player_theme_stats t WHERE t.player_id = p.id), '{}'::jsonb) AS theme_stats
        FROM card_definitions d JOIN players p ON p.id = d.player_id
        WHERE d.is_active AND d.edition = 'BASE';
        """
    )
    return cursor.fetchall()


def find_lineup(rules, pool, rng):
    n = sbc.required_count(rules)
    # Keep only cards that could help a rule every card must satisfy.
    candidates = pool
    for r in rules:
        if r["type"] == "min_rarity" and not r.get("min"):
            candidates = [c for c in candidates if sbc._rank(c["rarity"]) >= sbc._rank(r["rarity"])]
        if r["type"] in ("role", "played_in") and r["min"] == n:
            test = (lambda c, r=r: c["role"] == r["role"]) if r["type"] == "role" else (lambda c, r=r: sbc._played(c, r["theme"]))
            candidates = [c for c in candidates if test(c)]
    combined = [r for r in rules if r["type"] == "combined_stat"]
    if combined:
        r = combined[0]
        candidates = sorted(candidates, key=lambda c: -sbc._stat(c, r["theme"], r["stat"]))
    for combo in itertools.combinations(candidates[:SEARCH_TOP], n):
        if sbc.all_met(sbc.evaluate(rules, list(combo))):
            return list(combo)
    for _ in range(RANDOM_TRIES):
        if len(candidates) < n:
            break
        combo = rng.sample(candidates, n)
        if sbc.all_met(sbc.evaluate(rules, combo)):
            return combo
    return None


def check(cursor, data):
    pool = pool_cards(cursor)
    rng = random.Random(1)
    ok = True
    print(f"completability against the active pack pool ({len(pool)} definitions):")
    for c in data["challenges"]:
        lineup = find_lineup(c["requirements"], pool, rng)
        if lineup:
            print(f"  ok   {c['title']:<18} e.g. " + ", ".join(f"{x['player_name']} ({x['rarity']}, {x['country']})" for x in lineup))
        else:
            ok = False
            print(f"  FAIL {c['title']:<18} no set of pool cards meets every requirement: replace it")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only check completability")
    args = parser.parse_args()
    data = json.loads(FILE.read_text())
    with get_connection() as connection:
        with connection.cursor() as cursor:
            if not args.check:
                upsert(cursor, data)
                print(f"seeded {len(data['challenges'])} challenges, {len(data['editions'])} reward editions")
            check(cursor, data)


if __name__ == "__main__":
    main()
