import pytest

from xic_extractor.peak_detection.selected_envelope_changed_row_review import (
    build_selected_envelope_changed_row_preflight_manifest,
)


def _diagnostic_manifest(
    gate_decision: str = "promote",
    *,
    changed_row_count: str = "2",
    blocked_reasons: str = "",
) -> dict[str, str]:
    return {
        "gate_decision": gate_decision,
        "changed_row_count": changed_row_count,
        "changed_row_denominator": "8",
        "high_risk_strata": "",
        "unresolved_blocker_count": "0",
        "blocked_reasons": blocked_reasons,
        "next_gate": "manual_overlay_oracle",
    }


def _oracle_manifest(
    gate_decision: str = "promote",
    *,
    expert_oracle_row_count: str = "1",
    blocked_reasons: str = "",
) -> dict[str, str]:
    return {
        "gate_decision": gate_decision,
        "expert_oracle_row_count": expert_oracle_row_count,
        "benchmark_control_row_count": "0",
        "selected_envelope_closer_count": "1",
        "resolver_interval_closer_count": "0",
        "blocked_reasons": blocked_reasons,
        "next_gate": "changed_row_review",
    }


def _ready_preflight(**overrides: object) -> dict[str, str]:
    args = {
        "diagnostic_manifest": _diagnostic_manifest(),
        "oracle_manifest": _oracle_manifest(),
        "changed_row_artifact_path": "output/selected_full_envelope/changed_rows.tsv",
        "changed_row_artifact_exists": True,
        "changed_row_artifact_sha256": "a" * 64,
        "oracle_artifact_path": "output/selected_full_envelope/oracle.tsv",
        "oracle_artifact_exists": True,
        "oracle_artifact_sha256": "b" * 64,
        "output_root": "output/selected_full_envelope/8raw",
        "raw_runner_contract_checked": True,
        "raw_input_sample_count": 8,
        "expected_sample_count": 8,
        "raw_dir_exists": True,
        "dll_dir_exists": True,
    }
    args.update(overrides)
    return build_selected_envelope_changed_row_preflight_manifest(**args)


def test_changed_row_preflight_allows_raw_only_after_real_oracle_promote() -> None:
    manifest = _ready_preflight()

    assert manifest["gate_decision"] == "promote"
    assert manifest["raw_launch_allowed"] == "TRUE"
    assert manifest["readiness_label"] == "diagnostic_only"
    assert manifest["changed_row_count"] == "2"
    assert manifest["changed_row_artifact_present"] == "TRUE"
    assert manifest["changed_row_artifact_sha256"] == "a" * 64
    assert manifest["expert_oracle_row_count"] == "1"
    assert manifest["boundary_oracle_artifact_present"] == "TRUE"
    assert manifest["boundary_oracle_artifact_sha256"] == "b" * 64
    assert manifest["blocked_reasons"] == ""
    assert manifest["next_gate"] == "changed_row_diagnostic_run"


def test_changed_row_preflight_defers_without_boundary_oracle_rows() -> None:
    manifest = _ready_preflight(
        oracle_manifest=_oracle_manifest(
            "defer",
            expert_oracle_row_count="0",
            blocked_reasons="no_boundary_oracle_rows",
        ),
    )

    assert manifest["gate_decision"] == "defer"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "oracle_gate_not_promote:no_boundary_oracle_rows" in manifest[
        "blocked_reasons"
    ]
    assert "no_expert_boundary_oracle_rows" in manifest["blocked_reasons"]
    assert manifest["next_gate"] == "bounded_follow_up_required"


def test_changed_row_preflight_stops_when_diagnostic_manifest_is_no_go() -> None:
    manifest = _ready_preflight(
        diagnostic_manifest=_diagnostic_manifest(
            "no_go",
            blocked_reasons="max_envelope_width_exceeded",
        ),
    )

    assert manifest["gate_decision"] == "no_go"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "diagnostic_gate_no_go:max_envelope_width_exceeded" in manifest[
        "blocked_reasons"
    ]
    assert manifest["next_gate"] == "stop_selected_envelope_product_path"


def test_changed_row_preflight_externalizes_review_only_diagnostics() -> None:
    manifest = _ready_preflight(
        diagnostic_manifest=_diagnostic_manifest(
            "externalize",
            blocked_reasons="split_supported_review_required",
        ),
    )

    assert manifest["gate_decision"] == "externalize"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "diagnostic_gate_externalize:split_supported_review_required" in manifest[
        "blocked_reasons"
    ]
    assert manifest["next_gate"] == "diagnostic_review_only"


def test_changed_row_preflight_defers_without_changed_rows_to_review() -> None:
    manifest = _ready_preflight(
        diagnostic_manifest=_diagnostic_manifest(changed_row_count="0"),
    )

    assert manifest["gate_decision"] == "defer"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "no_changed_rows_to_review" in manifest["blocked_reasons"]


def test_changed_row_preflight_defers_on_raw_runner_or_sample_count_gap() -> None:
    manifest = _ready_preflight(
        raw_runner_contract_checked=False,
        raw_input_sample_count=85,
    )

    assert manifest["gate_decision"] == "defer"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "raw_runner_contract_not_checked" in manifest["blocked_reasons"]
    assert "raw_input_sample_count_mismatch:85!=8" in manifest["blocked_reasons"]


def test_changed_row_preflight_skips_cohort_size_check_without_expectation() -> None:
    manifest = _ready_preflight(
        raw_input_sample_count=85,
        expected_sample_count=None,
    )

    assert manifest["gate_decision"] == "promote"
    assert manifest["raw_launch_allowed"] == "TRUE"
    assert "raw_input_sample_count_mismatch" not in manifest["blocked_reasons"]


@pytest.mark.parametrize(
    ("raw_count", "expected_count", "expected_decision", "expected_blocker"),
    (
        (None, None, "defer", "raw_input_sample_count_not_checked"),
        (None, 0, "defer", "raw_input_sample_count_not_checked"),
        (None, 8, "defer", "raw_input_sample_count_not_checked"),
        (0, None, "defer", "raw_input_sample_count_nonpositive:0"),
        (0, 0, "defer", "raw_input_sample_count_nonpositive:0"),
        (-1, 8, "defer", "raw_input_sample_count_nonpositive:-1"),
        (8, None, "promote", ""),
        (8, 0, "defer", "expected_sample_count_nonpositive:0"),
        (7, 8, "defer", "raw_input_sample_count_mismatch:7!=8"),
        (8, 8, "promote", ""),
    ),
)
def test_changed_row_preflight_closes_sample_count_truth_table(
    raw_count: int | None,
    expected_count: int | None,
    expected_decision: str,
    expected_blocker: str,
) -> None:
    manifest = _ready_preflight(
        raw_input_sample_count=raw_count,
        expected_sample_count=expected_count,
    )

    assert manifest["gate_decision"] == expected_decision
    assert manifest["raw_launch_allowed"] == (
        "TRUE" if expected_decision == "promote" else "FALSE"
    )
    assert manifest["blocked_reasons"] == expected_blocker


def test_changed_row_preflight_requires_artifact_existence_and_hash_facts() -> None:
    manifest = _ready_preflight(
        changed_row_artifact_exists=False,
        changed_row_artifact_sha256="",
        oracle_artifact_exists=True,
        oracle_artifact_sha256="",
    )

    assert manifest["gate_decision"] == "defer"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "changed_row_artifact_not_confirmed" in manifest["blocked_reasons"]
    assert "boundary_oracle_artifact_hash_not_recorded" in manifest[
        "blocked_reasons"
    ]


def test_changed_row_preflight_requires_sha256_shaped_hash_facts() -> None:
    manifest = _ready_preflight(
        changed_row_artifact_sha256="changed-row-sha256",
        oracle_artifact_sha256="oracle-sha256",
    )

    assert manifest["gate_decision"] == "defer"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "changed_row_artifact_hash_invalid" in manifest["blocked_reasons"]
    assert "boundary_oracle_artifact_hash_invalid" in manifest["blocked_reasons"]


def test_changed_row_preflight_rejects_invalid_changed_row_denominator() -> None:
    manifest = _ready_preflight(
        diagnostic_manifest=_diagnostic_manifest(
            changed_row_count="9",
        ),
    )

    assert manifest["gate_decision"] == "defer"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "changed_row_count_exceeds_denominator" in manifest["blocked_reasons"]


def test_changed_row_preflight_requires_worktree_output_root() -> None:
    manifest = _ready_preflight(output_root="C:/tmp/selected_full_envelope")

    assert manifest["gate_decision"] == "defer"
    assert manifest["raw_launch_allowed"] == "FALSE"
    assert "output_root_not_under_worktree_output" in manifest["blocked_reasons"]
