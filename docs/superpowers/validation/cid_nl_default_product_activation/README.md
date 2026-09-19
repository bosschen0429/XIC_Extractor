# CID-NL Default Product Activation v1

Status: `pass`.

This is the explicit public default activation change for the narrowed CID-NL Discovery bundle. It writes exactly 95 adopt-ready blank cells through the existing ProductionAcceptanceManifest -> QuantMatrixVersion path.

It does not rerun RAW, change workbook/GUI behavior, change selected peak/area/counting, treat candidate rows as matrix rows, or make CID-NL/MS2 evidence direct ProductWriter authority.

Terminology boundary: this is a Discovery product scope. The compact summary uses `accepted_discovery_cell_count` and `write_cid_nl_discovery_default_cell` for the public decision. Legacy `accepted_backfill` / `write_accepted_backfill` values are retained only inside the shared QuantMatrixVersion writer and provenance compatibility surface.

Row-universe boundary: the output matrix uses the current 85RAW-derived Discovery-expanded alignment row universe. Sparse rows are acceptable for this untargeted handoff; prevalence filtering belongs downstream, not in this CID-NL activation gate.

Feature-inclusion boundary: source/successor m/z or RT similarity is an identity-review question only. The 95 active writes are accepted because the successor cell itself has CID-NL tag context, quant value, write-ready manifest authority, and provenance.

## Counts

- Accepted Discovery default writes: `95`
- Candidate transitions: `20`
- Existing successor context cells preserved: `337`
- Omitted no-target cells preserved: `27`
- Product authority scope: `cid_nl_adopt_ready_feature_inclusion_95_cells`

## Versioned Summary

- Summary JSON: `docs/superpowers/validation/cid_nl_default_product_activation/cid_nl_default_product_activation_summary.json`
- Compact transition manifest: `docs/superpowers/validation/cid_nl_default_product_activation/cid_nl_default_product_activation_manifest.tsv`
- Checks TSV: `docs/superpowers/validation/cid_nl_default_product_activation/cid_nl_default_product_activation_checks.tsv`

Full matrix and provenance TSV outputs are intentionally externalized under `output/validation/cid_nl_default_product_activation/` to keep review diffs small.

## Clean-Checkout Gate

Default build mode is a local replay path. It expects the externalized
successor-authority, value-delta, and 85RAW-derived alignment inputs under
ignored `output/` paths.

Clean-checkout CI and PR readiness must use the retained compact artifact gate:

```powershell
python scripts/build_cid_nl_default_product_activation.py --check-only
```
