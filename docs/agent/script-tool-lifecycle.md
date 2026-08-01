# Script And Diagnostic Tool Lifecycle

This document defines the lifecycle vocabulary and records bounded human
decisions for `scripts/` and `tools/diagnostics/`. It is not a second
filename-by-filename registry. The executable census remains derived and
disposable; [`tools/diagnostics/INDEX.md`](../../tools/diagnostics/INDEX.md)
remains the entry-by-entry diagnostic index. A surface without a specific
retirement decision is retained or `protected_unknown`, not implicitly
deletable.

The inventory and byte-equivalent helper consolidations are cleanup-only. The
Alignment decision reader tracer is separately classified as bounded input
contract hardening: it rejects duplicate and blank headers while preserving
valid-input output. None of this work grants evidence, Matrix, selected value or
area, counted-detection, workbook, activation, or export authority.

## Classify Exposure And Lifecycle Separately

A file's invocation surface and lifecycle role are different questions.

Invocation exposure:

- **installed entry point**: declared in `[project.scripts]`;
- **contract-bearing command**: named by a product or downstream contract even
  when it is not installed as a console script;
- **maintained operator interface**: directly runnable for development,
  validation, or review, but not a stable end-user command;
- **internal helper**: imported by another entry point and not independently
  promised to users.

Lifecycle role:

- **product adapter**: converts product inputs or emits a contracted product
  projection;
- **evidence producer**: creates evidence or a reproducible source artifact;
- **diagnostic oracle**: checks parity, performance, review, or a bounded
  scientific claim without product-writing authority;
- **workflow gate**: enforces repository, CI, productization, or review state;
- **compatibility wrapper**: preserves a historical import or invocation while
  behavior moves inward;
- **protected unknown**: tracked evidence is insufficient to prove retirement;
- **retirement candidate**: the original decision is closed and a replacement
  owner or explicit retained-history rule exists.

Being under `scripts/`, having `main()`, being importable, being covered by a
test, or being included by package discovery does not by itself make a command
public. Conversely, a product artifact can be public even when its producing
command is not installed as a console script.

## Bounded Census

The 2026-07-23 clean-checkout census found:

| Surface | Count | Interpretation |
| --- | ---: | --- |
| `scripts/**/*.py` | 90 | Mixed installed commands, product adapters, gates, validation orchestration, and helpers |
| Installed `scripts` console commands | 5 | Stable command-name surface |
| `tools/**/*.py` | 208 | Includes diagnostics, testing helpers, and package markers |
| `tools/diagnostics/*.py` | 205 | 121 entry points, 83 imported helpers, and one package marker |
| Diagnostic entry points with tracked test import/reference | 118 / 121 | Wrapper-level coverage is missing for three entry points |
| Diagnostic helpers with a tracked importer | 83 / 83 | No helper is an automatic orphan candidate |

The 90 scripts divide into these bounded responsibility families:

| Family | Count | Examples and disposition |
| --- | ---: | --- |
| Package marker | 1 | `scripts/__init__.py`; retain while `scripts*` is packaged |
| Installed public CLI adapters | 5 | Extraction, Discovery, Pt-pattern Discovery, Alignment, and Alignment validation |
| Explicit compatibility wrapper | 1 | `csv_to_excel.py`; preserve `scripts.csv_to_excel.run` while callers remain |
| Data or product adapters | 5 | Instrument QC, ISTD trend, XLSX target import, Delta Mass detected-feature import, and local MS1 feature production |
| Repository or CI gates | 3 | Sandbox doctor, architecture census, diagnostics-index checker |
| Review-action tooling | 8 | Review planning, label import, changeset application, and review validation |
| Performance, parity, or validation tooling | 13 | Benchmarks, workbook comparisons, harnesses, and RAW/XIC equivalence characterization |
| Productization evidence and governance | 54 | Bounded build/check/activation/reconstruction chains; retain until their artifact and authority consumers are reowned |

These counts are a review snapshot, not a second machine registry. New files
must be classified by the rules above; they must not merely increment this
table. Exact membership for the 90-script snapshot is recorded in
[`script-lifecycle-family-snapshot-2026-07-23.tsv`](script-lifecycle-family-snapshot-2026-07-23.tsv);
it is a review artifact, not a runtime registry.

### Default Disposition For The Complete Census

The bounded inventory covers the remainder through the dated script membership
snapshot, the entry-by-entry diagnostic index, and explicit defaults:

- the five installed script commands are public adapters;
- the dated script snapshot gives every script a specific `exposure` and
  `lifecycle_role`; its broader `family` column is only a navigation and count
  aid;
- every entry point in `tools/diagnostics/INDEX.md` is a maintained operator
  interface and diagnostic oracle unless its index status or an exception below
  identifies product, workflow-gate, compatibility, protected-unknown, or
  retirement behavior;
- each of the 83 diagnostic helpers is an internal helper with at least one
  tracked importer and is retained while that importer remains.

These defaults classify invocation and lifecycle; they do not prove that every
surface has an off-repository operator. A deletion proposal must still run the
consumer and unique-capability gate below.

## Installed Commands

`pyproject.toml` currently declares one GUI entry point and five script-backed
CLI entry points:

| Command | Owner |
| --- | --- |
| `xic-extractor` | `gui.main:main` |
| `xic-extractor-cli` | `scripts.run_extraction:main` |
| `xic-discovery-cli` | `scripts.run_discovery:main` |
| `xic-pt-pattern-discovery-cli` | `scripts.run_pt_pattern_discovery:main` |
| `xic-align-cli` | `scripts.run_alignment:main` |
| `xic-align-validate-cli` | `scripts.run_alignment_validation:main` |

Do not infer that every other `run_*.py` is installed or public by filename.

## Contract-Bearing And Unique Script Surfaces

| Surface | Exposure / lifecycle | Evidence that prevents retirement |
| --- | --- | --- |
| `scripts/run_instrument_qc.py` | Contract-bearing product adapter | Produces Instrument-QC trends, method-document sequence manifests, injection-order projection, and `sample_metadata_v1` sidecars. This is an existing partial owner for the downstream handoff direction in #137. |
| `scripts/csv_to_excel.py` | Public compatibility wrapper | `scripts.csv_to_excel.run` is an explicit public contract and is used by extraction/workbook paths. |
| `scripts/xlsx_to_targets.py` | GUI/frozen-application adapter | Imported by the GUI and listed as a PyInstaller hidden import. |
| `scripts/prepare_delta_mass_detected_features.py` | Maintained data adapter | Converts supported external feature tables into the Discovery `detected_features.csv` input contract without RAW access. |
| `scripts/build_local_ms1_feature_table.py` | Maintained evidence producer | Unique single-RAW local MS1 feature-table generation path; diagnostic-only and RAW-backed. |
| `scripts/add_istd_rt_trend.py` | Maintained operator diagnostic | Visualizes measured ISTD RT drift. It must not infer or correct other-feature RT behavior. |
| `scripts/validate_raw_xic_batch_equivalence.py` | On-demand characterization oracle | The only focused multi-request comparison of single XIC calls versus batched/multi-trace acquisition. It has no tracked runtime consumer, but retains a unique batching regression question. |

`run_instrument_qc.py` currently attaches `injection_order` to SDOLEK trend
rows but does not sort its TSV/workbook rows by that value. SDOLEK RAW discovery
is filename-ordered and writers preserve the produced row order. Sorting the
compatibility projection is a known product behavior gap related to #137; this
inventory does not change it.

`validate_raw_xic_batch_equivalence.py` now compares `extract_xic()` with the
same batched implementation it delegates to, so its single-request comparison
is weak. Its multi-m/z/same-window comparison remains useful. Retire or replace
the script only after an acquisition/batching integration oracle owns that
claim.

## Diagnostic Tool Disposition

The diagnostic index is exhaustive for the 121 entry points. The following
cross-boundary surfaces require special protection:

| Surface | Consumer or unique role |
| --- | --- |
| `standard_peak_backfill_preset.py` | Imported by Alignment CLI and GUI workflows; not merely a standalone diagnostic |
| `family_ms1_overlay_batch.py` | Imported by GUI review and full evidence-chain orchestration; owns batched RAW overlay production |
| CID-NL inclusion/copy/adopt gates | Consume and emit productization activation artifacts; no deletion without reowning the activation chain |
| `docs_policy.py` | Shared docs-governance path and classification owner used by hooks and checks |
| `docs_management_audit.py` | CI-facing repository documentation audit |
| `tools/testing/test_shards.py` | CI test-shard contract, not diagnostic evidence |

Twelve diagnostic modules directly open RAW or own a RAW-reading path:

- `backfill_peakhypothesis_raw85_overlay.py`
- `backfill_scope_probe.py`
- `cid_nl_differential_overlay_review.py`
- `family_ms1_overlay_batch.py`
- `family_ms1_overlay_trace.py`
- `ms1_index_backfill_audit.py`
- `pt_pattern_same_scan_replay.py`
- `pt_pattern_scan_proxy.py`
- `pt_pattern_trace_diagnosis.py`
- `selected_envelope_plot_review.py`
- `target_pair_rt_candidate_plot_review.py`
- `tier2_raw_trace_reread_producer.py`

Do not merge or delete these paths based only on similar CLI arguments. First
prove equivalent RAW opens, chromatogram calls, batching/locality, trace
cropping, cache ownership, and artifact provenance.

Wrapper-level tests are currently absent for:

- `backfill_peakhypothesis_raw85_overlay.py`
- `family_ms1_overlay_evidence_cache_seed.py`
- `gallery_browser_smoke.py`

Each has lower-layer behavior or possible manual use, but standalone
cache-seed CLI use remains unverified. Missing wrapper coverage is a test or
consumer-knowledge gap, not retirement evidence.

## Retirement And Protected Unknowns

- `p1_resolver_default_gate.py` is a retirement candidate because its
  resolver-default decision is closed. It remains retained decision history
  while public/config compatibility still exposes the related token.
- `build_istd_false_missing_fixture.py` is `protected_unknown` until the
  regeneration need for its fixture is confirmed.
- `validate_raw_xic_batch_equivalence.py` is `protected_unknown` despite having
  no tracked runtime consumer because it owns a unique integration question.
- Absence of tracked imports, docs, CI, or tests is insufficient by itself to
  prove there is no manual or off-repository consumer.

The tracked-consumer search checked exact filenames and module/import names
across tracked imports, tests, GUI, CI/release configuration, product/docs
references, and reproduction surfaces. It excluded the source file, this
lifecycle owner, the diagnostic index, and the dated script snapshot because
those inventory surfaces cannot count as their own consumers. Its exact
`no proven tracked consumer` set is:

- `scripts/validate_raw_xic_batch_equivalence.py`;
- `tools/diagnostics/family_ms1_overlay_evidence_cache_seed.py`.

This result does not prove that no off-repository operator invokes either
command. Both remain `protected_unknown`, and neither is deletion-ready. Every
other script and indexed diagnostic entry point had at least one tracked
reference inside that bounded search.

The wider deletion review also retains surfaces whose consumer or unique
regeneration ownership is incomplete:

| Surface | Proven consumer state | Unique capability or remaining owner | Deletion disposition |
| --- | --- | --- | --- |
| `validate_raw_xic_batch_equivalence.py` | No tracked runtime caller; retained as an on-demand operator command | Multi-m/z, same-window acquisition/batching integration comparison must move to a maintained acquisition oracle | `protected_unknown`; not deletable |
| `family_ms1_overlay_evidence_cache_seed.py` | No tracked caller outside its own index entry; possible manual CLI use is unverified | Its underlying legacy-overlay cache seeding behavior is owned by `family_ms1_overlay_batch.seed_evidence_cache_from_overlay_summary`; removal still requires confirming that the standalone CLI is not an operator contract | `protected_unknown`; not deletable |
| `build_istd_false_missing_fixture.py` | Focused test imports its builder; no current campaign regeneration owner is recorded | Deterministic mapping from production-shaped QC/targeted evidence into the ISTD false-missing fixture needs a named regeneration owner and cadence | `protected_unknown`; not deletable |
| `p1_resolver_default_gate.py` | Focused tests remain; the production decision is closed | Historical default-decision evidence and compatibility for the still-accepted `region_first_safe_merge` token must be retired or reowned together | retirement candidate, currently blocked |
| Three entry points without wrapper-level tests | Lower-layer behavior exists or manual use remains unverified, as recorded above | Wrapper invocation coverage or consumer confirmation, not a unique product capability, is missing | coverage/knowledge gap; not retirement evidence |

Therefore the current deletion-ready set is empty.
Future candidates must name the exact import, CLI, GUI, CI, release,
documentation, artifact-regeneration, and off-repository/manual search boundary;
“no `rg` hit” is not a lifecycle decision.

Deletion requires all of the following:

1. no installed, product, downstream, GUI, CI, release, or reproduction
   consumer;
2. unique evidence and regeneration behavior has a named replacement owner;
3. affected public artifacts have parity or an approved expected diff;
4. the diagnostic index and relevant product/architecture owner are updated in
   the same change.

## Consolidation Seams

The baseline census found repeated low-level mechanics across `scripts/` and
`tools/diagnostics/`:

- 24 local `_write_tsv` definitions, including 22 under diagnostics;
- 26 local `_read_tsv` definitions, including 20 under diagnostics;
- 16 local `_format_value` definitions;
- 5 local `_write_json` definitions;
- 3 local `_sha256_file` definitions.

The raw counts overstate the missing infrastructure. Twenty-two of the 24 local
TSV writers already delegate to `xic_extractor.tabular_io.write_tsv`; their
wrappers retain artifact-specific field order, newline, parent-directory,
extra-column, or cell-format policy. The reader census likewise found 58 direct
`csv.DictReader` calls across 46 files while 70 files already use
`read_tsv_required`, 29 use `read_tsv_with_header`, and three use
`read_delimited_rows`. The missing work is adoption and policy separation, not
a fourth IO framework. After the first reader tracer described below, 57 direct
calls remain across 45 files.

### Bounded IO Adoption Map

| Mechanic | Existing owner | Disposition |
| --- | --- | --- |
| File SHA-256 | `xic_extractor.tabular_io.file_sha256` | Three identical whole-file helpers in the family-overlay, provisional-gate, and Tier-2 reread paths were replaced by the streaming shared helper. The uppercase digest contract is unchanged. |
| Diagnostic value formatting | `format_diagnostic_value` | Three exact delegates or equivalent implementations were collapsed. Other formatters intentionally differ on precision, `NaN`/`Inf`, bool text, `None`, or embedded newlines and remain local policy. |
| Schema-aware TSV/CSV reads | `read_delimited_rows`, `read_tsv_required`, and `read_tsv_with_header` | Migrate one consumer at a time. Shared readers reject duplicate or blank headers; `read_delimited_rows` additionally rejects ragged rows while `read_tsv_with_header` preserves the current `DictReader` representation. Each safety change must be explicit and tested rather than hidden in a refactor. |
| TSV writes | `write_tsv` | Keep thin artifact adapters when they bind stable serialization policy. The two remaining direct `DictWriter` scripts are not migrated until LF versus Windows-runtime CRLF is decided because tracked summaries bind artifact hashes. |
| JSON writes | No canonical owner | At least four callers share `indent=2` and `sort_keys=True`, but parent creation and trailing-newline behavior differ. Do not add a helper until byte-policy characterization proves a shared contract. |

The first reader tracer,
`tools/diagnostics/alignment_decision_report_io.py:read_tsv`, now delegates to
`read_tsv_with_header`. Focused tests preserve valid rows, field order,
missing-column wording, UTF-8 BOM handling, ragged-row representation, and the
missing-file wrapper. The explicit expected diff is fail-closed rejection of
duplicate and blank header names. No domain parsing, evidence selection, or
artifact-writing behavior moved with it.

The second representative tracer,
`tools/diagnostics/family_ms1_overlay_batch.py:_write_tsv`, now passes the
existing `_format_value` policy directly to `write_tsv` instead of
materializing a second fully formatted row collection. An exact-byte fixture
locks LF, field order, float, bool, `NaN`, and `None`; the trace artifact bytes
and their cache/provenance hashes are unchanged.

The following reader families are deliberately not first adopters even when
their `DictReader` bodies are short:

- truth lockbox and manual-review inputs;
- productization state or authority checks;
- Backfill activation and readiness inputs;
- selected-envelope and CID-NL evidence loaders;
- migration and compatibility projections.

Those consumers can affect truth, evidence classification, activation,
authority, or compatibility bytes. Reuse only their mechanical read layer, and
require focused schema and artifact parity before changing it.

This is evidence of incomplete adoption, not evidence that all orchestrators
should become one module.

Consolidate in this order:

1. Reuse `xic_extractor.tabular_io` for schema-aware tabular IO, formatting,
   hashing, and parsing before adding another local copy.
2. Characterize and then centralize repeated artifact identity, provenance,
   manifest, and summary mechanics.
3. Share stable CLI input/schema validation when at least two current callers
   have the same contract.
4. Reown RAW trace acquisition and batching only after call-cost and trace
   parity tests prove that the domain semantics match.

Keep method-specific candidate assembly, evidence interpretation, gate policy,
and artifact orchestration separate. A small file is not automatically a good
boundary, but a shared utility must represent shared semantics rather than only
similar syntax.
