# Productization

Productization is the stable capability map and authority router for XIC product
surfaces. It explains which owner answers which question; it does not repeat the
mutable lane board, current accepted counts, writer scopes, or promotion
verdicts.

## Authority Model

| Question | Exclusive authority |
| --- | --- |
| Current per-lane readiness, owner, artifact, product effect, public surface, and next gate | [Productization status index](../superpowers/validation/productization_status_index_v1.tsv) |
| ProductWriter scope and current authorized counts | [Productization authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json) |
| Dependent canonical quantitative-effect status and package/activation binding | [Quantitative projection authority manifest](../superpowers/schemas/quantitative_projection_authority_manifest.v1.json) |
| Structured fields, required lane keys, allowed status values, stable compatibility anchors, writer rules, and dependent-effect rules | [Productization control-plane schema](../superpowers/schemas/productization_control_plane_schema.v3.json) |
| Readiness vocabulary, promotion semantics, and promotion-packet requirements | [Product validation contract](../agent/product-validation-contract.md) |
| Stable capability meaning and public boundaries | Matching `docs/product/*.md` topic owner |
| Compatibility lookup from historical control-plane anchors | [Productization compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md) |
| Historical chronology and retired reasoning | Git history plus the private source copy keyed by `source_repo_path` |

Narrative owners must not restate mutable status-index fields or authority-
manifest values. The dependent-effect manifest does not extend the registered
ProductWriter scopes. If narrative wording conflicts with an unchanged
structured owner, the structured owner wins and the narrative must be corrected.

## Capability Map

| Area | Stable capability | Canonical topic owner |
| --- | --- | --- |
| Domain evidence | Typed observations, hypotheses, evidence vectors, decisions, integration results, and audit records remain distinct from product projection | [Evidence Spine](evidence-spine.md) |
| Discovery | Method-specific evidence providers create sample-local candidates without directly granting matrix authority | [Discovery](discovery.md) |
| Alignment | Cross-sample correspondence and group hypotheses remain separate from candidate generation and publication | [Alignment](alignment.md) |
| Backfill | Evidence-based recovery reaches product outputs only through an explicit authority and expected-diff path | [Backfill](backfill.md) |
| Quant output | Accepted values, cell provenance, and matrix-version contracts are public output responsibilities | [Quant Matrix](quant-matrix.md) |
| Targeted selection | Configured identity and bounded rescue behavior have their own public and evidence boundaries | [Targeted Selection](targeted-selection.md) |
| Review | Human or automated review stays auditable and cannot silently become writer authority | [Review Roundtrip](review-roundtrip.md) |
| Run replay | Inputs, configuration, versions, and artifacts remain traceable through explicit provenance | [Run Provenance](run-provenance.md) |
| Sample metadata and QC | Sample roles and QC projections are typed inputs, not implicit activation authority | [Sample Metadata and QC](sample-metadata-qc.md) |
| Presets | Reproducible CLI/GUI bundles route behavior but do not create independent product authority | [Presets](presets.md) |

## Boundaries

- **Owns:** stable capability descriptions, topic-owner routing, and the
  field-level authority partition above.
- **Does not own:** current lane state or counts, ProductWriter scopes, active
  WIP, or a specific promotion verdict.
- Validation packets and diagnostics support a decision; they do not become a
  second current-state board.
- The compatibility index preserves stable lookup keys only. It must not grow a
  new maintenance log or duplicate rule prose from canonical owners.

## Promotion Inputs

A product-surface change should route to the promotion contract when it can
change selected values, counted detections, matrix identity, output schema,
CLI/config/GUI behavior, review/replay behavior, or writer authority. The
promotion packet records the proposed behavior delta, required non-changes,
evidence, expected diff, replay, downstream smoke, stop condition, and rollback.

Stable implementation priorities remain:

1. Reuse existing evidence and decision owners before adding a parallel model.
2. Make public behavior and replay inputs explicit before downstream consumers
   depend on them.
3. Keep review, diagnostic, and presentation surfaces outside writer authority
   until an explicit activation contract proves the expected product diff.

## Pitfalls

- Copying status-index rows or authority-manifest counts into narrative docs.
- Treating a dated plan, validation result, sidecar, or report as current
  product authority.
- Adding another summary when the missing piece is schema, replay, review
  import, expected diff, or activation authority.
- Treating a configuration hash as a complete method/replay manifest.

## See Also

- [Product topic index](README.md)
- [Product validation contract](../agent/product-validation-contract.md)
- [Productization compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md)
- [Validation retention](../superpowers/validation/RETENTION.md)
- [Project layout and documentation lifecycle](../project-layout.md)
