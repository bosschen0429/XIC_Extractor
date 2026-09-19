# CID-NL default activation authority reconstruction gate v1

Date: 2026-06-20

Status: `blocked`.

## Purpose

This no-RAW gate classifies the current 511-cell Backfill authority against the
new CID-NL 85RAW alignment baseline after bridge evaluation. It distinguishes
cells that are still blank-and-writable from cells that are already detected in
the new baseline, and keeps missing or ambiguous identity mappings as blockers.

It does not write ProductWriter outputs, regenerate the default matrix, change
workbook/GUI behavior, or grant new Backfill authority. The reconstruction
audit is diagnostic evidence, not a replacement manifest.

## Command

```powershell
python scripts/check_cid_nl_default_activation_authority_reconstruction_gate.py
```

Generated outputs are written under ignored
`output/validation/cid_nl_default_activation_authority_reconstruction_gate/`:

- `cid_nl_default_activation_authority_reconstruction_gate_summary.json`
- `cid_nl_default_activation_authority_reconstruction_audit.tsv`

## Result

- `overall_status=blocked`
- accepted authority cells: `511`
- expected-diff rows: `511`
- candidate replay: `pass`, `147` written Backfill cells
- unresolved authority cells: `101`

Authority reconstruction:

| Resolution | Count | Meaning |
| --- | ---: | --- |
| `write_ready_blank` | 147 | Unique bridge target and blank new baseline cell; included in the in-memory candidate replay. |
| `superseded_by_detected_baseline` | 263 | Unique bridge target but the new baseline already has a detected value; no Backfill write is allowed or needed. |
| `blocked_identity_ambiguous` | 82 | The old authority cell maps to more than one new identity row under the bridge tolerance. |
| `blocked_identity_missing` | 19 | No new identity row matches the old authority cell under the bridge tolerance. |

The unresolved blockers are concentrated in 11 old authority peaks:

- ambiguous: `FAM003533` 49 cells, `FAM002634` 14,
  `FAM003581` 8, `FAM003458` 6, `FAM004629` 3,
  `FAM009962` 2.
- missing: `FAM020375` 8 cells, `FAM017604` 4,
  `FAM001954` 3, `FAM009606` 3, `FAM016202` 1.

## Product Interpretation

This gate materially narrows the default-activation problem. The old bridge
gate reported 364 blocked cells. Reconstruction shows that 263 of those are not
writer failures: the new CID-NL baseline already has detected values, so they
should be preserved as detected baseline cells, not rewritten as Backfill.

The remaining blocker is canonical identity, not timing, RAW extraction, or
expected-diff content. The in-memory candidate replay for the 147 still-blank
write-ready cells passes, but default activation remains blocked until the 101
missing/ambiguous identity cells are resolved or explicitly excluded by a
separate product contract.

Detected-baseline supersession is not Backfill writer authority. CID-NL/MS2
evidence still must not directly become ProductWriter authority.

## Next Product Gate

The next gate should resolve the 11 blocked old authority peaks into a durable
canonical identity decision:

1. Decide whether each missing/ambiguous old authority peak has a valid new
   canonical row, should be superseded by detected baseline, or must be
   retired from the default activation scope.
2. Re-run this reconstruction checker.
3. Only if unresolved authority cells reach `0` should a default activation
   candidate be built from the reconstructed expected-diff contract.
