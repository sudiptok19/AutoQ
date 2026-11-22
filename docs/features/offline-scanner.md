# Dock scanner, snapshots and offline reconciliation

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

The phone PWA, QR camera (BarcodeDetector/jsQR), signed snapshot verification, IndexedDB append/queue, local gate and mock replay are implemented. The server end of sync is a browser handler. Existing unit tests include fake-indexeddb.

**Handoff.** Preserve Opus’s existing phone workflow. Astra supplies the AQ1 signer, LAN TLS override, and post-commit event transport. Opus owns every client edit and database operation.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Scanner.tsx`
- `frontend/src/scanner/QrCamera.tsx`
- `frontend/src/scanner/store.ts`
- `frontend/src/scanner/offline.ts`
- `frontend/src/scanner/offline.test.ts`
- `frontend/src/lib/chain.ts`
- `frontend/src/lib/token.ts`
- `frontend/vite.config.ts`
- `frontend/src/mock/router.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/offline_scanner/router.py`
- `backend/app/features/offline_scanner/service.py`
- `backend/tests/test_offline_scanner.py`

## Inputs, Outputs & Contract

- `GET /offline/snapshot?shipment_id=` → `{token}` containing the exact v2 fields and capacity semantics in [decision 3](../CONTRACT_ALIGNMENT.md#decision-3). Verify signer via authenticated trusted keys independently of the snapshot. Cache scoped per shipment/device. Stale snapshot → local `SNAPSHOT_STALE`, require refresh.

- `POST /sync/scans {device_id,snapshot_id,events:[{client_event_id,seq,prev_hash,hash,token,shipment_id,scanned_at,local_decision,local_reasons}]}` → `{results:[{client_event_id,outcome,scan,note}],chain:{ok,server_head,broken_at?}}`. CONTRACT §8.7 and `frontend/src/lib/chain.ts` define hashed event fields and byte encoding. Bind operator and device server-side. Enforce exact next sequence, compare already-processed content and reject changed replay. Broken chain returns `chain.ok=false`, zero new accepted events. Accept/advance head transactionally. Local block cannot become a load; conflicting provisional accept creates segregation work.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`devices(id,operator_id,last_seq,head_hash)`, `offline_snapshots(id,device_id,shipment_id,kid,token,issued_at,expires_at)`, `device_events(device_id,seq,client_event_id,hash,prev_hash,snapshot_id,scan_id,outcome,scanned_at,operator_id)` unique device/sequence and event ID; `segregation_tasks` as in recall. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Use a real phone on trusted HTTPS with live API, cache the production PWA, disconnect, scan, reload, reconnect and sync twice. Validate physical IndexedDB persistence and one accepted DB load.

- Run two devices on one package, quarantine while one is offline, and submit a gapped chain. Confirm explicit conflict/removal instructions, no duplicate credits and no head advance on rejected batch.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
