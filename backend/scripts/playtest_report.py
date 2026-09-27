"""Playtest report: who played, how much, and whether they came back.

    python -m backend.scripts.playtest_report

Per user: days active, packs opened, battles played (finished or forfeited),
whether they started another battle within REMATCH_WINDOW of finishing one,
and whether they returned on a later day. "Active on a day" means any of:
a page view (page_views), signing up, opening a pack, listing a card, or
taking part in a battle. Days are UTC. No third-party analytics: everything
comes from tables the app already writes.
"""

from datetime import timedelta

from backend.database import get_connection

REMATCH_WINDOW = timedelta(minutes=30)

REPORT_SQL = """
WITH activity AS (
    SELECT user_id, day FROM page_views
    UNION SELECT id, (created_at AT TIME ZONE 'UTC')::date FROM users
    UNION SELECT user_id, (created_at AT TIME ZONE 'UTC')::date FROM pack_openings
    UNION SELECT seller_id, (created_at AT TIME ZONE 'UTC')::date FROM listings
    UNION SELECT u, (b.created_at AT TIME ZONE 'UTC')::date
          FROM battles b CROSS JOIN LATERAL (VALUES (b.challenger_id), (b.opponent_id)) AS v(u)
          WHERE b.status IN ('ACTIVE', 'FINISHED', 'FORFEIT')
),
played AS (
    SELECT u AS user_id, b.id, b.started_at, b.finished_at
    FROM battles b CROSS JOIN LATERAL (VALUES (b.challenger_id), (b.opponent_id)) AS v(u)
    WHERE b.started_at IS NOT NULL
)
SELECT
    u.username,
    (SELECT count(DISTINCT day) FROM activity a WHERE a.user_id = u.id) AS days_active,
    (SELECT min(day) FROM activity a WHERE a.user_id = u.id) AS first_day,
    (SELECT max(day) FROM activity a WHERE a.user_id = u.id) AS last_day,
    (SELECT count(*) FROM pack_openings po WHERE po.user_id = u.id) AS packs_opened,
    (SELECT count(*) FROM played p WHERE p.user_id = u.id AND p.finished_at IS NOT NULL) AS battles_played,
    EXISTS (
        SELECT 1 FROM played first JOIN played second
            ON second.user_id = first.user_id AND second.id <> first.id
        WHERE first.user_id = u.id
          AND first.finished_at IS NOT NULL
          AND second.started_at > first.finished_at
          AND second.started_at <= first.finished_at + %(window)s
    ) AS rematched
FROM users u
ORDER BY u.id;
"""


def build_report(connection):
    rows = connection.execute(REPORT_SQL, {"window": REMATCH_WINDOW}).fetchall()
    for row in rows:
        row["returned"] = bool(row["last_day"] and row["first_day"] and row["last_day"] > row["first_day"])
    return rows


def main():
    with get_connection() as connection:
        rows = build_report(connection)

    if not rows:
        print("no users yet")
        return
    header = f"{'user':<20} {'days':>4} {'first day':>10} {'packs':>5} {'battles':>7}  {'rematch':<7}  returned"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['username']:<20} {r['days_active']:>4} {str(r['first_day'] or '–'):>10} "
            f"{r['packs_opened']:>5} {r['battles_played']:>7}  {'yes' if r['rematched'] else 'no':<7}  "
            f"{'yes' if r['returned'] else 'no'}"
        )
    n = len(rows)
    print("-" * len(header))
    print(
        f"{n} players · {sum(r['battles_played'] > 0 for r in rows)} battled · "
        f"{sum(r['rematched'] for r in rows)} rematched within {int(REMATCH_WINDOW.total_seconds() // 60)} min · "
        f"{sum(r['returned'] for r in rows)} came back on a later day"
    )


if __name__ == "__main__":
    main()
