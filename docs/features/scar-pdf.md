# Evidence-linked 8D/SCAR PDF rendering

**Owner:** Me / Astra

**Current Status in Repo:** Not started

The branch defines Report8D and report item types. No report page, report mock handler or PDF generator is tracked.

**Handoff.** Opus owns evidence aggregation, draft creation/approval, DB retention and report screen. Astra owns the binary download router and deterministic renderer.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/api/types.ts`

Planned files owned by Me / Astra. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/scar_pdf/router.py`
- `backend/app/features/scar_pdf/service.py`
- `backend/tests/test_scar_pdf.py`

## Inputs, Outputs & Contract

- `GET /reports/{id}/pdf` (authenticated) → `200 application/pdf` with a download filename. `render_pdf(report: ReportSnapshot) -> bytes`. Snapshot fields are `id,status,source,generator,created_at,sections[{key,title,items[{kind,text,evidence[{type,id,label}]}]}],approved_by,approved_at` plus proposed `as_of`, `counts`, `evidence_source`. Render all D1–D8. Mark D4/D6/D8 pending until reviewed evidence exists. Use stable metadata and ordering.

- Opus supplies an immutable snapshot and access-controlled evidence links. `404 REPORT_NOT_FOUND`, proposed `422 REPORT_SNAPSHOT_INVALID` for a malformed snapshot, `503 REPORT_RENDER_FAILED` for an unavailable renderer. Never substitute an empty PDF.

- Opus provides `load_snapshot(report_id, principal) -> ReportSnapshot` and `read_evidence(sha256, principal) -> bytes` for linked evidence access. Proposed `counts` is `{affected_units,held_packages,at_plant,loaded_not_departed,in_transit,delivered}` using unit counts for location fields. Preserve the snapshot’s evidence-source labels on output.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

No direct DB access. Opus adapter reads `reports(id,status,source_type,source_id,snapshot_json,snapshot_sha256,pdf_sha256,created_at,approved_by,approved_at)` and the evidence store. `snapshot_json` freezes recall counts and evidence refs at generation. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Generate a report from a real mixed-lot recall. Download the PDF through live HTTP, parse and visually inspect every page. Compare counts and evidence IDs with the stored snapshot; check long tables, pagination and pending sections.

- Render the same fixed snapshot twice and check matching bytes. Change a later shipment state and confirm the existing report remains reproducible. An inaccessible record or missing snapshot yields the specified error.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
