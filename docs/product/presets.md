# Presets

Presets are named Discovery methods with their effective tuning and the
retained Alignment runtime option. They are reproducibility and public
behavior surfaces; a preset is not product authority by itself. The code, the
built-in TOML files and their tests own keys, defaults and routes; this page
keeps the boundaries, reasons and limits.

## Where Each Decision Lives

| Decision | Owner | Tests |
| --- | --- | --- |
| Loading, binding, export | [`xic_extractor/presets/`](../../xic_extractor/presets/) | [`test_presets_loader.py`](../../tests/test_presets_loader.py), [`test_presets_apply.py`](../../tests/test_presets_apply.py), [`test_presets_serialization.py`](../../tests/test_presets_serialization.py) |
| Built-in methods and every numeric choice | [`xic_extractor/presets/data/`](../../xic_extractor/presets/data/) | [`test_presets_builtins.py`](../../tests/test_presets_builtins.py), [`test_delta_mass_presets.py`](../../tests/test_delta_mass_presets.py) |
| RAW mass recalibration | `configuration.mass_recalibration`, `raw_mass_recalibration` | [`test_raw_mass_recalibration.py`](../../tests/test_raw_mass_recalibration.py), [`test_mass_recalibration_contracts.py`](../../tests/test_mass_recalibration_contracts.py) |

## Boundaries

- **Intent only.** A preset holds method intent: no paths, worker counts or
  application state. A run is reproducible from documented CLI/config surfaces
  and run metadata, not a command diary.
- **No readiness.** Neither a preset name nor a successful preset run claims
  readiness, and a method label never becomes a new strategy or writer
  authority.
- **Public behavior.** Changing a default preset or silent resolver behavior
  needs [expected-diff framing](../agent/product-validation-contract.md#expected-diff-contract),
  focused tests and evidence.
- **One CID-NL route.** Normal Alignment rejects methods outside it; the Delta
  Mass path owns Pt and Pt + CID-NL composites.

## RAW Mass Recalibration

CID-NL Discovery presets restore lock-mass (Easy-IC) coordinates by default,
and downstream algorithms receive corrected masses rather than a second
correction policy.

- **Positive polarity only.** The lock-mass ppm approximation is supported
  only for positive-polarity RAWs; other polarities keep vendor masses.
- **What it is not.** The fixed baseline also moves normal scan-to-scan
  fluctuations: restoration is not absolute mass recalibration and does not
  remove background ions' native bias. Inclusion-list setpoints are unchanged.
- **Replay uses the saved result.** Each RAW's mass result is saved where its
  review can replay it, so review curves follow the saved result, not today's
  default; restoration and `none` runs have different scientific identities.

## Tissue Validation Defaults

The 8RAW validation set is a subset of the 85RAW tissue cohort with the same
`dna_dr` method and settings; dataset selection changes the sample scope, not
the method, and the runner keeps no second preset. A blank library's
suitability must be established separately. See
[local validation commands](../agent/local-execution.md#validation-scale).

See also [Discovery](discovery.md) · [Signal processing](signal-processing.md)
· [Diagnostic ledger](../agent/diagnostic-ledger.md)
