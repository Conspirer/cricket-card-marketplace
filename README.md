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

## Accounts

Players register with a username and password; every new account gets 10,000 Runs. Passwords are stored as scrypt hashes. A login is a server-side session held in an HttpOnly cookie, so it works the same in every tab and on every device. Every action (opening packs, listing, buying, battle picks and calls) is checked against the logged-in user, and acting for anyone else is refused. Browsing (the market, card pages, the player list) doesn't need the account to be the owner, but the site still asks everyone to log in first.

The live site is open: anyone can reach the sign-up page. `SITE_PASSWORD` is an optional extra, off by default; set it only to close the whole site behind one shared password (for example during maintenance).



| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | none (required) | Postgres connection string. On Render, the Neon string. |
| `DATABASE_SSLMODE` | `require` for any non-local host, `prefer` for localhost | Only used when the URL has no `sslmode`. Neon requires SSL. |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | Connections held by the app. Free Postgres plans allow few. |
| `SITE_PASSWORD` | unset (site open) | If set, the whole site asks for this password (HTTP basic auth; any username). |
| `FORCE_HTTPS` | on | Requests the proxy reports as plain HTTP (`X-Forwarded-Proto: http`) are redirected to HTTPS, so passwords and session cookies never travel unencrypted. Set `0` to disable. |
| `DEV_FAUCET_ENABLED` | unset (off) | Enables the dev tools: the Run faucet and the admin endpoints that create players, card definitions and cards. **Leave unset in production.** |
| `PORT` | `8000` in the image | Render sets it. |

`/healthz` is always open (it returns only `ok`) so the host's health check works behind the password.

## Deploying: Render (app) + Neon (Postgres)

The app ships as one Docker image (`Dockerfile`): a Node stage builds the React app, and a Python stage runs FastAPI, which serves the API at `/api` and the built frontend everywhere else. On start it runs `python -m backend.scripts.migrate` (a no-op when up to date), then uvicorn. Render's free tier has no separate release phase, so migrating on every start is the only option. Check the image builds locally with `docker build -t crease .`.

### 1. Database on Neon

1. Create a Neon project with Postgres 16 or newer, and a database, for example `crease`.
2. Copy the **direct** connection string (the host *without* `-pooler`). It ends in `?sslmode=require`.

   The app holds its own small pool (1–5 connections), so it doesn't need Neon's pooler. If you use the pooled string anyway, the app detects the `-pooler` host and turns off server-side prepared statements, which PgBouncer's transaction mode doesn't support.

### 2. Web service on Render

`render.yaml` describes the service. In Render: **New → Blueprint**, pick this repo, then fill in the secret it asks for:

- `DATABASE_URL`: the Neon connection string

Leave `DEV_FAUCET_ENABLED` and `SITE_PASSWORD` unset. Without the Blueprint, create a **Web Service**, choose **Docker** as the runtime, set `DATABASE_URL`, and set the health check path to `/healthz`.

The first deploy builds the empty Neon database from `backend/schema.sql`. Later deploys apply any new files in `backend/migrations/`. Free Render services sleep after about 15 minutes idle, so the first request after that takes around a minute.

### 3. Replacing Neon's database with the local catalogue

The player catalogue (Cricsheet, Wikipedia and Wikidata data, verified theme stats, the built card pool and the SBCs) takes a while to rebuild, so Neon is filled from a dump of the local database with the economy emptied: no users, cards, packs, listings, trades or battles.

**Make the dump.** Work on a scratch copy so the local database keeps its test data:

```bash
export DATABASE_URL=postgresql://cricket:cricket_dev_password@localhost:5432/cricket_marketplace
.venv/bin/python -m backend.scripts.seed_sbcs                 # SBCs and reward editions (idempotent)

# Scratch copy through pg_dump (createdb -T refuses while the app is connected).
docker exec cricket-postgres dropdb -U cricket --if-exists crease_clean
docker exec cricket-postgres createdb -U cricket crease_clean
docker exec cricket-postgres sh -c "pg_dump -U cricket -Fc cricket_marketplace | pg_restore -U cricket --no-owner -d crease_clean"

# Empty the economy and every user; keeps players, theme stats, card definitions
# (the active pool), SBCs and schema_migrations. Prints counts and runs the audit.
DATABASE_URL=postgresql://cricket:cricket_dev_password@localhost:5432/crease_clean \
  .venv/bin/python -m backend.scripts.reset_economy --confirm

docker exec cricket-postgres pg_dump -U cricket -Fc crease_clean > backups/crease_catalogue.dump
docker exec cricket-postgres dropdb -U cricket crease_clean
```

**The restore script.** `scripts/restore_to_neon.sh` drops the target's whole `public` schema, restores the dump in one transaction (`pg_restore --no-owner --no-privileges` in a `postgres:16` container, so no local client is needed), then prints row counts for players, active card definitions, SBC challenges and users. It refuses without `--confirm`, and refuses Neon's pooled (`-pooler`) host: use the **direct** connection string. `--dump PATH` picks another dump.

**Go-live order.** Every existing account on the live site is deleted by this, so players sign up again.

1. **Push the code and wait for the Render deploy** to show *Live*. The new code must be running before the new schema arrives; its start-up migrate brings the old Neon database up to date, which is harmless since it's replaced next.
2. **Suspend the Render service** (service → *Settings* → *Suspend Web Service*), so nothing writes during the restore and its connections close.
3. **Create a Neon backup branch** (Neon console → *Branches* → *Create branch* from the main branch, for example `before-restore-YYYY-MM-DD`). To roll back, restore the main branch from it in the console.
4. **Run the restore** against the main branch's direct connection string:

   ```bash
   DATABASE_URL='postgresql://USER:PASSWORD@ep-XXXX.REGION.aws.neon.tech/crease?sslmode=require' \
     scripts/restore_to_neon.sh --confirm
   ```

   Expect about 5,456 players, 332 active card definitions, 5 SBC challenges and 0 users.
5. **Resume the Render service.** Its start-up log should say `up to date` from migrate (the dump carries `schema_migrations` with every migration applied). Anything else means the dump and the deployed code don't match; suspend again and check before users arrive.
6. **Smoke test** the live site: `/healthz` returns 200; sign up (the new account has 10,000 Runs); open a pack (serials start at #1); the SBC page lists five challenges; `POST /api/dev/users/<id>/grant` returns 404 (dev tools off). Optionally, `DATABASE_URL=<neon url> .venv/bin/python -m backend.scripts.reset_economy` (no `--confirm`) only reports counts and is a quick read-only check.

The script was tested end to end against a scratch local database standing in for Neon (holding an older schema and a user): it replaced everything, migrate reported `up to date`, the audit held, and sign-up, packs and SBCs worked.

## Project layout

```
backend/
  main.py            JSON API (mounted at /api)
  web.py             root app: /api + the built frontend, HTTPS redirect, optional password gate
  battle_routes.py   battle API        battles.py  battle rules (shared with the harness)
  themes.py          battle themes and stat sets
  schema.sql         full schema (fresh databases)
  migrations/        incremental changes (existing databases)
  scripts/           migrate, data import, card pool, theme stats, balance harness
  tests/
frontend/            React app (Vite)
data/                legends, SBC challenges, rarity review and override files
scripts/
  restore_to_neon.sh replace a database with a catalogue dump (see Deploying)
backups/             local dumps (git-ignored)
Dockerfile, .dockerignore, render.yaml   deployment (Render + Neon)
```
