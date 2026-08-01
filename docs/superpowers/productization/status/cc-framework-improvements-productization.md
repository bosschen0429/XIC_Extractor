# XIC productization status anchor

Doc placement: repo_support_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/product/productization.md
Doc exit rule: Update when productization status-anchor phrases or checker expectations change; retire only after checkers and docs stop depending on this path.

Updated: 2026-06-25
Kind: `productization_status_anchor`
Branch: `n/a`
Status: shared productization anchor for productization checks and older
planning surfaces; not a branch current handoff.

This file is not the active handoff for every branch. Branch work must use a
local ignored current handoff such as
`docs/superpowers/handoffs/current/ACTIVE.local.md`; use a branch-named ignored
local handoff only when multiple local branches need simultaneous state.

Durable lane/readiness state lives in
`docs/superpowers/validation/productization_status_index_v1.tsv`; writer scope
lives in `docs/superpowers/schemas/productization_authority_manifest.v1.json`;
promotion requirements live in `docs/agent/product-validation-contract.md`.
The dated productization control-plane path is compatibility lookup only.

## Product State

- The default product tier remains `product_ready_default_matrix_activated`.
- `backfill_current_write_ready_scope` remains the existing 511-cell Backfill
  authority from #88.
- `cid_nl_default_product_activation_v1` remains the 95-cell CID-NL Discovery
  authority from #95.
- `backfill_expansion_clean_target_selective_product_activation_v1` remains the
  bounded 84-cell Backfill expansion authority from #96.
- Broad Backfill auto-write remains parked.
- Row-completion confidence product-gate mode remains a non-mutating
  `shadow_ready` gate only. It does not change matrix values, workbook/GUI
  behavior, selected peak, selected area, counted detection, ProductWriter
  authority, Backfill authority, active lane, default preset behavior, or
  persisted identifiers.

## Boundary Decisions

- Do not expand CID-NL beyond 95 cells without a new expected-diff and
  authority update.
- Do not grant broad Backfill auto-write without explicit authority.
- Keep row-completion confidence evidence non-mutating unless a future
  activation contract changes the status index and authority manifest through
  the promotion contract.
- Any CI red must be diagnosed from logs and stack boundary first, then fixed at
  the owner boundary.

## Status Index Anchors

Retain these anchor phrases for productization state checks:

- `product_ready_default_matrix_activated`
- `CID-NL default product activation v1`
- `Backfill Expansion Default Product Activation v1`
- `Backfill Expansion Full Evidence Chain v1`
- `Backfill Expansion Clean-Target Selective Default Activation v1`
- Broad Backfill auto-write remains parked
- Goal 0/1 hardening added
- machine-adjudicated without granting new writer authority
- Goal 2 added Review Packet / Approval Workflow v1
- lockbox_shadow_automation_experiment_v1
- Goal 4 added Missing-Overlay Evidence Recovery v1
- keep only as explanation/triage
- Targeted MS1 shape identity limited rescue remains production-ready
- GUI and broader targets remain blocked
- `sample_metadata_v1` remains production-ready for no-output ordering
- roles/batch/matrix/exclusion must not alter quant output
- ReviewAction selected-candidate switch and manual-boundary area recompute remain parked
- manual-boundary area recompute remain parked
- classification and planning only
