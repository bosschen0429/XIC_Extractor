from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    ProcessingRequestIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)


def test_attempt_history_is_owner_bound_ordered_and_append_only(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        append_attempt_lifecycle_event,
    )

    attempt = _attempt(tmp_path)
    empty = AttemptLifecycleHistory(attempt=attempt)
    queued = append_attempt_lifecycle_event(
        empty,
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    started = append_attempt_lifecycle_event(
        queued,
        AttemptLifecycleEventKind.ATTEMPT_STARTED,
    )
    cancelled = append_attempt_lifecycle_event(
        started,
        AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
    )

    assert empty.events == ()
    assert [event.sequence for event in cancelled.events] == [1, 2, 3]
    assert all(event.attempt == attempt for event in cancelled.events)
    assert cancelled.cancellation_accepted is True
    with pytest.raises(FrozenInstanceError):
        cancelled.events[0].sequence = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        cancelled.events = ()  # type: ignore[misc]


def test_queued_cancellation_prevents_attempt_start(tmp_path: Path) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleContractError,
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        append_attempt_lifecycle_event,
    )

    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=_attempt(tmp_path)),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    cancelled = append_attempt_lifecycle_event(
        queued,
        AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
    )

    with pytest.raises(
        AttemptLifecycleContractError,
        match="cannot start after queued cancellation",
    ):
        append_attempt_lifecycle_event(
            cancelled,
            AttemptLifecycleEventKind.ATTEMPT_STARTED,
        )


def test_queued_cancellation_and_manifest_preflight_failure_are_exclusive(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleContractError,
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        append_attempt_lifecycle_event,
        propose_manifest_preflight_failure,
    )

    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=_attempt(tmp_path)),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    cancelled = append_attempt_lifecycle_event(
        queued,
        AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
    )
    with pytest.raises(
        AttemptLifecycleContractError,
        match="preflight failure cannot follow queued cancellation",
    ):
        propose_manifest_preflight_failure(cancelled)

    preflight_failed = append_attempt_lifecycle_event(
        queued,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
    )
    with pytest.raises(
        AttemptLifecycleContractError,
        match="cancellation cannot be accepted after manifest preflight failure",
    ):
        append_attempt_lifecycle_event(
            preflight_failed,
            AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
        )


def test_manifest_preflight_failure_proposes_failed_terminal_without_start(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        AttemptTerminalOutcome,
        append_attempt_lifecycle_event,
        propose_manifest_preflight_failure,
    )

    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=_attempt(tmp_path)),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    proposed = propose_manifest_preflight_failure(queued)

    assert [event.kind for event in proposed.events] == [
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
        AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
    ]
    assert AttemptLifecycleEventKind.ATTEMPT_STARTED not in {
        event.kind for event in proposed.events
    }
    assert proposed.events[-1].proposed_outcome is AttemptTerminalOutcome.FAILED


def test_required_work_manifest_preflight_accepts_owner_attested_closed_work(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        append_attempt_lifecycle_event,
        preflight_required_work_manifest,
    )

    attempt = _attempt(tmp_path)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )

    preflight = preflight_required_work_manifest(
        queued,
        attempt=attempt,
        work_unit_keys=("sample-a:target-1", "sample-a:target-2"),
        owner_completeness_attestation="targeted-application-service",
    )

    assert preflight.accepted is True
    assert preflight.history == queued
    assert preflight.manifest is not None
    assert preflight.manifest.attempt == attempt
    assert preflight.manifest.work_unit_keys == (
        "sample-a:target-1",
        "sample-a:target-2",
    )
    assert (
        preflight.manifest.owner_completeness_attestation
        == "targeted-application-service"
    )


def test_required_work_manifest_preflight_rejects_empty_running_manifest(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        append_attempt_lifecycle_event,
        preflight_required_work_manifest,
    )

    attempt = _attempt(tmp_path)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )

    preflight = preflight_required_work_manifest(
        queued,
        attempt=attempt,
        work_unit_keys=(),
        owner_completeness_attestation="targeted-application-service",
    )

    assert preflight.accepted is False
    assert preflight.manifest is None
    assert [event.kind for event in preflight.history.events] == [
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
        AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
    ]


@pytest.mark.parametrize(
    ("work_unit_keys", "owner_completeness_attestation"),
    [
        (("sample-a:target-1",), None),
        (("sample-a:target-1", "sample-a:target-1"), "targeted"),
        (("",), "targeted"),
    ],
)
def test_invalid_required_work_manifest_drives_existing_failed_proposal(
    tmp_path: Path,
    work_unit_keys: tuple[str, ...],
    owner_completeness_attestation: str | None,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleContractError,
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        AttemptTerminalOutcome,
        append_attempt_lifecycle_event,
        preflight_required_work_manifest,
        start_attempt_runtime,
    )

    attempt = _attempt(tmp_path)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )

    preflight = preflight_required_work_manifest(
        queued,
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation=owner_completeness_attestation,
    )

    assert preflight.accepted is False
    assert preflight.manifest is None
    assert [event.kind for event in preflight.history.events] == [
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
        AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
    ]
    assert (
        preflight.history.events[-1].proposed_outcome
        is AttemptTerminalOutcome.FAILED
    )
    with pytest.raises(
        AttemptLifecycleContractError,
        match="cannot start runtime from rejected",
    ):
        start_attempt_runtime(preflight)


def test_required_work_manifest_attempt_mismatch_fails_closed(tmp_path: Path) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        append_attempt_lifecycle_event,
        preflight_required_work_manifest,
    )

    request = _request(tmp_path)
    attempt = issue_processing_attempt(request)
    other_attempt = issue_processing_attempt(request, retry_of=attempt)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )

    preflight = preflight_required_work_manifest(
        queued,
        attempt=other_attempt,
        work_unit_keys=("sample-a:target-1",),
        owner_completeness_attestation="targeted",
    )

    assert preflight.accepted is False
    assert preflight.manifest is None
    assert (
        preflight.history.events[-1].kind
        is AttemptLifecycleEventKind.TERMINAL_PROPOSAL
    )


def test_attempt_runtime_settles_manifest_work_exactly_once(tmp_path: Path) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        WorkUnitTerminalOutcome,
        append_attempt_lifecycle_event,
        preflight_required_work_manifest,
        settle_work_unit,
        start_attempt_runtime,
    )

    attempt = _attempt(tmp_path)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    preflight = preflight_required_work_manifest(
        queued,
        attempt=attempt,
        work_unit_keys=("sample-a:target-1", "sample-a:target-2"),
        owner_completeness_attestation="targeted",
    )

    runtime = start_attempt_runtime(preflight)
    target_1_ref = _owner_result_ref(
        attempt,
        "sample-a:target-1",
        b"target-1-result",
    )
    first = settle_work_unit(
        runtime,
        work_unit_key="sample-a:target-1",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=target_1_ref,
    )
    identical_replay = settle_work_unit(
        first,
        work_unit_key="sample-a:target-1",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=target_1_ref,
    )
    complete = settle_work_unit(
        identical_replay,
        work_unit_key="sample-a:target-2",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=_owner_result_ref(
            attempt,
            "sample-a:target-2",
            b"target-2-result",
        ),
    )

    assert runtime.history.events[-1].kind is AttemptLifecycleEventKind.ATTEMPT_STARTED
    assert identical_replay is first
    assert [item.work_unit_key for item in complete.settlements] == [
        "sample-a:target-1",
        "sample-a:target-2",
    ]
    assert complete.all_manifest_work_settled is True
    assert complete.must_fail is False


def test_conflicting_work_settlement_is_recorded_and_fails_attempt(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        WorkUnitSettlementIntegrityFailureKind,
        WorkUnitTerminalOutcome,
        append_attempt_lifecycle_event,
        preflight_required_work_manifest,
        settle_work_unit,
        start_attempt_runtime,
    )

    attempt = _attempt(tmp_path)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    runtime = start_attempt_runtime(
        preflight_required_work_manifest(
            queued,
            attempt=attempt,
            work_unit_keys=("sample-a:target-1",),
            owner_completeness_attestation="targeted",
        )
    )
    accepted = settle_work_unit(
        runtime,
        work_unit_key="sample-a:target-1",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=_owner_result_ref(
            attempt,
            "sample-a:target-1",
            b"first",
        ),
    )
    conflicted = settle_work_unit(
        accepted,
        work_unit_key="sample-a:target-1",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=_owner_result_ref(
            attempt,
            "sample-a:target-1",
            b"different",
        ),
    )
    repeated_conflict = settle_work_unit(
        conflicted,
        work_unit_key="sample-a:target-1",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=_owner_result_ref(
            attempt,
            "sample-a:target-1",
            b"different",
        ),
    )

    assert conflicted.settlements == accepted.settlements
    assert conflicted.integrity_failure is not None
    assert (
        conflicted.integrity_failure.kind
        is WorkUnitSettlementIntegrityFailureKind.CONFLICTING_SETTLEMENT
    )
    assert conflicted.must_fail is True
    assert repeated_conflict is conflicted


def test_undeclared_work_settlement_fails_attempt_without_accepting_result(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        WorkUnitSettlementIntegrityFailureKind,
        WorkUnitTerminalOutcome,
        append_attempt_lifecycle_event,
        preflight_required_work_manifest,
        settle_work_unit,
        start_attempt_runtime,
    )

    attempt = _attempt(tmp_path)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    runtime = start_attempt_runtime(
        preflight_required_work_manifest(
            queued,
            attempt=attempt,
            work_unit_keys=("sample-a:target-1",),
            owner_completeness_attestation="targeted",
        )
    )

    rejected = settle_work_unit(
        runtime,
        work_unit_key="sample-a:target-late",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=_owner_result_ref(
            attempt,
            "sample-a:target-late",
            b"late",
        ),
    )

    assert rejected.settlements == ()
    assert rejected.integrity_failure is not None
    assert (
        rejected.integrity_failure.kind
        is WorkUnitSettlementIntegrityFailureKind.UNDECLARED_WORK_UNIT
    )
    assert rejected.must_fail is True


def test_work_settlement_outcome_and_result_ref_fail_closed(tmp_path: Path) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleContractError,
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        WorkUnitFailureCategory,
        WorkUnitFailureDetail,
        WorkUnitTerminalOutcome,
        append_attempt_lifecycle_event,
        preflight_required_work_manifest,
        settle_work_unit,
        start_attempt_runtime,
    )

    attempt = _attempt(tmp_path)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    runtime = start_attempt_runtime(
        preflight_required_work_manifest(
            queued,
            attempt=attempt,
            work_unit_keys=("sample-a:target-1",),
            owner_completeness_attestation="targeted",
        )
    )

    with pytest.raises(
        AttemptLifecycleContractError,
        match="requires an owner-minted EvidenceArtifactRef",
    ):
        settle_work_unit(
            runtime,
            work_unit_key="sample-a:target-1",
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        )
    with pytest.raises(AttemptLifecycleContractError, match="only succeeded"):
        settle_work_unit(
            runtime,
            work_unit_key="sample-a:target-1",
            outcome=WorkUnitTerminalOutcome.FAILED,
            owner_result_ref=_owner_result_ref(
                attempt,
                "sample-a:target-1",
                b"not-allowed",
            ),
        )
    other_attempt = issue_processing_attempt(attempt_request := _request(tmp_path))
    assert other_attempt.processing_request_id == attempt_request.processing_request_id
    with pytest.raises(AttemptLifecycleContractError, match="request and attempt"):
        settle_work_unit(
            runtime,
            work_unit_key="sample-a:target-1",
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=_owner_result_ref(
                other_attempt,
                "sample-a:target-1",
                b"wrong-attempt",
            ),
        )
    with pytest.raises(
        AttemptLifecycleContractError,
        match="requires accepted cancellation",
    ):
        settle_work_unit(
            runtime,
            work_unit_key="sample-a:target-1",
            outcome=WorkUnitTerminalOutcome.CANCELLED,
            failure_detail=WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.CANCELLATION,
                stage="runtime-test",
                source_reference="sample-a:target-1",
                message="cancelled before settlement",
            ),
        )

    with pytest.raises(
        AttemptLifecycleContractError,
        match="requires WorkUnitFailureDetail",
    ):
        settle_work_unit(
            runtime,
            work_unit_key="sample-a:target-1",
            outcome=WorkUnitTerminalOutcome.FAILED,
        )
    execution_failure = WorkUnitFailureDetail(
        category=WorkUnitFailureCategory.EXECUTION_ERROR,
        stage="runtime-test",
        source_reference="sample-a:target-1",
        message="worker failed",
    )
    with pytest.raises(
        AttemptLifecycleContractError,
        match="succeeded settlement cannot carry failure_detail",
    ):
        settle_work_unit(
            runtime,
            work_unit_key="sample-a:target-1",
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=_owner_result_ref(
                attempt,
                "sample-a:target-1",
                b"unexpected-failure-detail",
            ),
            failure_detail=execution_failure,
        )
    with pytest.raises(
        AttemptLifecycleContractError,
        match="failed settlement cannot carry cancellation",
    ):
        settle_work_unit(
            runtime,
            work_unit_key="sample-a:target-1",
            outcome=WorkUnitTerminalOutcome.FAILED,
            failure_detail=WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.CANCELLATION,
                stage="runtime-test",
                source_reference="sample-a:target-1",
                message="wrong category",
            ),
        )
    failed = settle_work_unit(
        runtime,
        work_unit_key="sample-a:target-1",
        outcome=WorkUnitTerminalOutcome.FAILED,
        failure_detail=execution_failure,
    )
    assert failed.all_manifest_work_settled is True
    assert failed.must_fail is True


def test_manifest_preflight_failure_path_fails_closed(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleContractError,
        AttemptLifecycleEvent,
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        AttemptTerminalOutcome,
        append_attempt_lifecycle_event,
        propose_manifest_preflight_failure,
    )

    attempt = _attempt(tmp_path)
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    with pytest.raises(
        AttemptLifecycleContractError, match="requires an AttemptTerminalOutcome"
    ):
        AttemptLifecycleEvent(
            attempt=attempt,
            sequence=2,
            kind=AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
        )
    with pytest.raises(AttemptLifecycleContractError, match="only terminal_proposal"):
        AttemptLifecycleEvent(
            attempt=attempt,
            sequence=2,
            kind=AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
            proposed_outcome=AttemptTerminalOutcome.FAILED,
        )
    with pytest.raises(AttemptLifecycleContractError, match="AttemptTerminalOutcome"):
        AttemptLifecycleEvent(
            attempt=attempt,
            sequence=2,
            kind=AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
            proposed_outcome="failed",  # type: ignore[arg-type]
        )
    with pytest.raises(AttemptLifecycleContractError, match="must follow"):
        append_attempt_lifecycle_event(
            queued,
            AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
            proposed_outcome=AttemptTerminalOutcome.FAILED,
        )

    failed = append_attempt_lifecycle_event(
        queued,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
    )
    with pytest.raises(AttemptLifecycleContractError, match="must propose failed"):
        append_attempt_lifecycle_event(
            failed,
            AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
            proposed_outcome=AttemptTerminalOutcome.SUCCEEDED,
        )
    with pytest.raises(AttemptLifecycleContractError, match="may appear only once"):
        append_attempt_lifecycle_event(
            failed,
            AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
        )

    started = append_attempt_lifecycle_event(
        queued,
        AttemptLifecycleEventKind.ATTEMPT_STARTED,
    )
    with pytest.raises(AttemptLifecycleContractError, match="before attempt_started"):
        propose_manifest_preflight_failure(started)

    proposed = propose_manifest_preflight_failure(queued)
    with pytest.raises(
        AttemptLifecycleContractError, match="terminal proposal is final"
    ):
        append_attempt_lifecycle_event(
            proposed,
            AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
        )


def test_history_rejects_forged_attempt_sequence_and_transition(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleContractError,
        AttemptLifecycleEvent,
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
    )

    request = _request(tmp_path)
    attempt = issue_processing_attempt(request)
    retry = issue_processing_attempt(request, retry_of=attempt)
    queued = AttemptLifecycleEvent(
        attempt=attempt,
        sequence=1,
        kind=AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )

    with pytest.raises(AttemptLifecycleContractError, match="owner-issued attempt"):
        AttemptLifecycleHistory(
            attempt=attempt,
            events=(
                queued,
                AttemptLifecycleEvent(
                    attempt=retry,
                    sequence=2,
                    kind=AttemptLifecycleEventKind.ATTEMPT_STARTED,
                ),
            ),
        )
    with pytest.raises(AttemptLifecycleContractError, match="contiguous"):
        AttemptLifecycleHistory(
            attempt=attempt,
            events=(
                queued,
                AttemptLifecycleEvent(
                    attempt=attempt,
                    sequence=3,
                    kind=AttemptLifecycleEventKind.ATTEMPT_STARTED,
                ),
            ),
        )
    with pytest.raises(AttemptLifecycleContractError, match="first event"):
        AttemptLifecycleHistory(
            attempt=attempt,
            events=(
                AttemptLifecycleEvent(
                    attempt=attempt,
                    sequence=1,
                    kind=AttemptLifecycleEventKind.ATTEMPT_STARTED,
                ),
            ),
        )


def test_lifecycle_values_fail_closed_on_adversarial_types(tmp_path: Path) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleContractError,
        AttemptLifecycleEvent,
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
    )

    attempt = _attempt(tmp_path)
    with pytest.raises(AttemptLifecycleContractError, match="positive integer"):
        AttemptLifecycleEvent(
            attempt=attempt,
            sequence=True,  # type: ignore[arg-type]
            kind=AttemptLifecycleEventKind.ATTEMPT_QUEUED,
        )
    with pytest.raises(AttemptLifecycleContractError, match="positive integer"):
        AttemptLifecycleEvent(
            attempt=attempt,
            sequence=0,
            kind=AttemptLifecycleEventKind.ATTEMPT_QUEUED,
        )
    with pytest.raises(AttemptLifecycleContractError, match="EventKind"):
        AttemptLifecycleEvent(
            attempt=attempt,
            sequence=1,
            kind="attempt_queued",  # type: ignore[arg-type]
        )
    with pytest.raises(AttemptLifecycleContractError, match="must be a tuple"):
        AttemptLifecycleHistory(attempt=attempt, events=[])  # type: ignore[arg-type]
    with pytest.raises(AttemptLifecycleContractError, match="must contain"):
        AttemptLifecycleHistory(
            attempt=attempt,
            events=("attempt_queued",),  # type: ignore[arg-type]
        )


def test_history_rejects_duplicate_lifecycle_facts(tmp_path: Path) -> None:
    from xic_extractor.execution_runtime import (
        AttemptLifecycleContractError,
        AttemptLifecycleEventKind,
        AttemptLifecycleHistory,
        append_attempt_lifecycle_event,
    )

    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=_attempt(tmp_path)),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    with pytest.raises(AttemptLifecycleContractError, match="attempt_queued"):
        append_attempt_lifecycle_event(
            queued,
            AttemptLifecycleEventKind.ATTEMPT_QUEUED,
        )

    started = append_attempt_lifecycle_event(
        queued,
        AttemptLifecycleEventKind.ATTEMPT_STARTED,
    )
    with pytest.raises(AttemptLifecycleContractError, match="attempt_started"):
        append_attempt_lifecycle_event(
            started,
            AttemptLifecycleEventKind.ATTEMPT_STARTED,
        )

    cancelled = append_attempt_lifecycle_event(
        started,
        AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
    )
    with pytest.raises(AttemptLifecycleContractError, match="cancellation_accepted"):
        append_attempt_lifecycle_event(
            cancelled,
            AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
        )


def test_execution_runtime_contract_stays_inside_identity_boundary() -> None:
    path = Path("xic_extractor/execution_runtime.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = tuple(
        imported for imported in _imports(tree) if imported.startswith("xic_extractor")
    )

    assert imports == (
        "xic_extractor.evidence_artifacts",
        "xic_extractor.processing_identity",
    )


def _attempt(tmp_path: Path) -> ProcessingAttemptIdentity:
    return issue_processing_attempt(_request(tmp_path))


def _request(tmp_path: Path) -> ProcessingRequestIdentity:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"synthetic-no-raw-content")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo.raw.ms_controller_1",
    )
    return build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="sample.raw",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="package0_event_test",
        processing_algorithm_version="package0_event_test",
        processing_contract_version="processing_identity",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )


def _owner_result_ref(
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    payload: bytes,
):
    from xic_extractor.extraction.targeted_runtime_adapter import (
        mint_targeted_process_result_ref,
    )

    return mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key=work_unit_key,
        owner_result_payload=payload,
    ).artifact


def _imports(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)
