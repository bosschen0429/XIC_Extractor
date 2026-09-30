from __future__ import annotations

import concurrent.futures
import multiprocessing
import pickle
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.peak_detection.model_selection import ExpectedDiffApprovalRecord


def test_worker_job_and_result_objects_are_pickleable(tmp_path: Path) -> None:
    from xic_extractor.extraction.jobs import (
        RawFileJob,
        ScoringInputs,
        WorkerError,
        validate_job_payload,
    )
    from xic_extractor.extractor import RawFileExtractionResult

    job = RawFileJob(
        raw_index=3,
        raw_path=tmp_path / "SampleA.raw",
        config=_config(tmp_path),
        targets=(_target("Analyte"),),
        scoring_inputs=ScoringInputs(
            injection_order={"SampleA": 1},
            istd_rts_by_sample={},
            rt_prior_library={},
        ),
        model_selection_expected_diff_approvals={
            "SampleA|Analyte|expected-diff": _expected_diff_approval()
        },
    )
    validate_job_payload(job)
    restored_job = pickle.loads(pickle.dumps(job))

    assert restored_job.raw_index == 3
    assert restored_job.scoring_inputs is not None
    assert restored_job.scoring_inputs.injection_order == {"SampleA": 1}
    assert restored_job.model_selection_expected_diff_approvals is not None
    assert (
        restored_job.model_selection_expected_diff_approvals[
            "SampleA|Analyte|expected-diff"
        ].reviewer_verdict
        == "approved"
    )

    worker_error = WorkerError(raw_index=3, raw_name="SampleA.raw", message="boom")
    restored_error = pickle.loads(pickle.dumps(worker_error))
    assert restored_error.raw_name == "SampleA.raw"
    assert restored_error.message == "boom"

    result = RawFileExtractionResult(
        raw_index=3,
        sample_name="SampleA",
        file_result=None,
        diagnostics=[],
    )
    restored_result = pickle.loads(pickle.dumps(result))
    assert restored_result.raw_index == 3


def test_aggregation_sorts_successful_results_by_raw_index(tmp_path: Path) -> None:
    from xic_extractor.extraction.jobs import collect_ordered_results
    from xic_extractor.extractor import RawFileExtractionResult

    result_b = RawFileExtractionResult(
        raw_index=2,
        sample_name="B",
        file_result=None,
        diagnostics=[],
    )
    result_a = replace(result_b, raw_index=1, sample_name="A")

    assert collect_ordered_results([result_b, result_a]) == [result_a, result_b]


def test_worker_error_result_is_surfaced_with_raw_name() -> None:
    from xic_extractor.extraction.jobs import (
        ParallelExecutionError,
        WorkerError,
        collect_ordered_results,
    )

    with pytest.raises(ParallelExecutionError, match="SampleA.raw"):
        collect_ordered_results(
            [WorkerError(raw_index=1, raw_name="SampleA.raw", message="DLL failed")]
        )


def test_istd_prepass_result_collection_reuses_worker_error_contract() -> None:
    from xic_extractor.extraction.jobs import (
        IstdPrepassResult,
        ParallelExecutionError,
        WorkerError,
        collect_istd_prepass_results,
    )

    result_b = IstdPrepassResult(
        raw_index=2,
        raw_name="B.raw",
        sample_name="B",
        anchors={},
        results={},
        diagnostics=[],
        shape_metrics={},
    )
    result_a = IstdPrepassResult(
        raw_index=1,
        raw_name="A.raw",
        sample_name="A",
        anchors={},
        results={},
        diagnostics=[],
        shape_metrics={},
    )

    assert collect_istd_prepass_results([result_b, result_a]) == [
        result_a,
        result_b,
    ]
    with pytest.raises(ParallelExecutionError, match="A.raw"):
        collect_istd_prepass_results(
            [WorkerError(raw_index=1, raw_name="A.raw", message="boom")]
        )


def test_job_payload_rejects_callables_and_open_file_handles(tmp_path: Path) -> None:
    from xic_extractor.extraction.jobs import (
        RawFileJob,
        validate_job_payload,
    )

    job = RawFileJob(
        raw_index=1,
        raw_path=tmp_path / "SampleA.raw",
        config=_config(tmp_path),
        targets=(_target("Analyte"),),
        scoring_inputs=lambda: None,
    )
    with pytest.raises(TypeError, match="callable"):
        validate_job_payload(job)

    handle = (tmp_path / "payload.txt").open("w", encoding="utf-8")
    try:
        job = replace(job, scoring_inputs=handle)
        with pytest.raises(TypeError, match="file handle"):
            validate_job_payload(job)
    finally:
        handle.close()


def test_process_pool_spawn_can_run_importable_no_raw_worker(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.jobs import (
        SpawnSmokeJob,
        spawn_smoke_worker,
    )

    context = multiprocessing.get_context("spawn")
    job = SpawnSmokeJob(raw_index=5, raw_name="SampleA.raw")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
    ) as executor:
        future = executor.submit(spawn_smoke_worker, job)
        assert future.result(timeout=30) == "5:SampleA.raw"


def test_parallel_istd_prepass_submits_one_job_per_raw_file(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.jobs import IstdPrepassResult
    from xic_extractor.extraction.process_backend import collect_istd_prepass_process

    config = replace(_config(tmp_path), parallel_mode="process", parallel_workers=2)
    raw_paths = [tmp_path / "A.raw", tmp_path / "B.raw"]
    targets = (_target("ISTD", is_istd=True),)
    submitted = []

    def _runner(jobs, *, max_workers, **_kwargs):
        submitted.extend(jobs)
        assert max_workers == 2
        return [
            IstdPrepassResult(
                raw_index=job.raw_index,
                raw_name=job.raw_path.name,
                sample_name=job.raw_path.stem,
                anchors={"ISTD": float(job.raw_index)},
                results={},
                diagnostics=[],
                shape_metrics={},
            )
            for job in reversed(jobs)
        ]

    collect_istd_prepass_process(
        config,
        targets,
        raw_paths,
        runner=_runner,
    )

    assert [(job.raw_index, job.raw_path.name) for job in submitted] == [
        (1, "A.raw"),
        (2, "B.raw"),
    ]
    assert all(job.targets == targets for job in submitted)


def test_parallel_istd_prepass_aggregates_out_of_completion_order(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.jobs import IstdPrepassResult
    from xic_extractor.extraction.process_backend import collect_istd_prepass_process

    config = replace(_config(tmp_path), parallel_mode="process", parallel_workers=2)
    raw_paths = [tmp_path / "A.raw", tmp_path / "B.raw"]

    def _runner(jobs, *, max_workers, **_kwargs):
        return [
            IstdPrepassResult(
                raw_index=2,
                raw_name="B.raw",
                sample_name="B",
                anchors={"ISTD": 9.2},
                results={},
                diagnostics=[],
                shape_metrics={},
            ),
            IstdPrepassResult(
                raw_index=1,
                raw_name="A.raw",
                sample_name="A",
                anchors={"ISTD": 9.1},
                results={},
                diagnostics=[],
                shape_metrics={},
            ),
        ]

    istd_rts_by_sample = collect_istd_prepass_process(
        config,
        (_target("ISTD", is_istd=True),),
        raw_paths,
        runner=_runner,
    )

    assert istd_rts_by_sample == {"ISTD": {"A": 9.1, "B": 9.2}}


def test_parallel_istd_prepass_reports_worker_failure_with_raw_name(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.jobs import (
        ParallelExecutionError,
        WorkerError,
    )
    from xic_extractor.extraction.process_backend import collect_istd_prepass_process

    config = replace(_config(tmp_path), parallel_mode="process", parallel_workers=2)
    raw_paths = [tmp_path / "A.raw"]

    def _runner(jobs, *, max_workers, **_kwargs):
        return [WorkerError(raw_index=1, raw_name="A.raw", message="boom")]

    with pytest.raises(ParallelExecutionError, match="A.raw"):
        collect_istd_prepass_process(
            config,
            (_target("ISTD", is_istd=True),),
            raw_paths,
            runner=_runner,
        )


def test_parallel_full_extraction_submits_pickleable_scoring_jobs_and_sorts(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.jobs import (
        ScoringInputs,
        validate_job_payload,
    )
    from xic_extractor.extraction.process_backend import (
        collect_raw_file_results_process,
    )
    from xic_extractor.extractor import (
        FileResult,
        RawFileExtractionResult,
    )

    config = replace(_config(tmp_path), parallel_mode="process", parallel_workers=2)
    raw_paths = [tmp_path / "A.raw", tmp_path / "B.raw"]
    targets = (_target("Analyte"),)
    scoring_inputs = ScoringInputs(
        injection_order={"A": 1, "B": 2},
        istd_rts_by_sample={"ISTD": {"A": 9.1}},
        rt_prior_library={},
    )
    approval = _expected_diff_approval()
    submitted = []

    def _runner(jobs, *, max_workers, **_kwargs):
        submitted.extend(jobs)
        for job in jobs:
            validate_job_payload(job)
            assert job.scoring_inputs == scoring_inputs
            assert not callable(job.scoring_inputs)
            assert job.model_selection_expected_diff_approvals == {
                approval.stable_row_id: approval
            }
        return [
            RawFileExtractionResult(
                raw_index=2,
                sample_name="B",
                file_result=FileResult(sample_name="B", results={}),
                diagnostics=[DiagnosticRecord("B", "Analyte", "B_DIAG", "kept")],
            ),
            RawFileExtractionResult(
                raw_index=1,
                sample_name="A",
                file_result=FileResult(sample_name="A", results={}),
                diagnostics=[DiagnosticRecord("A", "Analyte", "A_DIAG", "kept")],
            ),
        ]

    ordered = collect_raw_file_results_process(
        config,
        targets,
        raw_paths,
        scoring_inputs,
        model_selection_expected_diff_approvals={approval.stable_row_id: approval},
        runner=_runner,
    )

    assert [(job.raw_index, job.raw_path.name) for job in submitted] == [
        (1, "A.raw"),
        (2, "B.raw"),
    ]
    assert [result.sample_name for result in ordered] == ["A", "B"]
    assert ordered[0].diagnostics[0].issue == "A_DIAG"
    assert not hasattr(ordered[1], "score_breakdown_rows")


def test_parallel_full_extraction_worker_error_fails_with_raw_name(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.jobs import (
        ParallelExecutionError,
        ScoringInputs,
        WorkerError,
    )
    from xic_extractor.extraction.process_backend import (
        collect_raw_file_results_process,
    )

    config = replace(_config(tmp_path), parallel_mode="process", parallel_workers=2)
    raw_paths = [tmp_path / "A.raw"]

    def _runner(jobs, *, max_workers, **_kwargs):
        return [WorkerError(raw_index=1, raw_name="A.raw", message="worker died")]

    with pytest.raises(ParallelExecutionError, match="A.raw"):
        collect_raw_file_results_process(
            config,
            (_target("Analyte"),),
            raw_paths,
            ScoringInputs(
                injection_order={"A": 1},
                istd_rts_by_sample={},
                rt_prior_library={},
            ),
            runner=_runner,
        )


def test_raw_worker_rebuilds_scoring_factory_inside_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction.jobs import (
        RawFileJob,
        ScoringInputs,
        extract_raw_file_job,
    )

    config = _config(tmp_path)
    raw_path = tmp_path / "SampleA.raw"
    target = _target("Analyte")
    rebuilt = {}

    def _fake_build_factory(**kwargs):
        rebuilt.update(kwargs)

        def _factory(**_factory_kwargs):
            return None

        return _factory

    def _fake_extract_raw_file_result(
        raw_index,
        config_arg,
        targets_arg,
        raw_path_arg,
        *,
        scoring_context_factory,
        rt_prior_library,
        model_selection_expected_diff_approvals,
    ):
        rebuilt["worker_factory"] = scoring_context_factory
        rebuilt["worker_rt_prior_library"] = rt_prior_library
        rebuilt["approvals"] = model_selection_expected_diff_approvals
        from xic_extractor.extractor import FileResult, RawFileExtractionResult

        return RawFileExtractionResult(
            raw_index=raw_index,
            sample_name=raw_path_arg.stem,
            file_result=FileResult(sample_name=raw_path_arg.stem, results={}),
            diagnostics=[],
        )

    monkeypatch.setattr(
        "xic_extractor.extraction.jobs.build_scoring_context_factory",
        _fake_build_factory,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.target_extraction.extract_raw_file_result",
        _fake_extract_raw_file_result,
    )

    result = extract_raw_file_job(
        RawFileJob(
            raw_index=7,
            raw_path=raw_path,
            config=config,
            targets=(target,),
            scoring_inputs=ScoringInputs(
                injection_order={"SampleA": 1},
                istd_rts_by_sample={"ISTD": {"SampleA": 9.1}},
                rt_prior_library={},
            ),
            model_selection_expected_diff_approvals={
                "SampleA|Analyte|expected-diff": _expected_diff_approval()
            },
        )
    )

    assert result.raw_index == 7
    assert rebuilt["config"] == config
    assert rebuilt["approvals"] is not None
    assert "SampleA|Analyte|expected-diff" in rebuilt["approvals"]
    assert rebuilt["injection_order"] == {"SampleA": 1}
    assert rebuilt["istd_rts_by_sample"] == {"ISTD": {"SampleA": 9.1}}
    assert rebuilt["rt_prior_library"] == {}
    assert rebuilt["worker_rt_prior_library"] == {}
    assert rebuilt["worker_factory"] is not None


def test_process_run_writes_output_only_after_collecting_worker_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction.pipeline import run_pipeline
    from xic_extractor.extractor import FileResult, RawFileExtractionResult

    config = replace(
        _config(tmp_path),
        keep_intermediate_csv=True,
        parallel_mode="process",
        parallel_workers=2,
    )
    (config.data_dir / "A.raw").write_text("", encoding="utf-8")
    targets = [_target("Analyte")]
    calls = []

    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.preflight_raw_reader",
        lambda _dll_dir: [],
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.process_backend.collect_istd_prepass_process",
        lambda *_args, **_kwargs: {},
    )

    def _fake_collect_raw_results(*_args, **_kwargs):
        calls.append("collect")
        return [
            RawFileExtractionResult(
                raw_index=1,
                sample_name="A",
                file_result=FileResult(sample_name="A", results={}),
                diagnostics=[],
            )
        ]

    def _fake_write_all(*_args, **_kwargs):
        calls.append("write")

    monkeypatch.setattr(
        "xic_extractor.extraction.process_backend.collect_raw_file_results_process",
        _fake_collect_raw_results,
    )
    monkeypatch.setattr(
        "xic_extractor.extraction.pipeline.write_outputs",
        _fake_write_all,
    )

    output = run_pipeline(config, targets)

    assert [file_result.sample_name for file_result in output.file_results] == ["A"]
    assert calls == ["collect", "write"]


def _config(tmp_path: Path) -> ExtractionConfig:
    data_dir = tmp_path / "raw"
    output_dir = tmp_path / "output"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    dll_dir.mkdir(exist_ok=True)
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


def _target(label: str, *, is_istd: bool = False) -> Target:
    return Target(
        label=label,
        mz=258.1085,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=None,
        nl_ppm_warn=None,
        nl_ppm_max=None,
        is_istd=is_istd,
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
