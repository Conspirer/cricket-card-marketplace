import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Always from the environment: Heroku sets DATABASE_URL; locally, export it
# (see README). No credentials live in the code.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Locally: "
        "export DATABASE_URL=postgresql://cricket:<password>@localhost:5432/cricket_marketplace"
    )

# Heroku Postgres only accepts SSL connections. On a dyno (DYNO is set) require
# it; elsewhere use libpq's default, which works for the local Docker database.
SSLMODE = os.environ.get("DATABASE_SSLMODE", "require" if os.environ.get("DYNO") else "prefer")

# Small Postgres plans allow few connections (Heroku's smallest: 20, shared
# with release-phase scripts and `heroku pg:psql`), so keep the pool small.
POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "5"))

pool = ConnectionPool(
    DATABASE_URL,
    min_size=POOL_MIN,
    max_size=POOL_MAX,
    kwargs={"row_factory": dict_row, "sslmode": SSLMODE},
    # Test each connection before handing it out, so a Postgres restart
    # doesn't leave the pool serving dead connections.
    check=ConnectionPool.check_connection,
    open=True,
)

def get_connection():
    return pool.connection()
