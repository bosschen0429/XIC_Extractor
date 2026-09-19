# CID-NL default activation bridge gate v1

Date: 2026-06-20

Status: `blocked`.

## Purpose

This no-RAW gate checks whether the existing 511-cell Backfill default authority
can be bridged from the tracked default activation identity space onto the new
CID-NL 85RAW alignment identity space.

It does not run RAW, write ProductWriter outputs, regenerate the default
matrix, change workbook/GUI behavior, or grant new write authority. The bridge
audit is diagnostic evidence, not a replacement manifest.

## Command

```powershell
python scripts/check_cid_nl_default_activation_bridge_gate.py
```

Generated outputs are written under ignored
`output/validation/cid_nl_default_activation_bridge_gate/`:

- `cid_nl_default_activation_bridge_gate_summary.json`
- `cid_nl_default_activation_bridge_audit.tsv`

## Result

- `overall_status=blocked`
- target preflight status: `pass`
- accepted authority cells: `511`
- expected-diff rows: `511`
- expected-diff content problems: `0`
- accepted peak IDs: `83`
- peak bridge status: `72 pass`, `11 blocked`
- cell bridge status: `147 pass`, `364 blocked`
- activation replay: `not_run`, because bridge blockers are present

Blocked cell reasons:

| Reason | Count | Meaning |
| --- | ---: | --- |
| `new_baseline_already_has_value` | 263 | The new matrix already has a detected value, so the current activation builder must not overwrite it as accepted Backfill. |
| `new_identity_ambiguous` | 82 | The old accepted peak maps to more than one new identity row under the explicit m/z/RT bridge tolerance. |
| `new_identity_missing` | 19 | No new identity row matches the old accepted peak under the explicit bridge tolerance. |

## Product Interpretation

The CID-NL target evidence is good enough to keep moving:

- `300.1605 -> 184.113` is present as a high-confidence primary row.
- `301.165 -> 185.116` remains present as its own dR-tag row.

But the default activation cannot be promoted through a simple ID bridge. The
old 511-cell Backfill authority is tied to a previous alignment identity and
baseline matrix. On the new CID-NL alignment baseline, many formerly accepted
Backfill cells are now nonblank detected values, and several row identities are
ambiguous or missing.

Therefore this gate blocks default activation before expected-diff replay. That
is the correct product behavior: do not convert Discovery candidates, m/z/RT
near-neighbors, or CID-NL/MS2 evidence directly into ProductWriter authority.

The checker also validates the supplied expected-diff content, not only row
count, and verifies that selected new identity rows still match the matrix row
coordinates they point to. The current real packet has no expected-diff content
problem, so the blocker is the identity/baseline bridge itself.

## Next Product Gate

The next viable product gate is not performance work and not another 85RAW run.
It is a canonical identity/authority reconstruction gate:

1. Define the durable row identity key that survives alignment reruns.
2. Rebuild or reclassify the 511-cell authority on that identity, distinguishing
   still-blank accepted Backfill cells from cells that are now detected in the
   new baseline.
3. Produce a new expected-diff packet only after the authority classification is
   complete and unambiguous.

Until that gate passes, the default product tier remains the existing
`product_ready_default_matrix_activated` bundle, and the CID-NL default
activation path remains `production_candidate_blocked`.
