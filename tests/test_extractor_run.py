import pickle
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.result_models import RunOutput
from xic_extractor.extraction.targeted_inputs import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    build_acquisition_source_identity,
)


@pytest.fixture(autouse=True)
def _placeholder_raw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    # RAW files here are placeholders; order them by their file times.
    install_fake_raw_headers(monkeypatch)


_OPEN_RAW_SEAM = "xic_extractor.extraction.target_extraction.open_raw"


@pytest.fixture(autouse=True)
def _disable_reader_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.preflight_raw_reader",
        lambda _dll_dir: [],
        raising=False,
    )


def test_acquire_uses_serial_backend_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    config = _config(tmp_path)
    (config.data_dir / "B.raw").write_text("", encoding="utf-8")
    (config.data_dir / "A.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    returned = RunOutput(file_results=[], diagnostics=[])
    calls: list[tuple[ExtractionConfig, list[Target], list[str]]] = []

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
    ) -> RunOutput:
        calls.append((config_arg, targets_arg, [path.name for path in raw_paths]))
        assert progress_callback is _progress
        assert should_stop is _should_stop
        assert injection_order == {"SampleA": 1}

        return returned

    def _progress(_current: int, _total: int, _filename: str) -> None:
        raise AssertionError("fake serial backend should receive but not call callback")

    def _should_stop() -> bool:
        return False

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)

    output = extractor.acquire(
        config,
        targets,
        progress_callback=_progress,
        should_stop=_should_stop,
        injection_order={"SampleA": 1},
    )

    assert output is returned
    assert calls == [(config, targets, ["A.raw", "B.raw"])]


@pytest.mark.parametrize(
    "pair_state",
    [
        "unpaired",
        "no_signal",
        "untrusted",
        "credible",
        "reference_impulse",
        "shape_review",
        "same_reference",
    ],
)
def test_run_no_nl_reference_selects_each_samples_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pair_state: str
) -> None:
    from xic_extractor import extractor

    config = _config(tmp_path)
    target = replace(_target("Analyte"), rt_min=7.5, rt_max=10.5)
    expected = {"SampleA": (9.0, 1000.0), "SampleB": (9.1, 2500.0)}
    targets = [target]
    if pair_state == "reference_impulse":
        target = replace(target, rt_min=7.9, rt_max=10.9)
        targets = [target]
    if pair_state == "same_reference":
        targets.append(replace(target, label="SameReference"))
    if pair_state in {"no_signal", "untrusted", "credible"}:
        target = replace(target, istd_pair="ISTD")
        targets = [target]
        targets.append(
            replace(
                target,
                label="ISTD",
                mz=261.1,
                rt_min=6.5,
                rt_max=9.5,
                is_istd=True,
                istd_pair="",
            )
        )
    if pair_state in {"credible"}:
        expected = {"SampleA": (9.3, 1000.0), "SampleB": (9.4, 2500.0)}
    for sample in expected:
        (config.data_dir / f"{sample}.raw").write_bytes(sample.encode())

    class Raw:
        def acquisition_rt_bounds(self):
            return 0.0, 18.0

        def __init__(self, sample: str) -> None:
            self.sample = sample

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            # Acquired scan spacing is fixed; requesting more context must
            # not resample the same resolved doublet onto a coarser grid.
            acquired_rt = np.arange(0.0, 18.0025, 0.0025)
            rt = acquired_rt[(acquired_rt >= rt_min) & (acquired_rt <= rt_max)]
            apex, height = expected[self.sample]
            if mz == 261.1:
                if pair_state == "no_signal":
                    return rt, np.zeros_like(rt)
                if pair_state == "untrusted":
                    signal = np.zeros_like(rt)
                    signal[np.argmin(np.abs(rt - 8.3))] = height * 10
                    return rt, signal
                istd_peak = height * np.exp(-0.5 * ((rt - (apex - 1.0)) / 0.06) ** 2)
                return rt, 1.0 + istd_peak
            width = (
                np.where(rt < apex, 0.04, 0.2) if pair_state == "shape_review" else 0.06
            )
            signal = height * np.exp(-0.5 * ((rt - apex) / width) ** 2)
            if pair_state == "reference_impulse":
                signal[np.abs(rt - apex) > 0.2] = 0.0
                signal[np.argmin(np.abs(rt - (apex + 0.4)))] = height * 10
                return rt, signal
            other_apex = apex - 0.3 if pair_state in {"credible"} else apex + 1.0
            interferent = height * 4 * np.exp(-0.5 * ((rt - other_apex) / 0.06) ** 2)
            return rt, 1.0 + signal + interferent

        def iter_ms2_scans(self, rt_min, rt_max):
            pytest.fail("no-NL must not query MS2")

    monkeypatch.setattr(_OPEN_RAW_SEAM, lambda path, _dll, **_kwargs: Raw(path.stem))
    output = extractor.run(config, targets)

    assert {file.sample_name for file in output.file_results} == set(expected)
    assert all(file.error is None for file in output.file_results)
    areas = {}
    for file in output.file_results:
        result = file.results[target.label]
        assert result.reported_rt == pytest.approx(
            expected[file.sample_name][0], abs=0.03
        )
        if pair_state == "shape_review":
            projection = result.targeted_product_projection
            assert projection is not None and projection.counted_detection
            assert projection.review_state == "flagged"
            assert "trace_morphology_review" in projection.review_reasons
        if pair_state == "same_reference":
            other = file.results["SameReference"]
            assert other.reported_rt == result.reported_rt
            assert other.reported_peak_area == result.reported_peak_area
        if pair_state in {"no_signal", "untrusted"}:
            assert result.peak_result.paired_istd_anchor_rt is None
            assert result.peak_result.selection_reference_rt == pytest.approx(9.0)
        physical = result.physical_evaluation
        assert physical is not None and physical.facts is not None
        assert result.reported_peak_area == pytest.approx(
            physical.facts.area_counts_seconds
        )
        assert result.reported_peak_start == physical.facts.left_rt_min
        assert result.reported_peak_end == physical.facts.right_rt_min
        areas[file.sample_name] = result.reported_peak_area
    assert areas["SampleB"] == pytest.approx(areas["SampleA"] * 2.5, rel=0.01)


def test_process_file_opens_raw_with_expected_acquisition_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction.target_extraction import process_file

    config = _config(tmp_path)
    raw_path = config.data_dir / "SampleA.raw"
    raw_path.write_bytes(b"identity-bound-raw")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    stat = raw_path.stat()
    expectation = AcquisitionSourceExpectation(
        source=source,
        path_identity=(
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        ),
    )
    captured = {}

    class _OpenedRaw:
        def __enter__(self):
            return object()

        def __exit__(self, *_args: object) -> None:
            return None

    def open_raw(
        actual_path,
        actual_dll_dir,
        *,
        expected_acquisition_source,
        _expected_path_identity,
        mass_recalibration,
        prepared_mass_recalibration,
    ):
        captured["path"] = actual_path
        captured["dll_dir"] = actual_dll_dir
        captured["source"] = expected_acquisition_source
        captured["path_identity"] = _expected_path_identity
        return _OpenedRaw()

    monkeypatch.setattr(_OPEN_RAW_SEAM, open_raw)

    file_result, diagnostics = process_file(
        config,
        [],
        raw_path,
        expected_acquisition_source=expectation,
    )

    assert file_result.sample_name == "SampleA"
    assert diagnostics == []
    assert captured == {
        "path": raw_path,
        "dll_dir": config.dll_dir,
        "source": source,
        "path_identity": expectation.path_identity,
    }


def test_acquire_passes_expected_sources_to_serial_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    config = _config(tmp_path)
    raw_path = config.data_dir / "SampleA.raw"
    raw_path.write_bytes(b"identity-bound-raw")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    stat = raw_path.stat()
    expectation = AcquisitionSourceExpectation(
        source=source,
        path_identity=(
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        ),
    )
    captured = {}

    def run_serial(*_args, **kwargs):
        captured.update(kwargs)
        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(serial_backend, "run_serial", run_serial)

    extractor.acquire(
        config,
        [_target("Analyte")],
        injection_order={"SampleA": 1},
        acquisition_sources_by_sample={"SampleA": expectation},
    )

    assert captured["acquisition_sources_by_sample"] == {"SampleA": expectation}


def test_acquire_resolves_scoring_inputs_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    config = _config(tmp_path)
    config = ExtractionConfig(
        **{
            **config.__dict__,
            "injection_order_source": tmp_path / "sample_info.csv",
            "config_hash": "abcd1234",
        }
    )
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    config.injection_order_source.write_text(
        "Sample_Name,Injection_Order\nSampleA,1\n", encoding="utf-8"
    )
    targets = [_target("Analyte")]

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
    ) -> RunOutput:
        assert config_arg is config
        assert targets_arg is targets
        assert [path.name for path in raw_paths] == ["SampleA.raw"]
        assert injection_order == {"SampleA": 1}

        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)

    output = extractor.acquire(config, targets)

    assert output.file_results == []


def test_acquire_orders_by_raw_even_with_a_sample_metadata_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend
    from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS

    config = _config(tmp_path)
    sample_metadata_path = tmp_path / "sample_metadata.tsv"
    config = ExtractionConfig(
        **{
            **config.__dict__,
            "injection_order_source": sample_metadata_path,
        }
    )
    (config.data_dir / "RawA.raw").write_text("", encoding="utf-8")
    sample_metadata_path.write_text(
        "\t".join(SAMPLE_METADATA_COLUMNS)
        + "\n"
        + "\t".join(
            [
                "sample_metadata",
                "SampleA",
                "RawA",
                "7",
                "study_sample",
                "",
                "",
                "",
                "",
                "FALSE",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    targets = [_target("Analyte")]

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
    ) -> RunOutput:
        assert config_arg is config
        assert targets_arg is targets
        assert [path.name for path in raw_paths] == ["RawA.raw"]
        # The metadata number 7 annotates nothing about order; the RAW does.
        assert injection_order == {"RawA": 1}

        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)

    output = extractor.acquire(config, targets)

    assert output.file_results == []


def test_serial_backend_keeps_sorted_raw_output_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extractor import acquire

    config = _config(tmp_path)
    (config.data_dir / "B.raw").write_text("", encoding="utf-8")
    (config.data_dir / "A.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())

    output = acquire(config, targets)

    assert [file_result.sample_name for file_result in output.file_results] == [
        "A",
        "B",
    ]


@pytest.mark.parametrize("bind_acquisition", [False, True])
def test_per_file_result_primitive_returns_worker_payload_and_is_pickleable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bind_acquisition: bool,
) -> None:
    from xic_extractor.extraction.target_extraction import extract_raw_file_result

    config = _config(tmp_path)
    raw_path = config.data_dir / "SampleA.raw"
    raw_path.write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    monkeypatch.setattr(_OPEN_RAW_SEAM, _open_raw_factory())
    expectation = None
    if bind_acquisition:
        source = build_acquisition_source_identity(
            raw_path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        stat = raw_path.stat()
        expectation = AcquisitionSourceExpectation(
            source=source,
            path_identity=(
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            ),
        )

    result = extract_raw_file_result(
        7,
        config,
        targets,
        raw_path,
        scoring_context_factory=None,
        expected_acquisition_source=expectation,
    )

    restored = pickle.loads(pickle.dumps(result))
    assert restored.raw_index == 7
    assert restored.sample_name == "SampleA"
    assert restored.file_result.sample_name == "SampleA"
    assert restored.diagnostics == []
    assert restored.file_result.results["Analyte"].peak_result.peak is not None
    provenance = restored.file_result.results[
        "Analyte"
    ].acquisition_observation_provenance
    if bind_acquisition:
        assert provenance is not None
        assert (
            provenance.acquisition_source_id == expectation.source.acquisition_source_id
        )
    else:
        assert provenance is None
    assert not hasattr(restored, "wide_rows")
    assert not hasattr(restored, "long_rows")
    assert not hasattr(restored, "score_breakdown_rows")
    assert not hasattr(restored, "error")


def test_per_file_result_primitive_captures_file_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction.target_extraction import extract_raw_file_result

    config = _config(tmp_path)
    raw_path = config.data_dir / "Bad.raw"
    raw_path.write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    monkeypatch.setattr(
        _OPEN_RAW_SEAM,
        _open_raw_factory(errors={"Bad.raw": RuntimeError("file locked")}),
    )

    result = extract_raw_file_result(
        2,
        config,
        targets,
        raw_path,
        scoring_context_factory=None,
    )

    assert result.raw_index == 2
    assert result.sample_name == "Bad"
    assert result.file_result.error is not None
    assert "file locked" in result.file_result.error
    assert not hasattr(result, "error")
    assert not hasattr(result, "wide_rows")
    assert not hasattr(result, "long_rows")
    assert not hasattr(result, "score_breakdown_rows")
    assert result.diagnostics[0].issue == "FILE_ERROR"


def _config(tmp_path: Path) -> ExtractionConfig:
    data_dir = tmp_path / "raw"
    output_dir = tmp_path / "output"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir()
    output_dir.mkdir()
    dll_dir.mkdir()
    return ExtractionConfig(
        data_dir=data_dir,
        dll_dir=dll_dir,
        output_csv=output_dir / "xic_results.csv",
        diagnostics_csv=output_dir / "xic_diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target(label: str) -> Target:
    return Target(
        label=label,
        mz=258.1085,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=False,
        istd_pair="",
    )


def _open_raw_factory(*, errors: dict[str, Exception] | None = None):
    error_by_name = errors or {}

    def _fake_open_raw(path: Path, dll_dir: Path, **_options):
        if path.name in error_by_name:
            raise error_by_name[path.name]
        return _FakeRaw()

    return _fake_open_raw


class _FakeRaw:
    def acquisition_rt_bounds(self):
        return 0.0, 20.0

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        # The worker/serialization fixture supplies a resolved physical peak,
        # not a sparsely sampled pulse whose qualification is under test.
        rt = np.linspace(rt_min, rt_max, 1001)
        return rt, 1.0 + 1000.0 * np.exp(-0.5 * ((rt - 8.5) / 0.08) ** 2)

    def iter_ms2_scans(self, rt_min: float, rt_max: float):
        return iter([])
