import os

from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Always from the environment (Render: set it to the Neon connection string;
# locally: export it, see README). No credentials live in the code.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Locally: "
        "export DATABASE_URL=postgresql://cricket:<password>@localhost:5432/cricket_marketplace"
    )

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def connection_options(url):
    """SSL and prepared-statement settings for a database URL.

    Hosted Postgres (Neon) only accepts SSL: require it for any non-local host
    unless the URL or DATABASE_SSLMODE says otherwise. Neon's pooled endpoint
    ("-pooler" host) is PgBouncer in transaction mode, where server-side
    prepared statements aren't safe, so psycopg's automatic ones are turned off.
    """
    params = conninfo_to_dict(url)
    host = params.get("host", "")
    options = {}
    if "sslmode" not in params:
        options["sslmode"] = os.environ.get("DATABASE_SSLMODE") or ("prefer" if host in LOCAL_HOSTS else "require")
    if "-pooler" in host:
        options["prepare_threshold"] = None
    return options


# Free Postgres plans allow few connections, so keep the pool small.
POOL_MIN = int(os.environ.get("DB_POOL_MIN", "1"))
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "5"))

pool = ConnectionPool(
    DATABASE_URL,
    min_size=POOL_MIN,
    max_size=POOL_MAX,
    kwargs={"row_factory": dict_row, **connection_options(DATABASE_URL)},
    # Test each connection before handing it out, so a Postgres restart
    # doesn't leave the pool serving dead connections.
    check=ConnectionPool.check_connection,
    open=True,
)

def get_connection():
    return pool.connection()
