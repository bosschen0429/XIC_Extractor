# Untargeted Output Reference

Column-level reference for all untargeted discovery and alignment output files.

This page documents the current public schemas. The frozen successor identity
uses internal `group_hypothesis_id` plus projection-owned `UntargetRowKey` and
`UntargetedDeliveryCellKey`; none replaces `feature_family_id` or changes these
files until a separately versioned public cutover passes expected-diff, schema,
downstream-consumer, and rollback gates.

## Discovery Outputs

### `discovery_candidates.csv`

One row per candidate feature per sample. Full provenance for audit.

#### Review Columns

| Column | Type | Description |
| --- | --- | --- |
| review_priority | string | `HIGH`, `MEDIUM`, or `LOW` |
| evidence_tier | string | Confidence tier (A–E) |
| evidence_score | int | Numeric evidence score |
| ms2_support | string | MS2 evidence level |
| ms1_support | string | MS1 feature finding status |
| rt_alignment | string | RT alignment quality |

#### Identity Columns

| Column | Type | Description |
| --- | --- | --- |
| candidate_id | string | Unique candidate identifier |
| feature_family_id | string | Per-sample peak anchor label for candidates sharing the same discovered MS1 peak |
| feature_family_size | int | Number of members in the family |

Legacy `family_context` and `feature_superfamily_*` columns are not part of the
current Discovery output schema. Consumers should use the listed columns rather
than requiring those removed fields.

#### Measurement Columns

| Column | Type | Description |
| --- | --- | --- |
| precursor_mz | float | Precursor m/z |
| product_mz | float | Product ion m/z |
| observed_neutral_loss_da | float | Observed neutral loss (Da) |
| best_seed_rt | float | Best MS2 scan retention time (min) |
| seed_event_count | int | Number of seed events |
| ms1_peak_found | bool | Whether MS1 peak was located |
| ms1_apex_rt | float | MS1 peak apex RT (min; null if not found) |
| ms1_area | float | MS1 peak area (null if not found) |
| ms2_product_max_intensity | float | Maximum product ion intensity |
| reason | string | Inclusion/flag reason |

#### State Columns

| Column | Type | Description |
| --- | --- | --- |
| discovery_candidate_state | string | See state values below |
| ms1_feature_row_id | string | Cross-reference to MS1 feature matrix |

State values: `ms1_feature_nl_supported`, `ms1_feature_nl_rescued`,
`review_only_orphan_nl`, `review_only_ambiguous_coisolation`,
`rejected_noise_or_outside_rt`.

`review_only_ambiguous_coisolation` includes otherwise feature-backed pure
inferred precursor hypotheses that share one sample/tag/best-MS2-scan context
with a stronger inferred hypothesis. They remain reviewable but do not
independently enter Alignment RAW owner build.

#### Provenance Columns

| Column | Type | Description |
| --- | --- | --- |
| raw_file | string | RAW data file path |
| sample_stem | string | Sample name (from filename) |
| best_ms2_scan_id | int | Best MS2 scan number |
| seed_scan_ids | string | Semicolon-separated scan IDs |
| neutral_loss_tag | string | NL identifier/tag |
| configured_neutral_loss_da | float | Configured NL mass |
| neutral_loss_mass_error_ppm | float | NL mass error (PPM) |
| neutral_loss_error_basis | string | Error measurement basis |
| precursor_mz_basis | string | Precursor source basis |
| scan_precursor_mz | float | Scan-observed precursor m/z |
| scan_precursor_delta_da | float | Precursor m/z deviation |
| max_scan_precursor_abs_delta_da | float | Maximum precursor deviation |
| rt_seed_min | float | Seed RT minimum |
| rt_seed_max | float | Seed RT maximum |
| ms1_search_rt_min | float | MS1 search window min |
| ms1_search_rt_max | float | MS1 search window max |
| ms1_seed_delta_min | float | Seed-to-MS1 RT delta |
| ms1_peak_rt_start | float | MS1 peak start RT |
| ms1_peak_rt_end | float | MS1 peak end RT |
| ms1_height | float | MS1 peak height |
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

### `discovery_review.csv`

Compact triage index — one row per candidate, subset of columns from
`discovery_candidates.csv` plus a constructed `review_note`.

| Column | Type | Description |
| --- | --- | --- |
| review_priority | string | `HIGH`, `MEDIUM`, or `LOW` |
| evidence_tier | string | Confidence tier |
| evidence_score | int | Numeric score |
| ms2_support | string | MS2 evidence |
| ms1_support | string | MS1 finding status |
| rt_alignment | string | RT alignment quality |
| candidate_id | string | Unique ID |
| precursor_mz | float | Precursor m/z |
| best_seed_rt | float | Best seed RT (min) |
| ms1_area | float | MS1 area (if found) |
| seed_event_count | int | Seed event count |
| neutral_loss_tag | string | NL tag |
| matched_tag_names | string | Tag names |
| matched_tag_count | int | Tag count |
| tag_intersection_status | string | Intersection status |
| review_note | string | Human-readable summary |

---

### `discovery_batch_index.csv`

One row per sample. Links each sample to its output files.

| Column | Type | Description |
| --- | --- | --- |
| sample_stem | string | Sample name |
| raw_file | string | RAW file path |
| candidate_csv | string | Path to `discovery_candidates.csv` |
| review_csv | string | Path to `discovery_review.csv` |
| candidate_count | int | Total candidates |
| high_count | int | HIGH priority count |
| medium_count | int | MEDIUM priority count |
| low_count | int | LOW priority count |

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

Machine consumers that need the unrounded m/z must bind each Matrix row to
`alignment_matrix_identity.tsv`; its `Mz` field uses round-trip-safe text.
`alignment_review.tsv.family_center_mz` also carries round-trip-safe text for
activation of rows that are not yet present in the Matrix. Activation never
reconstructs machine m/z from the four-decimal Matrix display. Two distinct
identity rows may therefore show the same four-decimal `Mz`.

New identity sidecars declare
`untargeted_peak_hypothesis_matrix_identity`. Each row contains
`matrix_row_binding_sha256`, which seals the complete ordered public Matrix row
to the complete identity row. A missing, duplicated, reordered, cross-bound, or
substituted row is rejected even when two rows have the same displayed `Mz` and
`RT`. This digest is an integrity and association check; it does not grant
scientific authenticity or activation authority.

This display formatting does not change row inclusion, row or sample ordering,
blank placement, RT, scientific selection, or activation authority. Legacy
Matrix/identity pairs using the earlier compact `.6g` m/z text remain accepted
only when the sidecar explicitly declares the schema and the Matrix and
identity m/z texts match exactly. Precision already absent from a legacy
sidecar cannot be recovered.

---

### `alignment_review.tsv`

One row per cross-sample group. Alignment quality and identity decisions.
`feature_family_id` is the stable public row/display label retained for output
compatibility; pipeline identity decisions should use `group_hypothesis_id`
where that internal or sidecar identity is available.

#### Core Identity

| Column | Type | Description |
| --- | --- | --- |
| feature_family_id | string | Stable public row/display label for the cross-sample group |
| neutral_loss_tag | string | NL identifier |
| family_center_mz | float | Center m/z |
| family_center_rt | float | Center RT (min) |
| family_product_mz | float | Product m/z |
| family_observed_neutral_loss_da | float | Observed NL (Da) |

#### Detection Counts

| Column | Type | Description |
| --- | --- | --- |
| has_anchor | bool | Has anchor evidence |
| event_cluster_count | int | Merged event cluster count |
| event_cluster_ids | string | Semicolon-separated cluster IDs |
| event_member_count | int | Total member count |
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
| artificial_adduct_role | string | Adduct role (if applicable) |
| artificial_adduct_name | string | Adduct name |
| artificial_adduct_related_family_id | string | Parent family ID |
| artificial_adduct_mz_delta_error_ppm | float | Adduct m/z error (PPM) |
| artificial_adduct_rt_delta_min | float | Adduct RT delta (min) |

#### Context

| Column | Type | Description |
| --- | --- | --- |
| representative_samples | string | Up to 5 sample names with detection |
| family_evidence | string | Family fold evidence type |
| warning | string | Warning flag (`no_anchor`, `high_unchecked`, `high_backfill_dependency`) |
| reason | string | Comprehensive description |
