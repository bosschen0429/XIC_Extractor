---
status: accepted
---

Doc placement: repo_support_doc
Repo owner: docs/architecture-contract.md

# Separate CrossSample, ModeTrack, and ObservationRepair authority

CrossSample is the sole owner of group identity, membership, edge eligibility,
disposition, and lineage. ModeTrack deepens the existing
`identity_coherence` capability but remains proposal-only, and
ObservationRepair is one independent, traceable repair epoch. This separation
keeps identity gates, trajectory refinement, and evidence recovery from
becoming competing authorities.

## Considered options

- Let ModeTrack mint groups or reopen hard-excluded edges. Rejected because it
  creates a second grouping and identity authority.
- Apply refinement one owner or group at a time. Rejected because reassignment,
  split, and merge can then depend on processing order or update only one side
  of a coupled decision.
- Let every accepted repair become an anchor. Rejected because model agreement
  would be repackaged as independent evidence.

## Consequences

- CrossSample classifies typed candidate edges as `eligible`, `review_only`, or
  `excluded`. Only `eligible` edges form a ModeTrack neighborhood, and
  ModeTrack cannot overturn `excluded`.
- A bounded connected neighborhood is the atomic refinement unit. ModeTrack
  evaluates all coupled actions together; CrossSample either mints every valid
  successor with complete lineage or applies none. An oversized neighborhood
  fails closed as unassessable; it is not split, truncated, or processed
  greedily to fit a budget.
- One execution permits at most one ObservationRepair epoch and one final
  ModeTrack rerun when trajectory identity changes. There is no recursive
  repair/refit loop.
- In a later execution, a repair-derived observation becomes anchor-eligible
  only if the active method-specific evidence owner independently classifies
  its evidence as sufficient. Repair provenance remains attached.

Current contracts: `docs/architecture-contract.md`,
`docs/agent/architecture-public-contracts.md`, and
`docs/product/shared-processing-kernel.md`. Delivery tickets: GitHub #302,
#303, and #304.
