"""Focused contract tests for ObservationRepair execution and receipts."""

from __future__ import annotations

from copy import copy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import xic_extractor.alignment.backfill.observation_repair as observation_repair_owner
from tests.cid_nl_peak_support_no_raw_fixture import cid_nl_peak
from tests.test_alignment_owner_backfill import _peak_config
from tests.test_backfill_observation_repair_authorization import (
    _authorization_population,
    _population_cells,
)
from tests.test_cell_observations import _not_evaluated_assessment, _source_cell
from tests.test_cid_nl_evidence_spine import _completed_receipt
from tests.test_cross_sample_association_lineage import _runtime_feature
from tests.test_owner_backfill_peak_fact_provenance import (
    _CanonicalOwnerBoundSource,
)
from xic_extractor.alignment.backfill import (
    ObservationRepairCandidate,
    ObservationRepairContractError,
    ObservationRepairMultipleEligibleAuthorization,
    ObservationRepairNotRunAuthorization,
    ObservationRepairRepairAuthorization,
    ObservationRepairTerminalResult,
    authorize_observation_repair,
    close_observation_repair,
    execute_observation_repair,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.owner_backfill import build_owner_backfill_result
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)


def test_candidate_receipt_requires_exact_authorization_and_is_membership_neutral(
    tmp_path: Path,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    authorization = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
    )
    assert isinstance(authorization, ObservationRepairRepairAuthorization)
    candidate = authorization.candidate
    sample = fixture.sample("s2")
    built, result = _repair_result(fixture, coverage, candidate)

    with pytest.raises(ObservationRepairContractError, match="exact current"):
        close_observation_repair(candidate, result)
    receipt = close_observation_repair(
        candidate,
        result,
        authorization=authorization,
        coverage=coverage,
        cell_observations=cells,
    )

    assert receipt.receipt_id.startswith("xic.observation-repair-receipt:")
    assert receipt.subject is candidate
    assert receipt.authorization_id == authorization.authorization_id
    assert receipt.candidate_id == candidate.candidate_id
    assert receipt.outcome == "repaired"
    assert receipt.evidence is not None
    assert receipt.evidence.member_subject_id is None
    assert receipt.evidence.work_unit_key
    assert receipt.evidence.selected_integration_authority_content_id
    assert receipt.membership_authority == "none"
    assert receipt.value_authority == "none"
    assert receipt.matrix_authority == "none"
    assert receipt.activation_authority == "none"
    assert receipt.writer_authority == "none"
    assert receipt.repair_epoch == candidate.repair_epoch == 1
    assert (
        sample.identity.sample_identity_id
        == candidate.cell.content.canonical_sample_identity_id
    )
    assert spine.group is not None

    unselected = copy(built.peak_fact_observations[0])
    object.__setattr__(unselected, "selected_for_output", False)
    no_repair = close_observation_repair(
        candidate,
        replace(result, peak_fact_observations=(unselected,)),
        authorization=authorization,
        coverage=coverage,
        cell_observations=cells,
    )
    assert no_repair.subject is candidate
    assert no_repair.authorization_id == authorization.authorization_id
    assert no_repair.candidate_id == candidate.candidate_id
    assert no_repair.outcome == "no_repair"
    assert no_repair.evidence is None
    assert no_repair.no_repair_terminal is not None
    assert no_repair.no_repair_terminal.work_unit_key == unselected.work_unit_key
    assert no_repair.no_repair_terminal.physical_answer_id
    _, empty_result = _repair_result(fixture, coverage, candidate, empty=True)
    other_no_repair = close_observation_repair(
        candidate,
        empty_result,
        authorization=authorization,
        coverage=coverage,
        cell_observations=cells,
    )
    assert other_no_repair.outcome == "no_repair"
    assert other_no_repair.no_repair_terminal is not None
    assert other_no_repair.receipt_id != no_repair.receipt_id
    assert (
        other_no_repair.no_repair_terminal.physical_answer_id
        != no_repair.no_repair_terminal.physical_answer_id
    )
    with pytest.raises(ObservationRepairContractError, match="exact terminal"):
        replace(
            no_repair,
            no_repair_terminal=None,
            _mint_token=observation_repair_owner._RECEIPT_MINT_TOKEN,
        )


def test_old_authorization_cannot_close_alternate_candidate(tmp_path: Path) -> None:
    first_fixture, _spine, first_coverage, first_cells = _authorization_population(
        tmp_path / "first",
        hold_samples=("s2",),
    )
    second_fixture, _spine, second_coverage, second_cells = _authorization_population(
        tmp_path / "second",
        hold_samples=("s4",),
    )
    first = authorize_observation_repair(
        processing_owners=first_fixture.owners,
        coverage=first_coverage,
        cell_observations=first_cells,
    )
    second = authorize_observation_repair(
        processing_owners=second_fixture.owners,
        coverage=second_coverage,
        cell_observations=second_cells,
    )
    assert isinstance(first, ObservationRepairRepairAuthorization)
    assert isinstance(second, ObservationRepairRepairAuthorization)
    with pytest.raises(ObservationRepairContractError, match="exact authorized"):
        close_observation_repair(
            first.candidate,
            _candidate_result_stub(first),
            authorization=second,
            coverage=second_coverage,
            cell_observations=second_cells,
        )


def test_replayed_authorization_cannot_close_after_population_changes(
    tmp_path: Path,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    current_cells = _population_cells(
        fixture,
        spine,
        hold_samples=("s2", "s4"),
        suffix="current",
    )
    old = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
    )
    current = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=current_cells,
    )
    assert isinstance(old, ObservationRepairRepairAuthorization)
    assert isinstance(current, ObservationRepairMultipleEligibleAuthorization)
    with pytest.raises(ObservationRepairContractError, match="complete current"):
        close_observation_repair(
            old,
            _candidate_result_stub(old),
        )
    with pytest.raises(ObservationRepairContractError, match="current single"):
        close_observation_repair(
            old,
            _candidate_result_stub(old),
            authorization=old,
            coverage=coverage,
            cell_observations=current_cells,
        )


def test_direct_old_authorization_cannot_close_alternate_current_population(
    tmp_path: Path,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    current_cells = _population_cells(
        fixture,
        spine,
        hold_samples=("s4",),
        suffix="current-alternate",
    )
    old = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
    )
    assert isinstance(old, ObservationRepairRepairAuthorization)
    _built, result = _repair_result(fixture, coverage, old.candidate)

    with pytest.raises(ObservationRepairContractError, match="not current"):
        close_observation_repair(
            old,
            result,
            authorization=old,
            coverage=coverage,
            cell_observations=current_cells,
        )


def test_authorization_and_terminal_result_are_directly_sealed(tmp_path: Path) -> None:
    authorizations = []
    for index, hold_samples in enumerate(((), ("s2",), ("s2", "s4"))):
        fixture, _spine, coverage, cells = _authorization_population(
            tmp_path / str(index),
            hold_samples=hold_samples,
        )
        authorizations.append(
            authorize_observation_repair(
                processing_owners=fixture.owners,
                coverage=coverage,
                cell_observations=cells,
            )
        )

    not_run, authorization, multiple = authorizations
    assert isinstance(not_run, ObservationRepairNotRunAuthorization)
    assert isinstance(authorization, ObservationRepairRepairAuthorization)
    assert isinstance(multiple, ObservationRepairMultipleEligibleAuthorization)
    for variant in authorizations:
        with pytest.raises(ObservationRepairContractError, match="minted by Backfill"):
            replace(variant, _mint_token=object())
    with pytest.raises(ObservationRepairContractError, match="minted by Backfill"):
        replace(authorization.candidate, _mint_token=object())
    with pytest.raises(ObservationRepairContractError, match="minted by Backfill"):
        ObservationRepairTerminalResult(
            authorization=authorization,
            receipt=None,
            _mint_token=object(),
        )
    with pytest.raises(FrozenInstanceError):
        authorization.population_cell_ids = ()  # type: ignore[misc]


def test_repaired_terminal_result_binds_exact_receipt_content(
    tmp_path: Path,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=lambda candidate: _repair_result(fixture, coverage, candidate)[1],
    )

    assert terminal.state == "repair"
    assert terminal.receipt is not None
    assert terminal.receipt.subject is terminal.authorization.candidate
    assert terminal.receipt.authorization_id == terminal.authorization.authorization_id
    assert (
        terminal.receipt.candidate_id == terminal.authorization.candidate.candidate_id
    )
    assert (
        terminal.receipt.repair_epoch == terminal.authorization.candidate.repair_epoch
    )
    assert terminal.receipt.evidence is not None
    assert terminal.receipt.evidence.work_unit_key
    assert terminal.receipt.evidence.fact_content_id

    with pytest.raises(ObservationRepairContractError, match="exact receipt"):
        ObservationRepairTerminalResult(
            authorization=terminal.authorization,
            receipt=None,
            _mint_token=observation_repair_owner._TERMINAL_RESULT_MINT_TOKEN,
        )


def test_terminal_result_rejects_crossed_receipt_content(tmp_path: Path) -> None:
    first_fixture, _first_spine, first_coverage, first_cells = (
        _authorization_population(tmp_path / "first", hold_samples=("s2",))
    )
    second_fixture, _second_spine, second_coverage, second_cells = (
        _authorization_population(tmp_path / "second", hold_samples=("s4",))
    )

    first_terminal = execute_observation_repair(
        processing_owners=first_fixture.owners,
        coverage=first_coverage,
        cell_observations=first_cells,
        execute=lambda candidate: _repair_result(
            first_fixture,
            first_coverage,
            candidate,
        )[1],
    )
    second_terminal = execute_observation_repair(
        processing_owners=second_fixture.owners,
        coverage=second_coverage,
        cell_observations=second_cells,
        execute=lambda candidate: _repair_result(
            second_fixture,
            second_coverage,
            candidate,
        )[1],
    )
    assert first_terminal.receipt is not None
    assert second_terminal.receipt is not None

    with pytest.raises(ObservationRepairContractError, match="crosses authorization"):
        ObservationRepairTerminalResult(
            authorization=first_terminal.authorization,
            receipt=second_terminal.receipt,
            _mint_token=observation_repair_owner._TERMINAL_RESULT_MINT_TOKEN,
        )


def test_cardinality_controls_production_execution_call_count(tmp_path: Path) -> None:
    calls: list[str] = []
    observed_calls: list[int] = []
    observed_states: list[str] = []
    cases = (((), 0), (("s2",), 1), (("s2", "s4"), 0))
    for index, (hold_samples, expected_calls) in enumerate(cases):
        fixture, _spine, coverage, cells = _authorization_population(
            tmp_path / str(index),
            hold_samples=hold_samples,
        )

        def execute(candidate: ObservationRepairCandidate) -> OwnerBackfillSampleResult:
            calls.append(candidate.candidate_id)
            _built, result = _repair_result(fixture, coverage, candidate)
            return result

        terminal = execute_observation_repair(
            processing_owners=fixture.owners,
            coverage=coverage,
            cell_observations=cells,
            execute=execute,
        )
        observed_states.append(terminal.state)
        observed_calls.append(len(calls))
        assert len(calls) == sum(expected for _, expected in cases[: index + 1])
        if expected_calls == 0:
            assert terminal.receipt is None
        else:
            assert terminal.receipt is not None
    assert [
        observed_calls[0],
        observed_calls[1] - observed_calls[0],
        observed_calls[2] - observed_calls[1],
    ] == [0, 1, 0]
    assert observed_states == ["not_run", "repair", "multiple_eligible"]


def test_retry_predecessor_group_context_is_accepted_by_owner_lineage(
    tmp_path: Path,
) -> None:
    fixture, spine, coverage, _cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    retry_fixture = fixture.retry()
    retry_coverage = replace(
        coverage,
        epoch=replace(coverage.epoch, processing_owners=retry_fixture.owners),
    )
    retry_cells = _retry_population_cells(
        retry_fixture,
        spine,
        hold_samples=("s2",),
    )

    authorization = authorize_observation_repair(
        processing_owners=retry_fixture.owners,
        coverage=retry_coverage,
        cell_observations=retry_cells,
    )

    assert isinstance(authorization, ObservationRepairRepairAuthorization)
    assert authorization.candidate.cell.group_context.processing_attempt == (
        fixture.attempt
    )
    assert (
        authorization.candidate.cell.artifact.producer_processing_attempt_id
        == retry_fixture.attempt.processing_attempt_id
    )


def _retry_population_cells(
    retry_fixture: object,
    spine: object,
    *,
    hold_samples: tuple[str, ...],
):
    hold_set = set(hold_samples)
    cells = []
    for sample in retry_fixture.samples:
        if sample.lookup_token in hold_set:
            assessment = _hold_assessment(retry_fixture, sample.lookup_token)
        else:
            assessment = _not_evaluated_assessment(
                retry_fixture,
                sample.lookup_token,
                suffix="retry",
            )
        cells.append(_source_cell(retry_fixture, sample, spine, assessment))
    return tuple(cells)


def _hold_assessment(fixture: object, sample_stem: str):
    sample = fixture.sample(sample_stem)
    peak = cid_nl_peak(fixture, sample_stem)
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}:retry",
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
        candidate_peaks=(peak,),
    )


def _repair_result(
    fixture: object,
    coverage: object,
    candidate: ObservationRepairCandidate,
    *,
    empty: bool = False,
):
    sample_stem = candidate.request_identity.sample_stem
    sample = fixture.sample(sample_stem)
    acquisition = next(
        source
        for sample_source, source in zip(
            fixture.samples,
            fixture.request.acquisition_sources,
            strict=True,
        )
        if sample_source.lookup_token == sample_stem
    )
    built = build_owner_backfill_result(
        (_runtime_feature(candidate.predecessor_group),),
        sample_order=(sample_stem,),
        raw_sources={sample_stem: _CanonicalOwnerBoundSource(acquisition, empty=empty)},
        expected_acquisition_sources={sample_stem: acquisition},
        alignment_config=coverage.epoch.alignment_config,
        peak_config=replace(_peak_config(), resolver_min_scans=3),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        evaluation_coverage_positions=(candidate.coverage_position,),
    )
    result = OwnerBackfillSampleResult(
        sample_index=fixture.samples.index(sample),
        sample_stem=sample_stem,
        cells=built.cells,
        peak_fact_observations=built.peak_fact_observations,
    )
    return built, result


def _candidate_result_stub(
    authorization: ObservationRepairRepairAuthorization,
) -> OwnerBackfillSampleResult:
    candidate = authorization.candidate
    return OwnerBackfillSampleResult(
        sample_index=1,
        sample_stem=candidate.request_identity.sample_stem,
        cells=(candidate.cell,),
        peak_fact_observations=(),
    )
