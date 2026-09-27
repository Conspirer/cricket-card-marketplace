-- Source of truth for the Postgres schema (fresh databases).
-- Existing databases: apply files in migrations/ in order instead.
-- Safe to re-run: every statement is IF NOT EXISTS.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/schema.sql

CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    email       TEXT UNIQUE,
    password_hash TEXT,                       -- scrypt; NULL = can't log in
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
    edition        TEXT NOT NULL DEFAULT 'BASE',  -- BASE = pack pool; SBC = reward-only edition
    edition_key    TEXT UNIQUE,
    edition_label  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT card_definitions_edition_check CHECK (edition IN ('BASE', 'SBC')),
    CONSTRAINT card_definitions_sbc_named CHECK (
        edition = 'BASE' OR (edition_key IS NOT NULL AND edition_label IS NOT NULL AND NOT is_active)
    ),
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
    burned_at           TIMESTAMPTZ,                          -- set when destroyed in an SBC
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
                            'MINT_RESET_GRANT',
                            'MINT_SBC_REWARD',
                            'TRANSFER_PURCHASE_DEBIT',
                            'TRANSFER_SALE_CREDIT',
                            'BURN_MARKET_FEE',
                            'BURN_PACK_PURCHASE'
                        )),
    related_listing_id  BIGINT REFERENCES listings(id),
    related_pack_opening_id BIGINT REFERENCES pack_openings(id),
    related_sbc_completion_id BIGINT,           -- FK added below, once sbc_completions exists
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only history of every card. card_instances.owner_id is the cached
-- current owner; the latest MINTED/PULLED/SOLD/TRADED event's to_user_id must match it.
CREATE TABLE IF NOT EXISTS card_ownership_events (
    id                       BIGSERIAL PRIMARY KEY,
    card_instance_id         BIGINT NOT NULL REFERENCES card_instances(id),
    event_type               VARCHAR(20) NOT NULL CHECK (event_type IN (
                                 'MINTED', 'PULLED', 'LISTED', 'DELISTED', 'SOLD', 'BURNED', 'TRADED'
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
    themes                  TEXT[],            -- 6 rounds + the sudden-death round
    current_round           INTEGER NOT NULL DEFAULT 0,
    phase                   VARCHAR(10) CONSTRAINT battles_phase_check CHECK (phase IN ('CARD_PICK', 'CALL', 'REVEAL')),
    phase_deadline          TIMESTAMPTZ,
    challenger_points       INTEGER NOT NULL DEFAULT 0,
    opponent_points         INTEGER NOT NULL DEFAULT 0,
    winner_id               BIGINT REFERENCES users(id),   -- NULL when drawn or not finished
    sudden_death_stat       TEXT,              -- drawn at start, revealed before the sudden-death pick
    decided_by              TEXT CONSTRAINT battles_decided_by_check
                                CHECK (decided_by IN ('regulation', 'sudden_death', 'draw')),
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
    round           INTEGER NOT NULL CHECK (round BETWEEN 1 AND 9),
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
    battle_id                        BIGINT NOT NULL REFERENCES battles(id),
    round                            INTEGER NOT NULL CHECK (round BETWEEN 1 AND 9),
    theme                            TEXT NOT NULL,
    challenger_card_id               BIGINT NOT NULL REFERENCES card_instances(id),
    opponent_card_id                 BIGINT NOT NULL REFERENCES card_instances(id),
    -- Each call compares both cards on the called stat. NULL value = no data.
    challenger_stat                  TEXT NOT NULL,
    challenger_call_challenger_value NUMERIC,
    challenger_call_opponent_value   NUMERIC,
    opponent_stat                    TEXT NOT NULL,
    opponent_call_challenger_value   NUMERIC,
    opponent_call_opponent_value     NUMERIC,
    challenger_points                INTEGER NOT NULL CHECK (challenger_points BETWEEN 0 AND 2),
    opponent_points                  INTEGER NOT NULL CHECK (opponent_points BETWEEN 0 AND 2),
    challenger_pick_timed_out        BOOLEAN NOT NULL,
    opponent_pick_timed_out          BOOLEAN NOT NULL,
    challenger_call_timed_out        BOOLEAN NOT NULL,
    opponent_call_timed_out          BOOLEAN NOT NULL,
    resolved_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (battle_id, round),
    CHECK (challenger_points + opponent_points <= 2)
);

CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_key ON users (lower(username));

-- One row per logged-in browser. Only a SHA-256 of the cookie's token is
-- stored, so a database leak doesn't hand out live sessions.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);

-- The feed scans recent pulls newest-first and keeps the notable ones.
CREATE INDEX IF NOT EXISTS card_ownership_events_pulls_recent_idx
    ON card_ownership_events (created_at DESC)
    WHERE event_type = 'PULLED';

-- Up to 5 cards a user shows on their profile. A card that leaves the user's
-- ownership stays here but is filtered out when read.
CREATE TABLE IF NOT EXISTS user_showcase (
    user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_instance_id  BIGINT NOT NULL REFERENCES card_instances(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL CHECK (position BETWEEN 1 AND 5),
    pinned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, card_instance_id),
    UNIQUE (user_id, position)
);

-- One row per user per (UTC) day they used the app: enough for the playtest
-- report (days active, returning players) without third-party analytics.
CREATE TABLE IF NOT EXISTS page_views (
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day         DATE NOT NULL,
    views       INTEGER NOT NULL DEFAULT 1 CHECK (views > 0),
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS sbc_challenges (
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

CREATE TABLE IF NOT EXISTS sbc_completions (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(id),
    challenge_id        BIGINT NOT NULL REFERENCES sbc_challenges(id),
    submitted_card_ids  BIGINT[] NOT NULL,
    reward_refs         JSONB NOT NULL,     -- {"cards": [card_instance_id, ...], "runs": n}
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sbc_completions_user_idx ON sbc_completions (user_id, challenge_id);

DO $$ BEGIN
    ALTER TABLE currency_ledger ADD CONSTRAINT currency_ledger_related_sbc_completion_fkey
        FOREIGN KEY (related_sbc_completion_id) REFERENCES sbc_completions(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Trading (migration 012): card-for-card, 1 to 3 cards a side, never Runs.
CREATE TABLE IF NOT EXISTS trades (
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
CREATE INDEX IF NOT EXISTS trades_proposer_idx ON trades (proposer_id, status);
CREATE INDEX IF NOT EXISTS trades_recipient_idx ON trades (recipient_id, status);

-- from_user_id gives the card up: the proposer for offered cards, the
-- recipient for requested ones.
CREATE TABLE IF NOT EXISTS trade_cards (
    trade_id          BIGINT NOT NULL REFERENCES trades(id),
    card_instance_id  BIGINT NOT NULL REFERENCES card_instances(id),
    from_user_id      BIGINT NOT NULL REFERENCES users(id),
    PRIMARY KEY (trade_id, card_instance_id)
);
CREATE INDEX IF NOT EXISTS trade_cards_card_idx ON trade_cards (card_instance_id);

ALTER TABLE card_ownership_events ADD COLUMN IF NOT EXISTS related_trade_id BIGINT REFERENCES trades(id);
