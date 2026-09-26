-- Real player database: Cricsheet identity, career stats and ratings on
-- players, plus an explicit "in the pack pool" flag on card definitions.
-- Apply with:
--   docker exec -i cricket-postgres psql -U cricket -d cricket_marketplace < backend/migrations/003_player_stats.sql

BEGIN;

-- country and role already exist (NOT NULL). The import fills them from data.
-- batting_hand, bowling_type and is_keeper aren't in Cricsheet: nullable,
-- filled manually later.
ALTER TABLE players
    ADD COLUMN cricsheet_id  TEXT UNIQUE,
    ADD COLUMN batting_hand  TEXT,
    ADD COLUMN bowling_type  TEXT,
    ADD COLUMN is_keeper     BOOLEAN,
    ADD COLUMN career_stats  JSONB,   -- {"T20": {...}, "ODI": {...}, "Test": {...}}
    ADD COLUMN ratings       JSONB;   -- {"T20": {"technique": 0-99, ...}, ...}

-- The two seed keepers were entered with role 'Wicketkeeper'; keep that
-- knowledge now that role is derived from data (Batter/Bowler/All-rounder).
UPDATE players SET is_keeper = true WHERE role = 'Wicketkeeper';

-- Only active definitions are rolled by packs. Existing definitions start
-- inactive; scripts/build_card_pool.py decides the active set.
ALTER TABLE card_definitions
    ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT false;

-- At most one active definition per player per rarity.
CREATE UNIQUE INDEX one_active_definition_per_player_rarity
    ON card_definitions (player_id, rarity)
    WHERE is_active;

COMMIT;
