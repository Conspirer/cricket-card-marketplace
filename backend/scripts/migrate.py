"""Bring the database named by DATABASE_URL up to date.

    python -m backend.scripts.migrate             # fresh DB: build it; otherwise apply pending migrations
    python -m backend.scripts.migrate --status    # show what's applied and pending
    python -m backend.scripts.migrate --baseline  # mark every migration applied, run nothing

Applied migrations are recorded in schema_migrations.
  * Fresh database (no tables): runs schema.sql, which is the full current
    schema, then records every migration as applied.
  * Existing database with history: applies pending migrations/*.sql in
    order, each in its own transaction together with its history row.
  * Existing database without history (a restored pg_dump from before this
    script existed): refuses to guess. If it's current, run --baseline.

Runs as Heroku's release phase (see Procfile), so every deploy migrates.
"""

import argparse
import re
import sys
from pathlib import Path

from backend.database import get_connection

BACKEND = Path(__file__).resolve().parents[1]
SCHEMA = BACKEND / "schema.sql"
MIGRATIONS = sorted((BACKEND / "migrations").glob("[0-9][0-9][0-9]_*.sql"))


def migration_sql(path):
    """The file's statements without its own BEGIN/COMMIT (we supply the transaction)."""
    return re.sub(r"^\s*(BEGIN|COMMIT)\s*;\s*$", "", path.read_text(), flags=re.MULTILINE | re.IGNORECASE)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version     TEXT PRIMARY KEY,
                    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cursor.execute("SELECT version FROM schema_migrations;")
            applied = {r["version"] for r in cursor.fetchall()}
            cursor.execute("SELECT to_regclass('public.users') IS NULL AS fresh;")
            fresh = cursor.fetchone()["fresh"]
        connection.commit()

        versions = [m.stem for m in MIGRATIONS]
        pending = [m for m in MIGRATIONS if m.stem not in applied]

        if args.status:
            state = "fresh (no tables)" if fresh else f"{len(applied)} migration(s) recorded"
            print(f"database: {state}")
            for m in MIGRATIONS:
                print(f"  {'applied' if m.stem in applied else 'PENDING'}  {m.name}")
            return 0

        record = "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING;"

        if fresh:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(SCHEMA.read_text())
                cursor.executemany(record, [(v,) for v in versions])
            print(f"fresh database: built from schema.sql; recorded {len(versions)} migrations as applied")
            return 0

        if args.baseline:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.executemany(record, [(v,) for v in versions])
            print(f"baseline: recorded {len(versions)} migrations as applied (nothing run)")
            return 0

        if not applied:
            print(
                "This database has tables but no migration history (e.g. a restored pg_dump).\n"
                "If its schema is current, run:  python -m backend.scripts.migrate --baseline",
                file=sys.stderr,
            )
            return 1

        if not pending:
            print("up to date")
            return 0

        for migration in pending:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(migration_sql(migration))
                cursor.execute(record, (migration.stem,))
            print(f"applied {migration.name}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
