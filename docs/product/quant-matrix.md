# Quant Matrix

The quant matrix delivers finalized feature × sample numeric values for
downstream analysis. A published area, counted detection and chemical identity
are different facts; a blank cell is not an inferred zero. The code and its
tests own the files, columns, status values and row rules; this page keeps the
boundaries and reasons. For reading and curating the result, use the
[user guide](../user/untargeted.md#整理矩陣).

## Where Each Decision Lives

| Decision | Owner | Tests |
| --- | --- | --- |
| Cell value or typed no-write | `ProductionCellDecision` | [`test_alignment_cell_decision.py`](../../tests/test_alignment_cell_decision.py) |
| Row inclusion, identity and Matrix rendering | `output.alignment` | [`test_alignment_tsv_writer.py`](../../tests/test_alignment_tsv_writer.py), [`test_untargeted_final_matrix_contract.py`](../../tests/test_untargeted_final_matrix_contract.py) |
| Output levels | `output.alignment.output_levels` | [`test_alignment_output_levels.py`](../../tests/test_alignment_output_levels.py) |
| Area policy | [Signal processing](signal-processing.md#boundary-and-area-owners) | |
| Targeted row binding | [Targeted selection](targeted-selection.md#candidate-selection) | |

## Boundaries

- **Final only.** The type name `Matrix` is reserved for the finalized shape;
  owner observations, Backfill results, consolidation snapshots and
  provisional cell collections cannot cross the delivery seam as if final.
- **Values only.** The primary Matrix carries numeric values and a stable
  row/sample structure; identity, evidence and review details belong in
  sidecars, and diagnostic risk flags authorize no value or row.
- **One decision set.** Every renderer formats one finalized decision set from
  the live cell-decision owner and cannot rebuild values, statuses, counts or
  row inclusion from raw cells; only the workflow writer publishes
  ([Writer Authority](decision-policy.md#writer-authority)), and a past
  validation packet cannot authorize current values.
- **A backfilled value is not a detection.** A `backfilled` peak can supply an
  area in an established row but does not establish the row or count as direct
  CID-NL support.
- **Established groups only.** Provisional, unresolved and rejected groups
  stay review and audit facts.
- **`feature_id` is report-scoped**, not identity; a change to public keys,
  filenames, ordering, columns or schema versions is a public-contract change
  needing expected-diff and downstream checks.

## Why It Works This Way

- A censored area is published as observed, never imputed, so downstream
  statistics see what was measured; publishing it does not change counted
  detection or physical selection.
- A row's m/z is decided once when the group is built, so Backfill searches and
  the Matrix displays the same centre; a member's own `peak_mz`
  ([accurate-mass reading](discovery.md#accurate-mass-reading)) can sit up to
  10 ppm from it.
- Owners without a clean integration do not train RT calibration, so values
  and calibration anchors come from different eligibility checks.
- The fast `validation-minimal` profile also writes the explanation report so
  a run can be reviewed without a rerun; it does not establish production
  readiness.

See also [Backfill](backfill.md) · [Alignment](alignment.md) ·
[Workbench and review](workbench.md)
