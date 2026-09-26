-- Real accounts: password logins and server-side sessions.
-- Apply with:
--   python -m backend.scripts.migrate   (runs automatically on deploy)

BEGIN;

-- Registration asks for a username and password only.
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;
-- NULL for accounts created before logins existed; they can't log in.
ALTER TABLE users ADD COLUMN password_hash TEXT;
-- "Alice" and "alice" are the same person.
CREATE UNIQUE INDEX users_username_lower_key ON users (lower(username));

-- One row per logged-in browser. Only a SHA-256 of the cookie's token is
-- stored, so a database leak doesn't hand out live sessions.
CREATE TABLE sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions (user_id);

COMMIT;
