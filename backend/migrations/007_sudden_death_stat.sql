-- Sudden death is one server-drawn stat, revealed before the card pick and
-- compared once. Countback is removed.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/migrations/007_sudden_death_stat.sql

BEGIN;

ALTER TABLE battles ADD COLUMN sudden_death_stat TEXT;

ALTER TABLE battles DROP CONSTRAINT battles_decided_by_check;
UPDATE battles SET decided_by = CASE
    WHEN status = 'FORFEIT' THEN NULL
    WHEN winner_id IS NULL THEN 'draw'
    WHEN current_round > 6 THEN 'sudden_death'
    ELSE 'regulation' END
WHERE decided_by IS NOT NULL;
-- NULL for unfinished battles and forfeits (the FORFEIT status says it all).
ALTER TABLE battles ADD CONSTRAINT battles_decided_by_check
    CHECK (decided_by IN ('regulation', 'sudden_death', 'draw'));

COMMIT;
