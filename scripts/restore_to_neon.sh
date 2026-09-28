#!/usr/bin/env bash
# Replace the database at DATABASE_URL with a catalogue dump.
#
#   DATABASE_URL='postgresql://USER:PASSWORD@HOST/DB?sslmode=require' \
#     scripts/restore_to_neon.sh --confirm [--dump backups/crease_catalogue.dump]
#
# DESTRUCTIVE: drops the whole public schema (every table and row) and
# restores the dump in its place. Without --confirm it only shows what it
# would do. Use the Neon *direct* connection string (host without -pooler):
# pg_restore needs a session-mode connection. psql and pg_restore run in a
# postgres:16 container, so no local client is needed; the URL is passed to
# the container as an environment variable, never on a command line.
#
# Afterwards it prints row counts for players, active card definitions, SBC
# challenges and users.

set -euo pipefail

DUMP="backups/crease_catalogue.dump"
CONFIRM=0
while [ $# -gt 0 ]; do
  case "$1" in
    --confirm) CONFIRM=1 ;;
    --dump) DUMP="${2:?--dump needs a path}"; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set" >&2
  exit 64
fi
if [ ! -f "$DUMP" ]; then
  echo "dump not found: $DUMP" >&2
  exit 66
fi

# Show where it's going without the password.
TARGET="$(printf '%s' "$DATABASE_URL" | sed -E 's#^([a-z]+://)[^@]*@#\1***@#')"
case "$DATABASE_URL" in
  *-pooler*)
    echo "refusing: $TARGET is Neon's pooled endpoint; use the direct connection string (no -pooler)" >&2
    exit 64 ;;
esac

DUMP_DIR="$(cd "$(dirname "$DUMP")" && pwd)"
DUMP_FILE="$(basename "$DUMP")"
pg() {  # run a command in a postgres:16 container with DATABASE_URL and the dump mounted
  docker run --rm -i --network host -e DATABASE_URL -v "$DUMP_DIR:/backups:ro" postgres:16 "$@"
}

echo "target: $TARGET"
echo "dump:   $DUMP ($(du -h "$DUMP" | cut -f1))"
if [ "$CONFIRM" -ne 1 ]; then
  echo
  echo "This would DROP the public schema of the target (all tables and rows) and restore the dump."
  echo "Nothing changed. Re-run with --confirm to do it."
  exit 1
fi

echo
echo "1/3 dropping and recreating the public schema"
pg sh -c 'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA IF EXISTS public CASCADE" -c "CREATE SCHEMA public"'

echo "2/3 restoring (one transaction; any error rolls the restore back)"
pg pg_restore --no-owner --no-privileges --exit-on-error --single-transaction \
  -d "$DATABASE_URL" "/backups/$DUMP_FILE"

echo "3/3 row counts"
pg sh -c 'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X -q -P footer=off -c "
  SELECT (SELECT count(*) FROM players)                                            AS players,
         (SELECT count(*) FROM card_definitions WHERE is_active)                   AS active_card_definitions,
         (SELECT count(*) FROM sbc_challenges)                                     AS sbc_challenges,
         (SELECT count(*) FROM users)                                              AS users,
         (SELECT count(*) FROM schema_migrations)                                  AS migrations_recorded;"'
echo "done"
