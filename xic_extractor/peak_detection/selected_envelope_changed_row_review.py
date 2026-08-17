from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

GateDecision = Literal["promote", "no_go", "externalize", "defer"]


def build_selected_envelope_changed_row_preflight_manifest(
    diagnostic_manifest: Mapping[str, str],
    oracle_manifest: Mapping[str, str],
    *,
    changed_row_artifact_path: str = "",
    changed_row_artifact_exists: bool = False,
    changed_row_artifact_sha256: str = "",
    oracle_artifact_path: str = "",
    oracle_artifact_exists: bool = False,
    oracle_artifact_sha256: str = "",
    output_root: str = "",
    raw_runner_contract_checked: bool = False,
    raw_input_sample_count: int | None = None,
    expected_sample_count: int | None = None,
    raw_dir_exists: bool = False,
    dll_dir_exists: bool = False,
    worktree_root: str = "",
) -> dict[str, str]:
    """Build the no-RAW gate that decides whether a changed-row RAW run may launch."""
    changed_row_count = _parse_int(diagnostic_manifest.get("changed_row_count"))
    changed_row_denominator = _parse_int(
        diagnostic_manifest.get("changed_row_denominator")
    )
    expert_oracle_row_count = _parse_int(
        oracle_manifest.get("expert_oracle_row_count")
    )

    blockers: list[str] = []
    diagnostic_decision = _gate_decision(diagnostic_manifest.get("gate_decision"))
    oracle_decision = _gate_decision(oracle_manifest.get("gate_decision"))
    terminal_decision: GateDecision | None = None

    _apply_upstream_gate(
        blockers,
        "diagnostic",
        diagnostic_decision,
        diagnostic_manifest.get("blocked_reasons", ""),
    )
    terminal_decision = _terminal_decision(terminal_decision, diagnostic_decision)

    _apply_upstream_gate(
        blockers,
        "oracle",
        oracle_decision,
        oracle_manifest.get("blocked_reasons", ""),
    )
    terminal_decision = _terminal_decision(terminal_decision, oracle_decision)

    if changed_row_count <= 0:
        blockers.append("no_changed_rows_to_review")
    if changed_row_count > changed_row_denominator:
        blockers.append("changed_row_count_exceeds_denominator")
    if not changed_row_artifact_path:
        blockers.append("missing_changed_row_artifact")
    elif not changed_row_artifact_exists:
        blockers.append("changed_row_artifact_not_confirmed")
    elif not changed_row_artifact_sha256:
        blockers.append("changed_row_artifact_hash_not_recorded")
    elif not _is_sha256(changed_row_artifact_sha256):
        blockers.append("changed_row_artifact_hash_invalid")
    if expert_oracle_row_count <= 0:
        blockers.append("no_expert_boundary_oracle_rows")
    if not oracle_artifact_path:
        blockers.append("missing_boundary_oracle_artifact")
    elif not oracle_artifact_exists:
        blockers.append("boundary_oracle_artifact_not_confirmed")
    elif not oracle_artifact_sha256:
        blockers.append("boundary_oracle_artifact_hash_not_recorded")
    elif not _is_sha256(oracle_artifact_sha256):
        blockers.append("boundary_oracle_artifact_hash_invalid")
    if not raw_runner_contract_checked:
        blockers.append("raw_runner_contract_not_checked")
    if raw_input_sample_count is None:
        blockers.append("raw_input_sample_count_not_checked")
    elif (
        expected_sample_count is not None
        and raw_input_sample_count != expected_sample_count
    ):
        blockers.append(
            f"raw_input_sample_count_mismatch:{raw_input_sample_count}!="
            f"{expected_sample_count}"
        )
    if not raw_dir_exists:
        blockers.append("raw_dir_not_confirmed")
    if not dll_dir_exists:
        blockers.append("dll_dir_not_confirmed")
    if not _is_worktree_output_root(output_root, worktree_root=worktree_root):
        blockers.append("output_root_not_under_worktree_output")

    decision = terminal_decision or ("defer" if blockers else "promote")
    raw_launch_allowed = decision == "promote" and not blockers
    if raw_launch_allowed:
        decision = "promote"

    return {
        "gate_decision": decision,
        "raw_launch_allowed": "TRUE" if raw_launch_allowed else "FALSE",
        "readiness_label": "diagnostic_only",
        "changed_row_count": str(changed_row_count),
        "changed_row_denominator": str(changed_row_denominator),
        "changed_row_artifact_present": (
            "TRUE" if changed_row_artifact_exists else "FALSE"
        ),
        "changed_row_artifact_sha256": changed_row_artifact_sha256,
        "expert_oracle_row_count": str(expert_oracle_row_count),
        "boundary_oracle_artifact_present": (
            "TRUE" if oracle_artifact_exists else "FALSE"
        ),
        "boundary_oracle_artifact_sha256": oracle_artifact_sha256,
        "blocked_reasons": ";".join(blockers),
        "next_gate": _next_gate(decision),
    }


def _apply_upstream_gate(
    blockers: list[str],
    prefix: Literal["diagnostic", "oracle"],
    decision: GateDecision,
    upstream_reasons: str,
) -> None:
    if decision == "promote":
        return
    reason = upstream_reasons or "no_blocked_reason_reported"
    if decision == "defer":
        blockers.append(f"{prefix}_gate_not_promote:{reason}")
        return
    blockers.append(f"{prefix}_gate_{decision}:{reason}")


def _terminal_decision(
    current: GateDecision | None,
    decision: GateDecision,
) -> GateDecision | None:
    if decision == "no_go":
        return "no_go"
    if current == "no_go":
        return current
    if decision == "externalize":
        return "externalize"
    if current == "externalize":
        return current
    return current


def _gate_decision(value: str | None) -> GateDecision:
    if value in {"promote", "no_go", "externalize", "defer"}:
        return cast(GateDecision, value)
    return "defer"


def _parse_int(value: str | None) -> int:
    try:
        parsed = int(value or "0")
    except ValueError:
        return 0
    return max(parsed, 0)


def _next_gate(decision: GateDecision) -> str:
    if decision == "promote":
        return "changed_row_diagnostic_run"
    if decision == "no_go":
        return "stop_selected_envelope_product_path"
    if decision == "externalize":
        return "diagnostic_review_only"
    return "bounded_follow_up_required"


def _is_worktree_output_root(output_root: str, *, worktree_root: str) -> bool:
    normalized_output = _normalize_path(output_root)
    if not normalized_output:
        return False
    if normalized_output == "output" or normalized_output.startswith("output/"):
        return True
    normalized_worktree = _normalize_path(worktree_root).rstrip("/")
    if not normalized_worktree:
        return False
    return normalized_output.startswith(f"{normalized_worktree}/output/")


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").rstrip("/")


def _is_sha256(value: str) -> bool:
    normalized = value.strip()
    return len(normalized) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in normalized
    )
