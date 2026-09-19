import json
import subprocess
import sys
from pathlib import Path

from scripts.build_quant_matrix_default_activation_dry_run import (
    build_quant_matrix_default_activation_dry_run,
)
from scripts.build_quant_matrix_product_ready_closeout import (
    CHECK_COLUMNS,
    PRODUCT_READY_CLOSEOUT_CHECK_SCHEMA,
    PRODUCT_READY_CLOSEOUT_SCHEMA,
    build_quant_matrix_product_ready_closeout,
    validate_quant_matrix_product_ready_closeout,
)
from tests.test_quant_matrix_default_activation_dry_run import (
    _build_synthetic_packet,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "docs/superpowers/schemas/"
    / "quant_matrix_product_ready_closeout_schema.json"
)


def test_product_ready_closeout_schema_matches_builder_contract() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["schema_version"] == "quant_matrix_product_ready_closeout_schema"
    assert schema["product_ready_closeout_schema"] == PRODUCT_READY_CLOSEOUT_SCHEMA
    assert schema["check_schema"] == PRODUCT_READY_CLOSEOUT_CHECK_SCHEMA
    assert schema["check_columns"] == list(CHECK_COLUMNS)
    assert schema["binding_rules"]["default_activation_dry_run_must_validate"] is True
    assert (
        schema["binding_rules"][
            "default_activation_dry_run_supplies_the_validated_promotion_packet_predecessor"
        ]
        is True
    )
    assert schema["binding_rules"]["checks_tsv_must_match_inputs"] is True
    assert schema["authority_rules"]["write_authority"] is False
    assert schema["authority_rules"]["product_writer_changed"] is False
    assert schema["authority_rules"]["default_quant_matrix_changed"] is False
    assert schema["authority_rules"]["default_matrix_files_written"] is False
    assert schema["authority_rules"]["requires_explicit_activation_commit"] is True


def test_product_ready_closeout_builds_candidate_from_promotion_and_replay(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_closeout(tmp_path)

    assert (
        validate_quant_matrix_product_ready_closeout(
            summary_json=outputs["summary_json"],
            source_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["closeout_label"] == "product_ready_default_matrix_candidate"
    assert summary["product_ready_candidate"] is True
    assert summary["default_quant_matrix_product_ready_candidate"] is True
    assert summary["may_activate_default_quant_matrix_with_explicit_contract"] is True
    assert summary["requires_product_writer_activation_commit"] is True
    assert summary["explicit_activation_not_in_this_commit"] is True
    assert summary["promotion_packet_readiness_label"] == (
        "production_ready_candidate_packet"
    )
    assert summary["dry_run_gate_status"] == "pass"
    assert summary["dry_run_expected_diff_count"] == "1"
    assert summary["dry_run_written_backfill_count"] == "1"
    assert summary["dry_run_unused_expected_diff_count"] == "0"
    assert summary["write_authority"] is False
    assert summary["product_writer_changed"] is False
    assert summary["default_quant_matrix_changed"] is False
    assert summary["default_matrix_files_written"] is False


def test_product_ready_closeout_rejects_stale_checks_tsv(tmp_path: Path) -> None:
    outputs = _build_synthetic_closeout(tmp_path)
    checks = outputs["checks_tsv"]
    checks.write_text(
        checks.read_text(encoding="utf-8").replace(
            "authority_unchanged\t",
            "authority_unchanged_tampered\t",
            1,
        ),
        encoding="utf-8",
    )

    problems = validate_quant_matrix_product_ready_closeout(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("checks_tsv sha256 mismatch" in problem for problem in problems)
    assert any("checks TSV is stale" in problem for problem in problems)


def test_product_ready_closeout_rejects_tampered_dry_run_input(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_closeout(tmp_path)
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    dry_run = tmp_path / summary["input_artifacts"]["activation_dry_run_summary_json"]
    dry_run_payload = json.loads(dry_run.read_text(encoding="utf-8"))
    dry_run_payload["may_enter_product_ready_closeout"] = False
    dry_run.write_text(
        json.dumps(dry_run_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_product_ready_closeout(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("activation_dry_run_summary_json_sha256 mismatch" in p for p in problems)
    assert any("may_enter_product_ready_closeout must be true" in p for p in problems)


def test_product_ready_closeout_rejects_tampered_promotion_packet_transitively(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_closeout(tmp_path)
    packet = outputs["packet_summary"]
    packet_payload = json.loads(packet.read_text(encoding="utf-8"))
    packet_payload["production_ready"] = False
    packet.write_text(
        json.dumps(packet_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_product_ready_closeout(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any(
        "default activation dry-run promotion_packet_summary_json_sha256 mismatch"
        in problem
        for problem in problems
    )


def test_product_ready_closeout_default_check_rejects_non_current_bundle(
    tmp_path: Path,
) -> None:
    outputs = _build_synthetic_closeout(tmp_path)

    problems = validate_quant_matrix_product_ready_closeout(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
    )

    assert any("source_run_id mismatch" in problem for problem in problems)
    assert any("downstream_scope mismatch" in problem for problem in problems)
    assert any("accepted_backfill_count mismatch" in problem for problem in problems)


def test_product_ready_closeout_script_check_only_round_trip(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_promotion_and_replay(tmp_path)
    output_dir = tmp_path / "product_ready_closeout"
    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_product_ready_closeout.py",
            "--output-dir",
            str(output_dir),
            "--source-root",
            str(tmp_path),
            "--activation-dry-run-summary-json",
            str(inputs["dry_run_summary"]),
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
            "scripts/build_quant_matrix_product_ready_closeout.py",
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
    assert "product_ready_closeout_status: pass" in check.stdout


def _build_synthetic_closeout(tmp_path: Path) -> dict[str, Path]:
    inputs = _build_synthetic_promotion_and_replay(tmp_path)
    outputs = dict(
        build_quant_matrix_product_ready_closeout(
            output_dir=tmp_path / "product_ready_closeout",
            source_root=tmp_path,
            activation_dry_run_summary_json=inputs["dry_run_summary"],
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
    )
    outputs.update(inputs)
    return outputs


def _build_synthetic_promotion_and_replay(tmp_path: Path) -> dict[str, Path]:
    inputs = _build_synthetic_packet(tmp_path)
    dry_run = build_quant_matrix_default_activation_dry_run(
        output_dir=tmp_path / "dry_run_gate",
        source_root=tmp_path,
        promotion_packet_summary_json=inputs["packet_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    return {
        "packet_summary": inputs["packet_summary"],
        "dry_run_summary": dry_run["summary_json"],
    }
