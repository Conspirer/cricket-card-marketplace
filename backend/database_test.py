import psycopg

connection = psycopg.connect(
    host="localhost",
    port=5432,
    dbname="cricket_marketplace",
    user="cricket",
    password="cricket_dev_password",
)

cursor = connection.cursor()
cursor.execute("SELECT 1")

result = cursor.fetchone()

print(result)

cursor.close()
connection.close()