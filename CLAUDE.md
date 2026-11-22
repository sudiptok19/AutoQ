# CLAUDE.md — Frontend agent instructions (Claude Code)

I own the **frontend, UI/UX and scanner PWA** of AutoQ-Trace. A teammate (GPT agent) owns the backend.

## Read first
`docs/CONTRACT.md` (binding API semantics), `docs/PLAN.md` (§2 Frontend tasks), `docs/DECISIONS.md`.

## Scope
- Edit: `frontend/**`, `e2e/**`.
- Shared files (`docs/CONTRACT.md`, `docs/DECISIONS.md`, `docker-compose.yml`, `Makefile`, `README.md`): only via `contract/<change>` PRs.
- **Never edit `backend/` or `fixtures/images/`.**

## Stack
- Vite + React 18 + TypeScript (strict), React Router, TanStack Query, Tailwind CSS, Recharts, React Flow (trace graph).
- MSW for mocks driven by CONTRACT fixtures; `VITE_API_MODE=mock|live`. Vite proxy `/api` → `http://localhost:8000`.
- Types: hand-written from CONTRACT until backend is live, then `npm run gen:api` (openapi-typescript from `/openapi.json`).
- PWA: vite-plugin-pwa, `idb` for the offline queue, `@noble/ed25519` for local token verification, a QR scanner lib using `getUserMedia`.
- Tests: Vitest for units (canonical JSON, token verify, queue hash chain), Playwright for the demo journey.

## UX principles
- Show *why*: every BLOCKED/HOLD shows gate checklist + reason codes + evidence links.
- Honesty is a feature: badge `synthetic`, `uncalibrated`, `PROVISIONAL_OFFLINE`, "anomaly score (not a probability)", "signature ≠ physical authenticity".
- Never compute authorization client-side as final; offline decisions are provisional.
- Works on a laptop (control room) and a phone (scanner) — responsive, large touch targets on scanner routes.
