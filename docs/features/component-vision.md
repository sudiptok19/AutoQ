# Component image quality and reference inspection

**Owner:** Me / Astra

**Current Status in Repo:** Partial

The browser measures blur, glare and resolution on pixels and can compare generated bracket fixtures. Captured real photos have no calibrated server detector. The real-photo fixture directory contains instructions only.

**Handoff.** Opus implements inspection policy, persistence and screen. Astra supplies evidence only, including reasons and bytes. Opus adds OpenCV/NumPy/scikit-image to the shared dependency files.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/lib/cv.ts`
- `frontend/src/pages/Inspect.tsx`
- `fixtures/images/README.md`

Planned files owned by Me / Astra. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/component_vision/router.py`
- `backend/app/features/component_vision/service.py`
- `backend/tests/test_component_vision.py`
- `fixtures/images/inspection/manifest.json`
- `fixtures/images/inspection/golden_01.jpg`
- `fixtures/images/inspection/good_01.jpg`
- `fixtures/images/inspection/defect_01.jpg`
- `fixtures/images/inspection/blurry_01.jpg`
- `fixtures/images/inspection/glare_01.jpg`

## Inputs, Outputs & Contract

- `GET /inspection-profiles` returns `{items:[{id,part_id,station_id,method_version,calibrated}],next_cursor}`. `POST /inspection-profiles/{id}/golden` accepts multipart `image` and returns the updated profile. Opus supplies authenticated profile lookup and immutable revision storage. Enrollment alone does not establish validated thresholds.

- `evaluate(image: bytes, reference: bytes | None, profile: VisionProfile) -> VisionEvidence`. Profile supplies blur/glare/resolution limits, registration limits and calibrated version. Result carries `quality_gate` and `analysis` from CONTRACT §8.1, aligned/difference PNG bytes when registration succeeds, and reason codes. Output score is nullable. Invalid input → `422 INVALID_IMAGE`; poor image, absent calibration or registration failure → no usable score with `IMAGE_QUALITY_*`, `MODEL_UNCALIBRATED` or proposed `ALIGNMENT_FAILED`. Opus maps these to HOLD at `POST /inspections`.

- Opus provides `list_profiles(principal, cursor) -> ProfilePage`, `load_profile(profile_id, principal) -> VisionProfile` and `save_reference(profile_id, image_sha256, expected_version, principal) -> VisionProfile`. Reference writes produce a new revision and invalidate calibration until the held-out evaluation is accepted.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`inspection_profiles(id,part_id,station_id,method_version,calibrated,reference_sha256,thresholds_json,calibration_version)` and `inspections(id,unit_id,station_id,profile_id,method_version,quality_gate_json,raw_score,image_sha256,aligned_sha256,diff_sha256,evidence_source)`; nullable derived evidence and scores. Profile versions referenced by inspections are immutable. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Enroll a photographed good object, capture held-out intact, visibly defective, blurred and glaring objects in a separate session. Record capture conditions and expected labels in the manifest. Evaluate all, report false accepts, false rejects and unverifiable counts. An unusable image must never acquire a numeric release score.

- Through the live inspection route, retrieve the original and derived evidence by digest and match the persisted measurement to the module result. Repeat without calibration and verify HOLD and zero passports.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
