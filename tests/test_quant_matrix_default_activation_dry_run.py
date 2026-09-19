import json
import subprocess
import sys
from pathlib import Path

import scripts.build_quant_matrix_default_activation_dry_run as dry_run_module
from scripts.build_quant_matrix_default_activation_dry_run import (
    ACTIVATION_DRY_RUN_COMPARISON_SCHEMA,
    COMPARISON_COLUMNS,
    DEFAULT_ACTIVATION_DRY_RUN_SCHEMA,
    build_quant_matrix_default_activation_dry_run,
    validate_quant_matrix_default_activation_dry_run,
)
from scripts.build_quant_matrix_promotion_packet import (
    build_quant_matrix_promotion_packet,
)
from scripts.build_quant_matrix_real_bundle import build_quant_matrix_real_bundle
from tests.test_quant_matrix_promotion_packet import _write_science_sources
from tests.test_quant_matrix_real_bundle import _write_source_run_fixture

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    / "quant_matrix_default_activation_dry_run_schema.json"
)


def test_default_activation_dry_run_schema_matches_builder_contract() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "quant_matrix_default_activation_dry_run_schema"
    assert schema["default_activation_dry_run_schema"] == (
        DEFAULT_ACTIVATION_DRY_RUN_SCHEMA
    )
    assert schema["comparison_schema"] == ACTIVATION_DRY_RUN_COMPARISON_SCHEMA
    assert schema["comparison_columns"] == list(COMPARISON_COLUMNS)
    assert schema["binding_rules"]["promotion_packet_must_validate"] is True
    assert (
        schema["binding_rules"][
            "promotion_packet_supplies_the_validated_real_bundle_predecessor"
        ]
        is True
    )
    assert schema["binding_rules"]["comparison_tsv_must_match_rerun"] is True
    assert schema["authority_rules"]["dry_run_only"] is True
    assert schema["authority_rules"]["write_authority"] is False
    assert schema["authority_rules"]["default_quant_matrix_changed"] is False
    assert schema["authority_rules"]["default_matrix_files_written"] is False


def test_default_activation_dry_run_replays_expected_diff_candidate(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_packet(tmp_path)

    outputs = build_quant_matrix_default_activation_dry_run(
        output_dir=tmp_path / "dry_run_gate",
        source_root=tmp_path,
        promotion_packet_summary_json=inputs["packet_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert (
        validate_quant_matrix_default_activation_dry_run(
            summary_json=outputs["summary_json"],
            source_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["default_activation_dry_run_gate_status"] == "pass"
    assert summary["promotion_packet_readiness_label"] == (
        "production_ready_candidate_packet"
    )
    assert summary["dry_run_expected_diff_count"] == "1"
    assert summary["dry_run_written_backfill_count"] == "1"
    assert summary["dry_run_unused_expected_diff_count"] == "0"
    assert summary["all_reference_outputs_match"] is True
    assert summary["may_enter_product_ready_closeout"] is True
    assert summary["dry_run_only"] is True
    assert summary["write_authority"] is False
    assert summary["default_quant_matrix_changed"] is False
    assert summary["default_matrix_files_written"] is False


def test_default_activation_dry_run_rejects_stale_comparison_tsv(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_dry_run(tmp_path)
    comparison = outputs["comparison_tsv"]
    comparison.write_text(
        comparison.read_text(encoding="utf-8").replace(
            "quant_matrix\t",
            "quant_matrix_tampered\t",
            1,
        ),
        encoding="utf-8",
    )

    problems = validate_quant_matrix_default_activation_dry_run(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("comparison_tsv sha256 mismatch" in problem for problem in problems)
    assert any("comparison TSV is stale" in problem for problem in problems)


def test_default_activation_dry_run_rejects_tampered_real_bundle_transitively(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_dry_run(tmp_path)
    bundle_summary = outputs["real_bundle_summary"]
    bundle_payload = json.loads(bundle_summary.read_text(encoding="utf-8"))
    quant_matrix = (
        bundle_summary.parent / bundle_payload["artifacts"]["quant_matrix"]["path"]
    )
    quant_matrix.write_text(
        quant_matrix.read_text(encoding="utf-8") + "tampered\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_default_activation_dry_run(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any(
        "promotion packet: real bundle:" in problem
        and "quant_matrix: sha256 mismatch" in problem
        for problem in problems
    )


def test_default_activation_dry_run_accepts_externalized_cell_provenance(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_dry_run(tmp_path)
    outputs["real_bundle_cell_provenance"].unlink()

    assert (
        validate_quant_matrix_default_activation_dry_run(
            summary_json=outputs["summary_json"],
            source_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )


def test_default_activation_dry_run_rejects_externalized_provenance_rerun_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    outputs = _build_synthetic_dry_run(tmp_path)
    outputs["real_bundle_cell_provenance"].unlink()
    original_run_activation = dry_run_module.run_activation

    def drifted_run_activation(*args, **kwargs):
        result = dict(original_run_activation(*args, **kwargs))
        provenance = result["cell_provenance"]
        provenance.write_text(
            provenance.read_text(encoding="utf-8").replace(
                "accepted_backfill",
                "detected",
                1,
            ),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(dry_run_module, "run_activation", drifted_run_activation)

    problems = validate_quant_matrix_default_activation_dry_run(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("cell_provenance" in problem for problem in problems)
    assert any("dry-run sha256 does not match reference" in p for p in problems)


def test_default_activation_dry_run_default_check_rejects_non_current_bundle(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_dry_run(tmp_path)

    problems = validate_quant_matrix_default_activation_dry_run(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
    )

    assert any("source_run_id mismatch" in problem for problem in problems)
    assert any("downstream_scope mismatch" in problem for problem in problems)
    assert any("accepted_backfill_count mismatch" in problem for problem in problems)


def test_default_activation_dry_run_script_check_only_round_trip(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_packet(tmp_path)
    output_dir = tmp_path / "dry_run_gate"
    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_default_activation_dry_run.py",
            "--output-dir",
            str(output_dir),
            "--source-root",
            str(tmp_path),
            "--promotion-packet-summary-json",
            str(inputs["packet_summary"]),
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
            "scripts/build_quant_matrix_default_activation_dry_run.py",
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
    assert "default_activation_dry_run_status: pass" in check.stdout


def _build_synthetic_dry_run(tmp_path: Path) -> dict[str, Path]:
    inputs = _build_synthetic_packet(tmp_path)
    outputs = dict(
        build_quant_matrix_default_activation_dry_run(
            output_dir=tmp_path / "dry_run_gate",
            source_root=tmp_path,
            promotion_packet_summary_json=inputs["packet_summary"],
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
    )
    outputs["real_bundle_cell_provenance"] = inputs["real_bundle_cell_provenance"]
    outputs["real_bundle_summary"] = inputs["real_bundle_summary"]
    return outputs


def _build_synthetic_packet(tmp_path: Path) -> dict[str, Path]:
    fixture = _write_source_run_fixture(tmp_path)
    real_bundle = build_quant_matrix_real_bundle(
        source_run_dir=fixture["source_run"],
        output_dir=fixture["output_dir"],
        repo_root=tmp_path,
        downstream_scope="synthetic_current_authority_replay",
    )
    sources = _write_science_sources(tmp_path)
    packet = build_quant_matrix_promotion_packet(
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
    return {
        "real_bundle_summary": real_bundle["summary_json"],
        "real_bundle_cell_provenance": real_bundle["cell_provenance"],
        "packet_summary": packet["summary_json"],
    }
