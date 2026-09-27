-- Squad building challenges: submit cards that meet a challenge's
-- requirements; they're destroyed (burned) and you get a reward.

BEGIN;

-- Burned cards stay in the table (history, serials) but leave the game.
ALTER TABLE card_instances ADD COLUMN burned_at TIMESTAMPTZ;

ALTER TABLE card_ownership_events DROP CONSTRAINT card_ownership_events_event_type_check;
ALTER TABLE card_ownership_events ADD CONSTRAINT card_ownership_events_event_type_check CHECK (event_type IN (
    'MINTED', 'PULLED', 'LISTED', 'DELISTED', 'SOLD', 'BURNED'
));

-- BASE definitions are the pack pool. SBC editions ("MS Dhoni · 2011 Final")
-- are reward-only: never rolled by packs, small print runs, minted through the
-- same guarded counter. edition_key names them from the challenge file.
ALTER TABLE card_definitions
    ADD COLUMN edition        TEXT NOT NULL DEFAULT 'BASE',
    ADD COLUMN edition_key    TEXT UNIQUE,
    ADD COLUMN edition_label  TEXT,
    ADD CONSTRAINT card_definitions_edition_check CHECK (edition IN ('BASE', 'SBC')),
    ADD CONSTRAINT card_definitions_sbc_named CHECK (
        edition = 'BASE' OR (edition_key IS NOT NULL AND edition_label IS NOT NULL AND NOT is_active)
    );

ALTER TABLE currency_ledger DROP CONSTRAINT currency_ledger_reason_check;
ALTER TABLE currency_ledger ADD CONSTRAINT currency_ledger_reason_check CHECK (reason IN (
    'MINT_SIGNUP',
    'MINT_DEV_GRANT',
    'MINT_RESET_GRANT',
    'MINT_SBC_REWARD',
    'TRANSFER_PURCHASE_DEBIT',
    'TRANSFER_SALE_CREDIT',
    'BURN_MARKET_FEE',
    'BURN_PACK_PURCHASE'
));
ALTER TABLE currency_ledger ADD COLUMN related_sbc_completion_id BIGINT;

CREATE TABLE sbc_challenges (
    id                        BIGSERIAL PRIMARY KEY,
    slug                      TEXT NOT NULL UNIQUE,
    title                     TEXT NOT NULL,
    description               TEXT NOT NULL,
    requirements              JSONB NOT NULL,   -- list of rules, see backend/sbc.py
    reward                    JSONB NOT NULL,   -- {"cards": [edition_key, ...], "runs": n}
    starts_at                 TIMESTAMPTZ,
    ends_at                   TIMESTAMPTZ,
    max_completions_per_user  INTEGER NOT NULL DEFAULT 1 CHECK (max_completions_per_user > 0),
    active                    BOOLEAN NOT NULL DEFAULT true,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sbc_completions (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(id),
    challenge_id        BIGINT NOT NULL REFERENCES sbc_challenges(id),
    submitted_card_ids  BIGINT[] NOT NULL,
    reward_refs         JSONB NOT NULL,     -- {"cards": [card_instance_id, ...], "runs": n}
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX sbc_completions_user_idx ON sbc_completions (user_id, challenge_id);

ALTER TABLE currency_ledger ADD CONSTRAINT currency_ledger_related_sbc_completion_fkey
    FOREIGN KEY (related_sbc_completion_id) REFERENCES sbc_completions(id);

COMMIT;
