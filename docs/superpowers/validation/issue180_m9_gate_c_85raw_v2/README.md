# Issue 180 M9 Gate C v2 audit packet

Status: `gate_ok` for the bounded opt-in CID-NL ModeTrack backfill transaction.
Persisted validation status remains `diagnostic_only`; the evidence ceiling is
`production_candidate`. This packet does not enable a default writer, change
M1, or update structured product status.

## Outcome

- C1 producer: `552f7d980ae63d870d6906489744f22a2210d0f3`
- C2 producer: `f26b8f075eb1f6bd0a5424c47cc1350a2508dc14`
- Sample universe: the same exact 85 RAW identities, metadata SHA-256
  `e2f98a6c59ac462030f9d0cda17d67d2e7676450292fc3659de57156fc28a21a`,
  and DLL tree SHA-256
  `28614de3a6a1572b8447fa877b7134e35b1decddef6313e3466f17438f7ac409`
- C1 census: 11 ModeTrack groups, 64 exact-owner candidates, 26 proposed,
  38 rejected, and 26 pending expected writes.
- C1/C2 stable scientific projection SHA-256:
  `89b572f807e8444cab72f51a9bb61f51a3c30d85bd97931c29e39510880208ad`
- Per run: 85 Discovery RAW opens + 83 M7 RAW opens = 168; 79 M7 XIC
  batches. Transaction total: 336 RAW opens, below the frozen 340 ceiling.
- Terminal result: 26 expected writes, 26 written, zero unused expected diff,
  zero unrelated changed cells, and rollback `baseline_exact`.
- Independent acceptance review: `run_ok(C1)=true`, `run_ok(C2)=true`,
  `gate_ok=true`, `rerun_needed=false`.

The `rejected -> proposed` attack is closed by a live owner oracle, not by
persisted digest consistency. C2 reran Discovery -> M7 -> M8 -> M2, passed the
fresh typed M2 facts directly to the finalizer in the same process, compared
the complete stable projection and freshly derived decision keys, and only
then loaded review, checked manifest, and the existing QMV owner.

## Review transfer

The historical v1 manual-review packet was revalidated under its own contract.
Its 26 `(group, sample, sample identity, observation content identity, raw
area)` subjects exactly matched C1 v2. The transfer preserved the human
reviewer, time, verdict, notes, and evidence locator; it only rebound the
unchanged subjects to the C1 v2 proposal. The transfer receipt is itself frozen
in the C2 launch manifest and terminal transaction chain.

## Retention boundary

This directory retains exact byte copies of the compact proposal, review,
fresh-owner, QMV, rollback, and terminal receipts (798,547 bytes total). The
699 MB Discovery/runtime working set remains under ignored task output and is
not committed. Launch manifests are also omitted because they contain private
machine paths; their exact producing hashes are:

- C1 launch manifest:
  `2885a7762a24606b2fd5c0468f5859baa5974e2ee57fe70ff93255a8f9ae2a52`
- C2 launch manifest:
  `804c08e496f529035079271f7359fa2889a91a5f7a20dbbd27ce67a11e1816b1`
- C1 runtime marker:
  `216c8aee038839667f5d3dce593f39e3497c7b4db1ec1265ce1670225ac58c74`
- C2 runtime marker:
  `ded65a3dc68c6dfc149612af559e8a671c4756fbfe99ffedf0a9783833472d2e`

The copied JSON retains its original task-output relative paths so its bytes
and hashes remain evidence-exact. Therefore this retained directory is an
audit packet, not a clean-checkout finalization input and not a serialized M2
authority. Reproduction requires a new named validation transaction; the M9
attempt budget forbids an automatic third 85RAW run.

Primary terminal locators:

- `c1_proposal/proposal_receipt.json`
- `review_transfer/review_transfer_receipt.json`
- `c2_replay/fresh_owner_receipt.json`
- `c2_final/final_receipt.json`
- `c2_final/transaction_receipt.json`
