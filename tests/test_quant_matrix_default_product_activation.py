import json
import subprocess
import sys
from pathlib import Path

import scripts.build_quant_matrix_default_product_activation as activation_module
from scripts.build_quant_matrix_default_activation_dry_run import (
    build_quant_matrix_default_activation_dry_run,
)
from scripts.build_quant_matrix_default_product_activation import (
    build_quant_matrix_default_product_activation,
    validate_quant_matrix_default_product_activation,
)
from scripts.build_quant_matrix_product_ready_closeout import (
    build_quant_matrix_product_ready_closeout,
)
from tests.test_quant_matrix_default_activation_dry_run import (
    _build_synthetic_packet,
)


def test_default_product_activation_writes_default_outputs(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_activation_inputs(tmp_path)

    outputs = build_quant_matrix_default_product_activation(
        output_dir=tmp_path / "default_activation",
        source_root=tmp_path,
        product_ready_closeout_summary_json=inputs["closeout_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert (
        validate_quant_matrix_default_product_activation(
            summary_json=outputs["summary_json"],
            source_root=tmp_path,
            expected_source_run_id="synthetic-current-511-authority-replay",
            expected_downstream_scope="synthetic_current_authority_replay",
            expected_accepted_backfill_count=1,
        )
        == []
    )

    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    assert summary["activation_label"] == "product_ready_default_matrix_activated"
    assert summary["accepted_backfill_count"] == 1
    assert summary["expected_diff_count"] == "1"
    assert summary["written_backfill_count"] == "1"
    assert summary["unused_expected_diff_count"] == "0"
    assert summary["detected_cell_count"] == "1"
    assert summary["accepted_backfill_cell_count"] == "1"
    assert summary["quant_available_cell_count"] == "2"
    assert summary["write_authority"] is True
    assert summary["product_writer_changed"] is True
    assert summary["default_quant_matrix_changed"] is True
    assert summary["default_matrix_files_written"] is True
    assert summary["workbook_or_gui_changed"] is False
    assert summary["selected_peak_area_or_counting_changed"] is False
    assert summary["artifacts"]["cell_provenance"]["retention_decision"] == (
        "externalize"
    )
    assert summary["artifacts"]["cell_provenance_summary"]["retention_decision"] == (
        "keep_summary"
    )
    assert (
        summary["artifacts"]["cell_provenance_minimal_fixture"]["retention_decision"]
        == "keep_minimal_fixture"
    )
    assert outputs["cell_provenance_summary_json"].is_file()
    assert outputs["cell_provenance_minimal_fixture"].is_file()

    matrix_rows = _read_tsv(outputs["quant_matrix"])
    assert matrix_rows == [
        {
            "Mz": "101.1",
            "RT": "5.5",
            "SampleA": "100",
            "SampleB": "222.2",
            "SampleC": "",
        },
    ]
    provenance_rows = _read_tsv(outputs["cell_provenance"])
    assert [row["cell_status"] for row in provenance_rows] == [
        "detected",
        "accepted_backfill",
    ]
    assert provenance_rows[1]["write_authority"] == "TRUE"
    assert provenance_rows[1]["truth_status"] == "not_truth_claimed"
    source_summary = outputs["source_summary"].read_text(encoding="utf-8")
    assert str(tmp_path) not in source_summary
    assert "../" in source_summary


def test_default_product_activation_rejects_tampered_default_matrix(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_activation_inputs(tmp_path)
    outputs = build_quant_matrix_default_product_activation(
        output_dir=tmp_path / "default_activation",
        source_root=tmp_path,
        product_ready_closeout_summary_json=inputs["closeout_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    outputs["quant_matrix"].write_text(
        outputs["quant_matrix"]
        .read_text(encoding="utf-8")
        .replace(
            "222.2",
            "333.3",
        ),
        encoding="utf-8",
    )

    problems = validate_quant_matrix_default_product_activation(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any(
        "quant_matrix artifact sha256 mismatch" in problem for problem in problems
    )
    assert any(
        "quant_matrix does not match rerun activation" in problem
        for problem in problems
    )


def test_default_product_activation_rejects_tampered_sidecars(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_activation_inputs(tmp_path)
    outputs = build_quant_matrix_default_product_activation(
        output_dir=tmp_path / "default_activation",
        source_root=tmp_path,
        product_ready_closeout_summary_json=inputs["closeout_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    outputs["cell_provenance_minimal_fixture"].write_text(
        outputs["cell_provenance_minimal_fixture"]
        .read_text(encoding="utf-8")
        .replace("accepted_backfill", "detected", 1),
        encoding="utf-8",
    )
    outputs["checks_tsv"].write_text(
        outputs["checks_tsv"]
        .read_text(encoding="utf-8")
        .replace(
            "expected_diff_closed",
            "expected_diff_closed_stale",
            1,
        ),
        encoding="utf-8",
    )
    summary = json.loads(outputs["summary_json"].read_text(encoding="utf-8"))
    summary["accepted_backfill_cell_count"] = "999"
    outputs["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_default_product_activation(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("cell_provenance summary" in p for p in problems)
    assert any("default product activation checks TSV is stale" in p for p in problems)
    assert any(
        "default product activation accepted_backfill_cell_count is stale" in p
        for p in problems
    )


def test_default_product_activation_rejects_missing_minimal_fixture(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_activation_inputs(tmp_path)
    outputs = build_quant_matrix_default_product_activation(
        output_dir=tmp_path / "default_activation",
        source_root=tmp_path,
        product_ready_closeout_summary_json=inputs["closeout_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    outputs["cell_provenance_minimal_fixture"].unlink()

    problems = validate_quant_matrix_default_product_activation(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any("cell_provenance_minimal_fixture" in p for p in problems)


def test_default_product_activation_rejects_tampered_dry_run_transitively(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_activation_inputs(tmp_path)
    outputs = build_quant_matrix_default_product_activation(
        output_dir=tmp_path / "default_activation",
        source_root=tmp_path,
        product_ready_closeout_summary_json=inputs["closeout_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    closeout_payload = json.loads(
        inputs["closeout_summary"].read_text(encoding="utf-8")
    )
    dry_run_summary = (
        tmp_path
        / closeout_payload["input_artifacts"]["activation_dry_run_summary_json"]
    )
    dry_run_payload = json.loads(dry_run_summary.read_text(encoding="utf-8"))
    dry_run_payload["may_enter_product_ready_closeout"] = False
    dry_run_summary.write_text(
        json.dumps(dry_run_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    problems = validate_quant_matrix_default_product_activation(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any(
        "product ready closeout activation_dry_run_summary_json_sha256 mismatch"
        in problem
        for problem in problems
    )


def test_default_product_activation_rejects_provenance_rerun_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    inputs = _build_synthetic_activation_inputs(tmp_path)
    outputs = build_quant_matrix_default_product_activation(
        output_dir=tmp_path / "default_activation",
        source_root=tmp_path,
        product_ready_closeout_summary_json=inputs["closeout_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    original_run_activation = activation_module.run_activation

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

    monkeypatch.setattr(activation_module, "run_activation", drifted_run_activation)

    problems = validate_quant_matrix_default_product_activation(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    assert any(
        "cell_provenance does not match rerun activation" in problem
        for problem in problems
    )


def test_default_product_activation_default_check_rejects_non_current_bundle(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_activation_inputs(tmp_path)
    outputs = build_quant_matrix_default_product_activation(
        output_dir=tmp_path / "default_activation",
        source_root=tmp_path,
        product_ready_closeout_summary_json=inputs["closeout_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )

    problems = validate_quant_matrix_default_product_activation(
        summary_json=outputs["summary_json"],
        source_root=tmp_path,
    )

    assert any("source_run_id mismatch" in problem for problem in problems)
    assert any("downstream_scope mismatch" in problem for problem in problems)
    assert any("accepted_backfill_count mismatch" in problem for problem in problems)


def test_default_product_activation_script_check_only_round_trip(
    tmp_path: Path,
) -> None:
    inputs = _build_synthetic_activation_inputs(tmp_path)
    output_dir = tmp_path / "default_activation"
    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_quant_matrix_default_product_activation.py",
            "--output-dir",
            str(output_dir),
            "--source-root",
            str(tmp_path),
            "--product-ready-closeout-summary-json",
            str(inputs["closeout_summary"]),
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
            "scripts/build_quant_matrix_default_product_activation.py",
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
    assert "default_product_activation_status: pass" in check.stdout


def _build_synthetic_activation_inputs(tmp_path: Path) -> dict[str, Path]:
    packet_inputs = _build_synthetic_packet(tmp_path)
    dry_run = build_quant_matrix_default_activation_dry_run(
        output_dir=tmp_path / "dry_run_gate",
        source_root=tmp_path,
        promotion_packet_summary_json=packet_inputs["packet_summary"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    closeout = build_quant_matrix_product_ready_closeout(
        output_dir=tmp_path / "product_ready_closeout",
        source_root=tmp_path,
        activation_dry_run_summary_json=dry_run["summary_json"],
        expected_source_run_id="synthetic-current-511-authority-replay",
        expected_downstream_scope="synthetic_current_authority_replay",
        expected_accepted_backfill_count=1,
    )
    return {
        "real_bundle_summary": packet_inputs["real_bundle_summary"],
        "closeout_summary": closeout["summary_json"],
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
