# Targeted Config Reference

Complete field reference for the two config files used by Targeted Extraction.
Runtime copies (`config/settings.csv`, `config/targets.csv`) are created from
the example files after Save or Run. Do not commit runtime copies — they
contain local machine paths.

## Settings (`settings.csv`)

### Paths

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `data_dir` | path | *(none)* | Directory containing `.raw` files to process |
| `dll_dir` | path | machine-specific DLL directory | Directory containing Thermo RawFileReader DLLs |
| `injection_order_source` | path | *(empty)* | Optional CSV/XLSX with `Sample_Name` and `Injection_Order` columns |

### Peak Detection

Targeted and Untargeted use the same physical signal and peak-boundary owner.
The same trace, context, profile and physical question produce the same facts.
Gaussian smoothing and the fixed AsLS baseline define the signal; Targeted
reference and ISTD evidence select a component without recomputing its area.

| Field | Type | Default | Valid range | Description |
| --- | --- | --- | --- | --- |
| `signal_processing_method` | enum | `gaussian` | `gaussian` | Shared physical signal smoothing method |
| `signal_processing_window_points` | int | 15 | odd, 3–999 | Gaussian smoothing window (points) |
| `ms1_morphology_smoothing_window_points` | int | 15 | odd, >= 3 | MS1 morphology Gaussian smoothing window (points) |

### Neutral Loss Confirmation

| Field | Type | Default | Valid range | Description |
| --- | --- | --- | --- | --- |
| `ms2_precursor_tol_da` | float | 1.6 | > 0 | MS2 precursor m/z matching window (Da). Typically quadrupole isolation width + 0.4 |
| `nl_min_intensity_ratio` | float | 0.01 | 0 – 1 | NL product must reach this fraction of the scan's base peak |
| `nl_rt_anchor_search_margin_min` | float | 2.0 | > 0 | Search radius around `rt_center` for NL anchor MS2 (minutes) |
| `nl_rt_anchor_half_window_min` | float | 1.0 | > 0 | XIC half-width when NL anchor is found (minutes) |
| `nl_fallback_half_window_min` | float | 2.0 | > 0 | XIC half-width when no NL anchor is found (minutes) |

### Detection Rules

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `count_no_ms2_as_detected` | bool | `false` | Count samples without MS2 triggers as detected (compensates for DDA stochasticity) |
| `dirty_matrix_mode` | bool | `false` | Relax S/N and tighten peak shape criteria for complex matrices (e.g., urine) |

### ISTD RT Prior

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `rolling_window_size` | int | 5 | Rolling window radius for ISTD RT prior (±N injections) |

### Debug and Audit

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `keep_intermediate_csv` | bool | `false` | Retain intermediate CSV files |
| `emit_score_breakdown` | bool | `false` | Add Score Breakdown sheet to workbook |
| `emit_review_report` | bool | `false` | Generate Review Report HTML |
| `emit_peak_candidates` | bool | `false` | Output Peak Candidate TSV for audit |
| `baseline_audit_method` | enum | *(empty)* | Shadow baseline method: empty or `asls` |

### Parallelization

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `parallel_mode` | enum | `process` | Execution backend: `serial` or `process` |
| `parallel_workers` | int | CPU count - 1 | Worker count for process mode |

### Developer-Only

These are rarely needed and not exposed in the GUI by default.

| Field | Type | Description |
| --- | --- | --- |
| `rt_prior_library_path` | path | Debug RT prior library CSV |
| `target_pair_rt_calibration_path` | path | Target-pair RT calibration TSV for shadow diagnostics |
| `model_selection_expected_diff_approval_registry` | path | Expected-diff approval registry TSV |
| `targeted_ms1_shape_identity_support_tsv` | path | Reviewed MS1 shape-identity support TSV |
| `targeted_ms1_shape_identity_activation_policy` | enum | `limited_5hmdc_5medc` or `explicit_support_tsv` |

---

## Targets (`targets.csv`)

Each row defines one compound to extract. The `label` column becomes the
identifier in workbook output and downstream CSV files.

### Required Columns

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `label` | string | unique, non-empty | Compound name or identifier |
| `mz` | float | > 0 | Target mass-to-charge ratio |
| `rt_min` | float | >= 0, < `rt_max` | RT window start (minutes) |
| `rt_max` | float | > `rt_min` | RT window end (minutes) |
| `ppm_tol` | float | > 0 | m/z matching tolerance (ppm) |

### Neutral Loss Columns

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `neutral_loss_da` | float | > 0, < `mz`; empty to disable | Expected neutral-loss mass (Da) |
| `nl_ppm_warn` | float | > 0; required if `neutral_loss_da` set | Warning threshold on descriptive ppm-of-loss error; does not determine NL acceptance |
| `nl_ppm_max` | float | >= `nl_ppm_warn`; required if `neutral_loss_da` set | Per-ion precursor/product m/z tolerance (ppm) for the quadrature NL match window |

### ISTD Pairing Columns

| Column | Type | Default | Description |
| --- | --- | --- | --- |
| `is_istd` | bool | `false` | Whether this target is an internal standard |
| `istd_pair` | string | *(empty)* | Label of the paired ISTD. Must reference a row with `is_istd=true`. Empty if this row is itself an ISTD |
| `isotope_label_type` | enum | `unknown` | `unknown`, `deuterated`, or `heavy_non_deuterium` |
| `paired_rt_relation` | enum | `none` | `none`, `istd_not_later_than_pair`, or `learned_delta_only` |
| `sample_applicability` | enum | `all` | `all` or `rna_containing` |

### ISTD Pairing Rules

- If `is_istd=true`: do not set `istd_pair` or `paired_rt_relation`.
- If `istd_pair` is set: `is_istd` must be `false`, and the referenced label
  must have `is_istd=true`.
- `paired_rt_relation=istd_not_later_than_pair` requires the paired ISTD to
  have `isotope_label_type=deuterated`.
