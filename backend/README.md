# AutoQ-Trace — Backend

FastAPI + PostgreSQL 16 + SQLAlchemy 2 (sync, psycopg 3) + Alembic, Python 3.12 via `uv`.
Ownership: Opus owns the app shell, DB, transactional services and route registration; Astra's
modules plug in under `app/features/{component_vision,seal_vision,spc,passport_crypto,scar_pdf}`
and `app/core/ws_bus.py` (see `docs/WORK_SPLIT.md`).

```bash
make db            # Postgres on localhost:5433 (configurable via AUTOQ_DB_PORT)
make migrate       # alembic upgrade head
make keys          # Ed25519 seed → backend/.keys/ (gitignored, 0600)
make seed-users    # DEV ONLY: admin, op.ravi, qe.ananya, qm.farhan, disp.kiran / autoq-dev
make backend       # http://localhost:8010/docs  (API_PORT=… to change)
make backend-test  # integration tests against a real `autoq_test` database
make contracts     # write contracts/openapi.json
```

## What exists (platform foundation)
| Area | Status |
|---|---|
| Full v0.2 schema: 44 tables, single migration `0001_initial`, partial unique indexes for one open package membership / active load / current passport | implemented + verified (migration applies; constraints exercised in later features) |
| Error envelope `{error:{code,message,details{request_id,…}}}`, fail-closed DB/storage/key errors | implemented + verified |
| JWT login, `/auth/me`, roles re-read from DB per request, `require_roles` | implemented + verified |
| Content-addressed evidence store (magic-byte check, atomic write, read-time re-hash) + `GET /evidence/{sha256}` | implemented + verified |
| Hash-chained audit log, advisory-lock serialized, `GET /audit`, `GET /audit/verify` | implemented + verified (20-way concurrency test) |
| Global lock hierarchy helper (`app/db/locking.py`) — refuses out-of-order locks and locks after audit append | implemented + verified |
| Transactional outbox table + `emit()` | implemented, relay pending WS bus |
| Signing-key custody (`app/core/signing.py`), `/health` 503 without key | implemented + verified |

| Catalog (suppliers, parts with `is_a_part`, lines, customers), heats with certificate evidence, single-heat lots, serialized units (row-locked sequence), `freeze-and-sample` (systematic, reproducible, policy-versioned; A-parts get no lot sample) | implemented + verified |
| Versioned policy (`GET/PUT /policies`, demo default `demo-1`) and central `policy/eligibility.py` (passport, quality, containment runs, recall holds) | implemented + verified |

Interfaces Astra's modules consume are listed in `AGENTS.md`.
