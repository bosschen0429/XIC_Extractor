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
| Shared Processing Kernel | [`shared-processing-kernel.md`](shared-processing-kernel.md) | Frozen workflow-neutral capture, CrossSample, ModeTrack, ObservationRevisit, cell-decision, final-result, and Targeted/Untargeted projection seams |
| Untargeted Method | [`untargeted-method.md`](untargeted-method.md) | Durable method boundary for untargeted LC-MS recovery, hygiene, and historical report absorption |
| Alignment | [`alignment.md`](alignment.md) | Current coupling problem and frozen CrossSample/ModeTrack/ObservationRevisit/final-result owner contract |
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
| Backfill | [`backfill.md`](backfill.md) | Bounded owner-backed ObservationRevisit; explicitly separate from membership, selected value, and writer authority |
| Run Provenance | [`run-provenance.md`](run-provenance.md) | Method manifests, run metadata, replay context |
| Processing Lineage | [`processing-lineage.md`](processing-lineage.md) | Stable request/attempt/artifact/delivery identity, plot-ready retention, retry/supersession, and two-phase delivery sealing |
| Execution Runtime | [`execution-runtime.md`](execution-runtime.md) | Attempt scheduling, cancellation, retry/resume, cleanup, cache lifecycle, failure aggregation, and runtime observability |

### Quality and Review

| Topic | Start here | Scope |
| --- | --- | --- |
| XIC Inspector | [`xic-inspector.md`](xic-inspector.md) | Installed read-only review CLI/API, method-neutral selection and rendering, and portable offline evidence workbench |
| Review Roundtrip | [`review-roundtrip.md`](review-roundtrip.md) | Review action import, lockbox evidence, expected-diff gates |
| Sample Metadata And QC | [`sample-metadata-qc.md`](sample-metadata-qc.md) | Sample identity/order/role/QC, activation gates |
| Instrument QC And Calibration | [`instrument-qc-calibration.md`](instrument-qc-calibration.md) | Clean-vs-biological QC, HCD audit, calibration readiness |

### System

| Topic | Start here | Scope |
| --- | --- | --- |
| Presets | [`presets.md`](presets.md) | Resolver and alignment preset behavior, reproducibility rules |
| Productization | [`productization.md`](productization.md) | Capability map, promotion boundaries, authority routing |
| Untargeted GUI | [`untargeted-gui.md`](untargeted-gui.md) | Discovery workspace, run modes, local config, bundle resources |
| Readers Index | [`readers-index.html`](readers-index.html) | Human-facing HTML route to the topics linked from the core modules guide |
| Core Modules Explainer | [`core-modules-explainer.html`](core-modules-explainer.html) | User-readable map of the shared core, Targeted/Untargeted entry and delivery, supporting modules, and read-only review |
| Processing Guide | [`processing-guide.html`](processing-guide.html) | Human-readable explanation of Discovery, shared peak/alignment/Backfill, and matrix decisions |
| System Guide | [`system-guide.html`](system-guide.html) | Human-readable architecture, execution lifecycle, and completed-evidence review |
| RAW-To-Final Matrix Story | [`raw-to-final-matrix-product-story.html`](raw-to-final-matrix-product-story.html) | Human-readable product explainer for RAW/discovery/alignment/matrix handoff |

## Authority Boundary

Topic pages summarize durable rules but do not replace their upstream owners.
If a topic file conflicts with an owner listed below, the owner wins.

- [LC-MS/MS evidence rules](../lc-msms-evidence-rules.md)
- [Architecture contract](../architecture-contract.md)
- [Productization status index](../superpowers/validation/productization_status_index.tsv)
- [Productization authority manifest](../superpowers/schemas/productization_authority_manifest.json)
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
- Human-readable HTML can stay here only as an explainer. If it conflicts with
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

## Writing Standard

Topic pages follow plain-language principles (ISO 24495-1) and a light subset
of controlled-language rules. The reader must find the rule, understand it,
and act on it after one read. A rewrite for clarity must not change a claim;
if a rewrite would change a claim, it is a contract change and follows the
update triggers above.

### Sentence rules

1. **One claim per sentence.** Split a sentence that states two independent
   rules. A rule with its condition or exception (`unless`, `until`, `only
   when`) is one claim; keep it in one sentence. An `X, not Y` contrast is
   also one claim.
2. **Name the subject.** Each rule names the owner that acts: `CrossSample
   owns membership.` not `Membership is owned.` Use the passive voice only
   when the actor is unknown or irrelevant.
3. **Subject, action, object.** Put the actor first, then the verb, then the
   thing acted on. Put conditions before the rule: `If S < K, Backfill does not
   start.`
4. **No ambiguous pronouns.** Do not start a sentence with `This`, `That`,
   `It`, `These`, `Those`, or `They` unless the noun follows (`This route`,
   `That receipt`).
   Repeat the noun when the referent is more than one sentence back.
5. **Rule words.** Use `must` / `must not` for a contract, `does` / `does not`
   for current behavior, and `can` for a permitted but optional behavior. Do
   not use `should`, `may`, or `ideally` for a contract.
6. **Short sentences.** Aim for 25 words or fewer. A longer sentence is
   acceptable only for one list-like chain that a table cannot hold.
7. **Negatives are explicit.** State what an owner does not do as a separate
   sentence. Name the owner that does it instead when that owner is known. A
   `Does not own:` list in `## Boundaries` is exempt; `## See Also` routes
   those owners.

### Vocabulary rules

1. **One word, one meaning.** Use the terms in the
   [Domain Glossary](domain-glossary.md). Do not introduce a synonym for a
   glossary term (for example `rescue` or `repair` for Backfill).
2. **Stable spelling.** Write a code identifier, file name, field, or label in
   backticks with its exact spelling. Write a product concept in plain words
   with the glossary capitalization (`Backfill`, `CrossSample`).
3. **Workflow names.** Capitalize `Targeted` and `Untargeted` when they name
   a workflow.
4. **Define before use.** A term that is not in the glossary gets a one-line
   definition at its first use, or moves to the glossary if two pages use it.
5. **No filler.** Delete words that do not change the meaning: `currently`
   when the page already describes current behavior, `simply`, `basically`,
   `in order to`.

### Page schema

A topic page uses these sections in this order. Omit a section that has no
content. Keep a page-specific section only when its content fits none of the
standard sections, and place it after `Contract`.

| Section | Content |
| --- | --- |
| Title + purpose | One paragraph: what the topic owns and what it does not own. |
| `## Contract` | Numbered or bulleted rules that code and tests must keep. |
| `## Surfaces` | Outputs, CLIs, files, and fields that expose the topic. |
| `## Boundaries` | What other owners decide; the authority routes. |
| `## Verification` | Tests, fixtures, and gates that prove the contract. |
| `## Pitfalls` | Known mistakes and the correct reading. |
| `## See Also` | Links to owners and neighboring topics. |

Metadata lines (`Doc placement`, `Doc kind`, `Doc lifecycle`, `Repo owner`,
`Doc exit rule`) stay directly under the title when a page has them.
