-- Adds pack opening: per-definition mint counter, pack_openings table,
-- and the ledger/instance columns that link back to a pack.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/migrations/001_packs.sql
-- schema.sql already reflects the post-migration shape for fresh databases.

BEGIN;

-- Serial numbers are handed out by this counter, so it must start past any
-- serials that were created manually before it existed.
ALTER TABLE card_definitions ADD COLUMN minted_count INTEGER NOT NULL DEFAULT 0;

UPDATE card_definitions d
SET minted_count = COALESCE(
    (SELECT MAX(serial_number) FROM card_instances i WHERE i.card_definition_id = d.id),
    0
);

ALTER TABLE card_definitions
    ADD CONSTRAINT card_definitions_max_supply_positive CHECK (max_supply > 0),
    ADD CONSTRAINT card_definitions_minted_within_supply CHECK (minted_count <= max_supply),
    ADD CONSTRAINT card_definitions_rarity_check CHECK (rarity IN ('Common', 'Rare', 'Epic', 'Legendary'));

CREATE TABLE pack_openings (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id),
    pack_type   VARCHAR(20) NOT NULL,
    price       NUMERIC(12,2) NOT NULL CHECK (price > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NULL for cards minted manually through POST /card-instances.
ALTER TABLE card_instances ADD COLUMN pack_opening_id BIGINT REFERENCES pack_openings(id);

ALTER TABLE currency_ledger ADD COLUMN related_pack_opening_id BIGINT REFERENCES pack_openings(id);

ALTER TABLE currency_ledger DROP CONSTRAINT currency_ledger_reason_check;
ALTER TABLE currency_ledger ADD CONSTRAINT currency_ledger_reason_check CHECK (reason IN (
    'MINT_SIGNUP',
    'MINT_DEV_GRANT',
    'TRANSFER_PURCHASE_DEBIT',
    'TRANSFER_SALE_CREDIT',
    'BURN_MARKET_FEE',
    'BURN_PACK_PURCHASE'
));

COMMIT;
