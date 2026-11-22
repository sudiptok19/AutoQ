# AGENTS.md — Backend agent instructions (GPT / Codex)

You are the **backend engineer** for AutoQ-Trace. A second engineer (using Claude Code) owns the frontend.

## Read first
1. `docs/CONTRACT.md` — the API and domain semantics you must implement. **This is binding.**
2. `docs/PLAN.md` — your task list (§2 "Backend") and phases.
3. `docs/DECISIONS.md` — decisions already made; don't relitigate silently.

## Scope
- You may edit: `backend/**`, `fixtures/images/**`.
- Shared files (`docs/CONTRACT.md`, `docs/DECISIONS.md`, `docker-compose.yml`, `Makefile`, `README.md`): only via a `contract/<change>` branch/PR the other engineer approves.
- **Never edit `frontend/` or `e2e/`.**

## Stack
- Python 3.12 (`uv`), FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL 16 (docker-compose service `db`, port 5432).
- OpenCV (`opencv-python-headless`), scikit-image (SSIM), NumPy. `cryptography` or `PyNaCl` for Ed25519. `reportlab` for PDF.
- pytest + httpx; tests run against a real Postgres test database, not mocks.
- Layout suggestion: `backend/app/{api,domain,policy,cv,crypto,db,reports}`, `backend/tests/`, `backend/alembic/`.
- Serve on `:8000`, all routes under `/api/v1`. Enable CORS for `http://localhost:5173` and `https://localhost:5173`.

## Non-negotiables
- All authorization/policy decisions happen server-side in one policy module. ML/CV output is evidence, never approval.
- Fail safe: missing evidence, bad image quality, uncalibrated model, unknown key, unverifiable seal → HOLD / BLOCKED / UNVERIFIABLE, never PASS / INTACT.
- Dispatch scans and recalls are transactional with row locks + unique constraints; scans are idempotent on `client_event_id`.
- Persist blocked scans and their reasons. Write a hash-chained audit event for every state-changing action with actor + reason.
- Private signing keys: from env/file outside the repo (`AUTOQ_SIGNING_KEY_PATH`); generate a dev key on first run into `backend/.keys/` (gitignored).
- Never fake CV scores or signatures. Synthetic/fixture-derived data gets `synthetic: true`.
- Evidence files are content-addressed by sha256 and never overwritten.
- Return errors in the contract's error shape with the contract's reason codes.

## Done means
- `make backend-test` green, covering acceptance scenarios 1–10 from the brief (map them in `backend/tests/README.md`).
- `POST /api/v1/demo/reset` produces exactly the seed in CONTRACT §9.
- `/openapi.json` matches CONTRACT field names (the frontend generates types from it).

## Commit style
Branch `be/<feature>`, small PRs into `main`. End commit messages with a short summary line; no secrets committed.
