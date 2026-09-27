"""Choose the active card pool that packs roll from.

    python -m backend.scripts.build_card_pool            # apply
    python -m backend.scripts.build_card_pool --dry-run  # show what would change

Who's in the pool: every row of data/rarity_review.csv with include=yes (the
current players, plus any legend you've approved there). Without that file,
the players who currently have an active definition.

Each player's rarity: data/rarity_overrides.csv if listed there (overrides
always win), otherwise their rank by whole-career score (backend/tiering.py).
A player gets a card at their rarity and every rarity below it (a Legendary
player also has Epic, Rare and Common cards).

Idempotent. An existing definition for the same player and rarity is always
reused (most remaining supply first), even when sold out: a sold-out card stays
sold out instead of silently getting a second print run. Everything not chosen
is deactivated; its minted cards are untouched. Neither re-running this nor
re-importing Cricsheet ever changes an override.
"""

import argparse
import csv
from collections import Counter
from pathlib import Path

from backend.database import get_connection
from backend.tiering import RARITIES, assign_tiers, career_score

DATA = Path(__file__).resolve().parents[2] / "data"
REVIEW_FILE = DATA / "rarity_review.csv"
OVERRIDES_FILE = DATA / "rarity_overrides.csv"

PRINT_RUN = {"Common": 500, "Rare": 100, "Epic": 25}
LEGENDARY_PRINT_RUN_TOP4 = 5
LEGENDARY_PRINT_RUN = 10


def load_overrides(path=OVERRIDES_FILE):
    """{player_id: rarity}. Rows with an unknown rarity are an error, not ignored."""
    if not Path(path).exists():
        return {}
    overrides = {}
    with open(path, newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            if not (row.get("player_id") or "").strip():
                continue
            rarity = row["rarity"].strip().capitalize()
            if rarity not in RARITIES:
                raise ValueError(f"{path}:{line}: unknown rarity {row['rarity']!r}")
            overrides[int(row["player_id"])] = rarity
    return overrides


def load_candidates(cursor, path=REVIEW_FILE):
    """Player ids in the pool: include=yes in the review file, else current actives."""
    if Path(path).exists():
        with open(path, newline="") as f:
            return sorted({int(r["player_id"]) for r in csv.DictReader(f) if r.get("include", "").strip().lower() == "yes"})
    cursor.execute("SELECT DISTINCT player_id FROM card_definitions WHERE is_active ORDER BY 1;")
    return [r["player_id"] for r in cursor.fetchall()]


def verified_theme_stats(cursor, player_ids):
    cursor.execute(
        "SELECT player_id, theme, stats FROM player_theme_stats WHERE verified AND player_id = ANY(%s);",
        (list(player_ids),),
    )
    out = {pid: {} for pid in player_ids}
    for r in cursor.fetchall():
        out[r["player_id"]][r["theme"]] = r["stats"]
    return out


def compute_tiers(cursor, player_ids, overrides):
    stats = verified_theme_stats(cursor, player_ids)
    scores = {pid: career_score(stats[pid]) for pid in player_ids}
    final, formula, warnings = assign_tiers(scores, overrides)
    return scores, final, formula, warnings


def print_run(rarity, rank_within_legendary):
    if rarity == "Legendary":
        return LEGENDARY_PRINT_RUN_TOP4 if rank_within_legendary < 4 else LEGENDARY_PRINT_RUN
    return PRINT_RUN[rarity]


def build_pool(cursor, candidates=None, overrides=None, verbose=True):
    """Activate exactly the definitions the final tiers call for. Returns a summary."""
    candidates = load_candidates(cursor) if candidates is None else candidates
    overrides = load_overrides() if overrides is None else overrides

    # The review file names players by id. Refuse rather than silently drop an
    # approved player this database doesn't have (e.g. a legend row created
    # locally but not yet copied here).
    cursor.execute("SELECT id FROM players WHERE id = ANY(%s);", (list(candidates),))
    missing = sorted(set(candidates) - {r["id"] for r in cursor.fetchall()})
    if missing:
        raise RuntimeError(f"players in the pool but not in this database: {missing}")
    unknown_overrides = sorted(set(overrides) - set(candidates))
    if unknown_overrides and verbose:
        print(f"  note: overrides for players not in the pool are ignored: {unknown_overrides}")

    # Same lock order as pack opening (card_definitions ascending id), and no
    # user rows involved, so this can't deadlock with a pack being opened.
    cursor.execute("SELECT id FROM card_definitions ORDER BY id FOR NO KEY UPDATE;")

    scores, final, formula, warnings = compute_tiers(cursor, candidates, overrides)
    ranked = sorted(candidates, key=lambda pid: (-scores[pid], pid))

    # BASE editions only: SBC reward editions are never part of the pack pool.
    cursor.execute(
        "SELECT id, player_id, rarity, max_supply, minted_count, is_active FROM card_definitions WHERE edition = 'BASE' ORDER BY id;"
    )
    existing = {}
    for d in cursor.fetchall():
        existing.setdefault((d["player_id"], d["rarity"]), []).append(d)
    previously_active = {d["id"] for defs in existing.values() for d in defs if d["is_active"]}

    # Deactivate first so the one-active-per-player-rarity index can't trip.
    cursor.execute("UPDATE card_definitions SET is_active = false WHERE is_active;")

    active_ids, created = [], Counter()
    legendary_rank = 0
    for pid in ranked:
        top = RARITIES.index(final[pid])
        for rarity in RARITIES[: top + 1]:
            candidates_defs = existing.get((pid, rarity))
            if candidates_defs:
                chosen = max(candidates_defs, key=lambda d: (d["max_supply"] - d["minted_count"], -d["id"]))
                active_ids.append(chosen["id"])
            else:
                cursor.execute(
                    "INSERT INTO card_definitions (player_id, rarity, max_supply, is_active) VALUES (%s, %s, %s, true) RETURNING id;",
                    (pid, rarity, print_run(rarity, legendary_rank)),
                )
                active_ids.append(cursor.fetchone()["id"])
                created[rarity] += 1
        if final[pid] == "Legendary":
            legendary_rank += 1

    cursor.execute("UPDATE card_definitions SET is_active = true WHERE id = ANY(%s);", (active_ids,))

    summary = {
        "players": len(candidates),
        "tiers": Counter(final.values()),
        "overridden": {pid: r for pid, r in overrides.items() if pid in final},
        "warnings": warnings,
        "created": created,
        "deactivated": len(previously_active - set(active_ids)),
        "activated": len(set(active_ids) - previously_active),
        "active_definitions": len(active_ids),
    }
    if verbose:
        print(f"pool: {summary['players']} players, {summary['active_definitions']} active definitions")
        for rarity in reversed(RARITIES):
            print(f"  {rarity:<10} top tier for {summary['tiers'][rarity]:>3} players  ({created[rarity]} new definitions)")
        print(f"  overrides applied: {len(summary['overridden'])}")
        print(f"  deactivated: {summary['deactivated']}, newly activated: {summary['activated']}")
        for w in warnings:
            print(f"  WARNING {w}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="roll back instead of committing")
    args = parser.parse_args()
    with get_connection() as connection:
        with connection.cursor() as cursor:
            build_pool(cursor)
            if args.dry_run:
                connection.rollback()
                print("dry run: rolled back")


if __name__ == "__main__":
    main()
