# Physical seal recheck

**Owner:** Me / Astra

**Current Status in Repo:** Partial

Packing and Dispatch already capture seal images. The mock simulates a comparison for synthetic fixtures; actual phone photos cannot produce a calibrated server recheck today.

**Handoff.** Opus owns the enrollment/re-enrollment HTTP routes, revision writes, operator UI, evidence storage and gate. Astra owns only the recheck router and comparison.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Packing.tsx`
- `frontend/src/pages/Dispatch.tsx`
- `frontend/src/mock/router.ts`
- `fixtures/images/README.md`

Planned files owned by Me / Astra. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/seal_vision/router.py`
- `backend/app/features/seal_vision/service.py`
- `backend/tests/test_seal_vision.py`
- `fixtures/images/seal/manifest.json`
- `fixtures/images/seal/enroll.jpg`
- `fixtures/images/seal/intact_recapture.jpg`
- `fixtures/images/seal/torn.jpg`
- `fixtures/images/seal/repositioned.jpg`
- `fixtures/images/seal/bad_light.jpg`

## Inputs, Outputs & Contract

- `POST /packages/{id}/seal-checks`, multipart `image` → persisted `SealCheck` in CONTRACT §8.2. `compare(reference: bytes, probe: bytes, profile: SealProfile) -> SealEvidence(result, method_version, metrics, quality_gate, aligned_png, diff_png, explanation)`. Compare the seam ROI after ORB/RANSAC registration. Preserve `INTACT | SUSPICIOUS | UNVERIFIABLE`. Weak registration, bad light and ambiguous similarity yield `UNVERIFIABLE`. Include `revision_id`, `quality_gate`, `evidence` and `evidence_source`; unavailable `ssim` and derived URLs are null.

- `404 PACKAGE_NOT_FOUND`, `409 SEAL_NOT_ENROLLED`, proposed `409 SEAL_REVISION_CHANGED` if the package is resealed during analysis. Opus loads a reference revision and atomically commits a result only against the same current revision. The dispatch policy owns freshness, alert generation and blocking.

- Opus provides `load_seal_context(package_id, principal) -> (revision_id, reference_bytes, SealProfile)` and `record_recheck(package_id, expected_revision_id, evidence, principal) -> SealCheck`. Store probe and derived image digests before publishing the completed check. A storage failure blocks the check.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`seal_revisions(id,package_id,revision_no,reference_sha256,roi_json,profile_version,invalidated_at)` unique `(package_id,revision_no)`; `seal_checks(id,package_id,revision_id,probe_sha256,aligned_sha256,diff_sha256,result,metrics_json,method_version,actor_id,checked_at,evidence_source)`. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Photograph a patterned seal across an actual opening; independently recapture intact, torn, displaced and poorly lit samples. Publish counts by physical label. No intact result for an unreadable/failed registration or a visibly changed held-out seam.

- Through authenticated real HTTP, enroll, recheck, repack, then attempt to load against the old check. Verify exact block reason and retained evidence. Race resealing against verification and ensure a stale result cannot authorize a load.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
