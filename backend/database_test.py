import os

import psycopg

connection = psycopg.connect(os.environ["DATABASE_URL"])

cursor = connection.cursor()
cursor.execute("SELECT 1")

result = cursor.fetchone()

print(result)

cursor.close()
connection.close()