# Architecture & Decision Log

Short entries. Newest at the bottom. Format: decision — why — tradeoff.

1. **Modular monolith (FastAPI) + SPA (Vite/React).** Smallest design where policy, CV and crypto share one authoritative process. — Tradeoff: one backend to scale; fine for a hackathon and a pilot.
2. **Vite SPA, not Next.js.** No SSR/SEO need; PWA/offline and one codebase for desktop + phone are simpler. — Tradeoff: no server components.
3. **PostgreSQL over SQLite.** Dispatch gate and recall need row locks (`SELECT … FOR UPDATE`) and unique constraints under concurrency. — Tradeoff: needs Docker.
4. **Per-piece serialized units.** Inspection, passport and packing are per unit so we never claim a bulk lot was individually inspected. Bulk items are out of scope. *Sampled lot release added in #15.*
5. ~~**Passports issued on PASS even during containment.**~~ *Superseded by #14.* Resolves the brief's "no passport until first N pass" vs "mint during containment" contradiction. Eligibility is never stored; the gate recomputes it.
6. **Override ≠ release.** A QE/QM review of a HOLD can only send the unit back for reinspection or reject it. No admin bypass anywhere.
7. **Golden-reference alignment + diff before PatchCore.** Works on a laptop with phone photos and a small image set; reports a raw anomaly score, not a probability. PatchCore is P2.
8. **Ed25519, canonical JSON, `AQ1.` tokens with `kid`.** Widely reviewed, fast, small signatures that fit in a QR. Frontend verifies offline with `@noble/ed25519` (audited, pure JS) rather than relying on WebCrypto Ed25519 support on every phone.
9. **Offline = provisional.** Offline scans never count as loaded until server sync accepts them; manifest close blocked until sync. Snapshot TTL is short (default 4h) and signed.
10. **Hold is an overlay, not a location state.** Keeps "where is the package physically" truthful while still blocking it.
11. **Location buckets derived only from scan + movement events** (never from a status flag).
12. **8D drafts from deterministic templates first.** Optional LLM narrative later; root cause is always `HYPOTHESIS`/`PENDING_HUMAN` until a human edits/approves.
13. **Numeric defaults (blur 80, glare 12%, SSIM 0.72, first-N 5, lookback 30 min, tightening 15%) are provisional** and live in `/policies`, calibrated on our fixture set and reported as such.
14. **No passport issuance during RED containment (v0.2).** Evidence is kept, but minting waits for distinct-unit recovery and QM clearance. A credential that exists before release stays a truthful record of what was certified. — Tradeoff: slower release after a 4M event.
15. **Frozen lot sampling with explicit inspection basis.** `SAMPLED_LOT` vs `INDIVIDUAL_100PCT` is signed into the passport, and A-parts are always individual. Without this the system would over-claim per-unit inspection.
16. **AQ1 envelope kept; payloads versioned to v2.** This avoids a JWS migration, and the scanner can reject claims it doesn't understand.
17. **Departure re-validates in one transaction with a global lock order** (heat → lot → unit → package → order → shipment), and physical follow-up is tracked as durable segregation tasks.
18. **Work split revised.** Claude/Opus owns UI plus all DB and transactional code. GPT/Astra owns seven isolated modules with typed interfaces. One writer per path removes the migration and lock-order conflicts two DB writers would create.
19. **Sync SQLAlchemy 2 + psycopg 3 behind FastAPI (threadpool), not async.** Explicit `SELECT … FOR UPDATE` transactions are simpler to reason about and test under real concurrency. — Tradeoff: per-request threads; fine at plant scale.
20. **Live mode adapts transport, not policy.** `frontend/src/api/live.ts` holds the only live-vs-mock differences: it reroutes `/units/:id` to `/detail`, nests the flat recall preview, flattens versioned policies, and tags fixture uploads as `SYNTHETIC_TEST`. Every decision still comes from the server. `/evidence/{sha}` alone also accepts `?access_token=`, because an `<img>` cannot send headers. — Tradeoff: the token can appear in local access logs; acceptable on the plant LAN, revisit with signed short-lived URLs.
21. **The demo is seeded through the public API, not SQL inserts.** `scripts/seed_demo.py` drives the same endpoints the UI uses, so seeded data carries real audit, outbox, signatures and evidence, and the story is a regression test (`tests/test_console_demo.py`). Browser fixture captures fetch the backend's own SYNTHETIC renders (`/demo/fixtures/*`), so they register against the calibrated golden reference.
22. **An 8D approval cites an uploaded review record, never a checkbox.** `POST /evidence` (QE/QM) stores the minutes as `UPLOAD` evidence, and the approval cites its SHA-256. Releasing a recall does **not** wait for physical tasks; open tasks stay visible until someone records what was done. — Tradeoff: a QM can release before the crate is physically moved, but the open task and its audit trail make that visible.
