# Crease

A cricket card marketplace: open packs of real players, trade them for Runs (a virtual currency with no cash value), and play themed stat battles against other users.

- **Backend:** FastAPI, handwritten SQL on PostgreSQL 16 (psycopg 3)
- **Frontend:** React + Vite, built and served by FastAPI in production (one origin, no CORS)
- **Data:** Cricsheet (ODC-BY 1.0), Wikipedia (CC BY-SA 4.0), Wikidata (CC0). See the Credits page in the app.

## Local development

```bash
docker compose up -d postgres          # Postgres 16 on localhost:5432

python -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
export DATABASE_URL=postgresql://cricket:cricket_dev_password@localhost:5432/cricket_marketplace
export DEV_FAUCET_ENABLED=1            # optional: POST /api/dev/users/{id}/grant mints test Runs

.venv/bin/python -m backend.scripts.migrate                 # builds a fresh DB or applies pending migrations
.venv/bin/uvicorn backend.main:app --port 8000 --reload --reload-dir backend

cd frontend && npm install && npm run dev                    # http://localhost:5173 (proxies /api to :8000)
```

To fill a fresh database with real players and a card pool (downloads Cricsheet data, roughly 50 MB):

```bash
.venv/bin/python -m backend.scripts.import_cricsheet
.venv/bin/python -m backend.scripts.build_card_pool
.venv/bin/python -m backend.scripts.link_players
.venv/bin/python -m backend.scripts.build_theme_stats
```

### Tests

```bash
.venv/bin/python -m pytest backend/tests -q
```

The tests create and drop their own `cricket_test` database on the local Postgres. Set `TEST_DATABASE_URL` to point them elsewhere. CI runs the same suite against a Postgres service container on every push.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | none (required) | Postgres connection URL. Heroku sets it. |
| `DATABASE_SSLMODE` | `require` on Heroku, else `prefer` | Heroku Postgres only accepts SSL. |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | Connections per dyno. Small Postgres plans have low limits (20 on the smallest). |
| `SITE_PASSWORD` | unset (site open) | If set, the whole site asks for this password (HTTP basic auth; any username). |
| `DEV_FAUCET_ENABLED` | unset (off) | Enables the Run-minting dev endpoint. **Leave unset in production.** |

On Heroku (the `DYNO` variable is set) plain-HTTP requests are redirected to HTTPS, so the site password never travels unencrypted.

## Deploying to Heroku

The app runs as one web dyno. The Node buildpack builds the React app (`heroku-postbuild` in the root `package.json`), then the Python buildpack installs the backend. `Procfile` runs migrations in the release phase and serves everything with uvicorn.

```bash
heroku create my-crease
heroku buildpacks:add --index 1 heroku/nodejs -a my-crease
heroku buildpacks:add --index 2 heroku/python -a my-crease
heroku addons:create heroku-postgresql:essential-0 -a my-crease

heroku config:set SITE_PASSWORD='choose-a-long-password' -a my-crease
# Do NOT set DEV_FAUCET_ENABLED.

git push heroku main        # the release phase builds the empty database from backend/schema.sql
heroku open -a my-crease
```

Every later deploy applies any new files in `backend/migrations/` automatically. Check with:

```bash
heroku run python -m backend.scripts.migrate --status -a my-crease
```

### Copying your local database to Heroku

The Cricsheet and Wikipedia data takes a while to rebuild, so the simplest way to populate Heroku is to restore a dump of your local database. The restore **replaces** everything in the Heroku database.

```bash
# 1. Dump the local database (custom format).
docker exec cricket-postgres pg_dump -U cricket -Fc cricket_marketplace > backups/local.dump

# 2. Put the app in maintenance mode so nothing writes during the restore.
heroku maintenance:on -a my-crease

# 3. Restore it into Heroku Postgres. pg_restore runs in a postgres:16 container, so no
#    local client is needed. --no-owner/--no-acl drop the local 'cricket' role references.
docker run --rm -v "$PWD/backups:/backups" postgres:16 \
  pg_restore --verbose --clean --if-exists --no-owner --no-acl \
  -d "$(heroku config:get DATABASE_URL -a my-crease)" /backups/local.dump

# 4. Check the migration history came across, then reopen the site.
heroku run python -m backend.scripts.migrate --status -a my-crease
heroku maintenance:off -a my-crease
```

If step 4 reports tables but no migration history (a dump taken before `schema_migrations` existed), and the local database was fully migrated, record it with:

```bash
heroku run python -m backend.scripts.migrate --baseline -a my-crease
```

`heroku pg:backups:restore` is an alternative, but it needs the dump at a publicly reachable URL. The `pg_restore` route above avoids uploading your data anywhere else.

## Project layout

```
backend/
  main.py            JSON API (mounted at /api)
  web.py             root app: /api + the built frontend, password gate, HTTPS redirect
  battle_routes.py   battle API        battles.py  battle rules (shared with the harness)
  themes.py          battle themes and stat sets
  schema.sql         full schema (fresh databases)
  migrations/        incremental changes (existing databases)
  scripts/           migrate, data import, card pool, theme stats, balance harness
  tests/
frontend/            React app (Vite)
Procfile, package.json, requirements.txt, .python-version, app.json   Heroku
```
