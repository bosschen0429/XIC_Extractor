# Package 0 Smallest-Strangler Contract v1

Status: `diagnostic_only`

This packet freezes the comparison boundary for the CID-NL / NL116 Targeted
strangler before successor implementation. It adds no scientific truth,
selected-value, counted-detection, Matrix, workbook, activation, publication,
or export authority.

## Legacy comparator membership

The only canonical legacy comparator is
`cid_nl_legacy_baseline_6d3b18ef_8raw_v2`, whose sealed run-1 output inventory
SHA-256 is
`CC25A6A731C822F9147FECAB5949EAABB79D0415FCA67436365CEC035B4293B1`.

Both the baseline-stage and final-stage `xic_results.csv` are comparator
surfaces:

- each stage contains 8 ordered sample rows and 99 ordered columns;
- the first column is sample identity and the remaining 98 columns are 14
  targets × (`RT`, `Int`, `Area`, `PeakStart`, `PeakEnd`, `PeakWidth`, `NL`);
- therefore each stage freezes 8 sample-identity cells and 784 analytical
  cells;
- membership is the complete Cartesian product of the sealed ordered sample
  sequence and sealed ordered header sequence, not a hand-picked subset;
- the ordered sample sequence SHA-256 is
  `BE184540E1CC5B32A7F40BECDADE25CDE5830DEE5F87036B42780C3A1D2AC2D0`;
- the final ordered header sequence SHA-256 is
  `8CBD538012713D9042752D00296C0654E9C7DBC5C7107F52E3D11EED4157CC08`.

The legacy baseline-to-final workflow itself has one already-characterized
expected difference: exactly six `5-hmdC` MS1 cells (`RT`, `Int`, `Area`,
`PeakStart`, `PeakEnd`, `PeakWidth`) change from `ND` to values in one row.
This is an intra-legacy stage difference. It is not permission for a successor
to differ from either corresponding sealed stage.

## Public surfaces

- Every CSV and TSV in the sealed run-1 artifact set is byte-exact.
- `method_manifest.json` may normalize only the JSON pointers already listed
  in the canonical comparator contract.
- Existing CLI flags, GUI result summary, CSV schemas, workbook production,
  output paths, and writer authority remain owned by their current modules.
- Package 0 routes execution through a shared headless service but does not
  transfer presentation or writer authority into that service.

## New shadow selections

A successor-selected cell outside the frozen legacy observation set is not
silently added to the legacy comparator and is not treated as a legacy
failure. It enters a separate no-write shadow universe and requires the
independent scientific oracle named by the later package before promotion.
Until then, legacy remains sole product authority.

[`contract.json`](contract.json) is the executable form of this checklist.
