-- Round rule change: both players call a stat every round and each call
-- scores separately (points, max 2 per round); up to 3 sudden-death rounds.
-- The single-caller model (caller, coin flip, round wins) is removed.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/migrations/005_simultaneous_calls.sql

BEGIN;

-- The only battles so far were two test games under the old rules.
DELETE FROM battle_rounds;
DELETE FROM battle_moves;
DELETE FROM battles;

ALTER TABLE battles
    DROP COLUMN sudden_death_caller_id,
    DROP COLUMN draws;
ALTER TABLE battles RENAME COLUMN challenger_wins TO challenger_points;
ALTER TABLE battles RENAME COLUMN opponent_wins TO opponent_points;
ALTER TABLE battles DROP CONSTRAINT battles_phase_check;
ALTER TABLE battles ADD CONSTRAINT battles_phase_check CHECK (phase IN ('CARD_PICK', 'CALL', 'REVEAL'));

-- Each player's row now carries their own call (stat, call_timed_out).
-- Rounds 7-9 are sudden death.
ALTER TABLE battle_moves DROP CONSTRAINT battle_moves_round_check;
ALTER TABLE battle_moves ADD CONSTRAINT battle_moves_round_check CHECK (round BETWEEN 1 AND 9);

DROP TABLE battle_rounds;
CREATE TABLE battle_rounds (
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

COMMIT;
