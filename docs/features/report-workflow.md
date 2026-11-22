# Corrective-action draft and approval workflow

**Owner:** Friend / Opus

**Current Status in Repo:** Not started

A report interface and wire contract exist. No mock route, live route or report page was found.

**Handoff.** Opus owns DB and screen; the approved immutable snapshot is the complete Astra handoff.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/api/types.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/report_workflow/router.py`
- `backend/app/features/report_workflow/service.py`
- `backend/tests/test_report_workflow.py`
- `frontend/src/pages/Reports.tsx`

## Inputs, Outputs & Contract

- `POST /reports/8d {source:{type:RECALL|FOUR_M|ALERT,id}}` → Report8D draft; `GET /reports/{id}` → frozen report. `POST /reports/{id}/approve` proposed body `{reason,review_evidence_ids:[...]}` → approved revision with approver and time. PDF route belongs to Astra. Missing source → 404, unresolved mandatory review evidence → proposed `409 REPORT_INCOMPLETE`, role violation → 403.

- Build all D1–D8 from facts, hypotheses, proposed actions or `PENDING_HUMAN`. Persist source IDs, source timestamp and counts. Provide `load_snapshot(report_id,principal)->ReportSnapshot` for the renderer, and evidence refs resolvable with role-gated HTTP.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`reports` per PDF sheet; `report_revisions(id,report_id,snapshot_json,snapshot_sha256,created_by,created_at)`, `report_evidence(report_id,revision_id,entity_type,entity_id,sha256)`. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Create drafts from actual persisted recall, 4M event and alert. Review JSON, download Astra’s PDF, and open the linked original evidence as an authorized user. Missing root cause stays pending. Refuse unauthorized/incomplete approvals and audit an evidence-backed approval.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
