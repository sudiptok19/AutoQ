# 4M policy and clearance

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

Containment board and browser rule/obligation logic exist. The master-plan reset, distinct ordered unit accounting and database enforcement need implementation.

**Handoff.** Opus owns policy versions, lock order, inspection side effects and UI.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Containment.tsx`
- `frontend/src/mock/router.ts`
- `frontend/src/api/types.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/containment/router.py`
- `backend/app/features/containment/service.py`
- `backend/tests/test_containment.py`

## Inputs, Outputs & Contract

- `GET/POST /four-m-events`, `GET /four-m-events/{id}`; create `{category,planned,line_id,description,reason,effective_at}` → event with `containment_id`. `GET /containments`, `GET /containments/{id}`, `POST /containments/{id}/clear {reason}` → updated run or `409 CONTAINMENT_NOT_SATISFIED`. `GET/PUT /policies` → versioned thresholds, lookback, first_n, recheck_existing, seal age, offline TTL.

- Freeze affected IDs by configured line/time policy; assign the first-N obligation in production order. Per run/epoch a distinct individual PASS counts once, HOLD leaves unresolved, any REJECT resets the count to zero. Overlaps have independent counters. Threshold reached means ready for review; only manager clearance with reason and all applicable holds cleared permits activation.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`policies(id,version,settings_json)`, `four_m_events(id,line_id,category,planned,effective_at,reason,actor_id)`, `containment_runs(id,event_id,required_qty,cleared_qty,sequence_epoch,status,cleared_by,clearance_reason,cleared_at)`, `containment_units(run_id,unit_id,production_seq)`, `containment_credits(run_id,epoch,unit_id,inspection_id)` unique `(run_id,epoch,unit_id)`. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Commit PASS, retry, out-of-order scan, HOLD, REJECT and new passing sequence. Verify independent counters for two overlapping runs and no issuance at threshold before manager clearance.

- Clear only one run and verify gate stays blocked. Clear the last applicable hold with a named manager and reason; RED-affected units retain their individual requirement.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
