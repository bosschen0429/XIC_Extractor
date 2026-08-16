# XIC productization compatibility lane registry

Doc placement: repo_support_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/product/productization.md
Doc exit rule: Update only when a stable lane key is added or retired; retire after checkers stop using this compatibility path.

Kind: `productization_compatibility_lane_registry`

This file is a checker lookup registry, not a current-state board or branch
handoff. Mutable lane state and writer scope belong only to
`docs/superpowers/validation/productization_status_index_v1.tsv` and
`docs/superpowers/schemas/productization_authority_manifest.v1.json`.
Dependent quantitative-effect package/activation binding belongs to
`docs/superpowers/schemas/quantitative_projection_authority_manifest.v1.json`;
it does not extend writer scope.
Promotion requirements belong to
`docs/agent/product-validation-contract.md`.

## Stable lane keys

- `backfill_current_write_ready_scope`
- `cid_nl_default_product_activation_v1`
- `backfill_expansion_default_product_activation_v1`
- `broad_backfill_autowrite`
- `productization_authority_firewall_v1`
- `mechanical_adjudication_contract_v1`
- `review_packet_workflow_v1`
- `peak_choice_truth_lockbox_v1`
- `missing_overlay_evidence_recovery_v1`
- `quality_explanation_sidecar_v1`
- `targeted_ms1_shape_identity_limited_rescue_v1`
- `targeted_ms1_shape_identity_broader_targets`
- `sample_metadata_order_projection_v1`
- `sample_metadata_role_value_behavior`
- `review_action_candidate_sidecar_v1`
- `review_action_selected_candidate_switch`
- `review_action_manual_boundary_area_writer`
- `calibration_normalization_activation`
- `gui_replay_parity`
- `untargeted_owner_backfill_canonical_quantitative_projection_v1`
