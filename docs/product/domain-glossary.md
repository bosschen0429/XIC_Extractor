# XIC Extractor Domain Glossary

Doc placement: formal_repo_doc
Doc kind: product_doc
Doc lifecycle: active
Repo owner: docs/product/domain-glossary.md
Doc exit rule: Update when stable vocabulary, lane boundaries, or authority concepts change.

This file is the single stable vocabulary owner for XIC Extractor. Internal
type names belong in their module docstrings; volatile status, counts and
blockers belong in Issues and PRs.

## Product Shape

XIC Extractor is an LC-MS evidence and model-selection system. Peak choice,
integration, review and export decisions must be traceable through evidence
rather than through dataset-specific shortcuts.

The three core stages are **Discovery → Alignment → Backfill（回填）**.
Backfill is the bounded revisit of a missing cell under an existing Target or
group; cells with sufficient observations need no revisit.

XIC has one shared scientific lifecycle. Untargeted extraction is its general
application; Targeted extraction is a constrained application, not a second
core:

1. Discovery providers interpret method-specific inputs and produce
   sample-local observations and peak hypotheses.
2. CrossSample grouping decides group membership, identity, disposition and
   lineage.
3. Optional bounded Backfill revisits missing cells of an established Target
   or group. It can return the first usable observation for a cell, but cannot
   originate a Target or group, mint membership, or select a value.
4. The cell decision selects one value or typed no-write answer per cell.
5. The workflow projection owns row inclusion, counts, reasons, review routing
   and delivery shape.

- Both applications can use a broad candidate-discovery policy. `broad`
  describes which evidence relationships a method inspects, such as dR, R or
  MeR; it does not mean Untargeted-only.
- Untargeted searches the configured evidence space, retains all qualifying
  groups, and projects established groups into the feature-by-sample Matrix for
  downstream preprocessing, calibration and statistics.
- Targeted supplies an exact target list. It retains one fixed output row per
  target and discards unrelated groups from that delivery. It does not need a
  second capture, grouping, Backfill or cell-decision core.

Shared does not mean method-blind: downstream owners can use typed provider
provenance or an explicit provider strategy when the science requires it.
Evidence or output authority in one workflow does not grant write authority in
the other without an explicit contract, expected-diff evidence and focused
tests.

## Evidence Terms

- `Trace`: intensity over retention time for a feature, target or candidate.
- `PeakHypothesis`: a proposed peak identity, RT window and integration basis.
- `selected hypothesis`: the chosen `PeakHypothesis` and its audit context.
  Selection identifies the candidate a projection uses; it does not by itself
  grant counted detection or writer authority.
- `EvidenceVector`: structured evidence used for scoring or model selection.
- `IntegrationResult`: selected boundaries, area and integration diagnostics.
- `DecisionRecord`: audit record of how evidence, gates, tie-breaks and
  projection authority produced a decision. It is not an LC-MS `Trace` and not
  analytical evidence.
- `cross-sample group`: one cross-sample feature hypothesis, identified by its
  members. A member is one canonical sample plus one peak hypothesis; a group
  holds at most one member per sample.
- `group disposition`: exactly one of `established`, `provisional`,
  `unresolved` or `rejected`. Disposition does not decide output inclusion.
- `CID-NL method subject`: the exact acquisition/assessment question retained
  in an assessment. It is not a cross-sample feature or a Matrix row.
- `CID-NL alignment workset`: compatible method subjects scheduled together so
  their observations reach the same grouping decision. Workset membership is
  computational scope, not chemical identity; see [Alignment](alignment.md).
- `ProductionCellDecision`: the cell decision's value or typed no-write, with
  its selected integration and provenance. Observations do not select delivered
  values themselves.
- `workflow projection`: the owner of output row/cell identity, inclusion,
  counts, reasons and review routing. It never selects the cell value.
- `feature_id`: in Discovery output, a per-sample label for candidates sharing
  one MS1 peak; in Alignment output, the report-scoped `F000001` label of one
  published group. Neither carries identity across reports.
- `final Matrix`: the immutable grid produced after cell and row-inclusion
  decisions close. Pre-final collections use other names and cannot cross the
  Matrix interface.
- `ProductWriter`: the exclusive approved code path for product-matrix
  mutations; see [Writer Authority](decision-policy.md#writer-authority).
- `Discovery record`: a retained sample-local observation and its
  qualification. A row in the Discovery candidate CSV is not by itself a feature
  for cross-sample grouping.
- `Alignment feature candidate`: a Discovery observation with eligible MS1
  feature support. An NL match alone does not grant that standing.
- `review-only NL observation`: NL evidence without eligible MS1 feature
  standing. `review_only_orphan_nl` is not proof of random noise.
- `seed evidence`: initial MS1/MS2 evidence that triggers candidate generation.
  A seed is not same-peak proof or counted detection.
- `family`: legacy review/search container for related evidence. Family
  membership is not same-peak proof or product authority.
- `morphology`: peak-shape characteristics such as width, symmetry, boundaries
  and quality flags used as selection or integration evidence.
- `sidecar`: accompanying TSV, JSON or similar metadata that explains or audits
  a primary output.
- `provenance`: the inputs, effective settings, runtime and decision path that
  identify how an artifact or decision was produced.
- `evidence-chain packet`: source evidence assembled for review or approval.
  The packet is evidence, not approval.
- `RAW-tier evidence label`: the actual fixture or real-sample review used for
  a claim. CID-NL 8RAW/85RAW fixtures are not universal gates for other methods.

Evidence providers include CID-NL, HCD-PI, Delta Mass, RT/iRT, MS1 shape,
standards, library matches, trace overlays, review labels and future models.
`delta_mass` is the Discovery strategy family for MS1 mass-difference
patterns; `pt` / `Pt pattern` is its first concrete pattern; `pt_pattern` is a
method or preset label. Composites such as `pt_pattern + cid_nl(dR)` intersect
providers and do not become product authority by themselves.

### Missing, absent and failed

Keep these facts on separate axes:

- Missing evidence, a failed attempt or a missing artifact never becomes
  scientific `absent` or numeric zero.
- `missing` means no completed scientific evaluation; `absent` means a completed
  evaluation found no supported hypothesis.
- `unassessable` is a reviewed evidence conclusion, not a pending-review status.
- Staleness is a relationship between an immutable artifact and the request or
  input now being evaluated; an artifact is never mutated to call it fresh.

## LC-MS/MS Terms

- `XIC`: extracted-ion chromatogram, signal intensity over time for one m/z
  window.
- `CID-NL`: Collision-Induced Dissociation Neutral Loss, a fragmentation pattern
  used as Discovery seed evidence.
- `Delta Mass`: Discovery strategy family for configured MS1 mass-difference
  patterns.
- `Pt pattern`: the first built-in `delta_mass` pattern for Pt-related MS1
  features. Its provisional parameters produce diagnostic candidates.
- `HCD`: Higher-energy Collision Dissociation. Its product-ion audit is a review
  surface unless a contract grants stronger meaning.
- `DDA`: Data-Dependent Acquisition, where MS2 triggering depends on MS1
  observations and instrument acquisition policy.
- `Batch RT warp`: a frozen per-injection RT mapping fitted from agreed anchors
  across the whole request, with residual neighbours restricted to the declared
  acquisition batch. Unsupported calibration is not measured zero drift.
- `Local feature RT trend`: the RT of one feature estimated from nearby
  injections, distinct from the shared sample warp.
- `Backfill query RT`: the target sample's request center. It controls
  extraction bounds and preferred peak location; it does not replace the
  group's `group_center_rt`.
- `ISTD`: internal standard, a spiked reference used for RT calibration,
  paired-area context and method QC.
- `NL`: neutral loss, the mass difference between precursor and product ion.
- `sample matrix`: the chemical background of a sample, such as a solvent
  standard or tissue extract. Do not shorten it to `matrix`; `Matrix` is the
  product output.
- `SDO/LEK`: the lab's two-standard instrument-check set. Code spells it
  `sdolek`.

## Product Authority Terms

- `candidate/audit universe`: rows visible for review, audit or diagnosis; not
  writable product output.
- `authority`: the permission boundary for Matrix, workbook or value writes;
  [Writer Authority](decision-policy.md#writer-authority) owns how it is granted.
- `validation evidence status`: a source-qualified claim about what a run or
  evidence packet verified. Its Evidence labels are defined in the
  [Product Topic Index](README.md#shared-conventions).
- `activation contract`: the evidence, tests, authority and expected-diff
  conditions required before diagnostic or shadow behavior can affect values.
- `parity`: a replacement preserves the declared observable contract unless an
  explicit expected diff authorizes a change.
- `adjudication`: machine classification of a candidate. It routes work but is
  not permission to write output.
- `review packet`: a structured human-review surface, not free-form value entry.
- `truth lockbox` (`lockbox`): independent peak-choice or area labels used to
  evaluate automation. Lockbox labels never mutate product output.
- `expected-diff`: explicit evidence that a public output change is intended,
  bounded and test-covered.

Diagnostics, sidecars and reports are observability unless an authority
contract says otherwise. Public output surfaces include CLI flags, config keys,
TSV/CSV schemas, workbook sheets, run metadata, Matrix identity, selected peak,
selected area and counted detection.

## Lane Boundaries

Targeted lane:

- Target lists and method settings constrain the shared lifecycle; they do not
  create a separate peak-identity or Backfill system.
- The Targeted workbook is its own delivery contract. It does not require an
  analytical downstream reader when no such consumer exists.
- Limited Targeted rescue produces `detected_flagged` only where explicitly
  contracted. It does not expand to new analytes or default extraction behavior
  without expected-diff evidence and a product decision.

Untargeted lane:

- The feature-by-sample Matrix is the downstream analytical handoff. Its sample
  identity and order can share method-derived metadata with Targeted delivery
  without sharing its output schema.
- Backfill stays bounded and owner-backed. Only the cell decision and the
  owning projection authorize a delivered value.

Shared lane:

- Sample metadata, QC roles, blanks, batches and Matrix annotations can be shown
  as metadata. They never change quantitative output or counted detection
  without a separate expected-diff gate.

## One Word, One Meaning

A word earns its place in an identifier when removing it changes the meaning.
New internal names use one word per concept. Persisted spellings below remain
stable because they identify existing data or public interfaces.

| Word | Meaning | Not this word |
| --- | --- | --- |
| `backfill` | the bounded missing-cell revisit stage; each workflow's missing cell is defined in [Backfill](backfill.md) | — |
| `Matrix` | the finalized product-facing numeric feature-by-sample grid; see [Quant Matrix](quant-matrix.md) | a sample matrix; a pre-final cell collection |
| `sample matrix` | the chemical background of a sample (clean standard, biological extract) | the product `Matrix` |
| `SDO/LEK` | the lab's two-standard instrument-check set (SDO and LEK); `sdolek` in code | `SDOLEK` in prose |
| `revisit` | one context-directed Backfill attempt, represented by the retained `ObservationRevisit*` receipt types | a repair of a previously observed peak |
| `recovery` | resuming or closing a run after a runtime failure | Backfill; candidate search; oracle recall |
| `relaxed search` | the peak-detection search near a preferred RT with relaxed thresholds (`peak_detection/relaxed_search.py`) | — |
| `recall` | the fraction of oracle reference compounds a run finds | — |
| `Workbench` | the daily HTML application (`xic_workbench/`) where users set up, run, and review analyses; see [Workbench](workbench.md) | a second review program |
| `Inspector` | the shared review view (`xic_inspector/`) that Workbench embeds and offline HTML reports reuse | a separate application to launch |

The `recovery`, `repair`, and `rescue` words must not describe Backfill in
prose either. Use them only in the senses in this table or in the preserved
spellings below.

The surviving `owner` senses are the sample-local MS1 peak to which candidates
are attributed (`SampleLocalMS1Owner`, `owner_apex_rt`), processing authority
(`processing_owners`, `capture_owner`), and module responsibility.

`physical` means the MS1-measured ion or chromatographic peak itself, as opposed
to CID-NL/MS2 evidence about it. It never implies chemical identity or CID-NL
support. The word names four views of the same MS1 peak in one sample, so a
name should say which view it means:

| Name in code | Plain meaning | Kind |
| --- | --- | --- |
| physical peak (`PeakObservationRef` content) | the MS1 peak itself | a thing |
| `peak_detection/physical_peak_*` evaluation | finding and judging that peak on its XIC | a step |
| `discovery/physical_fact_*`, `discovery_physical_facts.json` | Discovery's saved copy of that judgement (see [Discovery](discovery.md)) | a record |
| `physical_mz` | the measured MS1 mass used to extract the peak; not the precursor key that replays MS2 evidence | a number |

Persisted and public exceptions retain their exact spelling:

| Spelling | Why it remains |
| --- | --- |
| `preferred_rt_recovery` | Existing proposal-source label |
| `owner_backfill_xic_backend`, `owner_backfill_window_strategy`, `owner_backfill_superwindow_span_factor`, `owner_backfill_min_detected_samples` | Existing config and run-metadata keys |
| `owner_backfill_peak_fact_provenance_row_count`, `owner_backfill_peak_fact_provenance_tsv`, `owner_backfill_evaluation_coverage` | Metadata and public run/output fields |
| `owner_finalized_cell_decision`, `owner_finalized`, `owner_rehydrated` and their longer diagnostic forms | Existing metadata, decision, and diagnostic values |
| `alignment_owner_backfill_*`, `owner_backfill_revisit`, `alignment.owner_backfill` | Existing artifact names, source labels, and timing stage |
| `rescue_quantifiable`, `rescued_cell_*`, `cross_mode_rescue_*` | Existing quality, diagnostic TSV/JSON, and decision-record keys |
| `ms1_feature_nl_rescued` | Discovery NL evidence state, not a Backfill cell status |

The MS2 boundary search's `boundary_rescue` names a distinct peak-detection
fallback and stays outside Backfill vocabulary.

## Maintenance

Update this file when a stable domain term or authority concept changes, not for
validation results, counts or temporary blockers. Do not create another
hand-maintained product glossary; a reader-specific glossary belongs under
`docs/user/` and links back here.
