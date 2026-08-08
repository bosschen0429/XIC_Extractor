# Issue #195 owner-valid early-loser shadow evidence

Validation status: `diagnostic_only`.

Child ticket: [#208](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/208).
Programme decision: [#195](https://github.com/Chao-hu-Lab/XIC_Extractor/issues/195).

## Delivered boundary

`run_alignment --emit-early-loser-shadow` constructs a proposal from the live,
typed `OwnerAlignedFeature` population before Backfill. It reuses the existing
pre-Backfill consolidation owner and owner Backfill request-plan owner. The
unchanged source owner-feature tuple continues through Backfill. After the
existing claim registry and primary consolidation finish, the shadow compares
the proposal with the typed final Matrix and emits one optional atomic sidecar:

`alignment_early_loser_shadow.json`

Schema: `alignment_early_loser_shadow_v1`.

The report is not accepted as input by Backfill, Pass B, Matrix, ProductWriter,
activation, or any scientific selector.
It requires `full-audit` scope and rejects simultaneous activated
preconsolidation.

## Expected-diff packet

Every report contains:

- deterministic survivor, loser, and owner-unresolved proposal rows;
- source stage, direct winner, terminal winner, and complete proposal lineage;
- final consolidation state, direct/terminal winner, complete final lineage,
  comparison status, and reason for every source group;
- baseline/proposal requested-feature, sample-cell, and logical XIC-request
  workload counts from the existing request-plan owner;
- observed shadow diff rows for survivors/losers, winner identity, selected
  observations, missing/extra cells, status/reason, areas, Matrix identity,
  Matrix value, review population, and rollback; and
- explicit counterfactual availability and activation-gap reasons.

The sidecar also binds its timing `run_id`, canonical typed source-owner
snapshot, `AlignmentConfig`, and complete final comparison projection with
SHA-256 digests. It separately binds the sample order and available RAW
sample-stem request universe. The binding deliberately excludes Matrix
cells/values; the JSON is a diagnostic projection, not a rehydratable
scientific authority.

Observed product/Matrix diff is hard-zero by control flow: no alternate Matrix
is created, and the same final Matrix object reaches the existing writer path.
The report does not claim that an activated skip would be zero-diff. Selected
observations, missing/extra cells, status/reason, areas, Matrix identity/value,
and review population for that counterfactual remain unavailable and block
activation.

## Lineage and negative oracles

The focused synthetic owner fixture proves a legal two-stage chain:

`GROUP-LOSER → GROUP-MIDDLE → GROUP-WINNER`

The pre-Backfill proposal retains `GROUP-LOSER → GROUP-MIDDLE`; final authority
may then settle `GROUP-MIDDLE → GROUP-WINNER`. The comparator preserves both
stages and compares terminal winners instead of requiring direct-winner
normalization. Missing winner references, cycles, loser-to-non-winner
termination, false losers, terminal-winner mismatches, owner-unresolved rows,
and input reordering have separate deterministic tests.

## Evidence ceiling

No RAW files were opened and no 8RAW/85RAW pipeline was launched. The retained
Issue #195 artifact omits the complete typed pre-Backfill owner population, so
it cannot generate an owner-valid real-population shadow packet. Display IDs,
rounded coordinates, final persisted rows, and the PR #207 request proxy were
not used as substitutes.

The workload delta is therefore a projected count over current owner request
items. It is not elapsed-time, RAW-call, chromatogram-call, trace-point, or
monetary savings. A future real-population run requires separate RAW authority
and the `xic-raw-validation` gate.

## Reproduction

```powershell
.venv\Scripts\python.exe -m pytest -q `
  tests\test_early_loser_shadow.py `
  tests\test_alignment_pipeline_backends.py `
  tests\test_run_alignment.py
```

The full PR verification also runs relevant claim-registry, primary-
consolidation, owner-Backfill, Ruff, mypy, import-linter, architecture, docs,
diff, and secret/private-path gates. Exact final counts and the checked commit
SHA belong in the PR closeout.

## Verdict

- Implementation/shadow verdict: `GO` — typed proposal, workload projection,
  full transitive lineage, final-authority comparison, and no-write oracle are
  coherent, with positive projected requested-feature, cell, and logical-XIC
  reductions.
- Activation verdict: `REDESIGN / blocked` — the counterfactual selected-cell,
  area, Matrix, and review-population evidence is unavailable without a
  separately authorized execution path.
- Automatic `KILL`: any false loser, winner mismatch, missing/cyclic lineage,
  non-winner terminal, non-positive projected workload reduction, or nonzero
  scientific/output diff.

Rollback is removal or disabling of the opt-in sidecar. The normal Backfill,
final settlement, Matrix, and writer paths remain unchanged.
