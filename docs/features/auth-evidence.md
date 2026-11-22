# Authentication, evidence and audit trail

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

Login, role-aware session, mock auth and browser audit display exist. No server JWT, protected evidence byte store or durable audit log.

**Handoff.** Astra consumes authenticated user, file read/write and role dependencies supplied by Opus; secrets and environment wiring belong to Opus.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/auth/auth.tsx`
- `frontend/src/pages/Login.tsx`
- `frontend/src/pages/Audit.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/mock/router.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/auth_evidence/router.py`
- `backend/app/features/auth_evidence/service.py`
- `backend/tests/test_auth_evidence.py`
- `backend/app/storage/evidence.py`

## Inputs, Outputs & Contract

- `POST /auth/login {username,password}` → `{access_token,user:{id,username,display_name,roles[]}}`; `GET /auth/me` → user. Roles are `admin`, `operator`, `quality_engineer`, `quality_manager`, `dispatcher`. The agreed QC_MANAGER maps to `quality_manager`.

- `GET /evidence/{sha256}` → original bytes and stored media type; proposed service `put_evidence(data,media_type,evidence_source)->sha256`. `GET /audit?entity_id=` → paginated events; `GET /audit/verify` → verification status and first invalid sequence if any. Bearer-header authenticated downloads; Opus updates image loading to fetch blobs. `401`, `403 ROLE_FORBIDDEN`, `404` for absent evidence.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`users(id,username,password_hash,display_name)`, `user_roles(user_id,role)`, `evidence(sha256,media_type,byte_size,storage_path,evidence_source,created_by,created_at)`, `audit_events(id,seq,prev_hash,hash,actor_id,action,entity_type,entity_id,reason,data_json,created_at)`. Unique digest and audit sequence. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Use real accounts of different roles, upload the same file twice, fetch it by authenticated HTTP and recompute SHA-256. Anonymous fetch returns 401. Attempt a forbidden clearance as a dispatcher.

- Verify the audit chain after concurrent writes and detect a deliberately changed copy in an isolated test database.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
