# Contract alignment for joint approval

> **Decision recorded 2026-11-23 — Sudipta (Opus side): Approve Option A on #1–#5.** Ratified into [CONTRACT.md §10](CONTRACT.md#10-v02-binding-deltas-option-a). Work split in [WORK_SPLIT.md](WORK_SPLIT.md) accepted.

**Review baseline:** `main = d19c429`, `origin/fe/foundation = c120452`. Main defines wire contract v0.1; foundation implements browser mocks and a scanner. Neither contains a live Python backend. This document proposes five decisions. The existing contract stays in force until the reviewers record their choices.

**Reply:** `Approve Option A on #1–#5.` For exceptions, reply with each number and choice. Decisions 1–3 share the passport version: choosing sampled release in #2 requires the signed evidence fields in #3; RED release must always follow the rule selected in #1. Resolve an incompatible combination before implementing it.

| # | Option A (Adopt v2 — Recommended) | Option B (Keep Current v1) |
|---|---|---|
| 1 | Persist evidence during RED; issue only after distinct-unit recovery and manager clearance. | Issue held passports on PASS during containment; clear v1 obligations. |
| 2 | Freeze lot/sample membership; distinguish direct and sampled inspection; supersede stale credentials. | Use per-piece evidence, one inspection and one passport identity. |
| 3 | Keep AQ1 encoding; version the signed PP, PKG and SNAP payloads and harden offline replay. | Keep v1 signed claims and the current snapshot/sync contract. |
| 4 | Revalidate departure in the final transaction; count affected units and create durable physical tasks. | Keep close followed by movement and v1 recall action lists. |
| 5 | Integrate real calibrated evidence, live alerts, report drafts and trusted LAN TLS with the existing UI. | Implement the v1 REST evidence/report contracts and retain current alert delivery and dev gateway. |

**How to read the diffs.** Objects inside tables show the fields that differ; the signed payload examples in #3 show complete objects. Short IDs, digests and keys are illustrative. The **file impact** under each decision applies to its rows. Named file sets at the end expand to exact repository-relative paths. Option B describes retaining published semantics; missing backend implementations are identified separately from edits to existing files. Neither choice transfers UI, models, migrations or transaction ownership to Astra.

<a id="decision-1"></a>
## 1. RED containment and release timing

| Conflict | Current repo (main / foundation) — B | Proposed v2 master plan — A |
|---|---|---|
| First PASS during RED | Main §4.2 signs on PASS. Mock returns `{"verdict":"PASS","passport":{"status":"HELD","inspection_id":"i7"}}` for a new affected unit. | Save `{"verdict":"PASS","passport":null}`. Block explicit issuance or activation with HTTP 409 and `CONTAINMENT_ACTIVE`. An existing signed passport remains readable and dispatch-ineligible. |
| Counter, scope, overlap | `{"status":"ACTIVE","obligations":[{"type":"FULL_INSPECT_FIRST_N","required":5,"completed":2}]}`. Mock takes distinct post-event PASS IDs on a line, excludes existing recheck IDs and has no epoch, ordered run or REJECT reset. Main describes lot/line lookback; the mock selects recent unshipped units and also tracks line lot IDs. | Create a separate run per event with explicit affected unit IDs, production sequence and policy version. Example `{"run_id":"r1","sequence_epoch":2,"containment_required_qty":5,"containment_cleared_qty":0,"status":"ACTIVE"}` after REJECT. A distinct ordered individual PASS counts once per epoch; HOLD earns zero, REJECT resets, overlapping runs accrue separately. Record new in-scope production explicitly while active. |
| Clearance | Manager already supplies a reason and must satisfy obligations. Main/mock have ACTIVE→CLEARED. Mock recomputes passport holds on clearance. | ACTIVE→READY_FOR_CLEARANCE at threshold; RED issuance block remains until a `quality_manager` (business role QC_MANAGER) clears with reason. Independent recall/overlap holds remain effective. Only units with their own qualifying evidence can then activate with `four_m_tagged:true`. |
| Thresholds | Mock applies a configured reduction to hold/reject thresholds when contained. | Version the applied thresholds; stricter sampling/individual requirements are mandatory. Numeric tightening is optional and valid only with a calibrated profile. |

**File impact.** A — Opus: O-release, O-database, O-ui-quality and O-contract; Astra: no edits for the counter/authorization service. B — Opus: O-release and O-database when implementing v1; no foundation or binding-doc change. Astra: no files for this decision. [Main decisions 4–6](https://github.com/sudiptok19/AutoQ/blob/d19c429/docs/DECISIONS.md) · [Mock progress, inspection and clearance](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/mock/router.ts#L49)

<a id="decision-2"></a>
## 2. Genealogy, sampling and passport meaning

| Conflict | Current repo — B | Proposed v2 master plan — A |
|---|---|---|
| Material and frozen membership | `{"heat_ids":["h1","h2"],"planned_qty":40}`. Per-piece inspections only. No `is_a_part`, frozen sample, eligible-sample decision or sample-selection API. | Preserve historical multiple-heat genealogy. New lots take exactly one heat in the existing array. Add `{"is_a_part":false}` on Part. Freeze membership, policy version and selected serial IDs before inspection: `POST /lots/{id}/freeze-and-sample` → `{"lot_id":"l1","frozen_at":"2026-11-22T10:00:00Z","sample_unit_ids":["u1","u3"],"policy_version":"p2"}`. Reject additions after freeze. Failed/incomplete samples block release. |
| What a passport certifies | REST `{"unit_id":"u2","inspection_id":"i2","inspection_digest":"sha256:…","status":"ACTIVE"}`. TypeScript requires a single inspection. | REST adds `{"inspection_basis":"SAMPLED_LOT","authorizing_inspection_ids":["i1","i3"],"evidence_digests":["sha256:…","sha256:…"],"four_m_tagged":false}`. Only GREEN non-A lots with all selected units passed qualify. An A-part or RED-affected unit must carry `INDIVIDUAL_100PCT` and exact inspections of that unit. No invented direct inspection or direct PASS on an unsampled serial. |
| Live eligibility / superseding | One passport pointer; statuses ACTIVE, HELD, REVOKED. Gate expects unit quality PASS. After clearing a hold a credential can return ACTIVE. | A sampled unit can be eligible by frozen sample evidence even if its own quality status is UNINSPECTED. Recompute eligibility from its basis, live policy, individual failure history and all holds. Retain immutable prior signed bytes; set prior lifecycle `{"status":"SUPERSEDED","superseded_by_passport_id":"pp2"}`. A replacement individually certified after 4M carries the tag. Unique current passport, retriable issue. |
| Demo quantities | Contract seed lot A specifies 40 units. Foundation seeds 48 (40 packed plus 8 available). | Freeze 48 produced, distinguish 40 ordered, and label demo policy. Seed by explicit development command; keep an admin-only demo-reset facade in demo mode for the current UI. Mock also permits a quality manager on reset. |

**File impact.** A — Opus: O-catalog, O-release, O-database, O-ui-quality, O-demo and O-contract; Astra: A-crypto (claims/fixtures under #3). B — Opus: O-catalog, O-release, O-database and O-demo for v1, align the mock seed/reset in `frontend/src/mock/state.ts` and `frontend/src/mock/router.ts` to written v1; Astra: A-crypto implements v1 direct-inspection vectors. [v0.1 model, lifecycle and seed](https://github.com/sudiptok19/AutoQ/blob/d19c429/docs/CONTRACT.md) · [Browser passport types](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/api/types.ts)

<a id="decision-3"></a>
## 3. Exact signed bytes, trust and offline replay

Both choices use the browser’s AQ1 envelope: `AQ1.<base64url(payload_bytes)>.<base64url(Ed25519_signature_over_payload_bytes)>`. Encode canonical JSON in UTF-8, keys recursively sorted, no extra whitespace or padding, preserve array order, no floats. This differs from compact JWS, which signs a header/payload signing input. Use reviewed Ed25519 primitives with cross-language vectors. Under A restrict claim keys to the documented ASCII names and integers to the JS-safe range.

**Current signed objects issued by the mock** (example values). All three receive `v,kid,iat` from its common signer. PP signs an inspection **digest**, not its ID list. The digest is SHA-256 over canonical `{id,verdict,image,method}`. PKG signs sorted item passport IDs.

```json
[
  {"v":1,"typ":"PP","kid":"k1","id":"pp1","iat":1795300000,"unit":"u1","serial":"S1","lot":"L1","part":"PN1","heats":["H1"],"insp":"sha256:…"},
  {"v":1,"typ":"PKG","kid":"k1","id":"pkg1","iat":1795300000,"label":"PKG-1","order":"o1","items":["pp1"],"seal":"abcdef…"},
  {"v":1,"typ":"SNAP","kid":"k1","id":"sn1","iat":1795300000,"exp":1795314400,"shipment":"s1","order":"o1","expected_units":8,"allowed_packages":["pkg1"],"held":[],"revoked_kids":[],"keys":[{"kid":"k1","public_key":"BASE64URL"}]}
]
```

**Option A signed objects.** A schema version bump inside the same AQ1 envelope lets the scanner reject claims it cannot interpret. `lot`, `part`, `heats` are human codes as in v1. `unit`, `order`, and the authorization IDs are internal identities. Digests in PP use the `sha256:` prefix. PKG `seal` is the raw reference hex digest. Canonically order authorizing IDs and pair each by index with the digest of its immutable inspection evidence record. Keep evidence-source hashes inside those records.

```json
[
  {"v":2,"typ":"PP","kid":"k1","id":"pp2","iat":1795300000,"unit":"u1","serial":"S1","lot":"L1","part":"PN1","heats":["H1"],"inspection_basis":"INDIVIDUAL_100PCT","authorizing_inspection_ids":["i7"],"evidence_digests":["sha256:…"],"four_m_tagged":true},
  {"v":2,"typ":"PKG","kid":"k1","id":"pkg1","iat":1795300000,"label":"PKG-1","order":"o1","items":["pp2"],"seal":"abcdef…","seal_revision_id":"sr2"},
  {"v":2,"typ":"SNAP","kid":"k1","id":"sn2","iat":1795300000,"exp":1795314400,"shipment":"s1","order":"o1","part":"PN1","device_id":"d1","operator_id":"op1","policy_version":"p2","expected_units":6,"allowed_packages":["pkg1"],"held":[],"revoked_kids":[],"keys":[{"kid":"k1","public_key":"BASE64URL","status":"ACTIVE","not_before":1795000000,"not_after":1796000000}]}
]
```

| Conflict | Current repo — B | Proposed v2 master plan — A |
|---|---|---|
| Decoder vs validation | The TS verifier checks canonical bytes, string kid, known/non-revoked key and Ed25519. Its payload type permits arbitrary claims. The scanner separately checks SNAP or PKG. It does not enforce supported version, complete shape or key validity interval. Offline maps non-revoked snapshot keys to ACTIVE. | Validate type/version/shape, original bytes and key policy. Trust snapshot signer through independently provisioned trusted keys over authenticated TLS. Recheck SNAP expiry, operator/device/order and policy scope. Retired keys may verify historical records issued in their validity window; revoked keys block live use. Legacy credentials may be inspected historically; refresh snapshots and reissue/reseal before v2 dispatch. |
| Snapshot wire and capacity | Written §8.7 includes `part`; mock omits it. Mock `remainingQty` subtracts loads on **other** shipments. It does not subtract current-shipment loads. Scanner totals local accepted/provisional rows, including earlier rows. Two devices may disagree. | Snapshot `expected_units` means remaining order capacity **after all committed loads** at issuance for this shipment’s order. Only count new eligible events against this snapshot. Finish syncing pending events before refreshing it. Expired snapshot gives `LOCAL_BLOCK/SNAPSHOT_STALE`; verification cannot authorize a departure. |
| Events and chain | `POST /sync/scans` body `{"device_id":"d1","snapshot_id":"sn1","events":[{"client_event_id":"d1:1","seq":1,"prev_hash":"…","hash":"…","token":"AQ1.…","shipment_id":"s1","scanned_at":"…","local_decision":"PROVISIONAL_ACCEPT","local_reasons":[]}]}`. SHA-256 of prev hex + literal pipe + canonical projection `{client_event_id,seq,token,shipment_id,scanned_at,local_decision}`. Genesis is 64 zeroes. Verifier sorts and requires increasing seq, not contiguous seq. Mock treats known event IDs as duplicates; TS also queues ONLINE events. | Retain projection and outcomes for interoperability. Server requires registered device/operator, contiguous next seq and independently verified snapshot/session scope. Bind the stored event to its original snapshot at acceptance; reject changed-content/rebound retries. Checkpoint and results commit together. Drain old session/device queue before replacing snapshot; replay ONLINE attempts from the original online decision or re-run the live gate idempotently if the response was lost. LOCAL_BLOCK stays blocked. A broken batch returns `{"results":[],"chain":{"ok":false,"broken_at":3,"server_head":"…"}}` without advancing state. |
| Offline conflict | Current sync returns ACCEPTED/BLOCKED/DUPLICATE/CONFLICT and displays a physical unload note. Pasted plan uses STAGED_PENDING_SYNC / STAGED_REJECTED. | Retain existing wire enums; PROVISIONAL_ACCEPT is staged pending sync. On conflict return the server decision plus a durable segregation task reference; replay does not create another task. Record operator/server decision time separately from capture time. No cross-device safety or deletion-proof claim offline. |

**File impact.** A — Opus: O-scanner, O-pack, O-release, O-database and O-contract; Astra: A-crypto for real keys, canonical bytes and interoperability vectors. B — Opus: O-scanner for implementation of the existing REST/sync paths and correcting written-v1 missing SNAP `part` in `frontend/src/mock/router.ts`; Astra: A-crypto for existing AQ1 v1. No JWS migration under either option. [Signer and payloads](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/mock/state.ts#L82) · [Token verifier](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/lib/token.ts) · [Offline gate](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/scanner/offline.ts) · [Device chain](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/lib/chain.ts)

<a id="decision-4"></a>
## 4. Dispatch, departure and mixed-lot recall

| Conflict | Current repo — B | Proposed v2 master plan — A |
|---|---|---|
| Load vs departure | v1 already separates accepted scan, close, DEPARTED and DELIVERED. Mock `POST /shipments/{id}/close` checks quantity, package hold flag and optional client unsynced count. On `{"type":"DEPARTED"}` it checks only CLOSED, then advances physical state. Mock stamps server time in place of submitted occurrence time. | Accepted scan records a distinct LOAD_ACCEPTED fact. Keep `/close` as preparation; `/events` with DEPARTED atomically rechecks live units, signatures, revisions, holds, order totals and unresolved devices, finalizes manifest digest and records movement. Persist validated occurrence time and server recording time. DELIVERED requires an actual prior departure. |
| Simultaneous decisions | Written v1 specifies shipment/package locks and uniqueness; mock mutates localStorage. Overlap with heat quarantine, packing changes and order capacity is not serialized across devices. | Within each affected class lock IDs ascending: heat → lot → unit → package → order → shipment. Re-read membership under the locks and retry on a changed set. Use unique active load per package, one result per event ID, and reject reuse with different content. Dispatch gate reasons remain visible for accepted and blocked scans. |
| What an impact count means | `affected.units` counts affected serials; package groups carry affected units. Mock increments `packages_held` by **all loaded packages** on an open/closed shipment containing any affected package. Unpacked stock lacks a separate list in the recall result. Trace vs recall counters can have different units. | Add `{"unit_counts":{"at_plant":2,"loaded_not_departed":1,"in_transit":1,"delivered":0},"held_package_ids":["pkgA","pkgB"]}`. Count distinct affected units in exactly one physical bucket; include unpackaged stock. Hold a whole mixed package once. Unrelated units retain clean genealogy; unrelated packages on the same vehicle stay outside the count. |
| Physical action and history | Impact lists contain action strings UNLOAD_AND_HOLD, NOTIFY_AND_INTERCEPT and NOTIFY_CUSTOMER. No durable task ID. Current package pointers alone cannot reconstruct repack/unload history. | Record effective membership intervals and movements. If quarantine wins, create `{"type":"REMOVAL_REQUIRED","package_id":"pkgB","status":"OPEN"}` and block departure until removal/reconciliation. If departure wins create IN_TRANSIT_RECALL; delivered → NOTIFY_CUSTOMER. A rejected offline stage creates SEGREGATION_REQUIRED. Persist and audit completion; repeated recall/replay must not duplicate open tasks. |
| Shipment vs order API | Pasted master groups load/depart/deliver under orders and binds one order line. Repo uses single-part orders plus shipments; seal checks and counts attach to packages. | Preserve existing shipment routes and single-part order as the prototype’s single line. Mixed lots of that part are permitted. Scanners continue to receive `{"decision":"BLOCKED","checks":[],"reasons":[],"replay":false}` with the actual gate details populated. |

**File impact.** A — Opus: O-logistics, O-pack, O-scanner, O-database and O-contract; Astra: A-crypto and A-seal supply verified inputs, no dispatch or recall file edits. B — Opus: O-logistics, O-pack and O-database for live v1, correct the mock overcount in `frontend/src/mock/router.ts`; Astra: no files for dispatch transactions. [Dispatch, movements and recall calculation](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/mock/router.ts#L391)

<a id="decision-5"></a>
## 5. Seal response, alerts, inspection evidence and integration

| Conflict | Current repo — B | Proposed v2 master plan — A |
|---|---|---|
| Seal classification and shape | Main §8.2 includes metrics, quality gate and image URLs. TS SealCheck omits quality_gate/evidence, allows numeric metrics only; mock returns `{"result":"UNVERIFIABLE","metrics":{"inliers":0,"ssim":0}}` for unsupported real captures. Master draft calls a low match TAMPERED and ambiguously treats alignment failure as tamper. | Keep multipart `image` and v1 enums. Add mandatory revision, source, quality/evidence fields. Example delta: `{"revision_id":"sr2","result":"UNVERIFIABLE","metrics":{"inliers":4,"ssim":null},"quality_gate":{"passed":false,"failures":["REGISTRATION_FAILED"]},"evidence":{"reference_url":"/api/v1/evidence/ref","probe_url":"/api/v1/evidence/probe","aligned_url":null,"diff_url":null},"evidence_source":"LIVE_CAPTURE"}`. Bad/ambiguous registration blocks loading. Only a registered discrepancy is SUSPICIOUS; invalidate check and package token on unpack/reseal. |
| Alerts and siren | Main lists REST GET alerts / POST ack. Mock derives unacknowledged SPC warnings only. No WS schema, broadcaster, durable alert outbox, dedicated Alert Centre or cross-device siren. A siren icon in Trace is a visual control. | Opus persists alerts and outbox, authenticates socket and catches up missed events by REST. Astra provides a standalone audience-scoped bus and event schema. On confirmed commit send the envelope below. Acknowledge needs a reason; alert resolution, severity, audible flag and task completion are durable. Opus controls all sound interactions. |
| Component evidence | Real pixel-quality metrics exist; the browser golden diff is calibrated for synthetic bracket images only. Mock hashes image data URLs and uses a fixture indicator. Pasted master offers raw_score/defect_quadrant and an extra decision UNVERIFIABLE. | Preserve §8.1 image/analysis fields and policy PASS/HOLD/REJECT. Add `{"evidence_source":"LIVE_CAPTURE","analysis":{"calibrated":false,"score":null},"verdict":"HOLD"}` for uncalibrated input. Hash original bytes. Emit aligned/difference artifacts only for valid registration. Held-out independent phone sessions measure false accepts, false rejects and unverifiable counts. |
| Baseline and warning | Mock hardcodes part, filters by line/method, takes first 30, CUSUM only and emits numerical zeros while collecting/low variance. Main already distinguishes COLLECTING/LOW_VARIANCE. | Separate frozen part/station/method baselines and versioned configurable rules. Use real nullable normalized outputs: `{"baseline":{"status":"LOW_VARIANCE"},"points":[{"z":null,"cusum":null}]}`. Optional normalized calculations require usable variance. Add explicit FIVE_RISING warning on five strictly increasing valid scores once ready. One persisted warning per inspection/baseline even when both rules fire. |
| 8D/SCAR | v1 specifies draft JSON, PDF and manager approval. Foundation contains types but no report handler, renderer or viewer. The master uses different route/source naming and tracks pending steps explicitly. | Keep `POST /reports/8d {"source":{"type":"RECALL","id":"r1"}}` and `GET /reports/{id}/pdf`. Freeze evidence, counts and as-of time: `{"status":"DRAFT","sections":[{"key":"D4","items":[{"kind":"PENDING_HUMAN","text":"Root cause awaiting review","evidence":[]}]}]}`. Mark D6/D8 pending too unless evidence exists. Opus owns workflow/approval, Astra deterministic PDF. |
| REST dialect, auth and downloads | Repo flat endpoints, lower-case roles, `{"error":{"code":"…","message":"…","details":{}}}`. Pasted master proposes grouped paths, different role model and a top-level error_code/component/remediation. Foundation appends bearer to evidence URLs; draft suggests JWT in WS URL. | Keep existing path/role/envelope. Put `component,remediation,reason_codes,request_id` inside error.details when available. Use an authorized blob fetch for evidence. Exchange a bearer for a short-lived single-use WS ticket; pass only that ticket on the same-origin socket. Keep role-gated quality decisions and explicit storage/DB failures. |
| Platform, integration contract and seed | Main Compose provides PostgreSQL 16 only, Python 3.12/uv is planned; pasted master allows 3.11+ and bundles Caddy. Foundation Vite can serve self-signed HTTPS; no trusted phone validation, exported schemas or live backend. Branch README describes type generation absent from package scripts. | Keep repo Python 3.12 and frontend stack. Astra’s separate Compose override proxies HTTPS/API/WS to Opus’s services. Opus alone updates root Compose/manifests and exports OpenAPI; Astra owns canonical WS JSON Schema. Generate TS from live OpenAPI after agreement; run real phone and database tests. |
| Frontend, working agreement | Repo agent files give all backend to Codex, all frontend to Claude, and use be/* and fe/*. Pasted brief also specifies light-only cobalt styling. Foundation already has graphite/teal, light/dark, IBM Plex and a scanner PWA. | Adopt the revised seven-deliverable Astra split; Opus owns DB/integration and every UI/UX decision. Keep foundation’s design as the working starting point. No CSS, typography, theme or frontend rewrite is a contract requirement. Both options preserve Opus’s UI authority. |

**Option A WebSocket contract.** Proposed `WSS /ws/events?ticket=<single-use>`, with a ticket from `POST /api/v1/ws/tickets`. Required fields are shown below. Severity is INFO, MEDIUM, HIGH or CRITICAL. Allowed types are ALERT_CREATED, ALERT_RESOLVED, PASSPORT_REVOKED, LOT_4M_CHANGED, DISPATCH_STATE_CHANGED, SEGREGATION_TASK_CREATED, SPC_DRIFT_WARNING. The payload is an object with type-specific references, transport audience is supplied by the authorized server route, and event IDs survive retries.

```json
{"event_id":"evt1","type":"ALERT_CREATED","severity":"CRITICAL","audible_siren":true,"entity_type":"PACKAGE","entity_id":"pkg1","reason_codes":["SEAL_SUSPICIOUS"],"timestamp":"2026-11-22T12:00:00Z","payload":{"alert_id":"al1","seal_check_id":"sc1","revision_id":"sr2"}}
```

**File impact.** A — Opus: O-alerts, O-platform, O-evidence, O-reports, O-ui-quality, O-demo, O-database and O-contract; Astra: A-seal, A-component, A-spc, A-pdf, A-gateway and A-bus. B — Opus: O-evidence, O-reports, O-database and existing REST alert files in O-alerts for v1; Astra: A-component, A-seal, A-spc and A-pdf implement written v1. The new gateway and broadcaster remain Astra-reserved, deferred if the team chooses v1 delivery. Neither option assigns any frontend file to Astra. [Mock seal response](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/mock/router.ts#L271) · [Mock SPC/alerts](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/mock/router.ts#L529) · [Foundation UI tokens](https://github.com/sudiptok19/AutoQ/blob/c120452/frontend/src/index.css)

### Route crosswalk for the pasted master

All HTTP examples use `/api/v1`. This reconciles naming with the existing app whichever decision the team approves. An A-only capability is mounted only if adopted.

| Pasted proposal | Repository route retained, or proposed addition |
|---|---|
| /catalog parts, stations, calibration profiles | /parts, /lines, /inspection-profiles and /inspection-profiles/{id}/golden |
| /lots/heats, /lots/lots, lot-scoped 4m-events and clearance | /heats, /lots, /lots/{id}/units, /four-m-events, /containments/{id}/clear; A adds /lots/{id}/freeze-and-sample |
| /inspections/run, /inspections/spc, HOLD override | POST /inspections, GET /spc/series, POST /inspections/{id}/review with REINSPECT or REJECT |
| /passports/verify and /passports/offline-snapshot | POST /verify, GET /keys, GET /offline/snapshot?shipment_id=; A adds explicit idempotent POST /passports/issue |
| /packages/{id}/seal-enroll and /seal-verify | /packages/{id}/seal, /seal-checks, /re-enroll; A adds /packages/{id}/unpack |
| /dispatch/orders/{id}/load, /depart, /deliver and /dispatch/sync-offline | /orders, /shipments/{id}/scans, /shipments/{id}/close, /shipments/{id}/events and /sync/scans |
| /recall/lineage, /recall/quarantine, /alerts/scar | /trace, /recalls/preview, /recalls, /reports/8d, /reports/{id}/pdf; A adds /segregation-tasks and /alerts/{id}/resolve |
| Master-only GET /auth/users, security-admin recall, uppercase roles | Retain /auth/login and /auth/me. No new user-list requirement. Persist admin/operator/quality_engineer/quality_manager/dispatcher. Manager owns recall execution and clearance. |

### File impact key

Each named set below expands to the literal paths for that feature decision. Paths include future implementation files. Under B an existing file with conforming behavior needs no edit; shared contract edits under A follow the jointly reviewed contract PR. The authoritative single-writer inventory and per-feature test criteria are in [WORK_SPLIT.md](WORK_SPLIT.md).

- **A-crypto** — `backend/app/features/passport_crypto/router.py`, `backend/app/features/passport_crypto/service.py`, `backend/tests/test_passport_crypto.py`, `backend/tests/vectors/aq1_interop.json`.

- **A-seal** — `backend/app/features/seal_vision/router.py`, `backend/app/features/seal_vision/service.py`, `backend/tests/test_seal_vision.py`, `fixtures/images/seal/manifest.json`, `fixtures/images/seal/enroll.jpg`, `fixtures/images/seal/intact_recapture.jpg`, `fixtures/images/seal/torn.jpg`, `fixtures/images/seal/repositioned.jpg`, `fixtures/images/seal/bad_light.jpg`.

- **A-component** — `backend/app/features/component_vision/router.py`, `backend/app/features/component_vision/service.py`, `backend/tests/test_component_vision.py`, `fixtures/images/inspection/manifest.json`, `fixtures/images/inspection/golden_01.jpg`, `fixtures/images/inspection/good_01.jpg`, `fixtures/images/inspection/defect_01.jpg`, `fixtures/images/inspection/blurry_01.jpg`, `fixtures/images/inspection/glare_01.jpg`.

- **A-spc** — `backend/app/features/spc/router.py`, `backend/app/features/spc/service.py`, `backend/tests/test_spc.py`.

- **A-pdf** — `backend/app/features/scar_pdf/router.py`, `backend/app/features/scar_pdf/service.py`, `backend/tests/test_scar_pdf.py`.

- **A-gateway** — `docker-compose.https.yml`, `Caddyfile`, `certs/README.md`.

- **A-bus** — `backend/app/core/ws_bus.py`, `contracts/ws_events.schema.json`, `backend/tests/test_ws_bus.py`.

- **O-contract** — `docs/CONTRACT.md`, `docs/DECISIONS.md`, `docs/PLAN.md`, `AGENTS.md`, `CLAUDE.md`.

- **O-release** — `backend/app/features/inspection_passports/router.py`, `backend/app/features/inspection_passports/service.py`, `backend/tests/test_inspection_passports.py`, `backend/app/policy/eligibility.py`, `backend/app/features/containment/router.py`, `backend/app/features/containment/service.py`, `backend/tests/test_containment.py`.

- **O-database** — `backend/app/db/models.py`, `backend/app/db/session.py`, `backend/app/db/locking.py`, `backend/alembic/versions/0001_initial.py`.

- **O-ui-quality** — `frontend/src/api/types.ts`, `frontend/src/pages/Lots.tsx`, `frontend/src/pages/UnitPage.tsx`, `frontend/src/pages/Inspect.tsx`, `frontend/src/pages/Containment.tsx`, `frontend/src/pages/Spc.tsx`, `frontend/src/mock/router.ts`, `frontend/src/mock/state.ts`.

- **O-catalog** — `backend/app/features/catalog_sampling/router.py`, `backend/app/features/catalog_sampling/service.py`, `backend/tests/test_catalog_sampling.py`.

- **O-demo** — `backend/app/features/console_demo/router.py`, `backend/app/features/console_demo/service.py`, `backend/tests/test_console_demo.py`, `backend/scripts/seed_demo.py`, `frontend/src/api/generated.ts`, `e2e/playwright.config.ts`, `e2e/quality-to-recall.spec.ts`, `frontend/package.json`, `frontend/package-lock.json`.

- **O-scanner** — `backend/app/features/offline_scanner/router.py`, `backend/app/features/offline_scanner/service.py`, `backend/tests/test_offline_scanner.py`, `frontend/src/pages/Scanner.tsx`, `frontend/src/scanner/offline.ts`, `frontend/src/scanner/store.ts`, `frontend/src/scanner/offline.test.ts`, `frontend/src/lib/token.ts`, `frontend/src/lib/token.test.ts`, `frontend/src/lib/chain.ts`, `frontend/src/api/types.ts`, `frontend/src/mock/router.ts`, `frontend/src/mock/state.ts`.

- **O-pack** — `backend/app/features/packaging/router.py`, `backend/app/features/packaging/service.py`, `backend/tests/test_packaging.py`, `frontend/src/pages/Packing.tsx`.

- **O-logistics** — `backend/app/features/dispatch/router.py`, `backend/app/features/dispatch/service.py`, `backend/tests/test_dispatch.py`, `backend/app/features/recall/router.py`, `backend/app/features/recall/service.py`, `backend/tests/test_recall.py`, `frontend/src/pages/Dispatch.tsx`, `frontend/src/pages/Trace.tsx`, `frontend/src/api/types.ts`, `frontend/src/mock/router.ts`.

- **O-alerts** — `backend/app/features/alerts/router.py`, `backend/app/features/alerts/service.py`, `backend/tests/test_alerts.py`, `frontend/src/pages/Alerts.tsx`, `frontend/src/lib/alertStream.ts`, `frontend/src/main.tsx`, `frontend/src/layout/AppShell.tsx`, `frontend/src/pages/Spc.tsx`.

- **O-platform** — `backend/app/features/platform/router.py`, `backend/app/features/platform/service.py`, `backend/tests/test_platform.py`, `backend/app/main.py`, `backend/app/db/models.py`, `backend/app/db/session.py`, `backend/app/db/locking.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_initial.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/Dockerfile`, `contracts/openapi.json`, `backend/scripts/export_contracts.py`, `backend/tests/conftest.py`, `docker-compose.yml`, `.env.example`, `Makefile`.

- **O-evidence** — `backend/app/features/auth_evidence/router.py`, `backend/app/features/auth_evidence/service.py`, `backend/tests/test_auth_evidence.py`, `backend/app/storage/evidence.py`, `frontend/src/api/client.ts`.

- **O-reports** — `backend/app/features/report_workflow/router.py`, `backend/app/features/report_workflow/service.py`, `backend/tests/test_report_workflow.py`, `frontend/src/pages/Reports.tsx`.
