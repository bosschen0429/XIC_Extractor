# Targeted Config Reference

Targeted has two input tables. Local `config/settings.csv` and `config/targets.csv`
are not tracked; the example files show the current format. Unsupported columns
or settings produce an error rather than becoming hidden scientific overrides.

## Batch and machine settings (`settings.csv`)

| Key | Meaning |
| --- | --- |
| `data_dir` | Thermo RAW batch directory |
| `dll_dir` | Thermo RawFileReader DLL directory, shared with Untargeted |
| `injection_order_source` | Optional CSV/XLSX sample injection order; blank uses RAW mtime |

Scientific policy and process scheduling are platform-owned. The normal GUI and
Targeted CLI do not offer smoothing, tolerance, anchor-window, Dirty Matrix,
serial/process or worker controls. The run metadata records the effective
versioned policy and actual resource allocation.

## Target intent (`targets.csv`)

| Column | Meaning |
| --- | --- |
| `label` | Unique target name; becomes a public output identifier |
| `mz` | Positive precursor m/z |
| `expected_rt_min` | Expected retention time **in minutes**, not a lower RT bound |
| `cid_nl_tag` | `dR`, `R`, or `MeR`; chemistry and tolerances resolve from the corresponding shared built-in Discovery recipe |
| `role` | `analyte` or `istd` |
| `istd_pair` | Required ISTD label for an analyte; blank for an ISTD |
| `sample_applicability` | Optional `all` (default) or `rna_containing` |

Additional descriptive metadata is retained. Retired scientific fields such as
`rt_min`, `rt_max`, `ppm_tol`, `neutral_loss_da`, `nl_ppm_warn`, `nl_ppm_max`, and
`is_istd` are not metadata and are rejected. There is no automatic conversion.
Expected RT guides identity, including the declared analyte–ISTD offset; it does
not crop the acquired candidate universe.

## Portable method

A method folder contains `targets.csv` and `settings.csv`. The latter contains
only `strategy_version,targeted-cid-nl-v2` under the `key,value` header.
Batch and machine paths do not travel with the method. Loading replaces the
whole target intent; unsupported strategy versions are rejected.

## Evidence and outputs

A missing MS2 event does not suppress exact-mass MS1 search and does not create
CID-NL support. Reported value, counted detection and review are separate outputs.
Read [Targeted extraction](targeted-extraction.md) for the workbook workflow.
