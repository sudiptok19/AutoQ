# AutoQ-Trace — Frontend

Control-room web app plus the offline-capable phone scanner PWA. Vite + React 19 + TypeScript + Tailwind v4 + TanStack Query + React Flow + Recharts.

```bash
npm install
npm run dev          # http://localhost:5173 — mock API by default
VITE_API_MODE=live npm run dev   # proxy /api → http://localhost:8000 (FastAPI)
npm test             # vitest: tokens, offline gate, device hash chain (IndexedDB via fake-indexeddb)
npm run typecheck
```

Dev logins (all password `autoq-dev`): `op.ravi`, `qe.ananya`, `qm.farhan`, `disp.kiran`, `admin`. Switch role any time from the top-right menu.

## Dock scanner (`/scanner`, phone PWA)
Camera QR scanning (native `BarcodeDetector`, `jsQR` fallback), online gate decisions, and an offline mode:

1. **Setup → Download signed snapshot** while online. The device verifies the snapshot’s Ed25519 signature and caches trusted keys, allowed and held packages, and the expected quantity. TTL comes from policy (4 h by default).
2. **Offline**, the device checks the signature, key, order, holds as of the snapshot, local duplicates and quantity. The result is only `PROVISIONAL · LOAD` or `DO NOT LOAD`. Seal state, newer holds and other devices’ scans are *never* decided offline.
3. Every scan, online or offline, goes into a per-device **hash chain** in IndexedDB. Sequence number, previous hash and hash are assigned in one transaction.
4. **Queue → Sync** sends the unsynced events. The server verifies the chain, rejecting the whole batch if any link is broken. It then re-runs the full gate on each event and returns `ACCEPTED` / `BLOCKED` / `DUPLICATE` / `CONFLICT`. A conflict tells the dispatcher which crate to physically unload.

Limits: the chain shows that events were edited, reordered or have gaps after capture. It does not authenticate the device or prove nothing was deleted before the first sync. Offline loads never count until the server accepts them.

**On a phone:** camera and service worker need a secure context.
```bash
npm run dev:https            # self-signed HTTPS on the LAN, e.g. https://192.168.x.x:5173/scanner
npm run preview:https        # production build with the service worker (installable, works offline)
```
Accept the certificate warning once on the phone. In mock mode the backend runs inside the browser, so use **Setup → Simulate network loss** to go offline.

Demo of a stale-snapshot conflict: download the snapshot → simulate network loss → log a 4M event on Line 2 from the control room (a second tab shares the mock state) → scan PKG-0005 on the scanner (shows `PROVISIONAL`) → reconnect → Sync → `CONFLICT · physically unload PKG-0005`.

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
- `POST /sync/scans`: server verifies the device chain from its last known head; a broken chain rejects the batch (`chain.ok=false, broken_at`). Events the device blocked locally (`local_decision != PROVISIONAL_ACCEPT`) are recorded but never loaded. The snapshot payload follows CONTRACT §8.7.
- `POST /alerts/{id}/ack {reason}`; `POST /shipments/{id}/close` body may carry `unsynced_offline_events`.
- 4M affected units = unshipped units on the line created ≥ `effective_at − lookback` (unit-level, not whole lots).
