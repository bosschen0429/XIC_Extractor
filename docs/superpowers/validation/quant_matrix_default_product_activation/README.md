# QuantMatrix Default Product Activation v1

Status: `product_ready_default_matrix_activated`.

This directory is the activated default QuantMatrix bundle for the current
511-cell Backfill authority surface. It is a product-output activation artifact,
not a scorer run, RAW validation run, workbook change, GUI change, selected
peak/area change, or counted-detection change.

## Primary Use Path

Read this file for downstream numeric analysis:

`default_output/quant_matrix.tsv`

Shape:

- wide TSV;
- first columns: `Mz`, `RT`;
- remaining columns: sample stems;
- non-empty sample cells are numeric area values;
- blanks remain missing values.

The matrix intentionally does not mark which numeric cells were Backfill. For
normal downstream normalization, correction, and statistics, read all numeric
cells as quantification values.

This bundle is an untargeted, family-centered matrix, not a target-label table.
Do not use an isotope-shifted row as proof that the monoisotopic target row
exists. `d4-N6-2HE-dA` target m/z `300.1605` / product `184.113` is not
materialized as a product row in this activation bundle. The nearby
`FAM007866` row at `Mz=301.165`, `RT=23.3466` remains a valid dR-tag isotope
feature row; it must not be used as authority for `300.1605` target-row
presence.

Known stale-generation finding: this bundle was built from discovery artifacts
that contain no 22-25 min monoisotopic `300.1605 -> 184.113` candidate for this
target, while they do contain `301.165 -> 185.116` isotope-shift candidates
across all 85 samples. The Discovery generation-path fix is verified by
`scripts/check_discovery_precursor_inference_artifact.py` against
`../discovery_precursor_inference/TumorBC2312_DNA/discovery_candidates.csv`;
the checker requires both the inferred `300.1605` row and the valid `301.165`
isotope row. This activation bundle still predates that fix and must be
regenerated through an explicit discovery/alignment/default-activation
expected-diff goal before it can claim `300.1605` target-row presence.

## Audit Use Path

Use sidecars only when provenance or review is needed:

| File | Purpose |
| --- | --- |
| `default_output/cell_provenance.tsv` | One row per non-empty matrix cell. Join by `peak_hypothesis_id + sample_stem`; `cell_status` is `detected` or `accepted_backfill`. |
| `default_output/row_summary.tsv` | Per-row counts: detected, accepted Backfill, quant-available, missing, and Backfill fraction. |
| `default_output/expected_diff_summary.tsv` | Activation diff closure: 511 expected, 511 written, 0 unused. |
| `default_output/source_summary.tsv` | Relative source paths and SHA-256 hashes for the baseline matrix, matrix identity, manifest, and expected diff. |
| `default_product_activation_checks.tsv` | Fail-closed checks used by the activation script. |
| `quant_matrix_default_product_activation_summary.json` | Top-level status, counts, authority scope, and output hashes. |

For stable row identity, follow `default_output/source_summary.tsv` to
`../../quant_matrix_real_bundle/inputs/alignment_matrix_identity.tsv`.
That identity file maps matrix rows and `Mz`/`RT` pairs to
`peak_hypothesis_id`. Use `peak_hypothesis_id + sample_stem` to inspect
cell-level provenance in `cell_provenance.tsv`.

A future release-facing bundle should add explicit target-row identity outputs
for target/ISTD lookup. Until then, target labels should be resolved through
exact target evidence only; isotope-shift benchmark matches are diagnostic
context for that target and do not prove monoisotopic product-row presence.

## Contract Boundaries

- Accepted Backfill values are quantification values in the default matrix.
- Accepted Backfill values are not detections, truth labels, or counted
  detections.
- `cell_provenance.tsv` preserves detected-only reconstruction.
- `truth_status` for accepted Backfill rows is `not_truth_claimed`.
- Broad 4613-row Backfill remains parked.
- Current write authority is exactly 511 accepted Backfill cells under
  `backfill_policy_write_ready_rows`.
- Workbook and GUI behavior are unchanged.

## Quick Sanity Checks

The activation is healthy when:

- `quant_matrix_default_product_activation_summary.json` has `status=pass`;
- `accepted_backfill_count=511`;
- `written_backfill_count=511`;
- `unused_expected_diff_count=0`;
- `all_reference_outputs_match=true`;
- `workbook_or_gui_changed=false`;
- `selected_peak_area_or_counting_changed=false`;
- `broad_backfill_unparked=false`;
- `scorer_ran=false`;
- `raw_or_85raw_ran=false`.

To recheck the bundle without rerunning scorer or RAW:

```powershell
uv run python scripts/build_quant_matrix_default_product_activation.py --check-only
```
