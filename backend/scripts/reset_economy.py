"""Wipe the economy: users, cards, trades, packs, ledger, history and battles.

    python -m backend.scripts.reset_economy            # shows what would be deleted, changes nothing
    python -m backend.scripts.reset_economy --confirm  # does it

Empties users (and their login sessions, showcases and page views), card_instances, listings,
pack_openings, currency_ledger, card_ownership_events and every battle table, and resets
card_definitions.minted_count to 0 so serials start again at #1. Keeps
players, player_theme_stats, card_definitions (including which are active)
and schema_migrations. Everything happens in one transaction, then the
economy invariants are audited.

Targets whatever DATABASE_URL points at, so check it before confirming.
"""

import argparse
import sys

from psycopg.conninfo import conninfo_to_dict

from backend.audit import check_invariants
from backend.database import DATABASE_URL, get_connection

# Order doesn't matter to one TRUNCATE, but no CASCADE: if a kept table ever
# references one of these, Postgres refuses instead of silently emptying it.
EMPTIED = [
    "battle_rounds", "battle_moves", "battles",
    "card_ownership_events", "currency_ledger", "listings",
    "user_showcase", "page_views", "sbc_completions", "trade_cards", "trades",
    "card_instances", "pack_openings", "sessions", "users",
]
KEPT = ["players", "player_theme_stats", "card_definitions", "schema_migrations"]


def counts(conn):
    out = {}
    for table in EMPTIED + KEPT:
        exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{table}",)).fetchone()["ok"]
        out[table] = conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] if exists else None
    out["card_definitions with minted_count > 0"] = conn.execute(
        "SELECT count(*) AS n FROM card_definitions WHERE minted_count > 0"
    ).fetchone()["n"]
    return out


def show(title, before, after=None):
    print(title)
    for table, n in before.items():
        kept = " (kept)" if table in KEPT else ""
        if after is None:
            print(f"  {table:<40} {'–' if n is None else n:>8}{kept}")
        else:
            m = after[table]
            print(f"  {table:<40} {'–' if n is None else n:>8} -> {'–' if m is None else m:<8}{kept}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", action="store_true", help="actually delete (otherwise only report)")
    args = parser.parse_args()

    target = conninfo_to_dict(DATABASE_URL)
    print(f"database: {target.get('dbname')} on {target.get('host', 'localhost')}")

    with get_connection() as conn:
        before = counts(conn)
        if not args.confirm:
            show("would empty (nothing changed; pass --confirm to reset):", before)
            return 1

        with conn.transaction():
            conn.execute(f"TRUNCATE {', '.join(EMPTIED)} RESTART IDENTITY")
            conn.execute("UPDATE card_definitions SET minted_count = 0 WHERE minted_count <> 0")
        after = counts(conn)
        show("reset:", before, after)

        failures = check_invariants(conn)
        if failures:
            print("\nAUDIT FAILED:")
            for name, rows in failures.items():
                print(f"  {name}: {rows[:5]}")
            return 2
        print("\naudit: all invariants hold")
        return 0


if __name__ == "__main__":
    sys.exit(main())
