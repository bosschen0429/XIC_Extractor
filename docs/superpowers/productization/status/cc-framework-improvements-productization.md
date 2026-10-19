# XIC productization compatibility lane registry

Doc placement: repo_support_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/product/productization.md
Doc exit rule: Update only when a stable lane key is added or retired; retire after checkers stop using this compatibility path.

Kind: `productization_compatibility_lane_registry`

This file is a checker lookup registry, not a current-state board or branch
handoff. Mutable lane state and writer scope belong only to
`docs/superpowers/validation/productization_status_index.tsv` and
`docs/superpowers/productization/status/productization_authority_manifest.json`.
Promotion requirements belong to
`docs/agent/product-validation-contract.md`.

## Stable lane keys

- `productization_authority_firewall`
- `quality_explanation_sidecar`
- `sample_metadata_order_projection`
- `gui_replay_parity`
