# Productization

Productization is the stable capability map and authority router for XIC
product surfaces. This page names the owner that answers each question. This
page does not repeat the mutable lane board, current accepted counts, writer
scopes, or promotion verdicts.

## Authority Model

| Question | Exclusive authority |
| --- | --- |
| Current per-lane readiness, owner, artifact, product effect, public surface, and next gate | [Productization status index](../superpowers/validation/productization_status_index.tsv) |
| ProductWriter scope and current authorized counts | [Productization authority manifest](../superpowers/productization/status/productization_authority_manifest.json) |
| Structured fields, required lane keys, allowed status values, stable compatibility anchors and writer rules | [Productization control-plane schema](../superpowers/schemas/productization_control_plane_schema.json) |
| Readiness vocabulary, promotion semantics, and promotion-packet requirements | [Product validation contract](../agent/product-validation-contract.md) |
| Stable capability meaning and public boundaries | Matching `docs/product/*.md` topic owner |
| Compatibility lookup from historical control-plane anchors | [Productization compatibility index](../superpowers/plans/2026-06-15-productization-control-plane.md) |
| Historical chronology and retired reasoning | Git history plus the private source copy keyed by `source_repo_path` |

Narrative owners must not restate mutable status-index fields or
authority-manifest values. If narrative wording conflicts with an
unchanged structured owner, the structured owner wins. The narrative must then
be corrected.

## Writer Authority

Normal Targeted workbook and Untargeted Matrix publication retain their
existing workflow writers and publication mechanics under
[Writer Transaction Authority](../agent/architecture-public-contracts.md#writer-transaction-authority).
The activation-scope registration requirement below does not gate these normal
publication paths.

For explicit diagnostic or Backfill matrix activations, ProductWriter consumes
only scopes registered in the authority manifest. Activation authority requires
the authority manifest, status index, expected diff, and promotion contract;
the manifest `authority_policy` owns the exact write requirements and the sources
that can never grant authority. A `ProductionAcceptanceManifest`
([checker](../../xic_extractor/diagnostics/quant_matrix/acceptance.py)) can
grant per-cell write authority only when the later activation path binds it to
expected diff, provenance, and the current authority scope.
`QuantMatrixVersion` / default activation outputs are the writer surface.

Evidence providers, candidate rows, `write_ready` labels, quality blockers,
sidecars, replays, review packets, galleries, and diagnostics can explain or
gate a decision, but none independently grants writer authority. Topic pages
link here instead of restating this rule.

## Capability Map

| Area | Stable capability | Canonical topic owner |
| --- | --- | --- |
| Domain evidence | Typed observations, hypotheses, evidence vectors, decisions, integration results, and audit records remain distinct from product projection | [Evidence Spine](evidence-spine.md) |
| Discovery | Method-specific evidence providers create sample-local candidates without directly granting matrix authority | [Discovery](discovery.md) |
| Alignment | Cross-sample correspondence and group hypotheses remain separate from candidate generation and publication | [Alignment](alignment.md) |
| Backfill | An evidence-based Backfill value reaches product outputs only through an explicit authority and expected-diff path | [Backfill](backfill.md) |
| Quant output | Accepted values, cell provenance, and matrix-version contracts are public output responsibilities | [Quant Matrix](quant-matrix.md) |
| Targeted selection | Configured identity and bounded rescue behavior have their own public and evidence boundaries | [Targeted Selection](targeted-selection.md) |
| Review | Human or automated review stays auditable and cannot silently become writer authority | [Review Roundtrip](review-roundtrip.md) |
| Run replay | Inputs, configuration, versions, and artifacts remain traceable through explicit provenance | [Run Provenance](run-provenance.md) |
| Sample metadata and QC | Sample roles and QC projections are typed inputs, not implicit activation authority | [Sample Metadata and QC](sample-metadata-qc.md) |
| Presets | Reproducible CLI/HTML bundles route behavior but do not create independent product authority | [Presets](presets.md) |

## Boundaries

Normal Alignment output remains owned by its live cell-decision and projection
owners.

- **Owns:** stable capability descriptions, topic-owner routing, the
  field-level authority partition, and the writer-authority rule above.
- **Does not own:** current lane state or counts, ProductWriter scopes, active
  WIP, or a specific promotion verdict.
- Validation packets and diagnostics support a decision. Validation packets
  and diagnostics do not become a second current-state board.
- The compatibility index preserves stable lookup keys only. It must not grow a
  new maintenance log or duplicate rule prose from canonical owners.

## CLI And HTML Delivery

CLI and HTML routes target the same application capabilities and scientific
contracts. The local HTML workbench is the primary human surface for analysis
and result review. Script CLIs remain reproducible automation and diagnostic
surfaces. Both routes use the same application and scientific owners.

## Work Intake And Closeout

Each maintenance cycle keeps at most one lane of each kind:

- one primary productization lane;
- one supporting-infrastructure lane;
- one lane dedicated to diagnostics.

When a lane kind is already occupied, a new lane of that kind can start only
after promoting the current lane, parking it with a reopening condition,
externalizing/killing it, or abandoning it.

A new lane names:

- its structured current and intended state;
- its capability owner;
- the affected public surface;
- the expected behavior diff and required non-changes;
- the evidence tier;
- the downstream consumer;
- the stop/rollback rule.

If values or identity can change without an expected-diff plan, intake stops.

Current state comes from the status index and authority manifests. A branch
diary or the compatibility index is not a current-state source. Diagnostics,
reports, and sidecars need a promote, kill, externalize, or missing-evidence
exit.

Closeout does three things:

1. Closeout names the evidence actually used.
2. Closeout resolves only the active branch handoff.
3. Closeout keeps the durable delivery receipt in the PR body. Closeout does
   not create another progress board.

## Promotion Inputs

A product-surface change must route to the
[promotion packet contract](../agent/product-validation-contract.md#promotion-packet-contract)
when the change can alter any of these:

- selected values;
- counted detections;
- matrix identity;
- output schema;
- CLI/config/HTML behavior;
- review/replay behavior;
- writer authority.

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
- [Project layout and documentation lifecycle](../project-layout.md)
