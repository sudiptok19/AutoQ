# SPC drift measurement

**Owner:** Me / Astra

**Current Status in Repo:** Partial

The SPC page, chart and in-browser CUSUM logic exist against mock state. No persisted baseline or live score series exists.

**Handoff.** Opus maps line/station, reads measurements, persists baselines and publishes alerts. Astra owns the analysis and read router.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Spc.tsx`
- `frontend/src/mock/router.ts`
- `frontend/src/api/types.ts`

Planned files owned by Me / Astra. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/spc/router.py`
- `backend/app/features/spc/service.py`
- `backend/tests/test_spc.py`

## Inputs, Outputs & Contract

- `GET /spc/series?part_id=&line_id=&method_version=` → `{part_id,line_id,method_version,baseline,rule,points,alerts}` (CONTRACT §8.5). Opus resolves the line to its station for the single-station prototype. Require an explicit station selector in a future contract before pooling multiple stations.

- `compute_series(points, frozen_baseline, k_sigma, h_sigma, min_n) -> Series`. Input points are ordered `(inspection_id,t,score,synthetic)` for one part/station/method. Deduplicate inspection IDs. Use upper CUSUM `max(0, previous + z - k_sigma)`. Cross `h_sigma` to open a warning. Also report five consecutive strictly rising same-context scores as `FIVE_RISING`; deduplicate simultaneous triggers per inspection and baseline. Insufficient or near-zero variance yields `COLLECTING` or `LOW_VARIANCE`; propose nullable `z`/`cusum` then. Changing method starts a new baseline. `422` for invalid configuration. GET is read-only; Opus calls the same computation on inspection writes to persist alerts.

- Opus provides `load_spc_inputs(part_id, line_id, method_version, principal) -> (points, baseline, rule, persisted_alerts)`. Include `station_id` in the internal context. Preserve raw points when baseline is collecting and leave unavailable normalized values null.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`spc_baselines(id,part_id,station_id,method_version,n,mean,std,frozen_at,k_sigma,h_sigma,min_n)`; `inspections(id,unit_id,station_id,method_version,raw_score,created_at,evidence_source)`; `alerts(id,type,inspection_id,baseline_id,created_at,status)` unique `(type,inspection_id,baseline_id)`. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Use the actual evaluator on a frozen known series, manually computed crossing index and a stable segment. Exercise cold start, zero variance, duplicate IDs and version change. Mark analytic test data synthetic.

- Persist real inspection scores, call the live endpoint, and compare the chart values and persisted first warning with the evaluator. Re-read the chart and retry ingestion; neither may duplicate alerts.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
