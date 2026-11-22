from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.processing_run_spec_fixtures import (
    _compile_paths,
    _raws,
    _settings,
)
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.evidence_config import DiscoveryEvidenceThresholds
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.settings import DiscoverySettings


def test_parallel_fingerprints_preserve_identity_order_and_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from xic_extractor.discovery import processing_run_spec_adapter as adapter

    paths = _raws(tmp_path)
    expected = adapter.fingerprint_acquisition_sources(paths)
    original = adapter.build_local_acquisition_source_identity
    barrier = threading.Barrier(2)
    thread_ids: set[int] = set()
    lock = threading.Lock()

    def concurrent_fingerprint(*args, **kwargs):
        with lock:
            thread_ids.add(threading.get_ident())
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        adapter, "build_local_acquisition_source_identity", concurrent_fingerprint
    )
    actual = adapter.fingerprint_acquisition_sources(paths, fingerprint_threads=2)
    assert actual == expected
    assert len(thread_ids) == 2


def test_run_spec_reuses_verified_sources_without_rehashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.discovery import processing_run_spec_adapter as adapter
    from xic_extractor.raw_reader_errors import RawReaderError

    paths = _raws(tmp_path)
    expected = adapter.fingerprint_acquisition_sources(paths)
    sources = {path.stem: source for path, source in zip(paths, expected, strict=True)}
    monkeypatch.setattr(
        adapter,
        "build_local_acquisition_source_identity",
        lambda *_args, **_kwargs: pytest.fail("rehashed a verified source"),
    )
    actual = adapter.fingerprint_acquisition_sources(
        paths, verified_acquisition_sources=sources
    )
    assert actual == expected

    paths[0].write_bytes(b"changed source with different size")
    with pytest.raises(RawReaderError, match="RAW path state"):
        adapter.fingerprint_acquisition_sources(
            paths, verified_acquisition_sources=sources
        )


def test_parallel_fingerprints_reject_source_changed_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xic_extractor.discovery import processing_run_spec_adapter as adapter
    from xic_extractor.processing_identity import IdentityContractError

    paths = _raws(tmp_path)
    original = adapter.build_local_acquisition_source_identity

    def mutate_after_fingerprint(path, **kwargs):
        source = original(path, **kwargs)
        if path == paths[0]:
            path.write_bytes(b"changed source with different size")
        return source

    monkeypatch.setattr(
        adapter, "build_local_acquisition_source_identity", mutate_after_fingerprint
    )
    with pytest.raises(IdentityContractError, match="changed during"):
        adapter.fingerprint_acquisition_sources(paths, fingerprint_threads=2)


def test_discovery_preparation_binds_local_revision_without_reading_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _raws(tmp_path)
    original_open = Path.open

    def forbid_raw_read(path, *args, **kwargs):
        if path in paths:
            pytest.fail("Discovery source preparation read RAW bytes")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_raw_read)
    spec = _compile_paths(paths)
    assert all(
        item.acquisition_source.content_sha256 is None
        and item.acquisition_source.local_file_revision is not None
        for item in spec.acquisition_inputs
    )


def test_run_spec_rejects_duplicate_local_file_aliases_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.raw"
    alias = tmp_path / "alias.raw"
    first.write_bytes(b"identical acquisition")
    alias.hardlink_to(first)
    calls: list[Path] = []
    from xic_extractor.discovery import processing_run_spec_adapter as adapter

    owner = adapter.build_local_acquisition_source_identity

    def counted(path: Path, **kwargs):
        calls.append(path)
        return owner(path, **kwargs)

    monkeypatch.setattr(adapter, "build_local_acquisition_source_identity", counted)

    with pytest.raises(ValueError, match="duplicate.*acquisition_source_id"):
        compile_discovery_processing_run_spec(
            (first, alias),
            discovery_settings=_settings(),
        )

    assert calls == [first, alias]


def test_run_spec_rejects_invalid_direct_profile_type() -> None:
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
        "build_local_acquisition_source_identity",
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
        "build_local_acquisition_source_identity",
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

    assert spec.settings == settings
