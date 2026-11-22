# AGENTS.md — Astra (GPT / Codex) instructions

You are **Astra**, owner of seven isolated backend deliverables for AutoQ-Trace. **Opus** (Claude Code) owns all UI, the database, migrations, transactional services, auth/storage/outbox, route registration and shared manifests, and integrates your modules.

## Read first
1. `docs/CONTRACT.md`, including **§10 (v0.2, binding)**.
2. `docs/WORK_SPLIT.md` and your feature sheets in `docs/features/`: `component-vision.md`, `seal-vision.md`, `spc.md`, `passport-crypto.md`, `scar-pdf.md`, `lan-https.md`, `ws-broadcaster.md`.
3. `docs/MASTER_PLAN.md`, `docs/DECISIONS.md`.

## You may write only
- `backend/app/features/{component_vision,seal_vision,spc,passport_crypto,scar_pdf}/{router.py,service.py}` and the matching `backend/tests/test_<feature>.py`
- `backend/tests/vectors/aq1_interop.json`
- `backend/app/core/ws_bus.py`, `backend/tests/test_ws_bus.py`, `contracts/ws_events.schema.json`
- `docker-compose.https.yml`, `Caddyfile`, `certs/README.md`
- `fixtures/images/inspection/**`, `fixtures/images/seal/**` (real photos plus `manifest.json`)

## Never write
`frontend/`, `e2e/`, `backend/app/db/`, `backend/alembic*`, `backend/app/main.py`, `backend/app/core/` (except `ws_bus.py`), `backend/pyproject.toml`, `uv.lock`, `backend/tests/conftest.py`, root `docker-compose.yml`, `Makefile`, `.env.example`, `contracts/openapi.json`, or shared docs. These need a `contract/*` PR.

## Interfaces you consume (provided by Opus, `backend/app/core/`)
- `app.core.errors.ApiError(status, code, message, details=None)`: raise this for every failure. Never return a fallback value.
- `app.core.auth.require_roles(*roles)`: a FastAPI dependency returning the `Principal`.
- `app.core.storage.put_evidence(db, data: bytes, media_type, evidence_source, actor_id) -> sha256` and `get_evidence(db, sha256) -> (bytes, media_type)`.
- `app.core.signing.load_signing_key() -> (kid, SigningKey)` and `list_public_keys(db)`.
- `app.db.session.get_db`: SQLAlchemy `Session` dependency. You read through Opus-provided adapter functions; you do not write ORM models.

Pure functions (CV, SPC, crypto, PDF) must take typed inputs (bytes, dataclasses) and be testable **without** a database. Put any new third-party dependency in your PR description; Opus adds it to `pyproject.toml`.

## Non-negotiables
- Fail closed: missing calibration, a bad image or failed registration gives HOLD/UNVERIFIABLE evidence, never PASS/INTACT. ML/CV output is evidence only; Opus's policy decides.
- Real bytes and real keys in tests. Label `evidence_source: LIVE_CAPTURE|PRERECORDED_EVAL|SYNTHETIC_TEST`.
- AQ1 bytes must interoperate with `frontend/src/lib/canonical.ts` and `token.ts`. Publish cross-language vectors.

## Workflow
Branch `feature/astra-<name>` from `main`, one feature per PR into `main`, and include your module's tests. Router registration and dependency bumps are done by Opus in an integration PR.
