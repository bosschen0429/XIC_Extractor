# Backfill Expansion Full Evidence Chain v1

Status: `production_candidate_held_incomplete_chain`.

This gate checks whether the bounded 666-cell Backfill expansion candidate
packet has the complete Backfill evidence chain by stable
`peak_hypothesis_id + sample_stem` keys.

The answer is no: `374/666` cells pass the full chain, and `292/666` remain
held.

## What Was Checked

- Expected-diff/provenance row exists: `666/666`.
- Sample-local source evidence exists: `666/666`.
- RAW overlay trace identity is observed: `666/666`.
- Shift-aware standard-peak gate supports the family: `492/666` cells across
  `14/20` families.
- Raw own-max metric is above the current threshold: `496/666`.
- Product-authorized MS1 sidecar row exists after own-max enforcement:
  `374/666`.

## Held Reasons

- `174` cells are blocked because their family is not supported by the
  shift-aware standard-peak gate.
- `99` cells are blocked because `absolute_own_max_shape_similarity <= 0.5`.
- `19` cells are blocked because own-max metric evidence is missing.

The checker also fixed the evidence-chain split: the standard-peak MS1 authority
bundle now rejects rows whose own-max metric is missing or below threshold
instead of letting a shift-aware family gate alone create `product_authorized`
rows.

## Authority Boundary

This gate does not grant writer authority. It writes no default matrix, no
workbook, no GUI state, no selected peak, no selected area, and no counted
detection. The full cell map and generated sidecars stay externalized under
`output/validation/backfill_expansion_full_evidence_chain_v1/`; git keeps only
the compact summary, checks, row manifest, and this README.

Use `python -m scripts.check_backfill_expansion_full_evidence_chain` for
historical artifact integrity. Use
`python -m scripts.check_backfill_expansion_full_evidence_chain --require-full-chain`
to prove that the retained packet remains incomplete.

The checker no longer regenerates `full_chain_status` or product-authority
sidecars. Those historical classifications are validation/oracle context only;
they cannot mint current authority.
