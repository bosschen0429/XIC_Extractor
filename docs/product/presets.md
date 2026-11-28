# Presets

Presets are named Discovery methods with their effective tuning and the
retained Alignment runtime option. They are reproducibility and public
behavior surfaces; a preset is not product authority by itself.

| Question | Rule | Detail |
| --- | --- | --- |
| What does a preset hold? | Method intent only, no paths or worker counts. | [Contract](#contract) |
| Are RAW masses corrected? | CID-NL presets default to lock-mass restoration. | [RAW mass recalibration](#raw-mass-recalibration) |
| How do multi-tag presets combine? | OR or AND on one physical peak. | [CID-NL recipe binding](#cid-nl-recipe-binding) |

## Contract

- Changing a default preset or silent resolver behavior is a public change
  needing [expected-diff framing](../agent/product-validation-contract.md#expected-diff-contract),
  focused tests and evidence.
- Targeted and Untargeted processing use the shared physical signal owner;
  peak detection has no selectable mode.
- A preset run is reproducible from documented CLI/config surfaces and run
  metadata. A command diary is not a reproducibility source.
- The TOML format carries method intent only: no RAW/output paths, worker
  counts or application state. `source` is derived on load and is never a TOML
  field or scientific identity.
- Portable export resolves the effective values, writes the TOML shape and
  validates it through the same parser intake uses. Tag order and combination
  are preserved, and an invalid method leaves an existing destination intact.
- Neither a preset name nor a successful preset run claims readiness.
- A tag name contains no `;`: Discovery tables join a peak's tag names with
  it, so Alignment could not read such a name back.
- A method label can wrap a strategy family for usability: `pt_pattern` means
  `delta_mass(pattern_id=pt)`. The preset layer never turns a label into a new
  primitive strategy or writer authority.

## RAW Mass Recalibration

CID-NL Discovery presets default to `easy_ic_restore` when
`[discovery].mass_recalibration` is absent; Discovery and extraction settings
use the same default for new execution.

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
method identities, an absent policy means `none`, and restoration runs
have a different scientific identity from `none` and `easy_ic_probe` runs.

Each RAW's mass result is saved where its review can replay it:
the `samples` table of `discovery.sqlite`, the Alignment
`alignment_run_metadata.json` (`mass_recalibration_by_sample`) and Targeted
`Run Metadata`. Review curves replay the saved result, not today's default.
Saved `easy_ic_probe` results keep their original `a + b * LM` formula; a probe
policy without its saved fit cannot be replayed and raises a read error.
Re-analysis must select restoration or `none` explicitly. Direct `open_raw`
callers default to `none`. Delta Mass methods created in the Workbench write
`easy_ic_restore` explicitly; a hand-written Delta Mass preset that omits the
key uses `none`. The built-in Pt presets restore before building features
([Pt presets](discovery.md#pt-presets)).

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

Multi-tag CID-NL presets combine tags by `union` (OR) or `intersection` (AND)
on one physical peak within one sample
([composite methods](discovery.md#cid-nl-support-relations)); Alignment and
Backfill apply the same requirement. Different tag hits across samples can
share a row when the physical compatibility gates pass; labels alone never
establish identity.

A built-in Alignment preset emits `alignment_backfill_cell_evidence.tsv` where
the runner contract requires it.
Non-standard peaks and candidate-only evidence stay outside automatic
publication.

Normal Alignment rejects methods outside this CID-NL route. The Delta Mass
Discovery path owns the Pt and Pt + CID-NL composite presets, and CID-NL
binders reject them. Retired keys (Standard Peak, Backfill expansion,
`resolver_mode`) are rejected by name.

## Delta Mass Parameters

Delta Mass TOMLs specify `discovery.positions` (label, delta, height-ratio
bounds), anchor and discriminating labels, and co-elution and shape
tolerances. The detector's `smoothing_window_sec` is converted per RAW to the
nearest positive odd number of scan points; for example 6.6 s with 1.31 s scans
gives five points. This screening window is separate from the shared Gaussian
profile. Export preserves the nested settings. See
[Delta Mass](discovery.md#delta-mass-discovery) for the method.

## Tissue Validation Defaults

The 8RAW validation set is a subset of the 85RAW tissue cohort, with the same
`dna_dr` method and settings, blank qualification disabled; dataset selection
changes the sample scope, not the method, and the runner keeps no second
preset. An explicit `--blank-library`
binds the same library in Discovery and Alignment; its suitability must be
established separately. ISTD input follows the normal Alignment targets CSV,
and resolved inputs are shown before RAW work. See
[local validation commands](../agent/local-execution.md#validation-scale).

## Verification

`tests/test_presets_loader.py`, `tests/test_presets_builtins.py`,
`tests/test_presets_apply.py`, `tests/test_presets_serialization.py`,
`tests/test_delta_mass_presets.py`, `tests/test_raw_mass_recalibration.py`,
`tests/test_mass_recalibration_contracts.py`.

See also [Parameter settings](../agent/local-execution.md) ·
[Discovery](discovery.md) · [Signal processing](signal-processing.md) ·
[Diagnostic ledger](../agent/diagnostic-ledger.md)

## Where The Code Lives

| Rule | Module |
| --- | --- |
| Loader, binders, export (`serialize_preset`, `save_preset`); built-in TOML in [`data/`](../../xic_extractor/presets/data/) | [`xic_extractor/presets/`](../../xic_extractor/presets/) |
| Default policy for new execution | `DiscoverySettings`, `ExtractionConfig` |
