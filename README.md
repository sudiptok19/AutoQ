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

## Run (once both halves exist)
```bash
cp .env.example .env
make db
make backend      # http://localhost:8000/docs
make frontend     # http://localhost:5173
```
