# Core Alignment Owner Migration — Current Packet

Packet ID: `xic-core-alignment-owner-migration-r4`

Revision: `4`

Code-evidence base SHA: `f459f11ce1ba68a106095160ba453fc288288914`

Prior corrective freeze: `7f22c789c0833f2d0f912917e1e3805a8b5d87f1`

Status: `frozen / local issue draft / implementation-ready; implementation not started`

Canonical packet: this file

Supersedes:

- `xic-core-alignment-owner-migration-r1` at
  `5025511c955f169158d41d9d16d856af1851210c`; and
- `xic-core-alignment-owner-migration-r2` at
  `4354271f05ec8848168c23445e3232c4f0bc8c2a`; and
- `xic-core-alignment-owner-migration-r3` at the prior corrective freeze above.

This is the local execution index for the next architecture phase. It may be
copied into a GitHub Issue only after explicit authorization. The durable
architecture and vocabulary remain in the linked owner docs; PR bodies will own
their exact diffs, verification, rollback, and residual risk.

## Objective

Replace the current highly coupled Alignment/Targeted split with deep semantic
owners so Targeted and Untargeted share capture, CrossSample, ModeTrack,
ObservationRepair, and ProductionCellDecision implementations, while retaining
different typed search scopes and output projections.

User value: one scientific meaning per decision, less duplicate behavior, a
smaller review surface, traceable null/value outcomes, and a codebase where old
migration assets are actually removed after consumers move.

## Current reality and loss

| State | Evidence | Observable loss |
| --- | --- | --- |
| Targeted and Untargeted are only partially converged | [`shared-processing-kernel.md`](../../product/shared-processing-kernel.md) and current `extraction/` versus `alignment/` routes | Equivalent evidence can gain different semantics by entry point |
| Alignment transports a mutable owner Matrix through several authorities | `xic_extractor/alignment/pipeline.py`, `matrix_identity.py`, `production_decisions.py`, and writers | Grouping, repair, value, inclusion, and rendering changes have a wide blast radius |
| Cross-sample identity is commonly ordinal/display-shaped | Current `FAM...` construction and [`processing-lineage.md`](../../product/processing-lineage.md) | Ordering/parallelism and product row identity are not cleanly separated |
| ModeTrack capability already exists but is a large diagnostic island | `xic_extractor/alignment/identity_coherence/` and `mode_track_*` modules | Replacing it would duplicate a useful model; leaving it isolated preserves two trajectory meanings |
| Backfill currently mixes repair, promotion, evidence, and Matrix concerns | Current Backfill/owner/transaction/acceptance files and [`backfill.md`](../../product/backfill.md) | A repaired observation can be confused with membership or writer authority |
| Machine evidence is often projected to large string tables | Current Backfill TSV and optional strings-only Parquet shadow | Unnecessary I/O/storage and weak native typing; humans rarely inspect the full population |

Operator observation: current Targeted behavior can remain null even when an NL
tag and corresponding MS1 peak were observed. That is a behavior hypothesis to
diagnose and promote later, not permission to loosen rules during structural
migration.

## Premise ledger

| Premise | State | Consequence |
| --- | --- | --- |
| Untargeted is the general lifecycle; Targeted is a constrained specialization | `contract` | One shared scientific implementation, separate search/projection variants |
| Broad candidate discovery is not Untargeted-only | `contract` | Targeted may inspect broad dR/R/MeR evidence while retaining only configured targets |
| ModeTrack should be reused, deepened, and upgraded | `contract` | `identity_coherence` capability migrates into `alignment/mode_track/`; no replacement algorithm module |
| CrossSample owns membership and group lineage | `contract` | ModeTrack proposes only; Backfill cannot imply membership |
| Backfill owns bounded local ObservationRepair | `contract` | ProductionCellDecision remains the sole selected-value owner |
| A singleton cannot establish cross-sample trend or fill other samples by itself | `contract` | Singleton groups are provisional; direct local evidence or independent repair is required |
| One universal monotonic RT drift describes all features | `rejected` | Sensitivity evidence includes multi-direction ISTDs and high-detection unknown features |
| Structural cleanup should also tune science behavior | `rejected` | Every behavior delta moves to a separately promoted lane |

## Frozen boundaries

In scope:

- establish request-stable group/member/disposition/lineage contracts;
- deepen existing ModeTrack behind an immutable proposal-only interface;
- isolate ObservationRepair from membership and value authority;
- create one ProductionCellDecision implementation with typed Target/Group
  subjects and typed no-write reasons;
- create immutable finalized core/result variants and Target/Untarget row
  projections;
- reduce the pipeline to a typed composition root;
- separate evidence archive, analytical handoff, delivery, and explanation;
- replace loose output flags with an owner-derived `ArtifactPlan`; and
- delete superseded code, shims, tests, schemas, and diagnostics after the named
  consumer census reaches zero.

Out of scope for structural PRs:

- changing selected peak/area, thresholds, RT models, Backfill eligibility,
  counted detection, row inclusion, public schemas, default artifacts, or GUI;
- activating Target local fill, ModeTrack reassignment, Untarget row-key public
  cutover, QC correction, Parquet v2, DNP/MA handoff, or new writer authority;
- inferring cross-request Untarget row continuity; and
- running 85RAW or other expensive RAW validation without the RAW gate.

## Owner and asset disposition

| Current asset | Disposition | Successor / exit oracle |
| --- | --- | --- |
| `configuration/target_identity.py` | `keep` | Remains the Target identity owner for `TargetKey`, exact Target definition identity, and ordered `TargetListSnapshot`; Target projection consumes it and never remints or absorbs it |
| `cross_sample_peak_groups.py`, owner grouping/consolidation facts | `reuse_after_cleanup` | `alignment/cross_sample/`; identity/disposition/lineage characterization and consumer migration |
| `identity_coherence/`, ModeTrack producers/finalization | `reuse_after_cleanup` | `alignment/mode_track/`; proposal-only interface, no Matrix/Backfill/writer imports |
| Owner Backfill request/reuse/transaction/evidence facts | `reuse_after_cleanup` | `alignment/backfill/`; one bounded ObservationRepair epoch and truth-table closure |
| `production_decisions.py`, `machine_decision.py`, value-bearing `AlignedCell` fields | `replace_or_retire` | `alignment/cell_decision/`; parity oracle then field/implementation deletion |
| `matrix_identity.py`, `provisional_matrix.py`, `owner_matrix.py` mixed pre-final Matrix roles | `replace_or_retire` | distinct CrossSample/observation snapshots plus `alignment/result/`; final Matrix only after closure |
| `pipeline.py` domain branches and loose metadata transport | `replace_or_retire` | `alignment/workflow.py`; typed composition only |
| Targeted-specific alignment/rescue paths | `reuse_after_cleanup` then `compatibility_only` | Target projection adapters over shared owners; remove fallback algorithm after CLI/GUI/direct consumers migrate |
| `output_levels.py` and caller-authored emit booleans | `replace_or_retire` | delivery-owned `ArtifactPlan`; parity plus exact artifact-set oracle |
| `delivery_lineage.py` Targeted cell/revision/stage/seal identities | `reuse_after_cleanup` | Delivery composes the existing leaf used by `extraction/targeted_diagnostic_delivery.py`; generalize only for a named current Untarget consumer and never remint parallel Targeted identity |
| Existing Backfill TSV and string-Parquet adapters | `compatibility_only` | finite reader/adapter census; typed Parquet v2 is a separate promotion |
| Alignment TSV/XLSX/HTML writers | `reuse_after_cleanup` | delivery renderers consuming finalized decisions only |

### Shared explanation module disposition

Every filename below is relative to
`xic_extractor/alignment/shared_peak_identity_explanation/`; together the rows
cover all 26 Python modules at the code-evidence base. A file is not considered
consumer-free until import/referrer, CLI, diagnostic `INDEX.md`, documented
artifact-name, and downstream-reader censuses all reach zero or point to a
verified successor.

| Exact module group | Disposition | Current named consumers | Successor / boundary | Removal oracle |
| --- | --- | --- | --- | --- |
| `__init__.py`, `schema.py`, `writers.py` | `compatibility_only` | `tools/diagnostics/shared_peak_identity_explanation.py`, `scripts/check_productization_authority.py`, activation/report writers, and schema importers | Split only after migration: typed explanation schemas/renderers go to `explanation/`; activation schemas stay with activation/ProductWriter authority | All importing CLIs/modules and schema/artifact readers migrate; output-schema parity passes; package facade and old writers then delete together |
| `assembler.py`, `blast_radius.py`, `classifier.py`, `machine_artifacts.py`, `oracle.py`, `shadow_labels.py` | `reuse_after_cleanup` | shared-peak explanation CLI, health/blast-radius diagnostics, and review classifiers | `explanation/` consumes owner-backed archive queries and renders/classifies only; no product-value authority | Exact query/classification parity plus zero old imports, CLI/INDEX refs, and old artifact readers |
| `candidate_ms2_pattern.py`, `ms1_pattern_coherence.py`, `ms1_peak_modes.py`, `ms1_peak_quality_vector.py`, `overlay_trace_data.py`, `qc_ms1_pattern_reference.py` | `compatibility_only` during structural migration | candidate-MS2/MS1/QC evidence diagnostics, `qc_ms1_pattern_reference.py`, peak-hypothesis builders, and product-authority review tools | Remain method/evidence-provider helpers until a separately promoted existing scientific owner consumes them; they do not move into explanation merely because explanation displays them | Per-module import/CLI/INDEX and artifact-reader census; method-specific parity/expected diff before reuse, otherwise retire at zero consumers |
| `matrix_rt_drift_policy.py`, `mode_hypothesis_assignment.py`, `mode_window_assignment_gate.py`, `peak_hypothesis_matrix.py`, `peak_hypothesis_selection.py`, `rt_mode_evidence.py` | `compatibility_only` during structural migration | mode-assignment/window diagnostics, changed-row overlay review, RT normalization analysis, and peak-hypothesis matrix tooling | Current diagnostic evidence remains outside shared ModeTrack authority; reuse requires a typed proposal/evidence seam and separate behavior gate | Mode/RT output parity, no authority leakage, then zero import/CLI/INDEX/artifact-reader refs to the legacy modules |
| `hypothesis_consistency.py`, `machine_evidence_support.py`, `wrong_peak_root_cause.py` | `reuse_after_cleanup` | shared-peak explanation, consistency/diagnosis tools, Backfill evidence projection, and product-authority review | Typed explanation/evidence reconciliation over owner-backed inputs; cannot select value, membership, or activation | Reconciliation/query parity and migrated named consumers; delete legacy path when all four census classes reach zero |
| `activation_contract.py`, `product_activation.py` | `compatibility_only` | `tools/diagnostics/apply_shared_peak_identity_activation.py`, Backfill normal-peak activation, activation bridge/acceptance diagnostics, and current matrix-only writers | Remain with quant-matrix/ProductWriter activation authority; structural archive/explanation work must not absorb or duplicate them | Named activation consumers migrate through a separately approved behavior/writer contract; exact value/schema/rollback parity; then retire old modules |

Tests protect product invariants and public compatibility, not old module
shapes. A test moves to the successor interface or is deleted with its retired
implementation unless it still proves a named compatibility or rejection path.

## Known compatibility consumers

Initial production census at the packet base:

- `scripts/run_alignment.py` calls `alignment.pipeline.run_alignment`;
- `xic_extractor/extraction/untargeted_provisional_matrix_workflow.py` calls the
  same entry;
- current Targeted CLI, GUI, and `xic_extractor.extractor.run` retain their
  public output behavior;
- `alignment_matrix.tsv`, `alignment_review.tsv`, identity/evidence sidecars,
  Targeted CSV/workbook, Alignment XLSX/HTML, config keys, filenames, order, and
  reason/count/value semantics are public contracts; and
- `gui/workers/backfill_gallery.py`, `scripts/run_alignment_validation.py`,
  `tools/diagnostics/alignment_health_packet.py`,
  `tools/diagnostics/analyze_matrix_identity_blast_radius.py`, and
  `tools/diagnostics/backfill_scope_probe.py` are live readers/adapters rather
  than disposable diagnostics;
- `tools/diagnostics/family_ms1_overlay_batch.py` is a maintained downstream
  reader of `alignment_backfill_cell_evidence.tsv`;
- `scripts/validate_migration.py` calls exported `extractor.run` through the
  alias `run_extractor`. It is an executable validation oracle, not a live
  product consumer, but any PR changing that exported API must migrate and run
  it; the exclusion cannot justify a compatibility adapter; and
- tests and historical artifacts are oracles, not consumer evidence.

Each migration PR must refresh the executable caller/reader census from its
base SHA. A compatibility adapter is legal only when it names a still-live item
from that census and has an explicit removal condition.

## Transaction compilation

The following scope is compiled against the code-evidence base SHA above. The
census is bounded to in-repo production reachability and known public artifacts;
tests and historical artifacts remain oracles, not evidence of a live consumer.
`<base>` in the table means the declared code-evidence base SHA, never the
mutable working-tree head.
This documentation freeze launches no RAW work. Each owner PR must refresh its
census, execute the highest applicable production entry with the named real
consumer intact when dynamic dispatch/repeated work matters, and record every
monkeypatch, no-op, or substituted dependency.

| Requirement / transaction | Highest real entry and current owner route | Successor owner operations | Live production/public consumers | Census query and exclusions | Call cost, falsifier, and acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| Targeted request, identity, and search | `scripts/run_extraction.py:main` and `gui/workers/pipeline_worker.py:PipelineWorker.run` both enter `build_canonical_targeted_application_service(extractor.run)`; exported `xic_extractor.extractor.run` enters `extraction.pipeline.run_pipeline`; `configuration/target_identity.py` owns Target identity | Existing Target identity owner -> `TargetSearchScope` -> shared capture/core -> Target projection binding/subjects -> cell decision -> Target projection completion | CLI CSV/method manifest/workbook, GUI summary/workbook, direct `extractor.run` callers, and the migration validation oracle | Service topology: `git grep -l -E "build_canonical_targeted_application_service|CanonicalTargetedApplicationService" <base> -- tools xic_extractor scripts gui`; exported API topology, including aliases: `git grep -l -E "from xic_extractor\\.extractor import .*run|import xic_extractor\\.extractor|xic_extractor\\.extractor\\.run" <base> -- tools xic_extractor scripts gui`; identity: `git grep -l -E "configuration\\.target_identity|TargetKey|TargetListSnapshot" <base> -- tools xic_extractor scripts gui`. `scripts/validate_migration.py` is an executable oracle; `xic_extractor/output/method_manifest.py` is a live public entrypoint reference. Exclude tests, benchmarks, resolver comparison, and parameter sweeps as consumer evidence, but never from API-change verification | Highest entry is RAW-backed and potentially expensive; structural PRs add no new RAW run unless their gate requires it. Falsify with Target key/definition/snapshot/order or method/search-scope mismatch. Accept with current Targeted config/order and CSV/workbook hard-exact parity, or an approved expected diff |
| Untargeted discovery/alignment | `scripts/run_alignment.py:main`, `gui/workers/discovery_worker.py:DiscoveryWorker._run_alignment`, and `extraction/untargeted_provisional_matrix_workflow.py` enter `alignment.pipeline.run_alignment` | CrossSample -> ModeTrack proposal/apply -> bounded ObservationRepair -> scientific-core closure -> Untarget binding/subjects -> cell decision -> Untarget completion -> result finalizer | `alignment_matrix.tsv`, review/identity/evidence sidecars, Alignment XLSX/HTML, GUI discovery summary/gallery inputs, provisional full-universe workflow, `gui/workers/backfill_gallery.py`, and `scripts/run_alignment_validation.py` | Import/topology query, including aliases: `git grep -l -E "from xic_extractor\\.alignment(\\.pipeline)? import .*run_alignment|import xic_extractor\\.alignment\\.pipeline|alignment\\.pipeline\\.run_alignment" <base> -- tools xic_extractor scripts gui`; the source-derived artifact/referrer closure below is the reader census. Exclude tests, docs, and historical artifacts; named GUI/workflow adapters and maintained diagnostic readers are live consumers | RAW/DLL-backed at the highest entries. Falsify with membership/group identity drift under order/parallelism, unresolved ambiguity choosing a winner, or artifact ordering drift. Accept with current matrix/review/identity/output byte/schema parity and targeted group/order counterexamples |
| Projection, cell decision, and result | `run_alignment` currently builds mutable Matrix identity, calls `build_production_decisions`, then supplies `ProductionDecisionSet` to product matrix, provenance, explanation, and writers | `FinalizedScientificAlignment` -> Target/Untarget binding and typed cell subjects -> one `ProductionCellDecision` -> same projection's inclusion/count/reason/routing completion -> discriminated result finalizer | `product_matrix.py`, `pipeline_outputs.py`, TSV/XLSX/HTML writers, `untargeted_explanation_index.py`, `owner_backfill_peak_fact_provenance.py`, `tools/diagnostics/alignment_health_packet.py`, and `tools/diagnostics/analyze_matrix_identity_blast_radius.py` | `git grep -l -E "ProductionDecisionSet|build_production_decisions|accepted_cell_count|include_in_primary_matrix" <base> -- tools xic_extractor scripts gui` is a symbol cross-check only. Completeness comes from the source-derived owner artifact set, import/referrer topology, CLI registry, and downstream-reader union below. Exclude tests and ledger snapshots; every maintained diagnostic import remains a consumer until explicitly migrated or retired | In-memory row-by-sample work; no RAW is required for structural characterization. Falsify when Target evidence authorizes a Group subject, `accepted_cell_count` disagrees with inclusion, or typed no-write renders a different public reason/value. Accept with subject-crossing rejection, cell/no-write truth tables, current count/inclusion parity, and immutable closure tests |
| Delivery and artifact set | Targeted CLI/GUI writers and `run_alignment` currently combine `output_levels.py`, `emit_*` parameters, pipeline metadata, renderers, and optional `delivery_lineage.py` Targeted diagnostic candidates | Projection contract + delivery intent + authority snapshot + named compatibility consumers -> `ArtifactPlan`; finalized result + plan -> render-only writers -> atomic delivery receipt | Targeted CSV/workbook/method manifest, Alignment TSV/XLSX/HTML and conditional sidecars, GUI summaries, `targeted_diagnostic_delivery.py`, `gui/workers/backfill_gallery.py`, `tools/diagnostics/backfill_scope_probe.py`, `tools/diagnostics/family_ms1_overlay_batch.py`, and external readers of documented filenames/schemas | `git grep -l -E "emit_alignment_|artifact_names_for_output_level|AlignmentOutputLevel|output_level" <base> -- tools xic_extractor/alignment xic_extractor/extraction scripts gui/workers` is an option cross-check. The authoritative filename candidates begin from `output_levels.py`, `pipeline.py`, and `pipeline_outputs.py`, then close all downstream refs through the 102-name manifest below. Exclude tests/docs; CLI flags, GUI constants, path-carrying readers, and maintained diagnostic CLIs are live compatibility consumers | Output-I/O bound; no RAW needed for plan/atomicity tests. Falsify with missing/extra default artifact, schema/order drift, duplicate delivery identity, or partially visible failed bundle. Accept with exact artifact-set/schema/hash parity, current Targeted lineage roundtrip, and failure-atomic publication tests |
| Evidence archive and explanation | `run_alignment` writes Backfill evidence TSV; maintained `tools/diagnostics/backfill_evidence_parquet_shadow.py` writes/reads the current strings-only Parquet shadow; explanation indices, product-authority modules, activation diagnostics, gallery inputs, and authority checks query or import explanation assets | Typed owner batches -> compact `evidence_archive`; owner-backed queries -> `explanation`; workflow projection alone routes Review Queue/archive presence | Backfill evidence readers, Parquet-shadow diagnostic CLI, explanation index/sections, Backfill product-authority modules, maintained explanation/mode/QC diagnostic CLIs, activation bridge/acceptance diagnostics, gallery evidence/inputs, and `scripts/check_productization_authority.py` | Import/referrer: `git grep -l "shared_peak_identity_explanation" <base> -- tools xic_extractor scripts gui`; Parquet owner/import topology: `git grep -l -E "backfill_evidence_parquet|BACKFILL_PARQUET_FILENAME|SKIPPED_PARQUET_FILENAME|PARQUET_SHADOW_MANIFEST_FILENAME" <base> -- tools xic_extractor scripts gui docs/product docs/user`; CLI: exact referrers registered in `tools/diagnostics/INDEX.md`; artifacts: the 102-name source-derived manifest below. Exclude tests/history only; retain every import, CLI, index, documented artifact name, and downstream reader until classified and migrated | Query/replay and storage I/O; no new RAW run. Falsify with broken evidence ref/query result, explanation/activation selecting a product value, or an omitted named consumer. Accept with typed query roundtrip, render-only explanation, retention-class checks, and zero-consumer proof before owner-local retirement |

### Base-SHA topology and artifact census

The census below was executed against
`f459f11ce1ba68a106095160ba453fc288288914`, not inferred from the mutable
working tree. The old 64/53/9/9 symbol and five-filename counts remain useful
spot checks, but they are not completeness or removal oracles. Completeness is
compiled from owner sources first and readers second:

1. `git ls-tree -r --name-only <base> -- xic_extractor/alignment/shared_peak_identity_explanation`
   returns the 26 Python modules in the disposition table above.
2. `git grep -l "shared_peak_identity_explanation" <base> -- tools xic_extractor scripts gui`
   returns 22 paths: 21 code referrers plus `tools/diagnostics/INDEX.md`.
3. The 26 modules, the 21 code referrers, and the three Alignment artifact-set
   owners `output_levels.py`, `pipeline.py`, and `pipeline_outputs.py` form 48
   unique source paths. Extracting every literal `.tsv`, `.csv`, `.json`,
   `.jsonl`, `.parquet`, `.md`, `.html`, `.xlsx`, `.png`, and `.svg` basename
   from those sources yields 99 unique candidates. The existing Parquet owner
   constants in `alignment/backfill_evidence_parquet_bundle.py` add
   `bundle_manifest.json`, `backfill.parquet`, and `skipped.parquet`, for the
   frozen 102-name conservative manifest below.
4. Searching every manifest name with a separate fixed-string `-e` operand
   across `tools xic_extractor scripts gui docs/product docs/user` returns 94
   paths: 84 Python paths, `tools/diagnostics/INDEX.md`, and 9 product/user docs.
   This set includes `tools/diagnostics/family_ms1_overlay_batch.py` and its
   `alignment_backfill_cell_evidence.tsv` reader.
5. Union that artifact-ref set with the 22 shared-package referrers, the four
   Parquet owner/referrer paths from the transaction query, the two exported
   Target API refs, and the three Target application-service refs. The result
   is 108 classified candidates: 98 Python paths, one INDEX, and nine docs.

Tests and historical outputs are excluded from live-consumer counts but remain
oracles. Every one of the 108 returned paths is a compatibility candidate until
its owner PR records one of: writer/owner source, live product consumer,
maintained CLI, downstream reader, durable contract, executable oracle, or
retired-with-zero-ref proof. Only a named live product consumer, maintained CLI,
downstream reader, or durable contract with a current consumer can justify a
temporary compatibility adapter; an owner source or oracle alone cannot.
`scripts/validate_migration.py` is explicitly an executable oracle; aliasing
`run` as `run_extractor` does not make it invisible, but the script alone does
not preserve a legacy implementation.

Counts overlap; they are routing bands rather than additive populations:

| Census band | Current closure | Owning migration PR / disposition |
| --- | --- | --- |
| Target API and application-service topology | 2 exported-API refs plus 3 service refs | Target binding/completion PRs preserve Target identity; Workflow PR moves composition; Delivery PR preserves public outputs. `validate_migration.py` remains an oracle, while `method_manifest.py` is a live public entrypoint reference |
| Alignment and explanation artifact refs | 102 conservative names -> 94 paths | Delivery PR owns Alignment artifact planning/readers; Explanation PR owns render/query artifacts; activation/ProductWriter artifacts remain compatibility-only until a separate behavior/writer PR |
| Shared explanation package topology | 26 modules -> 22 direct package/referrer paths, then root-export symbol closure | Per-module disposition table above; Explanation or Evidence Archive PR migrates only its named group, and owner-local cleanup waits for all four per-path censuses |
| Backfill Parquet shadow | 3 exact bundle names -> 4 owner/import ref paths | Evidence Archive PR preserves the current compatibility reader/writer; typed Parquet v2 remains a separate behavior promotion |
| INDEX and durable docs | 1 maintained diagnostic index plus 9 product/user docs in artifact refs | Updated with the owner PR whose public/diagnostic surface changes; never deferred to a catch-all cleanup PR |

The frozen artifact-name manifest is:

```text
activation_acceptance.tsv
activation_application_summary.tsv
activation_decisions.tsv
activation_hypothesis_identity.tsv
activation_matrix_preflight.tsv
activation_value_delta.tsv
activation_values.tsv
alignment_backfill_cell_evidence.tsv
alignment_cell_integration_audit.tsv
alignment_cells.tsv
alignment_cells_activated.tsv
alignment_early_loser_shadow.json
alignment_matrix.tsv
alignment_matrix_activated.tsv
alignment_matrix_identity.tsv
alignment_matrix_status.tsv
alignment_owner_backfill_candidate_audit.tsv
alignment_owner_backfill_peak_fact_provenance.tsv
alignment_owner_backfill_seed_audit.tsv
alignment_production_candidate_gate.tsv
alignment_results.xlsx
alignment_retained_backfill_evidence_gate.tsv
alignment_review.tsv
alignment_review_activated.tsv
alignment_run_metadata.json
alignment_tier2_trace_evidence.tsv
ambiguous_ms1_owners.tsv
backfill.parquet
backfill_peakhypothesis_activation_acceptance.tsv
backfill_peakhypothesis_activation_acceptance_summary.json
backfill_peakhypothesis_activation_bridge_summary.json
backfill_peakhypothesis_activation_matrix_diff.tsv
backfill_peakhypothesis_normal_peak_activation.tsv
backfill_peakhypothesis_normal_peak_activation_summary.json
backfill_shadow_policy_cells.tsv
bundle_manifest.json
changed_row_mode_overlay_summary.tsv
changed_row_mode_sample_review.tsv
changed_row_peak_hypothesis_selection.tsv
changed_row_rt_mode_evidence.tsv
changed_row_similarity_review.tsv
changed_row_similarity_summary.tsv
event_to_ms1_owner.tsv
family_ms1_overlay_batch_summary.tsv
issue256_quantitative_projection_authority_v1.json
mode_aware_review_gallery.html
owner_edge_evidence.tsv
peak_hypothesis_cell_assignments.tsv
peak_hypothesis_inventory.tsv
peak_hypothesis_matrix_summary.tsv
production_acceptance_manifest.tsv
provisional_alignment_matrix.tsv
provisional_cell_provenance.tsv
provisional_matrix_summary.json
provisional_row_identity.tsv
review_report.html
seed_aware_backfill_review_families.tsv
seed_aware_backfill_review_summary.tsv
shadow_production_projection_cells.tsv
shared_peak_identity_activation_acceptance.tsv
shared_peak_identity_activation_decisions.tsv
shared_peak_identity_blast_radius_manifest.tsv
shared_peak_identity_blast_radius_summary.tsv
shared_peak_identity_candidate_ms2_pattern_evidence.tsv
shared_peak_identity_evidence_vectors.tsv
shared_peak_identity_explanation_report.md
shared_peak_identity_explanations.tsv
shared_peak_identity_hypothesis_consistency.tsv
shared_peak_identity_hypothesis_consistency_summary.tsv
shared_peak_identity_machine_evidence_support.tsv
shared_peak_identity_manual_oracle.tsv
shared_peak_identity_matrix_rt_drift_policy.tsv
shared_peak_identity_mode_window_assignment_gate.tsv
shared_peak_identity_mode_window_assignment_summary.tsv
shared_peak_identity_ms1_pattern_coherence_evidence.tsv
shared_peak_identity_peak_hypothesis_selection.tsv
shared_peak_identity_rt_mode_evidence.tsv
shared_peak_identity_run_facts.tsv
shared_peak_identity_shadow_alignment_summary.tsv
shared_peak_identity_shadow_labels.tsv
shared_peak_identity_v2_readiness.tsv
shared_peak_identity_v2_report.md
shared_peak_identity_wrong_peak_root_cause.tsv
shift_aware_standard_peak_gate_calibration.tsv
single_dr_gate_activation_decisions.tsv
single_dr_gate_candidates.tsv
single_dr_gate_changed_row_bundle.tsv
single_dr_gate_decision.json
single_dr_gate_decision.md
single_dr_gate_decision_detected_cells.tsv
single_dr_gate_decision_families.tsv
single_dr_gate_decision_summary.tsv
skipped.parquet
skipped_evidence_ledger.tsv
source_family_best_shift_summary.tsv
standard_peak_activation_values.tsv
targeted_istd_benchmark_summary.tsv
untargeted_identity_coherence_cell_evidence.tsv
untargeted_identity_coherence_controls.tsv
untargeted_identity_coherence_decisions.tsv
untargeted_identity_coherence_requests.tsv
untargeted_identity_coherence_summary.md
```

For the fixed-string reader query, load those exact names into a PowerShell
array, expand each as `-e`, and invoke one direct
`git grep -l -F @patternArgs <base> -- tools xic_extractor scripts gui docs/product docs/user`.
The 102-line block is the input record; no prefix heuristic such as
`alignment_*` or `shared_peak_identity_*` may replace it.

Each of the 26 explanation modules has a per-path closure, not merely a package
count:

- direct module refs search both its slash path and dotted module name;
- package-root imports are resolved through the module's exports in
  `shared_peak_identity_explanation/__init__.py`, then every exported symbol is
  searched across the 21 code referrers;
- each referrer under `tools/diagnostics/` is checked against
  `tools/diagnostics/INDEX.md`; a helper such as
  `single_dr_gate_decision_writers.py` remains a transitive CLI consumer when
  its registered report imports it; and
- module-owned literal artifacts and caller-supplied output paths are mapped to
  the 102-name manifest and its 94-path downstream-reader set.

An owner PR must record those four results beside each exact module path before
retiring it. Package self-matches are owner sources, not consumers. A module
re-export, alias, caller-supplied path, or INDEX topic group cannot be counted as
zero merely because a direct dotted import or one filename spelling is absent.

## Migration milestones and ordered owner PRs

Macro milestones may contain several ordered PRs, but every PR touches one
semantic owner/failure domain. The result and projection owners each have
operations on both sides of cell decision; their pre-cell and post-cell PRs are
explicitly sequenced and do not create a second owner. Do not split a coherent
operation into model/serializer/test/docs PRs or combine the stack into one
mega-PR.

| Ordered PR | Single owner operation | Merge gate | Exit/cleanup |
| --- | --- | --- | --- |
| 1. CrossSample | Canonical tagged member identity, group digest, disposition, split/merge lineage | Characterization parity; wrapper-canonicalization, order/parallel/retry/ambiguity counterexamples | Remove ordinal identity use from internal decisions; retain display adapter only |
| 2. ModeTrack | Absorb existing capability behind immutable snapshot/proposal interface | Model-set parity, proposal determinism, nontraining/unassessable/crossing cases | Delete duplicate/obsolete trajectory paths and ownerless diagnostic entry points |
| 3. Backfill | One bounded ObservationRepair epoch and transaction | Repair/membership/value truth table; exact receipt; no loop | Remove legacy repair paths and Backfill-owned writer decisions |
| 4. Result owner: scientific-core closure | Build immutable `FinalizedScientificAlignment` from closed group/refinement/repair receipts | Closure/failure/immutability/order tests | Stop mutable Matrix transport at the shared-core seam |
| 5a. Target projection: binding/subjects | Consume existing Target identity owner; create zero/one/many `TargetRowBinding` and `TargetCellSubject` | Target key/definition/snapshot/order and zero/one/many binding tests | Remove duplicate Target identity/binding logic after consumer migration |
| 5b. Untarget projection: keys/subjects | Create `UntargetRowKey` and `GroupCellSubject` from finalized groups | Group/row identity and established/provisional/unresolved counterexamples | Remove `FAM...`/display/ordinal use as internal row identity |
| 6. Cell decision | One deep Target/Group `ProductionCellDecision` | Current value/state parity; no-write taxonomy; cross-subject rejection | Remove fallback area/reason logic and value-bearing legacy aliases |
| 7a. Target projection: completion | Consume Target cell decisions; close inclusion, counts/reasons, product state, and Review Queue refs | Current Targeted count/state/reason/output parity | Remove post-decision Target projection duplication |
| 7b. Untarget projection: completion | Consume Group cell decisions; close row inclusion and archive routing | Current Matrix count/inclusion/reason parity | Remove mixed `matrix_identity`/row-decision authority |
| 7c. Result owner: finalizer | Close the discriminated immutable Targeted/Untargeted result after projection completion | Variant/closure/immutability tests; reject incomplete or crossed inputs | Remove optional-mode final-result structures |
| 8a. Workflow | Typed composition root over the ordered owner operations | Highest-entry Targeted/Untargeted transaction parity; runtime receipt closure | Delete domain branches and loose metadata transport from `pipeline.py` |
| 8b. Evidence archive | Compact typed archive and retention projection | Query/replay/retention tests; direct typed-batch boundary | Retire ownerless/heavy defaults; Parquet v2 remains a separate promotion |
| 8c. Delivery / ArtifactPlan | Owner-compiled artifact set, render-only writers, atomic bundle | Exact artifact-set/schema parity, current lineage roundtrip, failure atomicity | Delete emit booleans, loose ledgers, and stale adapters with zero consumers |
| 8d. Explanation | Typed decision rendering and archive/reference navigation | Render-only/query-reference tests and named-consumer migration | Delete compatibility bridges and heavy explanation assets at zero consumers |
| 9. Behavior promotions | One separately chosen Target fill, ModeTrack sensitivity, Untarget key/schema, Parquet v2, or handoff activation per PR | Expected diff, authority binding, method-appropriate real evidence/manual review, rollback | Promote or kill each behavior independently |

### Owner-local retirement and parent issue gate

There is no catch-all purge implementation PR. Each owner PR retires its own
superseded implementation, shim, migration schema, tests, diagnostics, flags,
and adapters as soon as that PR's named consumers and removal oracle close. A
lane cannot defer owner-local cleanup to a later cross-owner mega-change.

The parent issue closes only after the union of all owner exits has a fresh
referrer/caller/reader census, clean-checkout verification, and public-contract
review. If this temporary packet still needs deletion after its durable claims
are absorbed, that deletion is a separate docs-only closeout PR; it carries no
runtime owner migration.

## Validation and promotion rules

- Structural lanes use characterization parity plus maintainability gain. A
  changed scientific value relabels the work as behavior promotion.
- New or changed checks must be proven capable of failing by breaking the exact
  guarded invariant once.
- Evidence status uses the canonical Evidence labels. Tests alone never prove
  production readiness.
- SDO/LEK uses HCD evidence; MixSTDs may use CID/HCD; each Target method uses its
  declared evidence provider. ModeTrack sensitivity includes multi-direction
  ISTDs and several high-detection unknown features.
- Any 85RAW or other RAW-backed acceptance follows `xic-raw-validation` first.
- DNP/MA analytical handoff activates only after a real consumer roundtrip over
  typed samples/features/cells/lineage.
- Documentation-only closeout runs link/format checks, `git diff --check`, and
  secret/local-path scan; it grants no runtime or product authority.

## Stop and rollback

Stop and refreeze when a real Targeted/Untargeted caller proves incompatible
scientific semantics at a proposed shared seam; a structural lane produces an
unapproved product diff; a public consumer cannot be enumerated; cell decision
would run before its projection-owned binding/subject exists; canonical member
wrappers cannot resolve to one deliberate tagged identity; or owner closure
requires caller-authored authority. Do not add a flag or fallback to hide the
conflict.

Every PR body records its declared base SHA, its cutover point, and the ordered
dependent suffix that already consumes the new owner. Before a downstream PR
depends on it, a lane is rollbackable by reverting its coherent commit/PR while
the prior public path remains authoritative. After dependency cutover, rollback
reverts the dependent suffix in reverse dependency order before reverting the
owner PR. A compatibility facade never contains a second fallback algorithm,
so rollback selects the prior owner rather than silently mixing both.

## Done when

The migration is complete only when both workflows run through the frozen deep
interfaces, current public behavior is preserved or separately promoted, the
final result/delivery/archive contracts close, all named consumers have moved,
and superseded code, adapters, flags, tests, schemas, diagnostics, and this
temporary execution packet are removed or absorbed into their durable owners.

Durable owners:

- [`architecture-contract.md`](../../architecture-contract.md)
- [`architecture-public-contracts.md`](../../agent/architecture-public-contracts.md)
- [`domain-glossary.md`](../../product/domain-glossary.md)
- [`shared-processing-kernel.md`](../../product/shared-processing-kernel.md)
- [`alignment.md`](../../product/alignment.md)
- [`backfill.md`](../../product/backfill.md)
- [`processing-lineage.md`](../../product/processing-lineage.md)
- [`product-validation-contract.md`](../../agent/product-validation-contract.md)
