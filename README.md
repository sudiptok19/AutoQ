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

**Demo script (about 5 minutes).** Reseed first. Open the control room as `qm.farhan` on the laptop, and keep a second window ready for each other role.
1. **Inspection** (`op.ravi`, Inspect): a good part passes and gets a signed passport, badged synthetic. A cracked part is rejected. A blurry capture becomes HOLD. On Line 3, under the active 4M containment, a passing part gets **no** passport, and the screen says why.
2. **Dispatch gate** (`disp.kiran`, Dispatch, `SHP-0004`): `PKG-0004` loads. `PKG-0005` is refused until its seal is rechecked. `PKG-0006` (torn seal) is refused. A forged label fails the signature check, and the alert reaches the control room live, with the siren.
3. **Offline scanner** (phone, `/scanner`): download a snapshot, go offline and scan. Each decision is shown as **PROVISIONAL_OFFLINE**, and the label signature and key validity are checked on the phone. Reconnect to sync, then end the offline session so the shipment can close.
4. **Recall** (`qm.farhan`, Trace `H-7781`): the preview writes nothing; executing it quarantines every affected unit and package in one step. Draft the 8D: each statement is labelled and cites its evidence. Approval needs an uploaded review record, and then the PDF downloads.
5. **SPC** (`qe.ananya`, then `qm.farhan`): investigate the Line 2 drift alert. On synthetic data the honest answer is INSUFFICIENT_EVIDENCE, and the manager rejects it with a reason.
6. **Audit** (`admin`): the hash chain still verifies after the whole story.

What to say out loud: a signature proves the data, not the physical part. The anomaly score is not a probability. Offline decisions are provisional until the server accepts them.

**Mock mode (no backend):** `make frontend` runs everything in the browser against an in-memory mock API.

**End-to-end tests (Playwright):**
```bash
cd e2e && npm ci && npx playwright install chromium   # once
make db                                               # PostgreSQL must be running
make e2e                                              # disposable DB + API :8019 + UIs :5180 (live) / :5181 (mock)
```
The suite never touches your dev, demo or verification databases:
- It creates, migrates and seeds its own `autoq_e2e` database from scratch on every run.
- It starts its own API on a dedicated port and never attaches to a server that is already running.
- With `AUTOQ_E2E=1`, the API refuses to boot, and the demo reset refuses to run, unless the database is local and named `*_e2e`.
- It signs with its own throwaway key (`aq-e2e-1`, created under `e2e/.tmp`), never the dev key, and runs Python in UTF-8 mode, so Windows consoles work too.

Use `AUTOQ_E2E_DB=<name>_e2e` to choose another disposable database and `AUTOQ_E2E_API_PORT` to change the API port.

The `live` project plays the demo in order:
- sign-in and role gates;
- inspection PASS / REJECT / HOLD, plus no passport under 4M containment;
- dispatch blocks, replay, recheck and accept;
- recall, tasks and release;
- 8D approval and PDF;
- live alerts across two browsers;
- a mounted phone scanner reconciling offline loads (acceptance, a conflict, QE verification, ending the offline session, close) on a second shipment staged only in the e2e database;
- audit verification.

The `mock` project is a backend-free smoke test (`make e2e-mock`). `npm run report` opens the last HTML report.
