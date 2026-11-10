from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.processing_identity as processing_identity_module
from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.targeted_inputs import (
    ResolvedTargetedInputs,
    build_targeted_acquisition_sources,
    resolve_targeted_inputs,
    resolve_targeted_raw_paths,
)
from xic_extractor.injection_timeline import InjectionHeader
from xic_extractor.processing_identity import (
    FingerprintCancelledError,
    IdentityContractError,
    build_acquisition_source_identity,
    fingerprint_file_content,
)
from xic_extractor.raw_source_binding import THERMO_RAW_OBSERVATION_NAMESPACE
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    infer_sample_role,
)


@pytest.fixture(autouse=True)
def _placeholder_raw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    # RAW files here are placeholders; order them by their file times.
    install_fake_raw_headers(monkeypatch)


def test_acquisition_source_identity_is_content_bound_not_path_bound(
    tmp_path: Path,
) -> None:
    first = _raw(tmp_path / "first.raw", b"same-acquisition")
    copied = _raw(tmp_path / "copied.raw", b"same-acquisition")

    first_identity = _source_identity(first)
    copied_identity = _source_identity(copied)

    assert first_identity == copied_identity
    assert first_identity.byte_size == len(b"same-acquisition")
    assert len(first_identity.content_sha256) == 64
    assert str(first) not in first_identity.acquisition_source_id


def test_acquisition_source_identity_changes_on_substitution(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"first-content")
    first = _source_identity(path)

    path.write_bytes(b"mutated-content")
    mutated = _source_identity(path)

    assert mutated.acquisition_source_id != first.acquisition_source_id
    assert mutated.content_sha256 != first.content_sha256


def test_fingerprint_observer_reports_measured_calls_bytes_and_time(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"measured-content")
    observations: list[dict[str, object]] = []

    fingerprint = fingerprint_file_content(path, observation=observations.append)

    assert fingerprint.byte_size == len(b"measured-content")
    assert observations == [
        {
            "metric": "fingerprint",
            "scope": str(path),
            "calls": 1,
            "bytes": len(b"measured-content"),
            "elapsed_sec": observations[0]["elapsed_sec"],
            "availability": "measured",
            "reason": "",
        }
    ]
    assert isinstance(observations[0]["elapsed_sec"], float)


def test_fingerprint_without_observer_skips_diagnostic_timer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"content")
    monkeypatch.setattr(
        "xic_extractor.processing_identity.perf_counter",
        lambda: (_ for _ in ()).throw(AssertionError("timer must stay opt-in")),
    )

    fingerprint = fingerprint_file_content(path)

    assert fingerprint.byte_size == len(b"content")


def test_fingerprint_cancellation_is_checked_between_chunks(tmp_path: Path) -> None:
    path = _raw(tmp_path / "sample.raw", b"x" * (2 * 1024 * 1024))

    with pytest.raises(FingerprintCancelledError, match="was cancelled"):
        fingerprint_file_content(path, should_cancel=lambda: True)


@pytest.mark.parametrize("path_kind", ["invalid", "missing", "directory"])
def test_fingerprint_observer_reports_each_pre_read_failure_once(
    tmp_path: Path,
    path_kind: str,
) -> None:
    if path_kind == "invalid":
        path = None
    elif path_kind == "missing":
        path = tmp_path / "missing.raw"
    else:
        path = tmp_path / "directory.raw"
        path.mkdir()
    observations: list[dict[str, object]] = []

    with pytest.raises(IdentityContractError):
        fingerprint_file_content(  # type: ignore[arg-type]
            path,
            observation=observations.append,
        )

    assert len(observations) == 1
    assert observations[0]["availability"] == "failed"
    assert observations[0]["calls"] == 1
    assert observations[0]["bytes"] in {None, 0}


def test_fingerprint_observer_failure_does_not_change_identity_semantics(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"stable-content")

    fingerprint = fingerprint_file_content(
        path,
        observation=lambda _event: (_ for _ in ()).throw(RuntimeError("diagnostic")),
    )

    assert fingerprint.byte_size == len(b"stable-content")


def test_source_path_replacement_during_hash_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"original-content")
    original_stat = Path.stat

    class DifferentFileIdentity:
        def __init__(self, observed) -> None:
            self.st_dev = observed.st_dev
            self.st_ino = observed.st_ino + 1

    def replaced_path_stat(self, *args, **kwargs):
        observed = original_stat(self, *args, **kwargs)
        if self == path:
            return DifferentFileIdentity(observed)
        return observed

    monkeypatch.setattr(Path, "stat", replaced_path_stat)
    observations: list[dict[str, object]] = []

    with pytest.raises(IdentityContractError, match="replaced"):
        fingerprint_file_content(path, observation=observations.append)

    assert len(observations) == 1
    assert observations[0]["availability"] == "failed"
    assert observations[0]["bytes"] == len(b"original-content")
    assert "replaced" in str(observations[0]["reason"])


def test_source_handle_mutation_reports_failed_fingerprint_observation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"original-content")
    original_fstat = processing_identity_module.os.fstat
    calls = 0

    def changed_fstat(fd):
        nonlocal calls
        observed = original_fstat(fd)
        calls += 1
        if calls == 1:
            return observed
        values = {
            field: getattr(observed, field)
            for field in (
                "st_mode",
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        values["st_mtime_ns"] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(processing_identity_module.os, "fstat", changed_fstat)
    observations: list[dict[str, object]] = []

    with pytest.raises(IdentityContractError, match="mutated"):
        fingerprint_file_content(path, observation=observations.append)

    assert len(observations) == 1
    assert observations[0]["availability"] == "failed"
    assert observations[0]["bytes"] == len(b"original-content")
    assert "mutated" in str(observations[0]["reason"])


def test_source_size_drift_reports_failed_fingerprint_observation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"original-content")
    original_fstat = processing_identity_module.os.fstat

    def wrong_size_fstat(fd):
        observed = original_fstat(fd)
        return SimpleNamespace(
            st_mode=observed.st_mode,
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_size=observed.st_size + 1,
            st_mtime_ns=observed.st_mtime_ns,
            st_ctime_ns=observed.st_ctime_ns,
        )

    monkeypatch.setattr(processing_identity_module.os, "fstat", wrong_size_fstat)
    observations: list[dict[str, object]] = []

    with pytest.raises(IdentityContractError, match="byte size changed"):
        fingerprint_file_content(path, observation=observations.append)

    assert len(observations) == 1
    assert observations[0]["availability"] == "failed"
    assert observations[0]["bytes"] == len(b"original-content")
    assert "byte size changed" in str(observations[0]["reason"])


def test_source_identity_allows_path_stat_metadata_lag_for_same_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"stable-content")
    original_stat = Path.stat

    class SameFileWithLaggingMetadata:
        def __init__(self, observed) -> None:
            self.st_dev = observed.st_dev
            self.st_ino = observed.st_ino
            self.st_size = observed.st_size
            self.st_mtime_ns = observed.st_mtime_ns + 1
            self.st_ctime_ns = observed.st_ctime_ns + 1

    def lagging_stat(self, *args, **kwargs):
        observed = original_stat(self, *args, **kwargs)
        if self == path:
            return SameFileWithLaggingMetadata(observed)
        return observed

    monkeypatch.setattr(Path, "stat", lagging_stat)

    identity = _source_identity(path)

    assert identity.byte_size == len(b"stable-content")


def test_targeted_sources_use_local_revision_without_reading_raw_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _raw(tmp_path / "sample_a.raw", b"source-a")

    original_open = Path.open

    def reject_content_read(candidate, *args, **kwargs):
        if candidate == path:
            pytest.fail("Normal Targeted preparation must not read full RAW content")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_content_read)
    source = _sources([path], injection_order={"sample_a": 1})["sample_a"]
    state = path.stat()
    assert source.content_sha256 is None
    assert source.local_file_revision == (
        state.st_dev,
        state.st_ino,
        state.st_size,
        state.st_mtime_ns,
        state.st_ctime_ns,
    )


def test_missing_ambiguous_or_rebound_inputs_fail_closed(tmp_path: Path) -> None:
    first = _raw(tmp_path / "sample_a.raw", b"duplicate-content")
    second = _raw(tmp_path / "sample_b.raw", b"duplicate-content")

    with pytest.raises(IdentityContractError, match="injection order"):
        _sources([first, second], injection_order={"sample_a": 1})

    # Byte-identical copies remain different local files without a content audit.
    sources = _sources([first, second], injection_order={"sample_a": 1, "sample_b": 2})
    assert len({source.acquisition_source_id for source in sources.values()}) == 2

    with pytest.raises(IdentityContractError, match="injection_order values"):
        _sources(
            [_raw(first, b"first-content"), _raw(second, b"second-content")],
            injection_order={"sample_a": 1, "sample_b": 1},
        )

    with pytest.raises(IdentityContractError, match="unable to hash"):
        _source_identity(tmp_path / "missing.raw")


def test_sample_metadata_alias_projection_binds_targeted_sources(
    tmp_path: Path,
) -> None:
    raw_path = _raw(
        tmp_path / "Breast_Cancer_Tissue_pooled_QC1.raw",
        b"qc-source",
    )
    metadata_path = tmp_path / "sample_metadata.tsv"
    row = {column: "" for column in SAMPLE_METADATA_COLUMNS}
    row.update(
        {
            "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            "sample_name": "QC1",
            "raw_stem": raw_path.stem,
            "injection_order": "1",
            "sample_role": "pooled_qc",
        }
    )
    metadata_path.write_text(
        "\t".join(SAMPLE_METADATA_COLUMNS)
        + "\n"
        + "\t".join(row[column] for column in SAMPLE_METADATA_COLUMNS)
        + "\n",
        encoding="utf-8",
    )
    config = replace(_config(tmp_path), injection_order_source=metadata_path)
    resolved = resolve_targeted_inputs(
        config,
        raw_paths=[raw_path],
        injection_order=None,
    )

    sources = _sources([raw_path], injection_order=resolved.injection_order)

    assert resolved.injection_order == {raw_path.stem: 1}
    assert tuple(sources) == (raw_path.stem,)


def test_targeted_and_untargeted_read_one_sequence_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.sample_metadata_sequence_parsing_fixtures import _create_synthetic_docx
    from xic_extractor import raw_sequence
    from xic_workbench.samples import load_samples

    # The Word sequence numbers Blank 1 ... Normal 4; the RAW clock disagrees.
    clock = {
        "NormalBC001_DNA": 0,
        "TumorBC001_DNA": 10,
        "QC_Pool_1": 20,
        "Blank_1": 30,
        "Older_batch_S1": -60,
    }
    for stem in clock:
        _raw(tmp_path / f"{stem}.raw", stem.encode())

    def analysis_header(path: Path, _dll: Path):
        header = InjectionHeader(
            raw_path=str(path.resolve()),
            acquisition_source=f"D:/acquired/{path.name}",
            creation_date=datetime(2026, 1, 1, 12)
            + timedelta(minutes=clock[path.stem]),
            start_time_min=0.0,
            end_time_min=5.0,
            instrument_method="",
        )
        return header, infer_sample_role(None, sample_name=path.stem), path.stem

    monkeypatch.setattr(raw_sequence, "_analysis_header", analysis_header)
    sequence = tmp_path / "sequence.docx"
    _create_synthetic_docx(sequence)
    config = replace(_config(tmp_path), injection_order_source=sequence)

    raw_paths = resolve_targeted_raw_paths(config)
    targeted = resolve_targeted_inputs(
        config, raw_paths=raw_paths, injection_order=None
    )
    untargeted = {
        row["raw_stem"]: row
        for row in load_samples(tmp_path, dll_dir=tmp_path, sequence_source=sequence)[
            "samples"
        ]
    }

    assert targeted.injection_order == {
        "Older_batch_S1": 1,
        "NormalBC001_DNA": 2,
        "TumorBC001_DNA": 3,
        "QC_Pool_1": 4,
    }
    for row in targeted.sample_metadata:
        assert (row.injection_order, row.sample_role) == (
            untargeted[row.raw_stem]["injection_order"],
            untargeted[row.raw_stem]["sample_role"],
        )
    assert untargeted["Blank_1"]["sample_role"] == "blank"


def test_targeted_observation_namespace_tracks_raw_adapter_controller() -> None:
    raw_reader_source = Path("xic_extractor/raw_reader.py").read_text(encoding="utf-8")

    assert "SelectInstrument(thermo.device_ms, 1)" in raw_reader_source
    assert THERMO_RAW_OBSERVATION_NAMESPACE == ("thermo_raw_file_reader:Device.MS:1")


def _sources(paths: list[Path], *, injection_order: dict[str, int]):
    return build_targeted_acquisition_sources(
        raw_paths=paths, injection_order=injection_order
    )


def _source_identity(path: Path):
    return build_acquisition_source_identity(
        path,
        acquisition_format="thermo_raw",
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )


def _raw(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def _resolved_inputs() -> ResolvedTargetedInputs:
    return ResolvedTargetedInputs(
        injection_order={"sample_a": 1, "sample_b": 2},
    )


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )
