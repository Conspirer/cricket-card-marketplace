-- Per-theme verified stats and themed PvP battles.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/migrations/004_theme_stats_and_battles.sql

BEGIN;

-- External identities, used to fetch canonical names (Wikidata) and complete
-- career totals (Wikipedia infoboxes).
ALTER TABLE players
    ADD COLUMN cricinfo_id      TEXT,
    ADD COLUMN wikidata_id      TEXT,
    ADD COLUMN wikipedia_title  TEXT;

-- One row per player per theme. stats holds only the theme's stat set; a stat
-- is null when the player has no data for it (or is below its minimum sample),
-- which loses automatically in a battle.
CREATE TABLE player_theme_stats (
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

CREATE TABLE battles (
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

CREATE INDEX battles_challenger_idx ON battles (challenger_id, created_at DESC);
CREATE INDEX battles_opponent_idx ON battles (opponent_id, created_at DESC);

-- A player's secret pick for a round (and, for the caller, the stat called).
CREATE TABLE battle_moves (
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
CREATE TABLE battle_rounds (
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

COMMIT;
