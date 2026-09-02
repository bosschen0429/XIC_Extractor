import pickle
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.config import ConfigError, ExtractionConfig, Target
from xic_extractor.extractor import RunOutput
from xic_extractor.peak_detection.model_selection import ExpectedDiffApprovalRecord
from xic_extractor.rt_prior_library import LibraryEntry
from xic_extractor.signal_processing import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.target_pair_rt_calibration import (
    TARGET_PAIR_RT_CALIBRATION_FIELDS,
)


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

    config = _config(tmp_path, keep_intermediate_csv=False)
    (config.data_dir / "B.raw").write_text("", encoding="utf-8")
    (config.data_dir / "A.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    returned = RunOutput(file_results=[], diagnostics=[])
    calls: list[tuple[ExtractionConfig, list[Target], list[str]]] = []
    approval = _expected_diff_approval()

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
        rt_prior_library=None,
        model_selection_expected_diff_approvals=None,
    ) -> RunOutput:
        calls.append((config_arg, targets_arg, [path.name for path in raw_paths]))
        assert progress_callback is _progress
        assert should_stop is _should_stop
        assert injection_order == {"SampleA": 1}
        assert rt_prior_library == {}
        assert model_selection_expected_diff_approvals == {
            approval.stable_row_id: approval
        }
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
        rt_prior_library={},
        model_selection_expected_diff_approvals={
            approval.stable_row_id: approval
        },
    )

    assert output is returned
    assert calls == [(config, targets, ["A.raw", "B.raw"])]


def test_acquire_applies_targeted_ms1_shape_identity_support_tsv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    support_path = tmp_path / "targeted_ms1_shape_identity_v0.tsv"
    config = ExtractionConfig(
        **{
            **_config(tmp_path, keep_intermediate_csv=False).__dict__,
            "targeted_ms1_shape_identity_support_tsv": support_path,
            "targeted_ms1_shape_identity_activation_policy": (
                "limited_5hmdc_5medc_v1"
            ),
        }
    )
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    returned = RunOutput(file_results=[], diagnostics=[])
    supports = (object(),)
    calls: dict[str, object] = {}

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
        rt_prior_library=None,
        model_selection_expected_diff_approvals=None,
    ) -> RunOutput:
        calls["serial_output_written"] = False
        return returned

    def _fake_load_supports(path: Path, *, activation_policy: str):
        calls["support_path"] = path
        calls["activation_policy"] = activation_policy
        return supports

    def _fake_apply_supports(output, *, targets, supports):
        calls["projection_input"] = output
        calls["projection_targets"] = targets
        calls["projection_supports"] = supports
        return output

    def _fake_write_outputs(config_arg, targets_arg, output):
        calls["written_output"] = output

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.load_targeted_ms1_shape_identity_supports",
        _fake_load_supports,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.apply_targeted_ms1_shape_identity_projection",
        _fake_apply_supports,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.write_outputs",
        _fake_write_outputs,
    )

    output = extractor.acquire(config, targets)

    assert output is returned
    assert calls["support_path"] == support_path
    assert calls["activation_policy"] == "limited_5hmdc_5medc_v1"
    assert calls["projection_input"] is returned
    assert calls["projection_targets"] == targets
    assert calls["projection_supports"] == supports
    assert "written_output" not in calls


def test_acquire_limited_shape_identity_policy_without_support_tsv_keeps_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    config = ExtractionConfig(
        **{
            **_config(tmp_path, keep_intermediate_csv=False).__dict__,
            "targeted_ms1_shape_identity_activation_policy": (
                "limited_5hmdc_5medc_v1"
            ),
        }
    )
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    returned = RunOutput(file_results=[], diagnostics=[])
    calls: dict[str, object] = {}

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
        rt_prior_library=None,
        model_selection_expected_diff_approvals=None,
    ) -> RunOutput:
        return returned

    def _unexpected_load_supports(*_args, **_kwargs):
        raise AssertionError("support loader must not run without support TSV")

    def _unexpected_apply_supports(*_args, **_kwargs):
        raise AssertionError("projection must not run without support TSV")

    def _fake_write_outputs(config_arg, targets_arg, output):
        calls["written_output"] = output

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.load_targeted_ms1_shape_identity_supports",
        _unexpected_load_supports,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.apply_targeted_ms1_shape_identity_projection",
        _unexpected_apply_supports,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.write_outputs",
        _fake_write_outputs,
    )

    output = extractor.acquire(config, targets)

    assert output is returned
    assert "written_output" not in calls


def test_acquire_returns_projected_output_without_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    config = _config(tmp_path, keep_intermediate_csv=False)
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    returned = RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(
        serial_backend,
        "run_serial",
        lambda *_args, **_kwargs: returned,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.write_outputs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("acquisition must not publish final artifacts")
        ),
    )

    assert extractor.acquire(config, targets) is returned


def test_acquire_reports_invalid_targeted_ms1_shape_identity_support_as_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    support_path = tmp_path / "targeted_ms1_shape_identity_v0.tsv"
    config = ExtractionConfig(
        **{
            **_config(tmp_path, keep_intermediate_csv=False).__dict__,
            "targeted_ms1_shape_identity_support_tsv": support_path,
        }
    )
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    returned = RunOutput(file_results=[], diagnostics=[])

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
        rt_prior_library=None,
        model_selection_expected_diff_approvals=None,
    ) -> RunOutput:
        return returned

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.load_targeted_ms1_shape_identity_supports",
        lambda _path, *, activation_policy: (_ for _ in ()).throw(
            ValueError("missing required columns")
        ),
    )

    with pytest.raises(ConfigError, match="missing required columns"):
        extractor.acquire(config, targets)


def test_acquire_resolves_scoring_inputs_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    config = _config(tmp_path, keep_intermediate_csv=False)
    config = ExtractionConfig(
        **{
            **config.__dict__,
            "injection_order_source": tmp_path / "sample_info.csv",
            "rt_prior_library_path": tmp_path / "rt_prior_library.csv",
            "config_hash": "abcd1234",
        }
    )
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    library = {
        ("Analyte", "Analyte"): LibraryEntry(
            config_hash="abcd1234",
            target_label="Analyte",
            role="Analyte",
            istd_pair="",
            median_delta_rt=None,
            sigma_delta_rt=None,
            median_abs_rt=8.5,
            sigma_abs_rt=0.1,
            n_samples=3,
            updated_at="2026-05-07",
        )
    }

    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_inputs.read_injection_order",
        lambda path: {"SampleA": 1},
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_inputs.load_library",
        lambda path, config_hash: library,
    )

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
        rt_prior_library=None,
        model_selection_expected_diff_approvals=None,
    ) -> RunOutput:
        assert config_arg is config
        assert targets_arg is targets
        assert [path.name for path in raw_paths] == ["SampleA.raw"]
        assert injection_order == {"SampleA": 1}
        assert rt_prior_library == library
        assert model_selection_expected_diff_approvals is None
        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)

    output = extractor.acquire(config, targets)

    assert output.file_results == []


def test_acquire_resolves_sample_metadata_source_as_injection_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend
    from xic_extractor.sample_metadata import SAMPLE_METADATA_COLUMNS

    config = _config(tmp_path, keep_intermediate_csv=False)
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
                "sample_metadata_v1",
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
        rt_prior_library=None,
        model_selection_expected_diff_approvals=None,
    ) -> RunOutput:
        assert config_arg is config
        assert targets_arg is targets
        assert [path.name for path in raw_paths] == ["RawA.raw"]
        assert injection_order == {"SampleA": 7, "RawA": 7}
        assert rt_prior_library == {}
        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)

    output = extractor.acquire(config, targets)

    assert output.file_results == []


def test_target_pair_rt_calibration_fences_legacy_rt_prior_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor import extractor
    from xic_extractor.extraction import serial_backend

    config = _config(tmp_path, keep_intermediate_csv=False)
    config = ExtractionConfig(
        **{
            **config.__dict__,
            "rt_prior_library_path": tmp_path / "rt_prior_library.csv",
            "target_pair_rt_calibration_path": (
                tmp_path / "target_pair_rt_calibration.tsv"
            ),
            "config_hash": "abcd1234",
        }
    )
    (config.data_dir / "SampleA.raw").write_text("", encoding="utf-8")
    assert config.target_pair_rt_calibration_path is not None
    config.target_pair_rt_calibration_path.write_text(
        "\t".join(TARGET_PAIR_RT_CALIBRATION_FIELDS) + "\n",
        encoding="utf-8",
    )
    targets = [_target("Analyte")]
    injected_library = {
        ("Analyte", "analyte"): LibraryEntry(
            config_hash="abcd1234",
            target_label="Analyte",
            role="analyte",
            istd_pair="ISTD",
            median_delta_rt=0.25,
            sigma_delta_rt=0.05,
            median_abs_rt=None,
            sigma_abs_rt=None,
            n_samples=8,
            updated_at="2026-06-03",
        )
    }
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_inputs.load_library",
        lambda path, config_hash: injected_library,
    )

    def _fake_run_serial(
        config_arg: ExtractionConfig,
        targets_arg: list[Target],
        *,
        raw_paths: list[Path],
        progress_callback=None,
        should_stop=None,
        injection_order=None,
        rt_prior_library=None,
        model_selection_expected_diff_approvals=None,
    ) -> RunOutput:
        assert rt_prior_library == {}
        return RunOutput(file_results=[], diagnostics=[])

    monkeypatch.setattr(serial_backend, "run_serial", _fake_run_serial)

    extractor.acquire(config, targets, rt_prior_library=injected_library)


def test_resolve_injection_order_returns_empty_dict_without_raw_files(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.targeted_inputs import resolve_injection_order

    config = _config(tmp_path, keep_intermediate_csv=False)

    assert resolve_injection_order(config, [], None) == {}


def test_serial_backend_keeps_sorted_raw_output_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extractor import acquire

    config = _config(tmp_path, keep_intermediate_csv=False)
    (config.data_dir / "B.raw").write_text("", encoding="utf-8")
    (config.data_dir / "A.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    monkeypatch.setattr("xic_extractor.extractor.open_raw", _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extractor.find_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1000.0, 2000.0), _ok_peak(8.6, 1100.0, 2100.0)]),
    )

    output = acquire(config, targets)

    assert [file_result.sample_name for file_result in output.file_results] == [
        "A",
        "B",
    ]


def test_per_file_result_primitive_returns_worker_payload_and_is_pickleable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction.target_extraction import extract_raw_file_result

    config = _config(tmp_path, keep_intermediate_csv=False)
    raw_path = config.data_dir / "SampleA.raw"
    raw_path.write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    monkeypatch.setattr("xic_extractor.extractor.open_raw", _open_raw_factory())
    monkeypatch.setattr(
        "xic_extractor.extractor.find_peak_and_area",
        _peak_sequence([_ok_peak(8.5, 1000.0, 2000.0)]),
    )

    result = extract_raw_file_result(
        7,
        config,
        targets,
        raw_path,
        scoring_context_factory=None,
    )

    restored = pickle.loads(pickle.dumps(result))
    assert restored.raw_index == 7
    assert restored.sample_name == "SampleA"
    assert restored.file_result.sample_name == "SampleA"
    assert restored.diagnostics == []
    assert not hasattr(restored, "wide_rows")
    assert not hasattr(restored, "long_rows")
    assert not hasattr(restored, "score_breakdown_rows")
    assert not hasattr(restored, "error")


def test_per_file_result_primitive_captures_file_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction.target_extraction import extract_raw_file_result

    config = _config(tmp_path, keep_intermediate_csv=False)
    raw_path = config.data_dir / "Bad.raw"
    raw_path.write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    monkeypatch.setattr(
        "xic_extractor.extractor.open_raw",
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


def test_raw_file_extraction_result_remains_internal() -> None:
    from xic_extractor import extractor

    assert hasattr(extractor, "RawFileExtractionResult")
    assert "RawFileExtractionResult" not in extractor.__all__


def _config(tmp_path: Path, *, keep_intermediate_csv: bool) -> ExtractionConfig:
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
        keep_intermediate_csv=keep_intermediate_csv,
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


def _expected_diff_approval() -> ExpectedDiffApprovalRecord:
    return ExpectedDiffApprovalRecord(
        stable_row_id="SampleA|Analyte|expected-diff",
        sample_name="SampleA",
        target_label="Analyte",
        legacy_selected_candidate_id="legacy",
        successor_selected_candidate_id="successor",
        public_outputs_touched=("selected rt", "area", "final matrix value"),
        matrix_value_impact="area_value_changed",
        evidence_sources=("ms1_trace",),
        evidence_summary="approved expected-diff fixture",
        validation_tier="targeted_benchmark",
        reviewer_role="implementation-contract-reviewer",
        reviewer_verdict="approved",
        final_label="expected_diff",
    )


def _ok_peak(rt: float, intensity: float, area: float) -> PeakDetectionResult:
    peak = PeakResult(
        rt=rt,
        intensity=intensity,
        intensity_smoothed=intensity,
        area=area,
        peak_start=rt - 0.5,
        peak_end=rt + 0.5,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=rt,
        selection_apex_intensity=intensity,
        selection_apex_index=7,
        raw_apex_rt=rt,
        raw_apex_intensity=intensity,
        raw_apex_index=7,
        prominence=intensity * 0.5,
        quality_flags=(),
        region_scan_count=15,
        region_duration_min=1.0,
        region_edge_ratio=1.5,
    )
    return PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=15,
        max_smoothed=intensity,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )


def _peak_sequence(results: list[PeakDetectionResult]):
    pending = list(results)

    def _fake_find_peak_and_area(
        rt: np.ndarray,
        intensity: np.ndarray,
        config: ExtractionConfig,
        *,
        preferred_rt: float | None = None,
        strict_preferred_rt: bool = False,
        scoring_context_builder: object | None = None,
        istd_confidence_note: str | None = None,
        **_kwargs: object,
    ) -> PeakDetectionResult:
        return pending.pop(0)

    return _fake_find_peak_and_area


def _open_raw_factory(*, errors: dict[str, Exception] | None = None):
    error_by_name = errors or {}

    def _fake_open_raw(path: Path, dll_dir: Path):
        if path.name in error_by_name:
            raise error_by_name[path.name]
        return _FakeRaw()

    return _fake_open_raw


class _FakeRaw:
    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_xic(
        self, mz: float, rt_min: float, rt_max: float, ppm_tol: float
    ) -> tuple[np.ndarray, np.ndarray]:
        return np.asarray([rt_min, rt_max], dtype=float), np.asarray(
            [1.0, 2.0], dtype=float
        )

    def iter_ms2_scans(self, rt_min: float, rt_max: float):
        return iter([])
