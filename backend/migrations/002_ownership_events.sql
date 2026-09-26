-- Append-only history of everything that happens to a card instance.
-- card_instances.owner_id is a cached "current state"; this log is the record
-- of how the card got there. Every write path appends here in the same
-- transaction as the change it describes.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/migrations/002_ownership_events.sql

BEGIN;

CREATE TABLE card_ownership_events (
    id                       BIGSERIAL PRIMARY KEY,
    card_instance_id         BIGINT NOT NULL REFERENCES card_instances(id),
    event_type               VARCHAR(20) NOT NULL CHECK (event_type IN (
                                 'MINTED',    -- created manually via POST /card-instances
                                 'PULLED',    -- created by opening a pack
                                 'LISTED',
                                 'DELISTED',
                                 'SOLD'
                             )),
    from_user_id             BIGINT REFERENCES users(id),  -- NULL for MINTED/PULLED
    to_user_id               BIGINT REFERENCES users(id),  -- NULL for LISTED/DELISTED
    price                    NUMERIC(12,2),
    related_listing_id       BIGINT REFERENCES listings(id),
    related_pack_opening_id  BIGINT REFERENCES pack_openings(id),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX card_ownership_events_instance_idx
    ON card_ownership_events (card_instance_id, created_at);

-- ---------------------------------------------------------------------------
-- Backfill from existing data.
-- ---------------------------------------------------------------------------

-- Creation events. Cards only change hands through sales, so the original
-- owner is the seller of the card's first listing, or the current owner if it
-- was never listed.
INSERT INTO card_ownership_events
    (card_instance_id, event_type, to_user_id, related_pack_opening_id, created_at)
SELECT
    i.id,
    CASE WHEN i.pack_opening_id IS NULL THEN 'MINTED' ELSE 'PULLED' END,
    COALESCE(
        (SELECT l.seller_id FROM listings l
         WHERE l.card_instance_id = i.id
         ORDER BY l.created_at LIMIT 1),
        i.owner_id
    ),
    i.pack_opening_id,
    i.created_at
FROM card_instances i;

INSERT INTO card_ownership_events
    (card_instance_id, event_type, from_user_id, price, related_listing_id, created_at)
SELECT card_instance_id, 'LISTED', seller_id, price, id, created_at
FROM listings;

INSERT INTO card_ownership_events
    (card_instance_id, event_type, from_user_id, price, related_listing_id, created_at)
SELECT card_instance_id, 'DELISTED', seller_id, price, id, resolved_at
FROM listings
WHERE status = 'CANCELLED';

-- The buyer wasn't stored on listings, but it's recoverable: whoever listed
-- the card next, or its current owner if nobody has listed it since.
INSERT INTO card_ownership_events
    (card_instance_id, event_type, from_user_id, to_user_id, price, related_listing_id, created_at)
SELECT
    l.card_instance_id,
    'SOLD',
    l.seller_id,
    COALESCE(
        (SELECT nxt.seller_id FROM listings nxt
         WHERE nxt.card_instance_id = l.card_instance_id
           AND nxt.created_at > l.resolved_at
         ORDER BY nxt.created_at LIMIT 1),
        i.owner_id
    ),
    l.price,
    l.id,
    l.resolved_at
FROM listings l
JOIN card_instances i ON i.id = l.card_instance_id
WHERE l.status = 'SOLD';

COMMIT;
