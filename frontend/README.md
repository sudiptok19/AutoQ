# AutoQ-Trace — Frontend

Control-room web app (and, next, the phone scanner PWA). Vite + React 19 + TypeScript + Tailwind v4 + TanStack Query + React Flow + Recharts.

```bash
npm install
npm run dev          # http://localhost:5173 — mock API by default
VITE_API_MODE=live npm run dev   # proxy /api → http://localhost:8000 (FastAPI)
npm test             # vitest: canonical JSON + Ed25519 token sign/verify/forge
npm run typecheck
```

Dev logins (all password `autoq-dev`): `op.ravi`, `qe.ananya`, `qm.farhan`, `disp.kiran`, `admin`. Switch role any time from the top-right menu.

## Mock mode
`src/mock/` is an in-browser implementation of `docs/CONTRACT.md` (state in `localStorage`, "Reset demo data" in the sidebar).
It mirrors the policy so the UI is fully demoable before the backend exists — **the FastAPI backend is the authority.**

What is real in mock mode vs. simulated:
| | Mock mode |
|---|---|
| Ed25519 signing/verification, canonical JSON, forged-token rejection | real (`@noble/ed25519`) |
| Image quality gate (Laplacian blur, glare, resolution) | real, on the captured pixels |
| Golden-reference diff + heatmap | real, but only against a **synthetic** rendered bracket; real photos → `HOLD · MODEL_UNCALIBRATED` |
| Seal ORB/RANSAC/SSIM | **simulated** for synthetic fixtures; real photos → `UNVERIFIABLE` (backend does the real CV) |
| Dispatch gate, 4M containment, recall, audit hash-chain, SPC CUSUM | real logic over mock state |

Everything synthetic is badged in the UI.

## Contract deltas proposed (need backend agreement via `contract/*` PR)
- Seed lot `L-2611-A` has 48 units (40 packed + 8 uninspected for the live inspection / first-N demo).
- `GET /evidence/{sha}` must accept `?access_token=` because `<img>` can't send a bearer header.
- `POST /verify` response adds `failure` (reason code when `signature_valid` is false).
- `POST /alerts/{id}/ack {reason}`; `POST /shipments/{id}/close` body may carry `unsynced_offline_events`.
- 4M affected units = unshipped units on the line created ≥ `effective_at − lookback` (unit-level, not whole lots).
