from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.diagnostics import row_completion_confidence as cli


def test_cli_writes_row_completion_outputs(tmp_path: Path) -> None:
    current = _write_alignment_artifacts(tmp_path / "current")
    health = _write_health_packet(tmp_path / "health")
    output = tmp_path / "out"

    code = cli.main(
        [
            "--current-alignment-dir",
            str(current),
            "--current-health-dir",
            str(health),
            "--output-dir",
            str(output),
            "--run-id",
            "cli_synthetic",
            "--generation-context",
            "synthetic_cli",
        ],
    )

    assert code == 0
    assert (output / "row_completion_confidence_summary.json").is_file()
    assert (output / "row_completion_sentinels.tsv").is_file()
    assert (output / "row_completion_family_sentinels.tsv").is_file()
    assert (
        output / "row_completion_family_sentinels.tsv"
    ).read_text(encoding="utf-8") == (
        output / "row_completion_sentinels.tsv"
    ).read_text(encoding="utf-8")
    packet = json.loads(
        (output / "row_completion_confidence_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert packet["run_id"] == "cli_synthetic"
    assert packet["gate_mode"] == "diagnostic"
    assert packet["status"] == "PASS"
    assert packet["manual_review_required"] is False
    assert packet["production_safety"] == "stable"
    assert packet["review_utility"] == "stable"
    assert packet["authority_decision"] == "no_control_plane_change"


def test_cli_product_gate_requires_baseline(tmp_path: Path) -> None:
    current = _write_alignment_artifacts(tmp_path / "current")
    health = _write_health_packet(tmp_path / "health")
    output = tmp_path / "out"

    code = cli.main(
        [
            "--current-alignment-dir",
            str(current),
            "--current-health-dir",
            str(health),
            "--output-dir",
            str(output),
            "--run-id",
            "cli_product_gate",
            "--generation-context",
            "synthetic_cli",
            "--gate-mode",
            "product-gate",
        ],
    )

    assert code == 1
    packet = json.loads(
        (output / "row_completion_confidence_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert packet["gate_mode"] == "product_gate"
    assert packet["run_ok"] is True
    assert packet["gate_ok"] is False
    assert packet["status"] == "INCONCLUSIVE"
    assert packet["validation_tier"] == "inconclusive"
    assert packet["missing_evidence_code"] == "baseline_current_unbound"
    assert packet["authority_decision"] == "baseline_required_for_product_gate"


def test_cli_product_gate_passes_with_matching_baseline(tmp_path: Path) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline")
    current = _write_alignment_artifacts(tmp_path / "current")
    health = _write_health_packet(tmp_path / "health")
    output = tmp_path / "out"

    code = cli.main(
        [
            "--current-alignment-dir",
            str(current),
            "--current-health-dir",
            str(health),
            "--baseline-alignment-dir",
            str(baseline),
            "--output-dir",
            str(output),
            "--run-id",
            "cli_product_gate_pass",
            "--generation-context",
            "synthetic_cli",
            "--gate-mode",
            "product-gate",
        ],
    )

    assert code == 0
    packet = json.loads(
        (output / "row_completion_confidence_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert packet["gate_mode"] == "product_gate"
    assert packet["gate_ok"] is True
    assert packet["validation_tier"] == "shadow_ready"
    assert packet["product_gate_eligible"] is True


@pytest.mark.parametrize(
    ("current_area", "current_mz"),
    [
        ("12", "100"),
        ("10", "100.1"),
    ],
)
def test_cli_product_gate_returns_one_for_drift(
    tmp_path: Path,
    current_area: str,
    current_mz: str,
) -> None:
    baseline = _write_alignment_artifacts(tmp_path / "baseline")
    current = _write_alignment_artifacts(
        tmp_path / "current",
        area=current_area,
        mz=current_mz,
    )
    health = _write_health_packet(tmp_path / "health")
    output = tmp_path / "out"

    code = cli.main(
        [
            "--current-alignment-dir",
            str(current),
            "--current-health-dir",
            str(health),
            "--baseline-alignment-dir",
            str(baseline),
            "--output-dir",
            str(output),
            "--run-id",
            "cli_product_gate_drift",
            "--generation-context",
            "synthetic_cli",
            "--gate-mode",
            "product-gate",
        ],
    )

    assert code == 1
    packet = json.loads(
        (output / "row_completion_confidence_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert packet["gate_ok"] is False
    assert packet["status"] == "FAIL"
    assert packet["product_gate_eligible"] is False


def test_cli_packetizes_missing_required_evidence(tmp_path: Path) -> None:
    current = _write_alignment_artifacts(tmp_path / "current")
    (current / "alignment_owner_backfill_seed_audit.tsv").unlink()
    health = _write_health_packet(tmp_path / "health")
    output = tmp_path / "out"

    code = cli.main(
        [
            "--current-alignment-dir",
            str(current),
            "--current-health-dir",
            str(health),
            "--output-dir",
            str(output),
            "--run-id",
            "cli_missing_evidence",
            "--generation-context",
            "synthetic_cli",
        ],
    )

    assert code == 0
    summary_lines = (
        output / "row_completion_confidence_summary.tsv"
    ).read_text(encoding="utf-8").splitlines()
    summary_header = summary_lines[0].split("\t")
    summary_row = dict(zip(summary_header, summary_lines[1].split("\t"), strict=False))
    packet = json.loads(
        (output / "row_completion_confidence_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert packet["run_ok"] is False
    assert packet["gate_ok"] is False
    assert packet["status"] == "INCONCLUSIVE"
    assert packet["manual_review_required"] is True
    assert packet["production_safety"] == "inconclusive"
    assert packet["review_utility"] == "inconclusive"
    assert packet["missing_evidence_code"] == "metric_source_unavailable"
    assert summary_row["status"] == "INCONCLUSIVE"


def test_cli_returns_two_for_output_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _write_alignment_artifacts(tmp_path / "current")
    health = _write_health_packet(tmp_path / "health")
    output = tmp_path / "out"
    output_summary = output / "row_completion_confidence_summary.json"

    def _raise_output_write_failure(**_: object) -> object:
        raise PermissionError(13, "disk full", str(output_summary))

    monkeypatch.setattr(
        cli,
        "build_daily_confidence_packet",
        _raise_output_write_failure,
    )

    code = cli.main(
        [
            "--current-alignment-dir",
            str(current),
            "--current-health-dir",
            str(health),
            "--output-dir",
            str(output),
            "--run-id",
            "cli_write_failure",
            "--generation-context",
            "synthetic_cli",
        ],
    )

    assert code == 2


def test_cli_propagates_input_side_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _write_alignment_artifacts(tmp_path / "current")
    health = _write_health_packet(tmp_path / "health")
    output = tmp_path / "out"

    def _raise_input_side_failure(**_: object) -> object:
        raise FileNotFoundError(
            2,
            "missing evidence",
            str(current / "alignment_matrix.tsv"),
        )

    monkeypatch.setattr(
        cli,
        "build_daily_confidence_packet",
        _raise_input_side_failure,
    )

    with pytest.raises(FileNotFoundError):
        cli.main(
            [
                "--current-alignment-dir",
                str(current),
                "--current-health-dir",
                str(health),
                "--output-dir",
                str(output),
                "--run-id",
                "cli_input_failure",
                "--generation-context",
                "synthetic_cli",
            ],
        )


def _write_alignment_artifacts(
    path: Path,
    *,
    area: str = "10",
    mz: str = "100",
) -> Path:
    path.mkdir()
    (path / "alignment_matrix.tsv").write_text(
        "Mz\tRT\tSampleA\n" + mz + "\t5\t" + area + "\n",
        encoding="utf-8",
    )
    (path / "alignment_matrix_identity.tsv").write_text(
        "peak_hypothesis_id\tmatrix_row_index\tsource_feature_family_ids\t"
        "evidence_status\nP1\t1\tFAM1\tcomplete\n",
        encoding="utf-8",
    )
    (path / "alignment_review.tsv").write_text(
        "feature_family_id\tdetected_count\tambiguous_ms1_owner_count\t"
        "duplicate_assigned_count\tunchecked_count\taccepted_cell_count\t"
        "accepted_backfill_count\treview_backfill_count\tidentity_decision\t"
        "identity_confidence\tprimary_evidence\trow_flags\treason\n"
        "FAM1\t1\t0\t0\t0\t1\t0\t0\tproduction_family\thigh\towner\t\tok\n",
        encoding="utf-8",
    )
    (path / "alignment_backfill_cell_evidence.tsv").write_text(
        "schema_version\tfeature_family_id\tsample_stem\tstatus\t"
        "bounded_by_alignment_row\treason\n"
        "alignment_backfill_cell_evidence\tFAM1\tSampleA\t"
        "not_needed\ttrue\tok\n",
        encoding="utf-8",
    )
    (path / "alignment_owner_backfill_seed_audit.tsv").write_text(
        "schema_version\tfeature_family_id\tsample_stem\tstatus\t"
        "seed_source\treason\n"
        "alignment_owner_backfill_seed_audit\tFAM1\tSampleA\t"
        "not_needed\towner\tok\n",
        encoding="utf-8",
    )
    return path


def _write_health_packet(path: Path) -> Path:
    path.mkdir()
    packet = {
        "schema_version": "alignment_health_packet",
        "summary_metrics": {
            "row_flag_counts": {},
            "accepted_backfill_count_total": 0,
            "review_backfill_count_total": 0,
            "sentinel_count": 0,
        },
        "sentinel_rows": [],
        "status": "diagnostic_only",
    }
    (path / "alignment_health_summary.json").write_text(
        json.dumps(packet, indent=2) + "\n",
        encoding="utf-8",
    )
    (path / "alignment_health_family_sentinels.tsv").write_text(
        "rank\tfeature_family_id\tissue_class\tseverity_score\t"
        "recommended_action\treason\n",
        encoding="utf-8",
    )
    return path
