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
| `app/adapters/ports_impl.py`: Postgres `Ports` for Astra's routers. `/keys` (A4), `/spc/series` (A3) and `/reports/{id}/pdf` (A5) are mounted. Seal/report ports return 503 until those slices land | implemented + verified |
| Inspections: `POST /inspections` (multipart, `evidence_source`), deterministic verdict policy, HOLD review (`REINSPECT`/`REJECT`), lot-sample state (PASSED/FAILED), SPC baseline freeze plus drift alert on write | implemented + verified (synthetic images) |
| Passports v2 via Astra's AQ1 signer: INDIVIDUAL_100PCT on own PASS, SAMPLED_LOT on a complete passing sample, supersede-on-individual, revoke on REJECT, issuance blocked during open containment runs; `POST /passports/issue`, `GET /passports/{id}`, `POST /verify` (signature vs live eligibility reported separately) | implemented + verified |
| Component vision is **Astra's A1** (`gd-0.3`) behind the inspection policy; interim detector removed | implemented + verified (synthetic textured renders; real-photo thresholds pending Astra's held-out evaluation) |
| Alerts: `GET /alerts`, ack/resolve with reasons, idempotent `raise_alert`, transactional outbox relayed to Astra's WS bus; `POST /ws/tickets` (single-use, 60 s) plus `WS /ws/events?ticket=` | implemented + verified |
| 4M containment (`POST/GET /four-m-events`, `GET /containments[/{id}]`, `POST /containments/{id}/clear`): one run per event; scope frozen from policy lookback (existing stock rechecked when unplanned) plus new production enrolled while open; distinct-unit PASS credits per epoch, HOLD = 0, REJECT resets the epoch; READY_FOR_CLEARANCE, then QM clearance with reason; passports HELD during RED and re-minted INDIVIDUAL + `four_m_tagged` after clearance; FOUR_M_HOLD alert and LOT_4M_CHANGED events | implemented + verified (incl. overlap and concurrent credits) |
| Orders (`GET/POST /orders`) and packaging: `POST /packages`, add by unit or passport token (eligibility + part + single open membership), removal keeps membership history; `POST /packages/{id}/seal` enrolls a seal revision and signs a **PKG v2** token (order, exact passports, reference digest, revision id); `unpack` invalidates revision and token; `re-enroll` from SEAL_REVIEW (QE) | implemented + verified |
| Seal rechecks `POST /packages/{id}/seal-checks` through the Ports seam; only committed against the revision they were computed for (`409 SEAL_REVISION_CHANGED`); SUSPICIOUS → SEAL_REVIEW + CRITICAL audible SEAL_TAMPER alert | implemented + verified |
| Seal vision is **Astra's A2** (`seal-orb-ssim-0.2`), mounted directly; interim removed | implemented + verified (synthetic renders) |
| Dispatch: `GET/POST /shipments`, `POST /shipments/{id}/scans` runs the **11 ordered gates** (signature, key, identity incl. stale-revision, lifecycle, containment, quarantine, order, duplicate, seal freshness, quantity, sequence) under unit → package → order → shipment locks; every attempt persisted; idempotent on `client_event_id` (reuse with other content → 409); alerts for forgery, duplicate, quarantine and revoked scans | implemented + verified (incl. two-loader race) |
| `POST /shipments/{id}/close` (quantity reconciliation and re-validation); `POST /shipments/{id}/events`: DEPARTED re-runs every gate in one transaction and seals `manifest_sha256`, or returns 409 and **commits** REMOVAL_REQUIRED tasks (shipment reopened); CHECKPOINT/DELIVERED need a real departure; `occurred_at` validated and stored apart from `recorded_at` | implemented + verified |
| Segregation tasks: `GET /segregation-tasks`, `POST /segregation-tasks/{id}/complete` (removal completion unloads the package); idempotent creation | implemented + verified |
| Inspection profiles: `POST /inspection-profiles`, `GET /inspection-profiles/{id}`, golden upload (A1 route) creates an uncalibrated revision; `POST /inspection-profiles/{id}/calibrate` (QM, hold/reject plus held-out evaluation JSON stored as evidence) creates a NEW calibrated revision | implemented + verified |
| 8D/SCAR workflow: `POST /reports/8d` (FOUR_M / ALERT / RECALL) builds a frozen, hashed snapshot with evidence refs and D4/D6/D8 left PENDING_HUMAN; `GET /reports[/{id}]`; `POST /reports/{id}/approve` (QM plus review evidence) makes a new revision; Astra's A5 renders `GET /reports/{id}/pdf` through `load_snapshot`/`record_pdf` (byte-determinism enforced) | implemented + verified |

Interfaces Astra's modules consume are listed in `AGENTS.md`.
