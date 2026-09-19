# CID-NL default activation successor authority contract v1

Date: 2026-06-20

Status: `pass` for a successor authority candidate. This packet does not
install a new active default ProductWriter output.

Version-control policy: this directory keeps the human-readable report only.
Full generated TSVs and candidate matrix sidecars are written under
`output/validation/cid_nl_default_activation_successor_authority_contract/`,
which is intentionally ignored by git to keep PR diffs reviewable.

## Plain-Language Decision

The old product-ready default bundle had 511 accepted Backfill write cells. The
new CID-NL Discovery/alignment run changed the row identity space, so those 511
old authority cells cannot be copied blindly.

This packet rebuilds the authority contract in the new identity space:

- 147 cells are safe to write in a future CID-NL default activation candidate.
- 337 cells must not be written because the new baseline already has detected
  values.
- 27 cells must not be written because no single safe successor write target
  exists.

The authority is therefore an allow-list, not a candidate pool.

## What Was Built

| Artifact | Rows | Meaning |
| --- | ---: | --- |
| `successor_authority_manifest.tsv` | 147 | The only rows granted successor write authority. It uses `ProductionAcceptanceManifest v1` and has `write_authority=TRUE`. |
| `successor_expected_diff.tsv` | 147 | The exact expected value changes for those 147 writes. |
| `successor_authority_decisions.tsv` | 511 | Human-readable decision ledger for every old authority cell. |
| `candidate_quant_matrix_version/` | sidecar | In-repo replay proving the 147-row authority packet writes exactly 147 cells. |

Generated location:

- `output/validation/cid_nl_default_activation_successor_authority_contract/`

## Command

This command is a local replay/rebuild path. It reads ignored `output/`
alignment and validation inputs and rewrites generated artifacts under
`output/validation/`.

```powershell
python scripts/build_cid_nl_default_activation_successor_authority_contract.py --require-pass
```

Clean-checkout CI must not depend on those ignored outputs. The PR gate uses the
retained compact summaries and downstream `--check-only` / release-slice
checkers instead.

## Result

- `overall_status=pass`
- successor write authority rows: `147`
- expected-diff rows: `147`
- full decision rows: `511`
- detected-baseline no-write decisions: `337`
- scope-removed no-write decisions: `27`
- unresolved decisions: `0`
- candidate replay: `147` expected, `147` written, `0` unused
- matrix delta: `147` changed cells, `0` missing writes, `0` unexpected writes

Decision ledger counts:

| Decision | Count | User meaning |
| --- | ---: | --- |
| `write_authorized` | 147 | Safe successor row, blank sample cell, allowed in the next activation candidate. |
| `no_write_detected_baseline_preserved` | 337 | New baseline already contains a detected value; backfill must not overwrite it. |
| `no_write_omitted` | 27 | No safe successor write target; omitted from the candidate authority. |

## Boundary Statement

This packet creates a successor authority candidate, but it does not switch the
active default matrix.

Unchanged:

- ProductWriter default output
- active default QuantMatrix bundle
- workbook and GUI behavior
- selected peak/area/counting behavior
- broad Backfill authority

The new authority packet also does not make CID-NL/MS2 evidence direct writer
authority. CID-NL/MS2 evidence helped rebuild row identity; the actual write
authority is only the 147-row successor manifest plus its expected-diff replay.

## Why This Should Convince A User

The packet is inspectable from three directions:

1. **Authority allow-list:** `successor_authority_manifest.tsv` contains only
   147 rows and every row is a formal `ProductionAcceptanceManifest v1` row.
2. **Expected change list:** `successor_expected_diff.tsv` lists exactly the
   same 147 row/sample cells and expected values.
3. **Replay proof:** `candidate_quant_matrix_version/expected_diff_summary.tsv`
   reports 147 expected writes, 147 written writes, and 0 unused expected-diff
   rows; the summary also confirms there were 0 unexpected matrix changes.

The 337 detected-baseline cells and 27 omitted cells are not hidden. They are
all present in `successor_authority_decisions.tsv` with a plain reason and
`write_authority=FALSE`.

## Next Product Gate

The next gate is human review / adoption of this successor authority candidate
as an active default activation. That later gate must explicitly decide whether
to switch the active default ProductWriter output to this candidate packet.
