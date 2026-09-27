-- Trading: card-for-card swaps between two players, 1 to 3 cards a side,
-- never Runs (so the market's 5% fee can't be dodged).

BEGIN;

CREATE TABLE trades (
    id            BIGSERIAL PRIMARY KEY,
    proposer_id   BIGINT NOT NULL REFERENCES users(id),
    recipient_id  BIGINT NOT NULL REFERENCES users(id),
    status        TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN (
                      'PENDING', 'ACCEPTED', 'DECLINED', 'CANCELLED', 'EXPIRED', 'INVALID'
                  )),
    invalid_reason TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    resolved_at   TIMESTAMPTZ,
    CONSTRAINT trades_two_players CHECK (proposer_id <> recipient_id),
    CONSTRAINT trades_resolved CHECK ((status = 'PENDING') = (resolved_at IS NULL))
);
CREATE INDEX trades_proposer_idx ON trades (proposer_id, status);
CREATE INDEX trades_recipient_idx ON trades (recipient_id, status);

-- The cards on each side. from_user_id is who gives the card up: the
-- proposer for offered cards, the recipient for requested ones. Offering a
-- card doesn't lock it; accepting re-checks every row here.
CREATE TABLE trade_cards (
    trade_id          BIGINT NOT NULL REFERENCES trades(id),
    card_instance_id  BIGINT NOT NULL REFERENCES card_instances(id),
    from_user_id      BIGINT NOT NULL REFERENCES users(id),
    PRIMARY KEY (trade_id, card_instance_id)
);
CREATE INDEX trade_cards_card_idx ON trade_cards (card_instance_id);

ALTER TABLE card_ownership_events DROP CONSTRAINT card_ownership_events_event_type_check;
ALTER TABLE card_ownership_events ADD CONSTRAINT card_ownership_events_event_type_check CHECK (event_type IN (
    'MINTED', 'PULLED', 'LISTED', 'DELISTED', 'SOLD', 'BURNED', 'TRADED'
));
ALTER TABLE card_ownership_events ADD COLUMN related_trade_id BIGINT REFERENCES trades(id);

COMMIT;
