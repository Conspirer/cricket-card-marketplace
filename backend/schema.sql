-- Source of truth for the Postgres schema (fresh databases).
-- Existing databases: apply files in migrations/ in order instead.
-- Safe to re-run: every statement is IF NOT EXISTS.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/schema.sql

CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL UNIQUE,
    balance     NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_balance_non_negative CHECK (balance >= 0)
);

CREATE TABLE IF NOT EXISTS players (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    country       TEXT NOT NULL,
    role          TEXT NOT NULL,              -- Batter | Bowler | All-rounder (derived on import)
    cricsheet_id  TEXT UNIQUE,                -- NULL for hand-made players
    batting_hand  TEXT,                       -- not in Cricsheet; filled manually
    bowling_type  TEXT,                       -- not in Cricsheet; filled manually
    is_keeper     BOOLEAN,                    -- not in Cricsheet; filled manually
    career_stats  JSONB,                      -- {"T20" | "ODI" | "Test": {...}}
    ratings       JSONB,                      -- {"T20" | "ODI" | "Test": {"technique": 0-99, ...}}
    cricinfo_id      TEXT,
    wikidata_id      TEXT,
    wikipedia_title  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS card_definitions (
    id          BIGSERIAL PRIMARY KEY,
    player_id   BIGINT NOT NULL REFERENCES players(id),
    rarity      TEXT NOT NULL,
    max_supply  INTEGER NOT NULL,
    -- Last serial handed out; the next mint gets minted_count + 1.
    minted_count INTEGER NOT NULL DEFAULT 0,
    is_active   BOOLEAN NOT NULL DEFAULT false,  -- in the pack pool (scripts/build_card_pool.py)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT card_definitions_max_supply_positive CHECK (max_supply > 0),
    CONSTRAINT card_definitions_minted_within_supply CHECK (minted_count <= max_supply),
    CONSTRAINT card_definitions_rarity_check CHECK (rarity IN ('Common', 'Rare', 'Epic', 'Legendary'))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_definition_per_player_rarity
    ON card_definitions (player_id, rarity)
    WHERE is_active;

CREATE TABLE IF NOT EXISTS pack_openings (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id),
    pack_type   VARCHAR(20) NOT NULL,
    price       NUMERIC(12,2) NOT NULL CHECK (price > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS card_instances (
    id                  BIGSERIAL PRIMARY KEY,
    card_definition_id  BIGINT NOT NULL REFERENCES card_definitions(id),
    serial_number       INTEGER NOT NULL,
    owner_id            BIGINT NOT NULL REFERENCES users(id),
    pack_opening_id     BIGINT REFERENCES pack_openings(id), -- NULL for manual mints
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT card_instances_definition_serial_unique UNIQUE (card_definition_id, serial_number)
);

CREATE TABLE IF NOT EXISTS listings (
    id                BIGSERIAL PRIMARY KEY,
    card_instance_id  BIGINT NOT NULL REFERENCES card_instances(id),
    seller_id         BIGINT NOT NULL REFERENCES users(id),
    price             NUMERIC(12,2) NOT NULL CHECK (price > 0),
    status            VARCHAR(20) NOT NULL CHECK (status IN ('ACTIVE', 'SOLD', 'CANCELLED')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at       TIMESTAMPTZ
);

-- At most one ACTIVE listing per card; SOLD/CANCELLED history is unlimited.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_listing_per_card
    ON listings (card_instance_id)
    WHERE status = 'ACTIVE';

-- Append-only record of every balance change. users.balance is a cached running
-- total; SUM(delta) per user must always equal it.
--   MINT_*     currency created from nothing (no offsetting row anywhere)
--   BURN_*     currency destroyed (no offsetting row anywhere)
--   TRANSFER_* always a matched DEBIT/CREDIT pair with the same related_listing_id
CREATE TABLE IF NOT EXISTS currency_ledger (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(id),
    delta               NUMERIC(12,2) NOT NULL CHECK (delta <> 0),
    reason              VARCHAR(40) NOT NULL CHECK (reason IN (
                            'MINT_SIGNUP',
                            'MINT_DEV_GRANT',
                            'TRANSFER_PURCHASE_DEBIT',
                            'TRANSFER_SALE_CREDIT',
                            'BURN_MARKET_FEE',
                            'BURN_PACK_PURCHASE'
                        )),
    related_listing_id  BIGINT REFERENCES listings(id),
    related_pack_opening_id BIGINT REFERENCES pack_openings(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only history of every card. card_instances.owner_id is the cached
-- current owner; the latest MINTED/PULLED/SOLD event's to_user_id must match it.
CREATE TABLE IF NOT EXISTS card_ownership_events (
    id                       BIGSERIAL PRIMARY KEY,
    card_instance_id         BIGINT NOT NULL REFERENCES card_instances(id),
    event_type               VARCHAR(20) NOT NULL CHECK (event_type IN (
                                 'MINTED', 'PULLED', 'LISTED', 'DELISTED', 'SOLD'
                             )),
    from_user_id             BIGINT REFERENCES users(id),  -- NULL for MINTED/PULLED
    to_user_id               BIGINT REFERENCES users(id),  -- NULL for LISTED/DELISTED
    price                    NUMERIC(12,2),
    related_listing_id       BIGINT REFERENCES listings(id),
    related_pack_opening_id  BIGINT REFERENCES pack_openings(id),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS card_ownership_events_instance_idx
    ON card_ownership_events (card_instance_id, created_at);


-- External identities, used to fetch canonical names (Wikidata) and complete
-- career totals (Wikipedia infoboxes).


-- One row per player per theme. stats holds only the theme's stat set; a stat
-- is null when the player has no data for it (or is below its minimum sample),
-- which loses automatically in a battle.
CREATE TABLE IF NOT EXISTS player_theme_stats (
    id          BIGSERIAL PRIMARY KEY,
    player_id   BIGINT NOT NULL REFERENCES players(id),
    theme       TEXT NOT NULL,
    stats       JSONB NOT NULL,
    matches     INTEGER NOT NULL CHECK (matches >= 0),
    source      TEXT NOT NULL,
    verified    BOOLEAN NOT NULL,
    as_of       DATE,
    notes       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (player_id, theme)
);

CREATE TABLE IF NOT EXISTS battles (
    id                      BIGSERIAL PRIMARY KEY,
    challenger_id           BIGINT NOT NULL REFERENCES users(id),
    opponent_id             BIGINT NOT NULL REFERENCES users(id),
    status                  VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN (
                                'PENDING', 'ACTIVE', 'FINISHED', 'DECLINED', 'EXPIRED', 'FORFEIT'
                            )),
    -- Decks as chosen (card instance ids), then frozen snapshots at start:
    -- names, roles, credits and every theme stat. Snapshots are never
    -- returned by the API; only revealed values leave the server.
    challenger_card_ids     BIGINT[] NOT NULL,
    opponent_card_ids       BIGINT[],
    challenger_deck         JSONB,
    opponent_deck           JSONB,
    themes                  TEXT[],            -- 6 rounds + the sudden-death theme
    current_round           INTEGER NOT NULL DEFAULT 0,
    phase                   VARCHAR(10) CHECK (phase IN ('CARD_PICK', 'STAT_CALL', 'REVEAL')),
    phase_deadline          TIMESTAMPTZ,
    sudden_death_caller_id  BIGINT REFERENCES users(id),
    challenger_wins         INTEGER NOT NULL DEFAULT 0,
    opponent_wins           INTEGER NOT NULL DEFAULT 0,
    draws                   INTEGER NOT NULL DEFAULT 0,
    winner_id               BIGINT REFERENCES users(id),   -- NULL when drawn or not finished
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at              TIMESTAMPTZ NOT NULL,
    started_at              TIMESTAMPTZ,
    finished_at             TIMESTAMPTZ,
    CHECK (challenger_id <> opponent_id)
);

CREATE INDEX IF NOT EXISTS battles_challenger_idx ON battles (challenger_id, created_at DESC);
CREATE INDEX IF NOT EXISTS battles_opponent_idx ON battles (opponent_id, created_at DESC);

-- A player's secret pick for a round (and, for the caller, the stat called).
CREATE TABLE IF NOT EXISTS battle_moves (
    id              BIGSERIAL PRIMARY KEY,
    battle_id       BIGINT NOT NULL REFERENCES battles(id),
    round           INTEGER NOT NULL CHECK (round BETWEEN 1 AND 7),
    player_id       BIGINT NOT NULL REFERENCES users(id),
    card_id         BIGINT NOT NULL REFERENCES card_instances(id),
    stat            TEXT,
    pick_timed_out  BOOLEAN NOT NULL DEFAULT false,
    call_timed_out  BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (battle_id, round, player_id)
);

-- The public record of a resolved round.
CREATE TABLE IF NOT EXISTS battle_rounds (
    battle_id                  BIGINT NOT NULL REFERENCES battles(id),
    round                      INTEGER NOT NULL CHECK (round BETWEEN 1 AND 7),
    theme                      TEXT NOT NULL,
    caller_id                  BIGINT NOT NULL REFERENCES users(id),
    challenger_card_id         BIGINT NOT NULL REFERENCES card_instances(id),
    opponent_card_id           BIGINT NOT NULL REFERENCES card_instances(id),
    stat                       TEXT NOT NULL,
    challenger_value           NUMERIC,           -- NULL = no data
    opponent_value             NUMERIC,
    winner_id                  BIGINT REFERENCES users(id),   -- NULL = drawn
    challenger_pick_timed_out  BOOLEAN NOT NULL,
    opponent_pick_timed_out    BOOLEAN NOT NULL,
    call_timed_out             BOOLEAN NOT NULL,
    resolved_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (battle_id, round)
);
