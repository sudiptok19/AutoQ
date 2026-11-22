# AutoQ-Trace — API & Domain Contract

**Version 0.1 (draft)** · Changes via `contract/*` PRs only (see PLAN.md §4).

This is the handshake between frontend (`frontend/`) and backend (`backend/`). The backend implements it;
the frontend mocks it until the backend is live. Once the backend serves `/openapi.json`, generated types are
the source of truth for exact field shapes; this document stays the source of truth for **semantics**.

---

## 1. Conventions

- Base path: `/api/v1`. JSON, `snake_case` fields, UTC ISO-8601 timestamps (`2026-11-22T10:15:00Z`).
- IDs: string ULIDs with a type prefix (`unit_01J…`, `pkg_01J…`). Human codes (`L-2611-A`) are separate fields.
- Auth: `Authorization: Bearer <jwt>` on everything except `/auth/login`, `/keys`, `/health`.
- File uploads: `multipart/form-data`, field `image` (JPEG/PNG, ≤ 10 MB).
- Evidence files are content-addressed: `GET /evidence/{sha256}` (auth required). Never overwritten.
- Lists: `?limit=50&cursor=…` → `{ "items": [...], "next_cursor": null }`.
- Writes that can be retried accept `Idempotency-Key` header (or `client_event_id` for scans).
- Every object derived from simulated/fixture data has `"synthetic": true`. The UI badges it.

### Error shape (all non-2xx)
```json
{ "error": { "code": "CONTAINMENT_NOT_SATISFIED", "message": "2 of 5 first-piece inspections done",
             "details": { "unmet": [{ "type": "FULL_INSPECT_FIRST_N", "required": 5, "completed": 2 }] } } }
```
`401` unauthenticated · `403 ROLE_FORBIDDEN` · `404` · `409` state conflict · `422` validation.

---

## 2. Roles & permissions

| Role | Key | Can |
|---|---|---|
| Admin | `admin` | Master data (suppliers, parts, customers, lines), policies, users. **No** quality/dispatch bypass. |
| Operator | `operator` | Create units, capture inspections, pack packages, enroll seals. |
| Quality Engineer | `quality_engineer` | Review HOLDs (reinspect / reject), log 4M events, re-enroll seals after review. |
| Quality Manager | `quality_manager` | Everything QE can + clear containment, execute/release recall, approve 8D. |
| Dispatcher | `dispatcher` | Create shipments, scan/load, seal recheck, close manifest, record movement events. |

Dev users (seeded, password `autoq-dev` for all — **dev only**): `admin`, `op.ravi`, `qe.ananya`, `qm.farhan`, `disp.kiran`.

---

## 3. Domain model & genealogy

```
Supplier 1─* Heat ─*┐
                    ├─* Lot (part, line, heats[]) 1─* Unit (serial) 1─* Inspection
Part ──────────────┘                               │
                                                    ├─0..1 Passport (signed)
Customer 1─* Order (part, qty) 1─* Package 1─* PackageItem ─1 Unit
                         │            └─* SealCheck (ENROLL | RECHECK)
                         └─* Shipment (manifest) 1─* ScanEvent ─0..1 Package
                                        └─* MovementEvent (DEPARTED|CHECKPOINT|DELIVERED)
FourMEvent 1─1 Containment ─* Lot (affected)       Recall ─* (held entities)
AuditEvent (hash-chained, references any entity)
```

**Cardinality decisions**
- **Per-piece model.** Every `Unit` is an individually serialized part (e.g. brake caliper bracket). Inspection, passport and packing are per unit. We do *not* model bulk/sampled items, so we never claim a unit was inspected when it wasn't.
- A `Lot` has exactly one `Part` and one `Line`, and 1..n `Heat`s (genealogy edge `lot_heats`).
- A `Package` belongs to exactly one `Order`; it may only contain units of the order's part. Mixed lots allowed (recorded per item).
- A `Shipment` belongs to one `Order`. A package is loaded at most once (DB unique constraint on accepted loads per package).
- Recall genealogy edges: `heat → lot_heats → lot → unit → package_item → package → scan_event(ACCEPTED) → shipment → movement_events`.

### Core objects (abridged — full fields come from OpenAPI)

```jsonc
// Unit
{ "id": "unit_01J…", "serial": "BRK-2611A-0007", "lot_id": "lot_…", "part_id": "part_…",
  "quality_status": "PASS",          // UNINSPECTED | PASS | HOLD | REJECT
  "passport_id": "pp_…",             // null until issued
  "package_id": "pkg_…",             // null until packed
  "eligibility": { "dispatch_eligible": false,
                   "blockers": [{ "code": "CONTAINMENT_ACTIVE", "message": "4M event FM-0003 open on Line 2", "ref": "cont_…" }] },
  "created_at": "…" }

// Passport
{ "id": "pp_…", "unit_id": "unit_…", "status": "ACTIVE",    // ACTIVE | HELD | REVOKED
  "token": "AQ1.eyJ….Mg…", "kid": "aq-2026-11-a", "payload_version": 1,
  "issued_at": "…", "inspection_id": "insp_…", "inspection_digest": "sha256:…" }
```

---

## 4. State machines (the important part)

### 4.1 Unit quality
```
UNINSPECTED ──inspect──▶ PASS | HOLD | REJECT
HOLD ──QE review: REINSPECT──▶ UNINSPECTED (new inspection required)
HOLD ──QE review: REJECT────▶ REJECT  (terminal)
PASS ──new inspection (containment recheck)──▶ PASS | HOLD | REJECT
```
- A manager/QE **override never sets PASS directly**. It only permits reinspection.
- Verdict rules (deterministic, server-side):
  - quality gate fails (blur/glare/resolution) → `HOLD` with `IMAGE_QUALITY_*` reasons
  - no calibrated golden reference for part/profile → `HOLD` `MODEL_UNCALIBRATED`
  - score ≥ reject threshold → `REJECT`; hold threshold ≤ score < reject → `HOLD`; else `PASS`
  - thresholds tightened while a containment is active on the unit's line (configurable %).

### 4.2 Passport
- **Issued automatically** when an inspection results in `PASS` and the unit has no passport. Signed then; payload immutable.
- `ACTIVE` → `HELD` (containment or recall hold) → `ACTIVE` (hold released) · any → `REVOKED` (terminal; e.g. later REJECT).
- **Resolution of the brief's contradiction:** passports *may* be issued during containment (they prove quality evidence), but the unit is **dispatch-ineligible** until containment clears. `dispatch_eligible` is always computed, never stored, and it is the only thing the dispatch gate trusts besides the signature.

### 4.3 Dispatch eligibility (computed; blockers list is exhaustive)
A unit is `dispatch_eligible` iff: quality_status `PASS` ∧ passport `ACTIVE` ∧ no active containment covering its lot ∧ not in an active recall.

### 4.4 4M containment
```
FourMEvent created ──▶ Containment ACTIVE
   rule (configurable, per policy):
     affected lots  = lots on the line with units created in [effective_at − lookback, now] + all new lots until cleared
     obligations    = FULL_INSPECT_FIRST_N (next N units after the event, fresh inspection)
                      RECHECK_UNIT for each affected, already-PASS, unshipped unit (reinspect) — if policy.recheck_existing
     effects        = affected units → blockers CONTAINMENT_ACTIVE; affected passports → HELD
ACTIVE ──QM clear (all obligations met)──▶ CLEARED   (409 CONTAINMENT_NOT_SATISFIED otherwise)
```
- `planned: true` events: same rules but policy may set `recheck_existing: false`.
- A second event on a line with an active containment **adds** obligations to a new containment; both must clear.
- Default policy (provisional, editable by admin): `first_n=5, lookback_minutes=30, threshold_tightening=0.15, recheck_existing=true for unplanned`.

### 4.5 Package
```
OPEN ──seal enroll (photo)──▶ SEALED ──accepted load scan──▶ LOADED ──shipment DEPARTED──▶ IN_TRANSIT ──DELIVERED──▶ DELIVERED
SEALED ──recheck SUSPICIOUS──▶ SEAL_REVIEW ──QE re-enroll──▶ SEALED      (QE may instead reject → OPEN, items re-inspected)
```
- Hold is an overlay flag (`held: true` + `hold_reasons[]`), not a state, so we keep the physical location truthful.
- A package token (`AQ1.`, type `PKG`) is signed at seal-enroll time and binds package id, order id, item passport ids, seal reference sha256.

### 4.6 Seal check result
`INTACT | SUSPICIOUS | UNVERIFIABLE`. Quality gate failure, too few keypoints, failed homography → `UNVERIFIABLE`, never INTACT/SUSPICIOUS.
Loading requires an `INTACT` recheck newer than `policy.seal_recheck_max_age_minutes` (default 60), or a fresh one attached to the scan.

### 4.7 Shipment / manifest
`OPEN ──close (reconciled)──▶ CLOSED ──DEPARTED──▶ IN_TRANSIT ──DELIVERED──▶ DELIVERED`
Close fails (409) if loaded units ≠ order qty (policy may allow partial), any provisional offline scans unsynced, or any loaded package now held.

---

## 5. Dispatch gate

`POST /shipments/{id}/scans` runs these gates **in order**, inside one transaction with a row lock on the shipment and the package. All gates are evaluated and reported (for UI explainability); any `FAIL` → `BLOCKED`.

| # | Gate | Fail codes |
|---|---|---|
| 1 | `SIGNATURE` | `TOKEN_MALFORMED`, `SIG_INVALID` |
| 2 | `KEY` | `KEY_UNKNOWN`, `KEY_REVOKED`, `KEY_EXPIRED` |
| 3 | `IDENTITY` | `PACKAGE_NOT_FOUND`, `TOKEN_MISMATCH` (token payload ≠ DB record) |
| 4 | `LIFECYCLE` | `PACKAGE_NOT_SEALED`, `PASSPORT_REVOKED`, `PASSPORT_HELD`, `UNIT_NOT_PASS` |
| 5 | `CONTAINMENT` | `CONTAINMENT_ACTIVE` |
| 6 | `QUARANTINE` | `QUARANTINED` |
| 7 | `ORDER` | `WRONG_ORDER`, `WRONG_PART`, `MANIFEST_CLOSED` |
| 8 | `DUPLICATE` | `ALREADY_LOADED` (idempotent replay of same `client_event_id` returns the original decision, `replay: true`) |
| 9 | `SEAL` | `SEAL_NOT_ENROLLED`, `SEAL_RECHECK_REQUIRED`, `SEAL_SUSPICIOUS`, `SEAL_UNVERIFIABLE` |
| 10 | `QUANTITY` | `ORDER_QTY_EXCEEDED` |
| 11 | `SEQUENCE` | `CHECKPOINT_SEQUENCE` (e.g. package not seen at packing station, impossible timing) — warn-level by default |
| — | (role) | `ROLE_FORBIDDEN` → HTTP 403, no gate evaluation |

```jsonc
// POST /shipments/shp_…/scans
{ "token": "AQ1.…", "client_event_id": "dev-7f3a:000042", "device_id": "dev-7f3a", "seal_check_id": null }
// 200
{ "scan_id": "scan_…", "decision": "BLOCKED", "replay": false, "package_id": "pkg_…",
  "reasons": [{ "code": "CONTAINMENT_ACTIVE", "message": "Lot L-2611-A under 4M containment FM-0003", "ref": "cont_…" }],
  "checks": [{ "gate": "SIGNATURE", "status": "PASS" }, { "gate": "KEY", "status": "PASS", "detail": "kid aq-2026-11-a" },
             { "gate": "CONTAINMENT", "status": "FAIL", "detail": "…" }, { "gate": "SEAL", "status": "SKIP" }],
  "shipment_progress": { "loaded_units": 24, "expected_units": 40, "loaded_packages": 3 },
  "decided_at": "…", "decided_by": "disp.kiran" }
```
Both ACCEPTED and BLOCKED scans are persisted. Gates after a hard identity failure (1–3) report `SKIP`.

---

## 6. Tokens & crypto

- Ed25519. Token = `AQ1.` + base64url(canonical JSON payload) + `.` + base64url(signature over the exact payload bytes).
- Canonical JSON: UTF-8, keys sorted lexicographically, no insignificant whitespace, no floats (use strings/ints).
- Payload:
```jsonc
{ "v": 1, "typ": "PP" | "PKG" | "SNAP", "kid": "aq-2026-11-a", "id": "pp_…", "iat": 1795000000,
  // PP:  "unit": "unit_…", "serial": "BRK-…", "lot": "L-2611-A", "part": "PN-44120", "heats": ["H-7781"], "insp": "sha256:…"
  // PKG: "order": "ord_…", "items": ["pp_…", …], "seal": "sha256:…"
}
```
- `GET /keys` → `{ "keys": [{ "kid", "alg": "Ed25519", "public_key": "<base64url 32 bytes>", "status": "ACTIVE|RETIRED|REVOKED", "not_before", "not_after" }] }`
- `POST /verify { "token" }` → `{ "signature_valid", "kid", "typ", "payload", "exists", "status", "eligibility" }` — online verify for the UI.
- Private keys live only on the server (env/file, never in the DB dump or repo). Signature proves data authenticity, **not** that the label is on the original physical object — UI copy must say so.

---

## 7. Endpoints

| Method & path | Role | Purpose |
|---|---|---|
| `POST /auth/login` | — | `{username,password}` → `{access_token, user:{id,username,display_name,roles[]}}` |
| `GET /auth/me` | any | current user |
| `GET /health` | — | `{status, db, key_kid, version}` |
| `GET/POST /suppliers` | admin (write) | suppliers |
| `GET/POST /parts` | admin | `{part_number, name, drawing_rev, inspection_profile_id}` |
| `GET/POST /lines` | admin | production lines/stations |
| `GET/POST /heats` | admin, qe | multipart: `heat_code, supplier_id, material_grade, certificate(file)` → certificate sha256 |
| `GET/POST /lots`, `GET /lots/{id}` | operator+ | lot with heats, counts by quality status, containment ids |
| `POST /lots/{id}/units` | operator | `{count}` → created serial units |
| `GET /units?lot_id=`, `GET /units/{id}` | any | unit with eligibility, inspections, passport, package |
| `POST /inspections` | operator, qe | multipart `unit_id, station_id, image` → Inspection (§8.1); auto-issues passport on PASS |
| `GET /inspections?unit_id=&lot_id=` | any | list |
| `POST /inspections/{id}/review` | qe, qm | `{action: "REINSPECT"\|"REJECT", reason}` |
| `GET /inspection-profiles`, `POST /inspection-profiles/{id}/golden` | qe | upload golden reference image(s); sets `calibrated` |
| `GET /passports/{id}` | any | passport |
| `GET /keys`, `POST /verify` | —, any | crypto (§6) |
| `GET/POST /four-m-events`, `GET /four-m-events/{id}` | qe, qm (write) | `{category: MAN\|MACHINE\|MATERIAL\|METHOD, planned, line_id, description, reason, effective_at}` |
| `GET /containments`, `GET /containments/{id}` | any | obligations with progress, affected lots/units |
| `POST /containments/{id}/clear` | qm | `{reason}`; 409 if obligations unmet |
| `GET/PUT /policies` | any / admin | containment, thresholds, seal age, snapshot TTL |
| `GET/POST /customers` | admin | consignees |
| `GET/POST /orders`, `GET /orders/{id}` | admin, dispatcher | `{order_no, customer_id, part_id, qty_units, destination}` |
| `POST /packages`, `GET /packages/{id}` | operator | `{order_id}` |
| `POST /packages/{id}/items` | operator | `{unit_id}` or `{token}`; 409 with blockers if unit not eligible / wrong part |
| `DELETE /packages/{id}/items/{unit_id}` | operator | only while OPEN |
| `POST /packages/{id}/seal` | operator | multipart image → enroll; returns package + `token` |
| `POST /packages/{id}/seal-checks` | dispatcher, qe | multipart image → SealCheck (§8.2) |
| `POST /packages/{id}/re-enroll` | qe | multipart image + `reason`; from SEAL_REVIEW |
| `GET/POST /shipments`, `GET /shipments/{id}` | dispatcher | `{order_id, vehicle_no, dock}`; detail includes scans and movement events |
| `POST /shipments/{id}/scans` | dispatcher | dispatch gate (§5) |
| `POST /shipments/{id}/close` | dispatcher | 409 with reasons if not reconciled |
| `POST /shipments/{id}/events` | dispatcher | `{type: DEPARTED\|CHECKPOINT\|DELIVERED, location, occurred_at}` |
| `GET /trace?heat=&lot=&unit=&package=&serial=` | any | genealogy graph (§8.3) |
| `POST /recalls/preview` | qe, qm | `{scope:{type: HEAT\|LOT\|UNIT\|PACKAGE, id}}` → blast radius, no writes |
| `POST /recalls`, `GET /recalls/{id}` | qm | `{scope, reason}` → atomic quarantine result (§8.4) |
| `POST /recalls/{id}/release` | qm | `{reason}` lift hold on still-held entities |
| `GET /spc/series?part_id=&line_id=&method_version=` | any | §8.5 |
| `GET /alerts`, `POST /alerts/{id}/ack` | any / qe | SPC & system alerts |
| `POST /reports/8d` | qe, qm | `{source: {type: RECALL\|FOUR_M\|ALERT, id}}` → draft |
| `GET /reports/{id}`, `GET /reports/{id}/pdf`, `POST /reports/{id}/approve` | any / qm | §8.6 |
| `GET /audit?entity_id=`, `GET /audit/verify` | any / qm | hash-chained audit log |
| `GET /offline/snapshot?shipment_id=` | dispatcher | signed snapshot (§8.7) |
| `POST /sync/scans` | dispatcher | queued offline events (§8.7) |
| `GET /evidence/{sha256}` | any | image/certificate bytes |
| `POST /demo/reset` | admin, **dev only** (`AUTOQ_DEMO=1`) | wipe + reseed §9 |
| `GET /dashboard/summary` | any | counts for home screen: units by status, active containments, held packages, open shipments, open alerts, recent blocked scans |

---

## 8. Key payloads

### 8.1 Inspection
```jsonc
{ "id": "insp_…", "unit_id": "unit_…", "station_id": "st_…", "created_by": "op.ravi", "created_at": "…",
  "evidence": { "image_sha256": "…", "image_url": "/api/v1/evidence/…", "heatmap_url": "/api/v1/evidence/…", "aligned_url": "…" },
  "quality_gate": { "passed": true, "blur_laplacian_var": 212.4, "glare_ratio": 0.031, "width": 1280, "height": 960,
                    "thresholds": { "blur_min": 80, "glare_max": 0.12, "min_w": 320, "min_h": 240 }, "failures": [] },
  "analysis": { "method": "golden_diff", "method_version": "gd-0.3", "calibrated": true,
                "score": 0.18, "hold_threshold": 0.45, "reject_threshold": 0.70, "threshold_tightened": false,
                "regions": [{ "x": 410, "y": 220, "w": 60, "h": 40, "score": 0.81 }] },
  "verdict": "PASS", "reasons": [], "passport": { /* Passport or null */ }, "synthetic": false }
```
`score` is a raw method score in [0,1], **not a probability** — the UI labels it "anomaly score".

### 8.2 Seal check
```jsonc
{ "id": "seal_…", "package_id": "pkg_…", "kind": "RECHECK", "result": "SUSPICIOUS", "method_version": "seal-orb-ssim-0.2",
  "metrics": { "keypoints_ref": 812, "keypoints_probe": 760, "good_matches": 143, "inliers": 61, "inlier_ratio": 0.43, "ssim": 0.58,
               "thresholds": { "min_inliers": 40, "intact_ssim": 0.72, "suspicious_ssim": 0.60 } },
  "quality_gate": { "passed": true, "failures": [] },
  "evidence": { "reference_url": "…", "probe_url": "…", "aligned_url": "…", "diff_url": "…" },
  "explanation": "Pattern registered (61 inliers) but structural similarity 0.58 is below 0.60 in the upper-left region.",
  "created_by": "disp.kiran", "created_at": "…" }
```

### 8.3 Trace graph
```jsonc
{ "root": { "type": "HEAT", "id": "heat_…", "label": "H-7781" },
  "nodes": [{ "id": "lot_…", "type": "LOT|HEAT|SUPPLIER|UNIT|INSPECTION|PASSPORT|PACKAGE|ORDER|SHIPMENT|CUSTOMER",
              "label": "L-2611-A", "status": "…", "held": false, "meta": {} }],
  "edges": [{ "from": "heat_…", "to": "lot_…", "type": "MADE_FROM|CONTAINS|INSPECTED_BY|CERTIFIED_BY|PACKED_IN|FOR_ORDER|LOADED_ON|DELIVERED_TO" }],
  "summary": { "units": 40, "packages": 5, "at_plant": 3, "loaded": 0, "in_transit": 1, "delivered": 1 } }
```
Location buckets come only from scan + movement events: `at_plant` (no accepted load), `loaded` (accepted load, shipment not DEPARTED), `in_transit` (DEPARTED, no DELIVERED), `delivered`.

### 8.4 Recall result
```jsonc
{ "id": "rcl_…", "scope": { "type": "HEAT", "id": "heat_…", "label": "H-7781" }, "reason": "…", "executed_by": "qm.farhan",
  "executed_at": "…", "query_ms": 41,
  "affected": { "units": 40, "passports_held": 36, "packages_held": 3,
                "blocked_at_plant": [{ "package_id": "…", "label": "PKG-0003", "units": 8 }],
                "loaded_not_departed": [], 
                "in_transit": [{ "shipment_id": "…", "vehicle_no": "WB-02-AK-4471", "customer": "…", "units": 8, "action": "NOTIFY_AND_INTERCEPT" }],
                "delivered":  [{ "shipment_id": "…", "customer": "…", "units": 8, "action": "NOTIFY_CUSTOMER" }] },
  "evidence": { "genealogy_query": "recursive CTE heat→lot→unit→package→scan→shipment", "as_of": "…" },
  "audit_event_ids": ["aud_…"] }
```
Recall takes a lock on affected packages so a concurrent load scan either completes before (and is reported as loaded) or is blocked after.

### 8.5 SPC series
```jsonc
{ "part_id": "…", "line_id": "…", "method_version": "gd-0.3",
  "baseline": { "n": 30, "mean": 0.17, "std": 0.04, "frozen_at": "…", "status": "READY|COLLECTING|LOW_VARIANCE" },
  "rule": { "type": "CUSUM_UPPER", "k_sigma": 0.5, "h_sigma": 5 },
  "points": [{ "inspection_id": "…", "t": "…", "score": 0.19, "z": 0.5, "cusum": 0.0, "synthetic": true }],
  "alerts": [{ "id": "alert_…", "at_index": 47, "message": "Suspected upward drift (CUSUM 5.2σ > 5σ)" }] }
```
Baseline is per `method_version`; changing method version resets to `COLLECTING`. Alerts say *suspected drift*, never "predicted defect".

### 8.6 8D draft
```jsonc
{ "id": "rpt_…", "status": "DRAFT|APPROVED", "source": { "type": "RECALL", "id": "rcl_…" }, "generator": "template-0.1",
  "sections": [{ "key": "D2", "title": "Problem description",
                 "items": [{ "kind": "FACT|HYPOTHESIS|PROPOSED|PENDING_HUMAN", "text": "…", "evidence": [{ "type": "INSPECTION", "id": "insp_…", "label": "…" }] }] }],
  "approved_by": null, "approved_at": null }
```

### 8.7 Offline
```jsonc
// GET /offline/snapshot?shipment_id=…  → { "token": "AQ1.<SNAP payload>.<sig>" } where payload:
{ "v": 1, "typ": "SNAP", "kid": "…", "id": "snap_…", "iat": …, "exp": …,             // exp = iat + policy.snapshot_ttl (default 4h)
  "shipment": "shp_…", "order": "ord_…", "part": "PN-44120", "expected_units": 40,
  "allowed_packages": ["pkg_…"], "held": ["pkg_…", "pp_…"], "revoked_kids": [], "keys": [{ "kid", "public_key" }] }

// POST /sync/scans
{ "device_id": "dev-7f3a", "snapshot_id": "snap_…",
  "events": [{ "client_event_id": "dev-7f3a:000043", "seq": 43, "prev_hash": "…", "hash": "sha256(prev_hash|canonical(event))",
               "token": "AQ1.…", "shipment_id": "shp_…", "scanned_at": "…", "local_decision": "PROVISIONAL_ACCEPT|LOCAL_BLOCK", "local_reasons": [] }] }
// → { "results": [{ "client_event_id": "…", "outcome": "ACCEPTED|BLOCKED|DUPLICATE|CONFLICT", "scan": { /* §5 */ }, "note": "…" }],
//     "chain": { "ok": true, "server_head": "…" } }
```
- Offline scans are always `PROVISIONAL_OFFLINE` in the UI. They never count as loaded until the server accepts them on sync.
- Manifest close is blocked while the device reports unsynced events, and the server rejects close if a snapshot for the shipment was issued and the shipment has unsynced devices (best effort).
- `CONFLICT` = server re-runs the gate at sync time and the result differs from the local decision (e.g. package held after snapshot, or loaded by another device). The UI shows the conflict and the physical action needed ("unload PKG-0003").
- The hash chain detects reordering/gaps in synced events; it does **not** prove nothing was deleted before sync. Documented as a limit.

---

## 9. Demo seed scenario (`POST /demo/reset`)

Agreed fixed data so frontend mocks and backend seed look identical:

- Supplier `Shree Forgings Pvt Ltd (demo)`; heats `H-7781` (cert PDF), `H-7790`.
- Part `PN-44120 Brake caliper bracket`, profile `prof_bracket` calibrated with a golden image.
- Lines `Line 1`, `Line 2`. Lots `L-2611-A` (Line 2, H-7781, 40 units), `L-2611-B` (Line 1, H-7790, 20 units).
- Customer `Demo Auto Assembly Plant – Gurugram` (fictional) and `Demo Service Depot – Kolkata`.
- Orders `SO-1001` (40 units, Gurugram), `SO-1002` (16 units, Kolkata).
- Shipments: one `DELIVERED` (8 units from L-2611-A), one `IN_TRANSIT` (8 units from L-2611-A), one `OPEN` at Dock 3 for SO-1001.
- Remaining L-2611-A units: inspected PASS, packed in sealed packages at plant.
- SPC: 60 points for `gd-0.3`, first 30 baseline, then a gradual upward drift (labeled `synthetic: true`).
- No active containments at reset — the demo creates the 4M event live.

Names are fictional and must not imply any OEM endorsement.
