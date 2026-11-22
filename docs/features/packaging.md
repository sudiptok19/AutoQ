# Package membership and seal revision lifecycle

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

Packing, item add/remove, enrollment and mock package tokens exist. No durable membership history or live seal revision handling.

**Handoff.** Opus stores revisions and immutable evidence. Invoke Astra’s signer and recheck output; enrollment and re-enrollment stay Opus-owned.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Packing.tsx`
- `frontend/src/pages/Dispatch.tsx`
- `frontend/src/mock/router.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/packaging/router.py`
- `backend/app/features/packaging/service.py`
- `backend/tests/test_packaging.py`

## Inputs, Outputs & Contract

- `POST /packages {order_id}`, `GET /packages/{id}`, proposed list `GET /packages?order_id=`. `POST /packages/{id}/items {unit_id}` or `{token}` and `DELETE /packages/{id}/items/{unit_id}` while OPEN. Return package or `409` with blockers.

- `POST /packages/{id}/seal` multipart `image` → enrolled package with PKG token. `POST /packages/{id}/re-enroll` multipart `image,reason` by QE. Proposed `POST /packages/{id}/unpack {reason}` opens package and invalidates revision/token. Bind package, order, exact item passports and reference digest. Require a current intact recheck to load; `WRONG_PART`, `PASSPORT_HELD`, `TOKEN_MISMATCH` and `SEAL_RECHECK_REQUIRED` are visible gate reasons.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`packages(id,label,order_id,state,current_seal_revision_id,token)`, `package_items(id,package_id,unit_id,passport_id,added_at,removed_at)` with at most one open membership per unit; `seal_revisions` and `seal_checks` per seal sheet. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Pack eligible units from two lots of one part, reject a wrong part/held unit. Change contents, reseal and prove the previous token/check fails. Inspect the complete membership history, evidence and actor IDs.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
