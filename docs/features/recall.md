# Genealogy, quarantine and segregation tasks

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

Trace graph, recall preview and execution UI and mock impact calculations exist. Atomic containment, DB lineage and durable tasks are unstarted.

**Handoff.** Opus supplies frozen recall evidence and counts to the report feature.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Trace.tsx`
- `frontend/src/mock/router.ts`
- `frontend/src/api/types.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/recall/router.py`
- `backend/app/features/recall/service.py`
- `backend/tests/test_recall.py`

## Inputs, Outputs & Contract

- `GET /trace?heat=|lot=|unit=|package=|serial=` (one selector) → `{root,nodes,edges,summary}`. `POST /recalls/preview {scope:{type,id}}` read-only; `POST /recalls {scope,reason}`, `GET /recalls/{id}`, `POST /recalls/{id}/release {reason}`. Response `affected` follows §8.4, preserving package counts; proposed `unit_counts:{at_plant,loaded_not_departed,in_transit,delivered}` prevents confusing packages with units.

- Proposed `GET /segregation-tasks` → `{items,next_cursor}`, `POST /segregation-tasks/{id}/complete {reason}` → updated task. A hold on one descendant blocks the whole mixed package. Track `REMOVAL_REQUIRED`, `SEGREGATION_REQUIRED`, `IN_TRANSIT_RECALL`, `NOTIFY_CUSTOMER`. Unknown scope → 404, forbidden actor → 403, changed preview → recompute under lock at execution.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

Join `heats→lot_heats→lots→units→package_items→packages→loads→shipments→movement_events` with historical membership times. `recalls(id,scope_type,scope_id,reason,actor_id,executed_at,impact_json)`, `recall_holds(recall_id,entity_type,entity_id,released_at)`, `segregation_tasks(id,recall_id,scan_id,package_id,shipment_id,type,status,reason,created_at,completed_by,completed_at)`. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Plant an unrelated heat in a shared package and known descendants in each physical bucket, including unpackaged units. Reconcile distinct affected units and impacted whole packages independently.

- Race a load against quarantine; check persisted holds and tasks and the next denied scan. Release one recall while another hold is active and verify continued blocking.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
