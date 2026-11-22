# AutoQ-Trace — Product & Engineering Brief for Claude Opus

> **Purpose:** Use this document as a starting brief to **architect, implement, integrate, test, and deliver** AutoQ-Trace: an end-to-end automotive component quality, traceability, and dispatch-integrity platform with a strong live demo.
>
> **Important:** This is **not** a rigid implementation specification. You are the lead product engineer and architect. You **may replace, simplify, combine, reorder, or substantially improve** the proposed architecture, algorithms, data model, workflows, technology choices, and milestones when you have a sound reason. Preserve the underlying real-world problems, safety properties, and end-to-end value. Make independent decisions; do not blindly implement suggestions from this brief.
>
> **UI/UX freedom:** The original notes contained detailed page layouts, component lists, route names, colors, animations, and other UI prescriptions. Those have **intentionally been removed**. **You own the product experience and visual direction.** Design and build the UI/UX as you judge most effective for an understandable, polished, credible, cohesive application. The workflows below describe required *capabilities*, not screens or layouts.

---

## 1. Your assignment and working style

Act as a senior full-stack engineer, system architect, product designer, and technical reviewer. **Build a working product, not just a high-level plan or mock-up.**

1. **First inspect the repository and environment.** Preserve useful existing work and identify its actual state. If starting from scratch, scaffold an appropriately small, maintainable project.
2. **Challenge the proposal.** Identify contradictions, security gaps, unjustified technical claims, calibration risks, overengineering, missing relationships, and features unlikely to work reliably in a hackathon demo.
3. **Choose an architecture.** Treat the stack and schema later in this document as candidates, not mandates. Prefer the smallest coherent design that makes the core workflow genuine and testable.
4. **Implement in complete vertical slices.** Each slice should connect data, business logic, relevant user interactions, errors, and automated tests. Avoid a collection of disconnected fancy features.
5. **Verify independently.** Run tests and, when the environment allows, exercise critical journeys through a browser/camera or reproducible fixture. Fix failures rather than declaring success based only on code existing.
6. **Be transparent.** Distinguish features truly implemented and tested from simulated inputs, seeded demo data, experimental algorithms, and future-work ideas. Never disguise manual toggles or random scores as working vision, cryptography, or physical tamper detection.
7. **Exercise autonomy.** Do not seek approval for routine engineering and design decisions. Briefly record consequential deviations, their rationale, and tradeoffs. Only request information if a genuinely blocking external requirement is missing (e.g., credentials or proprietary data).

**Definition of success:** A judge can follow a coherent journey from material/lot creation through inspection, authorization, packaging, loading, and recall; see an actual policy interlock stop a bad shipment; understand why a decision was made; and observe meaningful evidence of secure verification and traceability. A smaller reliable system is better than six superficial demos.

## 2. Business context and problems to solve

The intended setting is Indian automotive OEM supply chains and Tier-1/Tier-2 component suppliers (including a possible Maruti Suzuki hackathon context). Do **not** imply OEM endorsement or claim access to official plant infrastructure unless explicitly confirmed.

| Pain point | Why it matters | Product response |
| --- | --- | --- |
| **Unannounced 4M process changes** (Man, Machine, Material, Method) | Operator, machine, material, or process changes can invalidate quality assumptions while stock continues to move | Record 4M events, apply deterministic containment/inspection rules, block affected releases, and preserve an audit trail |
| **Counterfeits, stolen overruns, and genuine-box reuse** | Basic QR codes are copyable; authentic packaging does not prove authentic contents | Signed identity, lifecycle checks, packaging verification, optional secondary one-time consumption mechanism |
| **Fragmented component genealogy** | Heat-code certificates, inspections, packaging, and shipments cannot be connected quickly during a recall | Queryable lineage from raw-material heat to individual part/passport, package, and dispatch destination |
| **Reactive inspection misses developing process drift** | Acceptable pieces may show worsening anomaly scores before failure | Record continuous calibrated inspection signals and flag suspicious trends for human action |
| **Slow supplier containment and corrective-action reporting** | Teams manually reconstruct affected inventory, evidence, and supplier notifications | Rapid impact analysis, controlled quarantine, evidence-backed draft 8D/SCAR documents |
| **Connectivity failures at loading points** | Scanning and dispatch can stall in poor network conditions | A clearly bounded offline verification and synchronization workflow with explicit freshness and trust limits |

**Unverified background claims in the source notes:** figures such as “1 in 3 spare parts counterfeit,” “₹15,000 crore annual losses,” an “11-year A-Part retention requirement,” exact OEM procedures, and IATF-related obligations were asserted without attached authoritative proof. **Do not present these as verified facts or compliance certifications.** Confirm from reliable documents if you need to cite them in the product/pitch. Implement retention as a configurable policy, with e.g. 11 years as a **provisional example**, not a universal legal guarantee.

## 3. The product's non-negotiable principles

- **Deterministic authorization:** Vision/ML outputs are evidence and risk signals, **never independent shipment approval**. A server-side policy engine decides inspect/hold/reject/release/quarantine based on validated facts.
- **Fail safely:** Missing evidence, unknown model version, bad image quality, unsigned or revoked credentials, incomplete containment, or uncertain seal verification must never silently become `PASS` or authorized dispatch.
- **Preserve genealogy:** Maintain traceable associations among material heat, supplier, production lot, part identity, inspection, passport, package, shipment, and destination. Model quantities and genealogy truthfully.
- **Tamper-evident != tamper-proof:** A signature protects data authenticity, not the physical object to which a QR label was attached. A hash chain detects certain alterations, not every insider attack. Document those limits.
- **Offline trust is bounded:** A device using cached data cannot guarantee it knows the newest revocations or other offline scans. Label any offline decision according to its real authority; the server resolves conflicts on reconnection.
- **Human accountability:** Reviews, 4M changes, overrides, quarantines, and report approval have named actors, timestamps, reasons, and permissions.
- **No fraudulent demos:** Simulation is acceptable for equipment and datasets not available, but clearly identify synthetic inspection signals, demo-only data, and uncalibrated detectors. A deterministic fixture is preferable to an unreliable “AI” claim.
- **Build for the available hardware:** The demo should run on a laptop and a smartphone/webcam where possible, without relying on specialized industrial cameras, RFID equipment, or constant internet access.

## 4. Capabilities to implement (product behavior, not UI direction)

### A. Supplier, material, part, and lot foundation

Support part definitions, supplier records, raw-material heat/batch identifiers and certificates/evidence, incoming or production lots, quantity, lineage, and configurable inspection policies. Establish who is allowed to create or change these records. Store timestamps and evidence digests.

### B. Actual inspection flow and anomaly/drift monitoring

Provide image capture/upload, image-quality validation, inspection results with supporting image evidence, continuous anomaly/quality scores where a legitimate calibrated algorithm produces them, and a deterministic `PASS`, `HOLD`, or `REJECT` decision. Where possible, expose the model/method version and interpretable evidence (e.g., a heatmap or difference image). Every inspection must be tied to a real part/lot identity.

**Candidate technical approaches — choose based on reliable performance, not novelty alone:**

- Image quality gate using blur, glare, resolution, and optionally framing/alignment checks.
- Anomaly detection using PatchCore/anomalib or another appropriate one-class method **if a suitable image set, reproducible setup, and measured threshold are available**.
- A simpler OpenCV golden-reference/alignment/difference pipeline if it works more reliably for the particular component and lighting conditions. It should not masquerade as a trained general-purpose defect detector.
- SPC with baseline data, model/version-scoped score distributions, and warning rules such as one-sided CUSUM or a tested sequence rule. Detect *suspected drift*; don't assert future defects were predicted without validation.

The source proposed illustrative defaults (Laplacian variance 80, glare ratio 12%, minimum 320×240 resolution; sample counts of 20–50 normal parts; five increasing anomaly scores; thresholds at 0.72 for seal SSIM). **These are provisional calibration starting points, not universal acceptance criteria.** Validate and adjust for chosen hardware/data. Explicitly handle cold starts, low variance, score normalization, sensitivity to camera position, and false alarms. Do not fabricate a probability scale from a raw score.

### C. 4M change-point policy and containment interlock

Record the four categories of change, planned/unplanned status, actor, reason, timestamp, affected lines/lots, and resolution. On a relevant event, enforce appropriate containment: added inspection obligations, affected inventory holds, and controlled resumption after required checks and authorized approval.

The previous draft suggested normal sampling (illustratively 10%), temporarily inspecting the first `N=5` pieces fully, retroactively affecting recent unshipped inventory (illustratively 30 minutes), and optionally tightening thresholds by 15%. **Do not hard-code these as OEM policies.** Replace with configurable, explainable rules and explicitly model which units need inspection.

Resolve this contradiction from the source: “No passport until the first N pass” versus “passports may be minted and tagged during containment.” Choose an unambiguous state machine: for example, completed inspected items can acquire quality evidence but remain **dispatch-ineligible** until lot containment is cleared, or they may be released individually through a documented controlled process. The release policy must be consistently enforced everywhere, including offline flows.

### D. Signed digital quality identities and physical packaging

Issue signed, verifiable digital quality passports **only when the applicable inspection and policy conditions are satisfied**. Bind identity, lot/part, relevant genealogy, inspection digest, issuance time, version, and key identifier into an immutable signed payload. Design a clean status lifecycle (e.g., issued, active, held, revoked, consumed where appropriate). Avoid ambiguous meanings for `PASS`, `ACTIVE`, and `DISPATCH_ELIGIBLE`.

Prefer Ed25519 or an equivalent widely reviewed signature scheme, deterministic serialization, versioned keys, key rotation provisions, secure private-key handling, duplicate/replay defenses, and verification that online identities actually exist. QR token format is your choice; the draft's compact `AQ1.<payload>.<signature>` is only a reference.

Support grouping individually identified units or genuinely quantity-based items into packages, binding packages to orders, and keeping counts consistent. If quality passports represent single pieces, enforce that. If they represent sampled lots or bulk quantities, **do not claim that each unit was individually inspected**. Prefer a truthful model over a simplistic but misleading one-to-one mapping.

### E. Vision-based physical seal verification

Allow enrollment of a **real reference photograph** of a patterned paper/tape seal across a package opening and a later physical recheck. A proposed method is ROI alignment + ORB feature matches + RANSAC homography + SSIM or other similarity metrics. Store source evidence and verification results. Aim to distinguish an intact seal from an actually changed/torn/repositioned seal using live images or a documented reproducible photo test set.

Treat outcomes as `INTACT`, `SUSPICIOUS/TAMPERED`, or `UNVERIFIABLE`, as warranted by evidence. Poor illumination, motion blur, weak texture, partial occlusion, and bad registration **must not be classified as confirmed tampering or intact** without adequate support. Report uncertainty and require recheck or manual escalation when needed. Visual similarity alone is not definitive proof that a package was never opened.

### F. Dispatch gate and exception enforcement

Make load/dispatch decisions depend on the **whole chain** of identities and authorizations. Typical gates include:

1. Signed identity/QR integrity and trusted key identity.
2. Passport/package existence, current lifecycle, 4M clearance, and quarantine/revocation status.
3. Correct order, component, destination context, and allowed package association.
4. No duplicate accepted load or replay; idempotent retry handling.
5. Appropriate checkpoint/sequence checks; flag suspicious or impossible movement using configurable site/time policy.
6. Recorded or newly captured seal verification and explicit `UNVERIFIABLE` behavior.
7. Consistent quantity reconciliation and manifest closure rules.

Perform concurrent decisions transactionally with uniqueness/locking protections. Persist both accepted and blocked attempts and their reasons. Ensure a quarantine prevents new authorization immediately on an online scanner.

### G. Offline verification, queued scans, and reconciliation

Provide an offline-capable smartphone/PWA workflow **if achievable reliably**. Locally validate signatures using cached trusted public keys, order scope, and a signed/versioned policy/revocation snapshot. Persist attempted scans in durable local storage with monotonically ordered identifiers or device-local counters. Sync on reconnection with idempotency and conflict handling.

Consider a per-device hash chain and server checkpointing for tamper evidence, but understand that hashing alone does not authenticate the scanner or prevent deletion/reconstruction of unsynced data. Bind events to authenticated operators/devices and protect the chain head or sequence where practicable.

**Critical limitation:** Cached revocations can be stale, so offline signature success **does not mean current server authorization**. Decide a clear mode such as `PROVISIONAL_OFFLINE` with shipment completion blocked until sync, or restrict offline operation to a sufficiently short-lived, server-issued authorization manifest with an explicit risk policy. Do not claim offline scanners can always detect cross-device duplicates or new recalls immediately.

Browser camera access/PWA behavior on phones often requires a secure context (HTTPS or properly configured local trust). Plan the real mobile demo accordingly; an insecure LAN HTTP origin may not permit camera or Service Worker APIs. Use independently verified crypto support rather than assuming a particular library or workaround guarantees platform compatibility.

### H. Traceability, recall blast radius, and quarantine

From a heat code, supplier lot, individual identity, or package, compute an **accurate lineage** of affected inspections, passports, packages, orders, checkpoints, and consignees. Distinguish inventory still at the warehouse, in transit, and already delivered based on **actual modeled shipment events**, not guessed status from a package flag.

Support atomic quarantine/hold/revocation of eligible descendants, audit actor and reason, and show what happens to already dispatched or delivered stock (which cannot be physically recalled or retroactively “blocked at loading”). Protect against active loading races. State the precise number of affected units/packages and the evidence used to calculate it.

A one-click workflow is desirable, but authorization, transaction boundaries, and correct lineage matter more than a dramatic animation. The source's “under 200 ms” target is aspirational, **not a promised benchmark**; measure performance on stated test data if you claim one.

### I. Evidence-based corrective-action drafts (8D/SCAR)

Generate a structured, downloadable corrective-action **draft** using inspection evidence, 4M events, relevant lots, exception history, and blast-radius counts. Useful sections: D1 team, D2 problem, D3 interim containment, D4 root-cause hypothesis, D5 proposed corrective action, D6 effectiveness verification, D7 recurrence prevention, D8 closure/recognition. Clearly separate known facts, hypotheses, proposed actions, and fields awaiting human completion. Preserve evidence references.

You may use an LLM/VLM for narrative assistance when credentials are available, with a deterministic local template fallback. No LLM should invent an identified root cause, completed action, or approval. This must be a **draft**, not a claim of IATF certification or a finalized compliant supplier report without human review.

### J. Optional aftermarket genuine-parts lifecycle

If time allows after core functionality, add separate B2B logistics tracking and a concealed, single-use inside-box token that marks a part/box consumed at installation. Reuse of that token should raise an exception. Understand this detects **token reuse**, not every cloned authentic box or counterfeit part. Strong guarantees may require tamper-evident packaging, physical binding, and distribution controls outside software.

A calibrated Copy Detection Pattern (CDP) print/scan classifier is also an optional experiment, **not a mandatory live dependency**, due to printer, phone-camera, and lighting sensitivity. Prefer evidence-backed claims from a controlled test over overpromising clone detection.

## 5. Reference architecture — ideas, not instructions

A prior draft proposed a four-layer decoupled system:

- **Web application + mobile scanning experience:** Next.js/React and TypeScript, optionally PWA + IndexedDB, as suited to the repo and phone deployment.
- **API, domain services, auth, and deterministic policy engine:** Python/FastAPI, or another choice you can justify and implement well.
- **Image analysis:** Python, OpenCV, optional PyTorch/anomalib/PatchCore, optionally ONNX Runtime for inference.
- **Persistent data/evidence:** PostgreSQL with migrations, relational lineage joins/CTEs, filesystem or S3-compatible evidence storage, immutable audit history and digests.
- **Report drafting:** local templates and PDF output, with an optional pluggable LLM/VLM.
- **Local deployment:** Docker Compose or an equally reproducible minimal setup.

**You may change every part of this.** Do not choose a technology merely because it appears in the old draft, and do not split services prematurely if a modular monolith is faster and easier to test. Keep signing keys server-side; enforce policies in one authoritative layer; use versioned API/data contracts; ensure evidence cannot be silently overwritten.

The earlier proposal listed roughly 13 tables: `users`, `part_master`, `raw_material_heats`, `supplier_lots`, `four_m_events`, `inspections`, `quality_passports`, `packages`, `package_items`, `dispatch_orders`, `scan_events`, `alerts`, and `scar_reports`. Treat this as a **domain checklist**, not a mandatory schema. Add supplier, station, identity, shipment-line, order-item, audit, containment, or ownership entities when needed. You may collapse concepts when this improves correctness. Explicitly model:

- cardinality of **inspection ↔ unit/passport ↔ lot ↔ package ↔ order**;
- quantity, part compatibility, and mixed-part/mixed-lot packages if allowed;
- what makes a unit eligible to pack or dispatch;
- exact genealogy edges used by recall queries;
- status transitions and revocation semantics;
- provenance, model version, signed payload version, and timestamp;
- immutable evidence and audit events; and
- offline reconciliation conflicts and delivery-state evidence.

Do not copy the original schema's missing package-to-dispatch association or infer in-transit/delivered state without explicit movement/fulfillment events.

## 6. Key security and operational decisions to make

Before implementing these workflows, decide and document concise rules for:

| Decision area | Questions to resolve |
| --- | --- |
| **Identity and roles** | Who may inspect, approve a HOLD, revoke, trigger 4M containment, pack, load, and quarantine? How are operator identity and device identity authenticated? |
| **Quality acceptance** | Is a verdict per item or per lot? What does an uncalibrated model do? Does a manager override authorize release or only permit reinspection? |
| **4M state machine** | Which inventory is affected by planned versus unplanned changes? How does containment clear? What if a second event occurs during containment? |
| **Signing and lifecycle** | Exactly when is a passport signed? What content is immutable? Can a signed passport be valid but dispatch-ineligible? How do keys and revocation snapshots expire? |
| **Packaging and seal** | Does a seal match alone permit loading? How is a failed/unverifiable recheck reviewed? Can a package be resealed and re-enrolled, and by whom? |
| **Dispatch concurrency** | How are duplicate simultaneous scans, retries, order quantity, cross-device events, and manifest closure serialized? |
| **Offline operations** | Which local actions are provisional? What is the maximum snapshot age? What happens to a physically loaded crate when a queued scan is rejected? |
| **Recall** | What is affected by a heat-code hold? Which shipments can be blocked versus only marked for notification/recall? How is reconciliation handled? |
| **Evidence and privacy** | How are images/certificates retained, access-controlled, and deleted or archived under configurable policies? |

Use strong defaults with explicit error states; avoid implementing fake compliance, “blockchain-grade” guarantees, or an all-powerful admin bypass.

## 7. Priorities and realistic delivery strategy

**P0 — The reliable integrated backbone (do first).**

Build secure identities/roles as appropriate, part/heat/lot setup, deterministic inspection & review, quality identity issuance, package creation, dispatch policy checks, evidence/audit events, and heat/lot quarantine with a correct affected-inventory query. This is the narrative spine and must work end-to-end. A straightforward, trustworthy inspection approach can come before ambitious ML.

**P1 — Distinctive working capabilities.**

Add and demonstrate 4M containment, physical seal reference/recheck using real image comparison, meaningful continuous inspection trend rules, robust offline verification/reconciliation, and actionable corrective-action PDF drafts. Reorder according to what the actual codebase/hardware can support. Ship each as a complete working slice rather than five partially integrated tabs.

**P2 — Experiments or extensions.**

PatchCore/ONNX optimization beyond a reliable baseline, advanced movement anomaly detection, MGP single-use inside-box verification, print-copy detection/CDP, richer model assessment, scale optimizations, and optional report-generating VLM augmentation. Promote a P2 idea only if it measurably improves a genuine demo and does not destabilize P0/P1.

**Your judgment supersedes these priorities** when there is a better demonstrable strategy. Record changes in a short architecture/decision log.

## 8. Acceptance scenarios — prove behavior, not just build output

Provide reproducible automated and/or hands-on verification for these minimum journeys. Adjust details to the actual system architecture but preserve the underlying guarantees.

1. **Normal flow:** Create a part, material heat and lot; inspect a suitable image; obtain a permitted quality identity; associate it with a package and an order; load it once; close a correctly reconciled manifest; trace it back to the original heat and evidence.
2. **Bad evidence / failed inspection:** Blurred/unverifiable image or failed analysis produces `HOLD` rather than `PASS`; an actual reject produces no authorized shipment identity.
3. **4M containment:** Log an unplanned tool/operator/material/method change; prove release obligations change and affected current inventory is blocked or marked for recheck; demonstrate a documented authorized path to recovery.
4. **Seal check:** Enroll a real seal photo and test a clean recapture, a physically altered/torn seal, and an ambiguous/bad capture. Each outcome and its impact on loading should be explicit.
5. **Dispatch hard stops:** Forged/modified QR or invalid signature; unknown/revoked/quarantined identity; wrong order/part; duplicate scan; unverified or suspect seal; over-quantity; and a forbidden role/action must be prevented for the correct reason.
6. **Recall:** Quarantine a known heat/lot containing multiple descendants; verify exactly which packages are blocked, which shipments are in transit or delivered, and which audit events were written. A new online scan of a held package must fail.
7. **Offline:** Verify a legitimate signed identity with current local material, queue at least one event without connectivity, reconnect, and prove idempotent synchronization. Also demonstrate stale revocation/cross-device conflict handling or explicitly explain why offline shipment remains provisional.
8. **SPC:** With a reproducible ordered sequence of real scores or clearly labeled fixture scores, show stable baseline behavior and a drift alert under documented criteria, including version-specific calibration assumptions.
9. **Report:** Generate an evidence-linked 8D/SCAR draft, marking unverified root causes and incomplete D-steps as pending rather than completed.
10. **Permissions and race conditions:** Unauthorized reviews/quarantines fail; concurrent loading attempts cannot both count the same package; rejected replay cannot increase order quantity.

Use integration tests with a real test database where appropriate, not only mocked unit tests; use fixture images for reproducible CV verification. Include at least one realistic browser-driven end-to-end path if the runtime allows. If camera/hardware tests are unavailable, state the gap clearly and provide an equivalent offline fixture-based exercise.

## 9. Demo narrative and presentation constraints

Target an understandable **approximately 90-second central demonstration**, with deeper features available for Q&A. An illustrative sequence:

- Create/show a supplier lot linked to a material heat.
- Inspect a component and retain visual evidence; show an actual decision and identity.
- Trigger a realistic unplanned 4M event; show that unauthorized dispatch/release is stopped by policy.
- Demonstrate a package seal recheck or a correctly blocked loading scan.
- Quarantine the heat/lot; show affected packages and destinations and prove the gate denies another scan.
- Open or export an evidence-backed corrective-action draft.

If live CV or ML depends on uncontrollable lighting, use a **real, pre-recorded test fixture disclosed as such** and retain an optional webcam path. Do not fake live inference. Offline verification may be a separate Q&A/demo scenario. Optimize for clarity, correctness, and reliability, not a feature-count race.

## 10. What you should deliver

At the end of implementation, provide:

- **Running source code** integrated with the existing repo or a clear new project structure.
- **Working setup** (`README`, environment example, reproducible run and test commands, migrations/seeds as applicable).
- **A product you designed** with your own UX/UI decisions; not a mechanically reproduced layout from prior specs.
- **Actual functional flows** across API, business rules, persistence, camera/photo inputs where feasible, and user experience.
- **Automated tests** and evidence of which commands/flows passed; disclose limitations and skipped tests.
- **A concise architecture/decision record** listing meaningful departures from this brief and why they improve the product.
- **A demo guide** with realistic seed data, test images, role logins/credentials for development only, and the intended walkthrough.
- **A capability matrix** tagging each major feature `implemented + verified`, `implemented but unverified`, `demo fixture/simulation`, or `not implemented`.
- **A prioritized next-steps list**, only after completing as much working code as possible.

### Execution instruction

**Proceed to implementation.** Do not respond with only a proposed roadmap and stop. Inspect the repository; make a short internal architecture/priority decision; then build, test, fix, and integrate the highest-value functional slices possible in the available session. If tool/runtime constraints prevent a part, state precisely what is blocked and deliver the rest. Use the design and engineering freedom granted above to improve the product beyond these notes.

---

## Appendix — Source ideas worth considering, but not blindly copying

The uploaded source notes proposed: OpenCV seal matching with ORB/RANSAC/SSIM; PatchCore plus a golden-template fallback; SPC/CUSUM using continuous anomaly scores; 4M hold with first-N controlled checks; Ed25519 QR passports; local scanner verification plus queued hash-chain events; material-heat lineage and atomic quarantine; structured LLM-assisted 8D/SCAR drafts; optional MGP inside-box single-use tokens; optional CDP microprint detection.

Those notes also contained **untested performance claims, arbitrary numeric defaults, exact code/module paths, and prescriptive UI details**. None are requirements. Evaluate each idea for actual utility, scientific/operational validity, implementation cost, and demo risk. **Your responsibility is to make AutoQ-Trace better, not to implement a questionable proposal verbatim.**
