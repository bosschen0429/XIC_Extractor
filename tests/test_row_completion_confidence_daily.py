from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.diagnostics.row_completion_confidence import (
    build_daily_confidence_packet,
)
from xic_extractor.diagnostics.row_completion_confidence_schema import (
    DISAGREEMENT_COLUMNS,
    SUMMARY_COLUMNS,
)


def test_daily_packet_writes_no_authority_outputs(tmp_path: Path) -> None:
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    health = _write_health_packet(tmp_path / "health")
    output = tmp_path / "out"

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=output,
        run_id="synthetic_daily",
        generation_context="synthetic_current",
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert packet["schema_version"] == "row_completion_confidence"
    assert packet["gate_mode"] == "diagnostic"
    assert packet["run_ok"] is True
    assert packet["gate_ok"] is True
    assert packet["status"] == "PASS"
    assert packet["manual_review_required"] is False
    assert packet["production_ready"] is False
    assert packet["production_safety"] == "stable"
    assert packet["review_utility"] == "stable"
    assert packet["validation_tier"] == "diagnostic_only"
    assert packet["baseline_binding"] == "no_baseline_supplied"
    assert packet["product_gate_eligible"] is False
    assert packet["authority_decision"] == "no_control_plane_change"
    assert packet["input_artifact_manifest"]["run_id"] == "synthetic_daily"
    assert "diagnostic_only" in packet["no_authority_statement"]
    summary_header = outputs.summary_tsv.read_text(
        encoding="utf-8",
    ).splitlines()[0].split("\t")
    assert tuple(summary_header) == SUMMARY_COLUMNS
    assert "duplicate_only_family_count" in outputs.summary_tsv.read_text(
        encoding="utf-8",
    )
    assert outputs.sentinels_tsv.name == "row_completion_sentinels.tsv"
    legacy_sentinels = output / "row_completion_family_sentinels.tsv"
    assert legacy_sentinels.is_file()
    assert legacy_sentinels.read_text(encoding="utf-8") == (
        outputs.sentinels_tsv.read_text(encoding="utf-8")
    )
    assert "FAM_BAD" in outputs.sentinels_tsv.read_text(encoding="utf-8")
    disagreements_text = outputs.disagreements_tsv.read_text(encoding="utf-8")
    disagreement_lines = disagreements_text.splitlines()
    assert tuple(disagreement_lines[0].split("\t")) == DISAGREEMENT_COLUMNS
    assert "external_reviewer_signal=not_available" in disagreements_text
    assert "\tnot_available\t" in disagreements_text
    report_text = outputs.report_md.read_text(encoding="utf-8")
    assert "External Reviewer Signal" in report_text
    assert "Baseline Binding: no_baseline_supplied" in report_text
    assert "selected_value_drift: 0 (not baseline-compared)" in report_text


def test_product_gate_without_baseline_is_inconclusive(tmp_path: Path) -> None:
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        run_id="product_gate_no_baseline",
        generation_context="synthetic_current",
        gate_mode="product_gate",
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert packet["gate_mode"] == "product_gate"
    assert packet["run_ok"] is True
    assert packet["gate_ok"] is False
    assert packet["status"] == "INCONCLUSIVE"
    assert packet["validation_tier"] == "inconclusive"
    assert packet["baseline_binding"] == "no_baseline_supplied"
    assert packet["missing_evidence_code"] == "baseline_current_unbound"
    assert packet["manual_review_required"] is True
    assert packet["product_gate_eligible"] is False
    assert packet["authority_decision"] == "baseline_required_for_product_gate"
    assert "baseline-bound shadow gate" in packet["no_authority_statement"]
    summary_rows = _read_tsv(outputs.summary_tsv)
    selected_drift = {
        row["metric_name"]: row for row in summary_rows
    }["selected_value_drift"]
    assert selected_drift["status"] == "INCONCLUSIVE"
    assert selected_drift["direction"] == "unknown"


def test_product_gate_with_matching_baseline_is_shadow_ready(tmp_path: Path) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline", area="10")
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        baseline_alignment_dir=baseline,
        run_id="product_gate_matching_baseline",
        generation_context="synthetic_current",
        gate_mode="product-gate",
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert packet["gate_mode"] == "product_gate"
    assert packet["run_ok"] is True
    assert packet["gate_ok"] is True
    assert packet["status"] == "PASS"
    assert packet["validation_tier"] == "shadow_ready"
    assert packet["baseline_binding"] == "baseline_supplied"
    assert packet["missing_evidence_code"] == ""
    assert packet["product_gate_eligible"] is True
    assert packet["production_ready"] is False
    assert (
        packet["authority_decision"]
        == "shadow_gate_pass_no_control_plane_change"
    )
    assert "baseline-bound shadow gate" in packet["no_authority_statement"]


def test_daily_packet_marks_selected_value_drift_as_expected_diff_required(
    tmp_path: Path,
) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline", area="10")
    current = _write_alignment_artifacts(tmp_path / "current", area="12")
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        baseline_alignment_dir=baseline,
        run_id="synthetic_drift",
        generation_context="synthetic_current",
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert packet["status"] == "FAIL"
    assert packet["manual_review_required"] is True
    assert packet["production_safety"] == "inconclusive"
    assert packet["review_utility"] == "inconclusive"
    assert packet["baseline_binding"] == "baseline_supplied"
    assert packet["authority_decision"] == "expected_diff_required"
    assert "selected_value_drift" in outputs.summary_tsv.read_text(
        encoding="utf-8",
    )


def test_product_gate_marks_selected_value_drift_as_failed_gate(
    tmp_path: Path,
) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline", area="10")
    current = _write_alignment_artifacts(tmp_path / "current", area="12")
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        baseline_alignment_dir=baseline,
        run_id="product_gate_drift",
        generation_context="synthetic_current",
        gate_mode="product_gate",
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert packet["gate_ok"] is False
    assert packet["status"] == "FAIL"
    assert packet["validation_tier"] == "inconclusive"
    assert packet["manual_review_required"] is True
    assert packet["product_gate_eligible"] is False
    assert packet["missing_evidence_code"] == "product_gate_required"
    assert packet["authority_decision"] == "expected_diff_required"


def test_product_gate_fails_on_matrix_mz_rt_anchor_drift(tmp_path: Path) -> None:
    baseline = _write_alignment_artifacts(
        tmp_path / "baseline",
        area="10",
        mz="100.0",
        rt="5.0",
    )
    current = _write_alignment_artifacts(
        tmp_path / "current",
        area="10",
        mz="100.1",
        rt="5.0",
    )
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        baseline_alignment_dir=baseline,
        run_id="product_gate_mz_anchor_drift",
        generation_context="synthetic_current",
        gate_mode="product_gate",
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert packet["gate_ok"] is False
    assert packet["status"] == "FAIL"
    assert packet["validation_tier"] == "inconclusive"
    assert packet["product_gate_eligible"] is False
    assert packet["metrics"]["selected_value_drift"] > 0
    assert packet["authority_decision"] == "expected_diff_required"


def test_daily_packet_fails_on_baseline_row_count_mismatch(tmp_path: Path) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline", area="10")
    _append_matrix_row(baseline, area="11", family="FAM_EXTRA", peak_id="P2")
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        baseline_alignment_dir=baseline,
        run_id="row_count_drift",
        generation_context="synthetic_current",
    )

    _assert_expected_diff_required(outputs.summary_json)


def test_daily_packet_fails_on_baseline_sample_column_mismatch(
    tmp_path: Path,
) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline", area="10")
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    (current / "alignment_matrix.tsv").write_text(
        "Mz\tRT\tSampleA\tSampleB\n100\t5\t10\t\n",
        encoding="utf-8",
    )
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        baseline_alignment_dir=baseline,
        run_id="sample_column_drift",
        generation_context="synthetic_current",
    )

    _assert_expected_diff_required(outputs.summary_json)


def test_daily_packet_fails_on_baseline_identity_reorder(tmp_path: Path) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline", area="10")
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    _append_matrix_row(baseline, area="11", family="FAM_EXTRA", peak_id="P2")
    _append_matrix_row(current, area="11", family="FAM_EXTRA", peak_id="P2")
    (current / "alignment_matrix_identity.tsv").write_text(
        "peak_hypothesis_id\tmatrix_row_index\tsource_feature_family_ids\t"
        "evidence_status\n"
        "P2\t2\tFAM_EXTRA\tcomplete\n"
        "P1\t1\tFAM_BAD\tcomplete\n",
        encoding="utf-8",
    )
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        baseline_alignment_dir=baseline,
        run_id="identity_reorder_drift",
        generation_context="synthetic_current",
    )

    _assert_expected_diff_required(outputs.summary_json)


def test_daily_packet_fails_on_blank_nonblank_selected_cell_mismatch(
    tmp_path: Path,
) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline", area="10")
    current = _write_alignment_artifacts(tmp_path / "current", area="")
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        baseline_alignment_dir=baseline,
        run_id="blank_nonblank_drift",
        generation_context="synthetic_current",
    )

    _assert_expected_diff_required(outputs.summary_json)


@pytest.mark.parametrize(
    "packet",
    [
        [],
        {"summary_metrics": []},
        {"summary_metrics": {"row_flag_counts": []}},
    ],
)
def test_daily_packet_is_inconclusive_for_malformed_health_summary(
    tmp_path: Path,
    packet: object,
) -> None:
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    health = _write_health_packet(tmp_path / "health")
    (health / "alignment_health_summary.json").write_text(
        json.dumps(packet) + "\n",
        encoding="utf-8",
    )

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        run_id="malformed_health",
        generation_context="synthetic_current",
    )

    _assert_metric_source_unavailable(outputs.summary_json)


def test_daily_packet_is_inconclusive_for_nonnumeric_sentinel_rank(
    tmp_path: Path,
) -> None:
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    health = _write_health_packet(tmp_path / "health")
    (health / "alignment_health_family_sentinels.tsv").write_text(
        "rank\tfeature_family_id\tissue_class\tseverity_score\t"
        "recommended_action\treason\n"
        "not_numeric\tFAM_BAD\tduplicate_claim\t48\t"
        "inspect_owner_assignment\tduplicate only\n",
        encoding="utf-8",
    )

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        run_id="nonnumeric_sentinel_rank",
        generation_context="synthetic_current",
    )

    _assert_metric_source_unavailable(outputs.summary_json)


def test_daily_packet_fails_closed_when_backfill_evidence_is_unbound(
    tmp_path: Path,
) -> None:
    current = _write_alignment_artifacts(tmp_path / "current", area="10")
    (current / "alignment_backfill_cell_evidence.tsv").unlink()
    health = _write_health_packet(tmp_path / "health")

    outputs = build_daily_confidence_packet(
        current_alignment_dir=current,
        current_health_dir=health,
        output_dir=tmp_path / "out",
        run_id="missing_backfill",
        generation_context="synthetic_current",
    )

    packet = json.loads(outputs.summary_json.read_text(encoding="utf-8"))
    assert packet["run_ok"] is False
    assert packet["gate_ok"] is False
    assert packet["status"] == "INCONCLUSIVE"
    assert packet["manual_review_required"] is True
    assert packet["production_safety"] == "inconclusive"
    assert packet["review_utility"] == "inconclusive"
    assert packet["missing_evidence_code"] == "metric_source_unavailable"


def _assert_expected_diff_required(summary_json: Path) -> None:
    packet = json.loads(summary_json.read_text(encoding="utf-8"))
    assert packet["status"] == "FAIL"
    assert packet["manual_review_required"] is True
    assert packet["production_safety"] == "inconclusive"
    assert packet["review_utility"] == "inconclusive"
    assert packet["authority_decision"] == "expected_diff_required"
    assert packet["metrics"]["selected_value_drift"] > 0


def _assert_metric_source_unavailable(summary_json: Path) -> None:
    packet = json.loads(summary_json.read_text(encoding="utf-8"))
    assert packet["run_ok"] is False
    assert packet["gate_ok"] is False
    assert packet["status"] == "INCONCLUSIVE"
    assert packet["manual_review_required"] is True
    assert packet["production_safety"] == "inconclusive"
    assert packet["review_utility"] == "inconclusive"
    assert packet["missing_evidence_code"] == "metric_source_unavailable"


def _read_tsv(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    return [
        dict(zip(header, line.split("\t"), strict=False))
        for line in lines[1:]
    ]


def _append_matrix_row(path: Path, *, area: str, family: str, peak_id: str) -> None:
    with (path / "alignment_matrix.tsv").open("a", encoding="utf-8") as handle:
        handle.write(f"101\t6\t{area}\n")
    with (path / "alignment_matrix_identity.tsv").open("a", encoding="utf-8") as handle:
        handle.write(f"{peak_id}\t2\t{family}\tcomplete\n")


def _write_alignment_artifacts(
    path: Path,
    *,
    area: str,
    mz: str = "100",
    rt: str = "5",
) -> Path:
    path.mkdir()
    (path / "alignment_matrix.tsv").write_text(
        "Mz\tRT\tSampleA\n" + mz + "\t" + rt + "\t" + area + "\n",
        encoding="utf-8",
    )
    (path / "alignment_matrix_identity.tsv").write_text(
        "peak_hypothesis_id\tmatrix_row_index\tsource_feature_family_ids\t"
        "evidence_status\nP1\t1\tFAM_BAD\tcomplete\n",
        encoding="utf-8",
    )
    (path / "alignment_review.tsv").write_text(
        "feature_family_id\tdetected_count\tambiguous_ms1_owner_count\t"
        "duplicate_assigned_count\tunchecked_count\taccepted_cell_count\t"
        "accepted_rescue_count\treview_rescue_count\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\trow_flags\treason\n"
        "FAM_BAD\t0\t0\t2\t0\t0\t0\t1\taudit_family\treview\towner\t"
        "duplicate_only;duplicate_claim_pressure\tduplicate only\n",
        encoding="utf-8",
    )
    (path / "alignment_backfill_cell_evidence.tsv").write_text(
        "schema_version\tfeature_family_id\tsample_stem\tstatus\t"
        "bounded_by_alignment_row\treason\n"
        "alignment_backfill_cell_evidence\tFAM_BAD\tSampleA\t"
        "review_only\ttrue\tduplicate only\n",
        encoding="utf-8",
    )
    (path / "alignment_owner_backfill_seed_audit.tsv").write_text(
        "schema_version\tfeature_family_id\tsample_stem\tstatus\t"
        "seed_source\treason\n"
        "alignment_owner_backfill_seed_audit\tFAM_BAD\tSampleA\t"
        "review_only\towner\tduplicate only\n",
        encoding="utf-8",
    )
    return path


def _write_health_packet(path: Path) -> Path:
    path.mkdir()
    packet = {
        "schema_version": "alignment_health_packet",
        "summary_metrics": {
            "row_flag_counts": {
                "duplicate_only": 1,
                "zero_present": 0,
                "high_backfill_dependency": 0,
            },
            "accepted_rescue_count_total": 0,
            "review_rescue_count_total": 1,
            "sentinel_count": 1,
        },
        "sentinel_rows": [
            {
                "rank": 1,
                "feature_family_id": "FAM_BAD",
                "issue_class": "duplicate_claim",
                "severity_score": 48,
                "recommended_action": "inspect_owner_assignment",
                "reason": "duplicate only",
            },
        ],
        "status": "diagnostic_only",
    }
    (path / "alignment_health_summary.json").write_text(
        json.dumps(packet, indent=2) + "\n",
        encoding="utf-8",
    )
    (path / "alignment_health_family_sentinels.tsv").write_text(
        "rank\tfeature_family_id\tissue_class\tseverity_score\t"
        "recommended_action\treason\n"
        "1\tFAM_BAD\tduplicate_claim\t48\tinspect_owner_assignment\t"
        "duplicate only\n",
        encoding="utf-8",
    )
    return path
