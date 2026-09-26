-- One sudden-death round, then countback. Records how a battle was decided.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/migrations/006_countback.sql

BEGIN;

ALTER TABLE battles ADD COLUMN decided_by TEXT CHECK (decided_by IN (
    'points', 'rounds_won', 'fewer_no_point_calls', 'draw', 'forfeit'
));

-- Battles finished before countback existed were decided on points (or drawn).
UPDATE battles SET decided_by = CASE
    WHEN status = 'FORFEIT' THEN 'forfeit'
    WHEN winner_id IS NULL THEN 'draw'
    ELSE 'points' END
WHERE status IN ('FINISHED', 'FORFEIT');

COMMIT;
