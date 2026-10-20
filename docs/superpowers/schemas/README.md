# Productization Schemas

Doc placement: repo_support_doc
Doc kind: manifest
Doc lifecycle: active
Repo owner: docs/project-layout.md
Doc exit rule: Retire or rewrite this index after schema contracts are absorbed into a generated registry or replaced by a narrower owner.

Status: routing index

Schemas define machine-checkable JSON contracts and formal validation shapes.
They are not automatic implementation goals, validation result buckets,
review-story galleries, design specs, or historical design archives.

Historical or visual design context belongs in Obsidian or a named validation
artifact lane after its durable product claim is represented in `docs/product/`
or the relevant structured productization owner.

## Active Reading Order

1. Productization status index for maturity/lane state, authority manifest for
   writer scope, structured control schema for allowed keys, and product
   validation contract for promotion semantics.
2. Current handoff for continuation state.
3. Current roadmap/blueprint for phase order.
4. The specific schema named by the active goal.

## Current Public Schemas

Current ProductWriter state lives in the
[writer manifest](../productization/status/productization_authority_manifest.json);
this authority instance is not a schema.

- `productization_control_plane_schema.json`: current control-plane schema
  for productization lane state, maturity, authority rules, and the stable
  compatibility-anchor registry.

The [Matrix publication contract](../../product/quant-matrix.md#matrix-publication)
describes current numeric publication and its decision owners.

## Rule

If a schema conflicts with its structured state/authority owner, the current
handoff, or the current Backfill quant-matrix product blueprint, stop and
resolve the conflict instead of silently following the older schema.
