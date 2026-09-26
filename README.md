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

The tests create and drop their own `cricket_test` database on the local Postgres. Set `TEST_DATABASE_URL` to point them elsewhere. CI runs the same suite against a Postgres service container on every push, and also builds the Docker image.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | none (required) | Postgres connection string. On Render, the Neon string. |
| `DATABASE_SSLMODE` | `require` for any non-local host, `prefer` for localhost | Only used when the URL has no `sslmode`. Neon requires SSL. |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | Connections held by the app. Free Postgres plans allow few. |
| `SITE_PASSWORD` | unset (site open) | If set, the whole site asks for this password (HTTP basic auth; any username). |
| `FORCE_HTTPS` | on | Requests the proxy reports as plain HTTP (`X-Forwarded-Proto: http`) are redirected to HTTPS, so the password never travels unencrypted. Set `0` to disable. |
| `DEV_FAUCET_ENABLED` | unset (off) | Enables the Run-minting dev endpoint. **Leave unset in production.** |
| `PORT` | `8000` in the image | Render sets it. |

`/healthz` is always open (it returns only `ok`) so the host's health check works behind the password.

## Deploying: Render (app) + Neon (Postgres)

The app ships as one Docker image (`Dockerfile`): a Node stage builds the React app, and a Python stage runs FastAPI, which serves the API at `/api` and the built frontend everywhere else. On start it runs `python -m backend.scripts.migrate` (a no-op when up to date), then uvicorn. Render's free tier has no separate release phase, so migrating on every start is the only option. Check the image builds locally with `docker build -t crease .`.

### 1. Database on Neon

1. Create a Neon project with Postgres 16 or newer, and a database, for example `crease`.
2. Copy the **direct** connection string (the host *without* `-pooler`). It ends in `?sslmode=require`.

   The app holds its own small pool (1–5 connections), so it doesn't need Neon's pooler. If you use the pooled string anyway, the app detects the `-pooler` host and turns off server-side prepared statements, which PgBouncer's transaction mode doesn't support.

### 2. Web service on Render

`render.yaml` describes the service. In Render: **New → Blueprint**, pick this repo, then fill in the two secrets it asks for:

- `DATABASE_URL`: the Neon connection string
- `SITE_PASSWORD`: a long shared password

Leave `DEV_FAUCET_ENABLED` unset. Without the Blueprint, create a **Web Service**, choose **Docker** as the runtime, set those two environment variables, and set the health check path to `/healthz`.

The first deploy builds the empty Neon database from `backend/schema.sql`. Later deploys apply any new files in `backend/migrations/`. Free Render services sleep after about 15 minutes idle, so the first request after that takes around a minute.

### 3. Copying your local data into Neon

The player catalogue (Cricsheet, Wikipedia and Wikidata data) takes a while to rebuild, so the simplest way to fill Neon is to restore a dump of your local database. To start the live site with a clean economy (no users, cards or battles, catalogue kept), reset a **copy** first:

```bash
# 1. Make a scratch copy of the local database and reset its economy. The copy goes
#    through pg_dump rather than `createdb -T`, which refuses while the app is connected.
docker exec cricket-postgres createdb -U cricket crease_clean
docker exec cricket-postgres sh -c "pg_dump -U cricket -Fc cricket_marketplace | pg_restore -U cricket --no-owner -d crease_clean"
DATABASE_URL=postgresql://cricket:cricket_dev_password@localhost:5432/crease_clean \
  .venv/bin/python -m backend.scripts.reset_economy --confirm   # prints counts and audits

# 2. Dump it (custom format).
docker exec cricket-postgres pg_dump -U cricket -Fc crease_clean > backups/crease_clean.dump
docker exec cricket-postgres dropdb -U cricket crease_clean
```

Restore into Neon. This **replaces** the objects in the Neon database, so don't run it against a database holding live data you want to keep. `pg_restore` runs in a `postgres:16` container, so no local client is needed. `--no-owner`/`--no-acl` drop references to the local `cricket` role.

```bash
export NEON_URL='postgresql://USER:PASSWORD@ep-XXXX.REGION.aws.neon.tech/crease?sslmode=require'

docker run --rm -v "$PWD/backups:/backups" postgres:16 \
  pg_restore --verbose --clean --if-exists --no-owner --no-acl \
  -d "$NEON_URL" /backups/crease_clean.dump

# Check it: migration history should list every migration as applied.
DATABASE_URL="$NEON_URL" .venv/bin/python -m backend.scripts.migrate --status
```

Then redeploy (or restart) the Render service. Its start-up migrate will report "up to date".

If `--status` shows tables but no migration history (a dump taken before `schema_migrations` existed) and the source database was fully migrated, record it with `DATABASE_URL="$NEON_URL" .venv/bin/python -m backend.scripts.migrate --baseline`. To run the economy audit against Neon at any time, run `reset_economy` **without** `--confirm`: it only reports counts.

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
Dockerfile, .dockerignore, render.yaml   deployment (Render + Neon)
```
