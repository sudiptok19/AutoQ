# Catalog, serialized lots and frozen sampling

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

Lots, units and genealogy views read browser-seeded records. The mock lists master data. Frozen membership, sample selection, database constraints and real writes are unstarted.

**Handoff.** Opus provides typed station/profile/lot lookups to Astra. Both approve the sampled-lot contract extension.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Lots.tsx`
- `frontend/src/pages/UnitPage.tsx`
- `frontend/src/mock/state.ts`
- `frontend/src/mock/router.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/catalog_sampling/router.py`
- `backend/app/features/catalog_sampling/service.py`
- `backend/tests/test_catalog_sampling.py`

## Inputs, Outputs & Contract

- `GET/POST /suppliers {code,name}`, `/parts {part_number,name,drawing_rev,inspection_profile_id,is_a_part}`, `/lines {name,station_id}`, `/customers {name,city}`; read lists return `{items,next_cursor}`. Writes return the created record. `POST /heats` multipart `heat_code,supplier_id,material_grade,certificate`; `GET /heats`.

- `GET/POST /lots`, `GET /lots/{id}`; create `{lot_code,part_id,line_id,heat_ids,planned_qty}`. Proposed single-heat prototype validates `len(heat_ids)==1`, retaining the UI array. `POST /lots/{id}/units {count}`, `GET /units?lot_id=`, `GET /units/{id}`. Proposed `POST /lots/{id}/freeze-and-sample {policy_version}` → `{lot_id,frozen_at,sample_unit_ids,policy_version}`. Reject additions after freeze (`409 LOT_FROZEN`); failed/missing sample holds release.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`suppliers(id,code,name)`, `parts(id,part_number,name,drawing_rev,inspection_profile_id,is_a_part)`, `lines(id,name,station_id)`, `heats(id,heat_code,supplier_id,material_grade,certificate_sha256)`, `lots(id,lot_code,part_id,line_id,planned_qty,frozen_at,policy_version,sample_status)`, `lot_heats(lot_id,heat_id)`, `units(id,lot_id,serial,production_seq,requires_individual_inspection,four_m_tagged)`, `lot_samples(lot_id,unit_id,policy_version)` unique pair, `customers(id,name,city)`. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Create a real lot, freeze it and race a unit addition against freeze. Check the stored sample exactly matches the frozen membership. Reject foreign-unit evidence and prevent failed samples from authorizing release.

- Prove A-parts always require individual evidence, and late production enters a new lot.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
