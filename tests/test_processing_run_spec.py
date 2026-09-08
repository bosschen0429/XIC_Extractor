from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.evidence_config import DiscoveryEvidenceThresholds
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
    validate_processing_run_spec_call,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    compile_processing_run_spec,
    deserialize_processing_run_spec,
    serialize_processing_run_spec,
)


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile(
            tag="DNA_dR",
            neutral_loss_da=116.0474,
        )
    )


def _raws(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "A.raw"
    second = tmp_path / "B.raw"
    first.write_bytes(b"first acquisition")
    second.write_bytes(b"second acquisition")
    return first, second


def _peak_config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("raw"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _compile_paths(
    raw_paths: tuple[Path, ...],
    *,
    settings: DiscoverySettings | None = None,
):
    return compile_discovery_processing_run_spec(
        raw_paths,
        discovery_settings=settings or _settings(),
    )


def test_run_spec_roundtrip_closes_content_hash_profile_and_order(
    tmp_path: Path,
) -> None:
    raw_paths = _raws(tmp_path)
    spec = _compile_paths(raw_paths)
    payload = serialize_processing_run_spec(spec)

    assert (
        deserialize_processing_run_spec(
            payload,
            expected_run_spec_hash=spec.run_spec_hash,
            expected_run_spec_id=spec.run_spec_id,
        )
        == spec
    )
    reversed_spec = _compile_paths(tuple(reversed(raw_paths)))
    assert reversed_spec.run_spec_id != spec.run_spec_id


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.pop("run_spec_id"),
        lambda value: value.__setitem__("run_spec_hash", "0" * 64),
        lambda value: value["content"]["discovery_method"].__setitem__(
            "method_hash", "0" * 64
        ),
        lambda value: value["content"]["signal_processing_profile"].__setitem__(
            "profile_id", "signal-profile.v1:" + "0" * 64
        ),
        lambda value: value["content"]["acquisition_inputs"][0][
            "acquisition_source"
        ].__setitem__("content_sha256", "0" * 64),
    ],
)
def test_run_spec_rejects_unknown_partial_tampered_and_cross_content(
    tmp_path: Path,
    mutation,
) -> None:
    spec = _compile_paths(_raws(tmp_path))
    value = json.loads(serialize_processing_run_spec(spec))
    mutation(value)

    with pytest.raises((TypeError, ValueError)):
        deserialize_processing_run_spec(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            expected_run_spec_id=spec.run_spec_id,
        )


def test_run_spec_rejects_duplicate_and_reordered_json_fields(tmp_path: Path) -> None:
    spec = _compile_paths(_raws(tmp_path))
    payload = serialize_processing_run_spec(spec).decode("utf-8")
    duplicate = payload.replace(
        '{"schema_version":"processing_run_spec_envelope_v1",',
        '{"schema_version":"processing_run_spec_envelope_v1",'
        '"schema_version":"processing_run_spec_envelope_v1",',
        1,
    )
    value = json.loads(payload)
    reordered = json.dumps(
        {
            "run_spec_hash": value["run_spec_hash"],
            "schema_version": value["schema_version"],
            "run_spec_id": value["run_spec_id"],
            "content": value["content"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        deserialize_processing_run_spec(duplicate)
    with pytest.raises(ValueError, match="fields must be exactly"):
        deserialize_processing_run_spec(reordered)


def test_run_spec_call_rejects_conflicts_before_runtime(tmp_path: Path) -> None:
    raw_paths = _raws(tmp_path)
    settings = _settings()
    spec = _compile_paths(raw_paths, settings=settings)

    with pytest.raises(ValueError, match="settings conflict"):
        validate_processing_run_spec_call(
            spec,
            raw_paths,
            discovery_settings=DiscoverySettings(
                neutral_loss_profile=NeutralLossProfile("DNA_dR", 98.0)
            ),
            signal_processing_profile=SignalProcessingProfile(),
        )
    with pytest.raises(ValueError, match="profile"):
        validate_processing_run_spec_call(
            spec,
            raw_paths,
            discovery_settings=settings,
            signal_processing_profile=SignalProcessingProfile(window_points=21),
        )
    with pytest.raises(ValueError, match="runtime RAW path"):
        validate_processing_run_spec_call(
            spec,
            (raw_paths[0], tmp_path / "C.raw"),
            discovery_settings=settings,
            signal_processing_profile=SignalProcessingProfile(),
        )


def test_run_spec_pure_owner_compiles_typed_inputs(tmp_path: Path) -> None:
    adapter_spec = _compile_paths(_raws(tmp_path))
    pure_spec = compile_processing_run_spec(
        adapter_spec.acquisition_inputs,
        discovery_settings=_settings(),
    )

    assert pure_spec == adapter_spec


def test_run_spec_rejects_duplicate_acquisition_content_aliases_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.raw"
    alias = tmp_path / "alias.raw"
    first.write_bytes(b"identical acquisition")
    alias.write_bytes(b"identical acquisition")
    calls: list[Path] = []
    from xic_extractor.discovery import processing_run_spec_adapter as adapter

    owner = adapter.build_acquisition_source_identity

    def counted(path: Path, **kwargs):
        calls.append(path)
        return owner(path, **kwargs)

    monkeypatch.setattr(adapter, "build_acquisition_source_identity", counted)

    with pytest.raises(ValueError, match="duplicate.*acquisition_source_id"):
        compile_discovery_processing_run_spec(
            (first, alias),
            discovery_settings=_settings(),
        )

    assert calls == [first, alias]


def test_pure_run_spec_rejects_same_source_under_different_names(
    tmp_path: Path,
) -> None:
    spec = _compile_paths(_raws(tmp_path))
    source = spec.acquisition_inputs[0].acquisition_source

    with pytest.raises(ValueError, match="duplicate.*acquisition_source_id"):
        compile_processing_run_spec(
            (
                spec.acquisition_inputs[0],
                ProcessingAcquisitionInput(
                    input_ordinal=1,
                    adapter_input_key="renamed.raw",
                    sample_stem="renamed",
                    acquisition_source=source,
                ),
            ),
            discovery_settings=_settings(),
        )


def test_run_spec_rejects_invalid_direct_profile_type(tmp_path: Path) -> None:
    valid = _compile_paths(_raws(tmp_path))
    with pytest.raises(TypeError, match="SignalProcessingProfile"):
        compile_processing_run_spec(
            valid.acquisition_inputs,
            discovery_settings=_settings(),
            signal_processing_profile="gaussian",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="method"):
        SignalProcessingProfile(method="savgol")


_THRESHOLD_FLOAT_FIELDS = (
    "rt_aligned_max_min",
    "rt_near_max_min",
    "rt_shifted_max_min",
    "product_intensity_high_min",
    "product_intensity_med_min",
    "area_high_min",
    "area_med_min",
    "ms1_support_strong_area_min",
    "ms1_support_moderate_area_min",
    "scan_support_high_score_min",
    "scan_support_low_score_max",
)


@pytest.mark.parametrize("field", _THRESHOLD_FLOAT_FIELDS)
@pytest.mark.parametrize("invalid", [True, "1.0"])
def test_evidence_thresholds_reject_non_numeric_population(
    field: str,
    invalid: object,
) -> None:
    values = DiscoveryEvidenceThresholds().__dict__ | {field: invalid}
    with pytest.raises(TypeError, match=field):
        DiscoveryEvidenceThresholds(**values)


@pytest.mark.parametrize("field", _THRESHOLD_FLOAT_FIELDS)
@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_evidence_thresholds_reject_non_finite_population(
    field: str,
    invalid: float,
) -> None:
    values = DiscoveryEvidenceThresholds().__dict__ | {field: invalid}
    with pytest.raises(ValueError, match=field):
        DiscoveryEvidenceThresholds(**values)


@pytest.mark.parametrize("field", _THRESHOLD_FLOAT_FIELDS)
def test_evidence_thresholds_reject_negative_population(field: str) -> None:
    values = DiscoveryEvidenceThresholds().__dict__ | {field: -1.0}
    with pytest.raises(ValueError, match=field):
        DiscoveryEvidenceThresholds(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"rt_aligned_max_min": 0.21},
        {"rt_near_max_min": 0.41},
        {"product_intensity_high_min": 9_999.0},
        {"area_high_min": 99_999.0},
        {"ms1_support_strong_area_min": 999_999.0},
        {"scan_support_low_score_max": 0.9},
        {"scan_support_high_score_min": 1.01},
        {"scan_support_low_score_max": 1.01},
    ],
)
def test_evidence_thresholds_reject_unordered_or_out_of_bounds_population(
    overrides: dict[str, float],
) -> None:
    values = DiscoveryEvidenceThresholds().__dict__ | overrides
    with pytest.raises(ValueError):
        DiscoveryEvidenceThresholds(**values)


@pytest.mark.parametrize("invalid", [True, 1.0, 0, -1])
def test_evidence_thresholds_reject_invalid_scan_support_target(
    invalid: object,
) -> None:
    values = DiscoveryEvidenceThresholds().__dict__ | {"scan_support_target": invalid}
    with pytest.raises((TypeError, ValueError), match="scan_support_target"):
        DiscoveryEvidenceThresholds(**values)


@pytest.mark.parametrize(
    ("invalid", "error"),
    [
        (True, TypeError),
        ("116.0474", TypeError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        (0, ValueError),
        (-1.0, ValueError),
    ],
)
def test_neutral_loss_mass_is_rejected_before_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: object,
    error: type[Exception],
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"not fingerprinted")
    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile(
            "invalid",
            invalid,  # type: ignore[arg-type]
        )
    )
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid method"),
    )

    with pytest.raises(error, match="neutral_loss_da"):
        compile_discovery_processing_run_spec(
            (raw_path,),
            discovery_settings=settings,
        )


@pytest.mark.parametrize(
    "settings",
    [
        DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474),
            resolver_mode="arbitrated",
        ),
        DiscoverySettings(
            neutral_loss_profiles=(
                NeutralLossProfile("tag-a", 116.0474),
                NeutralLossProfile("tag-b", 116.0474),
            ),
            tag_combine_mode="union",
        ),
    ],
)
def test_invalid_method_population_is_rejected_before_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings: DiscoverySettings,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"not fingerprinted")
    monkeypatch.setattr(
        "xic_extractor.discovery.processing_run_spec_adapter."
        "build_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("fingerprinted invalid method"),
    )

    with pytest.raises(ValueError):
        compile_discovery_processing_run_spec(
            (raw_path,),
            discovery_settings=settings,
        )


def test_near_equal_neutral_loss_masses_remain_distinct_and_hashable(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"near-equal canonical masses")
    settings = DiscoverySettings(
        neutral_loss_profiles=(
            NeutralLossProfile("tag-a", 116.0474),
            NeutralLossProfile("tag-b", math.nextafter(116.0474, math.inf)),
        ),
        tag_combine_mode="union",
    )

    spec = compile_discovery_processing_run_spec(
        (raw_path,),
        discovery_settings=settings,
    )

    assert spec.run_spec_id.startswith("processing-run.v1:")
    assert spec.discovery_method.settings == settings


def test_valid_canonical_hash_snapshots_match_current_profile(tmp_path: Path) -> None:
    spec = _compile_paths(_raws(tmp_path))

    assert spec.discovery_method.method_hash == (
        "a941225d663a845f4acecc8cfda602bef7e75a8ac115a471772b8f94a84726bf"
    )
    assert spec.run_spec_hash == (
        "8b2a7e362f16d62ac53e1aac132c452385edc81a30e85f1f7a6b8d392ad78778"
    )
