# XIC Extractor Productization Roadmap Review

Doc placement: repo_support_doc
Doc kind: plan
Doc lifecycle: active
Repo owner: docs/product/backfill.md; docs/product/quant-matrix.md
Doc exit rule: Retire or move to Obsidian after the Backfill/quant-matrix product docs and structured productization owners fully absorb this roadmap direction.

Updated: 2026-06-19
Status: direction-reset roadmap

## Verdict

The scorer-centered lockbox follow-up is not the product direction.

The product direction is:

```text
Backfill = accepted quantification value for a missing cell
Backfill != detection
Backfill != truth claim
default quant matrix = detected + accepted Backfill
write authority = ProductionAcceptanceManifest only
```

Use the Backfill/quant-matrix product docs plus the status index, authority
manifest, and promotion contract as the authoritative roadmap. The retired
Backfill quant-matrix blueprint and
the 2026-06-18 evidence-lifecycle blueprint are superseded and retained only as
Obsidian/source-history inputs.

## Read First

Use this roadmap with:

- [Backfill product doc](../../product/backfill.md)
- [Quant matrix product doc](../../product/quant-matrix.md)
- [historical compatibility index](../plans/2026-06-15-productization-control-plane.md)
- [productization compatibility lane registry](../productization/status/cc-framework-improvements-productization.md)
- [Untargeted Method](../../product/untargeted-method.md)

The status index remains the authority for current tier/lane; the authority
manifest owns the current writer surface; the promotion contract owns promotion
requirements. This roadmap records direction and phase order. Deepresearch
notes are background inputs and cannot grant write authority.

## Current Baseline

- Current Backfill product authority remains exactly 511 cells.
- Broad Backfill auto-write remains parked.
- `4613` remains the candidate/audit universe, not a writable pool.
- `3015` trace-matched unresolved rows remain review/adjudication targets, not
  writer rows.
- `1087` missing-overlay rows remain evidence gaps, not negative truth.
- Lockbox/owner-clean evidence remains non-authoritative challenge evidence.
- Manual wrong-peak/no-peak controls remain negative controls.
- Phase 0/1 do not change ProductWriter, matrix, workbook, selected peak/area,
  counted detection, GUI, default extraction, or broad Backfill authority.

Important nuance: parked broad Backfill means broad uncontracted writer
behavior is parked. Contracted accepted Backfill is the product direction.

## Dismantled Direction

Remove these as roadmap goals:

- scorer output schema as durable product authority;
- scorer metrics or stop-rule JSON as the next productization asset;
- lockbox scorer run over the included cases;
- scorer review report;
- scorer decision packet;
- any path where `accept/flag/reject` hides the evidence chain or grants write
  authority.

The lockbox package remains useful as shadow/review substrate. It must not
become a scoring-centered product lane.

## Product Spine

```text
source trace / integration artifact
-> PeakHypothesis
-> CellBackfillDecision
-> ProductionAcceptanceManifest
-> QuantMatrixVersion
-> Gallery/Report + CellProvenance sidecar
```

The product key for Backfill acceptance is `peak_hypothesis_id + sample_stem`.
`feature_family_id` is context/provenance only.

## Roadmap

Detailed entry gates, outputs, verification commands, done conditions, stop
rules, handoff requirements, and goal seeds live in the blueprint. This roadmap
only names phase order and product intent.

### Phase 0 - Blueprint And Cleanup Map

Objective:

Make the new Backfill/quant-matrix blueprint the active source of truth and
classify conflicting docs/code/tests as `delete`, `downgrade`, `adapt`, or
`keep`.

Done when:

- active routing docs point to the 2026-06-19 blueprint;
- Phase 0 handoff points to Phase 1 Shadow Adapter v1, and the active
  productization or branch-scoped handoff advances to Phase 2 after Phase 1
  closes;
- old 2026-06-18 blueprint is superseded;
- cleanup map exists;
- no structured status/authority update is needed unless tier/lane/authority actually changed.

### Phase 1 - Shadow Scoring Contract Adapter v1

Objective:

Contain shadow/scoring artifacts so they cannot become truth, reviewer slot
completion, or write authority.

Must preserve:

- one 72-case source: `lockbox_shadow_automation_cases_v1.tsv`;
- `shadow_only=true`;
- `write_authority=false`;
- no scorer run;
- no RAW/85RAW;
- no ProductWriter/matrix/workbook/default extraction changes.

Readiness label: `shadow_ready` only.

### Phase 2 - ProductionAcceptanceManifest v1

Objective:

Define and check the only Backfill artifact that can grant
`write_authority=true`. This phase does not write the default matrix yet.

### Phase 3 - QuantMatrixVersion Activation

Objective:

Use the production acceptance manifest to generate the default numeric
`quant_matrix` with detected plus accepted Backfill values, plus
`cell_provenance` and `row_summary`.

### Phase 4 - Gallery/Report Alignment

Objective:

Align the existing gallery/report with the manifest and sidecars. Do not rebuild
the gallery unless the contract requires it.

### Phase 5 - Validation And Promotion

Objective:

Separate contract correctness from scientific confidence. Synthetic/focused
tests prove schema and authority invariants. Real cohort/oracle evidence is
needed before production-ready claims.

## Stop Rules

Stop if a proposed goal:

- reintroduces composite scoring or score weights as write authority;
- treats Backfill as detection or truth claim;
- grants write authority from shadow/report/gallery/review artifacts;
- treats owner-clean, reviewer slot 2, or single-owner evidence as truth
  completion;
- creates a second independent lockbox case manifest;
- writes matrix output before a production acceptance manifest and expected-diff
  contract exist;
- hides detected/backfilled/manual/missing/rejected provenance;
- runs RAW/85RAW without the active phase naming that validation tier.

## Structured Productization State

No status-index or authority-manifest update is needed for Phase 0 because current maturity,
active lane, writer authority, selected values, counting behavior, ProductWriter
authority, and broad uncontracted Backfill state do not change.
