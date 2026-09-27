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
            WHERE e.card_instance_id = i.id AND e.event_type IN ('MINTED', 'PULLED', 'SOLD', 'TRADED')
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


CHECKS["burned cards have a BURNED event, and only they do"] = """
    SELECT i.id, i.burned_at
    FROM card_instances i
    LEFT JOIN card_ownership_events e ON e.card_instance_id = i.id AND e.event_type = 'BURNED'
    GROUP BY i.id
    HAVING (i.burned_at IS NOT NULL) <> (count(e.id) = 1) OR count(e.id) > 1
"""
CHECKS["no burned card is listed or in a live battle deck"] = """
    SELECT i.id
    FROM card_instances i
    WHERE i.burned_at IS NOT NULL
      AND (EXISTS (SELECT 1 FROM listings l WHERE l.card_instance_id = i.id AND l.status = 'ACTIVE')
           OR EXISTS (SELECT 1 FROM battles b WHERE b.status IN ('PENDING', 'ACTIVE')
                        AND i.id = ANY (b.challenger_card_ids || COALESCE(b.opponent_card_ids, '{}'))))
"""
CHECKS["SBC reward Runs match a completion"] = """
    SELECT l.id FROM currency_ledger l
    WHERE l.reason = 'MINT_SBC_REWARD'
      AND NOT EXISTS (SELECT 1 FROM sbc_completions c
                      WHERE c.id = l.related_sbc_completion_id AND c.user_id = l.user_id
                        AND (c.reward_refs ->> 'runs')::numeric = l.delta)
"""

CHECKS["every TRADED event belongs to an accepted trade, moving a card from its giver to the other side"] = """
    SELECT e.id FROM card_ownership_events e
    LEFT JOIN trades t ON t.id = e.related_trade_id
    LEFT JOIN trade_cards tc ON tc.trade_id = t.id AND tc.card_instance_id = e.card_instance_id
    WHERE e.event_type = 'TRADED'
      AND (t.status IS DISTINCT FROM 'ACCEPTED' OR tc.card_instance_id IS NULL
           OR e.from_user_id <> tc.from_user_id
           OR e.to_user_id <> CASE WHEN tc.from_user_id = t.proposer_id THEN t.recipient_id ELSE t.proposer_id END)
"""
CHECKS["every card in an accepted trade has exactly one TRADED event for it"] = """
    SELECT t.id, tc.card_instance_id FROM trades t
    JOIN trade_cards tc ON tc.trade_id = t.id
    LEFT JOIN card_ownership_events e
        ON e.related_trade_id = t.id AND e.card_instance_id = tc.card_instance_id AND e.event_type = 'TRADED'
    WHERE t.status = 'ACCEPTED'
    GROUP BY t.id, tc.card_instance_id HAVING count(e.id) <> 1
"""
CHECKS["trades have 1 to 3 cards a side, given by the two players"] = """
    SELECT t.id FROM trades t LEFT JOIN trade_cards tc ON tc.trade_id = t.id
    GROUP BY t.id
    HAVING count(*) FILTER (WHERE tc.from_user_id = t.proposer_id) NOT BETWEEN 1 AND 3
        OR count(*) FILTER (WHERE tc.from_user_id = t.recipient_id) NOT BETWEEN 1 AND 3
        OR count(*) FILTER (WHERE tc.from_user_id NOT IN (t.proposer_id, t.recipient_id)) > 0
"""


def check_invariants(conn):
    """{check name: offending rows} for every check that fails."""
    failures = {}
    for name, sql in CHECKS.items():
        rows = conn.execute(sql).fetchall()
        if rows:
            failures[name] = rows
    return failures
