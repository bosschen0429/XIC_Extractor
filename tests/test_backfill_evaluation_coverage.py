from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest

from tests.test_alignment_owner_backfill import _feature, _peak_config
from tests.test_alignment_owner_clustering import _owner
from tests.test_owner_backfill_request_equivalence import _OwnerBoundBatchSource
from xic_extractor.alignment.backfill.evaluation_coverage import (
    OWNER_BACKFILL_CANDIDATE_PHASES,
    OWNER_BACKFILL_PLANNING_REASONS,
    OwnerBackfillEvaluationCoverageContext,
    build_owner_backfill_evaluation_coverage,
    mint_owner_backfill_attempt_terminal,
)
from xic_extractor.alignment.backfill_scope import select_backfill_features
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.owner_backfill import build_owner_backfill_result
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
)
from xic_extractor.alignment.process_backend import run_owner_backfill_process
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    canonical_identity_basis_bytes,
    compose_processing_owner_references,
    issue_processing_attempt,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)
from xic_extractor.xic_models import XICRequest, XICTrace


class _DenseOwnerBoundBatchSource(_OwnerBoundBatchSource):
    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        self.batch_sizes.append(len(requests))
        traces = []
        for request in requests:
            center = (request.rt_min + request.rt_max) / 2.0
            rt = np.linspace(center - 0.2, center + 0.2, 61)
            intensity = 1000.0 * np.exp(-(((rt - center) / 0.04) ** 2))
            traces.append(XICTrace.from_arrays(rt, intensity))
        return tuple(traces)


class _FailingOwnerBoundBatchSource(_OwnerBoundBatchSource):
    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        self.batch_sizes.append(len(requests))
        raise OSError("synthetic unavailable source")


class _ZeroOwnerBoundBatchSource(_OwnerBoundBatchSource):
    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        self.batch_sizes.append(len(requests))
        return tuple(
            XICTrace.from_arrays(
                np.linspace(request.rt_min, request.rt_max, 61),
                np.zeros(61),
            )
            for request in requests
        )


@dataclass(frozen=True, slots=True)
class _CoverageFixture:
    features: tuple[object, ...]
    context: OwnerBackfillEvaluationCoverageContext
    sources: dict[str, _OwnerBoundBatchSource]
    acquisitions: dict[str, AcquisitionSourceIdentity]


def test_serial_coverage_closes_population_and_adds_no_xic_work(
    tmp_path: Path,
) -> None:
    fixture = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    result = _run(fixture, with_coverage=True)

    coverage = result.evaluation_coverage
    assert coverage is not None
    assert len(coverage.epoch.positions) == 2
    assert [position.planning_reason for position in coverage.epoch.positions] == [
        "retained_local_owner",
        "planned",
    ]
    assert len(coverage.terminals) == 1
    terminal = coverage.terminals[0]
    assert terminal.candidate_phase == "primary_query"
    assert terminal.trace_evaluated is True
    assert terminal.physical_evaluation is not None
    assert terminal.selected_for_output is True
    assert fixture.sources["sample-a"].batch_sizes == []
    calls_with_coverage = tuple(fixture.sources["sample-b"].batch_sizes)

    baseline = _coverage_fixture(
        tmp_path / "baseline",
        sample_order=("sample-a", "sample-b"),
    )
    _run(baseline, with_coverage=False)
    assert tuple(baseline.sources["sample-b"].batch_sizes) == calls_with_coverage


def test_epoch_preserves_one_terminal_per_logical_request_under_xic_reuse(
    tmp_path: Path,
) -> None:
    fixture = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    feature = replace(
        fixture.features[0],
        backfill_seed_centers=((500.0, 8.5), (500.0, 8.5)),
    )
    fixture = replace(
        fixture,
        features=(feature,),
        context=replace(fixture.context, input_features=(feature,)),
    )

    coverage = _run(fixture, with_coverage=True).evaluation_coverage

    assert coverage is not None
    planned = coverage.epoch.positions[1]
    assert len(planned.primary_logical_requests) == 2
    assert len(coverage.terminals) == 2
    logical_request_ids = {
        item.logical_request.logical_request_id for item in coverage.terminals
    }
    assert len(logical_request_ids) == 2
    assert fixture.sources["sample-b"].batch_sizes == [1]


def test_process_carrier_reassembles_serial_coverage_in_canonical_order(
    tmp_path: Path,
) -> None:
    fixture = _coverage_fixture(
        tmp_path,
        sample_order=("sample-a", "sample-b", "sample-c"),
    )
    serial = _run(fixture, with_coverage=True)
    assert serial.evaluation_coverage is not None

    def reverse_runner(
        jobs,
        *,
        max_workers,
        progress_callback,
        success_result_delivery,
    ):
        assert max_workers == 2
        assert progress_callback is None
        receipts = []
        for ordinal, job in reversed(tuple(enumerate(jobs))):
            worker = build_owner_backfill_result(
                job.features,
                sample_order=(job.sample_stem,),
                raw_sources={job.sample_stem: fixture.sources[job.sample_stem]},
                expected_acquisition_sources={
                    job.sample_stem: fixture.acquisitions[job.sample_stem]
                },
                alignment_config=job.alignment_config,
                peak_config=job.peak_config,
                signal_processing_profile=job.signal_processing_profile,
                candidate_index_offset=job.sample_projection.logical_request_offset,
                validation_candidate_index_offset=(
                    job.sample_projection.validation_request_offset
                ),
                evaluation_coverage_positions=job.evaluation_coverage_positions,
            )
            receipts.append(
                success_result_delivery(
                    ordinal,
                    OwnerBackfillSampleResult(
                        job.sample_index,
                        job.sample_stem,
                        worker.cells,
                        evaluation_coverage_terminals=(
                            worker.evaluation_coverage_terminals
                        ),
                    ),
                )
            )
        return receipts

    process_output = run_owner_backfill_process(
        fixture.features,
        sample_order=("sample-a", "sample-b", "sample-c"),
        raw_paths={
            sample: tmp_path / f"{sample}.raw"
            for sample in ("sample-a", "sample-b", "sample-c")
        },
        dll_dir=tmp_path / "dll",
        alignment_config=fixture.context.alignment_config,
        peak_config=_peak_config(),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        max_workers=2,
        expected_acquisition_sources=fixture.acquisitions,
        matrix_sample_order=("sample-a", "sample-b", "sample-c"),
        evaluation_coverage_context=fixture.context,
        runner=reverse_runner,
    )

    process_coverage = process_output.evaluation_coverage
    assert process_coverage == serial.evaluation_coverage


def test_hybrid_validation_unavailable_remains_a_typed_planned_terminal(
    tmp_path: Path,
) -> None:
    fixture = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    fixture = replace(
        fixture,
        context=replace(fixture.context, validation_mode="prefilter"),
    )
    fixture.sources["sample-b"] = _DenseOwnerBoundBatchSource(
        fixture.acquisitions["sample-b"],
    )

    result = build_owner_backfill_result(
        fixture.features,
        sample_order=fixture.context.sample_order,
        raw_sources=fixture.sources,
        validation_raw_sources={},
        expected_acquisition_sources=fixture.acquisitions,
        alignment_config=fixture.context.alignment_config,
        peak_config=_peak_config(),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        evaluation_coverage_context=fixture.context,
    )

    coverage = result.evaluation_coverage
    assert coverage is not None
    assert [item.candidate_phase for item in coverage.terminals] == [
        "prefilter_query",
        "validation_unavailable",
    ]
    unavailable = coverage.terminals[1]
    assert unavailable.trace_evaluated is False
    assert unavailable.physical_evaluation is None
    assert unavailable.terminal_reason == "validation_source_unavailable"
    planned = coverage.epoch.positions[1]
    assert planned.planning_state == "planned"
    with pytest.raises(ValueError, match="missing or adds a validation"):
        build_owner_backfill_evaluation_coverage(
            coverage.epoch,
            coverage.terminals[:-1],
        )


@pytest.mark.parametrize(
    ("validation_source", "expected_phase", "evaluated"),
    (
        ("dense", "validation_query", True),
        ("failing", "validation_fallback", False),
    ),
)
def test_hybrid_validation_retains_query_and_fallback_terminals(
    tmp_path: Path,
    validation_source: str,
    expected_phase: str,
    evaluated: bool,
) -> None:
    fixture = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    fixture = replace(
        fixture,
        context=replace(fixture.context, validation_mode="prefilter"),
    )
    fixture.sources["sample-b"] = _DenseOwnerBoundBatchSource(
        fixture.acquisitions["sample-b"]
    )
    secondary = (
        _DenseOwnerBoundBatchSource(fixture.acquisitions["sample-b"])
        if validation_source == "dense"
        else _FailingOwnerBoundBatchSource(fixture.acquisitions["sample-b"])
    )

    result = build_owner_backfill_result(
        fixture.features,
        sample_order=fixture.context.sample_order,
        raw_sources=fixture.sources,
        validation_raw_sources={"sample-b": secondary},
        expected_acquisition_sources=fixture.acquisitions,
        alignment_config=fixture.context.alignment_config,
        peak_config=_peak_config(),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        evaluation_coverage_context=fixture.context,
    )

    coverage = result.evaluation_coverage
    assert coverage is not None
    assert [item.candidate_phase for item in coverage.terminals] == [
        "prefilter_query",
        expected_phase,
    ]
    validation = coverage.terminals[1]
    assert validation.trace_evaluated is evaluated
    assert (validation.physical_evaluation is not None) is evaluated
    if expected_phase == "validation_fallback":
        assert validation.terminal_reason == "validation_unassessable"


def test_hybrid_superwindow_validation_retains_raw_exact_attempt_semantics(
    tmp_path: Path,
) -> None:
    fixture = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    fixture = replace(
        fixture,
        context=replace(
            fixture.context,
            backend="ms1_index_hybrid",
            window_strategy="super-window",
            validation_mode="prefilter",
        ),
    )
    fixture.sources["sample-b"] = _DenseOwnerBoundBatchSource(
        fixture.acquisitions["sample-b"]
    )
    secondary = _DenseOwnerBoundBatchSource(fixture.acquisitions["sample-b"])

    result = build_owner_backfill_result(
        fixture.features,
        sample_order=fixture.context.sample_order,
        raw_sources=fixture.sources,
        validation_raw_sources={"sample-b": secondary},
        expected_acquisition_sources=fixture.acquisitions,
        alignment_config=fixture.context.alignment_config,
        peak_config=_peak_config(),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        owner_backfill_xic_backend="ms1_index_hybrid",
        owner_backfill_window_strategy="super-window",
        evaluation_coverage_context=fixture.context,
    )

    coverage = result.evaluation_coverage
    assert coverage is not None
    validation = next(
        item
        for item in coverage.terminals
        if item.candidate_phase == "validation_query"
    )
    assert validation.request_identity.backend == "raw"
    assert validation.request_identity.window_strategy == "exact"


def test_validation_attempts_reject_rebound_equivalent_logical_member(
    tmp_path: Path,
) -> None:
    fixture = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    feature = replace(
        fixture.features[0],
        backfill_seed_centers=((500.0, 8.5), (500.0, 8.5)),
    )
    fixture = replace(
        fixture,
        features=(feature,),
        context=replace(
            fixture.context,
            input_features=(feature,),
            validation_mode="prefilter",
        ),
    )
    fixture.sources["sample-b"] = _DenseOwnerBoundBatchSource(
        fixture.acquisitions["sample-b"]
    )
    secondary = _DenseOwnerBoundBatchSource(fixture.acquisitions["sample-b"])
    result = build_owner_backfill_result(
        fixture.features,
        sample_order=fixture.context.sample_order,
        raw_sources=fixture.sources,
        validation_raw_sources={"sample-b": secondary},
        expected_acquisition_sources=fixture.acquisitions,
        alignment_config=fixture.context.alignment_config,
        peak_config=_peak_config(),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        evaluation_coverage_context=fixture.context,
    )
    coverage = result.evaluation_coverage
    assert coverage is not None
    validations = tuple(
        item
        for item in coverage.terminals
        if item.candidate_phase == "validation_query"
    )
    assert len(validations) == 2
    first, second = validations
    rebound = mint_owner_backfill_attempt_terminal(
        position_id=second.position_id,
        logical_request=first.logical_request,
        request_identity=second.request_identity,
        candidate_phase=second.candidate_phase,
        candidate_index=second.candidate_index,
        trace_evaluated=second.trace_evaluated,
        selected_for_output=second.selected_for_output,
        terminal_reason=second.terminal_reason,
        physical_evaluation=second.physical_evaluation,
    )
    candidate = tuple(
        rebound if item.terminal_id == second.terminal_id else item
        for item in coverage.terminals
    )

    with pytest.raises(ValueError, match="missing or adds a validation"):
        build_owner_backfill_evaluation_coverage(coverage.epoch, candidate)


def test_primary_fallback_and_evaluated_no_signal_remain_distinct(
    tmp_path: Path,
) -> None:
    failing = _coverage_fixture(
        tmp_path / "failing",
        sample_order=("sample-a", "sample-b"),
    )
    failing.sources["sample-b"] = _FailingOwnerBoundBatchSource(
        failing.acquisitions["sample-b"]
    )
    no_signal = _coverage_fixture(
        tmp_path / "no-signal",
        sample_order=("sample-a", "sample-b"),
    )
    no_signal.sources["sample-b"] = _ZeroOwnerBoundBatchSource(
        no_signal.acquisitions["sample-b"]
    )

    fallback_coverage = _run(failing, with_coverage=True).evaluation_coverage
    no_signal_coverage = _run(no_signal, with_coverage=True).evaluation_coverage

    assert fallback_coverage is not None
    fallback = fallback_coverage.terminals[0]
    assert fallback.candidate_phase == "primary_fallback"
    assert fallback.trace_evaluated is False
    assert fallback.terminal_reason == "primary_unassessable"
    assert fallback.physical_evaluation is None
    assert fallback.selected_for_output is True
    assert no_signal_coverage is not None
    evaluated = no_signal_coverage.terminals[0]
    assert evaluated.candidate_phase == "primary_query"
    assert evaluated.trace_evaluated is True
    assert evaluated.physical_evaluation is not None
    assert evaluated.physical_evaluation.state == "unavailable"
    assert evaluated.physical_evaluation.reason == "no_signal_above_baseline"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing", "missing or adds"),
        ("duplicate", "duplicates"),
        ("crossed", "crosses its canonical sample"),
    ),
)
def test_coverage_rejects_missing_duplicate_and_crossed_terminals(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    fixture = _coverage_fixture(
        tmp_path,
        sample_order=("sample-a", "sample-b", "sample-c"),
    )
    coverage = _run(fixture, with_coverage=True).evaluation_coverage
    assert coverage is not None
    terminals = coverage.terminals
    if mutation == "missing":
        candidate = terminals[:-1]
    elif mutation == "duplicate":
        candidate = (*terminals, terminals[0])
    else:
        first, second = terminals
        candidate = (
            mint_owner_backfill_attempt_terminal(
                position_id=second.position_id,
                logical_request=first.logical_request,
                request_identity=first.request_identity,
                candidate_phase=first.candidate_phase,
                candidate_index=first.candidate_index,
                trace_evaluated=first.trace_evaluated,
                selected_for_output=first.selected_for_output,
                terminal_reason=first.terminal_reason,
                physical_evaluation=first.physical_evaluation,
            ),
            second,
        )

    with pytest.raises(ValueError, match=message):
        build_owner_backfill_evaluation_coverage(coverage.epoch, candidate)


def test_terminal_rejects_crossed_phase_index_and_physical_evaluation(
    tmp_path: Path,
) -> None:
    fixture = _coverage_fixture(
        tmp_path,
        sample_order=("sample-a", "sample-b", "sample-c"),
    )
    coverage = _run(fixture, with_coverage=True).evaluation_coverage
    assert coverage is not None
    first, second = coverage.terminals
    assert second.physical_evaluation is not None

    with pytest.raises(ValueError, match="crossed its physical evaluation"):
        mint_owner_backfill_attempt_terminal(
            position_id=first.position_id,
            logical_request=first.logical_request,
            request_identity=first.request_identity,
            candidate_phase=first.candidate_phase,
            candidate_index=first.candidate_index,
            trace_evaluated=True,
            selected_for_output=first.selected_for_output,
            terminal_reason=None,
            physical_evaluation=second.physical_evaluation,
        )
    with pytest.raises(ValueError, match="crossed its physical evaluation"):
        mint_owner_backfill_attempt_terminal(
            position_id=first.position_id,
            logical_request=first.logical_request,
            request_identity=first.request_identity,
            candidate_phase="prefilter_query",
            candidate_index=first.candidate_index + 1,
            trace_evaluated=True,
            selected_for_output=first.selected_for_output,
            terminal_reason=None,
            physical_evaluation=first.physical_evaluation,
        )


def test_closed_vocabularies_cover_every_emitted_disposition_and_phase(
    tmp_path: Path,
) -> None:
    fixture = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    coverage = _run(fixture, with_coverage=True).evaluation_coverage
    assert coverage is not None

    assert set(OWNER_BACKFILL_PLANNING_REASONS) == {
        "planned",
        "production_equivalent_excluded",
        "selected_family_excluded",
        "review_only",
        "insufficient_detected_samples",
        "raw_source_unavailable",
        "retained_local_owner",
        "retained_unresolved_candidates",
        "local_owner_not_supersedable",
        "direct_support_threshold_not_met",
        "recovery_search_completed_empty",
        "no_discovery_input",
    }
    assert set(OWNER_BACKFILL_CANDIDATE_PHASES) == {
        "primary_query",
        "prefilter_query",
        "primary_fallback",
        "validation_query",
        "validation_unavailable",
        "validation_fallback",
    }
    assert {item.planning_reason for item in coverage.epoch.positions} <= set(
        OWNER_BACKFILL_PLANNING_REASONS
    )
    assert {item.candidate_phase for item in coverage.terminals} <= set(
        OWNER_BACKFILL_CANDIDATE_PHASES
    )


def test_epoch_identity_seals_planning_inputs_even_when_dispositions_match(
    tmp_path: Path,
) -> None:
    first = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    changed_config = replace(first.context.alignment_config, max_ppm=55.0)
    second = replace(
        first,
        context=replace(first.context, alignment_config=changed_config),
    )
    changed_backend = replace(
        first,
        context=replace(first.context, backend="ms1_index"),
    )
    changed_group = replace(first.features[0], disposition="unresolved")
    changed_disposition = replace(
        first,
        features=(changed_group,),
        context=replace(first.context, input_features=(changed_group,)),
    )

    first_coverage = _run(first, with_coverage=True).evaluation_coverage
    second_coverage = _run(second, with_coverage=True).evaluation_coverage
    changed_backend_coverage = _run(
        changed_backend,
        with_coverage=True,
    ).evaluation_coverage
    changed_disposition_coverage = _run(
        changed_disposition,
        with_coverage=True,
    ).evaluation_coverage

    assert first_coverage is not None and second_coverage is not None
    assert changed_backend_coverage is not None
    assert changed_disposition_coverage is not None
    assert [item.planning_reason for item in first_coverage.epoch.positions] == [
        item.planning_reason for item in second_coverage.epoch.positions
    ]
    assert first_coverage.epoch.epoch_id != second_coverage.epoch.epoch_id
    assert first_coverage.coverage_id != second_coverage.coverage_id
    assert first_coverage.epoch.epoch_id != changed_backend_coverage.epoch.epoch_id
    assert first_coverage.epoch.epoch_id != changed_disposition_coverage.epoch.epoch_id


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("production", "production_equivalent_excluded"),
        ("selected", "selected_family_excluded"),
        ("review", "review_only"),
        ("insufficient", "insufficient_detected_samples"),
        ("raw", "raw_source_unavailable"),
        ("retained", "retained_local_owner"),
        ("nonsupersedable", "local_owner_not_supersedable"),
        ("planned", "planned"),
    ),
)
def test_planning_epoch_emits_every_closed_first_match_disposition(
    tmp_path: Path,
    case: str,
    expected: str,
) -> None:
    fixture = _coverage_fixture(tmp_path, sample_order=("sample-a", "sample-b"))
    feature = fixture.features[0]
    context = fixture.context
    sources = fixture.sources
    if case == "production":
        context = replace(context, scope="production-equivalent")
    elif case == "selected":
        context = replace(
            context,
            scope="selected-families",
            selected_family_ids=frozenset({"not-this-family"}),
        )
    elif case == "review":
        feature = replace(feature, review_only=True, identity_conflict=True)
    elif case == "insufficient":
        context = replace(
            context,
            alignment_config=AlignmentConfig(owner_backfill_min_detected_samples=2),
        )
    elif case == "raw":
        context = replace(context, raw_sample_stems=frozenset({"sample-a"}))
        sources = {"sample-a": sources["sample-a"]}
    elif case == "nonsupersedable":
        feature = replace(feature, confirm_local_owners_with_backfill=True)
    if feature is not fixture.features[0]:
        context = replace(context, input_features=(feature,))
    fixture = replace(fixture, features=(feature,), context=context, sources=sources)

    coverage = _run(fixture, with_coverage=True).evaluation_coverage

    assert coverage is not None
    position = next(
        item
        for item in coverage.epoch.positions
        if item.sample.lookup_token
        == ("sample-a" if case in {"retained", "nonsupersedable"} else "sample-b")
    )
    assert position.planning_reason == expected
    expected_state = "planned" if expected == "planned" else "not_planned"
    assert position.planning_state == expected_state
    assert len(coverage.epoch.positions) == 2


def _run(fixture: _CoverageFixture, *, with_coverage: bool):
    selected_features = select_backfill_features(
        fixture.context.input_features,
        sample_order=fixture.context.sample_order,
        raw_sample_stems=fixture.context.raw_sample_stems,
        alignment_config=fixture.context.alignment_config,
        scope=fixture.context.scope,
        selected_family_ids=fixture.context.selected_family_ids,
    ).features
    return build_owner_backfill_result(
        selected_features,
        sample_order=fixture.context.sample_order,
        raw_sources=fixture.sources,
        expected_acquisition_sources=fixture.acquisitions,
        alignment_config=fixture.context.alignment_config,
        peak_config=_peak_config(),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        owner_backfill_xic_backend=fixture.context.backend,
        owner_backfill_window_strategy=fixture.context.window_strategy,
        owner_backfill_superwindow_span_factor=(
            fixture.context.superwindow_span_factor
        ),
        evaluation_coverage_context=fixture.context if with_coverage else None,
    )


def _coverage_fixture(
    tmp_path: Path,
    *,
    sample_order: tuple[str, ...],
) -> _CoverageFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    acquisitions: dict[str, AcquisitionSourceIdentity] = {}
    sources: dict[str, _OwnerBoundBatchSource] = {}
    bindings: list[AcquisitionInputBinding] = []
    metadata: list[SampleMetadata] = []
    for index, sample in enumerate(sample_order, start=1):
        raw_path = tmp_path / f"{sample}.raw"
        raw_path.write_bytes(f"coverage:{sample}".encode())
        acquisition = build_acquisition_source_identity(
            raw_path,
            acquisition_format="thermo_raw",
            observation_namespace="test-backfill-coverage",
        )
        acquisitions[sample] = acquisition
        sources[sample] = _OwnerBoundBatchSource(acquisition)
        bindings.append(
            AcquisitionInputBinding(
                acquisition_source_id=acquisition.acquisition_source_id,
                adapter_input_key=sample,
                injection_order=index,
            )
        )
        metadata.append(
            SampleMetadata(
                sample_name=sample,
                raw_stem=sample,
                injection_order=index,
                sample_role="study_sample",
            )
        )
    request = build_processing_request_identity(
        acquisition_sources=tuple(acquisitions.values()),
        acquisition_bindings=tuple(bindings),
        scientific_input_schema_version="backfill_coverage_test",
        processing_algorithm_version="backfill_coverage_test",
        processing_contract_version="processing_identity",
        scientific_inputs={"fixture": "backfill-evaluation-coverage"},
    )
    snapshot = mint_sample_metadata_snapshot(
        tuple(metadata),
        identity_namespace="study:backfill-evaluation-coverage",
    )
    resolved: tuple[ResolvedCanonicalSampleIdentity, ...] = tuple(
        resolve_canonical_sample_identity(snapshot, sample) for sample in sample_order
    )
    processing_owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=snapshot,
        sample_resolutions=resolved,
    )
    owner = _owner("sample-a", "coverage", apex_rt=8.5)
    old_subject = owner.cross_sample_member_subject
    assert old_subject is not None
    member_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": request.processing_request_id,
            "canonical_sample_identity_id": resolved[0].identity.sample_identity_id,
            "subject": {
                "kind": "physical_observation",
                "id": old_subject.subject_id,
            },
            "physical_observation_ids": [old_subject.subject_id],
        }
    )
    member = CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(
            "xic.group-member-subject:",
            member_basis,
        ),
        processing_request_id=request.processing_request_id,
        canonical_sample_identity_id=resolved[0].identity.sample_identity_id,
        subject_kind="physical_observation",
        subject_id=old_subject.subject_id,
        physical_observation_ids=(old_subject.subject_id,),
        basis_bytes=member_basis,
    )
    group = mint_cross_sample_group_identity(
        processing_request_id=request.processing_request_id,
        member_subjects=(member,),
    )
    owner = replace(owner, cross_sample_member_subject=member)
    feature = replace(
        _feature(),
        owners=(owner,),
        group_hypothesis_id=group.group_hypothesis_id,
        processing_request_id=group.processing_request_id,
        member_subjects=group.member_subjects,
        identity_basis_bytes=group.basis_bytes,
        disposition=group.disposition,
    )
    config = AlignmentConfig(owner_backfill_min_detected_samples=1)
    context = OwnerBackfillEvaluationCoverageContext(
        processing_owners=processing_owners,
        input_features=(feature,),
        samples=resolved,
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=config,
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=2,
        validation_mode="primary",
        result_semantics=OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
        scope="full-audit",
        selected_family_ids=frozenset(),
    )
    return _CoverageFixture((feature,), context, sources, acquisitions)
