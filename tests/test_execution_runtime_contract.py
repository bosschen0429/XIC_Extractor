import pytest

from xic_extractor.execution_runtime_contract import (
    SUPERVISOR_FINALIZATION_CLASSIFICATION,
    AttemptOutcome,
    RequiredWorkManifest,
    RuntimeContractError,
    RuntimeEvent,
    RuntimeEventKind,
    validate_attempt_history,
)


def test_success_requires_closed_manifest_cleanup_and_acknowledgement() -> None:
    history = validate_attempt_history(
        RequiredWorkManifest(("work-1",), owner_attests_complete=True),
        _events(
            (RuntimeEventKind.ATTEMPT_QUEUED, "", None),
            (RuntimeEventKind.ATTEMPT_STARTED, "", None),
            (RuntimeEventKind.RESOURCE_RESERVED, "resource-1", None),
            (RuntimeEventKind.RESOURCE_OPENED, "resource-1", None),
            (RuntimeEventKind.WORK_SUCCEEDED, "work-1", None),
            (RuntimeEventKind.ARTIFACT_REGISTERED, "artifact-1", None),
            (RuntimeEventKind.ARTIFACT_OFFERED_TO_OWNER, "artifact-1", None),
            (RuntimeEventKind.ARTIFACT_ACKNOWLEDGED, "artifact-1", None),
            (RuntimeEventKind.CLEANUP_SUCCEEDED, "resource-1", None),
            (RuntimeEventKind.ATTEMPT_TERMINAL, "", AttemptOutcome.SUCCEEDED),
        ),
    )

    assert history.outcome is AttemptOutcome.SUCCEEDED
    assert history.acknowledged_artifacts == ("artifact-1",)


def test_cancelled_attempt_quarantines_partial_artifacts() -> None:
    history = validate_attempt_history(
        RequiredWorkManifest(("work-1", "work-2"), owner_attests_complete=True),
        _events(
            (RuntimeEventKind.ATTEMPT_QUEUED, "", None),
            (RuntimeEventKind.ATTEMPT_STARTED, "", None),
            (RuntimeEventKind.WORK_SUCCEEDED, "work-1", None),
            (RuntimeEventKind.ARTIFACT_REGISTERED, "artifact-1", None),
            (RuntimeEventKind.CANCELLATION_REQUESTED, "", None),
            (RuntimeEventKind.ARTIFACT_QUARANTINED, "artifact-1", None),
            (RuntimeEventKind.ATTEMPT_TERMINAL, "", AttemptOutcome.CANCELLED),
        ),
    )

    assert history.outcome is AttemptOutcome.CANCELLED
    assert history.quarantined_artifacts == ("artifact-1",)


def test_worker_loss_and_unknown_cleanup_fail_closed() -> None:
    history = validate_attempt_history(
        RequiredWorkManifest(("work-1",), owner_attests_complete=True),
        _events(
            (RuntimeEventKind.ATTEMPT_QUEUED, "", None),
            (RuntimeEventKind.ATTEMPT_STARTED, "", None),
            (RuntimeEventKind.RESOURCE_RESERVED, "resource-1", None),
            (
                RuntimeEventKind.RESOURCE_UNKNOWN_AFTER_WORKER_LOSS,
                "resource-1",
                None,
            ),
            (
                RuntimeEventKind.CLEANUP_UNKNOWN_AFTER_WORKER_LOSS,
                "resource-1",
                None,
            ),
            (RuntimeEventKind.ATTEMPT_TERMINAL, "", AttemptOutcome.FAILED),
        ),
    )

    assert history.outcome is AttemptOutcome.FAILED


def test_cleanup_must_settle_exactly_once_before_terminal() -> None:
    with pytest.raises(RuntimeContractError, match="cleaned more than once"):
        validate_attempt_history(
            RequiredWorkManifest(("work-1",), owner_attests_complete=True),
            _events(
                (RuntimeEventKind.ATTEMPT_QUEUED, "", None),
                (RuntimeEventKind.ATTEMPT_STARTED, "", None),
                (RuntimeEventKind.RESOURCE_RESERVED, "resource-1", None),
                (RuntimeEventKind.RESOURCE_NOT_OPENED, "resource-1", None),
                (RuntimeEventKind.CLEANUP_SUCCEEDED, "resource-1", None),
                (RuntimeEventKind.CLEANUP_SUCCEEDED, "resource-1", None),
                (RuntimeEventKind.ATTEMPT_TERMINAL, "", AttemptOutcome.FAILED),
            ),
        )


def test_manifest_rejects_duplicate_work_units_before_start() -> None:
    with pytest.raises(RuntimeContractError, match="must be unique"):
        RequiredWorkManifest(("work-1", "work-1"), owner_attests_complete=True)


def test_supervisor_marker_is_recovery_metadata_not_delivery_evidence() -> None:
    assert SUPERVISOR_FINALIZATION_CLASSIFICATION == "runtime_recovery_metadata"


def _events(
    *rows: tuple[RuntimeEventKind, str, AttemptOutcome | None],
) -> tuple[RuntimeEvent, ...]:
    return tuple(
        RuntimeEvent(
            sequence=index,
            attempt_id="attempt-1",
            kind=kind,
            subject_id=subject_id,
            outcome=outcome,
        )
        for index, (kind, subject_id, outcome) in enumerate(rows, start=1)
    )
