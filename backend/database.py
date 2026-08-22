import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool = ConnectionPool(
    "host=localhost port=5432 dbname=cricket_marketplace user=cricket password=cricket_dev_password",
    kwargs={"row_factory": dict_row},
)

def get_connection():
    return pool.connection()