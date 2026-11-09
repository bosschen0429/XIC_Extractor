# Untargeted Output Reference

Column-level reference for all untargeted discovery and alignment output files.

This page documents the current public schemas. Changing them is a public
cutover that passes expected-diff, schema, downstream-consumer, and rollback
gates.

## Discovery Outputs

### `discovery_peaks.csv`

One row per physical MS1 peak per sample: one ion channel, one raw
valley-separated peak, one measurement. Full provenance for audit.

#### Review Columns

| Column | Type | Description |
| --- | --- | --- |
| review_priority | string | `HIGH`, `MEDIUM`, or `LOW` |
| admitted | bool | `TRUE` only when `admission_reason` is `admitted` |
| admission_reason | string | See admission values below |
| evidence_tier | string | Confidence tier (A–E) |
| evidence_score | int | Numeric evidence score |
| ms2_support | string | MS2 evidence level |
| ms1_support | string | MS1 peak support level |
| rt_alignment | string | RT alignment quality |

Admission values, highest precedence first: `system_background`,
`doubly_charged_isotope`, `requires_overlap_review`, `no_attached_event`,
`admitted`. Only `admitted` peaks enter Alignment.

#### Identity Columns

| Column | Type | Description |
| --- | --- | --- |
| peak_id | string | `{sample}@mz{channel_mz}_rt{raw apex RT}`; unique within the sample |
| channel_mz | float | XIC m/z of the ion channel that measured the peak |

#### Measurement Columns

| Column | Type | Description |
| --- | --- | --- |
| precursor_mz | float | Representative event's precursor m/z |
| product_mz | float | Representative event's product ion m/z |
| observed_neutral_loss_da | float | Observed neutral loss (Da) |
| ms1_apex_rt | float | MS1 peak apex RT (min) |
| ms1_area | float | MS1 peak area |
| attached_event_count | int | Number of events attached to the peak |
| best_seed_rt | float | Representative event's MS2 RT (min) |
| ms2_product_max_intensity | float | Maximum product ion intensity over located events |
| reason | string | Inclusion/flag reason |

#### Flag Columns

| Column | Type | Description |
| --- | --- | --- |
| requires_overlap_review | bool | Shoulder or overlapping envelope |
| doubly_charged_isotope | bool | The MS1 ion reads as a doubly charged isotope |
| blank_background | bool | Demoted by blank qualification |
| is_provisional | bool | Peak shape is provisional or overlapping |

#### Provenance Columns

| Column | Type | Description |
| --- | --- | --- |
| raw_file | string | RAW data file path |
| sample_stem | string | Sample name (from filename) |
| best_ms2_scan_id | int | Representative event's MS2 scan number |
| seed_scan_ids | string | Semicolon-separated MS2 scans of every event located on the peak |
| neutral_loss_tag | string | NL identifier/tag |
| configured_neutral_loss_da | float | Configured NL mass |
| neutral_loss_mass_error_ppm | float | Descriptive NL mass error (ppm of configured loss); acceptance uses the quadrature per-ion m/z window |
| neutral_loss_error_basis | string | Error measurement basis |
| precursor_mz_basis | string | Precursor source basis |
| scan_precursor_mz | float | Scan-observed precursor m/z |
| scan_precursor_delta_da | float | Precursor m/z deviation |
| max_scan_precursor_abs_delta_da | float | Maximum precursor deviation |
| rt_seed_min | float | Earliest located event RT |
| rt_seed_max | float | Latest located event RT |
| ms1_search_rt_min | float | MS1 search window min |
| ms1_search_rt_max | float | MS1 search window max |
| ms1_seed_delta_min | float | Seed-to-MS1 RT delta |
| ms1_peak_rt_start | float | MS1 peak start RT |
| ms1_peak_rt_end | float | MS1 peak end RT |
| ms1_height | float | MS1 peak height |
| ms1_apex_mz | float | MS1 ion m/z read at the peak apex for an inferred precursor (blank for selected precursors, two ions, or no reading); reported only |
| ms1_trace_quality | string | `good`, `fair`, or `poor` |
| ms1_scan_support_score | float | Scan matching score |
| selected_tag_count | int | Selected NL tag count |
| matched_tag_count | int | Matched tag count |
| matched_tag_names | string | Semicolon-separated tag names |
| primary_tag_name | string | Primary NL tag |
| tag_combine_mode | string | `single`, `union`, or `intersection` |
| tag_intersection_status | string | `not_required`, `complete`, or `incomplete` |
| tag_evidence_json | string | JSON evidence details |

---

### `discovery_events.csv`

One row per strict NL event (MS2 scan, tag and product) per sample. Each event
names the peak it attaches to, or why it attaches to none.

| Column | Type | Description |
| --- | --- | --- |
| sample_stem | string | Sample name |
| ms2_scan_number | int | MS2 scan number |
| ms2_rt | float | MS2 scan RT (min) |
| neutral_loss_tag | string | NL tag |
| product_mz | float | Product ion m/z |
| product_intensity | float | Product ion intensity |
| precursor_mz | float | Precursor m/z |
| precursor_mz_basis | string | Precursor source basis |
| scan_precursor_mz | float | Scan-observed precursor m/z |
| parent_ms1_rt | float | Parent MS1 scan RT (min; blank when not read) |
| parent_ms1_mz | float | Parent MS1 centroid m/z (blank when not read) |
| observed_neutral_loss_da | float | Observed neutral loss (Da) |
| neutral_loss_mass_error_ppm | float | Descriptive NL mass error |
| supports_nl | bool | The precursor verifies the configured loss |
| channel_mz | float | XIC m/z of the event's ion channel |
| peak_id | string | Attached peak; blank when unattached |
| unattached_reason | string | Why the event attaches to no peak; blank when attached |

---

### `discovery_batch_index.csv`

One row per sample. Links each sample to its output files.

| Column | Type | Description |
| --- | --- | --- |
| schema_version | string | `discovery_batch_index_peaks` |
| sample_stem | string | Sample name |
| raw_file | string | RAW file path |
| peak_csv | string | Path to `discovery_peaks.csv` |
| event_csv | string | Path to `discovery_events.csv` |
| peak_count | int | Total peaks |
| admitted_peak_count | int | Admitted peaks |
| event_count | int | Total events |
| high_count | int | HIGH priority count |
| medium_count | int | MEDIUM priority count |
| low_count | int | LOW priority count |
| peak_artifact_schema_version | string | Peak CSV format |
| peak_artifact_sha256 | string | SHA-256 of the peak CSV bytes |
| peak_artifact_byte_size | int | Peak CSV byte size |
| physical_facts_sha256 | string | SHA-256 of the sample's `discovery_physical_facts.json` bytes |
| physical_facts_byte_size | int | `discovery_physical_facts.json` byte size |
| acquisition_local_file_revision | string | JSON array of device, file identity, size, modification and creation/change timestamps |
| acquisition_byte_size | int | RAW file byte size |
| acquisition_format | string | Acquisition format, for example `thermo_raw` |
| observation_namespace | string | Adapter-readable observation namespace |
| mass_recalibration | string | Recalibration result; omitted when vendor masses were used unchanged |

Alignment rejects a peak CSV or physical-facts file whose bytes no longer
match these fingerprints.
RAW sources use local file revisions without scanning the file contents.
Moving or copying RAW files requires regenerating Discovery and its acquisition
cache. Earlier Discovery source schemas must also be regenerated; existing
Matrix, Review, and explanation reports remain readable.

---

## Alignment Outputs

### `alignment_matrix.tsv`

Tab-separated matrix: aligned features as rows, samples as columns. This is
the primary cross-sample quantitative output.

| Column | Type | Description |
| --- | --- | --- |
| Mz | float | Family center m/z |
| RT | float | Family center RT (min) |
| *{sample_name}* | float | Area value for sample (blank if absent/unchecked) |

Sample columns follow the order determined by `matrix.sample_order` (typically
injection order when an injection-order source is provided).

The TSV uses stable display text for people and spreadsheet consumers:

- `Mz` is intentionally rounded to exactly four digits after the decimal
  point. It is a display coordinate, not machine identity.
- Nonblank sample areas use lowercase scientific notation with five digits
  after the decimal point (for example, `1.23457e+03`).
- `RT` keeps its existing compact numeric rendering, and absent or unchecked
  sample cells remain blank.

Machine consumers bind Matrix rows to the nonblank `matrix_row_index` subset
of `alignment_review.tsv`. Its `matrix_mz` and `matrix_rt` retain machine
precision; `family_center_mz` remains available for groups outside Matrix.
Consumers never reconstruct machine coordinates from rounded Matrix display.

Each Matrix-backed Review row contains `matrix_row_binding_sha256`, covering
the complete Matrix header, its row values and the explicit Review Matrix
projection fields. Missing, duplicated, cross-bound or substituted rows are
rejected even when displayed coordinates coincide. Review row order is free:
its explicit indices must cover exactly 1..N. This is an integrity and
association check, not scientific authenticity or activation authority.
The old identity sidecar is not emitted or accepted by these readers.

---

### `alignment_review.tsv`

One row per cross-sample group. Alignment quality and identity decisions.
`feature_id` is the group label (for example `F000001`), shared by the
report tables. It does not assert group identity across different input sets.

#### Core Identity

| Column | Type | Description |
| --- | --- | --- |
| feature_id | string | Report-scoped group label (`F000001`) shared by the report tables |
| neutral_loss_tag | string | NL identifier |
| family_center_mz | float | Center m/z: mean of direct members' Discovery `ms1_apex_mz` when any member has one (`center_mz_basis = discovery_ms1_apex_mean`), otherwise the mean of observed m/z |
| family_center_rt | float | Center RT (min) |
| family_product_mz | float | Product m/z |
| family_observed_neutral_loss_da | float | Observed NL (Da) |

#### Detection Counts

| Column | Type | Description |
| --- | --- | --- |
| has_anchor | bool | Has anchor evidence |
| physical_peak_count | int | Number of physical peaks in the finalized group |
| physical_peak_keys | string | Semicolon-separated physical peak keys; not event cluster IDs |
| detected_count | int | Detected sample count |
| absent_count | int | Absent sample count |
| unchecked_count | int | Unchecked sample count |
| duplicate_assigned_count | int | Duplicate assignment count |
| ambiguous_ms1_owner_count | int | Ambiguous ownership count |
| present_rate | float | Detection rate (0.0–1.0) |

#### Identity Decision

| Column | Type | Description |
| --- | --- | --- |
| row_identity_decision | string | Identity status |
| row_identity_confidence | string | Confidence level |
| row_identity_reason | string | Reason text |
| row_flags | string | Semicolon-separated flags |
| identity_decision | string | Identity decision |
| identity_confidence | string | Decision confidence |
| primary_evidence | string | Main evidence type |
| identity_reason | string | Detailed reason |

#### Quantification

| Column | Type | Description |
| --- | --- | --- |
| quantifiable_detected_count | int | Detected and quantifiable |
| quantifiable_backfill_count | int | Rescued and quantifiable |
| accepted_cell_count | int | Accepted cells |
| accepted_backfill_count | int | Accepted rescues |
| review_backfill_count | int | Review-tier rescues |
| include_in_primary_matrix | bool | Included in output matrix |

#### Adduct Annotation

| Column | Type | Description |
| --- | --- | --- |

#### Context

| Column | Type | Description |
| --- | --- | --- |
| representative_samples | string | Up to 5 sample names with detection |
| family_evidence | string | Evidence status recorded by the finalized owner |
| warning | string | Warning flag (`no_anchor`, `high_unchecked`, `high_backfill_dependency`) |
| reason | string | Comprehensive description |

`high_backfill_dependency` marks rows with Backfill cells but no direct
detection. Review uses the recorded cell decisions, not the wording of
`family_evidence`, to determine this warning. Physical peak counts do not
indicate how many event clusters were merged.

## Explanation format and reruns

The file is `alignment_explanation.sqlite`, requested explicitly with
`--emit-alignment-explanation`. Its candidate records retain each sample's
measured `ms1_apex_mz`; missing measurements are null, not the row-center m/z.
Its cell table retains `apex_rt_min` directly from the owner's selected
integration, including MS1-only Backfill. Downstream cell readers do not infer
this apex from observation order or optional TSV ledgers; cells with no
selected integration retain null.
The SQLite explanation uses `alignment_explanation` (full audit) or
`alignment_explanation_review` (compact review), with `curve_payload`
for retained curves. Curve provenance records query and selection context,
not full-trace or selection-trace SHA-256 values. Scientific values and
curve arrays are unchanged. Artifact and curve identifiers are internal links
within the generated output and must be regenerated together.

Inspector and Workbench require the current explanation format. Older
explanations are rejected with a message to rerun Alignment; no conversion
or compatibility reader is provided. This does not change the Matrix row ID
format or its row-binding column.
