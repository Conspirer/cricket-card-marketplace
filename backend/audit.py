"""Economy invariants. Used by the test suite after every test and by
scripts that rewrite data (e.g. reset_economy). Each check returns the
offending rows; an empty result everywhere means the database is sound."""

CHECKS = {
    "ledger sum equals balance": """
        SELECT u.id, u.balance, COALESCE(SUM(l.delta), 0) AS ledger_total
        FROM users u LEFT JOIN currency_ledger l ON l.user_id = u.id
        GROUP BY u.id HAVING u.balance <> COALESCE(SUM(l.delta), 0)
    """,
    "owner equals latest ownership event": """
        SELECT i.id, i.owner_id, last.to_user_id
        FROM card_instances i
        LEFT JOIN LATERAL (
            SELECT to_user_id FROM card_ownership_events e
            WHERE e.card_instance_id = i.id AND e.event_type IN ('MINTED', 'PULLED', 'SOLD')
            ORDER BY e.created_at DESC, e.id DESC LIMIT 1
        ) last ON true
        WHERE i.owner_id IS DISTINCT FROM last.to_user_id
    """,
    "serials unique and gap-free (1..minted_count)": """
        SELECT d.id, d.minted_count, count(i.id) AS instances,
               count(DISTINCT i.serial_number) AS distinct_serials,
               COALESCE(max(i.serial_number), 0) AS max_serial
        FROM card_definitions d LEFT JOIN card_instances i ON i.card_definition_id = d.id
        GROUP BY d.id
        HAVING NOT (d.minted_count = count(i.id)
                    AND count(i.id) = count(DISTINCT i.serial_number)
                    AND COALESCE(max(i.serial_number), 0) = d.minted_count)
    """,
    "sale credits matched by equal purchase debits": """
        SELECT c.related_listing_id
        FROM currency_ledger c
        LEFT JOIN currency_ledger d
            ON d.related_listing_id = c.related_listing_id AND d.reason = 'TRANSFER_PURCHASE_DEBIT'
        WHERE c.reason = 'TRANSFER_SALE_CREDIT' AND (d.id IS NULL OR d.delta <> -c.delta)
    """,
}


def check_invariants(conn):
    """{check name: offending rows} for every check that fails."""
    failures = {}
    for name, sql in CHECKS.items():
        rows = conn.execute(sql).fetchall()
        if rows:
            failures[name] = rows
    return failures
