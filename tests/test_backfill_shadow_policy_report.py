from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from tools.diagnostics import backfill_shadow_policy_report as shadow_cli
from xic_extractor.diagnostics import backfill_shadow_policy as shadow
from xic_extractor.diagnostics.backfill_shadow_policy import (
    BACKFILL_SHADOW_POLICY_COLUMNS,
)


def test_cli_writes_cell_level_shadow_policy_report(tmp_path: Path) -> None:
    alignment_dir = _write_alignment_dir(tmp_path / "alignment")
    gate_tsv = _write_gate_tsv(tmp_path / "retained_gate.tsv")
    overlay_tsv = _write_overlay_tsv(tmp_path / "overlay.tsv")
    output_dir = tmp_path / "shadow"
    matrix_path = alignment_dir / "alignment_matrix.tsv"
    before_matrix_hash = _sha256_file(matrix_path)

    code = shadow_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--retained-gate-tsv",
            str(gate_tsv),
            "--overlay-batch-summary-tsv",
            str(overlay_tsv),
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "unit-shadow",
        ],
    )

    assert code == 0
    assert _sha256_file(matrix_path) == before_matrix_hash
    rows = _read_tsv(output_dir / "backfill_shadow_policy_cells.tsv")
    assert tuple(rows[0]) == BACKFILL_SHADOW_POLICY_COLUMNS

    by_key = {
        (row["feature_family_id"], row["seed_group_id"], row["sample_stem"]): row
        for row in rows
    }
    assert set(by_key) == {
        ("FAM_FILL", _seed_group_id("FAM_FILL"), "S2"),
        ("FAM_WOULD", _seed_group_id("FAM_WOULD"), "S2"),
        ("FAM_EDGE", _seed_group_id("FAM_EDGE"), "S2"),
        ("FAM_BLOCK", _seed_group_id("FAM_BLOCK"), "S2"),
        ("FAM_NO_SEED", _seed_group_id("FAM_NO_SEED"), "S2"),
    }

    fill_now = by_key[("FAM_FILL", _seed_group_id("FAM_FILL"), "S2")]
    assert fill_now["current_product_cell_state"] == "filled_now"
    assert fill_now["shadow_policy_decision"] == "fill_now"
    assert fill_now["decision_reason"] == "product_already_writes_rescue"
    assert fill_now["production_gap"] == ""
    assert fill_now["diagnostic_authority"] == "diagnostic_only"

    would_fill = by_key[("FAM_WOULD", _seed_group_id("FAM_WOULD"), "S2")]
    assert would_fill["current_product_cell_state"] == "review_only"
    assert would_fill["shadow_policy_decision"] == (
        "would_fill_under_ms1_rt_policy"
    )
    assert would_fill["decision_reason"] == "ms1_rt_shadow_supported"
    assert would_fill["production_gap"] == ""
    assert would_fill["own_max_shape_supported_fraction"] == "0.875"
    assert would_fill["overlay_png_path"] == "plots/fam-would.png"

    edge = by_key[("FAM_EDGE", _seed_group_id("FAM_EDGE"), "S2")]
    assert edge["current_product_cell_state"] == "review_only"
    assert edge["shadow_policy_decision"] == "blocked"
    assert edge["decision_reason"] == "own_max_shape_at_or_below_threshold"
    assert edge["own_max_shape_supported_fraction"] == "0.5"

    blocked = by_key[("FAM_BLOCK", _seed_group_id("FAM_BLOCK"), "S2")]
    assert blocked["shadow_policy_decision"] == "blocked"
    assert blocked["decision_reason"] == "visual_conflict_or_review_required"
    assert (
        blocked["blockers"]
        == "review_required_neighboring_ms1_interference"
    )

    no_seed = by_key[("FAM_NO_SEED", _seed_group_id("FAM_NO_SEED"), "S2")]
    assert no_seed["shadow_policy_decision"] == "blocked"
    assert no_seed["decision_reason"] == "missing_seed_provenance"
    assert "missing_seed_provenance" in no_seed["missing_evidence"]

    payload = json.loads(
        (output_dir / "backfill_shadow_policy_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["schema_version"] == "backfill_shadow_policy"
    assert payload["readiness_label"] == "diagnostic_only"
    assert payload["source_run_id"] == "unit-shadow"
    assert payload["row_count"] == 5
    assert payload["decision_counts"] == {
        "blocked": 3,
        "fill_now": 1,
        "would_fill_under_ms1_rt_policy": 1,
    }
    assert payload["production_gap_counts"] == {}
    assert payload["matrix_contract_changed"] is False
    assert payload["product_behavior_changed"] is False

    html = (output_dir / "backfill_shadow_policy_report.html").read_text(
        encoding="utf-8",
    )
    assert '<html lang="zh-Hant">' in html
    assert "MS1+RT shadow policy" in html
    assert "FAM_WOULD" in html
    assert "would_fill_under_ms1_rt_policy" in html
    assert "plots/fam-would.png" in html


def test_cli_reports_missing_required_columns(tmp_path: Path, capsys) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(alignment_dir / "alignment_cells.tsv", [{"feature_family_id": "F"}])
    _write_tsv(alignment_dir / "alignment_matrix.tsv", [{"Mz": "1", "RT": "2"}])
    gate_tsv = tmp_path / "gate.tsv"
    _write_tsv(gate_tsv, [{"feature_family_id": "F"}])

    code = shadow_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--retained-gate-tsv",
            str(gate_tsv),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert code == 2
    stderr = capsys.readouterr().err
    assert "missing required columns" in stderr
    assert "sample_stem" in stderr


def test_shadow_policy_html_rejects_dangerous_overlay_links(tmp_path: Path) -> None:
    index = shadow.BackfillShadowPolicyIndex(
        rows=(
            _shadow_output_row(
                "FAM_JS",
                overlay_png_path="javascript:alert(1)",
            ),
            _shadow_output_row(
                "FAM_DATA",
                overlay_png_path="data:text/html,<script>alert(2)</script>",
            ),
            _shadow_output_row(
                "FAM_CTRL",
                overlay_png_path="java\nscript:alert(3)",
            ),
            _shadow_output_row(
                "FAM_SAFE",
                overlay_png_path="plots/fam-safe.png",
            ),
        ),
        summary={
            "row_count": 4,
            "family_count": 4,
        },
    )

    shadow.write_backfill_shadow_policy_outputs(tmp_path, index)

    html = (tmp_path / "backfill_shadow_policy_report.html").read_text(
        encoding="utf-8",
    )
    assert 'href="javascript:' not in html.lower()
    assert 'href="data:' not in html.lower()
    assert "java\nscript:alert" not in html
    assert 'href="plots/fam-safe.png"' in html
    assert "data:text/html,&lt;script&gt;alert(2)&lt;/script&gt;" in html
    assert html.count(">none</span>") == 3


def test_shadow_policy_prefers_conflicting_duplicate_overlay_verdict(
    tmp_path: Path,
) -> None:
    alignment_dir = _write_alignment_dir(tmp_path / "alignment")
    gate_tsv = _write_gate_tsv(tmp_path / "retained_gate.tsv")
    overlay_tsv = tmp_path / "overlay.tsv"
    _write_tsv(
        overlay_tsv,
        [
            _overlay_row("FAM_WOULD", "0.920"),
            _overlay_row(
                "FAM_WOULD",
                "0.750",
                verdict="review_required_neighboring_ms1_interference",
            ),
        ],
    )
    output_dir = tmp_path / "shadow"

    code = shadow_cli.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--retained-gate-tsv",
            str(gate_tsv),
            "--overlay-batch-summary-tsv",
            str(overlay_tsv),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "backfill_shadow_policy_cells.tsv")
    row = next(row for row in rows if row["feature_family_id"] == "FAM_WOULD")
    assert row["shadow_policy_decision"] == "would_fill_under_ms1_rt_policy"
    assert row["decision_reason"] == "ms1_rt_shadow_supported"
    assert row["own_max_shape_supported_fraction"] == "0.750"
    assert row["overlay_png_path"] == "plots/fam-would.png"


def _write_alignment_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    _write_tsv(
        path / "alignment_cells.tsv",
        [
            _cell_row("FAM_FILL", "S1", "detected"),
            _cell_row(
                "FAM_FILL",
                "S2",
                "backfilled",
                gap_fill_state="gap_fill_rescued",
            ),
            _cell_row("FAM_WOULD", "S1", "detected"),
            _cell_row("FAM_WOULD", "S2", "backfilled"),
            _cell_row("FAM_EDGE", "S1", "detected"),
            _cell_row("FAM_EDGE", "S2", "backfilled"),
            _cell_row("FAM_BLOCK", "S1", "detected"),
            _cell_row("FAM_BLOCK", "S2", "backfilled"),
            _cell_row("FAM_NO_SEED", "S1", "detected"),
            _cell_row("FAM_NO_SEED", "S2", "backfilled"),
        ],
    )
    _write_tsv(
        path / "alignment_matrix.tsv",
        [{"Mz": "269.145", "RT": "10.0000", "S1": "100", "S2": "90"}],
    )
    return path


def _cell_row(
    family_id: str,
    sample: str,
    status: str,
    *,
    gap_fill_state: str = "",
) -> dict[str, str]:
    primary_matrix_area_source = (
        "gaussian15_positive_asls_residual" if status == "backfilled" else ""
    )
    return {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "status": status,
        "primary_matrix_area": "100" if primary_matrix_area_source else "",
        "primary_matrix_area_source": primary_matrix_area_source,
        "apex_rt": "10.0500",
        "rt_delta_sec": "3",
        "gap_fill_state": gap_fill_state,
        "gap_fill_reason": "owner_backfill" if status == "backfilled" else "",
        "backfill_candidate_ms2_product_authority_status": "",
        "backfill_candidate_ms2_product_authority_scope": "",
        "backfill_candidate_ms2_product_authority_source": "",
        "backfill_evidence_reason": "",
    }


def _write_gate_tsv(path: Path) -> Path:
    _write_tsv(
        path,
        [
            _gate_row("FAM_FILL", "visual_support"),
            _gate_row("FAM_WOULD", "visual_support"),
            _gate_row("FAM_EDGE", "visual_support"),
            _gate_row(
                "FAM_BLOCK",
                "evidence_conflict",
                challenge_blockers="review_required_neighboring_ms1_interference",
            ),
            _gate_row(
                "FAM_NO_SEED",
                "evidence_missing",
                missing_evidence="missing_seed_provenance",
            ),
        ],
    )
    return path


def _gate_row(
    family_id: str,
    status: str,
    *,
    challenge_blockers: str = "",
    missing_evidence: str = "",
) -> dict[str, str]:
    return {
        "schema_version": "unsupported_retained_backfill_evidence_gate",
        "feature_family_id": family_id,
        "seed_group_id": _seed_group_id(family_id),
        "seed_group_basis": (
            "missing_seed_audit"
            if missing_evidence == "missing_seed_provenance"
            else "seed_audit"
        ),
        "seed_mz": "269.145",
        "seed_rt": "10.0000",
        "suggested_rt_min": "9.0000",
        "suggested_rt_max": "11.0000",
        "ppm": "10",
        "product_behavior_state": "product_primary_backfill_review_only",
        "evidence_gate_status": status,
        "detected_cell_count": "2",
        "rescued_cell_count": "1",
        "support_components": (
            "seed_request_provenance;ms1_shape_supports_family_backfill"
            if status == "visual_support"
            else "seed_request_provenance"
        ),
        "challenge_blockers": challenge_blockers,
        "missing_evidence": missing_evidence,
        "overlay_family_verdict": (
            "ms1_shape_supports_family_backfill"
            if status == "visual_support"
            else challenge_blockers
        ),
        "overlay_png_path": f"plots/{family_id.lower().replace('_', '-')}.png",
        "seed_source_samples": "S2",
    }


def _write_overlay_tsv(path: Path) -> Path:
    _write_tsv(
        path,
        [
            _overlay_row("FAM_FILL", "0.920"),
            _overlay_row("FAM_WOULD", "0.875"),
            _overlay_row("FAM_EDGE", "0.5"),
            _overlay_row(
                "FAM_BLOCK",
                "0.250",
                verdict="review_required_neighboring_ms1_interference",
            ),
        ],
    )
    return path


def _overlay_row(
    family_id: str,
    own_max_fraction: str,
    *,
    verdict: str = "ms1_shape_supports_family_backfill",
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "seed_group_id": _seed_group_id(family_id),
        "family_verdict": verdict,
        "png_path": f"plots/{family_id.lower().replace('_', '-')}.png",
        "absolute_own_max_shape_supported_fraction": own_max_fraction,
        "absolute_trace_apex_cluster_fraction": "0.750",
    }


def _seed_group_id(family_id: str) -> str:
    return f"seed::{family_id}::mz=269.145::rt=10.0000::window=9.0000-11.0000::ppm=10"


def _shadow_output_row(
    family_id: str,
    *,
    overlay_png_path: str,
) -> dict[str, str]:
    row = {column: "" for column in BACKFILL_SHADOW_POLICY_COLUMNS}
    row.update(
        {
            "schema_version": "backfill_shadow_policy",
            "feature_family_id": family_id,
            "seed_group_id": _seed_group_id(family_id),
            "sample_stem": "S2",
            "current_product_cell_state": "review_only",
            "shadow_policy_decision": "would_fill_under_ms1_rt_policy",
            "decision_reason": "ms1_rt_shadow_supported",
            "diagnostic_authority": "diagnostic_only",
            "seed_rt": "10.0000",
            "seed_rt_window": "9.0000-11.0000",
            "own_max_shape_supported_fraction": "0.875",
            "absolute_trace_apex_cluster_fraction": "1",
            "support_components": "seed_request_provenance",
            "overlay_png_path": overlay_png_path,
        },
    )
    return row


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
