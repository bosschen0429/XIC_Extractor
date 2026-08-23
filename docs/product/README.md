# Product Topic Index

Reference pages for XIC product behavior and ownership. Audience: maintainers
and code reviewers. For user-facing workflow guides, see
[`docs/user/`](../user/README.md).

Use this index when a GUI workflow, preset, review surface, matrix, or
validation claim needs a durable rule instead of a dated plan or private note.
For definitions of domain-specific terms used in these pages, see the
[XIC Extractor Domain Glossary](domain-glossary.md), the single vocabulary
owner for product documentation.

### Pipeline

| Topic | Start here | Scope |
| --- | --- | --- |
| Discovery | [`discovery.md`](discovery.md) | Shared candidate-discovery stage; broad Untargeted search and constrained Targeted search, candidate/review outputs, seed evidence handoff |
| Shared Processing Kernel | [`shared-processing-kernel.md`](shared-processing-kernel.md) | Frozen workflow-neutral capture, CrossSample, ModeTrack, ObservationRepair, cell-decision, final-result, and Targeted/Untargeted projection seams |
| Untargeted Method | [`untargeted-method.md`](untargeted-method.md) | Durable method boundary for untargeted LC-MS recovery, hygiene, and historical report absorption |
| Alignment | [`alignment.md`](alignment.md) | Current coupling problem and frozen CrossSample/ModeTrack/ObservationRepair/final-result owner contract |
| Targeted Selection | [`targeted-selection.md`](targeted-selection.md) | Targeted behavior, selected hypotheses, region/boundary gates, reasons |
| Decision Policy | [`decision-policy.md`](decision-policy.md) | Typed evidence, workflow gates, score boundaries, and projection authority |
| Peak Model Selection | [`peak-model-selection.md`](peak-model-selection.md) | Model-selection invariants, parity requirements, expected-diff gates |
| Quantitation Context | [`quantitation-context.md`](quantitation-context.md) | Bounded trace context, morphology evidence, area-impacting gates |
| Signal Processing Rationale | [`signal-processing-rationale.md`](signal-processing-rationale.md) | Why smoothed-everywhere: noise regime, stability vs precision trade-off, design exceptions |

### Data Products

| Topic | Start here | Scope |
| --- | --- | --- |
| Quant Matrix | [`quant-matrix.md`](quant-matrix.md) | Product-facing matrix values, Backfill activation boundaries, authority routing |
| Evidence Spine | [`evidence-spine.md`](evidence-spine.md) | Shared evidence carriers, peak hypotheses, projection boundaries |
| Vendor RAW Evidence Model | [`vendor-raw-evidence-model.md`](vendor-raw-evidence-model.md) | Qual Browser-derived RAW evidence-query concepts, scan-filter lineage, trace-type boundaries |
| Peak Anchor And Group Boundary | [`family-hypothesis-boundary.md`](family-hypothesis-boundary.md) | Discovery peak anchors, cross-sample groups, PeakHypothesis, and projection authority |
| Backfill | [`backfill.md`](backfill.md) | Bounded owner-backed ObservationRepair; explicitly separate from membership, selected value, and writer authority |
| Run Provenance | [`run-provenance.md`](run-provenance.md) | Method manifests, run metadata, replay context |
| Processing Lineage | [`processing-lineage.md`](processing-lineage.md) | Stable request/attempt/artifact/delivery identity, plot-ready retention, retry/supersession, and two-phase delivery sealing |
| Execution Runtime | [`execution-runtime.md`](execution-runtime.md) | Attempt scheduling, cancellation, retry/resume, cleanup, cache lifecycle, failure aggregation, and runtime observability |

### Quality and Review

| Topic | Start here | Scope |
| --- | --- | --- |
| Review Roundtrip | [`review-roundtrip.md`](review-roundtrip.md) | Review action import, lockbox evidence, expected-diff gates |
| Sample Metadata And QC | [`sample-metadata-qc.md`](sample-metadata-qc.md) | Sample identity/order/role/QC, activation gates |
| Instrument QC And Calibration | [`instrument-qc-calibration.md`](instrument-qc-calibration.md) | Clean-vs-biological QC, HCD audit, calibration readiness |

### System

| Topic | Start here | Scope |
| --- | --- | --- |
| Presets | [`presets.md`](presets.md) | Resolver and alignment preset behavior, reproducibility rules |
| Productization | [`productization.md`](productization.md) | Capability map, promotion boundaries, authority routing |
| Untargeted GUI | [`untargeted-gui.md`](untargeted-gui.md) | Discovery workspace, run modes, local config, bundle resources |
| RAW-To-Final Matrix Story | [`raw-to-final-matrix-product-story.html`](raw-to-final-matrix-product-story.html) | Human-readable product explainer for RAW/discovery/alignment/matrix handoff |

## Authority Boundary

Topic pages summarize durable rules but do not replace their upstream owners.
If a topic file conflicts with an owner listed below, the owner wins.

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
- [Productization status index](../superpowers/validation/productization_status_index_v1.tsv)
- [Productization authority manifest](../superpowers/schemas/productization_authority_manifest.v1.json)
- [Product validation contract](../agent/product-validation-contract.md)

## Contributor Rules

- Add a topic page only when durable public rules need a small entry point.
  Do not add pages to preserve branch diaries or private review threads.
- Use a lean reference-page style: one-paragraph purpose, tables, concise
  contract rules, and links to owners. Avoid repeating shared governance in
  every file — put it here instead.
- Do not copy dated plans, private investigation detail, local paths, or
  command transcripts into this directory.
- Topic pages do not change maturity tier, active lane, writer authority,
  matrix behavior, schema, or validation verdicts.
- Product-story HTML can stay here only as an explainer. If it conflicts with
  topic pages, status index, authority manifest, or product validation
  contract, those owners win.

### Shared Conventions

These apply to all topic pages and do not need to be restated in each file:

- **Validation evidence status** uses the Evidence labels `diagnostic_only`,
  `shadow_ready`, `production_candidate`, `production_ready`, and
  `inconclusive` to describe what a source-qualified branch, run, or evidence
  packet verified. `inconclusive` means the required evidence did not establish
  a stronger claim; it grants no authority. Evidence labels are not inherent
  properties of a topic page and are not persisted lane state in the
  productization status index.
- **ProductWriter authority** is the only path that writes product matrices.
  Sidecars, review surfaces, galleries, and diagnostics can explain or gate a
  decision, but they do not write product values unless the authority manifest
  explicitly grants that role.
- **Cleanup policy**: dated plans, command transcripts, and sample-level
  investigation can move to private notes after their stable public claims
  are represented in the relevant topic page or source owner.
- **Update triggers**: update a topic page when it gains or changes a durable
  output, contract, boundary, or verification gate. If the change affects
  product authority, update the owning spec and tests first.
