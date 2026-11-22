# AutoQ-Trace collaboration plan

**For approval by Sudipta (Friend / Opus 5.5) and Suman (Me / Astra).** Suman owns five isolated backend features, the LAN HTTPS override and the standalone WebSocket broadcaster. Sudipta owns all UI/UX, database and transactional work, shared registration, manifests and types. Review [the five contract decisions](CONTRACT_ALIGNMENT.md) alongside [the master plan](MASTER_PLAN.md).

## Repository audit

Read-only inspection of [main at d19c429](https://github.com/sudiptok19/AutoQ/tree/d19c4294a871ef04b58411423f4a33d9dcd57f76) and [fe/foundation at c120452](https://github.com/sudiptok19/AutoQ/tree/c1204525b1f1fc94ef92a80d00c3a8f09f3a5e07). The original project workspace had no checkout. The audit covers published Git objects; unpublished local edits on either developer’s computer are unknown. No application, Docker database, browser, camera, or test suite was run during this source audit.

| Requested check | Result before writing these documents |
|---|---|
| `git status` | Clean working tree on `main`, tracking `origin/main`. |
| `git branch -a` | Local `main`; remote `origin/HEAD -> origin/main`, `origin/main`, `origin/fe/foundation`. |
| `git log -n 15 --oneline` | Only `d19c429 Project plan, API contract, and repo skeleton` on main. |
| All-branch history | Three additional commits on foundation, oldest first: `772ad07` frontend foundation/design/mock/screens, `f3cfd08` ignore TS build info, `c120452` dock scanner PWA/offline sync. All four commits list SUDIPTA Karmakar as author. |
| Full file inventory | `git ls-tree -r --name-only` on both tips: 15 tracked paths on main, 54 on foundation. Complete branch-by-branch inventory below. |

### What Opus has built

- **Repository and DB setup.** Main supplies the brief, decisions, API contract, role/agent instructions, Make targets, dev environment example and a Compose PostgreSQL 16 service with a persistent volume. Both branches have only `backend/.gitkeep` under backend. There are no Python routes, ORM models, migrations, backend dependency manifest, actual photo fixtures or Playwright tests. A running database instance cannot be inferred from a Compose file.
- **Frontend.** The foundation branch implements React 19.1.1, TypeScript, Vite 7, React Router 7, TanStack Query 5, Tailwind 4, React Flow and Recharts. Login is an auth gate; the routed pages are overview, lot list/detail, unit/passport detail, inspection, containment, packing, dispatch, trace/recall, SPC, audit and a separate dock scanner. Overview and SPC show alerts. There is no independent Alert Centre, WebSocket listener/siren or report viewer. Report types exist.
- **Style.** The shipped source defines graphite surfaces, teal accents, light/dark themes, semantic quality colors, IBM Plex Sans/Mono, tabular numbers and reusable shell/card/badge components. The pasted light-only cobalt/crimson proposal is design input for Opus. Astra has no styling assignment.
- **Real browser code.** Camera QR decoding, Ed25519, canonical JSON, IndexedDB queue, per-device hash chaining, the snapshot verifier and pixel-quality metrics exist. Offline scans are provisional. The Vite PWA has a production service worker configuration and optional local HTTPS. A trusted physical-phone session is still to be verified.
- **Browser simulations.** `VITE_API_MODE` defaults to mock. The app calls a custom in-browser router backed by local storage, despite an older plan naming MSW. It implements quality decisions, package/dispatch gates, 4M progress, recall, SPC, audit and replay over mock state. Golden comparisons use a rendered synthetic bracket. Seal outcomes for fixtures are simulated. Real photo comparison, authoritative signing/issuance, protected storage and multi-user transactions require the backend.
- **Test coverage present in source.** Token and offline tests are tracked. The offline tests use fake IndexedDB. There is no live PostgreSQL, real-phone or browser-driven integration proof in the checked-in tree. The frontend README describes a type-generation command which is absent from the current package scripts.

### Browser API inventory

The following 51 method/path pairs were registered in `frontend/src/mock/router.ts` at the foundation tip. Prefix each with `/api/v1`. This inventory records browser handlers only; [CONTRACT.md](CONTRACT.md) also lists intended endpoints without handlers. Evidence in mock mode is resolved via a helper.

```text
POST /auth/login
GET /auth/me
GET /health
POST /demo/reset
GET /suppliers
GET /parts
GET /lines
GET /heats
GET /customers
GET /lots
GET /lots/:id
GET /units
GET /units/:id
GET /policies
POST /inspections
GET /inspections
POST /inspections/:id/review
GET /keys
POST /verify
GET /four-m-events
POST /four-m-events
GET /containments
GET /containments/:id
POST /containments/:id/clear
GET /orders
GET /orders/:id
GET /packages
GET /packages/:id
POST /packages
POST /packages/:id/items
DELETE /packages/:id/items/:uid
POST /packages/:id/seal
POST /packages/:id/seal-checks
GET /shipments
GET /shipments/:id
POST /shipments/:id/scans
POST /shipments/:id/close
POST /shipments/:id/events
POST /recalls/preview
POST /recalls
GET /recalls
GET /recalls/:id
GET /trace
GET /spc/series
GET /alerts
POST /alerts/:id/ack
GET /audit
GET /audit/verify
GET /dashboard/summary
GET /offline/snapshot
POST /sync/scans
```

## Proposed ownership

| Sole feature owner | Deliverable and handoff | Status across published branches |
|---|---|---|
| Me / Astra | [Component image quality and reference inspection](features/component-vision.md) | Partial |
| Me / Astra | [Physical seal recheck](features/seal-vision.md) | Partial |
| Me / Astra | [SPC drift measurement](features/spc.md) | Partial |
| Me / Astra | [Ed25519 signing and verification](features/passport-crypto.md) | Partial |
| Me / Astra | [Evidence-linked 8D/SCAR PDF rendering](features/scar-pdf.md) | Not started |
| Me / Astra | [LAN HTTPS phone gateway](features/lan-https.md) | Not started |
| Me / Astra | [WebSocket broadcaster and event schema](features/ws-broadcaster.md) | Not started |
| Friend / Opus | [Application and database foundation](features/platform.md) | Partial |
| Friend / Opus | [Authentication, evidence and audit trail](features/auth-evidence.md) | Partial |
| Friend / Opus | [Catalog, serialized lots and frozen sampling](features/catalog-sampling.md) | Partial |
| Friend / Opus | [Inspection verdict, review and passport lifecycle](features/inspection-passports.md) | Partial |
| Friend / Opus | [4M policy and clearance](features/containment.md) | Partial |
| Friend / Opus | [Package membership and seal revision lifecycle](features/packaging.md) | Partial |
| Friend / Opus | [Orders, load decisions and shipment movement](features/dispatch.md) | Partial |
| Friend / Opus | [Dock scanner, snapshots and offline reconciliation](features/offline-scanner.md) | Partial |
| Friend / Opus | [Genealogy, quarantine and segregation tasks](features/recall.md) | Partial |
| Friend / Opus | [Corrective-action draft and approval workflow](features/report-workflow.md) | Not started |
| Friend / Opus | [Alert Centre, WebSocket and audible alarm](features/alerts.md) | Partial |
| Friend / Opus | [Control-room UI, dashboard and reproducible demo](features/console-demo.md) | Partial |

**Status convention.** Partial means at least one implementation layer is present; each feature sheet identifies the missing layer. Not started means implementation of that feature is absent even if a type or design paragraph exists. Existing browser files always stay with Opus, including browser counterparts of Astra’s seven deliverables.

## File ownership and merge workflow

| Hotspot | Sole writer during implementation |
|---|---|
| `backend/app/db/`, `backend/alembic/`, `backend/alembic.ini`, DB sessions, SQL queries, models, constraints, migrations, seeds and test DB setup | **Opus only.** Astra specifies the required columns, uniqueness, nullability or interface in the feature sheet. Opus maps the agreed contract and ships database adapters. |
| `backend/app/main.py`, central policy, dependency injection, global errors/auth, `backend/pyproject.toml`, `backend/uv.lock`, shared pytest fixtures | **Opus only.** Astra sends router imports, required packages, typed adapter signatures and expected errors in its PR. Opus registers each router once. |
| `frontend/`, `e2e/`, shared TS types, API client, routes, mocks, CSS, `frontend/package.json` and lockfile | **Opus only**, including UI already started for CV, signing, SPC and reports. |
| Base `docker-compose.yml`, Makefile, `.env.example`, README, agent instructions, CI, generated `contracts/openapi.json`, `docs/CONTRACT.md` and `docs/DECISIONS.md` | **Opus integrates; both review shared contract changes.** Local `.env`, credentials, signing keys and private CAs stay outside Git. |
| `docker-compose.https.yml`, `Caddyfile`, `certs/README.md`, `backend/app/core/ws_bus.py`, `backend/tests/test_ws_bus.py`, `contracts/ws_events.schema.json` | **Astra only.** Opus consumes the event schema and registers the endpoint. The OpenAPI exporter may validate the WS schema but must never rewrite it. |
| New feature routers, services and tests | Exactly one `backend/app/features/<feature>/router.py`, one `service.py`, and one `backend/tests/test_<feature>.py` for each HTTP feature. The infrastructure-only gateway and broadcaster use their smaller explicit inventories. Owners write only their assigned paths and explicitly listed extras. No common “utils” module under concurrent development. |
| Physical fixtures | **Astra** owns the named component/seal photos, manifests and crypto public vectors. Opus can consume them. The shared fixture README remains an integration file. |
| These review documents | Each feature owner proposes amendments to their sheet after approval. Opus integrates edits to the index. |

1. Suman confirmed that the two audited remote tips are the complete current state. Use the file reservations below for work begun after approval.
2. Preserve `fe/foundation` and its history. Opus merges it via its own PR. For future feature work, branch from updated `main` as `feature/astra-<name>` or `feature/opus-<name>`. One feature per PR; target `main`. No direct pushes to main or concurrent editing of an assigned path.
3. Current `AGENTS.md`, `CLAUDE.md` and `docs/PLAN.md` assign all backend work to the backend agent and prescribe `be/*` and `fe/*`. Opus opens a separate, jointly reviewed `contract/work-split` PR to align these instructions and ratify the interface deltas. Reserve `contract/*` for changes to shared contracts. This review does not amend those files.
4. After approval, both sides can start independently. Astra tests pure computations using real bytes, keys, PDFs and ordered score sequences. Opus builds adapters and DB-backed services. Merge feature PRs into main only with the matching dependencies available. Run an integrated live test before marking a feature complete.
5. At PR review, compare changed paths to the sheet. Route registration, migrations, dependency bumps and generated types go in an Opus integration PR. If a change requires crossing ownership, update the contract and assign one writer before editing. This avoids the known file collisions without promising conflict-free merges after unrelated upstream changes.

## Contract decisions before integration

Record the team’s five A/B choices in [CONTRACT_ALIGNMENT.md](CONTRACT_ALIGNMENT.md). It includes exact current/proposed wire examples, mock discrepancies and the file impact of each decision. The feature sheets describe Option A. The published v0.1 contract remains the baseline until the two owners ratify the choices in a contract PR.

## Delivery order after both approvals

1. **Contract and foundation.** Opus lands the agreed contract changes, dependency manifests, DB foundation, real auth and the evidence store. Astra supplies the separate TLS override and starts independent crypto vectors and held-out phone-photo collections.
2. **Release and dispatch.** Integrate Astra’s image evidence and signer with Opus’s frozen samples, 4M rules and passport lifecycle. Connect Astra’s seal recheck to revision-aware packing, transactional loading and recall.
3. **Offline and analysis.** Opus wires the existing scanner to live snapshots/sync and connects its committed alert outbox to Astra’s broadcaster. Integrate Astra’s SPC evaluator and PDF renderer against persisted inspections and report snapshots.
4. **Joint acceptance.** Use live HTTP, real PostgreSQL, a real phone, actual Ed25519 keys, photographs and PDF bytes. Exercise two concurrent loaders and quarantine versus departure. Record held-out evaluation counts, explicit blocked reasons and any unverified hardware in the release evidence.

## Full tracked file tree

Each row is one exact repository-relative path. M = present on main; F = present on fe/foundation. Git metadata and ignored machine-local files are outside the tracked tree.

```text
M F  path
  F  .claude/launch.json
M F  .env.example
M F  .gitignore
M F  AGENTS.md
M F  CLAUDE.md
M F  Makefile
M F  README.md
M F  backend/.gitkeep
M F  docker-compose.yml
M F  docs/BRIEF.md
M F  docs/CONTRACT.md
M F  docs/DECISIONS.md
M F  docs/PLAN.md
M F  e2e/.gitkeep
M F  fixtures/images/README.md
M    frontend/.gitkeep
  F  frontend/README.md
  F  frontend/index.html
  F  frontend/package-lock.json
  F  frontend/package.json
  F  frontend/public/mark.svg
  F  frontend/src/api/client.ts
  F  frontend/src/api/hooks.ts
  F  frontend/src/api/types.ts
  F  frontend/src/auth/auth.tsx
  F  frontend/src/components/domain.tsx
  F  frontend/src/components/ui.tsx
  F  frontend/src/index.css
  F  frontend/src/layout/AppShell.tsx
  F  frontend/src/lib/canonical.ts
  F  frontend/src/lib/chain.ts
  F  frontend/src/lib/cv.ts
  F  frontend/src/lib/token.test.ts
  F  frontend/src/lib/token.ts
  F  frontend/src/main.tsx
  F  frontend/src/mock/router.ts
  F  frontend/src/mock/state.ts
  F  frontend/src/pages/Audit.tsx
  F  frontend/src/pages/Containment.tsx
  F  frontend/src/pages/Dispatch.tsx
  F  frontend/src/pages/Inspect.tsx
  F  frontend/src/pages/Login.tsx
  F  frontend/src/pages/Lots.tsx
  F  frontend/src/pages/Overview.tsx
  F  frontend/src/pages/Packing.tsx
  F  frontend/src/pages/Scanner.tsx
  F  frontend/src/pages/Spc.tsx
  F  frontend/src/pages/Trace.tsx
  F  frontend/src/pages/UnitPage.tsx
  F  frontend/src/scanner/QrCamera.tsx
  F  frontend/src/scanner/offline.test.ts
  F  frontend/src/scanner/offline.ts
  F  frontend/src/scanner/store.ts
  F  frontend/tsconfig.json
  F  frontend/vite.config.ts
```
