# Orders, load decisions and shipment movement

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

Dispatch screen and browser handlers for loads, closure and movements exist. PostgreSQL locking, uniqueness, task creation and departure revalidation are unstarted.

**Handoff.** Opus owns the authoritative gate, offline replay entrypoint and all transactions; Astra outputs are inputs.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Dispatch.tsx`
- `frontend/src/mock/router.ts`
- `frontend/src/api/types.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/dispatch/router.py`
- `backend/app/features/dispatch/service.py`
- `backend/tests/test_dispatch.py`

## Inputs, Outputs & Contract

- `GET/POST /orders`, `GET /orders/{id}`; create `{order_no,customer_id,part_id,qty_units,destination}`. `GET/POST /shipments`, `GET /shipments/{id}`; create `{order_id,vehicle_no,dock}`. `POST /shipments/{id}/scans {token,client_event_id,device_id,seal_check_id}` → HTTP 200 `{scan_id,decision,replay,package_id,reasons,checks,shipment_progress,decided_at,decided_by}` per CONTRACT §5; forbidden role → 403.

- `POST /shipments/{id}/close {}` reconciles and prepares manifest. `POST /shipments/{id}/events {type,location,occurred_at}` records DEPARTED/CHECKPOINT/DELIVERED. On DEPARTED repeat all gates and atomically finalize manifest digest + movement. A hold after close blocks departure (`409 QUARANTINED`/`CONTAINMENT_ACTIVE`) and creates removal work. Final transaction uses Opus’s common heat→lot→unit→package→order→shipment lock order and uniqueness constraints.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`orders(id,order_no,customer_id,part_id,qty_units,destination)`, `shipments(id,order_id,vehicle_no,dock,status,manifest_sha256)`, `scans(id,shipment_id,package_id,client_event_id,device_id,decision,reasons_json,checks_json,decided_at,actor_id)`, `movement_events(id,shipment_id,type,location,occurred_at)`, `loads(id,package_id,shipment_id,accepted_scan_id,removed_at)` with unique active load and unique scan retry key. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Race two actual PostgreSQL sessions loading one package; verify one active load. Repeat the same event and prove stable prior decision. Reject wrong order, invalid QR, expired/revoked key, stale seal, duplicate, excess quantity and forbidden role.

- Race quarantine versus departure in both commit orders, with a hold between close and departure. Check totals, physical state, blocked attempt logs and removal/consignee work.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
