# Presets

Presets are named Discovery methods with their effective tuning and the
retained Alignment runtime option. Presets are reproducibility and public
behavior surfaces; a preset is not product authority by itself. Code lives in
[`xic_extractor/presets/`](../../xic_extractor/presets/) and built-in TOML files
in [`xic_extractor/presets/data/`](../../xic_extractor/presets/data/).

## Contract

- Changing a default preset or silent resolver behavior changes public
  behavior. It needs
  [expected-diff framing](../agent/product-validation-contract.md#expected-diff-contract),
  focused tests and relevant evidence.
- Targeted and profile-bound Untargeted processing use the shared physical
  signal owner. Direct resolver-based diagnostic callers keep their explicit
  `local_minimum` or `savgol` configuration; those settings do not choose the
  Targeted physical algorithm.
- A preset run is reproducible from documented CLI/config surfaces and run
  metadata. A command diary is not a reproducibility source.
- The TOML format carries method intent only: no RAW/output paths, worker
  counts or application state. `source` is derived on load and is never a TOML
  field or scientific identity.
- `serialize_preset` and `save_preset` own portable export. Export resolves the
  effective values, writes the existing TOML shape, and validates it through the
  same parser intake uses. Tag order and combination are preserved, and an
  invalid method leaves an existing destination intact.
- Preset names carry no readiness claim. A successful run of any preset does
  not assert product readiness.
- A method label can wrap a strategy family for usability: `pt_pattern` means
  `delta_mass(pattern_id=pt)`. The preset layer never turns a label into a new
  primitive strategy or ProductWriter authority.

## RAW Mass Recalibration

CID-NL Discovery presets default to `easy_ic_restore` when
`[discovery].mass_recalibration` is absent. `DiscoverySettings` and
`ExtractionConfig` use the same default for new execution.

The RAW adapter takes the median finite LM Correction from MS1 scans with
Number of LM Found > 0 as each file's baseline. Each scan's shift in ppm is
twice its LM minus twice the baseline, and recorded masses are divided by
`1 + shift_ppm * 1e-6`. Downstream algorithms receive corrected masses, not a
second correction policy.

- The ppm approximation is supported by the lock-mass mechanism only for
  positive-polarity RAWs, including MS2-only excursions. No background-ion
  threshold gates MS2 correction.
- Negative-polarity RAWs keep vendor masses and report
  `outcome=not_applicable`, `reason=negative_polarity`. Mixed or unknown
  polarity reports `unavailable` and keeps vendor masses.
- Missing trailers or an absent baseline report `unavailable` and keep vendor
  masses. A missing finite LM in a scan under an applied policy is a read error.

The fixed baseline also moves normal scan-to-scan fluctuations. Restoration is
not absolute mass recalibration and does not remove background ions' native
bias. Measured precursors use the parent MS1 scale; fragments use their own MS2
scale. Inclusion-list setpoints are unchanged.

Set `mass_recalibration = "none"` in `[discovery]` to keep vendor masses.
Export preserves an explicit `none` and omits the default. In persisted
method/request identities, an absent policy means `none`, and restoration runs
have a different scientific identity from `none` and `easy_ic_probe` runs.

Each RAW's mass result is saved where its review can replay it:
`discovery_batch_index.csv` (`mass_recalibration`), the Alignment
`alignment_run_metadata.json` (`mass_recalibration_by_sample`) and Targeted
`Run Metadata`. Review curves replay the saved result, not today's default.
Saved `easy_ic_probe` results keep their original `a + b * LM` formula; a probe
policy without its saved fit cannot be replayed and raises a read error.
Re-analysis must select restoration or `none` explicitly. Direct `open_raw`
callers default to `none`; QC and blank-library acquisition are unchanged.
The built-in Pt Delta Mass presets select `easy_ic_restore` before feature
construction. Their MS1 index, partner matching and XICs use the same restored
coordinates. Custom Delta Mass presets default to `none` unless they explicitly
select a mass policy. Delta Mass sidecars retain the per-sample restoration
results; review reuses those results instead of estimating new baselines.

## CID-NL Recipe Binding

A CID-NL preset is bound once, before expensive work, to one immutable
in-memory recipe holding the typed Discovery and Alignment settings. The recipe
contains no paths, sample metadata, worker counts, review inputs or second
invocation schema.

| Route | Preset-backed behavior |
| --- | --- |
| `discovery_only` | RAW to the Discovery outputs |
| `full` | RAW -> Discovery -> finalized CID-NL batch -> canonical Matrix writer |
| `align_only` | Existing Discovery index -> finalized CID-NL batch -> canonical Matrix writer |

Multi-tag CID-NL presets combine tags by `union` (OR) or `intersection` (AND):

- OR accepts any configured tag on the same physical peak.
- AND requires every configured tag on that peak within one sample.
- Alignment and Backfill apply the same requirement.
- Different tag hits across samples can share a row when the physical peak
  compatibility gates pass. Labels alone never establish identity.

A built-in Alignment preset emits the seed audit and
`alignment_backfill_cell_evidence.tsv` where the runner contract requires them.
Non-standard peaks and candidate-only evidence stay outside automatic
publication.

Normal Alignment rejects methods outside this CID-NL route. The Delta Mass
Discovery path owns the Pt and Pt + CID-NL composite presets, and CID-NL
binders reject them. Normal recipes reject the retired Standard Peak and
Backfill-expansion keys; no replacement publication choice is exposed.

## Resolver Presets

| Preset | Role |
| --- | --- |
| `savgol` | Savitzky-Golay smoothing and prominence-based candidate resolver |
| `local_minimum` | Local-minimum candidate resolver |

The `savgol` proposal-source label identifies Savitzky-Golay candidates.

## Delta Mass Parameters

Delta Mass TOMLs specify `discovery.positions` (label, delta, height-ratio
bounds), anchor and discriminating labels, and co-elution and shape
tolerances. The detector's `smoothing_window_sec` is converted per RAW to the
nearest positive odd number of scan points; for example 6.6 s with 1.31 s scans
gives five points. This screening window is separate from the shared Gaussian
profile. Export preserves the nested settings. See
[Delta Mass](discovery.md#delta-mass-discovery) for the method.

## Tissue Validation Defaults

The 8RAW validation set is a subset of the 85RAW tissue cohort. Both use the
same `dna_dr` method and effective settings, including disabled blank
qualification. Dataset selection changes the sample scope, not the method, and
the runner keeps no second scientific preset. An explicit `--blank-library`
binds the same library in Discovery and Alignment; its suitability must be
established separately. ISTD input follows the normal Alignment targets CSV,
and resolved inputs are shown before RAW work. See
[local validation commands](../agent/local-execution.md#validation-scale).

## Verification

- `tests/test_presets_loader.py`, `tests/test_presets_builtins.py`,
  `tests/test_presets_apply.py`, `tests/test_presets_serialization.py`
- `tests/test_delta_mass_presets.py`
- `tests/test_raw_mass_recalibration.py`, `tests/test_mass_recalibration_contracts.py`

## See Also

- [Parameter settings](../agent/local-execution.md)
- [Discovery](discovery.md) | [Signal processing](signal-processing.md)
- [Diagnostic ledger](../agent/diagnostic-ledger.md)
