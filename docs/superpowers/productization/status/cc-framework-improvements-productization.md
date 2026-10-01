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
Dependent quantitative-effect package/activation binding belongs to
`docs/superpowers/productization/status/quantitative_projection_authority_manifest.json`;
it does not extend writer scope.
Promotion requirements belong to
`docs/agent/product-validation-contract.md`.

## Stable lane keys

- `broad_backfill_autowrite`
- `productization_authority_firewall`
- `quality_explanation_sidecar`
- `targeted_ms1_shape_identity_limited_rescue`
- `targeted_ms1_shape_identity_broader_targets`
- `sample_metadata_order_projection`
- `sample_metadata_role_value_behavior`
- `review_action_candidate_sidecar`
- `review_action_selected_candidate_switch`
- `review_action_manual_boundary_area_writer`
- `calibration_normalization_activation`
- `gui_replay_parity`
- `untargeted_owner_backfill_canonical_quantitative_projection`
