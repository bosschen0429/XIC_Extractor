# CID-NL Targeted CSV Contract v1

Doc placement: formal_repo_doc
Doc kind: validation_artifact
Doc lifecycle: active
Repo owner: docs/superpowers/validation/cid_nl_targeted_csv_contract_v1/README.md
Doc exit rule: Retain while targeted_output_v1 remains a characterized public delivery surface.

Status: `diagnostic_only`; characterization evidence, not scientific truth or
new product authority.

## Product boundary

`xic_results.csv` is the current targeted evidence delivery. Targeted and
untargeted are separate product lines that share evidence/domain components but
do not share an output schema or downstream-consumer requirement. The
feature-by-sample untargeted matrix is the normal handoff to preprocessing,
calibration, and statistics.

There is currently no required targeted downstream analytical consumer.
Targeted results are technically convertible to a feature-by-sample matrix, but
that capability is not an activated handoff contract. Issue #130 therefore
records `no current downstream analytical consumer` instead of fabricating a
reader smoke.

## Real baseline characterization

The immutable source is
`cid_nl_legacy_baseline_6d3b18ef_8raw_v2`, commit
`6d3b18efd88971ffb6d96412a8d8ee47e8ed4742`. The retained artifact root and
full provenance hashes remain in
[`../cid_nl_legacy_baseline_v1/baseline_summary.json`](../cid_nl_legacy_baseline_v1/baseline_summary.json).
Real sample rows stay in the ignored retained artifact store and are not copied
into the test suite.

The sealed summary's `legacy_characterization_limits` preserves the historical
statement that issue #130 required an external reader. That sentence records the
old issue premise and is not current product authority; the sealed JSON is not
rewritten after publication. The product-line correction is owned by the domain
glossary, architecture contract, and current issue #130 body.

| Property | Baseline stage | Final stage |
| --- | ---: | ---: |
| Bytes | 6,737 | 6,763 |
| SHA-256 | `DFDC5B4227819BA8087FC72EE9023FFFAC8FE4A82E77FD73ED1CEF813C8EB145` | `F1783E22AF31B92F99938FDA9420B8A3A1671BB17587A4EECD936A83E16EF168` |
| Data rows | 8 | 8 |
| Headers | 99 | 99 |
| UTF-8 BOM | yes | yes |
| CRLF records | 9 | 9 |
| Bare LF / CR | 0 / 0 | 0 / 0 |
| `ND` cells | 246 | 240 |
| `ERROR` cells | 0 | 0 |
| Blank analytical cells | 0 | 0 |
| `OK` / `NL_FAIL` / `NO_MS2` | 70 / 30 / 11 | 70 / 30 / 11 |

Both stages preserve the same sample order (ordered-value SHA-256
`BE184540E1CC5B32A7F40BECDADE25CDE5830DEE5F87036B42780C3A1D2AC2D0`)
and final header order (ordered-value SHA-256
`8CBD538012713D9042752D00296C0654E9C7DBC5C7107F52E3D11EED4157CC08`).
The final file contains 14 distinct targets, each expanded to `RT`, `Int`,
`Area`, `PeakStart`, `PeakEnd`, `PeakWidth`, and `NL`.

The expected baseline-to-final difference is exactly six cells for one
`5-hmdC` row: `RT`, `Int`, `Area`, `PeakStart`, `PeakEnd`, and `PeakWidth`
change from `ND` to the limited-default selected peak values. Sample identity,
row order, headers, all NL tokens, and all other cells remain unchanged. The
sealed two-run comparison reports both stage CSVs as byte-exact across repeated
execution.

## Semantic contract

- Reported `RT`, intensity, area, boundaries, and width come from the selected
  integration when present; otherwise they use the legacy selected peak.
- Numeric formatting is fixed at four decimals for RT/boundaries/width, zero
  decimals for intensity, and two decimals for area.
- A complete counted peak with numerical zero values writes numerical zero;
  zero is not missing.
- A non-counted result, no peak, or a partially missing peak projection writes
  `ND` for all six MS1 values rather than a mixture of numbers and nulls.
- A file-level failure writes `ERROR` for every target field.
- NL remains its own token (`OK`, `WARN_*ppm`, `NL_FAIL`, `NO_MS2`, `ND`, or
  `ERROR`) and does not infer a numeric peak value.
- Wide CSV preserves input file-result row order. Duplicate sample rows are
  preserved by the writer; no last-write-wins normalization occurs here.
- Duplicate target labels are rejected by configuration loading before the
  writer (`tests/test_config.py::test_load_config_rejects_duplicate_target_labels`).
- Confidence, reason, product state, counted detection, and review state are
  not wide-CSV columns. Their characterized delivery surface is the sibling
  `xic_results_long.csv`; they must not be inferred from wide values alone.

The byte and edge contracts are executable in `tests/test_csv_writers.py`.
Synthetic rows use non-real sample names and are explicitly separate from the
checksum-bound 8RAW observation above.

## Authority

This characterization changes no CSV bytes, schema, selected peak, selected
area, counted detection, matrix identity, workbook, activation, export,
maturity tier, or active lane. Product/public authority remains owned by issue
#120. No RAW replay was run for this packet.
