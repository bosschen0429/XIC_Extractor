# Untargeted Output Reference

Column-level reference for all untargeted discovery and alignment output files.

This page documents the current public schemas. Changing them is a public
cutover that passes expected-diff, schema, downstream-consumer, and rollback
gates.

## Discovery Outputs

### `discovery_peaks.csv`

One row per physical MS1 peak per sample: one ion's XIC, one raw
valley-separated peak, one measurement. Each fact about a peak appears once:
the representative MS2 event and every other located event are rows of
`discovery_events.csv`, found by `peak_id` (and `best_ms2_scan_id` for the
representative); the measured trace and its evaluation are in
`discovery_physical_facts.json`. No cell grows with the number of MS2 scans.

| Column | Type | Description |
| --- | --- | --- |
| status | string | Whether the peak enters Alignment; see status values below |
| evidence_score | int | Evidence score 0–100 from the attached events, their RT match, product intensity, MS1 area and scan support |
| attached_event_count | int | Number of located events that attach to the peak |
| peak_id | string | `{sample}@mz{XIC center m/z}_rt{query RT}`; unique within the sample |
| sample_stem | string | Sample name (from filename) |
| peak_mz | float | The peak's ion mass, read by Alignment and Delta Mass: `ms1_apex_mz` when present, else the XIC center |
| precursor_mz_basis | string | Precursor source basis over the located events: `scan_precursor`, `scan_monoisotopic`, `product_plus_neutral_loss`, or `mixed` |
| ms1_apex_mz | float | MS1 ion m/z read at the peak apex when all of the peak's events are inferred (blank for a recorded precursor, two ions, or no reading) |
| ms1_apex_rt | float | MS1 peak apex RT (min) |
| ms1_peak_rt_start | float | MS1 peak start RT (min) |
| ms1_peak_rt_end | float | MS1 peak end RT (min) |
| ms1_area | float | MS1 peak area |
| ms1_height | float | MS1 peak height |
| best_ms2_scan_id | int | MS2 scan of the representative event in `discovery_events.csv` |
| neutral_loss_tag | string | NL tag of the representative event |
| matched_tag_names | string | Semicolon-separated NL tags of the located events, in configured tag order |

Status values, highest precedence first: `blank` (demoted by blank
qualification), `doubly_charged` (the MS1 ion reads as a doubly charged
isotope), `overlap` (shoulder or overlapping envelope), `no_nl_event` (no
attached NL event), `admitted`. Only `admitted` peaks enter Alignment.

Rows are ordered for review: peaks with two or more attached events whose
apex lies within the MS1 search padding of the representative event and
without a shoulder first, then the other peaks, then `blank` peaks; within
each, higher `evidence_score`, more attached events, stronger product
intensity and larger area first.

---

### `discovery_events.csv`

One row per strict NL event (MS2 scan, tag and product) per sample; the
sample is the folder and its `discovery_batch_index.csv` row. Each event
names the peak it locates and, when it does not attach there, why. Alignment
reads its evidence from this table.

| Column | Type | Description |
| --- | --- | --- |
| ms2_scan_number | int | MS2 scan number |
| ms2_rt | float | MS2 scan RT (min) |
| neutral_loss_tag | string | NL tag |
| product_mz | float | Product ion m/z |
| product_intensity | float | Product ion intensity |
| precursor_mz | float | Precursor m/z |
| precursor_mz_basis | string | Precursor source basis |
| scan_precursor_mz | float | Scan-observed precursor m/z |
| precursor_monoisotopic_mz | float | Vendor-recorded monoisotopic m/z (blank when not recorded) |
| precursor_charge | int | Vendor-recorded charge (blank when not recorded) |
| isolation_width_da | float | Isolation window width (Da; blank when not recorded) |
| isolation_offset_da | float | Isolation window offset (Da; blank when not recorded) |
| master_scan_number | int | Parent MS1 scan the MS2 was selected from (blank when not recorded) |
| parent_ms1_scan_number | int | Parent MS1 scan read for an inferred precursor (blank when not read) |
| parent_ms1_rt | float | Parent MS1 scan RT (min; blank when not read) |
| parent_ms1_centroid_mzs | string | Semicolon-separated centroids in the parent MS1 precursor window; one value is the ion, several are ambiguous, blank with a scan number is none |
| observed_neutral_loss_da | float | Observed neutral loss (Da) |
| neutral_loss_mass_error_ppm | float | Descriptive NL mass error |
| precursor_verified | bool | The precursor verifies the configured loss |
| xic_mz | float | XIC center of the event's ion |
| peak_id | string | Peak the event locates, attached or not; blank only when the ion has no measured peak at the event RT |
| unattached_reason | string | Why the event does not attach to its located peak, or the physical reason it locates none; blank when attached |

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
| peak_csv_schema_version | string | Peak CSV format |
| peak_csv_sha256 | string | SHA-256 of the peak CSV bytes |
| peak_csv_byte_size | int | Peak CSV byte size |
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
cache. Discovery tables written by another version, whose columns differ from
the ones listed here, are rejected with a request to rerun Discovery; existing
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
| *{sample_name}* | float | Area value for sample (blank when the cell has no value) |

Sample columns follow the order determined by `matrix.sample_order` (typically
injection order when an injection-order source is provided).

The TSV uses stable display text for people and spreadsheet consumers:

- `Mz` is intentionally rounded to exactly four digits after the decimal
  point. It is a display coordinate, not machine identity.
- Nonblank sample areas use lowercase scientific notation with five digits
  after the decimal point (for example, `1.23457e+03`).
- `RT` keeps its existing compact numeric rendering, and sample cells without
  a value remain blank.

Machine consumers bind Matrix rows to the nonblank `matrix_row_index` subset
of `alignment_review.tsv`. Its `group_center_mz` and `group_center_rt` keep
machine precision for every group, in Matrix or not. Consumers never
reconstruct machine coordinates from rounded Matrix display.

Review row order is free: the explicit `matrix_row_index` values must cover
exactly 1..N, and each indexed row's `group_center_mz` (at display precision)
and `group_center_rt` must match its Matrix row's `Mz` and `RT`. Missing or
duplicated rows are rejected. Sample values are not cross-checked, so two rows with the
same displayed coordinates can be swapped undetected. This is an association
check, not scientific authenticity or activation authority.
The old identity sidecar is not emitted or accepted by these readers.

---

### `alignment_review.tsv`

One row per cross-sample group. Alignment quality and identity decisions.
`feature_id` is the group label (for example `F000001`), shared by the
report tables. It does not assert group identity across different input sets.
Each fact appears once, and no cell grows with the number of samples or
members; a group's member peaks are in the Inspector and the explanation.

#### Core Identity

| Column | Type | Description |
| --- | --- | --- |
| feature_id | string | Report-scoped group label (`F000001`) shared by the report tables |
| matrix_row_index | int | The group's Matrix row (1..N); blank when the group is not in Matrix |
| neutral_loss_tag | string | NL identifier |
| group_center_mz | float | Center m/z: the group's centre, the mean of its members' `peak_mz`; Backfill searches the same value |
| group_center_rt | float | Center RT (min): the mean apex RT of its members |
| group_product_mz | float | Product m/z |
| group_observed_neutral_loss_da | float | Observed NL (Da) |

#### Detection Counts

| Column | Type | Description |
| --- | --- | --- |
| detected_count | int | Detected sample count |
| not_searched_count | int | Samples whose cell was not searched |
| unresolved_count | int | Samples whose candidate evidence did not resolve to one peak |

#### Identity Decision

| Column | Type | Description |
| --- | --- | --- |
| identity_reason | string | The group's disposition. Only `established` groups enter Matrix; `unresolved`, `rejected` and other dispositions stay in Review with a blank `matrix_row_index` |

#### Quantification

| Column | Type | Description |
| --- | --- | --- |
| accepted_cell_count | int | Cells with a Matrix value |
| accepted_backfill_count | int | Of those, cells filled by Backfill |

#### Context

| Column | Type | Description |
| --- | --- | --- |
| membership_risk_flags | string | Semicolon-separated kinds of risk accepted when the group's members or Backfills were chosen (each kind listed once) |
| warning | string | Warning flag (`no_anchor`, `high_not_searched`, `high_backfill_dependency`) |

`high_backfill_dependency` marks rows with Backfill cells but no direct
detection. Review uses the recorded cell decisions to determine this warning.

### `alignment_cells.tsv`, `alignment_backfill_cell_evidence.tsv`

One row per cell (`alignment_cells.tsv`, written on request) or per cell with
a peak in a group that has a Backfill cell (`alignment_backfill_cell_evidence.tsv`,
validation output). Both identify the group by `feature_id` only; its centre
and tag are in Review. `area` is the Matrix value; `primary_matrix_area_source`
names how it was integrated or, for a cell without a value, why not. The
Backfill evidence file adds `backfill_query_rt_min` and `backfill_query_rt_max`.

### `alignment_run_metadata.json`

The inputs and settings of the run: `discovery_batch_index`, `raw_dir`,
`dll_dir`, `output_level`, `resolver_mode`, `baseline_integration_method`,
`signal_processing_profile_json`, `mass_recalibration_by_sample`,
`processing_attempt_id`, the `known_istd_reference_*` keys when an ISTD
reference was used, and `backfill_execution` when Backfill did not run.

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
format.
