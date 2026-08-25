from pathlib import Path

import pytest

from tools import agent_task_state
from tools.agent_task_state import (
    TaskStateError,
    create_task_state,
    load_task_state,
    main,
    render_handoff,
    save_task_state,
    transition_task_state,
)


def _state():
    return create_task_state(
        task_id="task-123",
        objective="Keep a long-running task recoverable",
        active_node="state-spine",
        owner="main",
        base_sha="abc123",
        acceptance=("state resumes without transcript replay",),
        next_transition="Implement the owner",
    )


def test_state_round_trip_and_handoff_are_two_views_of_one_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    save_task_state(path, _state())

    loaded = load_task_state(path, current_base_sha="abc123")
    handoff = render_handoff(loaded)

    assert loaded.task_id == "task-123"
    assert loaded.status == "shaped"
    assert "Keep a long-running task recoverable" in handoff
    assert "state-spine" in handoff
    assert "Implement the owner" in handoff


def test_failed_atomic_replace_preserves_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    path.write_text("previous\n", encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(agent_task_state.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        save_task_state(path, _state())

    assert path.read_text(encoding="utf-8") == "previous\n"
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_cli_init_refuses_to_replace_existing_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "state.json"
    save_task_state(path, _state())
    previous = path.read_bytes()

    with pytest.raises(SystemExit) as raised:
        main(
            (
                "init",
                "--state",
                str(path),
                "--task-id",
                "replacement",
                "--objective",
                "Do not replace the existing state",
                "--active-node",
                "state-spine",
                "--owner",
                "main",
                "--base-sha",
                "def456",
                "--acceptance",
                "replacement is rejected",
                "--next",
                "Stop",
            )
        )

    error = capsys.readouterr().err
    assert raised.value.code == 2
    assert "already exists" in error
    assert "transition" in error
    assert path.read_bytes() == previous


def test_resume_rejects_state_from_a_different_base(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_task_state(path, _state())

    with pytest.raises(TaskStateError, match="stale base"):
        load_task_state(path, current_base_sha="def456")


def test_cli_reports_stale_state_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "state.json"
    save_task_state(path, _state())

    with pytest.raises(SystemExit) as raised:
        main(
            (
                "show",
                "--state",
                str(path),
                "--current-base-sha",
                "def456",
            )
        )

    error = capsys.readouterr().err
    assert raised.value.code == 2
    assert "stale base" in error
    assert "Traceback" not in error


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(TaskStateError, match="illegal transition"):
        transition_task_state(
            _state(),
            to_status="done",
            actor="main",
            next_transition="None",
        )


def test_transition_rejects_a_second_owner() -> None:
    with pytest.raises(TaskStateError, match="owner mismatch"):
        transition_task_state(
            _state(),
            to_status="ready",
            actor="reviewer",
            next_transition="Begin execution",
        )


def test_current_owner_can_transfer_single_ownership_atomically() -> None:
    transferred = transition_task_state(
        _state(),
        to_status="ready",
        actor="main",
        new_owner="executor",
        next_transition="Begin execution",
    )

    assert transferred.owner == "executor"
    with pytest.raises(TaskStateError, match="owner mismatch"):
        transition_task_state(
            transferred,
            to_status="executing",
            actor="main",
            next_transition="Run the slice",
        )


def test_resuming_from_blocked_clears_the_current_blocker() -> None:
    blocked = transition_task_state(
        _state(),
        to_status="blocked",
        actor="main",
        blockers=("Need operator decision",),
        next_transition="Wait for operator",
    )
    resumed = transition_task_state(
        blocked,
        to_status="ready",
        actor="main",
        next_transition="Resume execution",
    )

    assert resumed.blockers == ()


def test_review_and_done_require_evidence() -> None:
    ready = transition_task_state(
        _state(),
        to_status="ready",
        actor="main",
        next_transition="Begin execution",
    )
    executing = transition_task_state(
        ready,
        to_status="executing",
        actor="main",
        next_transition="Run focused verification",
    )
    verifying = transition_task_state(
        executing,
        to_status="verifying",
        actor="main",
        next_transition="Review the evidence",
    )

    with pytest.raises(TaskStateError, match="evidence"):
        transition_task_state(
            verifying,
            to_status="reviewing",
            actor="main",
            next_transition="Close the task",
        )

    reviewing = transition_task_state(
        verifying,
        to_status="reviewing",
        actor="main",
        evidence_refs=("pytest: tests/test_agent_task_state.py",),
        next_transition="Close the task",
    )
    done = transition_task_state(
        reviewing,
        to_status="done",
        actor="main",
        next_transition="None",
    )

    assert done.status == "done"
    assert done.checkpoint_version == 6
