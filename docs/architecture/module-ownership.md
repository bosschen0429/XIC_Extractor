# Module Ownership

Which package or module owns a behavior. Model fields, exports and
construction sites are in source; query CBM before moving an owner rather than
expanding these tables.

## Packages

| Surface | Owns | Does not own |
| --- | --- | --- |
| `configuration/` | settings, target/config loading, validation, typed runtime settings | extraction or rendering |
| `applications/targeted/` | Targeted composition, request/result interface, input binding and publication | peak selection, cell decisions, RAW acquisition or workbook formatting |
| `applications/untargeted/` | complete method routing, frozen run inputs, Discovery-to-Matrix CID-NL composition or Delta Mass diagnostics, stage outputs and cancellation; Alignment from existing Discovery remains a separate intent | grouping, cell decisions, a second runtime owner or writer algorithm |
| `discovery/` | candidate discovery, seed generation, workflow-neutral discovery facts and the published Discovery file | matrix decisions or target-list policy |
| `execution/` | generic attempt lifecycle, scheduling, workers, cancellation, cleanup, result storage and runtime metrics | method-specific science, subject splitting, or writer authority |
| `extraction/` | Targeted acquisition, result models, product projection, input binding and capture population | application composition, UI, or Alignment decisions |
| `untargeted_execution/` | retained Untargeted planning, partition execution and private result lifetime | Targeted policy, application composition, Matrix writing or a second runtime owner |
| `peak_detection/` | physical observations, hypotheses, resolvers, boundaries, integration, evidence composition | cross-sample grouping, workflow policy, or writers |
| `alignment/` | cross-sample grouping, Backfill and Alignment adapters | a second runtime owner, transport authority, or reading Targeted acquisition results |
| `output/` | schema rendering and the Targeted product result model; `output/alignment/` writes Matrix artifacts | evidence recomputation, application store lifetime or RAW access |
| `alignment_explanation/` | shared SQLite format, retention codecs, atomic publication and completed-evidence queries | scientific decisions, RAW acquisition, or Inspector presentation policy |
| `exclusion_list/` | blank background ions, target-safe row selection and import CSVs for DDA exclusion lists | Untargeted discovery, the background library used by Discovery, or instrument method files |
| `diagnostics/` | reusable diagnostics and method-specific projections of completed facts | published product artifacts, CLI parsing, production mutation, or domain recomputation |
| `xic_inspector/` | read-only review API/CLI, source adapters, selection and offline HTML rendering | RAW acquisition, scientific recomputation, identity/Matrix decisions, or write authority |
| `xic_workbench/` and `scripts/` | HTML interaction, local transport, and thin orchestration | reusable domain behavior or scientific authority |
| root facades | current public compatibility and thin orchestration | new implementation detail |

Complete Untargeted intent is `FullAnalysisRequest`, executed directly by
`UntargetedAnalysis`; Workbench parses JSON and adds the Inspector overview to
the completed result. Only the CID-NL route enters Alignment/Backfill and
publishes Matrix. Delta Mass and CID-NL dR composite routes publish diagnostics.
The composite reads existing per-sample peak tables; the downloadable merged
peak table is an output, not an analysis handoff. Stage CLI inputs remain valid.

## Cross-Cutting Modules

| Cross-cutting owner | Responsibility |
| --- | --- |
| `sample_metadata.py` | sample metadata parsing, RAW mapping and role inference; a run's samples are `SampleMetadata` rows |
| `matrix_curation/` | post-Alignment curation over a published Matrix and its export; never Alignment decisions or normalization |
| `evidence_artifacts.py` | completed-evidence locator: report and Matrix paths, Matrix SHA-256, attempt UUID |
| `processing_identity.py` | RAW source fingerprints checked at file handoffs |
| `acquisition_contract.py` | capability-bound acquisition batches and call/point facts |

## Method Modules

CID-NL interpretation lives in explicit method modules: tag/composition and mass
envelopes for grouping, revisit eligibility and assessment-to-cell evidence.
Shared grouping keeps physical mass/RT/shape and sample uniqueness; shared cell
composition keeps exact sample/group lineage. Shared inference does not select
a method or read method reason strings; the
`grouping-and-cell-consume-method-answers` import contract keeps the method
modules out of shared grouping and cell closure. The CID-NL cell record (`CellObservation`) answers its own
evidence state, which shared closure consumes. These are in-memory seams, not a
registry: another method needs its own evidence adapter. A revisit does not reopen grouping, and a scheduling
partition is never a feature boundary.

## Configuration

| Owner | Responsibility |
| --- | --- |
| `configuration.target_list_files` | strict Targeted target-list XLSX exchange |
| `configuration.workspace_state` | local JSON application, batch and target persistence |
| `configuration.delta_mass` | shared pure pattern settings |
| `configuration.targeted_identification` | named all/any condition intake and quantitation-relative Delta Mass conversion |

Targeted acquisition reuses Discovery's matcher and CID-NL evaluation on the
same physical candidates; only method-admitted candidates enter completion, and
`evidence.targeted_identification` keeps clause answers for output and Inspector.
`prepare_targeted_config` freezes admitted inputs into private CSV run receipts;
`load_config_snapshot` reads such a receipt for diagnostics only. Workbench and
CLI do not discover legacy CSV workspaces. Public examples are read-only
resources in `configuration/data/`.

## RAW Acquisition

- `raw_reader` owns Thermo DLL loading, native handle lifetime and copying
  RAW/SLD facts into Python values. DLL access stays in the adapter.
- `raw_source_binding` verifies file revisions at admission, cache replay and
  before/after vendor opening; it does not interpret spectra.
- `raw_sequence` resolves analysis membership and chronology from adapter facts.
- `raw_mass_recalibration` owns in-memory restoration decisions and arithmetic;
  it is not implemented by a facade.
- Targeted review uses `xic_inspector.sources.targeted_workbook` for saved
  facts/settings and `acquisition.review_xics` for RAW lifetime, recalibration,
  full-trace preparation and near-ISTD signal diagnostics. Inspector's
  `targeted_curves` projects those observations into `CurveBundle`; Workbench
  passes it directly to `prepare_curve_view`. Offline export is user-requested,
  not an interactive curve-file handoff. Diagnostic reference RT remains the
  saved ISTD RT, separate from the formal method's reference-RT adjustment.

## Evidence And Decisions

Evidence providers enter through `EvidenceVector`, `PeakHypothesis`, model
selection and `AuditTrail` before any Matrix change; activation rules are in
[`product-validation-contract.md`](../agent/product-validation-contract.md).

- `peak_detection.evidence_semantics` is role-neutral and shared by candidate
  selection, product projection and Alignment, so changes there reach both
  paths. Targeted role policy is in `extraction.targeted_product_projection`;
  writers consume `output.targeted_product_model`. Resolution order is in
  [`decision-policy.md`](../product/decision-policy.md).
- Peak detection has one path: physical segments from
  `peak_detection.physical_peak_evaluation`.
  `peak_detection/peak_selection.py` scores a supplied candidate set; Targeted
  passes its physical candidates straight to selection.
- Targeted selection consumes the selected physical answer from
  `physical_peak_candidate_adapter.answer_selected_physical_candidate`, which owns
  candidate-to-segment binding and reuse of prepared evaluations.
  `candidate_scoring` owns evidence scoring and score summaries.

## Workbench Client Modules

Workbench's [Analysis Session](../../xic_workbench/assets/analysis_session.js)
owns client-side job commands, response ordering, observation lifetime,
connection status and once-only completion delivery. Its consumers submit
intentions and read immutable snapshots; rendering does not advance lifecycle.
`analysis_jobs.js` composes that Module with application-setting adoption and
presentation. [Sample metadata](../../xic_workbench/assets/analysis_sample_metadata.js)
owns sample rows, read recency, cross-flow sharing and ordered folder-scoped
edits. Consumers read immutable page snapshots and submit
edits by RAW stem; they do not coordinate revisions or mutate retained job
results. Within one Workbench page, metadata writes and Analysis jobs exclude
one another so sequence-import jobs cannot overlap those writes. Ending Analysis
observation does not cancel the server job; execution and publication authority
remain with their Python owners.

The [Curation Session](../../xic_workbench/assets/analysis_curation_session.js) owns
the source and step draft, debounced single-flight reads, and preview-backed
export admission. A draft edit retires its previous preview immediately; export
rechecks that draft after saving preferences. The view renders snapshots and
submits intentions, without request equality checks or lifecycle timers.
Preview admission concerns the frontend draft, not scientific writer authority;
curation calculations and publication remain with their Python owners.

For saved-result and curve review, see the decision on
[late review responses](../adr/0010-reject-stale-workbench-review-responses.md).
