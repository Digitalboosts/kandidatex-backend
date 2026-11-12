# Interview Monitoring — FastAPI backend

Standalone Python backend that replaces `apps/backend` (Next.js). **Same API** (paths, JSON, status codes, token format),
same database tables, so the existing dashboard and mobile app work unchanged — only their backend URL changes.
Nothing outside this folder was modified.

* **Docs:** [`docs/API.md`](docs/API.md) (guide) · `/docs` Swagger UI · `/redoc` · [`docs/openapi.json`](docs/openapi.json)
* **Stack:** FastAPI · SQLAlchemy 2 (async) · PostgreSQL (asyncpg) · Alembic · PyJWT · bcrypt · livekit-api · pytest

```
app/
  main.py            app factory, middleware, OpenAPI metadata
  config.py          environment settings          errors.py   error format
  models.py          tables (match Prisma schema)  schemas.py  request/response models (camelCase)
  security.py        passwords + JWT               deps.py     auth dependencies (user / candidate)
  routers/           auth · interviews · livekit · monitoring · analytics · health
  services/          livekit.py · monitoring.py · interviews.py
  monitoring_rules.py  event catalog + derived-event rules
  ai/                reserved for the future AI features
alembic/             migrations      tests/   59 tests      docs/   API documentation
```

## Run locally

```bash
cd apps/backend-fastapi
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                      # then fill JWT_SECRET etc.

alembic upgrade head                                      # create tables
uvicorn app.main:app --reload --port 4000                 # http://localhost:4000/docs
```

Postgres + LiveKit for development: `docker compose -f ../../infrastructure/docker-compose.yml up -d`.

### Using the existing database
The tables and columns are identical to the Prisma ones. Point `DATABASE_URL` at the existing database and run
`alembic upgrade head`: the first migration detects the existing tables and does nothing (it only adds Alembic's own
`alembic_version` table). Users, interviews and events are kept. Reuse the old `JWT_SECRET` and logged-in sessions keep working.

## Tests

```bash
pytest            # 59 tests, in-memory SQLite, LiveKit calls mocked — no services needed
```
Covers auth/sessions, ownership and candidate isolation, join rules, LiveKit token grants, signed webhooks
(including tampered and redelivered ones), status → timeline events, ending sessions, analytics, CORS and the OpenAPI surface.

## Configuration

See [`.env.example`](.env.example). Required: `DATABASE_URL`, `JWT_SECRET` (≥32 chars), `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET` (≥32), `LIVEKIT_HOST`, `LIVEKIT_PUBLIC_URL`. Deployment-specific: `ENVIRONMENT=production`,
`CORS_ORIGINS`, `COOKIE_SAMESITE`, `COOKIE_DOMAIN`, `DOCS_ENABLED`.

## Deploy

The backend is a single container with no state besides PostgreSQL and LiveKit.

```bash
docker build -t interview-api apps/backend-fastapi
docker run -p 4000:4000 --env-file apps/backend-fastapi/.env -e ENVIRONMENT=production interview-api
```
On start it runs `alembic upgrade head`, then `uvicorn` on `$PORT` (default 4000). Health check: `GET /api/health`.
Works on any container host (Render, Railway, Fly.io, Cloud Run, ECS, a VM) with a managed PostgreSQL (e.g. Supabase —
the `?sslmode=require` URL is handled).

Checklist:
1. **HTTPS** in front (the platform usually provides it). Set `ENVIRONMENT=production` → Secure cookies.
2. **LiveKit webhook:** point LiveKit's webhook URL at `https://<api-host>/api/livekit/webhook` (same API key/secret).
3. `LIVEKIT_PUBLIC_URL` must be a `wss://` URL reachable from phones/browsers; `LIVEKIT_HOST` is how *this server* reaches it.
4. Run **one instance** (or add Redis for the rate limiter — see `app/rate_limit.py`).
5. Consider `DOCS_ENABLED=false` if you don't want public docs.

## Connecting the frontend (no frontend code changes)

* **Dashboard (Next.js)** already proxies `/api/*` to `BACKEND_URL` (`apps/dashboard/.env`, read at build time). Set it to the
  deployed API, e.g. `BACKEND_URL="https://api.example.com"`, and rebuild/redeploy the dashboard. The browser keeps talking to
  its own origin, so the session cookie stays first-party and **no CORS is needed**.
* **Mobile app** uses `API_BASE_URL` (`apps/mobile/src/config.ts`, e.g. `PRODUCTION_BACKEND_URL`) with Bearer tokens — set it
  to the deployed API. No CORS involved.
* **If you later let a browser call the API directly** on another domain: set `CORS_ORIGINS=https://dashboard.example.com`,
  send requests with `credentials: 'include'`, and use `COOKIE_SAMESITE=none` (HTTPS) — or just send the Bearer token.

## Adding AI later

Put model code in `app/ai/` and run it **outside the request cycle** — a LiveKit Agents worker that joins the room and reads the
candidate's video track, or a task queue (Celery/RQ + Redis) for batch analysis. Store findings with
`app.services.monitoring.record_event(...)` after adding new types to `app/monitoring_rules.py::EVENT_CATALOG`; they then appear
on the existing timeline and realtime channel automatically. Keep heavy dependencies (PyTorch, OpenCV) out of the API image if the
worker is deployed separately.

## Notes for maintainers

* `app/monitoring_rules.py` is a port of `packages/shared/src/monitoring.ts` (event catalog, allowlists, derived events). While
  the dashboard/mobile still import the TypeScript copy, keep both in step.
* Row ids are time-ordered strings (not Prisma cuids); existing rows keep their old ids.
