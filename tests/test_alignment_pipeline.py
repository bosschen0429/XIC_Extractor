from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.alignment.pipeline as pipeline_module
from tests.alignment_pipeline_helpers import matrix as _matrix
from tests.alignment_pipeline_helpers import peak_config as _peak_config
from tests.alignment_pipeline_helpers import write_batch as _write_batch
from tests.test_targeted_real_data_adapter import _settings
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.process_execution import AlignmentProcessExecutionError
from xic_extractor.peak_detection.signal_processing_profile import (
    resolve_signal_processing_profile,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_pure_single_provider_cid_nl_dispatches_with_route_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    expected = pipeline_module.AlignmentRunOutputs(
        matrix_tsv=tmp_path / "out" / "alignment_matrix.tsv"
    )
    calls: list[dict[str, object]] = []

    def run_cid_nl(**kwargs: object):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(
        pipeline_module,
        "_run_untargeted_cid_nl_alignment",
        run_cid_nl,
    )

    result = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        discovery_settings=_settings(),
        owner_build_xic_backend="raw_superwindow",
    )

    assert result is expected
    assert len(calls) == 1
    assert calls[0]["settings"] == _settings()
    assert calls[0]["outputs"] == expected
    assert calls[0]["owner_build_route_receipt"].as_dict() == {
        "configured_owner_build_xic_backend": "raw_superwindow",
        "effective_owner_build_route": "retained_untargeted_cid_nl_batch",
        "effective_owner_build_xic_backend": None,
        "effective_route_validation": "owner_validated",
        "processing_run_spec_id": None,
        "processing_run_spec_hash": None,
    }


@pytest.mark.parametrize("writer_fails", (False, True))
@pytest.mark.parametrize("with_reference", (False, True))
@pytest.mark.parametrize("raw_workers", (1, 2))
def test_pure_cid_nl_wrapper_reports_retained_route_and_typed_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_fails: bool,
    with_reference: bool,
    raw_workers: int,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    closed: list[bool] = []

    class MockCompleted:
        def close(self) -> None:
            closed.append(True)

    completed = MockCompleted()
    writer_inputs = SimpleNamespace(
        matrix=_matrix(("S1",)),
        production_decisions=object(),
    )
    retained_sample_metadata = (
        SampleMetadata(
            sample_name="S1",
            raw_stem="S1",
            injection_order=4,
            sample_role="blank",
            excluded=True,
            exclusion_reason="operator exclusion",
        ),
    )
    captured_retained: dict[str, object] = {}
    captured_write: dict[str, object] = {}
    reference_provenance = {
        "workbook_sha256": "a" * 64, "content_sha256": "b" * 64,
    }
    reference = SimpleNamespace(
        provenance=reference_provenance,
        explanation_provenance={
            **reference_provenance,
            "calibration": {
                "schema_version": "known_istd_calibration", "samples": [],
            },
        },
    )
    monkeypatch.setattr(
        pipeline_module, "read_known_istd_reference", lambda _path: reference,
    )

    def complete(**kwargs: object) -> object:
        captured_retained.update(kwargs)
        return completed

    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        complete,
    )
    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        lambda value: writer_inputs if value is completed else pytest.fail(),
    )

    def write_outputs(*_args: object, **kwargs: object) -> None:
        captured_write.update(kwargs)
        if writer_fails:
            raise OSError("Matrix publication failed")
        matrix_path = tmp_path / "out" / "alignment_matrix.tsv"
        matrix_path.parent.mkdir(parents=True, exist_ok=True)
        matrix_path.write_text("Mz\tRT\tS1\n", encoding="utf-8")

    report = tmp_path / "out" / "cid_nl_explanation.sqlite"

    def write_explanation(path: Path, **kwargs: object) -> Path:
        assert kwargs["completed"] is completed
        assert kwargs["writer_inputs"] is writer_inputs
        assert kwargs["matrix_path"].read_text(encoding="utf-8") == "Mz\tRT\tS1\n"
        assert kwargs["known_istd_reference_provenance"] == (
            reference.explanation_provenance if with_reference else None
        )
        path.write_text("published", encoding="utf-8")
        return path

    monkeypatch.setattr(pipeline_module, "_write_outputs_atomic", write_outputs)
    monkeypatch.setattr(
        pipeline_module, "write_cid_nl_explanation", write_explanation, raising=False
    )

    if writer_fails:
        with pytest.raises(OSError, match="Matrix publication failed"):
            pipeline_module.run_alignment(
                discovery_batch_index=batch_index,
                raw_dir=tmp_path / "raw",
                dll_dir=tmp_path / "dll",
                output_dir=tmp_path / "out",
                alignment_config=AlignmentConfig(),
                peak_config=_peak_config(),
                discovery_settings=_settings(),
                raw_workers=raw_workers,
            )
        assert not report.exists()
        assert closed == [True]
        return

    result = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        discovery_settings=_settings(),
        owner_build_xic_backend="raw_superwindow",
        raw_workers=raw_workers,
        sample_metadata=retained_sample_metadata,
        known_istd_reference_workbook=(
            tmp_path / "reference.xlsx" if with_reference else None
        ),
    )

    assert report.read_text(encoding="utf-8") == "published"
    assert result.untargeted_cid_nl_result is completed
    assert captured_retained["sample_metadata"] is retained_sample_metadata
    assert captured_retained["raw_workers"] == raw_workers
    assert captured_retained["known_istd_reference"] is (
        reference if with_reference else None
    )
    assert result.owner_build_route_receipt is not None
    assert result.owner_build_route_receipt.as_dict() == {
        "configured_owner_build_xic_backend": "raw_superwindow",
        "effective_owner_build_route": "retained_untargeted_cid_nl_batch",
        "effective_owner_build_xic_backend": None,
        "effective_route_validation": "owner_validated",
        "processing_run_spec_id": None,
        "processing_run_spec_hash": None,
    }
    metadata = captured_write["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["owner_build_xic_backend"] == "raw_superwindow"
    assert metadata["owner_build_xic_backend_role"] == "configured_legacy_option"
    assert metadata["effective_owner_build_route"] == (
        "retained_untargeted_cid_nl_batch"
    )
    assert metadata["effective_owner_build_xic_backend"] == "none"
    assert metadata.get("known_istd_reference_content_sha256") == (
        reference.provenance["content_sha256"] if with_reference else None
    )
    assert closed == [True]


def test_pure_cid_nl_cancellation_after_completion_skips_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    completed = object()
    cancellation_checks = iter((False, True))
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: completed,
    )
    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        lambda _value: pytest.fail("cancelled run reached writer projection"),
    )
    monkeypatch.setattr(
        pipeline_module,
        "_write_outputs_atomic",
        lambda *_args, **_kwargs: pytest.fail("cancelled run published outputs"),
    )

    with pytest.raises(InterruptedError, match="before output publication"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            should_cancel=lambda: next(cancellation_checks),
        )


def test_pure_cid_nl_cancellation_during_projection_skips_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    completed = object()
    writer_inputs = SimpleNamespace(
        matrix=_matrix(("S1",)),
        production_decisions=object(),
    )
    cancellation_checks = iter((False, False, True))
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: completed,
    )

    def project(value: object) -> object:
        assert value is completed
        return writer_inputs

    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        project,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_write_outputs_atomic",
        lambda *_args, **_kwargs: pytest.fail("cancelled run published outputs"),
    )

    with pytest.raises(InterruptedError, match="before output publication"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            should_cancel=lambda: next(cancellation_checks),
        )


def test_pure_cid_nl_runtime_failure_uses_alignment_error_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))

    def fail_runtime(**_kwargs: object) -> object:
        raise RuntimeError("retained runtime failed")

    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        fail_runtime,
    )

    with pytest.raises(AlignmentProcessExecutionError, match="retained runtime failed"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
        )


def test_pure_cid_nl_collision_rejects_before_retained_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "alignment_matrix.tsv").write_text("owned\n", encoding="utf-8")
    monkeypatch.setattr(
        pipeline_module,
        "_run_untargeted_cid_nl_alignment",
        lambda **_kwargs: pytest.fail("collision reached retained runtime"),
    )

    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            raw_workers=2,
        )


def test_pure_cid_nl_rejects_invalid_worker_budget_before_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: pytest.fail("invalid knobs reached retained acquisition"),
    )

    with pytest.raises(ValueError, match="raw_workers"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            raw_workers=0,
        )


def test_pure_cid_nl_rejects_invalid_worker_backend_before_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: pytest.fail("invalid knobs reached retained acquisition"),
    )

    with pytest.raises(ValueError, match="raw_worker_backend"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
            raw_worker_backend="invalid_backend",
        )


def test_pure_cid_nl_projection_failure_publishes_no_owned_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    output_dir = tmp_path / "out"
    completed = object()
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **_kwargs: completed,
    )

    def reject_projection(value: object) -> object:
        assert value is completed
        raise ValueError("invalid finalized projection")

    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        reject_projection,
    )

    with pytest.raises(ValueError, match="invalid finalized projection"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
        )

    assert not (output_dir / "alignment_matrix.tsv").exists()
    assert not (output_dir / "alignment_results.xlsx").exists()
    assert not (output_dir / "review_report.html").exists()


def test_pipeline_rejects_profile_config_conflict_before_read_or_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched = False

    def fail_if_touched(*_args, **_kwargs):
        nonlocal touched
        touched = True
        raise AssertionError("entry validation must fail before batch or RAW work")

    monkeypatch.setattr(
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_if_touched
    )

    with pytest.raises(ValueError, match="conflicts with peak_config"):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "batch.csv",
            raw_dir=tmp_path / "raw",
            dll_dir=tmp_path / "dll",
            output_dir=tmp_path / "out",
            alignment_config=AlignmentConfig(),
            peak_config=replace(
                _peak_config(),
                signal_processing_window_points=21,
            ),
            discovery_settings=_settings(),
            signal_processing_profile=resolve_signal_processing_profile(
                signal_processing_window_points=15,
            ),
        )

    assert touched is False


@pytest.mark.parametrize("suffix", ("", ".tmp", ".bak"))
def test_pipeline_rejects_owned_output_collision_before_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    batch_index = _write_batch(tmp_path, ("Sample_A",))
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Sample_A.raw").write_text("raw", encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    sentinel = output_dir / f"alignment_matrix.tsv{suffix}"
    sentinel.write_bytes(b"sentinel")
    opened = False

    def fail_opener(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("RAW must not open after an output collision")

    monkeypatch.setattr(
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_opener
    )

    with pytest.raises(FileExistsError, match="owned artifact collisions"):
        pipeline_module.run_alignment(
            discovery_batch_index=batch_index,
            raw_dir=raw_dir,
            dll_dir=tmp_path / "dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=_settings(),
        )

    assert opened is False
    assert sentinel.read_bytes() == b"sentinel"


@pytest.mark.parametrize(
    "settings", [None, replace(_settings(), tag_combine_mode="intersection")]
)
def test_pipeline_rejects_unsupported_discovery_before_acquisition_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    output_dir = tmp_path / "out"

    def fail_if_touched(*_args, **_kwargs):
        pytest.fail("unsupported discovery reached acquisition or output")

    monkeypatch.setattr(
        pipeline_module, "complete_untargeted_cid_nl_alignment", fail_if_touched
    )
    monkeypatch.setattr(pipeline_module, "_output_paths", fail_if_touched)

    with pytest.raises(ValueError, match="pure single-provider CID-NL"):
        pipeline_module.run_alignment(
            discovery_batch_index=tmp_path / "missing-batch.csv",
            raw_dir=tmp_path / "missing-raw",
            dll_dir=tmp_path / "missing-dll",
            output_dir=output_dir,
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(),
            discovery_settings=settings,
        )

    assert not output_dir.exists()


def test_known_reference_provenance_survives_sqlite_summary(tmp_path, monkeypatch):
    from tests.test_alignment_drift_evidence import _known_reference_workbook
    from tests.test_cid_nl_explanation import _owner_run
    from xic_extractor.alignment.drift_evidence import read_known_istd_reference
    from xic_extractor.diagnostics.cid_nl_explanation import read_cid_nl_explanation
    from xic_extractor.diagnostics.cid_nl_explanation_projection import (
        write_cid_nl_explanation,
    )

    completed, writer, candidates, matrix, config = _owner_run(
        tmp_path, monkeypatch, "positive"
    )
    arguments = dict(
        completed=completed, writer_inputs=writer,
        candidates_by_sample=candidates, matrix_path=matrix, alignment_config=config,
    )
    plain = write_cid_nl_explanation(tmp_path / "plain.sqlite", **arguments)
    plain_summary = read_cid_nl_explanation(plain)
    assert "known_istd_reference" not in plain_summary
    matrix_bytes = matrix.read_bytes()
    reference_path = tmp_path / "reference.xlsx"
    _known_reference_workbook(reference_path)
    provenance = read_known_istd_reference(reference_path).provenance
    assert "content_sha256" in provenance
    report = write_cid_nl_explanation(
        tmp_path / "reference.sqlite", **arguments,
        known_istd_reference_provenance=provenance,
    )
    assert read_cid_nl_explanation(report) == {
        **plain_summary, "known_istd_reference": provenance,
    }
    candidate_id = candidates["S1"][0].candidate_id
    assert read_cid_nl_explanation(report, candidate_id=candidate_id) == (
        read_cid_nl_explanation(plain, candidate_id=candidate_id)
    )
    assert matrix.read_bytes() == matrix_bytes


def test_pure_cid_nl_targets_csv_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_index = _write_batch(tmp_path, ("S1",))
    targets_csv = tmp_path / "targets.csv"
    targets_csv.write_text(
        "label,mz,rt_min,rt_max,ppm_tol,neutral_loss_da,nl_ppm_warn,nl_ppm_max,is_istd,istd_pair\n"
        "ISTD1,200.0,1.0,5.0,10.0,100.0,10.0,20.0,true,\n",
        encoding="utf-8",
    )
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "S1.raw").touch()

    completed = object()
    writer_inputs = SimpleNamespace(
        matrix=_matrix(("S1",)),
        production_decisions=object(),
    )
    fake_ref = SimpleNamespace(
        provenance={"workbook_sha256": "b" * 64},
        explanation_provenance={
            "workbook_sha256": "b" * 64,
            "calibration": {
                "schema_version": "known_istd_calibration", "samples": [],
            },
        },
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        pipeline_module,
        "extract_batch_known_istd_reference",
        lambda **kwargs: fake_ref,
    )
    monkeypatch.setattr(
        pipeline_module,
        "complete_untargeted_cid_nl_alignment",
        lambda **kwargs: captured.update(kwargs) or completed,
    )
    monkeypatch.setattr(
        pipeline_module,
        "project_completed_untarget_batch_writer_inputs",
        lambda _value: writer_inputs,
    )
    monkeypatch.setattr(
        pipeline_module,
        "_write_outputs_atomic",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline_module,
        "write_cid_nl_explanation",
        lambda *_args, **_kwargs: tmp_path / "out" / "cid_nl_explanation.sqlite",
        raising=False,
    )
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)

    result = pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=raw_dir,
        dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(),
        discovery_settings=_settings(),
        targets_csv=targets_csv,
    )

    assert result.untargeted_cid_nl_result is completed
    assert captured["known_istd_reference"] is fake_ref


def test_mixed_target_list_only_supplies_istds_to_untargeted_alignment(
    tmp_path, monkeypatch,
):
    import xic_extractor.alignment.raw_sources as raw_sources
    from xic_extractor.configuration.targets import read_targets

    source = Path(__file__).parents[1] / "config" / "targets.example.csv"
    all_targets = read_targets(source)
    batch_index = _write_batch(tmp_path, ("S1",))
    captured = {}

    def extract_reference(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(raw_sources, "existing_raw_paths", lambda **kwargs: {})
    monkeypatch.setattr(
        pipeline_module, "extract_batch_known_istd_reference", extract_reference,
    )
    monkeypatch.setattr(
        pipeline_module, "_run_untargeted_cid_nl_alignment", lambda **kwargs: None,
    )
    pipeline_module.run_alignment(
        discovery_batch_index=batch_index,
        raw_dir=tmp_path / "raw", dll_dir=tmp_path / "dll",
        output_dir=tmp_path / "out", alignment_config=AlignmentConfig(),
        peak_config=_peak_config(), discovery_settings=_settings(),
        targets_csv=source,
    )
    assert captured["targets"] == tuple(t for t in all_targets if t.is_istd)
    assert len(captured["targets"]) < len(all_targets)
