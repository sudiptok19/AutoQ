# Application and database foundation

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

Compose defines only db (postgres:16-alpine, port 5432, named volume). The environment example and Make targets anticipate a backend. Python project, schema, migrations, FastAPI entrypoint and Caddy are absent. Vite has optional self-signed TLS.

**Handoff.** Opus exports OpenAPI and validates Astra’s read-only WS schema. Opus imports all routers and updates manifests/locks centrally. Astra submits import names, route prefixes, versions and extras in each PR.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `docker-compose.yml`
- `.env.example`
- `Makefile`
- `README.md`
- `backend/.gitkeep`
- `frontend/vite.config.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/platform/router.py`
- `backend/app/features/platform/service.py`
- `backend/tests/test_platform.py`
- `backend/app/main.py`
- `backend/app/db/models.py`
- `backend/app/db/session.py`
- `backend/app/db/locking.py`
- `backend/alembic.ini`
- `backend/alembic/env.py`
- `backend/alembic/versions/0001_initial.py`
- `backend/pyproject.toml`
- `backend/uv.lock`
- `backend/Dockerfile`
- `contracts/openapi.json`
- `backend/scripts/export_contracts.py`
- `backend/tests/conftest.py`

## Inputs, Outputs & Contract

- `GET /health` → `{status,db,key_kid,version}`. Return `503` and diagnostic code on failed required dependencies. Serve `/openapi.json` with the mounted routers. Astra’s [gateway](lan-https.md) forwards `/api/*` and `/ws/*` to backend and `/` to Vite. Use a phone-trusted LAN certificate with its LAN hostname/IP in the SAN.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

Opus owns all DDL, migration revision IDs, FK/index/nullability decisions and adapter mappings below. Start with a single migration lineage. Every feature sheet specifies its logical column requirements; reuse a table where several sheets name it. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Bring up a fresh real PostgreSQL 16 volume, apply Alembic head, restart preserving data, and verify health and OpenAPI. Exercise failed DB health explicitly.

- Load the app on a physical phone over trusted HTTPS, obtain camera permission, reopen the production PWA offline and reach the live API after reconnect.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
