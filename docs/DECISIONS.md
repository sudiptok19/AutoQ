# Architecture & Decision Log

Short entries. Newest at the bottom. Format: decision — why — tradeoff.

1. **Modular monolith (FastAPI) + SPA (Vite/React).** Smallest design where policy, CV and crypto share one authoritative process. — Tradeoff: one backend to scale; fine for a hackathon and a pilot.
2. **Vite SPA, not Next.js.** No SSR/SEO need; PWA/offline and one codebase for desktop + phone are simpler. — Tradeoff: no server components.
3. **PostgreSQL over SQLite.** Dispatch gate and recall need row locks (`SELECT … FOR UPDATE`) and unique constraints under concurrency. — Tradeoff: needs Docker.
4. **Per-piece serialized units.** Inspection, passport and packing are per unit so we never claim a bulk lot was individually inspected. Bulk/sampled items are out of scope.
5. **Passports issued on PASS even during containment; dispatch eligibility computed separately.** Resolves the brief's "no passport until first N pass" vs "mint during containment" contradiction. Eligibility is never stored; the gate recomputes it.
6. **Override ≠ release.** A QE/QM review of a HOLD can only send the unit back for reinspection or reject it. No admin bypass anywhere.
7. **Golden-reference alignment + diff before PatchCore.** Works on a laptop with phone photos and a small image set; reports a raw anomaly score, not a probability. PatchCore is P2.
8. **Ed25519, canonical JSON, `AQ1.` tokens with `kid`.** Widely reviewed, fast, small signatures that fit in a QR. Frontend verifies offline with `@noble/ed25519` (audited, pure JS) rather than relying on WebCrypto Ed25519 support on every phone.
9. **Offline = provisional.** Offline scans never count as loaded until server sync accepts them; manifest close blocked until sync. Snapshot TTL is short (default 4h) and signed.
10. **Hold is an overlay, not a location state.** Keeps "where is the package physically" truthful while still blocking it.
11. **Location buckets derived only from scan + movement events** (never from a status flag).
12. **8D drafts from deterministic templates first.** Optional LLM narrative later; root cause is always `HYPOTHESIS`/`PENDING_HUMAN` until a human edits/approves.
13. **Numeric defaults (blur 80, glare 12%, SSIM 0.72, first-N 5, lookback 30 min, tightening 15%) are provisional** and live in `/policies`, calibrated on our fixture set and reported as such.
