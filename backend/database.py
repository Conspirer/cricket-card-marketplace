import os

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "host=localhost port=5432 dbname=cricket_marketplace user=cricket password=cricket_dev_password",
)

pool = ConnectionPool(
    DATABASE_URL,
    kwargs={"row_factory": dict_row},
    # Test each connection before handing it out, so a Postgres restart
    # doesn't leave the pool serving dead connections.
    check=ConnectionPool.check_connection,
    open=True,
)

def get_connection():
    return pool.connection()
