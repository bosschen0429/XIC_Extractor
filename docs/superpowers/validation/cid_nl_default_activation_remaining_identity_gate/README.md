# CID-NL default activation remaining identity gate v1

Date: 2026-06-20

Status: `pass` for resolving the final 27 identity blockers as explicit
no-write scope removals. No product output was written.

## Purpose

This no-RAW gate consumes the cell-local identity gate and closes the last
default-activation identity blocker without broadening authority. It resolves
only the known terminal cases:

- missing safe bridge identity;
- ambiguous candidates that are all blank for the sample;
- ambiguous candidates where multiple rows already have detected values.

Those cases are removed from the future CID-NL default-activation candidate
scope. They are not rewritten to candidate rows, not converted into Backfill
writer authority, and not used to change the current product-ready default
bundle.

## Command

```powershell
python scripts/check_cid_nl_default_activation_remaining_identity_gate.py --require-pass
```

Generated outputs are written under ignored
`output/validation/cid_nl_default_activation_remaining_identity_gate/`:

- `cid_nl_default_activation_remaining_identity_gate_summary.json`
- `cid_nl_default_activation_remaining_identity_audit.tsv`

## Result

- `overall_status=pass`
- accepted authority cells: `511`
- input unresolved authority cells: `27`
- output unresolved authority cells: `0`
- ProductWriter/default matrix/workbook/GUI/Backfill authority changes: `false`
- default activation candidate built: `false`

Activation-candidate contract represented by the audit:

| Classification | Count | Meaning |
| --- | ---: | --- |
| `write_ready_blank` | 147 | Candidate replay-ready blank Backfill writes. |
| `superseded_by_detected_baseline` | 263 | Unique bridge target already has a detected baseline value; no write. |
| `cell_local_unique_detected_candidate_supersession` | 74 | Ambiguous bridge target, but exactly one candidate is detected for that sample; no write and no Backfill authority. |
| `scope_removed_missing_identity_no_write` | 19 | No safe new identity bridge for the legacy authority cell. |
| `scope_removed_ambiguous_blank_no_write` | 5 | Ambiguous candidates are all blank; no safe write target. |
| `scope_removed_ambiguous_multiple_detected_no_write` | 3 | Multiple ambiguous candidates are already detected; no canonical choice is made. |

The 27 resolved no-write removals are:

- missing bridge identity: `FAM020375` 8 cells, `FAM017604` 4,
  `FAM001954` 3, `FAM009606` 3, `FAM016202` 1.
- ambiguous all-blank: `FAM003458` 4 cells, `FAM009962` 1.
- ambiguous multiple-detected: `FAM003533` 2 cells, `FAM003581` 1.

The audit includes row identity context for hidden source traps. In the real
packet, `FAM017604` appears as an exact/source feature-family identity row in
the new matrix identity, but it is not a safe bridge target for the old
authority cells, so it remains a no-write scope removal rather than a replay
target.

## Product Interpretation

This gate answers the previous product question: the remaining 27 cells should
not be forced into CID-NL default activation. They are now explicitly accounted
for as no-write removals from the next activation candidate contract.

The CID-NL default activation path is no longer blocked on unresolved identity
cells. The next product gate is not more identity slicing; it is an
expected-diff/default-activation candidate contract that must preserve these
semantics:

- write only the 147 `write_ready_blank` cells;
- preserve the 337 detected-baseline/no-write cells;
- omit the 27 no-write scope removals;
- continue to assert that CID-NL/MS2 evidence and candidate rows are not direct
  ProductWriter authority.

The current `product_ready_default_matrix_activated` bundle remains unchanged.
