# AutoQ-Trace

Automotive component quality, traceability and dispatch-integrity platform: material heat → lot → inspected,
signed unit passport → sealed package → policy-gated dispatch → one-click recall and evidence-backed 8D drafts.

> Hackathon project. Fictional demo data; no OEM endorsement implied.

| Doc | What |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | Team split, phases, git workflow, demo script |
| [docs/CONTRACT.md](docs/CONTRACT.md) | API + domain contract between frontend and backend |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decision log |
| [docs/BRIEF.md](docs/BRIEF.md) | Original product brief |

## Layout
- `frontend/` — Vite + React control room and scanner PWA (owner: frontend)
- `backend/` — FastAPI, policy engine, CV, crypto (owner: backend)
- `fixtures/images/` — real photo fixtures for CV tests
- `e2e/` — Playwright demo journey

## Run

**Live demo (real backend):**
```bash
cp .env.example .env
make db           # PostgreSQL 16 on :5433
make migrate
make keys         # dev Ed25519 signing key (once)
make seed-demo    # wipes the LOCAL db and plays the demo story through the API (~20 s)
make backend      # API on :8010 (docs at /docs)
cd frontend && VITE_API_MODE=live npm run dev   # UI proxies /api to :8010
```
Sign in with any dev account (password `autoq-dev`). The header shows **LIVE** while the event socket is connected. Alerts pop up on every open control-room screen, with a siren you can mute. The seed leaves:
- **Dock demo:** shipment `SHP-0004` open at Dock 3. `PKG-0004` scans ACCEPTED; `PKG-0005` is blocked until a dispatcher rechecks its seal; `PKG-0006` has a torn seal and is in SEAL_REVIEW.
- **Recall:** heat `H-7781` is ready to recall from Trace.
- **Containment:** an active 4M containment on Line 3.
- **SPC:** a drift alert on Line 2.

All images and the profile calibration are **SYNTHETIC_TEST**, and the UI badges them. Seal rechecks expire after 60 min, so reseed shortly before presenting.

**Mock mode (no backend):** `make frontend` runs everything in the browser against an in-memory mock API.

**End-to-end tests (Playwright):**
```bash
cd e2e && npm ci && npx playwright install chromium   # once
make db && make migrate                               # live specs need PostgreSQL
make e2e                                              # reseeds, starts API :8010 + UIs :5180 (live) / :5181 (mock)
```
The `live` project reseeds the **local** database and then plays the demo in order:
- sign-in and role gates;
- inspection PASS / REJECT / HOLD, plus no passport under 4M containment;
- dispatch blocks for a stale seal, a torn seal, a forged label and a duplicate, then a replay, a recheck and an accept;
- recall preview and execute, tasks, release;
- an 8D approved with an uploaded review record, and the PDF downloaded;
- audit chain verification.

The `mock` project is a backend-free smoke test. Running servers on those ports are reused. `npm run test:mock` needs no database, and `npm run report` opens the last HTML report.
