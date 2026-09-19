# Issue 180 M9 Gate C closed-writer v3 audit packet

Status: `gate_ok` for the bounded, opt-in CID-NL ModeTrack C2 finalization
transaction at commit `975c5ab9afc9f9a29f82566c35ef765328d07b57`.
Persisted validation status remains `diagnostic_only`; the evidence ceiling is
`production_candidate`. This packet does not enable a default writer, change
M1, or grant preset/product authority.

## Outcome

- Independent post-run adjudication: `run_ok=true`, `gate_ok=true`, and
  `rerun_needed=false`.
- The frozen launch used the worktree `.venv` from a clean checkout and the
  exact 85-sample metadata SHA-256
  `e2f98a6c59ac462030f9d0cda17d67d2e7676450292fc3659de57156fc28a21a`.
- Fresh scientific projection SHA-256:
  `89b572f807e8444cab72f51a9bb61f51a3c30d85bd97931c29e39510880208ad`.
  It exactly matches the reviewed historical v2 projection.
- Call cost: 85 Discovery RAW opens, 83 M7 RAW opens, 168 total RAW
  opens, and 79 M7 XIC batches. The frozen ceilings were 85, 85, 170,
  and 85 respectively.
- The finalizer adopted 26 cells: 10 for `FAM000004`, 11 for
  `FAM000007`, and 5 for `FAM000009`.
- QMV expected diff: 26 expected, 26 written, zero unused, status `pass`.
- Rollback status: `baseline_exact`.
- Runtime terminal state: `finalized` / `succeeded`; completed heartbeat
  elapsed time: 1943.009 seconds.

The writer received only the current in-process typed scientific projection
and the owner-minted validated review set. It derived the checked manifest,
expected diff, QMV, counts, rollback, and terminal receipts internally, then
published the final directory atomically. The final receipt records
`writer_authority=fresh_projection_plus_validated_review_only`; the transaction
receipt records `product_authority=checked_qmv_owner_only`.

The final receipt does not retain a separate unrelated-cell scalar. Before it
can be emitted, the current terminal validator checks every unauthorized
Matrix value and exact detected-only rollback. That successful terminal path,
the QMV expected-diff pass, zero unused expected diff, and `baseline_exact`
rollback are the bounded no-unrelated-diff evidence. This is not a broader
production-readiness claim.

## Review transfer

The existing 26-subject human review was revalidated against the current
validator and rebound without changing reviewer verdicts. The validated review
subject SHA-256 remained
`84a23e970a8953fbd31406dd35af4a61dfa3633e863fb1184d66b92db933795c`.
The persisted proposal, transfer receipt, checked manifest, and review files
remain audit/review inputs only; they cannot directly authorize Matrix writes.

## Retention boundary

This directory retains exact byte copies of the compact review-transfer,
fresh-owner, QMV, rollback, and terminal artifacts. The reusable historical C1
proposal remains in
`../issue180_m9_gate_c_fresh_owner/c1_proposal/`; it is not duplicated here.
The Discovery working set, private-path launch manifest, and 532 KB execution
runtime ledger remain under ignored task output.

Omitted producing-artifact hashes:

- C2 launch manifest SHA-256:
  `a1270f269e758716a1de318fa3120257077c427939af5de0afe8e720fc074fd4`
- Runtime ledger SHA-256:
  `083c530f8f51f43f1d302465ad2d66188afbc6abac014377c41eddaee06e6723`

Primary retained locators:

- `review_transfer/review_transfer_receipt.json`
- `c2_replay/fresh_mode_track_source.json`
- `c2_replay/fresh_owner_receipt.json`
- `c2_replay/timing.live.json`
- `c2_final/checked_manifest.tsv`
- `c2_final/result_qmv.json`
- `c2_final/final_receipt.json`
- `c2_final/transaction_receipt.json`
