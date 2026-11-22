# Ed25519 signing and verification

**Owner:** Me / Astra

**Current Status in Repo:** Partial

Opus already implemented canonical JSON and AQ1 signing/verification in TypeScript, plus token tests. Server key handling and the Python signer/verifier are unstarted.

**Handoff.** Opus owns lifecycle, policy, key registry adapter and browser implementation. Astra never changes browser crypto or issues a passport as a side effect of signing.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/lib/canonical.ts`
- `frontend/src/lib/token.ts`
- `frontend/src/lib/token.test.ts`
- `frontend/src/scanner/offline.ts`

Planned files owned by Me / Astra. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/passport_crypto/router.py`
- `backend/app/features/passport_crypto/service.py`
- `backend/tests/test_passport_crypto.py`
- `backend/tests/vectors/aq1_interop.json`

## Inputs, Outputs & Contract

- `GET /keys` → `{keys:[{kid,alg:"Ed25519",public_key,status,not_before,not_after}]}`. `sign(payload: dict, key: SigningKey) -> str`; `verify(token: str, trusted_keys, expected_type, as_of) -> VerifiedPayload | CryptoError`. `AQ1.<base64url(canonical UTF-8 payload)>.<base64url(signature)>`, sorted keys, integer timestamps, no floats or padding. Preserve PP, PKG, SNAP domain separation. The server obtains private keys by `AUTOQ_SIGNING_KEY_PATH`.

- Use the v2 PP, PKG and SNAP shapes in [alignment decision 3](../CONTRACT_ALIGNMENT.md#decision-3). PKG binds `seal_revision_id`; SNAP binds device, operator and policy version plus key validity. Proposed PP v2 adds `inspection_basis`, `authorizing_inspection_ids`, `evidence_digests`, `four_m_tagged` to the unit/lot/part/heat identity. Explicit schema validation by version/type; legacy PP cannot silently satisfy v2 release rules. Typed failures include `TOKEN_MALFORMED`, `SIG_INVALID`, `KEY_UNKNOWN`, `KEY_REVOKED`, `KEY_EXPIRED`, `TOKEN_MISMATCH`. Opus owns `POST /verify` identity lookup and eligibility.

- Opus provides `list_public_keys() -> list[KeyRecord]` and a trusted private-key handle to the release/package/snapshot services. A signing-key lookup failure aborts issuance with `503 SIGNING_KEY_UNAVAILABLE`. Public vectors contain disposable test material only.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

Pure signing and verification require zero business tables. Opus supplies public metadata from `signing_keys(kid,public_key,status,not_before,not_after)` and records `passports(id,unit_id,kid,token,payload_version,inspection_basis,four_m_tagged,status,superseded_by_passport_id,issued_at)` plus immutable evidence links. Private keys stay outside these tables. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Use a disposable key and deterministic public vectors: Python signs and the Opus browser verifier accepts the exact bytes; JS signs with a test key and Python verifies. Cover all three token kinds, non-ASCII text, altered signatures, noncanonical JSON, unknown/revoked/expired keys and type confusion.

- Verify a genuine signed yet held package through live `/verify` and dispatch. Cryptographic validity stays true while eligibility is false. Verify a public key rotation with both a previously issued token and a newly issued token.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
