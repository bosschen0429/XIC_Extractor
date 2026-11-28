from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest

from tests.processing_run_spec_fixtures import (
    _compile_paths,
    _raws,
    _settings,
)
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.configuration.execution_profile import in_process_execution
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
        neutral_loss_profiles=(
            NeutralLossProfile(
                "invalid",
                invalid,  # type: ignore[arg-type]
            ),
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


def test_invalid_method_population_is_rejected_before_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = DiscoverySettings(
        neutral_loss_profiles=(
            NeutralLossProfile("tag-a", 116.0474),
            NeutralLossProfile("tag-b", 116.0474),
        ),
        tag_combine_mode="union",
    )
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


def _write_bound_library(path: Path, intensity: float) -> None:
    from xic_extractor.discovery.blank_qualification import (
        BlankBackgroundIon,
        BlankBackgroundLibrary,
    )

    BlankBackgroundLibrary(
        ions=(BlankBackgroundIon(258.1085, intensity, 3, "blank"),),
        min_intensity=0.0,
        source_blanks=("blank",),
    ).save_json(path)


def test_run_identity_does_not_inherit_packaged_blank_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.discovery import blank_qualification as blank

    raw_paths = _raws(tmp_path)
    packaged_path = tmp_path / "packaged" / "blank_library.json"
    packaged_path.parent.mkdir()
    _write_bound_library(packaged_path, 1000.0)
    monkeypatch.setattr(
        blank, "get_default_background_library_path", lambda: packaged_path
    )
    monkeypatch.setattr(blank, "_CACHED_DEFAULT_LIBRARY", None)
    settings = replace(
        _settings(),
        enable_blank_qualification=True,
        blank_library_path=None,
    )
    first = _compile_paths(raw_paths, settings=settings)
    _write_bound_library(packaged_path, 1.0)
    monkeypatch.setattr(blank, "_CACHED_DEFAULT_LIBRARY", None)
    second = _compile_paths(raw_paths, settings=settings)
    assert all(item.blank_library_content is None for item in first.acquisition_inputs)
    assert first == second


def test_bound_blank_snapshot_controls_each_sample_after_library_mutation(
    tmp_path: Path,
) -> None:
    import numpy as np

    from tests.discovery_pipeline_fixtures import (
        _OwnerBoundFakeRawHandle,
        _scan_event,
    )
    from tests.discovery_store_fixtures import stored_peaks
    from xic_extractor.discovery.pipeline import run_discovery_batch

    paths = []
    for name, intensity in (("A", 1000.0), ("B", 1.0)):
        directory = tmp_path / name
        directory.mkdir()
        raw = directory / f"{name}.raw"
        raw.write_bytes(name.encode())
        paths.append(raw)
        _write_bound_library(directory / "blank_library.json", intensity)
    settings = replace(_settings(), enable_blank_qualification=True)
    spec = _compile_paths(tuple(paths), settings=settings)
    for path, intensity in zip(paths, (1.0, 1000.0), strict=True):
        _write_bound_library(path.parent / "blank_library.json", intensity)
    rt = np.linspace(7.4, 8.2, 81)
    handles = {
        path: _OwnerBoundFakeRawHandle(
            item.acquisition_source,
            [_scan_event(scan_number=101, rt=7.8)],
            rt=rt,
            intensity=100.0 * np.exp(-0.5 * ((rt - 7.8) / 0.04) ** 2),
        )
        for path, item in zip(paths, spec.acquisition_inputs, strict=True)
    }
    result = run_discovery_batch(
        spec,
        output_dir=tmp_path / "out",
        dll_dir=tmp_path / "dll",
        raw_opener=lambda path, _dll: handles[path],
        execution=in_process_execution(),
    )
    states = {peak.sample_stem: peak.status for peak in stored_peaks(result.store)}
    assert states == {"A": "blank", "B": "admitted"}
