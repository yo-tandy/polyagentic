# Cloud Deployment Readiness — GCP with Multi-Org SaaS Path

## Context

Polyagentic currently runs as a local development tool on localhost. The goal is to deploy it on **GCP/Firebase** and evolve it into a **multi-organization SaaS** platform. Key requirements:
- Remote agents (Docker containers on other servers)
- Reduce filesystem dependency (file-based message broker, worktrees, logs)
- Support both Claude CLI and Claude API providers

The plan is phased: **P0** makes it deployable on a single GCP VM, **P1** hardens for production, **P2** enables multi-tenant SaaS.

---

## P0 — Make It Deployable (Single GCP VM)

These are blockers that prevent the app from running on any cloud server.

### P0.1 — Dockerfile + docker-compose.yml

**No Dockerfile exists.** Create:

- `Dockerfile` — Python 3.11 slim, install requirements, copy app, expose 8000, run with uvicorn
- `docker-compose.yml` — app service + PostgreSQL service + volume mounts for data persistence
- `.dockerignore` — exclude `.git`, `worktrees/`, `__pycache__`, `*.db`

**Files:** New `Dockerfile`, `docker-compose.yml`, `.dockerignore` at project root

### P0.2 — Bind to 0.0.0.0

**`config.py` line 18:** `WEB_HOST = "127.0.0.1"` — blocks external access.

**Change:** Default to `"0.0.0.0"` for container environments. Keep `127.0.0.1` as fallback for local dev via env var.

**`db/config_provider.py` line 62:** Seed default also says `"127.0.0.1"` — update to `"0.0.0.0"`.

**Files:** `config.py`, `db/config_provider.py`

### P0.3 — CORS Origins from Config

**`core/constants.py` lines 50-53:** `DEFAULT_CORS_ORIGINS` hardcoded to `["http://localhost:8000", "http://127.0.0.1:8000"]`.

**Change:** Read CORS origins from `CORS_ORIGINS` config entry (comma-separated string). Keep localhost defaults for local dev. Add seed default in `config_provider.py`.

**Files:** `core/constants.py`, `web/app.py` (read config before creating CORS middleware), `db/config_provider.py`

### P0.4 — Fix JWT Secret Bug

**`web/auth.py` lines 46-55:** `_get_jwt_secret()` generates a **new random secret on every call** if not configured, invalidating all tokens.

**Fix:** Cache the generated secret in `app.state` so it persists within a server run. Also add a startup warning if no persistent secret is configured.

```python
def _get_jwt_secret(request: Request) -> str:
    cached = getattr(request.app.state, "_jwt_secret", None)
    if cached:
        return cached
    config = request.app.state.config_provider
    secret = config.get("JWT_SECRET", "")
    if not secret:
        secret = secrets.token_hex(32)
        logger.warning("JWT_SECRET not configured — using ephemeral key")
    request.app.state._jwt_secret = secret
    return secret
```

**Files:** `web/auth.py`

### P0.5 — Enable Auth by Default + Seed JWT Secret

**`db/config_provider.py`:** Change `AUTH_ENABLED` seed default from `"false"` to `"true"`. Generate and seed a random `JWT_SECRET` on first init (so it persists in DB).

Add a startup check in `main.py` that warns if critical secrets are missing.

**Files:** `db/config_provider.py`, `main.py`

### P0.6 — PostgreSQL Support

**`db/engine.py` line 21-24:** Default is SQLite (`sqlite+aiosqlite:///./polyagentic.db`). Already reads `DATABASE_URL` from env.

**Changes needed:**
- Add `asyncpg` to `requirements.txt` (PostgreSQL async driver)
- Update `db/engine.py` to handle PostgreSQL-specific engine options (pool_size, SSL)
- Replace SQLite-specific `ALTER TABLE` migrations with proper conditionals
- The existing `create_all()` works for both SQLite and PostgreSQL

**Files:** `requirements.txt`, `db/engine.py`

### P0.7 — Health Check Endpoint

Add `GET /health` — returns `{"status": "ok", "db": "connected"}`. Used by GCP health checks, load balancers, and container orchestration.

**Files:** `web/app.py` or new `web/routes/health.py`

### P0.8 — Environment Variable Support (.env)

Currently config comes from DB (`config_entries` table). For deployment, secrets should come from environment (GCP Secret Manager, env vars).

**Changes:**
- Add `python-dotenv` to `requirements.txt`
- Load `.env` file at startup in `main.py` before DB init
- In `db/config_provider.py`, check env vars first, then fall back to DB
- Priority: env var → DB config_entries → seed default

**Files:** `requirements.txt`, `main.py`, `db/config_provider.py`

### P0.9 — Graceful Shutdown

**`main.py` `run()` function:** Currently starts uvicorn but shutdown handling is minimal.

**Changes:**
- Register signal handlers (SIGTERM, SIGINT) that call `lifecycle.stop_all()`
- Ensure broker tasks are properly cancelled
- Close DB connections cleanly
- Set uvicorn shutdown timeout

**Files:** `main.py`

---

## P1 — Production Hardening

### P1.1 — Reverse Proxy Config (nginx/Cloud Run)

Provide nginx configuration for:
- SSL termination (Let's Encrypt or GCP-managed certs)
- WebSocket proxying (`/ws` path)
- Static file serving
- Request size limits
- Connection timeouts

Also document GCP Cloud Run deployment option with managed SSL.

**Files:** New `deploy/nginx.conf`, `deploy/cloud-run.yaml`

### P1.2 — Rate Limiting

No rate limiting exists. Add `slowapi` middleware:
- API endpoints: 60 req/min per user
- Auth endpoints: 10 req/min per IP
- WebSocket: connection limit per user

**Files:** `requirements.txt` (add `slowapi`), `web/app.py` (add limiter middleware)

### P1.3 — Structured Logging

Currently uses Python `logging` module with basic format. For cloud deployment, need:
- JSON-formatted log output (for Cloud Logging/Stackdriver)
- Request ID correlation
- Log level from config
- Remove file-based log rotation (use stdout in containers)

**Files:** `main.py` (logging setup), new `core/logging_config.py`

### P1.4 — Database Migrations with Alembic

Currently uses `create_all()` + manual `ALTER TABLE` statements in `db/engine.py`. This doesn't scale.

**Changes:**
- Add `alembic` to `requirements.txt`
- Set up Alembic config (`alembic.ini`, `alembic/env.py`)
- Generate initial migration from current models
- Replace `_apply_migrations()` in `db/engine.py` with Alembic `upgrade head`
- Keep `create_all()` only for test environments

**Files:** `requirements.txt`, new `alembic/` directory, `db/engine.py`

### P1.5 — Secure Defaults

- Set `Secure` flag on JWT cookies (HTTPS only) — `web/auth.py`
- Add `SameSite=Lax` on cookies — `web/auth.py`
- HSTS header already conditional on non-localhost — good
- Add CSP header for static pages — `core/constants.py`
- Strip server version header — `web/app.py`

**Files:** `web/auth.py`, `core/constants.py`

### P1.6 — Gunicorn/Uvicorn Workers

Single uvicorn process won't scale. Add gunicorn with uvicorn workers:
- `gunicorn main:app -k uvicorn.workers.UvicornWorker -w 4`
- But note: in-memory state (lifecycle_manager, registries) is per-process — need to handle this

**Short-term:** Single process is fine for initial deployment. Document the constraint.
**Medium-term:** Extract shared state to Redis/DB (see P2).

**Files:** New `deploy/gunicorn.conf.py`, `Dockerfile` CMD update

---

## P2 — Multi-Tenant SaaS + Remote Agents

### P2.1 — Organization Isolation on Projects

**Gap:** `Project` model has no `org_id` FK. All projects share the default tenant.

**Changes:**
- Add `org_id` FK to `Project` model (`db/models/project.py`)
- Filter all project queries by org_id (from authenticated user's token)
- Update `ProjectStore` to scope operations by org
- Update `ProjectLifecycleManager` to track org ownership

**Files:** `db/models/project.py`, `db/repositories/project_repo.py`, `core/project_store.py`, `main.py`

### P2.2 — DB-Backed Message Broker

**Current:** File-based JSON in `messages/<agent_id>/inbox/` polled every 1s by `MessageBroker._poll_directory()`.

**Migration to DB queue:**
- New `message_queue` table: `id`, `sender`, `recipient`, `type`, `content`, `task_id`, `metadata`, `status` (pending/delivered/processed), `created_at`
- Replace `_poll_directory()` with DB query: `SELECT * FROM message_queue WHERE recipient = ? AND status = 'pending'`
- Replace `Message.to_file()` in `deliver()` with DB insert
- Keep `Message` dataclass, add `to_db_row()` / `from_db_row()` methods
- Message broker keeps same interface — swap implementation only

**Key files:** `core/message_broker.py` (~150 lines to refactor), `core/message.py`, new `db/models/message_queue.py`, new `db/repositories/message_queue_repo.py`

### P2.3 — Remote Agent Support (Docker API Provider)

**Current:** Agents run as local subprocesses (`subprocess_manager.py`). Container support exists (`container_manager.py`) but for local Docker only.

**For remote agents:**
- Add `RemoteAgentProvider` — communicates with agents running in Docker containers on remote servers via HTTP API
- Each remote agent container runs a lightweight FastAPI endpoint that accepts prompts and returns responses
- Agent configuration in `team_config.yaml` gets `runtime: remote` option
- Container orchestration via GCP Cloud Run Jobs or GKE

**Approach:**
1. Create `core/providers/remote_provider.py` — HTTP client that sends prompts to remote agent endpoint
2. Remote agent container image includes: Claude API SDK, tool executor, file system, git
3. Agent state (session) stored in DB, not local filesystem
4. Results returned via HTTP response, stored in DB message queue

**Files:** New `core/providers/remote_provider.py`, new `deploy/agent/Dockerfile`, `core/agent.py` (runtime selection)

### P2.4 — Reduce Filesystem Dependencies

Beyond message broker (P2.2), other filesystem dependencies:

| Dependency | Current | Migration Path |
|-----------|---------|---------------|
| **Message passing** | JSON files in `messages/` | DB queue (P2.2) |
| **Git worktrees** | Local dirs in `worktrees/` | Keep for local agents; remote agents have own filesystem |
| **Agent logs** | Files in `logs/agents/` | Stream to stdout/DB; use Cloud Logging |
| **File uploads** | Local `uploads/` dir | GCS bucket via `google-cloud-storage` |
| **Team config** | `team_config.yaml` file | Already has DB-backed team_structure; deprecate YAML |
| **Static files** | `web/static/` | Bundle in Docker image; or serve from CDN/GCS |

**Priority migration:** File uploads → GCS, Agent logs → stdout, Message broker → DB.

### P2.5 — Multi-Worker State Sharing

For horizontal scaling (multiple server processes), shared state needs to move out of process memory:

| State | Current Location | Migration |
|-------|-----------------|-----------|
| `_running_projects` | In-memory dict | DB `projects.is_running` (already exists) + Redis for ephemeral state |
| `AgentRegistry` | In-memory | DB-backed (agents table already exists) |
| `WebSocket connections` | In-memory set | Redis Pub/Sub for cross-process broadcasting |
| `SessionStore` | In-memory + DB | Already DB-backed, remove in-memory cache |

### P2.6 — Billing & Usage Tracking

`RequestHistory` table (already created in multi-project feature) provides per-request cost tracking. Extend for billing:
- Add `org_id` to `RequestHistory`
- Monthly rollup queries
- Usage limits per org (configurable)
- Stripe integration for payment

---

## Implementation Order

**Phase 1 (P0 — 1-2 weeks):** Get deployable on GCP VM
```
P0.1 Dockerfile          → P0.2 Bind 0.0.0.0     → P0.3 CORS config
P0.4 JWT secret fix      → P0.5 Enable auth       → P0.6 PostgreSQL
P0.7 Health endpoint     → P0.8 .env support      → P0.9 Graceful shutdown
```

**Phase 2 (P1 — 1-2 weeks):** Production hardening
```
P1.1 Reverse proxy       → P1.2 Rate limiting     → P1.3 Structured logging
P1.4 Alembic migrations  → P1.5 Secure defaults   → P1.6 Multi-worker docs
```

**Phase 3 (P2 — 4-8 weeks):** SaaS platform
```
P2.1 Org isolation       → P2.2 DB message broker  → P2.3 Remote agents
P2.4 Reduce filesystem   → P2.5 State sharing      → P2.6 Billing
```

---

## Files Summary

### P0 — New Files
| File | Purpose |
|------|---------|
| `Dockerfile` | App container image |
| `docker-compose.yml` | App + PostgreSQL orchestration |
| `.dockerignore` | Exclude non-essential files |
| `web/routes/health.py` | Health check endpoint |

### P0 — Modified Files
| File | Change |
|------|--------|
| `config.py` | WEB_HOST default → `0.0.0.0` |
| `db/config_provider.py` | Update seed defaults (host, auth, JWT), env var priority |
| `core/constants.py` | CORS from config |
| `web/app.py` | CORS from config, health router, mount changes |
| `web/auth.py` | Cache JWT secret in app.state |
| `db/engine.py` | PostgreSQL engine options, conditional migrations |
| `requirements.txt` | Add `asyncpg`, `python-dotenv` |
| `main.py` | .env loading, signal handlers, startup warnings |

### P1 — New Files
| File | Purpose |
|------|---------|
| `deploy/nginx.conf` | Reverse proxy config |
| `deploy/gunicorn.conf.py` | Worker config |
| `deploy/cloud-run.yaml` | GCP Cloud Run config |
| `core/logging_config.py` | Structured JSON logging |
| `alembic/` directory | Database migrations |

### P1 — Modified Files
| File | Change |
|------|--------|
| `requirements.txt` | Add `slowapi`, `alembic` |
| `web/app.py` | Rate limiter middleware |
| `web/auth.py` | Secure cookie flags |
| `db/engine.py` | Alembic integration |

---

## Verification

### P0 Verification
1. `docker-compose up` — app starts, connects to PostgreSQL, health check passes
2. Access from browser via `http://<external-ip>:8000` — CORS allows it
3. Auth flow works: Google OAuth login → JWT cookie set → API calls authenticated
4. Server restart: JWT tokens still valid (secret persisted in DB)
5. Create project, start agents, verify they run inside container
6. `docker-compose down` → `docker-compose up` — data persists (PostgreSQL volume)
7. `curl http://localhost:8000/health` → `{"status": "ok"}`

### P1 Verification
1. nginx proxies HTTPS traffic to app, WebSocket works through proxy
2. Rate limiting: 61st request in a minute returns 429
3. Logs output as JSON to stdout, parseable by Cloud Logging
4. `alembic upgrade head` applies cleanly on fresh and existing DBs
5. Cookies have `Secure` and `SameSite=Lax` flags in production

### P2 Verification
1. Two orgs can't see each other's projects
2. Message delivery works via DB queue (no file system)
3. Remote agent receives prompt via HTTP, returns response
4. File uploads stored in GCS, accessible across instances
