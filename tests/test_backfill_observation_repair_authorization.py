"""Focused contract tests for complete-population repair authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.alignment.backfill.observation_repair as observation_repair_owner
from tests.test_backfill_observation_repair import _repair_fixture, _subject
from tests.test_cell_observations import (
    _not_evaluated_assessment,
    _owner_handoff,
    _source_cell,
)
from tests.test_cid_nl_evidence_spine import _completed_receipt
from xic_extractor.alignment.backfill import (
    ObservationRepairCandidate,
    ObservationRepairContractError,
    ObservationRepairMultipleEligibleAuthorization,
    ObservationRepairNoRepairTerminal,
    ObservationRepairNotRunAuthorization,
    ObservationRepairRepairAuthorization,
    authorize_observation_repair,
    close_observation_repair,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    build_owner_backfill_evaluation_coverage,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment_ref,
)


def test_subject_no_repair_uses_current_complete_receipt_shape(tmp_path: Path) -> None:
    fixture = _repair_fixture(tmp_path, empty=True)

    receipt = close_observation_repair(_subject(fixture), fixture.result)

    assert receipt.outcome == "no_repair"
    assert receipt.evidence is None
    assert receipt.no_repair_terminal is None
    expected_payload = {
        "schema_version": "observation_repair_receipt",
        "repair_scope": "subject",
        "subject_id": receipt.subject.subject_id,
        "repair_epoch": receipt.subject.repair_epoch,
        "outcome": "no_repair",
        "sample_stem": receipt.sample_stem,
        "authorization_id": None,
        "candidate_id": None,
        "evidence": None,
        "no_repair_terminal": None,
    }
    expected_bytes = json.dumps(
        expected_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert receipt.receipt_id == (
        "xic.observation-repair-receipt:"
        + hashlib.sha256(expected_bytes).hexdigest()
    )


def test_subject_receipt_rejects_candidate_terminal(tmp_path: Path) -> None:
    fixture = _repair_fixture(tmp_path, empty=True)
    subject = _subject(fixture)
    evaluation = fixture.observation.physical_evaluation
    assert evaluation is not None

    with pytest.raises(ObservationRepairContractError, match="candidate"):
        ObservationRepairNoRepairTerminal(
            terminal_id=observation_repair_owner._no_repair_terminal_id(
                subject_id=subject.subject_id,
                authorization_id=None,
                candidate_id=None,
                repair_epoch=subject.repair_epoch,
                work_unit_key=fixture.observation.work_unit_key,
                physical_answer_id=evaluation.answer.physical_answer_id,
            ),
            subject_id=subject.subject_id,
            authorization_id=None,
            candidate_id=None,
            repair_epoch=subject.repair_epoch,
            work_unit_key=fixture.observation.work_unit_key,
            physical_answer_id=evaluation.answer.physical_answer_id,
            _mint_token=observation_repair_owner._NO_REPAIR_TERMINAL_MINT_TOKEN,
        )


@pytest.mark.parametrize(
    ("hold_samples", "expected_type", "expected_state"),
    [
        ((), ObservationRepairNotRunAuthorization, "not_run"),
        (("s2",), ObservationRepairRepairAuthorization, "repair"),
        (
            ("s2", "s4"),
            ObservationRepairMultipleEligibleAuthorization,
            "multiple_eligible",
        ),
    ],
)
def test_population_authorization_closes_cardinality_and_ignores_population_order(
    tmp_path: Path,
    hold_samples: tuple[str, ...],
    expected_type: type[object],
    expected_state: str,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=hold_samples,
    )

    authorization = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
    )
    reordered = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=tuple(reversed(cells)),
    )

    assert type(authorization) is expected_type
    assert authorization.state == expected_state
    assert authorization == reordered
    if isinstance(authorization, ObservationRepairRepairAuthorization):
        assert type(authorization.candidate) is ObservationRepairCandidate
        assert authorization.candidate.member_subject is None
        assert authorization.candidate.successor_application is None
        assert authorization.candidate.sample_identity.lookup_token == "s2"
        assert (
            authorization.candidate.sample_identity.identity.sample_identity_id
            not in {
                member.canonical_sample_identity_id
                for member in authorization.candidate.predecessor_group.member_subjects
            }
        )


def test_population_authorization_selection_flags_are_not_authority(
    tmp_path: Path,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    flipped = build_owner_backfill_evaluation_coverage(
        coverage.epoch,
        tuple(
            replace(terminal, selected_for_output=not terminal.selected_for_output)
            for terminal in coverage.terminals
        ),
    )

    original = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
    )
    selection_flipped = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=flipped,
        cell_observations=cells,
    )

    assert coverage.coverage_id != flipped.coverage_id
    assert original == selection_flipped


def test_population_authorization_rejects_incomplete_and_duplicate_cells(
    tmp_path: Path,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )

    with pytest.raises(ObservationRepairContractError, match="incomplete"):
        authorize_observation_repair(
            processing_owners=fixture.owners,
            coverage=coverage,
            cell_observations=cells[:-1],
        )
    with pytest.raises(ObservationRepairContractError, match="duplicate"):
        authorize_observation_repair(
            processing_owners=fixture.owners,
            coverage=coverage,
            cell_observations=(*cells, cells[0]),
        )
    with pytest.raises(ObservationRepairContractError, match="immutable"):
        authorize_observation_repair(
            processing_owners=fixture.owners,
            coverage=coverage,
            cell_observations=list(cells),  # type: ignore[arg-type]
        )


def test_population_authorization_rejects_crossed_position_and_sample(
    tmp_path: Path,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    by_sample = {
        cell.content.canonical_sample_identity_id: cell for cell in cells
    }
    s2_id = fixture.sample("s2").identity.sample_identity_id
    s3_id = fixture.sample("s3").identity.sample_identity_id
    crossed = by_sample[s2_id]
    original_content = crossed.content
    object.__setattr__(
        crossed,
        "content",
        replace(original_content, canonical_sample_identity_id=s3_id),
    )
    try:
        with pytest.raises(ObservationRepairContractError, match="noncanonical"):
            authorize_observation_repair(
                processing_owners=fixture.owners,
                coverage=coverage,
                cell_observations=cells,
            )
    finally:
        object.__setattr__(crossed, "content", original_content)
    assert spine.group is not None


def test_population_authorization_rejects_extra_crossed_group_and_request(
    tmp_path: Path,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path / "current",
        hold_samples=("s2",),
    )
    foreign_cells = _authorization_population(
        tmp_path / "foreign",
        hold_samples=("s2",),
    )[3]
    with pytest.raises(
        ObservationRepairContractError,
        match="stale|extra or crossed",
    ):
        authorize_observation_repair(
            processing_owners=fixture.owners,
            coverage=coverage,
            cell_observations=(foreign_cells[0], *cells[1:]),
        )


def test_population_authorization_rejects_stale_attempt_context(
    tmp_path: Path,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    cell = cells[0]
    context = cell.group_context
    original_attempt = context.processing_attempt
    stale_attempt = fixture.retry().attempt
    object.__setattr__(context, "processing_attempt", stale_attempt)
    try:
        with pytest.raises(ObservationRepairContractError, match="stale"):
            authorize_observation_repair(
                processing_owners=fixture.owners,
                coverage=coverage,
                cell_observations=cells,
            )
    finally:
        object.__setattr__(context, "processing_attempt", original_attempt)


def test_population_authorization_statuses_are_not_eligible(tmp_path: Path) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=(),
    )
    existing_by_sample = {
        cell.content.canonical_sample_identity_id: cell for cell in spine.cells
    }
    status_cells = []
    for sample in fixture.samples:
        existing = existing_by_sample[sample.identity.sample_identity_id]
        if sample.lookup_token == "s4":
            status_cells.append(
                _source_cell(
                    fixture,
                    sample,
                    spine,
                    _failed_assessment(fixture, sample.lookup_token),
                )
            )
        elif sample.lookup_token == "s2":
            status_cells.append(
                _source_cell(
                    fixture,
                    sample,
                    spine,
                    _not_evaluated_assessment(
                        fixture,
                        sample.lookup_token,
                        suffix="status",
                    ),
                )
            )
        else:
            status_cells.append(existing)
    authorization = authorize_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=tuple(status_cells),
    )
    assert isinstance(authorization, ObservationRepairNotRunAuthorization)
    assert authorization.eligible_cell_ids == ()


def _authorization_population(
    tmp_path: Path,
    *,
    hold_samples: tuple[str, ...],
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture, spine, handoff = _owner_handoff(tmp_path)
    return fixture, spine, handoff.coverage, _population_cells(
        fixture,
        spine,
        hold_samples=hold_samples,
        suffix="authorization",
    )


def _population_cells(
    fixture: object,
    spine: object,
    *,
    hold_samples: tuple[str, ...],
    suffix: str,
):
    existing_by_sample = {
        cell.content.canonical_sample_identity_id: cell for cell in spine.cells
    }
    cells = []
    hold_set = set(hold_samples)
    for sample in fixture.samples:
        if sample.lookup_token in hold_set:
            cells.append(existing_by_sample[sample.identity.sample_identity_id])
        else:
            cells.append(
                _source_cell(
                    fixture,
                    sample,
                    spine,
                    _not_evaluated_assessment(
                        fixture,
                        sample.lookup_token,
                        suffix=suffix,
                    ),
                )
            )
    return tuple(cells)


def _failed_assessment(fixture: object, sample_stem: str):
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,  # type: ignore[attr-defined]
        sample=fixture.sample(sample_stem),  # type: ignore[attr-defined]
        target=fixture.target,  # type: ignore[attr-defined]
        detector_invocation_id=f"gaussian15:{sample_stem}:failed",
        evaluation_receipt=replace(
            _completed_receipt(scanned_event_count=0),
            search_completed=False,
            reason_code="search_failed",
        ),
        technical_state="failed",
    )
