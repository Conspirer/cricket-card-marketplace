"""Choose the active card pool that packs roll from.

    python -m backend.scripts.build_card_pool            # apply
    python -m backend.scripts.build_card_pool --dry-run  # show what would change

Who's in the pool: every row of data/rarity_review.csv with include=yes (the
current players and the legends), then data/pool_overrides.csv on top:
include=yes adds a player, include=no removes one, edition=legend/current
moves one between editions, and it always wins. Without the review file, the
players who currently have an active definition.

Each player's rarity: data/rarity_overrides.csv if listed there (overrides
always win), otherwise their rank by whole-career score (backend/tiering.py).
Legends (status=legend in the review file) are ranked only against each
other and are Rare, Epic or Legendary. A player gets a card at their rarity
and every rarity below it (a Legendary player also has Epic, Rare and Common
cards); a legend's cards start at Rare.

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
from backend.tiering import LEGEND_FLOOR, RARITIES, assign_tiers, career_score

DATA = Path(__file__).resolve().parents[2] / "data"
REVIEW_FILE = DATA / "rarity_review.csv"
OVERRIDES_FILE = DATA / "rarity_overrides.csv"
POOL_OVERRIDES_FILE = DATA / "pool_overrides.csv"

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


EDITIONS = ("legend", "current")


def load_pool_overrides(path=POOL_OVERRIDES_FILE):
    """{player_id: {"include": True/False/None, "edition": "legend"/"current"/None}}.
    A blank field means "no override"; any other unexpected value is an error."""
    if not Path(path).exists():
        return {}
    out = {}
    with open(path, newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            if not (row.get("player_id") or "").strip():
                continue
            include = (row.get("include") or "").strip().lower()
            edition = (row.get("edition") or "").strip().lower()
            if include not in ("yes", "no", ""):
                raise ValueError(f"{path}:{line}: include must be yes, no or blank, not {row.get('include')!r}")
            if edition not in EDITIONS + ("",):
                raise ValueError(f"{path}:{line}: edition must be legend, current or blank, not {row.get('edition')!r}")
            out[int(row["player_id"])] = {"include": {"yes": True, "no": False}.get(include), "edition": edition or None}
    return out


def apply_pool_overrides(ids, pool_overrides):
    return sorted((set(ids) | {pid for pid, o in pool_overrides.items() if o["include"] is True})
                  - {pid for pid, o in pool_overrides.items() if o["include"] is False})


def apply_edition_overrides(legend_ids, pool_overrides):
    return (set(legend_ids) | {pid for pid, o in pool_overrides.items() if o["edition"] == "legend"}) \
        - {pid for pid, o in pool_overrides.items() if o["edition"] == "current"}


def load_candidates(cursor, path=REVIEW_FILE, pool_overrides=None):
    """Player ids in the pool: include=yes in the review file (else current
    actives), then data/pool_overrides.csv, which always wins."""
    pool_overrides = load_pool_overrides() if pool_overrides is None else pool_overrides
    if Path(path).exists():
        with open(path, newline="") as f:
            ids = {int(r["player_id"]) for r in csv.DictReader(f) if r.get("include", "").strip().lower() == "yes"}
    else:
        cursor.execute("SELECT DISTINCT player_id FROM card_definitions WHERE is_active ORDER BY 1;")
        ids = {r["player_id"] for r in cursor.fetchall()}
    return apply_pool_overrides(ids, pool_overrides)


def load_legend_ids(path=REVIEW_FILE, pool_overrides=None):
    """Players the review file marks as legends (a separate edition), with
    data/pool_overrides.csv's edition column winning."""
    pool_overrides = load_pool_overrides() if pool_overrides is None else pool_overrides
    ids = set()
    if Path(path).exists():
        with open(path, newline="") as f:
            ids = {int(r["player_id"]) for r in csv.DictReader(f) if r.get("status", "").strip() == "legend"}
    return apply_edition_overrides(ids, pool_overrides)


def verified_theme_stats(cursor, player_ids):
    """{player_id: {theme: {"stats", "matches"}}}, verified rows only: an
    unverified format is absent (scored on the others), never zero."""
    cursor.execute(
        "SELECT player_id, theme, stats, matches FROM player_theme_stats WHERE verified AND player_id = ANY(%s);",
        (list(player_ids),),
    )
    out = {pid: {} for pid in player_ids}
    for r in cursor.fetchall():
        out[r["player_id"]][r["theme"]] = {"stats": r["stats"], "matches": r["matches"]}
    return out


def compute_tiers(cursor, player_ids, overrides, legends=frozenset()):
    stats = verified_theme_stats(cursor, player_ids)
    scores = {pid: career_score(stats[pid]) for pid in player_ids}
    # Included by hand but not rankable (no format with enough matches): last.
    unranked = sorted(pid for pid, s in scores.items() if s is None)
    scores = {pid: (0.0 if s is None else s) for pid, s in scores.items()}
    final, formula, warnings = assign_tiers(scores, overrides, legends)
    if unranked:
        warnings.append(f"no format with enough verified matches to rank, placed last: {unranked}")
    return scores, final, formula, warnings


def print_run(rarity, rank_within_legendary):
    if rarity == "Legendary":
        return LEGENDARY_PRINT_RUN_TOP4 if rank_within_legendary < 4 else LEGENDARY_PRINT_RUN
    return PRINT_RUN[rarity]


def build_pool(cursor, candidates=None, overrides=None, verbose=True, legends=None):
    """Activate exactly the definitions the final tiers call for. Returns a summary.
    With no candidates given, reads the review file and pool overrides (and
    the legends from the review file); given candidates, legends default to none."""
    if candidates is None:
        candidates = load_candidates(cursor)
        legends = load_legend_ids() if legends is None else legends
    legends = set(legends or ()) & set(candidates)
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

    scores, final, formula, warnings = compute_tiers(cursor, candidates, overrides, legends)
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
    legendary_rank = {"legend": 0, "current": 0}  # top-4 print runs, per edition
    for pid in ranked:
        group = "legend" if pid in legends else "current"
        top = RARITIES.index(final[pid])
        bottom = RARITIES.index(LEGEND_FLOOR) if group == "legend" else 0
        for rarity in RARITIES[bottom: top + 1]:
            candidates_defs = existing.get((pid, rarity))
            if candidates_defs:
                chosen = max(candidates_defs, key=lambda d: (d["max_supply"] - d["minted_count"], -d["id"]))
                active_ids.append(chosen["id"])
            else:
                cursor.execute(
                    "INSERT INTO card_definitions (player_id, rarity, max_supply, is_active) VALUES (%s, %s, %s, true) RETURNING id;",
                    (pid, rarity, print_run(rarity, legendary_rank[group])),
                )
                active_ids.append(cursor.fetchone()["id"])
                created[rarity] += 1
        if final[pid] == "Legendary":
            legendary_rank[group] += 1

    cursor.execute("UPDATE card_definitions SET is_active = true WHERE id = ANY(%s);", (active_ids,))

    summary = {
        "players": len(candidates),
        "legends": len(legends),
        "tiers": Counter(r for pid, r in final.items() if pid not in legends),
        "legend_tiers": Counter(r for pid, r in final.items() if pid in legends),
        "overridden": {pid: r for pid, r in overrides.items() if pid in final},
        "warnings": warnings,
        "created": created,
        "deactivated": len(previously_active - set(active_ids)),
        "activated": len(set(active_ids) - previously_active),
        "active_definitions": len(active_ids),
    }
    if verbose:
        print(f"pool: {summary['players']} players ({summary['legends']} legends), "
              f"{summary['active_definitions']} active definitions")
        for rarity in reversed(RARITIES):
            print(f"  {rarity:<10} top tier for {summary['tiers'][rarity]:>3} current players, "
                  f"{summary['legend_tiers'][rarity]:>2} legends  ({created[rarity]} new definitions)")
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
