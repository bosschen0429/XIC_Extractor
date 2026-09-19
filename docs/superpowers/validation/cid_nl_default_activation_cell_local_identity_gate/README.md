# CID-NL default activation cell-local identity gate v1

Date: 2026-06-20

Status: `blocked`.

## Purpose

This no-RAW gate narrows the unresolved canonical identity blockers from the
authority reconstruction gate. It only resolves ambiguous cells when exactly
one candidate row already has a detected baseline value for the same sample.
That state is a no-write detected-baseline supersession, not Backfill writer
authority.

Ambiguous candidates are only considered if the identity row still points to a
matrix row with matching `Mz` and `RT`. The current real packet has no candidate
identity-to-matrix coordinate mismatches.

It does not write ProductWriter outputs, regenerate the default matrix, change
workbook/GUI behavior, or grant new Backfill authority.

## Command

```powershell
python scripts/check_cid_nl_default_activation_cell_local_identity_gate.py
```

Generated outputs are written under ignored
`output/validation/cid_nl_default_activation_cell_local_identity_gate/`:

- `cid_nl_default_activation_cell_local_identity_gate_summary.json`
- `cid_nl_default_activation_cell_local_identity_audit.tsv`

## Result

- `overall_status=blocked`
- reconstruction unresolved authority cells: `101`
- cell-local resolved ambiguous cells: `74`
- remaining unresolved authority cells: `27`

Cell-local classification:

| Resolution | Count | Meaning |
| --- | ---: | --- |
| `write_ready_blank` | 147 | Already candidate replay-ready from the reconstruction gate. |
| `superseded_by_detected_baseline` | 263 | Unique bridge target already has a detected baseline value. |
| `cell_local_unique_detected_candidate_supersession` | 74 | Ambiguous bridge target, but exactly one candidate has a detected baseline value for that sample; no Backfill write is needed or allowed. |
| `blocked_identity_missing` | 19 | No matching new identity row. |
| `blocked_ambiguous_all_blank` | 5 | Ambiguous candidates are all blank for the sample; no safe write target exists. |
| `blocked_ambiguous_multiple_detected_candidates` | 3 | More than one candidate already has a detected value for the sample; preserving one as canonical would require a separate identity decision. |
| `blocked_ambiguous_identity_matrix_coordinate_mismatch` | 0 | Candidate identity row no longer matches the matrix row coordinates. |

Remaining unresolved peaks:

- missing: `FAM020375` 8 cells, `FAM017604` 4,
  `FAM001954` 3, `FAM009606` 3, `FAM016202` 1.
- ambiguous all-blank: `FAM003458` 4 cells, `FAM009962` 1.
- ambiguous multiple-detected: `FAM003533` 2 cells, `FAM003581` 1.

## Product Interpretation

This gate narrows the default-activation blocker without broadening authority.
The prior reconstruction gate had 101 unresolved cells; 74 of those are now
explained as no-write detected-baseline supersession using a cell-local,
human-explainable rule.

Default activation remains blocked because 27 cells still lack a durable
canonical identity decision. The 147 blank candidate writes remain replay-ready,
but no default activation candidate should be built until the remaining 27 cells
are resolved or explicitly removed from scope by a separate product contract.

Cell-local detected candidates are not Backfill writer authority and must not
be used to authorize blank writes.

## Next Product Gate

Resolve the remaining 27 cells:

1. Decide whether the five missing old authority peaks have a valid new
   canonical row, should be retired, or need external/manual evidence.
2. Decide the five all-blank ambiguous cells without writing to a candidate row
   unless a separate identity rule makes that target unambiguous.
3. Decide the three multiple-detected ambiguous cells without choosing one
   detected row as canonical unless supporting identity evidence is added.
