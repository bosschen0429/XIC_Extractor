from __future__ import annotations

import json
from pathlib import Path

from tools.diagnostics.diagnose_shared_peak_wrong_peak import main
from xic_extractor.alignment.shared_peak_identity_explanation import (
    wrong_peak_root_cause,
)


def test_wrong_peak_family_consensus_proposes_later_alternate() -> None:
    rows = wrong_peak_root_cause.build_wrong_peak_root_cause_rows(
        machine_evidence_support_rows=[
            _support_row(
                family_id="FAM011810",
                sample_id="TumorBC2263_DNA",
                metrics=(
                    "ms1_pattern_reason="
                    "family_ms1_overlay_competing_peak_matches_family_consensus;"
                    "ms1_cell_to_local_window_max_ratio=1;"
                    "ms1_shape_correlation_score=0.90"
                ),
            )
        ],
        alignment_cell_rows=[
            _cell_row(
                family_id="FAM011810",
                sample_id="TumorBC2263_DNA",
                apex_rt="6.56",
                start_rt="6.25",
                end_rt="7.72",
                rt_delta_sec="'-39",
            )
        ],
        overlay_traces={
            ("FAM011810", "TumorBC2263_DNA"): wrong_peak_root_cause.OverlayTrace(
                family_id="FAM011810",
                sample_stem="TumorBC2263_DNA",
                artifact_path="trace.json",
                family_center_rt=7.21,
                rt=(5.8, 6.0, 6.2, 6.56, 6.8, 7.7, 7.93, 8.1, 8.3),
                intensity=(0, 60, 0, 100, 90, 0, 35, 30, 0),
            )
        },
    )

    row = rows[0]
    assert row["root_cause_class"] == (
        "selected_peak_conflicts_with_family_consensus"
    )
    assert row["selection_failure_mode"] == (
        "duplicate_owner_peak_claim_selected_conflicting_peak"
    )
    assert row["alternate_peak_status"] == "candidate_found"
    assert row["alternate_peak_rt"] == "7.93"
    assert row["recommended_next_action"] == "inspect_alternate_peak_before_retarget"


def test_wrong_peak_qc_conflict_reports_low_dominance_candidate() -> None:
    rows = wrong_peak_root_cause.build_wrong_peak_root_cause_rows(
        machine_evidence_support_rows=[
            _support_row(
                family_id="FAM001473",
                sample_id="TumorBC2312_DNA",
                metrics=(
                    "qc_ms1_reference_status=conflict;"
                    "ms1_cell_to_local_window_max_ratio=0.406;"
                    "ms1_shape_correlation_score=0.03;"
                    "candidate_ms2_pattern_status=conflict"
                ),
            )
        ],
        alignment_cell_rows=[
            _cell_row(
                family_id="FAM001473",
                sample_id="TumorBC2312_DNA",
                apex_rt="18.48",
                start_rt="18.39",
                end_rt="18.49",
                rt_delta_sec="'-60.9",
            )
        ],
        overlay_traces={
            ("FAM001473", "TumorBC2312_DNA"): wrong_peak_root_cause.OverlayTrace(
                family_id="FAM001473",
                sample_stem="TumorBC2312_DNA",
                artifact_path="trace.json",
                family_center_rt=19.49,
                rt=(18.0, 18.17, 18.25, 18.34, 18.42, 18.48, 18.55),
                intensity=(0, 100, 20, 80, 20, 41, 0),
            )
        },
    )

    row = rows[0]
    assert row["root_cause_class"] == "selected_peak_conflicts_with_qc_reference"
    assert row["selection_failure_mode"] == "selected_peak_not_local_dominant"
    assert "low_local_peak_dominance" in row["secondary_root_cause_tokens"]
    assert row["alternate_peak_status"] == "candidate_found"
    assert row["alternate_peak_rt"] == "18.17"


def test_wrong_peak_uses_sample_id_alias_for_alignment_cell_context() -> None:
    rows = wrong_peak_root_cause.build_wrong_peak_root_cause_rows(
        machine_evidence_support_rows=[
            _support_row(
                family_id="FAM011810",
                sample_id="TumorBC2263_DNA",
                metrics=(
                    "ms1_pattern_reason="
                    "family_ms1_overlay_competing_peak_matches_family_consensus"
                ),
            )
        ],
        alignment_cell_rows=[
            {
                **_cell_row(
                    family_id="FAM011810",
                    sample_id="TumorBC2263_DNA",
                    apex_rt="6.56",
                    start_rt="6.25",
                    end_rt="7.72",
                    rt_delta_sec="'-39",
                ),
                "sample_id": "TumorBC2263_DNA",
                "sample_stem": "",
            }
        ],
    )

    row = rows[0]
    assert row["selected_cell_status"] == "rescued"
    assert row["selected_apex_rt"] == "6.56"
    assert row["selected_peak_start_rt"] == "6.25"


def test_wrong_peak_ignores_machine_support_without_conflict_evidence() -> None:
    rows = wrong_peak_root_cause.build_wrong_peak_root_cause_rows(
        machine_evidence_support_rows=[
            _support_row(
                family_id="FAM000001",
                sample_id="SampleA",
                metrics="ms1_pattern_status=supportive",
            )
        ],
        alignment_cell_rows=[],
    )

    assert rows == ()


def test_wrong_peak_cli_writes_root_cause_tsv(tmp_path: Path) -> None:
    support = tmp_path / "support.tsv"
    cells = tmp_path / "cells.tsv"
    trace = tmp_path / "trace.json"
    _write_tsv(
        support,
        (
            "feature_family_id",
            "sample_id",
            "machine_current_label",
            "observed_machine_metrics",
            "missing_machine_evidence",
        ),
        [
            _support_row(
                family_id="FAM011810",
                sample_id="TumorBC2263_DNA",
                metrics=(
                    "ms1_pattern_reason="
                    "family_ms1_overlay_competing_peak_matches_family_consensus"
                ),
            )
        ],
    )
    _write_tsv(
        cells,
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "rt_delta_sec",
            "reason",
        ),
        [
            _cell_row(
                family_id="FAM011810",
                sample_id="TumorBC2263_DNA",
                apex_rt="6.56",
                start_rt="6.25",
                end_rt="7.72",
                rt_delta_sec="'-39",
            )
        ],
    )
    trace.write_text(
        json.dumps(
            {
                "family_id": "FAM011810",
                "family_center_rt": 7.21,
                "traces": [
                    {
                        "sample_stem": "TumorBC2263_DNA",
                        "rt": [6.2, 6.56, 6.8, 7.72, 7.93, 8.1],
                        "intensity": [0, 100, 0, 0, 40, 0],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--machine-evidence-support-tsv",
                str(support),
                "--alignment-cells-tsv",
                str(cells),
                "--trace-data-json",
                str(trace),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    output = tmp_path / "out" / "shared_peak_identity_wrong_peak_root_cause.tsv"
    assert output.exists()
    assert "selected_peak_conflicts_with_family_consensus" in output.read_text(
        encoding="utf-8"
    )


def _support_row(
    *,
    family_id: str,
    sample_id: str,
    metrics: str,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_id": sample_id,
        "machine_current_label": "rescued",
        "observed_machine_metrics": metrics,
        "missing_machine_evidence": "",
    }


def _cell_row(
    *,
    family_id: str,
    sample_id: str,
    apex_rt: str,
    start_rt: str,
    end_rt: str,
    rt_delta_sec: str,
) -> dict[str, str]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample_id,
        "status": "rescued",
        "area": "100",
        "apex_rt": apex_rt,
        "height": "100",
        "peak_start_rt": start_rt,
        "peak_end_rt": end_rt,
        "rt_delta_sec": rt_delta_sec,
        "reason": "duplicate MS1 peak claim; winner=FAM000001",
    }


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    import csv

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
