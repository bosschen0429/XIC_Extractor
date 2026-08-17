import pickle
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_alignment_owner_backfill import _feature
from xic_extractor.alignment import (
    backfill_transaction,
    owner_backfill_request_plan,
    owner_matrix,
)
from xic_extractor.alignment.backfill_scope import backfill_features_for_sample
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_coherence.models import (
    IdentityCoherenceTraceRequest,
    IdentityCoherenceTraceResult,
)
from xic_extractor.alignment.matrix import AlignedCell
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    OwnerAssignment,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.process_backend import (
    AlignmentProcessExecutionError,
    IdentityTraceSampleJob,
    IdentityTraceSampleResult,
    OwnerBackfillSampleResult,
    OwnerBackfillTimingStats,
    OwnerBackfillWorkerError,
    OwnerBuildSampleJob,
    OwnerBuildSampleResult,
    OwnerBuildTimingStats,
    OwnerBuildWorkerError,
    extract_identity_trace_sample_job,
    owner_backfill_job_payload_metrics,
    process_job_payload_size_bytes,
    run_identity_trace_process,
    run_owner_backfill_process,
    run_owner_build_jobs,
    run_owner_build_process,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.processing_identity import build_acquisition_source_identity
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.xic_models import XICRequest, XICTrace


def test_owner_backfill_process_builds_pickleable_sample_jobs_and_orders_output(
    tmp_path: Path,
) -> None:
    feature_a = _confirmable_feature_for_sample_a()
    feature_b = replace(_feature(), feature_family_id="FAM000002")
    features = (feature_b, feature_a)
    cell_a = _cell(cluster_id=feature_a.feature_family_id, sample_stem="sample-a")
    cell_b = _cell(cluster_id=feature_b.feature_family_id, sample_stem="sample-b")
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        for job in jobs:
            pickle.loads(pickle.dumps(job))
        assert max_workers == 2
        return [
            OwnerBackfillSampleResult(
                sample_index=2,
                sample_stem="sample-b",
                cells=(cell_b,),
                timing_stats=(
                    OwnerBackfillTimingStats(
                        sample_stem="sample-b",
                        elapsed_sec=2.0,
                        extract_xic_count=3,
                        point_count=30,
                    ),
                ),
            ),
            OwnerBackfillSampleResult(
                sample_index=1,
                sample_stem="sample-a",
                cells=(cell_a,),
                timing_stats=(
                    OwnerBackfillTimingStats(
                        sample_stem="sample-a",
                        elapsed_sec=1.0,
                        extract_xic_count=2,
                        point_count=20,
                    ),
                ),
            ),
        ]

    output = run_owner_backfill_process(
        features,
        sample_order=("sample-a", "sample-b"),
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path, baseline_audit_method="asls"),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        owner_backfill_xic_backend="ms1_index",
        owner_backfill_window_strategy="super-window",
        owner_backfill_superwindow_span_factor=2,
        backfill_scope="production-equivalent",
        emit_region_audit=True,
        region_audit_family_ids=frozenset({"FAM000001"}),
        audit_evidence_mode="full",
        runner=fake_runner,
    )

    assert [job.sample_stem for job in captured_jobs] == ["sample-a", "sample-b"]
    assert [job.sample_index for job in captured_jobs] == [1, 2]
    assert {job.owner_backfill_xic_backend for job in captured_jobs} == {"ms1_index"}
    assert {job.owner_backfill_window_strategy for job in captured_jobs} == {
        "super-window"
    }
    assert {job.owner_backfill_superwindow_span_factor for job in captured_jobs} == {2}
    assert {job.request_plan_id for job in captured_jobs} == {
        "p7-owner-backfill-request-plan-v1"
    }
    assert {job.backfill_scope for job in captured_jobs} == {"production-equivalent"}
    assert [job.feature_payload_count for job in captured_jobs] == [1, 2]
    assert {job.emit_region_audit for job in captured_jobs} == {True}
    assert {job.audit_evidence_mode for job in captured_jobs} == {"full"}
    assert {job.region_audit_family_ids for job in captured_jobs} == {
        frozenset({"FAM000001"})
    }
    assert {job.peak_config.baseline_audit_method for job in captured_jobs} == {"asls"}
    assert [cell.sample_stem for cell in output.cells] == ["sample-b", "sample-a"]
    assert [stat.sample_stem for stat in output.timing_stats] == [
        "sample-a",
        "sample-b",
    ]


def test_process_parent_does_not_materialize_logical_backfill_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_jobs = []

    def forbidden_request_allocation(*args, **kwargs):
        raise AssertionError("parent process materialized a logical Backfill request")

    for name in ("OwnerBackfillRequestItem", "XICRequest"):
        monkeypatch.setattr(
            owner_backfill_request_plan,
            name,
            forbidden_request_allocation,
        )

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        return []

    feature = replace(
        _confirmable_feature_for_sample_a(),
        confirm_local_owners_with_backfill=False,
    )
    run_owner_backfill_process(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        runner=fake_runner,
    )

    assert [job.sample_stem for job in captured_jobs] == ["sample-b"]
    assert [job.features for job in captured_jobs] == [(feature,)]
    assert [job.request_payload_count for job in captured_jobs] == [1]


def test_process_parent_compact_partition_matches_full_request_owner(
    tmp_path: Path,
) -> None:
    feature_a = _confirmable_feature_for_sample_a()
    feature_b = replace(
        _feature(),
        feature_family_id="FAM000002",
        backfill_seed_centers=((500.0, 8.5), (501.0, 8.75)),
    )
    features = (feature_a, feature_b, feature_a)
    sample_order = ("sample-a", "sample-b")
    raw_paths = {
        sample_stem: tmp_path / f"{sample_stem}.raw"
        for sample_stem in sample_order
    }
    alignment_config = AlignmentConfig()
    full_plan = owner_backfill_request_plan.build_owner_backfill_request_plan(
        features,
        sample_order=sample_order,
        raw_sample_stems=frozenset(raw_paths),
        alignment_config=alignment_config,
    )
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        return []

    run_owner_backfill_process(
        features,
        sample_order=sample_order,
        raw_paths=raw_paths,
        dll_dir=tmp_path / "dll",
        alignment_config=alignment_config,
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        runner=fake_runner,
    )

    jobs_by_sample = {job.sample_stem: job for job in captured_jobs}
    for sample_stem in sample_order:
        job = jobs_by_sample[sample_stem]
        assert job.features == full_plan.features_for_sample(sample_stem)
        assert job.request_payload_count == full_plan.request_count_for_sample(
            sample_stem
        )


def test_process_backfill_transaction_keeps_full_matrix_feature_universe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scoped_feature = _confirmable_feature_for_sample_a()
    unrequested_feature = replace(_feature(), feature_family_id="FAM000002")
    allocated_cells = 0
    original_cell_type = owner_matrix.AlignedCell

    def counted_cell(*args, **kwargs):
        nonlocal allocated_cells
        allocated_cells += 1
        return original_cell_type(*args, **kwargs)

    monkeypatch.setattr(owner_matrix, "AlignedCell", counted_cell)

    def fake_runner(
        jobs,
        *,
        max_workers,
        progress_callback,
        success_result_delivery,
    ):
        assert allocated_cells == 0
        assert max_workers == 2
        receipts = []
        for ordinal, job in enumerate(jobs):
            assert job.peak_fact_feature_order_indices == (1,)
            assert job.peak_fact_sample_order_index == (1, 0)[ordinal]
            result = OwnerBackfillSampleResult(
                sample_index=job.sample_index,
                sample_stem=job.sample_stem,
                cells=tuple(
                    _cell(
                        cluster_id=feature.feature_family_id,
                        sample_stem=job.sample_stem,
                    )
                    for feature in job.features
                ),
            )
            receipts.append(success_result_delivery(ordinal, result))
        return receipts

    output = run_owner_backfill_process(
        (scoped_feature,),
        matrix_features=(unrequested_feature, scoped_feature),
        matrix_sample_order=("sample-b", "sample-a"),
        sample_order=("sample-a", "sample-b"),
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        runner=fake_runner,
    )

    assert tuple(
        feature.feature_family_id for feature in output.matrix.clusters
    ) == (unrequested_feature.feature_family_id, scoped_feature.feature_family_id)
    assert tuple(
        (cell.cluster_id, cell.sample_stem) for cell in output.matrix.cells
    ) == (
        (unrequested_feature.feature_family_id, "sample-b"),
        (unrequested_feature.feature_family_id, "sample-a"),
        (scoped_feature.feature_family_id, "sample-b"),
        (scoped_feature.feature_family_id, "sample-a"),
    )
    assert allocated_cells > 0


def test_process_jobs_carry_discovery_acquisition_source_to_raw_workers(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample-a.raw"
    raw_path.write_bytes(b"owner-bound raw")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    captured_build_jobs = []
    captured_backfill_jobs = []

    def build_runner(jobs, *, max_workers):
        captured_build_jobs.extend(jobs)
        return []

    def backfill_runner(jobs, *, max_workers):
        captured_backfill_jobs.extend(jobs)
        return []

    run_owner_build_process(
        (_candidate("sample-a"),),
        sample_order=("sample-a",),
        raw_paths={"sample-a": raw_path},
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        max_workers=2,
        expected_acquisition_sources={"sample-a": source},
        runner=build_runner,
    )
    run_owner_backfill_process(
        (_confirmable_feature_for_sample_a(),),
        sample_order=("sample-a",),
        raw_paths={"sample-a": raw_path},
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        expected_acquisition_sources={"sample-a": source},
        runner=backfill_runner,
    )

    assert [job.expected_acquisition_source for job in captured_build_jobs] == [source]
    assert [job.expected_acquisition_source for job in captured_backfill_jobs] == [
        source
    ]
    assert [
        job.raw_session_capability.acquisition_source
        for job in captured_backfill_jobs
        if job.raw_session_capability is not None
    ] == [source]


def test_owner_backfill_process_sends_only_sample_requested_features(
    tmp_path: Path,
) -> None:
    feature_a = _confirmable_feature_for_sample_a()
    feature_b = replace(
        _feature(),
        feature_family_id="FAM000002",
        backfill_seed_centers=((500.0, 8.5), (501.0, 8.75)),
    )
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        return []

    run_owner_backfill_process(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b"),
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        runner=fake_runner,
    )

    by_sample = {job.sample_stem: job for job in captured_jobs}
    assert tuple(
        feature.feature_family_id for feature in by_sample["sample-a"].features
    ) == (feature_a.feature_family_id,)
    assert tuple(
        feature.feature_family_id for feature in by_sample["sample-b"].features
    ) == (
        feature_a.feature_family_id,
        feature_b.feature_family_id,
    )
    assert by_sample["sample-a"].feature_payload_count == 1
    assert by_sample["sample-b"].feature_payload_count == 2
    assert by_sample["sample-a"].request_payload_count == 1
    assert by_sample["sample-b"].request_payload_count == 3


def test_owner_backfill_process_reports_payload_economics_without_deepening_payload(
    tmp_path: Path,
) -> None:
    feature_a = _confirmable_feature_for_sample_a()
    feature_b = replace(
        _feature(),
        feature_family_id="FAM000002",
        backfill_seed_centers=((500.0, 8.5), (501.0, 8.75)),
    )
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        return []

    run_owner_backfill_process(
        (feature_a, feature_b),
        sample_order=("sample-a", "sample-b"),
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        runner=fake_runner,
    )

    metrics = [owner_backfill_job_payload_metrics(job) for job in captured_jobs]

    assert [
        (item.sample_stem, item.feature_payload_count, item.request_payload_count)
        for item in metrics
    ] == [
        ("sample-a", 1, 1),
        ("sample-b", 2, 3),
    ]
    assert [item.requests_per_feature for item in metrics] == [1.0, 1.5]
    assert all(item.pickle_payload_bytes > 0 for item in metrics)
    assert process_job_payload_size_bytes(captured_jobs[0]) == (
        metrics[0].pickle_payload_bytes
    )


def test_process_job_payload_size_still_rejects_callable_leaf() -> None:
    with pytest.raises(TypeError, match="contains callable value"):
        process_job_payload_size_bytes({"callback": lambda: None})


def test_owner_backfill_process_per_sample_features_match_legacy_scan(
    tmp_path: Path,
) -> None:
    # The per-feature bucketing optimization must produce per-sample job
    # features byte-identical to the original per-sample
    # backfill_features_for_sample scan it replaced.
    feature_a = _confirmable_feature_for_sample_a()
    feature_b = replace(_feature(), feature_family_id="FAM000002")
    features = (feature_a, feature_b)
    sample_order = ("sample-a", "sample-b")
    raw_paths = {
        "sample-a": tmp_path / "sample-a.raw",
        "sample-b": tmp_path / "sample-b.raw",
    }
    alignment_config = AlignmentConfig()
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        return []

    run_owner_backfill_process(
        features,
        sample_order=sample_order,
        raw_paths=raw_paths,
        dll_dir=tmp_path / "dll",
        alignment_config=alignment_config,
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        runner=fake_runner,
    )

    by_sample = {job.sample_stem: job.features for job in captured_jobs}
    raw_sample_stems = frozenset(raw_paths)
    for sample_stem in sample_order:
        expected = backfill_features_for_sample(
            features,
            sample_stem=sample_stem,
            sample_order=sample_order,
            raw_sample_stems=raw_sample_stems,
            alignment_config=alignment_config,
        )
        assert by_sample.get(sample_stem, ()) == expected


def test_owner_backfill_process_accepts_delivery_contract_payload(
    tmp_path: Path,
) -> None:
    feature = _delivery_contract_feature()
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        for job in jobs:
            pickle.loads(pickle.dumps(job))
        assert max_workers == 2
        return []

    run_owner_backfill_process(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_paths={"sample-b": tmp_path / "sample-b.raw"},
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        runner=fake_runner,
    )

    assert len(captured_jobs) == 1
    assert captured_jobs[0].sample_stem == "sample-b"
    assert captured_jobs[0].features == (feature,)
    assert captured_jobs[0].features[0].group_hypothesis_id == "GROUP_CONTRACT"
    assert captured_jobs[0].features[0].public_family_id == "FAM_CONTRACT"
    assert captured_jobs[0].features[0].group_delivery_role == (
        "successor_delivery_protocol"
    )
    assert captured_jobs[0].feature_payload_count == 1


def test_owner_backfill_process_passes_progress_callback_to_runner(
    tmp_path: Path,
) -> None:
    feature = _confirmable_feature_for_sample_a()
    cell = _cell(cluster_id=feature.feature_family_id, sample_stem="sample-a")
    seen = []

    def fake_runner(jobs, *, max_workers, progress_callback):
        result = OwnerBackfillSampleResult(
            sample_index=1,
            sample_stem="sample-a",
            cells=(cell,),
            timing_stats=(
                OwnerBackfillTimingStats(
                    sample_stem="sample-a",
                    elapsed_sec=1.0,
                    extract_xic_count=2,
                    point_count=20,
                ),
            ),
        )
        progress_callback(result)
        return [result]

    run_owner_backfill_process(
        (feature,),
        sample_order=("sample-a",),
        raw_paths={"sample-a": tmp_path / "sample-a.raw"},
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        runner=fake_runner,
        progress_callback=seen.append,
    )

    assert [result.sample_stem for result in seen] == ["sample-a"]


def test_owner_backfill_process_raises_worker_errors(tmp_path: Path) -> None:
    def fake_runner(jobs, *, max_workers):
        return [
            OwnerBackfillWorkerError(
                sample_index=1,
                sample_stem="sample-a",
                raw_name="sample-a.raw",
                message="RuntimeError: boom",
            )
        ]

    with pytest.raises(
        AlignmentProcessExecutionError,
        match="sample-a.raw: RuntimeError: boom",
    ):
        run_owner_backfill_process(
            (_confirmable_feature_for_sample_a(),),
            sample_order=("sample-a",),
            raw_paths={"sample-a": tmp_path / "sample-a.raw"},
            dll_dir=tmp_path / "dll",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
            signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
            max_workers=2,
            runner=fake_runner,
        )


def test_owner_backfill_transaction_discards_success_after_late_worker_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    feature = _confirmable_feature_for_sample_a()
    finish_calls = 0
    original_finish = backfill_transaction.OwnerBackfillSampleTransaction.finish

    def tracked_finish(self):
        nonlocal finish_calls
        finish_calls += 1
        return original_finish(self)

    monkeypatch.setattr(
        backfill_transaction.OwnerBackfillSampleTransaction,
        "finish",
        tracked_finish,
    )

    def fake_runner(
        jobs,
        *,
        max_workers,
        progress_callback,
        success_result_delivery,
    ):
        first, second = jobs
        receipt = success_result_delivery(
            0,
            OwnerBackfillSampleResult(
                first.sample_index,
                first.sample_stem,
                tuple(
                    _cell(
                        cluster_id=item.feature_family_id,
                        sample_stem=first.sample_stem,
                    )
                    for item in first.features
                ),
            ),
        )
        return [
            receipt,
            OwnerBackfillWorkerError(
                sample_index=second.sample_index,
                sample_stem=second.sample_stem,
                raw_name=second.raw_path.name,
                message="RuntimeError: late boom",
            ),
        ]

    with pytest.raises(AlignmentProcessExecutionError, match="late boom"):
        run_owner_backfill_process(
            (feature,),
            matrix_features=(feature,),
            matrix_sample_order=("sample-a", "sample-b", "sample-c"),
            sample_order=("sample-a", "sample-b", "sample-c"),
            raw_paths={
                sample: tmp_path / f"{sample}.raw"
                for sample in ("sample-a", "sample-b", "sample-c")
            },
            dll_dir=tmp_path / "dll",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
            signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
            max_workers=2,
            runner=fake_runner,
        )

    assert finish_calls == 0


def test_owner_build_process_builds_pickleable_sample_jobs_and_merges_output(
    tmp_path: Path,
) -> None:
    owner_a = _owner("sample-a")
    owner_b = _owner("sample-b")
    unresolved_a = OwnerAssignment(
        "sample-a#missing",
        None,
        "unresolved",
        "missing_raw",
    )
    primary_a = OwnerAssignment("sample-a#1", owner_a.owner_id, "primary", "primary")
    primary_b = OwnerAssignment("sample-b#1", owner_b.owner_id, "primary", "primary")
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        for job in jobs:
            pickle.loads(pickle.dumps(job))
        assert max_workers == 2
        return [
            OwnerBuildSampleResult(
                sample_index=2,
                sample_stem="sample-b",
                owners=(owner_b,),
                assignments=(primary_b,),
                ambiguous_records=(),
                timing_stats=(
                    OwnerBuildTimingStats(
                        sample_stem="sample-b",
                        elapsed_sec=2.0,
                        extract_xic_count=3,
                        point_count=30,
                    ),
                ),
            ),
            OwnerBuildSampleResult(
                sample_index=1,
                sample_stem="sample-a",
                owners=(owner_a,),
                assignments=(unresolved_a, primary_a),
                ambiguous_records=(),
                timing_stats=(
                    OwnerBuildTimingStats(
                        sample_stem="sample-a",
                        elapsed_sec=1.0,
                        extract_xic_count=2,
                        point_count=20,
                    ),
                ),
            ),
        ]

    output = run_owner_build_process(
        (_candidate("sample-a"), _candidate("sample-b")),
        sample_order=("sample-a", "sample-b"),
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path, baseline_audit_method="asls"),
        max_workers=2,
        owner_build_xic_backend="ms1_index",
        emit_region_audit=True,
        region_audit_family_ids=frozenset({"sample-a@F0001"}),
        runner=fake_runner,
    )

    assert [job.sample_stem for job in captured_jobs] == ["sample-a", "sample-b"]
    assert [len(job.candidates) for job in captured_jobs] == [1, 1]
    assert {job.owner_build_xic_backend for job in captured_jobs} == {"ms1_index"}
    assert {job.emit_region_audit for job in captured_jobs} == {True}
    assert {job.region_audit_family_ids for job in captured_jobs} == {
        frozenset({"sample-a@F0001"})
    }
    assert {job.peak_config.baseline_audit_method for job in captured_jobs} == {"asls"}
    assert output.ownership.owners == (owner_a, owner_b)
    assert output.ownership.assignments == (unresolved_a, primary_a, primary_b)
    assert [stat.sample_stem for stat in output.timing_stats] == [
        "sample-a",
        "sample-b",
    ]


def test_owner_build_process_raises_worker_errors(tmp_path: Path) -> None:
    def fake_runner(jobs, *, max_workers):
        return [
            OwnerBuildWorkerError(
                sample_index=1,
                sample_stem="sample-a",
                raw_name="sample-a.raw",
                message="RuntimeError: boom",
            )
        ]

    with pytest.raises(
        AlignmentProcessExecutionError,
        match="sample-a.raw: RuntimeError: boom",
    ):
        run_owner_build_process(
            (_candidate("sample-a"),),
            sample_order=("sample-a",),
            raw_paths={"sample-a": tmp_path / "sample-a.raw"},
            dll_dir=tmp_path / "dll",
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
            max_workers=2,
            runner=fake_runner,
        )


def test_owner_build_jobs_accepts_empty_job_list() -> None:
    assert run_owner_build_jobs((), max_workers=2) == []


def test_owner_build_process_handles_missing_raw_in_parent_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _owner("sample-a")
    assignment = OwnerAssignment("sample-a#1", owner.owner_id, "primary", "primary")
    calls = {}
    seen = []

    def fake_build_sample_local_owners(candidates, **kwargs):
        calls["candidates"] = candidates
        calls.update(kwargs)
        return SimpleNamespace(
            owners=(owner,),
            assignments=(assignment,),
            ambiguous_records=(),
        )

    monkeypatch.setattr(
        "xic_extractor.alignment.process_backend.build_sample_local_owners",
        fake_build_sample_local_owners,
    )

    output = run_owner_build_process(
        (_candidate("sample-a"),),
        sample_order=("sample-a",),
        raw_paths={},
        dll_dir=tmp_path / "dll",
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        max_workers=2,
        progress_callback=seen.append,
    )

    assert tuple(candidate.sample_stem for candidate in calls["candidates"]) == (
        "sample-a",
    )
    assert calls["raw_sources"] == {}
    assert output.ownership.owners == (owner,)
    assert output.ownership.assignments == (assignment,)
    assert output.timing_stats == ()
    assert [result.sample_stem for result in seen] == ["sample-a"]


def test_owner_build_jobs_wrap_executor_exceptions_as_build_errors(
    tmp_path: Path,
) -> None:
    job = OwnerBuildSampleJob(
        sample_index=1,
        sample_stem="sample-a",
        raw_path=tmp_path / "sample-a.raw",
        dll_dir=tmp_path / "dll",
        candidates=(_candidate("sample-a"),),
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
    )

    results = run_owner_build_jobs(
        (job,),
        max_workers=1,
        executor_factory=_FailingExecutor,
    )

    assert results == [
        OwnerBuildWorkerError(
            sample_index=1,
            sample_stem="sample-a",
            raw_name="sample-a.raw",
            message="RuntimeError: executor boom",
        )
    ]


def test_owner_build_jobs_wrap_submit_exceptions_as_build_errors(
    tmp_path: Path,
) -> None:
    jobs = (
        OwnerBuildSampleJob(
            sample_index=1,
            sample_stem="sample-a",
            raw_path=tmp_path / "sample-a.raw",
            dll_dir=tmp_path / "dll",
            candidates=(_candidate("sample-a"),),
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
        ),
        OwnerBuildSampleJob(
            sample_index=2,
            sample_stem="sample-b",
            raw_path=tmp_path / "sample-b.raw",
            dll_dir=tmp_path / "dll",
            candidates=(_candidate("sample-b"),),
            alignment_config=AlignmentConfig(),
            peak_config=_peak_config(tmp_path),
        ),
    )

    progress = []
    results = run_owner_build_jobs(
        jobs,
        max_workers=2,
        executor_factory=_SubmitFailingExecutor,
        progress_callback=progress.append,
    )

    assert results == [
        OwnerBuildWorkerError(
            sample_index=1,
            sample_stem="sample-a",
            raw_name="sample-a.raw",
            message="RuntimeError: submit boom",
        )
    ]
    assert progress == results


def test_owner_build_jobs_reject_unexpected_cancelled_census(tmp_path: Path) -> None:
    job = OwnerBuildSampleJob(
        sample_index=1,
        sample_stem="sample-a",
        raw_path=tmp_path / "sample-a.raw",
        dll_dir=tmp_path / "dll",
        candidates=(_candidate("sample-a"),),
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
    )

    with pytest.raises(AlignmentProcessExecutionError, match=r"cancelled=\(0,\)"):
        run_owner_build_jobs(
            (job,),
            max_workers=1,
            executor_factory=_CancelledExecutor,
        )


def test_timed_process_raw_source_records_batch_calls() -> None:
    import xic_extractor.alignment.process_backend as process_module
    from xic_extractor.xic_models import XICRequest, XICTrace

    class BatchSource:
        def __init__(self) -> None:
            self.raw_chromatogram_call_count = 0

        def extract_xic_many(self, requests):
            self.raw_chromatogram_call_count += 1
            return tuple(
                XICTrace.from_arrays([request.rt_min], [request.mz])
                for request in requests
            )

        def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
            self.raw_chromatogram_call_count += 1
            return [rt_min], [mz]

    stats = process_module._TimedProcessStats(sample_stem="Sample_A")
    source = process_module._TimedProcessRawSource(BatchSource(), stats=stats)

    traces = source.extract_xic_many(
        (
            XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
            XICRequest(mz=259.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0),
        )
    )

    assert [trace.intensity.tolist() for trace in traces] == [[258.0], [259.0]]
    assert stats.extract_xic_count == 2
    assert stats.extract_xic_batch_count == 1
    assert stats.raw_chromatogram_call_count == 1
    assert stats.point_count == 2


def test_timed_process_raw_source_delegates_scan_window_lookup() -> None:
    import xic_extractor.alignment.process_backend as process_module
    from xic_extractor.xic_models import XICRequest

    class WindowSource:
        def scan_window_for_request(self, request):
            return (int(request.rt_min), int(request.rt_max))

    stats = process_module._TimedProcessStats(sample_stem="Sample_A")
    source = process_module._TimedProcessRawSource(WindowSource(), stats=stats)

    window = source.scan_window_for_request(
        XICRequest(mz=258.0, rt_min=8.0, rt_max=9.0, ppm_tol=20.0)
    )

    assert window == (8, 9)


def test_timed_process_raw_source_delegates_retention_time_lookup() -> None:
    import xic_extractor.alignment.process_backend as process_module

    class WindowSource:
        def retention_time_for_scan(self, scan_number):
            return scan_number / 100.0

    stats = process_module._TimedProcessStats(sample_stem="Sample_A")
    source = process_module._TimedProcessRawSource(WindowSource(), stats=stats)

    assert source.retention_time_for_scan(812) == 8.12


def _cell(*, cluster_id: str, sample_stem: str) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=cluster_id,
        status="rescued",
        area=100.0,
        apex_rt=8.5,
        height=50.0,
        peak_start_rt=8.4,
        peak_end_rt=8.6,
        rt_delta_sec=0.0,
        trace_quality="owner_backfill",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
        reason="owner-centered MS1 backfill",
    )


def _confirmable_feature_for_sample_a():
    base = _feature()
    low_area_owner = replace(base.owners[0], owner_area=1.0)
    return replace(
        base,
        owners=(low_area_owner, _owner("sample-c")),
        confirm_local_owners_with_backfill=True,
    )


def _delivery_contract_feature():
    owner = SimpleNamespace(sample_stem="sample-a", owner_area=1000.0)
    return SimpleNamespace(
        feature_family_id="FAM_CONTRACT",
        cluster_id="FAM_CONTRACT",
        neutral_loss_tag="NL116",
        family_center_mz=500.0,
        family_center_rt=8.5,
        family_product_mz=383.9526,
        family_observed_neutral_loss_da=116.0474,
        has_anchor=True,
        owners=(owner,),
        members=(owner,),
        event_cluster_ids=("OWN-sample-a-000001",),
        event_member_count=1,
        evidence="test_delivery_contract",
        identity_conflict=False,
        review_only=False,
        confirm_local_owners_with_backfill=False,
        backfill_seed_centers=(),
        ambiguous_sample_stem=None,
        ambiguous_candidate_ids=(),
        group_hypothesis_id="GROUP_CONTRACT",
        public_family_id="FAM_CONTRACT",
        group_construction_role="successor_projection_adapter",
        group_delivery_role="successor_delivery_protocol",
        group_membership_source="cross_sample_peak_group_hypothesis",
        consolidation_state="not_consolidated",
        consolidation_winner_group_hypothesis_id="",
        consolidation_source_group_hypothesis_id="",
    )


def _candidate(sample_stem: str):
    return SimpleNamespace(
        sample_stem=sample_stem,
        candidate_id=f"{sample_stem}#1",
    )


def _owner(sample_stem: str) -> SampleLocalMS1Owner:
    event = IdentityEvent(
        candidate_id=f"{sample_stem}#1",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        neutral_loss_tag="NL116",
        precursor_mz=500.0,
        product_mz=383.9526,
        observed_neutral_loss_da=116.0474,
        seed_rt=8.5,
        evidence_score=80,
        seed_event_count=2,
    )
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample_stem}-000001",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        precursor_mz=500.0,
        owner_apex_rt=8.5,
        owner_peak_start_rt=8.4,
        owner_peak_end_rt=8.6,
        owner_area=100.0,
        owner_height=50.0,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="owner_exact_apex_match",
    )


class _FailingExecutor:
    def __init__(self, *args, **kwargs) -> None:
        self._future: Future = Future()
        self._future.set_exception(RuntimeError("executor boom"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, worker, job):
        return self._future


class _SubmitFailingExecutor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, worker, job):
        raise RuntimeError("submit boom")


class _CancelledExecutor:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def submit(self, worker, job):
        future = Future()
        future.cancel()
        future.set_running_or_notify_cancel()
        return future

    def shutdown(self, *, wait: bool, cancel_futures: bool = False) -> None:
        pass


def _peak_config(
    tmp_path: Path,
    *,
    baseline_audit_method: str = "",
) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "xic_results.csv",
        diagnostics_csv=tmp_path / "xic_diagnostics.csv",
        smooth_window=3,
        smooth_polyorder=1,
        ms1_morphology_smoothing_window_points=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
        baseline_audit_method=baseline_audit_method,
    )


def _identity_trace_request(
    sample_id: str,
    candidate_id: str,
) -> IdentityCoherenceTraceRequest:
    return IdentityCoherenceTraceRequest(
        decision_id=f"DEC-{candidate_id}",
        request_id=f"REQ-{candidate_id}",
        sample_id=sample_id,
        candidate_id=candidate_id,
        precursor_mz=500.0,
        ppm_tolerance=20.0,
        rt_min=5.0,
        rt_max=5.1,
    )


def test_run_identity_trace_process_builds_pickleable_jobs_and_groups_by_sample(
    tmp_path: Path,
) -> None:
    requests = (
        _identity_trace_request("sample-a", "A1"),
        _identity_trace_request("sample-b", "B1"),
        _identity_trace_request("sample-a", "A2"),
    )
    captured_jobs = []

    def fake_runner(jobs, *, max_workers):
        captured_jobs.extend(jobs)
        for job in jobs:
            pickle.loads(pickle.dumps(job))
        assert max_workers == 8
        return [
            IdentityTraceSampleResult(
                sample_index=job.sample_index,
                sample_stem=job.sample_stem,
                indexed_results=tuple(
                    (
                        index,
                        IdentityCoherenceTraceResult(
                            request=request,
                            trace=None,
                            status="blocked_infrastructure",
                            blocked_reason="test_only",
                        ),
                    )
                    for index, request in job.requests
                ),
            )
            for job in jobs
        ]

    run_identity_trace_process(
        requests,
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        max_workers=8,
        raw_xic_batch_size=64,
        runner=fake_runner,
    )

    assert [job.sample_stem for job in captured_jobs] == ["sample-a", "sample-b"]
    assert [job.raw_xic_batch_size for job in captured_jobs] == [64, 64]
    assert [index for index, _ in captured_jobs[0].requests] == [0, 2]
    assert [index for index, _ in captured_jobs[1].requests] == [1]


def test_run_identity_trace_process_preserves_request_order(tmp_path: Path) -> None:
    requests = (
        _identity_trace_request("sample-a", "A1"),
        _identity_trace_request("sample-b", "B1"),
    )

    def fake_runner(jobs, *, max_workers):
        results = []
        for job in reversed(tuple(jobs)):
            results.append(
                IdentityTraceSampleResult(
                    sample_index=job.sample_index,
                    sample_stem=job.sample_stem,
                    indexed_results=tuple(
                        (
                            index,
                            IdentityCoherenceTraceResult(
                                request=request,
                                trace=None,
                                status="blocked_infrastructure",
                                blocked_reason="test_only",
                            ),
                        )
                        for index, request in job.requests
                    ),
                )
            )
        return results

    output = run_identity_trace_process(
        requests,
        raw_paths={
            "sample-a": tmp_path / "sample-a.raw",
            "sample-b": tmp_path / "sample-b.raw",
        },
        dll_dir=tmp_path / "dll",
        max_workers=8,
        raw_xic_batch_size=64,
        runner=fake_runner,
    )

    assert [result.request.candidate_id for result in output.results] == [
        "A1",
        "B1",
    ]


def test_identity_trace_worker_uses_xic_request_shape(
    monkeypatch,
    tmp_path: Path,
) -> None:
    request = _identity_trace_request("sample-a", "A1")
    seen = {}

    class RawContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def extract_xic_many(self, requests):
            seen["requests"] = tuple(requests)
            assert all(isinstance(item, XICRequest) for item in requests)
            return tuple(
                XICTrace.from_arrays([5.0, 5.1], [0.0, 10.0]) for _ in requests
            )

    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda raw_path, dll_dir: RawContext(),
    )

    result = extract_identity_trace_sample_job(
        IdentityTraceSampleJob(
            sample_index=1,
            sample_stem="sample-a",
            raw_path=tmp_path / "sample-a.raw",
            dll_dir=tmp_path / "dll",
            requests=((0, request),),
            raw_xic_batch_size=64,
        )
    )

    assert seen["requests"][0].mz == request.precursor_mz
    assert result.indexed_results[0][1].status == "pass"


def test_identity_trace_process_returns_blocked_results_on_extraction_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    requests = (
        _identity_trace_request("sample-a", "A1"),
        _identity_trace_request("sample-a", "A2"),
    )

    class PartiallyBrokenRawContext:
        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def extract_xic_many(self, requests):
            self.calls += 1
            if self.calls == 1:
                raise OSError("chunk unavailable")
            return tuple(
                XICTrace.from_arrays([5.0, 5.1], [0.0, 10.0]) for _ in requests
            )

    monkeypatch.setattr(
        "xic_extractor.raw_reader.open_raw",
        lambda raw_path, dll_dir: PartiallyBrokenRawContext(),
    )

    result = extract_identity_trace_sample_job(
        IdentityTraceSampleJob(
            sample_index=1,
            sample_stem="sample-a",
            raw_path=tmp_path / "sample-a.raw",
            dll_dir=tmp_path / "dll",
            requests=tuple(enumerate(requests)),
            raw_xic_batch_size=1,
        )
    )

    first = result.indexed_results[0][1]
    second = result.indexed_results[1][1]
    assert first.status == "blocked_infrastructure"
    assert first.blocked_reason == "raw_xic_extraction_error"
    assert second.status == "pass"
