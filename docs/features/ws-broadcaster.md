# WebSocket broadcaster and event schema

**Owner:** Me / Astra

**Current Status in Repo:** Not started

The foundation branch has browser REST alert reads/acknowledgement. The durable alert workflow and authenticated socket entrypoint belong to [Opus](alerts.md).

## Exact File Paths

Planned files owned by Me / Astra.

- `backend/app/core/ws_bus.py`
- `contracts/ws_events.schema.json`
- `backend/tests/test_ws_bus.py`

## Inputs, Outputs & Contract

- Public Python hooks: `await bus.subscribe(socket, *, audience: str)`, `await bus.unsubscribe(socket)`, `await bus.publish(event: Mapping, *, audience: str) -> int` (number queued to eligible connected subscribers). The authenticated Opus route supplies the server-derived audience. No client may choose its authorization scope. Use one backend process for the prototype.
- Validate against the schema before fan-out. Invalid payload raises `InvalidEvent`. A bounded per-socket queue isolates a slow consumer; close it with 1013 on overflow. Clean up disconnected clients. Zero subscribers is a valid delivery count. The bus provides live delivery; Opus’s durable outbox, retries, authenticated catch-up and client event-ID deduplication handle reconnects.
- Schema fields, enums and example in [decision 5](../CONTRACT_ALIGNMENT.md#decision-5). Payloads carry references suitable for the authorized audience, UTC timestamps, and stable event IDs from the outbox. Opus calls publish only after the business transaction commits. Opus registers `/ws/events`, authenticates tickets and consumes this hand-authored schema read-only.

## Schema Dependencies

Zero database dependencies in the bus. Opus owns the alert and outbox tables and transaction boundaries. Only Opus adds backend package dependencies if needed.

## Verification Criteria

Run a real WebSocket test server and independent clients in two audiences. Prove delivery to the right audience, schema rejection of malformed events, clean disconnect and slow-client isolation. In the integrated app, race a rollback and a commit, reconnect a client, compare REST catch-up against published IDs, and verify the desktop receives the actual phone-triggered alert exactly once after client deduplication.
