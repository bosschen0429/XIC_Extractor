from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.diagnostics.row_completion_confidence_panel import (
    evaluate_gate_panel,
    load_canonical_panel,
)


def test_load_canonical_panel_requires_columns(tmp_path: Path) -> None:
    panel = tmp_path / "panel.tsv"
    panel.write_text(
        "case_id\tcase_type\ncase1\ttargeted_gt_summary\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required columns"):
        load_canonical_panel(panel)


def test_gate_panel_passes_only_when_full_canonical_panel_is_bound(
    tmp_path: Path,
) -> None:
    panel = _write_panel(tmp_path / "panel.tsv", include_hmdc=True)
    manifest = _write_panel_manifest(
        tmp_path / "panel_manifest.json",
        required_case_count=2,
    )
    gt_5medc = _write_targeted_gt_dir(
        tmp_path / "5medc",
        rows=[
            {"sample_stem": "S1", "failure_mode": "PASS"},
            {"sample_stem": "S2", "failure_mode": "PASS"},
        ],
    )
    gt_5hmdc = _write_targeted_gt_dir(
        tmp_path / "5hmdc",
        rows=[
            {"sample_stem": "S3", "failure_mode": "PASS"},
            {"sample_stem": "S4", "failure_mode": "PASS"},
        ],
    )

    result = evaluate_gate_panel(
        panel,
        panel_manifest=manifest,
        targeted_gt_dirs={"5-medC": gt_5medc, "5-hmdC": gt_5hmdc},
        current_sentinel_summary={"duplicate_only_family_count": 0},
        baseline_sentinel_summary={"duplicate_only_family_count": 0},
    )

    assert result.status == "PASS"
    assert result.gate_ok is True
    assert result.production_ready is False
    assert result.manual_review_required is False


@pytest.mark.parametrize("failure_mode", ["", "UNKNOWN"])
def test_gate_panel_is_inconclusive_on_non_pass_unknown_failure_mode(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    panel = _write_panel(tmp_path / "panel.tsv")
    manifest = _write_panel_manifest(tmp_path / "panel_manifest.json")
    gt_5medc = _write_targeted_gt_dir(
        tmp_path / "5medc",
        rows=[
            {"sample_stem": "S1", "failure_mode": "PASS"},
            {"sample_stem": "S2", "failure_mode": failure_mode},
        ],
    )

    result = evaluate_gate_panel(
        panel,
        panel_manifest=manifest,
        targeted_gt_dirs={"5-medC": gt_5medc},
        current_sentinel_summary={"duplicate_only_family_count": 0},
        baseline_sentinel_summary={"duplicate_only_family_count": 0},
    )

    assert result.status == "INCONCLUSIVE"
    assert result.gate_ok is False
    assert result.missing_evidence_code == "metric_source_unavailable"


@pytest.mark.parametrize("failure_mode", ["SPLIT", "MISS", "DRIFT", "DUPLICATE"])
def test_gate_panel_fails_on_targeted_gt_regression(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    panel = _write_panel(tmp_path / "panel.tsv")
    manifest = _write_panel_manifest(tmp_path / "panel_manifest.json")
    gt_5medc = _write_targeted_gt_dir(
        tmp_path / "5medc",
        rows=[
            {"sample_stem": "S1", "failure_mode": "PASS"},
            {"sample_stem": "S2", "failure_mode": failure_mode},
        ],
    )

    result = evaluate_gate_panel(
        panel,
        panel_manifest=manifest,
        targeted_gt_dirs={"5-medC": gt_5medc},
        current_sentinel_summary={"duplicate_only_family_count": 0},
        baseline_sentinel_summary={"duplicate_only_family_count": 0},
    )

    assert result.status == "FAIL"
    assert result.gate_ok is False
    assert result.manual_review_required is True
    assert "targeted GT regression" in result.reason


def test_gate_panel_is_inconclusive_when_second_required_gt_is_missing(
    tmp_path: Path,
) -> None:
    panel = _write_panel(tmp_path / "panel.tsv", include_hmdc=True)
    manifest = _write_panel_manifest(
        tmp_path / "panel_manifest.json",
        required_case_count=2,
    )
    gt_5medc = _write_targeted_gt_dir(
        tmp_path / "5medc",
        rows=[{"sample_stem": "S1", "failure_mode": "PASS"}],
    )

    result = evaluate_gate_panel(
        panel,
        panel_manifest=manifest,
        targeted_gt_dirs={"5-medC": gt_5medc},
        current_sentinel_summary={"duplicate_only_family_count": 0},
        baseline_sentinel_summary={"duplicate_only_family_count": 0},
    )

    assert result.status == "INCONCLUSIVE"
    assert result.gate_ok is False
    assert result.missing_evidence_code == "canonical_panel_case_unbound"


def test_gate_panel_warns_when_sentinel_pressure_increases(tmp_path: Path) -> None:
    panel = _write_panel(tmp_path / "panel.tsv", targeted_only=False)
    manifest = _write_panel_manifest(tmp_path / "panel_manifest.json")

    result = evaluate_gate_panel(
        panel,
        panel_manifest=manifest,
        targeted_gt_dirs={},
        current_sentinel_summary={"duplicate_only_family_count": 2},
        baseline_sentinel_summary={"duplicate_only_family_count": 0},
    )

    assert result.status == "WARN"
    assert result.gate_ok is False
    assert result.manual_review_required is True
    assert result.missing_evidence_code == "manual_review_required"


def test_gate_panel_is_inconclusive_without_sentinel_baseline(tmp_path: Path) -> None:
    panel = _write_panel(tmp_path / "panel.tsv", targeted_only=False)
    manifest = _write_panel_manifest(tmp_path / "panel_manifest.json")

    result = evaluate_gate_panel(
        panel,
        panel_manifest=manifest,
        targeted_gt_dirs={},
        current_sentinel_summary={"duplicate_only_family_count": 0},
        baseline_sentinel_summary=None,
    )

    assert result.status == "INCONCLUSIVE"
    assert result.gate_ok is False
    assert result.missing_evidence_code == "baseline_current_unbound"


def test_gate_panel_is_inconclusive_for_unknown_manual_review_trigger(
    tmp_path: Path,
) -> None:
    panel = _write_panel(
        tmp_path / "panel.tsv",
        targeted_only=False,
        manual_review_trigger="bogus_trigger",
    )
    manifest = _write_panel_manifest(tmp_path / "panel_manifest.json")

    result = evaluate_gate_panel(
        panel,
        panel_manifest=manifest,
        targeted_gt_dirs={},
        current_sentinel_summary={"duplicate_only_family_count": 0},
        baseline_sentinel_summary={"duplicate_only_family_count": 0},
    )

    assert result.status == "INCONCLUSIVE"
    assert result.gate_ok is False
    assert result.manual_review_required is True
    assert result.missing_evidence_code == "canonical_panel_case_unbound"


def test_gate_panel_is_inconclusive_for_unknown_targeted_manual_review_trigger(
    tmp_path: Path,
) -> None:
    panel = _write_panel(
        tmp_path / "panel.tsv",
        targeted_manual_review_trigger="bogus_trigger",
    )
    manifest = _write_panel_manifest(tmp_path / "panel_manifest.json")
    gt_5medc = _write_targeted_gt_dir(
        tmp_path / "5medc",
        rows=[
            {"sample_stem": "S1", "failure_mode": "PASS"},
            {"sample_stem": "S2", "failure_mode": "PASS"},
        ],
    )

    result = evaluate_gate_panel(
        panel,
        panel_manifest=manifest,
        targeted_gt_dirs={"5-medC": gt_5medc},
        current_sentinel_summary={"duplicate_only_family_count": 0},
        baseline_sentinel_summary={"duplicate_only_family_count": 0},
    )

    assert result.status == "INCONCLUSIVE"
    assert result.gate_ok is False
    assert result.production_ready is False
    assert result.manual_review_required is True
    assert result.missing_evidence_code == "canonical_panel_case_unbound"
    assert "unknown manual_review_trigger" in result.reason


def test_load_canonical_panel_rejects_malformed_row_shape(tmp_path: Path) -> None:
    panel = tmp_path / "panel.tsv"
    panel.write_text(
        "case_id\tcase_type\ttarget_label\tfeature_family_id\tsample_stem\t"
        "expected_outcome\tproduction_safety_expectation\t"
        "review_utility_expectation\trequired_artifacts\tbaseline_binding\t"
        "manual_review_trigger\treason\n"
        "sentinel_duplicate_only\tduplicate_only\t\t\t\t"
        "no_new_duplicate_only_production\tlower_duplicate_pressure\t"
        "sentinels\tbaseline_required\ton_any_current\t"
        "duplicate-only production pressure must not increase\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="malformed row shape"):
        load_canonical_panel(panel)


def test_committed_canonical_panel_manifest_binding() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    panel_path = (
        repo_root
        / "docs"
        / "superpowers"
        / "validation"
        / "row_completion_canonical_panel.tsv"
    )
    manifest_path = (
        repo_root
        / "docs"
        / "superpowers"
        / "validation"
        / "row_completion_canonical_panel_manifest.json"
    )

    panel_cases = load_canonical_panel(panel_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_panel_tsv = (
        "docs/superpowers/validation/row_completion_canonical_panel.tsv"
    )

    assert len(panel_cases) == 6
    assert manifest["required_case_count"] == len(panel_cases)
    assert manifest["panel_tsv"] == expected_panel_tsv


def _write_panel(
    path: Path,
    *,
    include_hmdc: bool = False,
    targeted_only: bool = True,
    manual_review_trigger: str = "on_any_current",
    targeted_manual_review_trigger: str = "on_warn_fail",
) -> Path:
    rows = [
        "gt_5medc_summary\ttargeted_gt_summary\t5-medC\t\t\t"
        "no_new_split_or_miss\tno_regression\trecall_stable_or_better\t"
        "targeted_gt_comparison\tbaseline_required\t"
        f"{targeted_manual_review_trigger}\t"
        "5-medC targeted GT checkpoint",
    ]
    if include_hmdc:
        rows.append(
            "gt_5hmdc_summary\ttargeted_gt_summary\t5-hmdC\t\t\t"
            "no_new_split_or_miss\tno_regression\trecall_stable_or_better\t"
            "targeted_gt_comparison\tbaseline_required\t"
            f"{targeted_manual_review_trigger}\t"
            "5-hmdC targeted GT checkpoint",
        )
    if not targeted_only:
        rows = [
            "sentinel_duplicate_only\tduplicate_only\t\t\t\t\t"
            "no_new_duplicate_only_production\tlower_duplicate_pressure\t"
            f"sentinels\tbaseline_required\t{manual_review_trigger}\t"
            "duplicate-only production pressure must not increase",
        ]
    path.write_text(
        "case_id\tcase_type\ttarget_label\tfeature_family_id\tsample_stem\t"
        "expected_outcome\tproduction_safety_expectation\t"
        "review_utility_expectation\trequired_artifacts\tbaseline_binding\t"
        "manual_review_trigger\treason\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_panel_manifest(path: Path, *, required_case_count: int = 1) -> Path:
    path.write_text(
        '{"schema_version":"row_completion_canonical_panel_manifest",'
        f'"required_case_count":{required_case_count},'
        '"status":"diagnostic_only"}\n',
        encoding="utf-8",
    )
    return path


def _write_targeted_gt_dir(path: Path, *, rows: list[dict[str, str]]) -> Path:
    path.mkdir()
    columns = ("sample_stem", "failure_mode")
    with (path / "comparison.csv").open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(columns) + "\n")
        for row in rows:
            handle.write(f"{row['sample_stem']},{row['failure_mode']}\n")
    return path
