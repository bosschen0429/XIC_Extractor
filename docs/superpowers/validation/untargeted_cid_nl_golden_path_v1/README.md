# Untargeted CID-NL Golden Path v1

Status: `production_candidate`; product authority:
`none_until_checked_manifest`.

This retained packet closes Issue #175 with one no-RAW, in-memory contract
fixture. It proves that the existing owners can carry an Untargeted CID-NL
method from Discovery evidence through method assessment, peak/cell/group
identity, conservative Backfill, a checked `ProductionAcceptanceManifest`, and
the existing `QuantMatrixVersion` owner. It does not activate a current lane,
change a default, add a writer, or change a public output schema.

## Frozen contract result

- Activation base: `5f3ca8ace68b2205c7bf96dd62b08aef7809e79e`.
- Method subject: workflow-neutral `cid_nl` subject; the Untargeted path does
  not fabricate a Target or Targeted definition identity.
- Sample universe: `N=6` eligible Study/QC samples; Blank and MixSTD are
  excluded; `K=max(2,floor(0.05*N))=2`; direct SUPPORT count `S=2`.
- Direct SUPPORT values: Study `660.0`, QC `1320.0`.
- Accepted Backfill value: one missing Study sample `1980.0`.
- Nonnumeric cells: shared-split HOLD, method REJECT, technical failure, Blank,
  and MixSTD. No unresolved state is rendered as numeric zero.
- Terminal matrix: one group row with direct values retaining `detected`
  provenance and the recovered value retaining `accepted_backfill`
  provenance.
- One qualifying, uniquely linked NL116 scan is sufficient for method SUPPORT.
  A valid peak without qualifying NL116 is HOLD. Valid method evidence without
  a usable MS1 peak is terminal REJECT and is not Backfill-eligible.

## Retained lineage

The source artifact binds canonical bytes and hashes for the processing
request/attempt, complete method subject, group and owner identities, Alignment
configuration, `N/K/S`, eligible/support sample identities, complete SUPPORT
trace artifact identities, per-sample assessment/cell/evidence/Discovery
projection identities, Backfill disposition and reasons, recovery
universe/assessment identity, matched peak, value kind, and raw integrated
area. Reordering either the sample inputs or a sample's order-neutral Discovery
projection refs produces the same source and terminal matrix.

The implementation reuses the existing evidence spine, conservative Backfill,
manifest checker, and Quant Matrix version owner. No Package 3B quarry hunk was
cherry-picked because the required owners already existed; the salvage result
is `not_needed_existing_owners`.

## Root boundary closures

- Alignment uses an explicit one-event anchor minimum for this golden method,
  matching the approved one-scan SUPPORT rule.
- Method REJECT is terminal `not_applicable`, not silently converted into a
  recoverable HOLD.
- Targeted compatibility cells retain their exact Targeted IDs; Untargeted
  cells retain none.
- Discovery projection lineage remains complete through the terminal source
  artifact; each sample's projection set has one canonical owner ordering.
- The prepared Matrix/acceptance/expected-diff projection is minted only by the
  golden-path orchestration owner; caller-side dataclass replacement cannot
  rewrite its writer inputs.
- Untargeted method-subject request binding compares exact canonical JSON, so
  JSON booleans cannot alias numeric precursor, tolerance, or neutral-loss
  fields.
- `production_acceptance` is the single manifest authority owner. It reads and
  validates one exact byte snapshot, case-folds both the manifest spelling and
  resolved in-repo target for Windows-compatible forbidden-source
  classification, and mints deeply immutable checked rows. The terminal and
  file-backed QuantMatrix adapter consume that receipt rather than reopening the
  manifest.
- `QuantMatrixVersionRows` are deeply immutable after projection. Source-summary
  hashes bind the exact matrix, identity, manifest, and expected-diff snapshots
  that were consumed, not a later path reread.
- Historical M5 replay now separates its evaluated candidate revision from its
  reviewed-evidence revision, so later source evolution cannot rewrite or
  invalidate the frozen packet while all tamper gates remain fail closed.

## Validation

RAW decision: `rerun_needed=false`. M6 changes orchestration and contract
binding only; it does not change the RAW reader, peak-detection science,
product value algorithm, or an active/default lane. Existing RAW artifacts
cannot answer this source-to-terminal contract, so the method-appropriate gate
is the deterministic no-RAW fixture plus downstream authority and hard-zero
tests. The resulting authority ceiling remains `production_candidate`.

Observed on this checkout:

- focused manifest/golden-path authority suites: 43 passed;
- downstream activation, consumer, and M5 regression suites: 125 passed;
- `docs-config`: 728 passed, 1 skipped;
- `gui`: 110 passed;
- `targeted-core`: 1,257 passed, 1 skipped;
- `alignment-core`: 2,553 passed;
- `product-gates-activation`: 894 passed, 1 skipped;
- `product-gates-evidence`: 702 passed;
- `diagnostics-tools`: 356 passed, 1 skipped;
- Ruff, mypy (493 source files), import-linter (4 contracts), architecture
  census, bootstrap offline check, diagnostics index, and docs audit passed.

No productization control-plane tier or active-lane update is required. This
packet hardens enforcement of the existing manifest-only Matrix authority, but
grants or revokes no authority and changes no maturity registration, writer
scope, current accepted count, selected area/counting rule, default, or public
schema. No branch handoff is updated because this checkout has no active
branch-handoff file under `docs/superpowers/handoffs/current/`; this retained
packet is the durable M6 evidence owner, not a WIP snapshot.
