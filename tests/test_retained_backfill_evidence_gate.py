from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from tools.diagnostics import retained_backfill_evidence_gate as gate_cli
from xic_extractor.diagnostics import retained_backfill_evidence_gate as gate
from xic_extractor.diagnostics.retained_backfill_evidence_gate import (
    RETAINED_BACKFILL_EVIDENCE_GATE_COLUMNS,
)


def test_cli_writes_retained_product_backfill_gate_and_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    output_dir = tmp_path / "gate"
    overlay_path = _write_overlay_summary(tmp_path / "overlay.tsv")
    matrix_path = alignment_dir / "alignment_matrix.tsv"
    before_hash = _sha256_file(matrix_path)
    candidate_calls = 0
    original_candidates = gate._missing_overlay_queue_candidates

    def counted_candidates(rows):
        nonlocal candidate_calls
        candidate_calls += 1
        return original_candidates(rows)

    monkeypatch.setattr(gate, "_missing_overlay_queue_candidates", counted_candidates)

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--overlay-batch-summary-tsv",
            str(overlay_path),
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "unit-test-run",
        ],
    )

    assert code == 0
    assert _sha256_file(matrix_path) == before_hash
    rows = _read_tsv(output_dir / "alignment_retained_backfill_evidence_gate.tsv")
    assert tuple(rows[0]) == RETAINED_BACKFILL_EVIDENCE_GATE_COLUMNS
    by_id = {row["feature_family_id"]: row for row in rows}
    assert set(by_id) == {"FAM_SUPPORT", "FAM_CONFLICT", "FAM_MISSING"}
    assert "FAM_CELL_GAP" not in by_id

    supported = by_id["FAM_SUPPORT"]
    assert supported["product_behavior_state"] == "product_primary_backfill_accepted"
    assert supported["evidence_gate_status"] == "visual_support"
    assert supported["recommended_action"] == "track_supported_backfill"
    assert supported["diagnostic_authority"] == "diagnostic_only"
    assert "seed_request_provenance" in supported["support_components"]
    assert "ms1_shape_supports_family_backfill" in supported["support_components"]
    assert supported["seed_source_samples"] == "S2"
    assert supported["overlay_png_path"] == "plots/fam-support.png"

    conflict = by_id["FAM_CONFLICT"]
    assert conflict["product_behavior_state"] == (
        "product_primary_backfill_review_only"
    )
    assert conflict["evidence_gate_status"] == "evidence_conflict"
    assert conflict["recommended_action"] == "review_product_backfill"
    assert (
        "review_required_neighboring_ms1_interference"
        in conflict["challenge_blockers"]
    )
    assert "backfill_cell_evidence_required" in conflict["dependent_context"]
    assert "backfill_rescue_review_only" in conflict["dependent_context"]

    missing = by_id["FAM_MISSING"]
    assert missing["evidence_gate_status"] == "evidence_missing"
    assert missing["recommended_action"] == "generate_missing_evidence"
    assert "missing_overlay_evidence" in missing["missing_evidence"]
    assert missing["support_components"] == "seed_request_provenance"
    assert missing["seed_mz"] == "269.145"
    assert missing["suggested_rt_min"] == "9.0000"

    queue_rows = _read_tsv(
        output_dir / "alignment_retained_backfill_missing_overlay_queue.tsv",
    )
    assert [row["feature_family_id"] for row in queue_rows] == ["FAM_MISSING"]
    review_queue_rows = _read_tsv(
        output_dir / "alignment_retained_backfill_overlay_review_queue.tsv",
    )
    assert [row["feature_family_id"] for row in review_queue_rows] == ["FAM_MISSING"]
    queue = queue_rows[0]
    assert queue["rank"] == "1"
    assert queue["family_center_mz"] == "269.145"
    assert queue["suggested_rt_min"] == "9.0000"
    assert queue["suggested_rt_max"] == "11.0000"
    assert queue["backfill_seed_mz"] == "269.145"
    assert queue["backfill_request_ppm"] == "10"
    assert queue["suggested_output_prefix"] == (
        "001_fam_missing_retained_backfill_missing_overlay"
    )
    assert "--family-id FAM_MISSING" in queue["suggested_overlay_command_args"]
    assert "--mz 269.145" in queue["suggested_overlay_command_args"]

    payload = json.loads(
        (output_dir / "alignment_retained_backfill_evidence_gate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["schema_version"] == "retained_backfill_evidence_gate"
    assert payload["readiness_label"] == "diagnostic_only"
    assert payload["source_run_id"] == "unit-test-run"
    assert payload["row_count"] == 3
    assert payload["family_count"] == 3
    assert payload["missing_overlay_queue_count"] == 1
    assert payload["review_overlay_queue_count"] == 1
    assert payload["excluded_family_counts"] == {
        "detected_cell_join_mismatch": 1,
        "detected_zero_family": 1,
    }
    assert payload["status_counts"] == {
        "evidence_conflict": 1,
        "evidence_missing": 1,
        "visual_support": 1,
    }
    assert payload["production_ready"] is False
    assert payload["matrix_contract_changed"] is False
    assert payload["source_matrix_sha256"] == before_hash
    assert payload["source_overlay_artifacts"] == str(overlay_path)
    assert candidate_calls == 1


def test_overlay_support_without_seed_provenance_fails_closed(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment", seed_audit=False)
    output_dir = tmp_path / "gate"
    overlay_path = _write_overlay_summary(tmp_path / "overlay.tsv")

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--overlay-batch-summary-tsv",
            str(overlay_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "alignment_retained_backfill_evidence_gate.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}
    assert by_id["FAM_SUPPORT"]["evidence_gate_status"] == "evidence_missing"
    assert "ms1_shape_supports_family_backfill" not in by_id["FAM_SUPPORT"][
        "support_components"
    ]
    assert "missing_seed_provenance" in by_id["FAM_SUPPORT"]["missing_evidence"]
    assert "missing_overlay_evidence" in by_id["FAM_SUPPORT"]["missing_evidence"]
    payload = json.loads(
        (output_dir / "alignment_retained_backfill_evidence_gate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["source_seed_audit_artifact"] == ""
    assert payload["source_seed_audit_sha256"] == ""


def test_high_detected_low_rescue_missing_overlay_is_not_queued(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    output_dir = tmp_path / "gate"
    family_id = "FAM_STRONG"
    detected_cells = [
        _cell_row(family_id, f"D{index:02d}", "detected")
        for index in range(1, 84)
    ]
    rescued_cells = [
        _cell_row(family_id, "R01", "backfilled"),
        _cell_row(family_id, "R02", "backfilled"),
    ]
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [_review_row(family_id, detected=83, rescued=2, accepted=2)],
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [*detected_cells, *rescued_cells],
    )
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        [{"Mz": "269.145", "RT": "10.0000", "D01": "100", "R01": "90"}],
    )
    _write_tsv(
        alignment_dir / "alignment_owner_backfill_seed_audit.tsv",
        [_seed_row(family_id, "R01"), _seed_row(family_id, "R02")],
    )

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "strong-support",
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "alignment_retained_backfill_evidence_gate.tsv")
    assert {row["evidence_gate_status"] for row in rows} == {
        "machine_support_no_overlay",
    }
    assert {row["recommended_action"] for row in rows} == {
        "track_machine_supported_backfill",
    }
    assert all(
        "high_detected_anchor_low_rescue_machine_support"
        in row["support_components"]
        for row in rows
    )
    assert all(
        "missing_overlay_evidence" not in row["missing_evidence"]
        for row in rows
    )
    queue_rows = _read_tsv(
        output_dir / "alignment_retained_backfill_missing_overlay_queue.tsv",
    )
    assert queue_rows == []
    review_queue_rows = _read_tsv(
        output_dir / "alignment_retained_backfill_overlay_review_queue.tsv",
    )
    assert review_queue_rows == []
    payload = json.loads(
        (output_dir / "alignment_retained_backfill_evidence_gate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["missing_overlay_queue_count"] == 0
    assert payload["review_overlay_queue_count"] == 0
    assert payload["recommended_action_counts"] == {
        "track_machine_supported_backfill": 1,
    }


def test_unbound_family_overlay_is_not_evidence(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    output_dir = tmp_path / "gate"
    legacy_overlay_path = _write_overlay_summary(
        tmp_path / "legacy_overlay.tsv",
        seed_specific=False,
    )

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--overlay-batch-summary-tsv",
            str(legacy_overlay_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "alignment_retained_backfill_evidence_gate.tsv")
    by_id = {row["feature_family_id"]: row for row in rows}
    supported = by_id["FAM_SUPPORT"]
    assert supported["evidence_gate_status"] == "evidence_missing"
    assert supported["recommended_action"] == "generate_missing_evidence"
    assert "missing_overlay_evidence" in supported["missing_evidence"]
    assert "ms1_shape_supports_family_backfill" not in supported["support_components"]
    assert supported["overlay_png_path"] == ""

    queue_rows = _read_tsv(
        output_dir / "alignment_retained_backfill_missing_overlay_queue.tsv",
    )
    by_queue_id = {row["feature_family_id"]: row for row in queue_rows}
    assert by_queue_id["FAM_SUPPORT"]["seed_group_id"] == _seed_group_id(
        "FAM_SUPPORT",
    )
    assert "missing_overlay_evidence" in by_queue_id["FAM_SUPPORT"][
        "missing_evidence"
    ]


def test_seed_specific_overlay_rows_do_not_leak_across_seed_groups(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    output_dir = tmp_path / "gate"
    family_id = "FAM_MULTI"
    seed_a = _seed_group_id(family_id, seed_rt="10.0000")
    seed_b = _seed_group_id(
        family_id,
        seed_rt="12.0000",
        rt_min="11.0000",
        rt_max="13.0000",
    )
    seed_c = _seed_group_id(
        family_id,
        seed_rt="14.0000",
        rt_min="13.0000",
        rt_max="15.0000",
    )
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [_review_row(family_id, detected=2, rescued=2, accepted=2)],
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            _cell_row(family_id, "S1", "detected"),
            _cell_row(family_id, "S2", "backfilled"),
        ],
    )
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        [{"Mz": "269.145", "RT": "10.0000", "S1": "100", "S2": "90"}],
    )
    _write_tsv(
        alignment_dir / "alignment_owner_backfill_seed_audit.tsv",
        [
            _seed_row(family_id, "S2", seed_rt="10.0000"),
            _seed_row(
                family_id,
                "S3",
                seed_rt="12.0000",
                rt_min="11.0000",
                rt_max="13.0000",
            ),
            _seed_row(
                family_id,
                "S4",
                seed_rt="14.0000",
                rt_min="13.0000",
                rt_max="15.0000",
            ),
        ],
    )
    overlay_path = tmp_path / "overlay.tsv"
    _write_tsv(
        overlay_path,
        [
            {
                "feature_family_id": family_id,
                "seed_group_id": seed_a,
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "plots/fam-multi-a.png",
            },
            {
                "feature_family_id": family_id,
                "seed_group_id": seed_b,
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "plots/fam-multi-b.png",
            },
        ],
    )

    code = gate_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--overlay-batch-summary-tsv",
            str(overlay_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "alignment_retained_backfill_evidence_gate.tsv")
    by_seed = {row["seed_group_id"]: row for row in rows}
    assert by_seed[seed_a]["evidence_gate_status"] == "visual_support"
    assert by_seed[seed_a]["overlay_png_path"] == "plots/fam-multi-a.png"
    assert by_seed[seed_b]["evidence_gate_status"] == "evidence_conflict"
    assert (
        by_seed[seed_b]["challenge_blockers"]
        == "review_required_neighboring_ms1_interference"
    )
    assert by_seed[seed_c]["evidence_gate_status"] == "evidence_missing"
    assert "missing_overlay_evidence" in by_seed[seed_c]["missing_evidence"]


def test_cli_defaults_output_dir_to_alignment_dir(tmp_path: Path) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")

    code = gate_cli.main(["--alignment-dir", str(alignment_dir)])

    assert code == 0
    assert (
        alignment_dir / "alignment_retained_backfill_evidence_gate.tsv"
    ).is_file()
    assert (
        alignment_dir / "alignment_retained_backfill_evidence_gate.json"
    ).is_file()
    assert (
        alignment_dir / "alignment_retained_backfill_missing_overlay_queue.tsv"
    ).is_file()
    assert (
        alignment_dir / "alignment_retained_backfill_overlay_review_queue.tsv"
    ).is_file()


def test_cli_prefers_compact_backfill_cell_evidence_over_full_cells(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_run(tmp_path / "alignment")
    compact_path = alignment_dir / "alignment_backfill_cell_evidence.tsv"
    compact_path.write_text(
        (alignment_dir / "alignment_cells.tsv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write_tsv(alignment_dir / "alignment_cells.tsv", [{"feature_family_id": "FAM"}])

    code = gate_cli.main(["--alignment-dir", str(alignment_dir)])

    assert code == 0
    payload = json.loads(
        (alignment_dir / "alignment_retained_backfill_evidence_gate.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["source_cell_artifact"] == str(compact_path)


def test_cli_reports_missing_required_columns(tmp_path: Path, capsys) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(alignment_dir / "alignment_review.tsv", [{"feature_family_id": "FAM"}])
    _write_tsv(alignment_dir / "alignment_cells.tsv", [{"feature_family_id": "FAM"}])
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        [{"Mz": "269.145", "RT": "10.0", "S1": "1"}],
    )

    code = gate_cli.main(["--alignment-dir", str(alignment_dir)])

    assert code == 2
    stderr = capsys.readouterr().err
    assert "missing required columns" in stderr
    assert "include_in_primary_matrix" in stderr


def _write_alignment_run(path: Path, *, seed_audit: bool = True) -> Path:
    path.mkdir(parents=True)
    _write_tsv(
        path / "alignment_review.tsv",
        [
            _review_row("FAM_SUPPORT", detected=2, rescued=1, accepted=1),
            _review_row(
                "FAM_CONFLICT",
                detected=2,
                rescued=5,
                accepted=0,
                review=5,
                reason="primary_identity_retained_backfill_review_only",
                flags=(
                    "rescue_heavy;backfill_cell_evidence_required;"
                    "backfill_rescue_review_only;"
                    "missing_independent_backfill_identity_evidence"
                ),
            ),
            _review_row("FAM_MISSING", detected=3, rescued=1, accepted=1),
            _review_row("FAM_CELL_GAP", detected=1, rescued=1, accepted=1),
            _review_row(
                "FAM_ZERO",
                include="FALSE",
                decision="audit_family",
                detected=0,
                rescued=2,
                accepted=0,
            ),
            _review_row(
                "FAM_PROVISIONAL",
                include="FALSE",
                decision="provisional_discovery",
                detected=1,
                rescued=1,
                accepted=0,
                flags="single_detected_seed;provisional_retention_candidate",
            ),
        ],
    )
    _write_tsv(
        path / "alignment_cells.tsv",
        [
            _cell_row("FAM_SUPPORT", "S1", "detected"),
            _cell_row("FAM_SUPPORT", "S2", "backfilled"),
            _cell_row("FAM_CONFLICT", "S1", "detected"),
            _cell_row("FAM_CONFLICT", "S2", "backfilled"),
            _cell_row("FAM_MISSING", "S1", "detected"),
            _cell_row("FAM_MISSING", "S2", "backfilled"),
            _cell_row("FAM_CELL_GAP", "S2", "backfilled"),
            _cell_row("FAM_ZERO", "S2", "backfilled"),
            _cell_row("FAM_PROVISIONAL", "S2", "backfilled"),
        ],
    )
    _write_tsv(
        path / "alignment_matrix.tsv",
        [{"Mz": "269.145", "RT": "10.0000", "S1": "100", "S2": "90"}],
    )
    if seed_audit:
        _write_tsv(
            path / "alignment_owner_backfill_seed_audit.tsv",
            [
                _seed_row("FAM_SUPPORT", "S2"),
                _seed_row("FAM_CONFLICT", "S2"),
                _seed_row("FAM_MISSING", "S2"),
                _seed_row("FAM_CELL_GAP", "S2"),
                _seed_row("FAM_ZERO", "S2"),
            ],
        )
    return path


def _review_row(
    family_id: str,
    *,
    detected: int,
    rescued: int,
    accepted: int,
    review: int = 0,
    include: str = "TRUE",
    decision: str = "production_family",
    reason: str = "owner_complete_link",
    flags: str = "",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "family_center_mz": "269.145",
        "family_center_rt": "10.0000",
        "include_in_primary_matrix": include,
        "identity_decision": decision,
        "identity_reason": reason,
        "primary_evidence": "owner_complete_link",
        "quantifiable_detected_count": str(detected),
        "quantifiable_backfill_count": str(rescued),
        "accepted_backfill_count": str(accepted),
        "review_backfill_count": str(review),
        "row_flags": flags,
    }


def _cell_row(family_id: str, sample: str, status: str) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "status": status,
        "primary_matrix_area": "100",
        "primary_matrix_area_source": "gaussian15_positive_asls_residual",
        "primary_matrix_area_reason": "",
        "gap_fill_state": "gap_fill_rescued" if status == "backfilled" else "",
        "gap_fill_reason": (
            "group_centered_query_detected" if status == "backfilled" else ""
        ),
        "trace_quality": "owner_backfill" if status == "backfilled" else "clean",
        "backfill_evidence_reason": "",
        "reason": (
            "owner-centered MS1 backfill" if status == "backfilled" else "detected"
        ),
    }


def _seed_row(
    family_id: str,
    sample: str,
    *,
    seed_rt: str = "10.0000",
    rt_min: str = "9.0000",
    rt_max: str = "11.0000",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "status": "backfilled",
        "backfill_seed_mz": "269.145",
        "backfill_seed_rt": seed_rt,
        "backfill_request_rt_min": rt_min,
        "backfill_request_rt_max": rt_max,
        "backfill_request_ppm": "10",
    }


def _seed_group_id(
    family_id: str,
    *,
    seed_rt: str = "10.0000",
    rt_min: str = "9.0000",
    rt_max: str = "11.0000",
) -> str:
    return (
        f"seed::{family_id}::mz=269.145::rt={seed_rt}::"
        f"window={rt_min}-{rt_max}::ppm=10"
    )


def _write_overlay_summary(path: Path, *, seed_specific: bool = True) -> Path:
    support: dict[str, str] = {
        "feature_family_id": "FAM_SUPPORT",
        "family_verdict": "ms1_shape_supports_family_backfill",
        "png_path": "plots/fam-support.png",
    }
    conflict: dict[str, str] = {
        "feature_family_id": "FAM_CONFLICT",
        "family_verdict": "review_required_neighboring_ms1_interference",
        "png_path": "plots/fam-conflict.png",
    }
    if seed_specific:
        support["seed_group_id"] = _seed_group_id("FAM_SUPPORT")
        conflict["seed_group_id"] = _seed_group_id("FAM_CONFLICT")
    _write_tsv(
        path,
        [support, conflict],
    )
    return path


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
