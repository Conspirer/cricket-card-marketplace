"""Choose the active card pool that packs roll from.

    python -m backend.scripts.build_card_pool            # apply
    python -m backend.scripts.build_card_pool --dry-run  # show what would change

Takes the top POOL_SIZE Full Member players by overall T20 rating. Every one
gets a Common definition; the best also get Rare, Epic and Legendary.

Idempotent. An existing definition for the same player and rarity is always
reused (most remaining supply first), even when sold out: a sold-out card stays
sold out instead of silently getting a second print run. Everything not chosen
is deactivated; its minted cards are untouched.
"""

import argparse
from collections import Counter

from backend.database import get_connection
from backend.ratings import FULL_MEMBERS

POOL_SIZE = 120
MIN_T20_MATCHES = 20

# Rank cut-offs (0-based, exclusive) for each rarity above Common.
TIERS = [("Legendary", 8), ("Epic", 20), ("Rare", 40), ("Common", POOL_SIZE)]

PRINT_RUN = {"Common": 500, "Rare": 100, "Epic": 25}
LEGENDARY_PRINT_RUN_TOP4 = 5
LEGENDARY_PRINT_RUN = 10


def print_run(rarity, rank):
    if rarity == "Legendary":
        return LEGENDARY_PRINT_RUN_TOP4 if rank < 4 else LEGENDARY_PRINT_RUN
    return PRINT_RUN[rarity]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="roll back instead of committing")
    args = parser.parse_args()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Same lock order as pack opening (card_definitions ascending id),
            # and no user rows involved, so this can't deadlock with a pack.
            cursor.execute("SELECT id FROM card_definitions ORDER BY id FOR NO KEY UPDATE;")

            cursor.execute(
                """
                SELECT id, name, country, role
                FROM players
                WHERE country = ANY(%s)
                  AND (ratings -> 'T20' ->> 'overall') IS NOT NULL
                  AND (career_stats -> 'T20' ->> 'matches')::int >= %s
                ORDER BY (ratings -> 'T20' ->> 'overall')::int DESC,
                         (career_stats -> 'T20' ->> 'matches')::int DESC,
                         name
                LIMIT %s;
                """,
                (list(FULL_MEMBERS), MIN_T20_MATCHES, POOL_SIZE),
            )
            pool = cursor.fetchall()

            cursor.execute(
                """
                SELECT id, player_id, rarity, max_supply, minted_count, is_active
                FROM card_definitions
                ORDER BY id;
                """
            )
            existing = {}
            for d in cursor.fetchall():
                existing.setdefault((d["player_id"], d["rarity"]), []).append(d)
            previously_active = {
                d["id"] for defs in existing.values() for d in defs if d["is_active"]
            }

            wanted = []
            for rank, player in enumerate(pool):
                for rarity, cutoff in TIERS:
                    if rank < cutoff:
                        wanted.append((player, rarity, rank))

            # Deactivate first so the one-active-per-player-rarity index can't
            # trip while we switch definitions over.
            cursor.execute("UPDATE card_definitions SET is_active = false WHERE is_active;")

            active_ids, created = [], Counter()
            for player, rarity, rank in wanted:
                candidates = existing.get((player["id"], rarity))
                if candidates:
                    chosen = max(candidates, key=lambda d: (d["max_supply"] - d["minted_count"], -d["id"]))
                    active_ids.append(chosen["id"])
                else:
                    cursor.execute(
                        """
                        INSERT INTO card_definitions (player_id, rarity, max_supply, is_active)
                        VALUES (%s, %s, %s, true)
                        RETURNING id;
                        """,
                        (player["id"], rarity, print_run(rarity, rank)),
                    )
                    active_ids.append(cursor.fetchone()["id"])
                    created[rarity] += 1

            cursor.execute(
                "UPDATE card_definitions SET is_active = true WHERE id = ANY(%s);",
                (active_ids,),
            )

            cursor.execute(
                """
                SELECT rarity, count(*) AS n, sum(max_supply - minted_count) AS remaining
                FROM card_definitions
                WHERE is_active
                GROUP BY rarity;
                """
            )
            summary = {row["rarity"]: row for row in cursor.fetchall()}

            now_active = set(active_ids)
            print(f"pool: {len(pool)} players, {len(now_active)} active definitions")
            for rarity, _ in reversed(TIERS):
                row = summary.get(rarity, {"n": 0, "remaining": 0})
                print(f"  {rarity:<10} {row['n']:>4} definitions  {row['remaining']:>6} cards left  ({created[rarity]} new)")
            print(f"  roles: {dict(Counter(p['role'] for p in pool))}")
            print(f"  deactivated: {len(previously_active - now_active)}, newly activated: {len(now_active - previously_active)}")

            if args.dry_run:
                connection.rollback()
                print("dry run: rolled back")


if __name__ == "__main__":
    main()
