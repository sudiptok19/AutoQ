# CLAUDE.md — Opus (Claude Code) instructions

I own **all UI/UX**, plus the **database and transactional backend** of AutoQ-Trace. Astra (GPT) owns seven isolated modules: component CV, seal CV, SPC, AQ1 crypto, 8D PDF renderer, LAN HTTPS gateway and WS bus/schema.

## Read first
`docs/CONTRACT.md` (§10 v0.2 is binding), `docs/WORK_SPLIT.md`, `docs/features/*.md` (my sheets are marked Friend / Opus), `docs/MASTER_PLAN.md`, `docs/DECISIONS.md`.

## I write
- `frontend/**`, `e2e/**`
- `backend/app/main.py`, `backend/app/core/**` (except `ws_bus.py`), `backend/app/db/**`, `backend/app/policy/**`, `backend/alembic*`, `backend/pyproject.toml`, `uv.lock`, `backend/Dockerfile`, `backend/tests/conftest.py`, `backend/scripts/**`
- My feature routers: `backend/app/features/{platform,auth_evidence,catalog_sampling,inspection_passports,containment,packaging,dispatch,offline_scanner,recall,report_workflow,alerts,console_demo}/`
- Root `docker-compose.yml`, `Makefile`, `.env.example`, `README.md`, `contracts/openapi.json`

## I never write
Astra's paths: `backend/app/features/{component_vision,seal_vision,spc,passport_crypto,scar_pdf}/`, `backend/app/core/ws_bus.py`, `contracts/ws_events.schema.json`, `docker-compose.https.yml`, `Caddyfile`, `certs/`, `fixtures/images/{inspection,seal}/`. Until Astra's modules land, I use clearly labelled interim adapters behind the same interfaces and replace them on merge.

## Backend rules
- Python 3.12, FastAPI, sync SQLAlchemy 2 + psycopg 3, Alembic, PostgreSQL 16. Every state transition runs in one transaction. Locks are taken in the order heat → lot → unit → package → order → shipment, ascending id.
- Errors use `{error:{code,message,details}}` and fail closed. Write an audit event (hash-chained) and an outbox row in the same transaction as the fact.
- Tests run against real Postgres (`make db`), with race tests on separate connections.

## UX principles
- Show *why*: every BLOCKED/HOLD shows the gate checklist, reason codes and evidence.
- Honesty is a feature: badge `synthetic`, `uncalibrated`, `PROVISIONAL_OFFLINE`, "anomaly score (not a probability)", and "signature ≠ physical authenticity".
- Offline decisions are provisional. Laptop control room plus phone scanner, with large touch targets.
