"""Durable, local task-state spine for long-running agent work.

The JSON state is authoritative. Human-readable handoffs are projections and
must never be parsed to reconstruct task state.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

SCHEMA_VERSION = 1

_ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "shaped": frozenset({"ready", "blocked", "superseded"}),
    "ready": frozenset(
        {"executing", "waiting", "needs_decision", "blocked", "superseded"}
    ),
    "executing": frozenset(
        {"verifying", "waiting", "needs_decision", "blocked"}
    ),
    "verifying": frozenset({"reviewing", "executing", "blocked"}),
    "reviewing": frozenset({"done", "executing", "blocked"}),
    "waiting": frozenset({"ready", "blocked", "superseded"}),
    "needs_decision": frozenset({"ready", "blocked", "superseded"}),
    "blocked": frozenset({"ready", "superseded"}),
    "done": frozenset(),
    "superseded": frozenset(),
}
_BLOCKING_STATUSES = frozenset({"waiting", "needs_decision", "blocked"})


class TaskStateError(ValueError):
    """Raised when persisted task state cannot safely drive the next action."""


@dataclass(frozen=True)
class TaskState:
    schema_version: int
    task_id: str
    objective: str
    status: str
    active_node: str
    owner: str
    base_sha: str
    acceptance: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    decisions: tuple[str, ...]
    rejected_paths: tuple[str, ...]
    blockers: tuple[str, ...]
    external_side_effects: tuple[str, ...]
    next_transition: str
    checkpoint_version: int


_STATE_FIELDS = frozenset(TaskState.__dataclass_fields__)
_SEQUENCE_FIELDS = (
    "acceptance",
    "evidence_refs",
    "decisions",
    "rejected_paths",
    "blockers",
    "external_side_effects",
)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskStateError(f"{name} must be non-empty text")
    return value.strip()


def _text_tuple(name: str, values: Iterable[object]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TaskStateError(f"{name} must be a sequence of text values")
    result = tuple(_required_text(name, value) for value in values)
    if name == "acceptance" and not result:
        raise TaskStateError("acceptance must contain at least one criterion")
    return result


def _append_unique(
    current: tuple[str, ...], additions: Sequence[str]
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*current, *additions)))


def _validate_state(state: TaskState) -> TaskState:
    if not isinstance(state.schema_version, int) or isinstance(
        state.schema_version, bool
    ):
        raise TaskStateError("schema_version must be an integer")
    if state.schema_version != SCHEMA_VERSION:
        raise TaskStateError(
            "unsupported schema_version "
            f"{state.schema_version}; expected {SCHEMA_VERSION}"
        )
    if state.status not in _ALLOWED_TRANSITIONS:
        raise TaskStateError(f"unknown status: {state.status}")
    for name in (
        "task_id",
        "objective",
        "active_node",
        "owner",
        "base_sha",
        "next_transition",
    ):
        _required_text(name, getattr(state, name))
    for name in _SEQUENCE_FIELDS:
        _text_tuple(name, getattr(state, name))
    if not isinstance(state.checkpoint_version, int) or isinstance(
        state.checkpoint_version, bool
    ):
        raise TaskStateError("checkpoint_version must be an integer")
    if state.checkpoint_version < 1:
        raise TaskStateError("checkpoint_version must be positive")
    if state.status in {"reviewing", "done"} and not state.evidence_refs:
        raise TaskStateError(f"{state.status} requires evidence")
    if state.status in _BLOCKING_STATUSES and not state.blockers:
        raise TaskStateError(f"{state.status} requires a current blocker")
    if state.status not in _BLOCKING_STATUSES and state.blockers:
        raise TaskStateError(f"{state.status} cannot retain a current blocker")
    return state


def create_task_state(
    *,
    task_id: str,
    objective: str,
    active_node: str,
    owner: str,
    base_sha: str,
    acceptance: Sequence[str],
    next_transition: str,
) -> TaskState:
    return _validate_state(
        TaskState(
            schema_version=SCHEMA_VERSION,
            task_id=_required_text("task_id", task_id),
            objective=_required_text("objective", objective),
            status="shaped",
            active_node=_required_text("active_node", active_node),
            owner=_required_text("owner", owner),
            base_sha=_required_text("base_sha", base_sha),
            acceptance=_text_tuple("acceptance", acceptance),
            evidence_refs=(),
            decisions=(),
            rejected_paths=(),
            blockers=(),
            external_side_effects=(),
            next_transition=_required_text("next_transition", next_transition),
            checkpoint_version=1,
        )
    )


def transition_task_state(
    state: TaskState,
    *,
    to_status: str,
    actor: str,
    next_transition: str,
    active_node: str | None = None,
    new_owner: str | None = None,
    evidence_refs: Sequence[str] = (),
    decisions: Sequence[str] = (),
    rejected_paths: Sequence[str] = (),
    blockers: Sequence[str] | None = None,
    external_side_effects: Sequence[str] = (),
) -> TaskState:
    """Apply one validated transition without changing task authority."""

    _validate_state(state)
    normalized_actor = _required_text("actor", actor)
    if normalized_actor != state.owner:
        raise TaskStateError(
            f"owner mismatch: state belongs to {state.owner!r}, "
            f"actor is {normalized_actor!r}"
        )
    normalized_status = _required_text("to_status", to_status)
    if normalized_status not in _ALLOWED_TRANSITIONS[state.status]:
        raise TaskStateError(
            f"illegal transition: {state.status!r} -> {normalized_status!r}"
        )

    if new_owner is not None and normalized_status in {"done", "superseded"}:
        raise TaskStateError("terminal transition cannot transfer ownership")
    if normalized_status in _BLOCKING_STATUSES:
        current_blockers = (
            _text_tuple("blockers", blockers)
            if blockers is not None
            else state.blockers
        )
    else:
        supplied_blockers = (
            _text_tuple("blockers", blockers) if blockers is not None else ()
        )
        if supplied_blockers:
            raise TaskStateError(
                f"{normalized_status} cannot accept a current blocker"
            )
        current_blockers = ()

    updated = replace(
        state,
        status=normalized_status,
        active_node=(
            _required_text("active_node", active_node)
            if active_node is not None
            else state.active_node
        ),
        owner=(
            _required_text("new_owner", new_owner)
            if new_owner is not None
            else state.owner
        ),
        evidence_refs=_append_unique(
            state.evidence_refs, _text_tuple("evidence_refs", evidence_refs)
        ),
        decisions=_append_unique(
            state.decisions, _text_tuple("decisions", decisions)
        ),
        rejected_paths=_append_unique(
            state.rejected_paths, _text_tuple("rejected_paths", rejected_paths)
        ),
        blockers=current_blockers,
        external_side_effects=_append_unique(
            state.external_side_effects,
            _text_tuple("external_side_effects", external_side_effects),
        ),
        next_transition=_required_text("next_transition", next_transition),
        checkpoint_version=state.checkpoint_version + 1,
    )
    return _validate_state(updated)


def _write_bytes_atomic(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary_path.read_bytes() != encoded:
            raise TaskStateError(f"temporary write verification failed for {path}")
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def save_task_state(path: Path, state: TaskState) -> None:
    """Atomically publish canonical UTF-8 JSON in the destination directory."""

    _validate_state(state)
    payload = asdict(state)
    for name in _SEQUENCE_FIELDS:
        payload[name] = list(payload[name])
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    _write_bytes_atomic(path, encoded)


def load_task_state(path: Path, *, current_base_sha: str) -> TaskState:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TaskStateError(f"cannot read task state {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise TaskStateError("task state root must be an object")
    unknown = frozenset(payload) - _STATE_FIELDS
    missing = _STATE_FIELDS - frozenset(payload)
    if unknown or missing:
        raise TaskStateError(
            f"task state fields differ: missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )
    for name in _SEQUENCE_FIELDS:
        value = payload[name]
        if not isinstance(value, list):
            raise TaskStateError(f"{name} must be a list")
        payload[name] = tuple(value)
    try:
        state = TaskState(**payload)
    except TypeError as exc:
        raise TaskStateError(f"invalid task state: {exc}") from exc
    _validate_state(state)
    current = _required_text("current_base_sha", current_base_sha)
    if current != state.base_sha:
        raise TaskStateError(
            f"stale base: state={state.base_sha!r}, current={current!r}"
        )
    return state


def _markdown_list(values: Sequence[str], *, empty: str = "None") -> str:
    return "\n".join(f"- {value}" for value in values) if values else f"- {empty}"


def render_handoff(state: TaskState) -> str:
    """Render a disposable human view from authoritative task state."""

    _validate_state(state)
    return "\n".join(
        (
            f"# Task handoff: {state.task_id}",
            "",
            f"- Status: `{state.status}`",
            f"- Active node: `{state.active_node}`",
            f"- Owner: `{state.owner}`",
            f"- Base SHA: `{state.base_sha}`",
            f"- Checkpoint: `{state.checkpoint_version}`",
            "",
            "## Objective",
            "",
            state.objective,
            "",
            "## Acceptance",
            "",
            _markdown_list(state.acceptance),
            "",
            "## Evidence",
            "",
            _markdown_list(state.evidence_refs),
            "",
            "## Decisions",
            "",
            _markdown_list(state.decisions),
            "",
            "## Rejected paths",
            "",
            _markdown_list(state.rejected_paths),
            "",
            "## Blockers / waits",
            "",
            _markdown_list(state.blockers),
            "",
            "## External side effects",
            "",
            _markdown_list(state.external_side_effects),
            "",
            "## Next transition",
            "",
            state.next_transition,
            "",
        )
    )


def _add_state_and_base(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--current-base-sha", required=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create a shaped task state")
    init.add_argument("--state", type=Path, required=True)
    init.add_argument("--task-id", required=True)
    init.add_argument("--objective", required=True)
    init.add_argument("--active-node", required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--base-sha", required=True)
    init.add_argument("--acceptance", action="append", required=True)
    init.add_argument("--next", dest="next_transition", required=True)

    transition = commands.add_parser("transition", help="Apply one state transition")
    _add_state_and_base(transition)
    transition.add_argument("--to", dest="to_status", required=True)
    transition.add_argument("--actor", required=True)
    transition.add_argument("--new-owner")
    transition.add_argument("--active-node")
    transition.add_argument("--evidence", action="append", default=[])
    transition.add_argument("--decision", action="append", default=[])
    transition.add_argument("--rejected-path", action="append", default=[])
    transition.add_argument("--blocker", action="append")
    transition.add_argument("--external-side-effect", action="append", default=[])
    transition.add_argument("--next", dest="next_transition", required=True)

    show = commands.add_parser("show", help="Print authoritative JSON")
    _add_state_and_base(show)

    handoff = commands.add_parser("handoff", help="Render a Markdown projection")
    _add_state_and_base(handoff)
    handoff.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            if args.state.exists():
                raise TaskStateError(
                    f"state already exists at {args.state}; "
                    "use transition or choose a new path"
                )
            state = create_task_state(
                task_id=args.task_id,
                objective=args.objective,
                active_node=args.active_node,
                owner=args.owner,
                base_sha=args.base_sha,
                acceptance=args.acceptance,
                next_transition=args.next_transition,
            )
            save_task_state(args.state, state)
            return 0

        state = load_task_state(args.state, current_base_sha=args.current_base_sha)
        if args.command == "transition":
            state = transition_task_state(
                state,
                to_status=args.to_status,
                actor=args.actor,
                active_node=args.active_node,
                new_owner=args.new_owner,
                evidence_refs=args.evidence,
                decisions=args.decision,
                rejected_paths=args.rejected_path,
                blockers=args.blocker,
                external_side_effects=args.external_side_effect,
                next_transition=args.next_transition,
            )
            save_task_state(args.state, state)
            return 0
        if args.command == "show":
            print(
                json.dumps(
                    asdict(state), ensure_ascii=False, sort_keys=True, indent=2
                )
            )
            return 0

        rendered = render_handoff(state)
        if args.output is None:
            print(rendered, end="")
        else:
            _write_bytes_atomic(args.output, rendered.encode("utf-8"))
        return 0
    except (OSError, TaskStateError) as exc:
        parser.exit(2, f"agent-task-state error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
