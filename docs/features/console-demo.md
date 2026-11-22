# Control-room UI, dashboard and reproducible demo

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

The foundation branch includes a routed console, auth gate, shell, theme tokens, mock/live client, overview and feature screens. The browser hosts the mock router. No tracked Playwright suite, live integrations, report viewer or standalone Alert Centre.

**Handoff.** Keep all existing UI and future Playwright work with Opus. Astra supplies reproducible feature inputs and module-level tests.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/main.tsx`
- `frontend/src/index.css`
- `frontend/src/layout/AppShell.tsx`
- `frontend/src/pages/Overview.tsx`
- `frontend/src/auth/auth.tsx`
- `frontend/src/components/domain.tsx`
- `frontend/src/components/ui.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/api/hooks.ts`
- `frontend/src/api/types.ts`
- `frontend/src/mock/router.ts`
- `frontend/src/mock/state.ts`
- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/README.md`
- `frontend/index.html`
- `frontend/public/mark.svg`
- `frontend/tsconfig.json`
- `frontend/vite.config.ts`
- `.claude/launch.json`
- `e2e/.gitkeep`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/console_demo/router.py`
- `backend/app/features/console_demo/service.py`
- `backend/tests/test_console_demo.py`
- `backend/scripts/seed_demo.py`
- `frontend/src/api/generated.ts`
- `e2e/playwright.config.ts`
- `e2e/quality-to-recall.spec.ts`

## Inputs, Outputs & Contract

- `GET /dashboard/summary` → `{units_by_status,active_containments,held_packages,open_shipments,open_alerts,recent_scans,dispatch_eligible_units,first_pass_yield}`. Explicit dev-only `POST /demo/reset {}` → `{ok:true}` for admin only behind `AUTOQ_DEMO=1`; proposed use same seed routine as CLI, disable route outside demo. No automatic reset on startup.

- Routes today `/`, `/lots`, `/lots/:id`, `/units/:id`, `/inspect`, `/containment`, `/packing`, `/dispatch`, `/trace`, `/spc`, `/audit`, `/scanner`; login renders at the auth gate. Proposed `/reports/:id` and `/alerts` are owned by their feature sheets. `VITE_API_MODE=live` for acceptance. Styling and UX decisions belong exclusively to Opus.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

Reads `units`, `containment_runs`, `packages`, `shipments`, `alerts`, `inspections`, `scans` from their owning features; seed writes through the same services. No dashboard-only table required. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Run the production browser journey against real FastAPI/Postgres, with mock mode disabled. Record actual request/response and evidence IDs for image → release → containment block → recall → PDF. Assert a network error presents a visible diagnostic.

- Repeat the explicitly seeded demo after a clean setup. Test physical phone and two-browser alerts separately. Synthetic data must remain labeled in the interface and report.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
