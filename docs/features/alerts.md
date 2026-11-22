# Alert Centre, WebSocket and audible alarm

**Owner:** Friend / Opus

**Current Status in Repo:** Partial

The SPC page reads and acknowledges mock alerts. No dedicated Alert Centre route, WebSocket server, or cross-device audible alarm implementation is tracked.

**Handoff.** Opus owns durable alerts/outbox, post-commit relay and retry, tickets, authenticated socket registration and scope checks, catch-up REST, and every UI/sound decision. Astra owns the [transport and schema](ws-broadcaster.md). Opus passes an authorized socket and allowed audience to the bus and calls `await bus.publish(event, audience=...)` from the outbox relay.

## Exact File Paths

Existing touchpoints at the audited commits (read-only to Astra). A reference to an existing client or mock file records a dependency. Its sole writer is Opus.

- `frontend/src/pages/Spc.tsx`
- `frontend/src/layout/AppShell.tsx`
- `frontend/src/mock/router.ts`

Planned files owned by Friend / Opus. The router, service and test listed first are exclusive to this feature.

- `backend/app/features/alerts/router.py`
- `backend/app/features/alerts/service.py`
- `backend/tests/test_alerts.py`
- `frontend/src/pages/Alerts.tsx`
- `frontend/src/lib/alertStream.ts`

## Inputs, Outputs & Contract

- `GET /alerts` → `{items,next_cursor}`, `POST /alerts/{id}/ack {reason}`; proposed `POST /alerts/{id}/resolve {reason}`. Proposed authenticated same-origin `WS /ws/events`. Envelope `{event_id,type,severity,audible_siren,entity_type,entity_id,reason_codes,timestamp,payload}`. `type` is `ALERT_CREATED|ALERT_RESOLVED|PASSPORT_REVOKED|LOT_4M_CHANGED|DISPATCH_STATE_CHANGED|SEGREGATION_TASK_CREATED|SPC_DRIFT_WARNING`.

- Publish committed events only. Proposed `POST /ws/tickets {}` with bearer auth → `{ticket,expires_at}` for one-time WS authentication. On reconnect use persisted alerts for catch-up and deduplicate by event_id. Bad auth → 401/403 or WS 1008. Explicitly show muted/acknowledged state; operator enables audio on the browser.

HTTP paths use the `/api/v1` prefix except `/openapi.json` and `/ws/events`. CONTRACT v0.1 defines the existing public vocabulary. New fields, service interfaces and extensions here are proposed for review. Errors use `{error:{code,message,details}}`. See [contract decisions and file ownership](../WORK_SPLIT.md#contract-decisions-before-integration).

## Schema Dependencies

`alerts(id,type,severity,entity_type,entity_id,reason_codes_json,audible_siren,status,created_at,ack_by,ack_at,resolved_by,resolved_at)` plus SPC linkage; `outbox_events(id,type,entity_type,entity_id,payload_json,created_at,sent_at)`. All table and column names here are proposed logical contracts for Opus. Only Opus edits models and migrations.

## Verification Criteria

- Use two real authenticated browsers. A blocked phone recheck/scan commits an alert, desktop receives it and plays enabled audio; acknowledge/mute and verify persisted status. Disconnect and reconnect, deduplicate, confirm no event for rolled-back writes.

Verification above is planned. Live acceptance uses the real server, PostgreSQL and applicable camera, keys, storage or renderer. A browser mock response cannot satisfy these checks.
