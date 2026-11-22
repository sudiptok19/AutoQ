# Astra brief: your work on AutoQ-Trace

**For:** Suman / Astra (GPT coding agent) · **From:** Sudipta / Opus (Claude Code) · **Date:** 2026-11-23
**Binding references:** [`CONTRACT.md` §10 (v0.2)](CONTRACT.md#10-v02-binding-deltas-option-a), [`WORK_SPLIT.md`](WORK_SPLIT.md), your sheets in [`features/`](features/), [`AGENTS.md`](../AGENTS.md).

Paste this whole file into your agent as its task brief. Where it is more specific than a feature sheet, follow **this** file.

---

## 0. TL;DR

You own **seven isolated deliverables**. Each one is either a pure library (bytes or dataclasses in, dataclasses out, with no database) or infrastructure:

| # | Deliverable | Your folder / files | HTTP routes you write |
|---|---|---|---|
| A1 | Component vision (quality gate, reference alignment and diff) | `backend/app/features/component_vision/` | `GET /inspection-profiles`, `POST /inspection-profiles/{id}/golden` |
| A2 | Seal vision (ORB + RANSAC + SSIM) | `backend/app/features/seal_vision/` | `POST /packages/{id}/seal-checks` |
| A3 | SPC drift (CUSUM + FIVE_RISING) | `backend/app/features/spc/` | `GET /spc/series` |
| A4 | AQ1 crypto (sign/verify, v2 schemas, vectors) | `backend/app/features/passport_crypto/` | `GET /keys` |
| A5 | 8D/SCAR PDF renderer | `backend/app/features/scar_pdf/` | `GET /reports/{id}/pdf` |
| A6 | WebSocket bus + event JSON Schema | `backend/app/core/ws_bus.py`, `contracts/ws_events.schema.json` | none (Opus mounts `/ws/events`) |
| A7 | LAN HTTPS gateway for phones | `docker-compose.https.yml`, `Caddyfile`, `certs/README.md` | none |

**Opus owns everything else**: the database, models and migrations, every transaction and lock, auth, the evidence store, outbox and alerts, `main.py` route registration, `pyproject.toml`/`uv.lock`, `conftest.py`, all frontend code, and every other HTTP route (inspections, passports issue/verify, packing, dispatch, recall, sync, reports workflow, …).

**The rule that prevents collisions:** you only create or edit files listed in §2 "You write". If you need anything outside that list, ask through a `contract/*` PR or a message. Never edit it yourself.

---

## 1. Setup

```bash
git clone https://github.com/sudiptok19/AutoQ.git && cd AutoQ
git checkout main && git pull
cd backend && uv sync          # Python 3.12; all your deps are already installed (see §5)
uv run pytest -m nodb          # your tests: no Postgres needed
```
Full integration tests (`uv run pytest`) need Postgres: run `make db` from the repo root. The DB listens on host port **5433**, and the API runs on **8010** (`make backend`).

You can work on any OS. Paths in this repo are POSIX-style, and the Compose override must work on both Windows and macOS Docker Desktop.

---

## 2. File ownership

### You write (and only these)
```
backend/app/features/component_vision/__init__.py, router.py, service.py
backend/app/features/seal_vision/__init__.py,      router.py, service.py
backend/app/features/spc/__init__.py,              router.py, service.py
backend/app/features/passport_crypto/__init__.py,  router.py, service.py
backend/app/features/scar_pdf/__init__.py,         router.py, service.py
backend/app/core/ws_bus.py
backend/tests/test_component_vision.py
backend/tests/test_seal_vision.py
backend/tests/test_spc.py
backend/tests/test_passport_crypto.py
backend/tests/test_scar_pdf.py
backend/tests/test_ws_bus.py
backend/tests/vectors/aq1_interop.json
backend/scripts/evaluate_vision.py                 # held-out evaluation runner (§A1/A2)
contracts/ws_events.schema.json
docker-compose.https.yml
Caddyfile
certs/README.md
fixtures/images/inspection/**                       # real photos + manifest.json
fixtures/images/seal/**                             # real photos + manifest.json
datasets/**                                          # optional: larger eval sets + metrics JSON
```
You may put private helpers inside your own feature folder, e.g. `backend/app/features/seal_vision/_orb.py`. Do **not** create a shared `utils` module.

### Read-only for you (import freely, never edit)
| File | What you use |
|---|---|
| `backend/app/ports.py` | Input dataclasses (`VisionProfile`, `SealProfile`, `SealContext`, `SpcPoint`, `SpcBaseline`, `SpcRule`, `ReportSnapshot`, …), the `Ports` protocol, and the `ports()` FastAPI dependency |
| `backend/app/core/errors.py` | `ApiError(status, code, message, details=None)`. Raise it for every client-visible failure. |
| `backend/app/core/auth.py` | `require_roles(*roles)` and `current_principal` dependencies, `Principal` |
| `backend/app/core/canonical.py` | `canonicalize(obj) -> str`, byte-compatible with `frontend/src/lib/canonical.ts`. Use it for AQ1. |
| `backend/app/core/signing.py` | `load_signing_key() -> (kid, nacl SigningKey)` and `b64url()` |
| `backend/app/core/storage.py` | `sniff(bytes)` if you need to validate media types. Persistence goes through ports. |
| `backend/app/db/session.py` | `get_db` dependency. Pass the `Session` into port calls; never query it yourself. |
| `docs/*`, `frontend/**`, `backend/tests/conftest.py`, `backend/pyproject.toml`, `uv.lock`, `backend/app/main.py`, root `docker-compose.yml`, `Makefile`, `.env.example` | Read only |

### Never touch
`backend/app/db/**`, `backend/alembic/**`, `backend/app/main.py`, `backend/app/core/*` (except `ws_bus.py`), `backend/app/ports.py`, `backend/app/policy/**`, any other `backend/app/features/*` folder, `frontend/**`, `e2e/**`, `contracts/openapi.json`, `docs/**` (change docs through a `contract/*` PR).

If a bug sits in a file you can't edit, open an issue or message with the file, the line and a proposed fix. Opus fixes it the same day.

---

## 3. How your code plugs in

```
           your pure function             your router (thin)                Opus
bytes ──► service.evaluate(...) ──► router calls ports().load_* / record_* ──► DB, storage, audit, outbox
```

- **Pure functions first.** Every deliverable exposes a pure function with typed inputs and outputs. That function holds all the science and almost all your tests.
- **Routers are thin.** They parse the request, call `ports()` to load inputs, call your pure function, call `ports()` to persist, and return JSON. No SQL, no ORM, no file writes.
- **Router module shape:**
  ```python
  from fastapi import APIRouter, Depends
  from app.core.auth import Principal, require_roles
  from app.db.session import get_db
  from app.ports import Ports, ports

  router = APIRouter(tags=["seal-vision"])     # NO prefix; Opus mounts under /api/v1

  @router.post("/packages/{package_id}/seal-checks")
  def seal_check(package_id: str, image: UploadFile, ..., db=Depends(get_db),
                 p: Principal = Depends(require_roles("dispatcher", "quality_engineer", "quality_manager")),
                 P: Ports = Depends(ports)) -> dict: ...
  ```
- **Registration.** Do not edit `main.py`. In your PR description, write the line Opus should add (e.g. `from app.features.seal_vision.router import router as seal_vision`). Opus registers it in an integration PR.
- **Testing routers without a DB:** build a tiny app in your test file:
  ```python
  import pytest
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from app.core import errors
  from app.core.auth import current_principal, Principal
  from app.ports import ports
  pytestmark = pytest.mark.nodb

  def make_client(fake_ports, roles=("dispatcher",)):
      app = FastAPI(); errors.install(app); app.include_router(router, prefix="/api/v1")
      app.dependency_overrides[ports] = lambda: fake_ports
      app.dependency_overrides[current_principal] = lambda: Principal("u1", "t", "T", tuple(roles))
      app.dependency_overrides[get_db] = lambda: None
      return TestClient(app)
  ```
  Mark **every** test module with `pytestmark = pytest.mark.nodb`. The shared conftest then never opens Postgres for your tests.
- **Errors.** Use the envelope through `ApiError`, with codes exactly as listed below. Put `component` and `remediation` in `details` when they help an operator.
- **Fail closed.** Never catch an exception and return a default (`0`, `[]`, `"PASS"`, `"INTACT"`, an empty PDF). Unusable input means `HOLD`/`UNVERIFIABLE` evidence or an explicit error.
- **Evidence labels.** Every image-derived output carries `evidence_source ∈ {LIVE_CAPTURE, PRERECORDED_EVAL, SYNTHETIC_TEST}`, passed in by the caller.

---

## 4. Deliverables in detail

### A1 · Component vision (`features/component_vision/`)

**Pure API (`service.py`):**
```python
METHOD_VERSION = "gd-0.3"   # bump on any algorithm change; SPC baselines are per method_version

@dataclass(frozen=True)
class VisionEvidence:
    quality_gate: dict        # CONTRACT §8.1: passed, blur_laplacian_var, glare_ratio, width, height, thresholds, failures[]
    analysis: dict            # method, method_version, calibrated, score|None, hold_threshold, reject_threshold,
                              # threshold_tightened(False), regions[{x,y,w,h,score}]
    reason_codes: list[str]   # IMAGE_QUALITY_BLUR|IMAGE_QUALITY_GLARE|IMAGE_QUALITY_RESOLUTION|MODEL_UNCALIBRATED|ALIGNMENT_FAILED|SCORE_ABOVE_HOLD|SCORE_ABOVE_REJECT
    aligned_png: bytes | None # only when registration succeeded
    diff_png: bytes | None    # heatmap overlay, only when registration succeeded
    raw_score: float | None

def evaluate(image: bytes, reference: bytes | None, profile: VisionProfile) -> VisionEvidence
def quality_gate(image: bytes, thresholds: dict) -> dict   # also exported on its own
```
**Rules:**
1. Decode the image; on failure raise `ApiError(422, "INVALID_IMAGE", …)`.
2. Run the quality gate **first**: resolution, Laplacian-variance blur and overexposed-pixel glare. If it fails, set `score=None`, put the `IMAGE_QUALITY_*` codes in `reason_codes`, and stop.
3. If `profile.calibrated` is False or `reference` is None, return `score=None` with `MODEL_UNCALIBRATED` and stop.
4. Register the image to the reference (ORB/ECC/feature homography; your choice, but document it). Poor registration means `score=None` with `ALIGNMENT_FAILED`.
5. Compute the reference difference over the aligned part region and output a raw score in [0,1]. **It is not a probability.** Return regions and the heatmap PNG.
6. You **do not** decide PASS/HOLD/REJECT. You report `SCORE_ABOVE_HOLD`/`SCORE_ABOVE_REJECT` against `profile.thresholds["hold"/"reject"]` as evidence, and Opus's policy maps that to a verdict.
7. Behaviour is deterministic: the same bytes and profile give the same output.

**Routes:** `GET /inspection-profiles` → `{items:[{id,part_id,station_id,method_version,calibrated,calibration_version,reference_sha256}],next_cursor:null}` (any role). `POST /inspection-profiles/{id}/golden` multipart `image` → updated profile (roles `quality_engineer`, `quality_manager`). Uploading a reference **does not** set `calibrated=True`; Opus's `save_reference` creates a new uncalibrated revision.

**Fixtures:** real phone photos in `fixtures/images/inspection/`: at least `golden_01.jpg` plus held-out `good_*`, `defect_*`, `blurry_*`, `glare_*`, `misaligned_*`, captured in a **separate session** from the golden image. `manifest.json` records, for each file: label, device, lighting, distance, session id and `evidence_source: PRERECORDED_EVAL`.

**Evaluation:** `backend/scripts/evaluate_vision.py` runs `evaluate` over the manifest and prints and writes `datasets/vision_eval.json`: counts per condition, false accepts, false rejects and unverifiable. Thresholds you choose are documented there as provisional.

**Tests (`test_component_vision.py`):** blur, glare and low-resolution each give `score=None` with the right code; an uncalibrated profile gives `MODEL_UNCALIBRATED`; a garbage image gives 422; a good image scores below a defect image on your real fixtures; determinism; the route works with fake ports and the role check is enforced.

### A2 · Seal vision (`features/seal_vision/`)

**Pure API:**
```python
METHOD_VERSION = "seal-orb-ssim-0.2"

@dataclass(frozen=True)
class SealReference:          # returned at enrolment so Opus can reject an unusable reference
    quality_gate: dict
    keypoints: int
    usable: bool
    reason_codes: list[str]

@dataclass(frozen=True)
class SealEvidence:
    result: str               # INTACT | SUSPICIOUS | UNVERIFIABLE
    method_version: str
    metrics: dict             # keypoints_ref, keypoints_probe, good_matches, inliers, inlier_ratio, ssim|None,
                              # thresholds{min_inliers,intact_ssim,suspicious_ssim}
    quality_gate: dict        # passed, failures[] (IMAGE_QUALITY_*, TOO_FEW_KEYPOINTS, REGISTRATION_FAILED)
    aligned_png: bytes | None
    diff_png: bytes | None
    explanation: str          # one human sentence, shown in UI as-is

def assess_reference(reference: bytes, profile: SealProfile) -> SealReference
def compare(reference: bytes, probe: bytes, profile: SealProfile) -> SealEvidence
```
**Rules (decision 5):**
- Order: quality gate → ORB keypoints → BFMatcher + Lowe ratio → RANSAC homography → warp → SSIM over the seam ROI.
- A bad image, too few keypoints, too few inliers or a degenerate homography gives **`UNVERIFIABLE`**, never SUSPICIOUS or INTACT. In that case `ssim=None`, `aligned_png=None` and `diff_png=None`.
- **`SUSPICIOUS` only when registration succeeded** and the seam SSIM is below `suspicious_ssim`, or a localized discrepancy is clearly measured.
- `INTACT` when registered and SSIM ≥ `intact_ssim`. Anything between the two thresholds is `UNVERIFIABLE` with the explanation "ambiguous similarity, recapture".

**Route:** `POST /packages/{package_id}/seal-checks`, multipart `image` plus optional form field `evidence_source` (default `LIVE_CAPTURE`). Roles: `dispatcher`, `quality_engineer`, `quality_manager`. Flow: `ctx = P.load_seal_context(db, package_id, p)` → `ev = compare(ctx.reference_bytes, probe, ctx.profile)` → `return P.record_recheck(db, ctx, ev, probe, media_type, evidence_source, p)`. Opus raises `404 PACKAGE_NOT_FOUND`, `409 SEAL_NOT_ENROLLED` and `409 SEAL_REVISION_CHANGED`. You raise `422 INVALID_IMAGE`. Freshness, blocking loads and alerts are Opus's job.

**Fixtures:** `fixtures/images/seal/enroll.jpg`, `intact_recapture.jpg`, `torn.jpg`, `repositioned.jpg`, `bad_light.jpg`, plus more if you can. Use a **real patterned paper or tape seal across a real box seam**, with `manifest.json` as in A1. Add the seal results to `datasets/vision_eval.json`.

**Tests:** intact gives INTACT; torn and repositioned give SUSPICIOUS; bad light gives UNVERIFIABLE; the reference itself gives INTACT; a blank or texture-less image gives UNVERIFIABLE (never SUSPICIOUS); the route works with fake ports.

### A3 · SPC drift (`features/spc/`)

**Pure API:**
```python
@dataclass(frozen=True)
class Series:
    baseline: dict            # {status: COLLECTING|LOW_VARIANCE|READY, n, mean|None, std|None, frozen_at|None}
    rule: dict                # {type:"CUSUM_UPPER", k_sigma, h_sigma, min_n, also:["FIVE_RISING"]}
    points: list[dict]        # {inspection_id, t (ISO Z), score, z|None, cusum|None, synthetic}
    warnings: list[dict]      # {inspection_id, at_index, rules:["CUSUM_UPPER","FIVE_RISING"], message}

def compute_series(points: list[SpcPoint], baseline: SpcBaseline | None, rule: SpcRule) -> Series
def freeze_baseline(points: list[SpcPoint], rule: SpcRule) -> SpcBaseline | None   # None while n < min_n
```
**Rules:** deduplicate by `inspection_id`, keeping the first, and sort by `t`. The baseline is the first `min_n` points of the given context, which is always one (part, station, method_version). Below `min_n` the status is `COLLECTING`; if `std < min_std` it is `LOW_VARIANCE`. In both cases **`z` and `cusum` are `None`** for every point; never fake statistics. When READY, `S_i = max(0, S_{i-1} + z_i − k)` for points after the baseline, and a warning fires when `S > h`. **FIVE_RISING** means five consecutive strictly increasing scores after the baseline. Emit **one warning per inspection** even when both rules fire on it (merge the `rules` list). Messages say "suspected drift", never "predicted defect". An invalid rule (k ≤ 0, h ≤ 0, min_n < 2) raises `ApiError(422, "SPC_RULE_INVALID")`.

**Route:** `GET /spc/series?part_id=&line_id=&method_version=`. Response per CONTRACT §8.5 with nullable `z`/`cusum`, plus `alerts` = persisted alerts from the port. Any role. Read-only: Opus calls `compute_series` on inspection writes to persist alerts.

**Tests:** a hand-computed crossing index on a fixed synthetic series (label it synthetic), a stable series with no warning, cold start, zero variance, duplicate IDs, FIVE_RISING firing alone, both rules firing on one inspection giving one warning, and a different method_version not mixing in.

### A4 · AQ1 crypto (`features/passport_crypto/`)

**Pure API:**
```python
class CryptoError(Exception):
    code: str   # TOKEN_MALFORMED | SIG_INVALID | KEY_UNKNOWN | KEY_REVOKED | KEY_EXPIRED | TOKEN_MISMATCH | SCHEMA_INVALID | VERSION_UNSUPPORTED

@dataclass(frozen=True)
class KeyRecord:
    kid: str; public_key: str; status: str; not_before: int; not_after: int     # b64url pubkey, unix seconds

@dataclass(frozen=True)
class VerifiedPayload:
    typ: str; v: int; kid: str; payload: dict; payload_bytes: bytes

def sign(payload: dict, kid: str, key: nacl.signing.SigningKey) -> str            # validates schema first
def verify(token: str, trusted_keys: list[KeyRecord], expected_type: str | None, as_of: int) -> VerifiedPayload
def validate_payload(payload: dict) -> None                                         # raises CryptoError(SCHEMA_INVALID/VERSION_UNSUPPORTED)
```
**Rules (CONTRACT §10.3):**
- Envelope `AQ1.<b64url(canonical UTF-8)>.<b64url(sig)>` with no padding. Use `app.core.canonical.canonicalize`.
- Reject a payload whose bytes are not canonical (`TOKEN_MALFORMED`).
- Exact v2 shapes for PP, PKG and SNAP, as in §10.3: required keys, types, ASCII claim names, JS-safe integers, no floats, no unknown keys.
- v1 tokens verify only with `expected_type=None` and a `VERSION_UNSUPPORTED` marker. They must never satisfy a v2 check.
- Key policy: REVOKED always fails. RETIRED verifies only if the token's `iat` falls inside `[not_before, not_after]`. ACTIVE outside its window is `KEY_EXPIRED`.
- An `expected_type` mismatch gives `TOKEN_MISMATCH`.

**Interop vectors (`backend/tests/vectors/aq1_interop.json`):** a **disposable** test key (seed included, clearly labelled TEST ONLY), and for each of PP, PKG and SNAP: the payload, canonical string, token and expected verify result. Include negative cases: altered signature, non-canonical JSON, non-ASCII text value, unknown kid, revoked key, expired key and type confusion. Opus adds a vitest test that runs the browser verifier against the same file. Also add a case where **JS signs and Python verifies**: generate it with `node` and `@noble/ed25519` from `frontend/node_modules`, and commit only the resulting vector.

**Route:** `GET /keys` → `{keys:[{kid,alg:"Ed25519",public_key,status,not_before,not_after}]}` from `P.list_public_keys(db)`. No auth (public keys). `POST /verify` and identity lookups are Opus's.

**Tests:** round trip for all three types, every negative vector, rotation (an old token signed with a RETIRED key verifies, a new token with the ACTIVE key verifies, and a REVOKED key fails) and determinism.

### A5 · 8D/SCAR PDF (`features/scar_pdf/`)

**Pure API:** `render_pdf(snapshot: ReportSnapshot) -> bytes` (ReportLab).
**Rules:**
- Render D1–D8 in order, including the header (report id, revision id, source, `as_of`, generator, status), the counts table with **unit** counts for location fields, and every item tagged FACT / HYPOTHESIS / PROPOSED / PENDING_HUMAN with its evidence references (type, id, label, sha256 prefix).
- D4, D6 and D8 render a visible **PENDING HUMAN REVIEW** banner unless they hold evidence-backed items.
- `status=DRAFT` gives a DRAFT watermark on every page.
- `evidence_source` is printed on the cover.
- **Deterministic bytes**: fix the PDF metadata creation date to `snapshot.created_at`, use invariant mode, and sort nothing implicitly. The same snapshot gives byte-identical output.
- Long tables paginate.
- A malformed snapshot raises `ApiError(422, "REPORT_SNAPSHOT_INVALID")`. A renderer failure raises `ApiError(503, "REPORT_RENDER_FAILED")`. Never return an empty PDF.

**Route:** `GET /reports/{report_id}/pdf` (any authenticated role) → `snap = P.load_snapshot(...)`, then `pdf = render_pdf(snap)`, then `P.record_pdf(db, snap, pdf, p)`, then `Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="8D-{snap.id}-{snap.revision_id}.pdf"'})`.

**Tests:** byte-identical double render; DRAFT watermark present (extract the text with `pypdf` if you need it, but propose that dependency in your PR); pending banners; a 200-row evidence table paginates; malformed input gives 422; the route works with fake ports.

### A6 · WebSocket bus (`backend/app/core/ws_bus.py`, `contracts/ws_events.schema.json`)

**API:**
```python
class InvalidEvent(ValueError): ...
class Bus:
    async def subscribe(self, socket: WebSocket, *, audience: str) -> None
    async def unsubscribe(self, socket: WebSocket) -> None
    async def publish(self, event: Mapping, *, audience: str) -> int    # number of subscribers queued
bus = Bus()                                                             # module singleton, one process
```
**Rules:** validate each event against `contracts/ws_events.schema.json` (Draft 2020-12, using `jsonschema`) before fan-out, and raise `InvalidEvent` if it fails. Give each socket a bounded queue (default 100). On overflow, close that socket with code 1013 without affecting others. Clean up on disconnect. An audience is an opaque string chosen by the **server**, never by the client. Zero subscribers returns 0.

**Schema:** the envelope from CONTRACT §10.5: `event_id, type, severity, audible_siren, entity_type, entity_id, reason_codes[], timestamp (date-time, Z), payload{}`. It has the 7 type enums and 4 severity enums, and `additionalProperties: false` at the top level. Opus consumes it read-only. You own it.

**Tests:** two audiences get correct delivery; a malformed event is rejected; a slow client is closed with 1013 while the fast client still receives; disconnect cleanup. Use FastAPI's TestClient WebSocket support in a tiny test app, with `nodb`.

**Not yours:** `/ws/events` route, ticket auth, outbox relay, REST catch-up. Opus builds those on top of `bus.publish`.

### A7 · LAN HTTPS gateway (`docker-compose.https.yml`, `Caddyfile`, `certs/README.md`)

- The override adds a `caddy` service (pin an exact image tag) publishing 443 (and 80 for redirect). Run it with `docker compose -f docker-compose.yml -f docker-compose.https.yml up -d`, and keep the base `db` service unchanged.
- Upstreams come from env with these defaults: `AUTOQ_BACKEND_UPSTREAM=host.docker.internal:8010` and `AUTOQ_FRONTEND_UPSTREAM=host.docker.internal:5173`. **8010, not 8000.** On Linux add `extra_hosts: ["host.docker.internal:host-gateway"]`.
- Routes: `/api/*`, `/ws/*` (with upgrade) and `/openapi.json` go to the backend with paths intact; everything else goes to the frontend. Same origin, so no CORS is needed. If an upstream is down, the proxy returns a visible 502, not a fallback page.
- Certificates are mounted read-only from **outside the repo** (`AUTOQ_CERT_DIR`, default `~/.autoq-certs`). `certs/README.md` explains mkcert step by step on macOS and Windows:
  - issue a leaf for the LAN IP or hostname;
  - install **only** the public `rootCA.pem` on Android and iOS, including the iOS "Certificate Trust Settings" toggle;
  - never copy `rootCA-key.pem` anywhere;
  - re-issue when the LAN IP changes;
  - remove phone trust after the hackathon.
- Acceptance: the phone opens `https://<lan-ip>/scanner` with **no warning**, the camera works, `/api/v1/health` returns 200, and a WebSocket connects. Record the phone model, browser and LAN IP in the PR.

---

## 5. Dependencies already installed for you
`numpy`, `opencv-python-headless` (5.x), `scikit-image`, `reportlab`, `jsonschema`, `pynacl`, `fastapi`, `pytest`, `httpx`. If you need anything else (e.g. `pypdf` for tests), **list it in your PR description** and Opus adds it to `pyproject.toml` and `uv.lock`. Do not edit the manifest or lockfile.

## 6. Branches, PRs and checklist
- Branch per deliverable from fresh `main`: `feature/astra-component-vision`, `feature/astra-seal-vision`, `feature/astra-spc`, `feature/astra-crypto`, `feature/astra-scar-pdf`, `feature/astra-ws-bus`, `feature/astra-https`.
- One deliverable per PR into `main`. Rebase on `main` before asking for review.
- PR description template:
  ```
  ## Deliverable: A? <name>
  Files changed (must all be in ASTRA_BRIEF §2): …
  Router import for main.py: from app.features.<x>.router import router as <x>   (or "none")
  New dependencies needed: none | <pkg>=<ver> (why)
  Ports used: P.load_… / P.record_…
  Tests: `uv run pytest -m nodb tests/test_<x>.py` → N passed
  Real-data evidence: fixtures/… + datasets/vision_eval.json (counts) | n/a
  Honesty: what is synthetic / provisional / not yet verified on a phone
  ```
- Run `git diff --name-only origin/main` before pushing. Every path must be in §2.

## 7. Suggested order
1. **A4 crypto + vectors** first. Opus's passport issuance and the scanner both depend on it.
2. **A1 component vision.** Capture the golden and held-out photo sessions early, since lighting takes iteration.
3. **A2 seal vision.** Reuse the photo session, with a real taped box.
4. **A6 WS bus** (small, and unblocks live alerts).
5. **A3 SPC.**
6. **A7 HTTPS gateway.** Needed for the real-phone demo, so it must be done before the joint rehearsal.
7. **A5 PDF.**

Ship each one as soon as its tests pass. Don't batch them.

## 8. What Opus builds in parallel (so you know what's coming)
Catalog, lots and frozen sampling → inspection verdict policy and passport issuance, calling your A1 and A4 → 4M containment runs → packing and seal revisions, calling A2 → dispatch gate with locking → recall, unit buckets and segregation tasks → offline snapshot and sync, calling A4 → alerts, outbox relay and the `/ws/events` route, calling A6 → report workflow, calling A5 → frontend switched to the live API and v2 → Playwright demo E2E.

Until your module lands, Opus uses **clearly labelled interim adapters** in Opus-owned paths behind the same function signatures, and deletes them when your PR merges. Your signatures above are therefore the contract. If you need to change one, say so **before** merging.

## 9. Non-negotiables (repeat of the important bits)
- No silent fallbacks, ever. Unusable input means HOLD/UNVERIFIABLE evidence or an explicit error code.
- CV output is **evidence**, never approval. Scores are raw, not probabilities.
- Thresholds are provisional demo parameters until held-out evaluation supports them, and you write down what you measured.
- Real bytes, real keys and real photos in tests; label synthetic data as synthetic.
- No OEM branding or endorsement claims in fixtures, PDFs or docs.
- Private keys and the mkcert CA key never enter git. Only the disposable test seed in the vectors file, clearly labelled.

Questions or contract changes: open a `contract/<topic>` PR or message Sudipta. Don't work around ownership.
