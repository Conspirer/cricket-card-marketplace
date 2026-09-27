-- Pull feed, public profiles (showcase), and playtest page-view logging.
-- Applied automatically on start (python -m backend.scripts.migrate).

BEGIN;

-- The feed scans recent pulls newest-first and keeps the notable ones.
CREATE INDEX card_ownership_events_pulls_recent_idx
    ON card_ownership_events (created_at DESC)
    WHERE event_type = 'PULLED';

-- Up to 5 cards a user shows on their profile. A card that leaves the user's
-- ownership stays here but is filtered out when read.
CREATE TABLE user_showcase (
    user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_instance_id  BIGINT NOT NULL REFERENCES card_instances(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL CHECK (position BETWEEN 1 AND 5),
    pinned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, card_instance_id),
    UNIQUE (user_id, position)
);

-- One row per user per (UTC) day they used the app: enough for the playtest
-- report (days active, returning players) without third-party analytics.
CREATE TABLE page_views (
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day         DATE NOT NULL,
    views       INTEGER NOT NULL DEFAULT 1 CHECK (views > 0),
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, day)
);

COMMIT;
