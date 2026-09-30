import json
import subprocess
import sys
from pathlib import Path

import scripts.build_quant_matrix_promotion_packet as packet_module
from scripts.build_quant_matrix_promotion_packet import (
    build_quant_matrix_promotion_packet,
    validate_quant_matrix_promotion_packet,
)
from scripts.build_quant_matrix_real_bundle import build_quant_matrix_real_bundle
from tests.test_quant_matrix_real_bundle import _write_source_run_fixture
from xic_extractor.tabular_io import file_sha256

ROOT = Path(__file__).resolve().parents[1]


def test_promotion_packet_builds_real_bundle_ready_candidate(
    tmp_path: Path,
) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    real_bundle = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    sources = _write_science_sources(tmp_path)

    outputs = build_quant_matrix_promotion_packet(
        output_dir=tmp_path / "packet",
        source_root=tmp_path,
        real_bundle_summary_json=real_bundle["summary_json"],
        large_cohort_artifact=sources["large"],
        heldout_oracle_artifact=sources["oracle"],
        cohort_id="synthetic_85raw",
        oracle_packet_id="synthetic_oracle",
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert (
        validate_quant_matrix_promotion_packet(
            summary_json=outputs["summary_json"],
            source_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["readiness_label"] == "production_ready_candidate_packet"
    assert summary["production_ready"] is True
    assert summary["may_promote_default_quant_matrix"] is True
    assert summary["write_authority"] is False
    assert summary["default_quant_matrix_changed"] is False
    assert summary["raw_or_full_cohort_ran"] is False
    assert summary["validation_tiers"] == {
        "full_cohort": "pass",
        "heldout_oracle": "pass",
        "downstream_impact_smoke": "pass",
    }
    assert summary["missing_science_evidence"] == []

    evidence = json.loads(
        outputs["validation_evidence_json"].read_text(encoding="utf-8")
    )
    downstream_row = next(
        row for row in evidence["evidence"] if row["tier"] == "downstream_impact_smoke"
    )
    copied_downstream = outputs["validation_evidence_json"].parent / downstream_row[
        "artifact_path"
    ]
    copied_downstream_payload = json.loads(
        copied_downstream.read_text(encoding="utf-8"),
    )
    copied_downstream_rows = (
        copied_downstream.parent / copied_downstream_payload["row_metrics_tsv"]
    )
    assert copied_downstream_rows.is_file()
    assert (
        summary["artifacts"]["real_bundle_readiness_summary_json"]["sha256"]
        == file_sha256(outputs["real_bundle_readiness_summary_json"])
    )


def test_promotion_packet_rejects_stale_real_bundle_readiness(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_packet(tmp_path)
    readiness_summary = outputs["real_bundle_readiness_summary_json"]
    payload = json.loads(readiness_summary.read_text(encoding="utf-8"))
    payload["production_ready"] = False
    readiness_summary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_promotion_packet(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("readiness production_ready must be true" in p for p in problems)
    assert any("readiness summary is stale" in p for p in problems)


def test_promotion_packet_check_accepts_externalized_real_bundle_cell_provenance(
    tmp_path: Path,
) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    real_bundle = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    sources = _write_science_sources(tmp_path)
    outputs = build_quant_matrix_promotion_packet(
        output_dir=tmp_path / "packet",
        source_root=tmp_path,
        real_bundle_summary_json=real_bundle["summary_json"],
        large_cohort_artifact=sources["large"],
        heldout_oracle_artifact=sources["oracle"],
        cohort_id="synthetic_85raw",
        oracle_packet_id="synthetic_oracle",
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    real_bundle["cell_provenance"].unlink()

    assert (
        validate_quant_matrix_promotion_packet(
            summary_json=outputs["summary_json"],
            source_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )


def test_promotion_packet_rejects_externalized_provenance_readiness_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    real_bundle = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    sources = _write_science_sources(tmp_path)
    outputs = build_quant_matrix_promotion_packet(
        output_dir=tmp_path / "packet",
        source_root=tmp_path,
        real_bundle_summary_json=real_bundle["summary_json"],
        large_cohort_artifact=sources["large"],
        heldout_oracle_artifact=sources["oracle"],
        cohort_id="synthetic_85raw",
        oracle_packet_id="synthetic_oracle",
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    real_bundle["cell_provenance"].unlink()
    original_evaluate = packet_module.evaluate_quant_matrix_promotion_readiness

    def drifted_evaluate(*args, **kwargs):
        result = dict(original_evaluate(*args, **kwargs))
        summary = json.loads(result["summary_json"].read_text(encoding="utf-8"))
        summary["production_ready"] = False
        result["summary_json"].write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(
        packet_module,
        "evaluate_quant_matrix_promotion_readiness",
        drifted_evaluate,
    )

    problems = validate_quant_matrix_promotion_packet(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("readiness summary is stale" in problem for problem in problems)


def test_promotion_packet_default_check_rejects_non_current_511_bundle(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_packet(tmp_path)

    problems = validate_quant_matrix_promotion_packet(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
    )

    assert any("source_run_id mismatch" in problem for problem in problems)
    assert any("downstream_scope mismatch" in problem for problem in problems)
    assert any("accepted_backfill_count mismatch" in problem for problem in problems)


def test_promotion_packet_script_check_only_round_trip(tmp_path: Path) -> None:
    fixture = _write_source_run_fixture(tmp_path)
    real_bundle = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    sources = _write_science_sources(tmp_path)
    output_dir = tmp_path / "packet"

    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_promotion_packet.py",
            "--output-dir",
            str(output_dir),
            "--source-root",
            str(tmp_path),
            "--real-bundle-summary-json",
            str(real_bundle["summary_json"]),
            "--large-cohort-artifact",
            str(sources["large"]),
            "--heldout-oracle-artifact",
            str(sources["oracle"]),
            "--cohort-id",
            "synthetic_85raw",
            "--oracle-packet-id",
            "synthetic_oracle",
            "--expected-source-run-id",
            "synthetic-current-511-authority-replay",
            "--expected-downstream-scope",
            "synthetic_current_authority_replay",
            "--expected-accepted-backfill-count",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr

    check = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_promotion_packet.py",
            "--output-dir",
            str(output_dir),
            "--source-root",
            str(tmp_path),
            "--check-only",
            "--expected-source-run-id",
            "synthetic-current-511-authority-replay",
            "--expected-downstream-scope",
            "synthetic_current_authority_replay",
            "--expected-accepted-backfill-count",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert check.returncode == 0, check.stderr
    assert "promotion_packet_status: pass" in check.stdout


def _build_synthetic_packet(tmp_path: Path) -> dict[str, Path]:
    fixture = _write_source_run_fixture(tmp_path)
    real_bundle = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    sources = _write_science_sources(tmp_path)
    return dict(
        build_quant_matrix_promotion_packet(
            output_dir=tmp_path / "packet",
            source_root=tmp_path,
            real_bundle_summary_json=real_bundle["summary_json"],
            large_cohort_artifact=sources["large"],
            heldout_oracle_artifact=sources["oracle"],
            cohort_id="synthetic_85raw",
            oracle_packet_id="synthetic_oracle",
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
    )


def _write_science_sources(root: Path) -> dict[str, Path]:
    source_dir = root / "science_sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    large = source_dir / "large_cohort_summary.json"
    oracle = source_dir / "heldout_oracle_summary.json"
    large.write_text('{"large_cohort": true}\n', encoding="utf-8")
    oracle.write_text('{"heldout_oracle": true}\n', encoding="utf-8")
    return {"large": large, "oracle": oracle}
