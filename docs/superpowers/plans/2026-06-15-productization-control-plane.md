# Productization compatibility index

Doc placement: formal_repo_doc
Doc kind: manifest
Doc lifecycle: active
Repo owner: docs/product/productization.md
Doc exit rule: retain while stable control-plane path references exist.

This path is a compatibility lookup surface. It preserves stable lane keys and
historical anchor names; it does not own mutable lane state, counts, authority,
promotion decisions, or product rules.

When a hook or older referrer says to update the productization control plane,
treat that phrase as an instruction to consult this index and update the
applicable authority route. Modify this file only when a stable lane key or
owner route changes.

## Authority routes

- [Lane-state index](../validation/productization_status_index_v1.tsv)
- [Writer-authority manifest](../schemas/productization_authority_manifest.v1.json)
- [Structured control schema](../schemas/productization_control_plane_schema.v2.json)
- [Readiness and promotion contract](../../agent/product-validation-contract.md)
- [Stable capability map](../../product/productization.md)

## Historical source

The exact retired body is retained privately under
`source_repo_path:docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
The checked retirement transaction is recorded in the
[retirement evidence packet](../file-management/docs-cleanup/2026-08-01-r11-d0-productization-control-plane-retirement-evidence.json).

## Lane anchor index

### QuantMatrix Default Product Activation v1

Lane key: `backfill_current_write_ready_scope`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### CID-NL Default Product Activation v1

Lane key: `cid_nl_default_product_activation_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### Backfill Expansion Full Evidence Chain v1

Lane key: `backfill_expansion_default_product_activation_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### Backfill Expansion Clean-Target Selective Default Activation v1

Lane key: `backfill_expansion_clean_target_selective_product_activation_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### park_broad_backfill

Lane key: `broad_backfill_autowrite`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### productization_authority_manifest_v1

Lane key: `productization_authority_firewall_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### mechanical_adjudication_index_v1

Lane key: `mechanical_adjudication_contract_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### Review Packet / Approval Workflow v1

Lane key: `review_packet_workflow_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### lockbox_shadow_automation_experiment_v1

Lane key: `peak_choice_truth_lockbox_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### Missing-Overlay Evidence Recovery v1

Lane key: `missing_overlay_evidence_recovery_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### quality_explanations and quality_blockers are explanation and triage inputs only

Lane key: `quality_explanation_sidecar_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### Targeted MS1 shape identity limited rescue remains production-ready

Lane key: `targeted_ms1_shape_identity_limited_rescue_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### GUI and broader targets remain blocked

Lane key: `targeted_ms1_shape_identity_broader_targets`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### sample_metadata cross-module parity

Lane key: `sample_metadata_order_projection_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### role/value behavior remains blocked

Lane key: `sample_metadata_role_value_behavior`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### candidate-sidecar verifier is now production_candidate

Lane key: `review_action_candidate_sidecar_v1`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### selected candidate / manual boundary writer parked

Lane key: `review_action_selected_candidate_switch`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### manual-boundary area writer parked

Lane key: `review_action_manual_boundary_area_writer`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### calibration/normalization activation

Lane key: `calibration_normalization_activation`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).

### GUI replay / GUI parity is out of scope

Lane key: `gui_replay_parity`

Routes: [state](../validation/productization_status_index_v1.tsv), [writer authority](../schemas/productization_authority_manifest.v1.json), [stable capability](../../product/productization.md).
