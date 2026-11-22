# Inspection verdict, review and passport lifecycle

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

Browser mock returns verdicts, signs on PASS and exposes verification. The data types assume a single inspection per passport. No live release engine exists.

**Handoff.** Consume Astra vision evidence and crypto primitives; Opus owns every release decision, database write and public lifecycle endpoint.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Inspect.tsx`
- `frontend/src/pages/UnitPage.tsx`
- `frontend/src/api/types.ts`
- `frontend/src/mock/router.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/inspection_passports/router.py`
- `backend/app/features/inspection_passports/service.py`
- `backend/tests/test_inspection_passports.py`
- `backend/app/policy/eligibility.py`

## Inputs, Outputs & Contract

- `POST /inspections` multipart `unit_id,station_id,image` → CONTRACT §8.1 Inspection + nullable passport. `GET /inspections?unit_id=&lot_id=`, `POST /inspections/{id}/review {action:REINSPECT|REJECT,reason}`. No direct HOLD→PASS. Missing calibrated evidence produces HOLD with reasons.

- `GET /passports/{id}`, `POST /verify {token}` → `{signature_valid,kid,typ,payload,exists,status,eligibility}`. Proposed `POST /passports/issue {unit_id}` → passport or `409` with blockers. Use the same issuance service on normal GREEN PASS, successful sample completion and post-clearance issuance. Return existing matching issuance on retry.

- Proposed passport has `inspection_basis`, `authorizing_inspection_ids[]`, `four_m_tagged`, `evidence_digests[]`, `superseded_by_passport_id`. Require individual PASS for an A-part or RED-affected unit. GREEN non-A sample basis requires every frozen selected unit to pass. During RED, persist evidence and block minting/activation. Supersede stale sampled identities on qualified individual reinspection after clearance.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`inspections(id,unit_id,station_id,profile_id,method_version,decision,reason_codes_json,created_at,actor_id)` plus vision fields; `inspection_reviews(id,inspection_id,action,reason,reviewer_id,created_at)`; `passports` as in crypto; `passport_inspections(passport_id,inspection_id)` and `passport_evidence(passport_id,sha256)`. Enforce one current passport per unit and retain superseded revisions. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Use real photos, DB, keys and API. Trace every signed ID back to the correct unit or frozen sample. Test A-part, sampled GREEN, RED, overlapping holds, wrong-lot evidence and human review.

- Read a superseded but authentic passport and show `signature_valid=true` with dispatch denied. Retry issuance concurrently and verify one current passport.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
