# AutoQ-Trace — Team Plan

> **Ownership revised 2026-11-23 (v0.2).** The split below is superseded by [WORK_SPLIT.md](WORK_SPLIT.md): Opus (Claude) owns UI plus DB/transactional backend; Astra (GPT) owns component CV, seal CV, SPC, AQ1 crypto, 8D PDF, LAN HTTPS gateway and WS bus. Branches are now `feature/opus-<name>` / `feature/astra-<name>`; shared contract changes stay on `contract/*`.

Two people, two coding agents, one repo. The split is designed so neither person waits on the other:
the **API contract** ([CONTRACT.md](CONTRACT.md)) is the handshake, and the frontend develops against
mocks generated from it until the backend is live.

| | Owner | Agent | Owns (directories) |
|---|---|---|---|
| **Frontend / UI-UX / scanner PWA** | You | Claude Code | `frontend/`, `e2e/`, `CLAUDE.md` |
| **Backend / policy engine / CV / crypto** | Teammate | GPT (Codex / ChatGPT) | `backend/`, `fixtures/images/`, `AGENTS.md` |
| **Shared (both must agree)** | Both | — | `docs/CONTRACT.md`, `docs/DECISIONS.md`, `docker-compose.yml`, `README.md`, `Makefile` |

Hackathon domain: **Manufacturing & Industrial Innovation** (secondary fit: Safety & Security).

---

## 1. Architecture (decided)

A modular monolith — no microservices. One authoritative policy layer on the server.

```
┌──────────────────────── frontend/ (Vite + React + TS) ─────────────────────────┐
│  Control-room web app (desktop)        Scanner PWA (phone, same codebase)      │
│  TanStack Query · Tailwind · Recharts  IndexedDB queue · @noble/ed25519 verify │
│  MSW mocks (from CONTRACT fixtures)    Service worker · camera capture         │
└──────────────────────────────┬─────────────────────────────────────────────────┘
                               │  HTTPS  /api/v1  (Vite proxy in dev)
┌──────────────────────────────▼──────── backend/ (FastAPI, Python 3.12) ────────┐
│  auth/roles · master data · inspection (OpenCV quality gate + golden diff)     │
│  passports (Ed25519, AQ1 tokens) · 4M containment · packages · seal CV         │
│  dispatch gate (transactional) · trace/recall (recursive CTE) · SPC (CUSUM)    │
│  offline snapshot + sync · 8D draft (template → PDF) · hash-chained audit      │
└──────────────────────────────┬─────────────────────────────────────────────────┘
                     PostgreSQL 16 (docker)  +  evidence/ (content-addressed files)
```

Why these choices (details in [DECISIONS.md](DECISIONS.md)):
- **Vite SPA instead of Next.js** — no SSR need; PWA + offline is simpler; one build for desktop and phone.
- **FastAPI** — Python is where OpenCV/scikit-image live, so CV and policy stay in one process. FastAPI
  also emits `/openapi.json`, from which the frontend generates TypeScript types (no hand-syncing).
- **Postgres, not SQLite** — the dispatch gate needs real row locks and unique constraints under concurrency.
- **Golden-reference diff before PatchCore** — reliable on a laptop with phone photos; PatchCore is P2.

---

## 2. Who builds what

### Frontend — You (Claude Code)
1. **Design system & app shell** — visual identity, tokens, dark/light, components, role-aware nav, login.
2. **Mock layer** — MSW handlers + fixtures that follow CONTRACT.md exactly, so every screen works before the backend exists. A `VITE_API_MODE=mock|live` switch.
3. **Control-room screens** — master data (suppliers, parts, heats, lots), inspection capture/review with evidence + heatmap, passports, 4M events & containment board, packing & sealing, orders & shipments, dispatch gate live view, trace graph & recall blast radius, SPC charts, 8D draft viewer, audit log.
4. **Scanner PWA** — camera QR scan, seal photo capture, offline mode (IndexedDB queue, signed snapshot, local Ed25519 verify, `PROVISIONAL_OFFLINE` labeling, sync & conflict view).
5. **Transparency UI** — every synthetic/uncalibrated/fixture value is visibly badged; every block shows its reason codes.
6. **Browser E2E** (Playwright) for the 90-second demo journey against the live backend.
7. **HTTPS for phones** — `vite --host` with a local cert (mkcert) or a tunnel, documented.

### Backend — Teammate (GPT agent)
1. **Project + DB** — FastAPI, SQLAlchemy 2, Alembic migrations, Postgres in docker-compose, seed script.
2. **Auth & roles** — JWT login, 5 roles (see CONTRACT §2), server-side permission checks on every write.
3. **Master data + genealogy** — suppliers, parts, heats (+certificate evidence & sha256), lots, serialized units.
4. **Inspection** — image quality gate (blur/glare/resolution), golden-reference align + diff + heatmap, deterministic PASS/HOLD/REJECT, review/override rules.
5. **Passports & tokens** — Ed25519 signing, canonical JSON, `AQ1.` tokens, key ids, `/keys`, `/verify`.
6. **4M containment** — events, configurable rules, obligations, hold/clear state machine.
7. **Packaging & seal CV** — packages, items, ORB + RANSAC + SSIM seal enrollment/recheck with `UNVERIFIABLE`.
8. **Dispatch gate** — ordered gate checks, transactional loading, idempotency, manifest close, shipment movement events.
9. **Trace & recall** — recursive genealogy query, preview + atomic quarantine, in-transit vs delivered from events.
10. **SPC** — per method-version baseline, one-sided CUSUM, alerts.
11. **Offline** — signed snapshot, `/sync/scans` with per-event outcomes and hash-chain check.
12. **8D/SCAR** — evidence-linked draft JSON + PDF (reportlab), sections tagged FACT/HYPOTHESIS/PENDING.
13. **Audit** — append-only, hash-chained audit events + verify endpoint.
14. **Fixtures** — real photos: golden part, good part, defective part, blurry; seal enrolled / intact / torn / bad-light. Put in `fixtures/images/` with a README describing how each was captured.
15. **pytest integration tests** with a real test DB for acceptance scenarios 1–10 (brief §8).

### Together
- Freeze CONTRACT v0.1 (first hour). Agree the demo seed scenario (CONTRACT §9).
- Integration checkpoints (below). Demo script + capability matrix + pitch at the end.

---

## 3. Phases & checkpoints

Times assume a ~36–48h hackathon; scale proportionally.

| Phase | Frontend (You) | Backend (Teammate) | Exit criteria |
|---|---|---|---|
| **0 · Kickoff** (≤1h) | Read CONTRACT, raise objections | Read CONTRACT, raise objections | CONTRACT v0.1 tagged, both agents have their brief |
| **1 · Spine** | Design system, shell, login, MSW mocks, inspection → passport → package → order → dispatch scan → trace screens | Auth, master data, inspection (gate + golden diff), passports, packages, orders, shipments, dispatch gate, trace/recall, audit, seed | **Checkpoint A:** frontend flips to `live`; acceptance scenarios 1, 2, 5, 6 pass end-to-end |
| **2 · Differentiators** | 4M board, seal capture/recheck UI, SPC charts, scanner PWA + offline, 8D viewer, recall blast-radius visual | 4M containment, seal CV, SPC/CUSUM, offline snapshot + sync, 8D PDF | **Checkpoint B:** scenarios 3, 4, 7, 8, 9, 10 pass |
| **3 · Harden & demo** | Playwright demo E2E, polish, empty/error/loading states, phone HTTPS | `make demo-reset`, perf measurement, test gaps, fixture docs | 90-second demo runs 3× in a row without a hiccup |
| **4 · Pitch** | Screens/recording for slides | Capability matrix, limits section | Slides + README + matrix done |

**Rule:** if a backend endpoint is late, the frontend keeps working on mocks — nobody blocks.
**Rule:** if a feature isn't working by its checkpoint, it gets cut or labeled honestly, not faked.

---

## 4. Git workflow (your GitHub repo)

- `main` is always runnable. No direct pushes after Phase 0 — use PRs.
- Branches: `fe/<feature>` (you), `be/<feature>` (teammate), `contract/<change>` (either, needs the other's approval).
- Small PRs, merged often (at least every few hours) so integration surprises stay small.
- Each side only edits its own directories. Shared files change only via `contract/*` PRs.
- Commit fixtures images with Git LFS only if they get large (>10 MB total); otherwise plain git.
- Contract changes: bump the version line at the top of CONTRACT.md and add a changelog entry.

### Contract change protocol
1. Open a `contract/<change>` PR editing CONTRACT.md (+ fixture JSON if relevant).
2. Other person approves (a 2-minute look is fine).
3. Backend implements; frontend updates mocks. Once the backend serves `/openapi.json`, the frontend regenerates types with `npm run gen:api`, and **the running backend's OpenAPI becomes the source of truth for field shapes**, with CONTRACT.md as the design rationale.

---

## 5. Kickoff prompt for the teammate's GPT agent

Codex reads `AGENTS.md` automatically. If using ChatGPT chat instead, paste this:

> You are the backend engineer for AutoQ-Trace. Read `AGENTS.md`, `docs/CONTRACT.md`, `docs/DECISIONS.md`,
> and `docs/PLAN.md` in the repo. You own only `backend/` and `fixtures/images/`. Implement the API exactly as
> CONTRACT.md specifies, starting with Phase 1 (auth, master data, inspection, passports, packages, orders,
> dispatch gate, trace/recall, audit, seed). Write pytest integration tests against a real Postgres for each
> acceptance scenario. Never fake CV results or signatures; label synthetic data with `synthetic: true`.
> If the contract is wrong or missing something, propose a change instead of silently diverging.

---

## 6. The 90-second demo (target)

1. **Lot from heat** — show lot `L-2611-A` linked to heat `H-7781` with its mill certificate (sha256 shown).
2. **Inspect** — capture a bracket photo → quality gate passes → golden diff heatmap → `PASS` → signed passport appears with QR.
3. **4M event** — log an unplanned *Machine* change (tool insert replaced) on Line 2 → containment opens, 12 units go dispatch-ineligible, first-5 full-inspection obligation appears.
4. **Blocked load** — dispatcher scans a contained package on the phone → **BLOCKED · CONTAINMENT_ACTIVE**; then a tampered seal → **SEAL_SUSPICIOUS** with diff image.
5. **Recall** — quarantine heat `H-7781` → blast radius: N units, M packages, 1 shipment in transit, 1 delivered (notify only) → re-scan → **BLOCKED · QUARANTINED**.
6. **8D draft** — open the generated draft: facts with evidence links, root cause marked *hypothesis — awaiting human*.

Q&A extras: offline scanning + sync conflicts, SPC drift alert, audit chain verify, forged QR rejection.
