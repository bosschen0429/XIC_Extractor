# Productization Plans

Doc placement: repo_support_doc
Doc kind: plan
Doc lifecycle: active
Repo owner: docs/product/productization.md
Doc exit rule: Update when productization plan routing or active reading order changes.

Status: routing index

This directory contains a mix of active productization documents, current
blueprints, completed implementation plans, and historical provenance. A dated
plan is not active by default.

## Active Reading Order

1. `../validation/productization_status_index_v1.tsv` for current tier/lane,
   `../schemas/productization_authority_manifest.v1.json` for writer scope, and
   `../../agent/product-validation-contract.md` for promotion requirements.
   `2026-06-15-productization-control-plane.md` is compatibility lookup only.
2. The branch-specific current handoff named by the goal or PR workflow for
   continuation state. The file
   `../productization/status/cc-framework-improvements-productization.md` is a
   checker-facing stable lane-key registry, never a branch handoff or mutable
   status board.
3. `2026-06-19-backfill-quant-matrix-productization-roadmap.md` for the current
   Backfill / quant-matrix roadmap direction when that lane is active.
4. `docs/product/backfill.md`, `docs/product/quant-matrix.md`, and the current
   branch handoff for Backfill / quant-matrix phase execution. The old Backfill
   quant-matrix blueprint has been retired to Obsidian and must not be treated
   as an active repo plan.
5. Older dated plans only when the active plan names them as provenance.

## Current Backfill Direction

Backfill values are accepted quantification values, not detections and not truth
claims. The future default quant matrix should include detected plus accepted
Backfill values. Production write authority must come from a
`ProductionAcceptanceManifest`; shadow, report, gallery, and candidate artifacts
remain non-authority. Old plans that discuss broad promotion, sidecar-driven
promotion, or productization gates are historical unless explicitly reactivated.

## Current Pt Pattern Routing

- `2026-07-01-pt-pattern-discovery-implementation-plan.md` is the active
  execution plan for the first Pt pattern Discovery implementation. Use it for
  code work on `codex/pt-pattern-discovery`.
- `2026-07-01-pt-pattern-discovery-baseline.md` is now the refined source
  baseline and provenance surface. Do not use it as the execution checklist
  when the implementation plan is present.

## Rule

Do not treat old implementation checklists as open work. First check the control
plane and current handoff; then create a fresh plan or goal-shaped runtime
contract for any reactivation.

## Lifecycle Closeout

Active execution plans are tracked repo documents because another agent must be
able to resume work without private Obsidian access. They must declare
`Doc placement`, `Doc kind`, `Doc lifecycle`, `Repo owner`, and `Doc exit rule`.

When a dated plan is complete, do not leave the original as permanent residue
and do not delete it by hand. Close it out by:

1. moving durable decisions into the declared repo owner;
2. running product-absorption review and recording `pass_can_retire`;
3. source-copying the original to Obsidian with readback verified;
4. retiring it through `tools/diagnostics/retire_docs.py --evidence <json>`;
5. staging the retirement evidence packet with any lifecycle-managed deletion.

Same-path stubs are temporary exceptions for active continuation or exact
referrers. Normal completed-plan exit is owner absorption plus Obsidian original,
not another long tracked plan.
