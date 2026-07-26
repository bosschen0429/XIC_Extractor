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

    assert imports == ("xic_extractor.processing_identity",)


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
        scientific_input_schema_version="package0_event_test_v1",
        processing_algorithm_version="package0_event_test_v1",
        processing_contract_version="processing_identity_v1",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )


def _imports(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)
