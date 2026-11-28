# Independent PR #45 acceptance

Candidate `5889ea90e2d09002f11b3b8e5be2a8757b173cbc`, tree `c9191c05f41fd759a561a3decba7179aa025657c`, based on `8e8a60c`. Decision #35 contains the seven originally reported corrections plus the legacy drain and floor changes. Use the [portable setup](README.md) with this candidate SHA.

```sh
python3 "$RUN" --checkout ../autoq-candidate --candidate "$CANDIDATE" --group backend \
  --case 'not r2 and not (settlement_and_close or recall_and_departure or recall_fixture)'

python3 "$RUN" --checkout ../autoq-candidate --candidate "$CANDIDATE" --group backend --case r2
```

The second command contains assertions expected to fail on this candidate. Fixtures create their own scans, snapshots, PDFs and tasks. The test runner verifies the candidate and disposable database marker before running. The timing hooks only control transaction order in this environment.

## Seven corrections

| Correction | Result | Independent evidence |
| --- | --- | --- |
| Reopen after crate removal and recheck manifest at departure | PASS | Removing the full load forced an open manifest and zero departure movements. |
| Reject stale seal rechecks | PASS | Departure returned 409. Fresh synthetic rechecks on the loaded crates then allowed one departure with a manifest digest. |
| Bind recovery evidence to a physical reconciliation | FAIL | Fresh synthetic input was refused. The two-step route still accepted unrelated QE and QM supplier uploads and allowed one digest to settle two simultaneous recoveries. |
| Preserve pending recovery during refresh | PASS | The previous authorization remained outstanding. |
| Separate omitted and explicit-null v2 snapshots | PASS | Omission did not advance the chain. Explicit null advanced the valid ONLINE entry without creating a load. |
| Persist only correctly scoped snapshot associations | PASS | A wrong-shipment claim remained in the submitted field and did not become a validated reference. |
| Bind SPC alert to its own warning and inspection | PASS | An unrelated alert was refused. |

The completed two-step API test accepted a synthetic recount document submitted through the fixture's generic upload route, completed the task, allowed close, and rejected sequential reuse on the other shipment. The concurrent reuse test returned two HTTP 200 responses and persisted two settlements. This PR should stay unmerged as a release-blocker fix until recovery binding and exact historical replay are corrected. Keep physical acceptance on hold.

## Recovery evidence

`offline_scanner/service.py` lines 205–280 requires the recovery request before accepting evidence and preserves the pending task. The evidence filter checks source, stored time, uploader's current role and existing citation. The upload at `auth_evidence/service.py` lines 37–48 records no shipment, recovery task or snapshot. The server adds `for_snapshot` at settlement. Those fields establish who uploaded bytes and when the server stored them. They cannot establish what was counted, which truck or staging lane it concerned, or that a qualifying QE/QM document was made for that recovery.

Tests cover a fresh synthetic record attributed to a QE, fresh unrelated supplier uploads by a QE and QM, a document for the other shipment, a legitimate two-step submission, sequential reuse and simultaneous reuse on different devices and shipments. The wrong-shipment test independently confirms that the document was stored after both requests. On `5889ea9` the wrong shipment settled with HTTP 200. The positive test labels the generated recount with the correct scenario identifiers and checks the API path. The current generic upload endpoint has no structured binding for the test to supply.

Bind each evidence attachment to an already-pending reconciliation task and to the task's snapshot and shipment. Record who attested to the disposition and the scope they counted. Validate that binding and consume the attachment in the same transaction as task completion and terminal settlement. Enforce globally exclusive record consumption, for example by a claim with a unique digest key, including when transactions lock different shipments. Preserve the separate QE/QM task-completion path with attributable physical disposition. A rejected attachment must leave the task open and the shipment blocked.

## Four Opus issues

| Issue | Exact path on 5889ea9 and failure scenario | Release invariant and focused regression |
| --- | --- | --- |
| Legacy-drain authority | `offline_scanner/service.py:406–417,447–484`. Before R2, a first-time v1 provisional capture could load at writer 2/floor 1. #45 adds the active writer to the shared policy-lock check. | First-time below-writer provisional capture is a conflict with one task and zero newly authorized loads. A replay of an acceptance already committed in v1 retains the original scan and decision. Run `legacy_drain_while_writer` and `writer_switch_keeps`. |
| Version-floor downgrade policy | `policy/policies.py:82–97`. An ordinary lower-floor publication used to re-admit old writers. #45 reads the active floor under the exclusive policy lock and returns `POLICY_FLOOR_DOWNGRADE`. | Normal publishing cannot lower the plant floor or split a concurrent sync decision. Run `policy_floor_cannot` and `sync_first_policy`. Recover service with compatible readers and preserved history. A routine policy edit cannot act as a rollback. |
| Concurrent event provenance | `offline_scanner/service.py:464–484` pre-reads the scan and ignores the returned `created` flag. `dispatch/service.py:263–267,282–286` treats the same shipment/token fingerprint as a duplicate even if actor/device/source differ. An overlapping scan can be adopted under the wrong provenance. A provisional capture with surrendered authority can be reported as accepted with no conflict task even though the unique load belongs to the online scan. | Determine event ownership atomically through either duplicate path. Only the matching original online scan may reconcile an ONLINE event. Keep a competing scan's load and identity intact. Return the mismatch and one durable task where physical staging requires it. Run `competing_online_scan` and `online_cannot_adopt` to exercise both orders. |
| In-transit instruction | `offline_scanner/service.py:430–443,486–493` creates a dock segregation instruction regardless of shipment state. `_replay_result` at 311–318 repeats the same instruction. If departure commits first, the dock cannot act on the departed truck. | Under lock, issue an instruction for the current recorded location. While at plant, segregate at the dock. In transit or delivered, direct carrier/consignee follow-up appropriate to the outcome. Keep one durable task and replay the same physical disposition instruction. Run `late_conflict_after_departure`. |

## Compatibility and follow-up

- `offline_scanner/router.py:58–69` and `offline_scanner/service.py:322–363` correctly distinguish new v1 explicit null and absence. Before R2, v1 accepted explicit null plus a batch snapshot and persisted the effective batch claim. The replay case `replay_preserves_pre_r2_v1_effective_batch_claim` creates that historical row shape in an isolated database, then resubmits the original wire form. Corrected readers must preserve retries of synchronized history, and must never use the fallback to authorize a new capture. The test does not assert that the retained Vivo has such an event.
- A second lost response can hide an independently recorded online decision. `_replay_result` returns the device-event-level `DUPLICATE` as the original result, whereas first reconciliation reports the linked online scan's ACCEPTED or BLOCKED decision. Run `losing_both_online_and_first_sync_replies`. Keep the linked scan and authoritative original decision across all retries.
- Source-review items pending runtime verification include strict raw JSON typing, legacy browser handling of explicit v1 null, and the recovery modal retry after the first request commits but its upload fails. Those checks require separate input or browser coverage.

The published baseline selection passed 12 tests on disposable PostgreSQL 16. Focused follow-ups confirmed wrong-shipment settlement, cross-shipment double consumption, both competing scan orders, the repeated dock instruction after departure, the pre-R2 v1 replay 409 and loss of the original online result on replay. A v1 acceptance recorded before the writer switch replayed successfully when its submitted claim matched the stored value. This review exercised the APIs on Windows. The PR #45 recovery UI and macOS execution remain unverified.

The bundle is opt-in. Opus owns permanent integration test placement. It contains no physical evidence or production data.
