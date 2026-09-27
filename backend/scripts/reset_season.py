"""Start a new season: rebuild the card pool from the final tiers and reset the
economy, keeping every account.

    python -m backend.scripts.reset_season            # shows what would change, changes nothing
    python -m backend.scripts.reset_season --confirm  # does it

In one transaction:
  * deletes all cards, listings, pack openings, ownership events, battles
    (rounds, moves), showcases, SBC and trade rows (when those tables exist),
    and every ledger row
  * deletes every card definition and rebuilds them from the final tiers
    (data/rarity_review.csv include=yes + data/rarity_overrides.csv), so every
    print run starts again at serial #1 (minted_count 0)
  * sets every user's balance to RESET_GRANT, recorded as a MINT_RESET_GRANT
    ledger row so ledger sums still equal balances
Keeps users, their sessions and page views, players and theme stats. Then the
economy invariants are audited.

Targets whatever DATABASE_URL points at, so check it before confirming.
"""

import argparse
import sys
from decimal import Decimal

from psycopg.conninfo import conninfo_to_dict

from backend.audit import check_invariants
from backend.database import DATABASE_URL, get_connection
from backend.main import apply_balance_change
from backend.scripts.build_card_pool import build_pool

RESET_GRANT = Decimal("1000.00")

# Emptied in one TRUNCATE. No CASCADE: if a table this list doesn't know about
# references one of these, Postgres refuses instead of silently emptying it.
ECONOMY_TABLES = [
    "battle_rounds", "battle_moves", "battles",
    "user_showcase", "listings", "card_ownership_events",
    "card_instances", "pack_openings", "currency_ledger",
]
# Later phases (SBCs, trading) add these; included when present.
OPTIONAL_TABLES = ["sbc_completions", "trade_cards", "trades"]
KEPT = ["users", "sessions", "page_views", "players", "player_theme_stats", "schema_migrations"]


def exists(conn, table):
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{table}",)).fetchone()["ok"]


def counts(conn):
    out = {}
    for table in ECONOMY_TABLES + OPTIONAL_TABLES + ["card_definitions"] + KEPT:
        out[table] = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] if exists(conn, table) else None
    out["active card_definitions"] = conn.execute("SELECT count(*) AS n FROM card_definitions WHERE is_active").fetchone()["n"]
    out["definitions with minted_count > 0"] = conn.execute(
        "SELECT count(*) AS n FROM card_definitions WHERE minted_count > 0").fetchone()["n"]
    out["users with balance <> reset grant"] = conn.execute(
        "SELECT count(*) AS n FROM users WHERE balance <> %s", (RESET_GRANT,)).fetchone()["n"]
    return out


def show(title, before, after=None):
    print(title)
    for key, n in before.items():
        kept = " (kept)" if key in KEPT else ""
        shown = lambda v: "–" if v is None else v
        if after is None:
            print(f"  {key:<36} {shown(n):>8}{kept}")
        else:
            print(f"  {key:<36} {shown(n):>8} -> {shown(after[key]):<8}{kept}")


def reset(conn, candidates=None, overrides=None):
    tables = ECONOMY_TABLES + [t for t in OPTIONAL_TABLES if exists(conn, t)]
    with conn.transaction():
        conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY")
        # Rebuild the pack pool; SBC reward editions stay (the challenges name
        # them) but their print runs start again too.
        conn.execute("DELETE FROM card_definitions WHERE edition = 'BASE'")
        conn.execute("UPDATE card_definitions SET minted_count = 0 WHERE minted_count <> 0")
        with conn.cursor() as cursor:
            summary = build_pool(cursor, candidates=candidates, overrides=overrides, verbose=False)
            cursor.execute("UPDATE users SET balance = 0")
            cursor.execute("SELECT id FROM users ORDER BY id")
            for user in cursor.fetchall():
                apply_balance_change(cursor, user["id"], RESET_GRANT, "MINT_RESET_GRANT")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", action="store_true", help="actually reset (otherwise only report)")
    args = parser.parse_args()

    target = conninfo_to_dict(DATABASE_URL)
    print(f"database: {target.get('dbname')} on {target.get('host', 'localhost')}")
    with get_connection() as conn:
        before = counts(conn)
        if not args.confirm:
            show("would reset (nothing changed; pass --confirm):", before)
            return 1
        summary = reset(conn)
        after = counts(conn)
        show("reset:", before, after)
        tiers = ", ".join(f"{r} {summary['tiers'][r]}" for r in ("Legendary", "Epic", "Rare", "Common"))
        print(f"\npool rebuilt: {summary['players']} players ({tiers}), {summary['active_definitions']} definitions, "
              f"{len(summary['overridden'])} overrides")
        for w in summary["warnings"]:
            print(f"WARNING {w}")
        failures = check_invariants(conn)
        if failures:
            print("\nAUDIT FAILED:")
            for name, rows in failures.items():
                print(f"  {name}: {rows[:5]}")
            return 2
        print("audit: all invariants hold")
        return 0


if __name__ == "__main__":
    sys.exit(main())
