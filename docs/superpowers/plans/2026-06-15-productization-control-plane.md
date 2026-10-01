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

- [Lane-state index](../validation/productization_status_index.tsv)
- [Writer-authority manifest](../productization/status/productization_authority_manifest.json)
- [Dependent quantitative-effect authority manifest](../productization/status/quantitative_projection_authority_manifest.json)
- [Structured control schema](../schemas/productization_control_plane_schema.json)
- [Readiness and promotion contract](../../agent/product-validation-contract.md)
- [Stable capability map](../../product/productization.md)

## Historical source

The exact retired body is retained privately under
`source_repo_path:docs/superpowers/plans/2026-06-15-productization-control-plane.md`.
Vault note title:
`2026-06-15-productization-control-plane-source-97778ff9fb5c`.
Its searchable locator note is
`2026-06-15-productization-control-plane-source-97778ff9fb5c-locator`; the
locator carries the stable `source_repo_path:` key while the source note stays
byte-for-byte identical to the retired body.

## Lane anchor index

### park_broad_backfill

Lane key: `broad_backfill_autowrite`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### productization_authority_manifest

Lane key: `productization_authority_firewall`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### quality_explanations and quality_blockers are explanation and triage inputs only

Lane key: `quality_explanation_sidecar`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### Targeted MS1 shape identity limited rescue remains production-ready

Lane key: `targeted_ms1_shape_identity_limited_rescue`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### GUI and broader targets remain blocked

Lane key: `targeted_ms1_shape_identity_broader_targets`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### sample_metadata cross-module parity

Lane key: `sample_metadata_order_projection`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### role/value behavior remains blocked

Lane key: `sample_metadata_role_value_behavior`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### selected candidate / manual boundary writer parked

Lane key: `review_action_selected_candidate_switch`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### manual-boundary area writer parked

Lane key: `review_action_manual_boundary_area_writer`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### calibration/normalization activation

Lane key: `calibration_normalization_activation`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### GUI replay / GUI parity is out of scope

Lane key: `gui_replay_parity`

Routes: [state](../validation/productization_status_index.tsv), [writer authority](../productization/status/productization_authority_manifest.json), [stable capability](../../product/productization.md).

### Untargeted Owner Backfill Canonical Quantitative Projection

Lane key: `untargeted_owner_backfill_canonical_quantitative_projection`

Routes: [state](../validation/productization_status_index.tsv), [dependent-effect authority](../productization/status/quantitative_projection_authority_manifest.json), [stable capability](../../product/productization.md).
