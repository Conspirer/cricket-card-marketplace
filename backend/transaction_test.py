from database import get_connection


with get_connection() as connection:
    try:
        with connection.cursor() as cursor:

            cursor.execute(
                """
                INSERT INTO users (username, email)
                VALUES (%s, %s);
                """,
                ("commit_test", "commit@test.com"),
            )

            print("User inserted")

            connection.commit()
            print("Transaction committed")

    except Exception:
        connection.rollback()
        raise