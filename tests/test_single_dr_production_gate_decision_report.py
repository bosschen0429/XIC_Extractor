import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.diagnostics import single_dr_gate_decision_loaders as loaders
from tools.diagnostics import single_dr_production_gate_decision_report as report
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.matrix_identity import build_matrix_identity_decisions
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.alignment.promotion_policy import (
    ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
    CELL_EVIDENCE_SUPPORTED_REASON,
    DDA_LIMITED_MS2_SHAPE_REASON,
)
from xic_extractor.alignment.tsv_writer import (
    write_alignment_cells_tsv,
    write_alignment_review_tsv,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult


def test_extreme_dr_backfill_row_becomes_supported_capped_warning(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "diagnostics"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_EXT", q_detected=2, q_rescue=8),),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_EXT", detected=2, rescued=8)),
    )

    code = report.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    families = _read_tsv(output_dir / "single_dr_gate_decision_families.tsv")
    assert families[0]["risk_classification"] == "supported_backfill_capped"
    assert families[0]["promotion_state"] == "supported"
    assert families[0]["promotion_reason"] == CELL_EVIDENCE_SUPPORTED_REASON
    assert families[0]["rescue_fraction"] == "0.8000"

    candidates = _read_tsv(output_dir / "single_dr_gate_candidates.tsv")
    extreme = _candidate(candidates, "dr_extreme_backfill_dependency")
    assert extreme["affected_primary_rows"] == "0"
    assert extreme["affected_istd_rows"] == "0"
    assert extreme["recommended_action"] == "reject"
    supported = _candidate(candidates, "dr_supported_backfill_capped")
    assert supported["affected_primary_rows"] == "1"
    assert supported["recommended_action"] == "keep_warning"
    detected_cells = _read_tsv(
        output_dir / "single_dr_gate_decision_detected_cells.tsv",
    )
    assert detected_cells[0]["seed_candidate_joined"] == "False"
    assert (output_dir / "single_dr_gate_decision_summary.tsv").is_file()
    assert (output_dir / "single_dr_gate_decision_detected_cells.tsv").is_file()
    assert (output_dir / "single_dr_gate_decision.json").is_file()
    assert (output_dir / "single_dr_gate_decision.md").is_file()


def test_weak_seed_rescue_heavy_row_becomes_supported_capped_warning(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "diagnostics"
    discovery_dir = tmp_path / "discovery"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_WEAK", q_detected=3, q_rescue=6),),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_WEAK", detected=3, rescued=6, absent=1)),
    )
    batch_index = _write_discovery_index(
        discovery_dir,
        [
            _candidate_row("S001", "FAM_WEAK_C001", evidence_score="55"),
            _candidate_row("S002", "FAM_WEAK_C002", seed_event_count="1"),
            _candidate_row("S003", "FAM_WEAK_C003", nl_ppm="12.5"),
        ],
    )

    result = report.build_decision_report(
        alignment_dir=alignment_dir,
        discovery_batch_index=batch_index,
    )

    family = result["families"][0]
    assert family["risk_classification"] == "supported_backfill_capped"
    assert family["promotion_state"] == "supported"
    assert family["promotion_reason"] == DDA_LIMITED_MS2_SHAPE_REASON
    assert family["seed_quality_status"] == "weak"
    assert family["min_evidence_score"] == 55.0
    assert family["min_seed_event_count"] == 1.0
    assert family["max_abs_nl_ppm"] == 12.5

    report.write_outputs(output_dir, result)
    candidates = _read_tsv(output_dir / "single_dr_gate_candidates.tsv")
    weak_seed = _candidate(candidates, "dr_weak_seed_backfill_dependency")
    assert weak_seed["affected_primary_rows"] == "0"
    assert weak_seed["recommended_action"] == "reject"


def test_weak_seed_tolerated_row_is_supported_capped_warning(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "diagnostics"
    discovery_dir = tmp_path / "discovery"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (
            _review_row(
                "FAM_TOL",
                q_detected=3,
                q_rescue=6,
                row_flags="rescue_heavy;weak_seed_tolerated",
            ),
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_TOL", detected=3, rescued=6, absent=1)),
    )
    batch_index = _write_discovery_index(
        discovery_dir,
        [
            _candidate_row("S001", "FAM_TOL_C001", evidence_score="55"),
            _candidate_row("S002", "FAM_TOL_C002"),
            _candidate_row("S003", "FAM_TOL_C003"),
        ],
    )

    result = report.build_decision_report(
        alignment_dir=alignment_dir,
        discovery_batch_index=batch_index,
    )

    family = result["families"][0]
    assert family["risk_classification"] == "supported_backfill_capped"
    assert family["promotion_state"] == "supported"
    assert family["promotion_reason"] == DDA_LIMITED_MS2_SHAPE_REASON
    assert family["seed_quality_status"] == "adequate"

    report.write_outputs(output_dir, result)
    candidates = _read_tsv(output_dir / "single_dr_gate_candidates.tsv")
    tolerated = _candidate(candidates, "dr_weak_seed_tolerated_watch")
    assert tolerated["affected_primary_rows"] == "0"
    supported = _candidate(candidates, "dr_supported_backfill_capped")
    assert supported["affected_primary_rows"] == "1"


def test_discovery_candidate_loader_reuses_duplicate_candidate_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    read_counts: dict[str, int] = {}

    def fake_read_delimited_rows(
        path: Path,
    ) -> tuple[list[tuple[int, dict[str, str]]], tuple[str, ...]]:
        read_counts[path.name] = read_counts.get(path.name, 0) + 1
        if path.name == "index.tsv":
            return (
                [
                    (2, {"sample_stem": "S_A", "candidate_csv": "candidates.csv"}),
                    (3, {"sample_stem": "S_B", "candidate_csv": "candidates.csv"}),
                ],
                ("sample_stem", "candidate_csv"),
            )
        if path.name == "candidates.csv":
            return (
                [
                    (2, {"candidate_id": "C1", "evidence_score": "80"}),
                    (
                        3,
                        {
                            "sample_stem": "S_C",
                            "candidate_id": "C2",
                            "evidence_score": "70",
                        },
                    ),
                ],
                ("sample_stem", "candidate_id", "evidence_score"),
            )
        raise AssertionError(path)

    monkeypatch.setattr(loaders, "_read_delimited_rows", fake_read_delimited_rows)

    result = loaders.load_discovery_candidates(tmp_path / "index.tsv")

    assert read_counts["candidates.csv"] == 1
    candidates = result["candidates"]
    assert candidates[("S_A", "C1")]["sample_stem"] == "S_A"
    assert candidates[("S_B", "C1")]["sample_stem"] == "S_B"
    assert ("S_A", "C2") not in candidates
    assert ("S_B", "C2") not in candidates
    assert candidates[("S_C", "C2")]["sample_stem"] == "S_C"


def test_gate_candidates_preserve_classification_bucket_order(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def capture_gate_candidate(**kwargs):
        gate_id = kwargs["gate_candidate_id"]
        captured[gate_id] = [row["feature_family_id"] for row in kwargs["families"]]
        return {"gate_candidate_id": gate_id}

    monkeypatch.setattr(report, "_gate_candidate", capture_gate_candidate)

    candidates = report._gate_candidates(
        [
            _family_row("FAM_BLOCK_A", "blocked_low_ms1_assessable_coverage"),
            _family_row("FAM_EXT", "risky_extreme_backfill"),
            _family_row("FAM_BLOCK_B", "blocked_rescue_only"),
            _family_row("FAM_DUP", "watch_duplicate_rescue"),
            _family_row("FAM_WEAK", "risky_weak_seed_backfill"),
        ],
    )

    assert [row["gate_candidate_id"] for row in candidates] == [
        "dr_extreme_backfill_dependency",
        "dr_weak_seed_backfill_dependency",
        "dr_supported_backfill_capped",
        "dr_backfill_policy_blocked",
        "dr_duplicate_rescue_pressure",
        "dr_weak_seed_tolerated_watch",
    ]
    assert captured["dr_extreme_backfill_dependency"] == ["FAM_EXT"]
    assert captured["dr_weak_seed_backfill_dependency"] == ["FAM_WEAK"]
    assert captured["dr_backfill_policy_blocked"] == [
        "FAM_BLOCK_A",
        "FAM_BLOCK_B",
    ]
    assert captured["dr_duplicate_rescue_pressure"] == ["FAM_DUP"]


def test_tsv_adapter_excludes_review_rescue_outside_alignment_window(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_DRIFT", q_detected=2, q_rescue=8),),
    )
    cells = _cells("FAM_DRIFT", detected=2, rescued=8)
    cells.append(
        {
            **cells[-1],
            "sample_stem": "S999",
            "rt_delta_sec": "-240.0",
            "area": "500",
            "reason": "review_rescue_outside_rt_window",
        },
    )
    _write_tsv(alignment_dir / "alignment_cells.tsv", tuple(cells))

    result = report.build_decision_report(alignment_dir=alignment_dir)

    family = result["families"][0]
    assert family["risk_classification"] == "supported_backfill_capped"
    assert family["promotion_state"] == "supported"
    assert family["assessed_rescue_count"] == 8


def test_tsv_adapter_reduces_generic_review_rescue_to_unavailable(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "diagnostics"
    review_row = _review_row("FAM_REVIEW", q_detected=2, q_rescue=0)
    review_row["review_rescue_count"] = "1"
    _write_tsv(alignment_dir / "alignment_review.tsv", (review_row,))
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_REVIEW", detected=2, rescued=1)),
    )

    result = report.build_decision_report(alignment_dir=alignment_dir)
    report.write_outputs(output_dir, result)

    family = result["families"][0]
    assert family["risk_classification"] == "unavailable_backfill_owner_evidence"
    assert family["promotion_state"] == "unavailable"
    assert family["promotion_reason"] == "backfill_owner_rehydration_unavailable"
    summary = {row["metric"]: row["value"] for row in result["summary"]}
    assert summary["unavailable_backfill_owner_evidence_rows"] == "1"
    assert result["activation_decisions"] == []
    assert _read_tsv(output_dir / "single_dr_gate_activation_decisions.tsv") == []
    assert _read_tsv(output_dir / "single_dr_gate_changed_row_bundle.tsv") == []


def test_tsv_adapter_requires_height_for_rescue_quantifiable_parity(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_NO_HEIGHT", q_detected=2, q_rescue=8),),
    )
    cells = _cells("FAM_NO_HEIGHT", detected=2, rescued=8)
    for row in cells:
        if row["status"] == "rescued":
            row["height"] = ""
    _write_tsv(alignment_dir / "alignment_cells.tsv", tuple(cells))

    result = report.build_decision_report(alignment_dir=alignment_dir)

    family = result["families"][0]
    assert family["risk_classification"] == "blocked_low_ms1_assessable_coverage"
    assert family["promotion_state"] == "blocked"
    assert family["assessed_rescue_count"] == 0


def test_gate_writes_activation_decisions_for_implemented_primary_blocks(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "diagnostics"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_BLOCK", q_detected=2, q_rescue=8),),
    )
    cells = _cells("FAM_BLOCK", detected=2, rescued=8)
    for row in cells:
        if row["status"] == "rescued":
            row.update(_backfill_evidence_row(supported=False))
    _write_tsv(alignment_dir / "alignment_cells.tsv", tuple(cells))

    result = report.build_decision_report(alignment_dir=alignment_dir)
    report.write_outputs(output_dir, result)

    decisions = _read_tsv(output_dir / "single_dr_gate_activation_decisions.tsv")
    assert decisions == [
        {
            "activation_schema_version": (
                "shared_peak_identity_activation_decision_v1"
            ),
            "feature_family_id": "FAM_BLOCK",
            "candidate_container_id": "FAM_BLOCK",
            "sample_id": "__family_context__",
            "peak_hypothesis_id": "FAM_BLOCK",
            "activation_unit_scope": "legacy_family_row",
            "machine_current_label": "blocked_missing_backfill_identity_evidence",
            "evidence_support_status": "not_supportive",
            "activation_status": "auto_block",
            "activation_action": "require_review",
            "product_label_candidate": "fail",
            "product_effect": "block_family_promotion",
            "activation_confidence": "medium",
            "hard_product_block": "TRUE",
            "contract_rule_id": "context_or_not_evaluable",
            "activation_reason": (
                "single_dr_gate:blocked_missing_backfill_identity_evidence:"
                "backfill_ms1_pattern_not_supportive"
            ),
            "required_review_reason": "",
            "source_evidence_tokens": (
                "single_dr_gate;missing_independent_backfill_identity_evidence"
            ),
            "diagnostic_only": "FALSE",
        }
    ]
    changed_rows = _read_tsv(output_dir / "single_dr_gate_changed_row_bundle.tsv")
    assert changed_rows == [
        {
            "stable_row_id": "FAM_BLOCK",
            "sample": "__row__",
            "target": "DNA_dR",
            "legacy_candidate_id": "",
            "successor_candidate_id": "FAM_BLOCK",
            "selected_rt": "8.0",
            "area": "",
            "boundary": "",
            "confidence": "medium",
            "reason": (
                "single_dr_gate:blocked_missing_backfill_identity_evidence:"
                "backfill_ms1_pattern_not_supportive"
            ),
            "presence_impact": "primary_row_removed",
            "typed_facts_completeness": (
                "blocked:backfill_ms1_pattern_not_supportive:"
                "supported_rescue_count=0:assessed_rescue_count=8"
            ),
            "retired_legacy_inputs": "scan_support_only;owner_backfill_label",
            "ms2_nl_opportunity_status": "not_supportive",
            "rt_istd_rationale": "",
            "evidence_tier": "blocked_missing_backfill_identity_evidence",
            "reviewer_verdict": "pending_manual_review",
        }
    ]


def test_tsv_adapter_uses_primary_matrix_area_for_rescue_evidence(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_PRIMARY_AREA", q_detected=2, q_rescue=8),),
    )
    cells = _cells("FAM_PRIMARY_AREA", detected=2, rescued=8)
    for row in cells:
        if row["status"] == "rescued":
            row["area"] = ""
            row["primary_matrix_area"] = "500"
    _write_tsv(alignment_dir / "alignment_cells.tsv", tuple(cells))

    result = report.build_decision_report(alignment_dir=alignment_dir)

    family = result["families"][0]
    assert family["risk_classification"] == "supported_backfill_capped"
    assert family["promotion_state"] == "supported"
    assert family["assessed_rescue_count"] == 8


def test_tsv_adapter_requires_backfill_evidence_schema(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_STALE_SCHEMA", q_detected=2, q_rescue=8),),
    )
    cells = _cells("FAM_STALE_SCHEMA", detected=2, rescued=8)
    for row in cells:
        row.pop("backfill_ms1_pattern_status")
    _write_tsv(alignment_dir / "alignment_cells.tsv", tuple(cells))

    with pytest.raises(ValueError, match="missing required columns"):
        report.build_decision_report(alignment_dir=alignment_dir)


def test_tsv_adapter_requires_review_rescue_count(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    review_row = _review_row("FAM_STALE_REVIEW", q_detected=2, q_rescue=8)
    review_row.pop("review_rescue_count")
    _write_tsv(alignment_dir / "alignment_review.tsv", (review_row,))
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_STALE_REVIEW", detected=2, rescued=8)),
    )

    with pytest.raises(ValueError, match="missing required columns"):
        report.build_decision_report(alignment_dir=alignment_dir)


def test_single_dr_gate_row_flags_use_exact_tokens(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (
            _review_row(
                "FAM_FALSE_FLAG",
                primary=False,
                q_detected=2,
                q_rescue=8,
                row_flags="not_high_backfill_dependency",
            ),
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_FALSE_FLAG", detected=2, rescued=8)),
    )

    result = report.build_decision_report(alignment_dir=alignment_dir)

    assert result["families"] == []


def test_tsv_diagnostic_reports_neighbor_interference_block(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "diagnostics"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_NEIGHBOR", q_detected=2, q_rescue=8),),
    )
    cells = _cells("FAM_NEIGHBOR", detected=2, rescued=8)
    for row in cells:
        if row["status"] == "rescued":
            row["region_review_reason"] = "neighboring_ms1_interference"
    _write_tsv(alignment_dir / "alignment_cells.tsv", tuple(cells))

    result = report.build_decision_report(alignment_dir=alignment_dir)
    report.write_outputs(output_dir, result)

    family = result["families"][0]
    assert family["risk_classification"] == "blocked_neighboring_ms1_interference"
    assert family["promotion_reason"] == "neighboring_ms1_interference_blocked"
    blocked = _candidate(result["gate_candidates"], "dr_backfill_policy_blocked")
    assert blocked["affected_primary_rows"] == 1


def test_single_dr_family_writer_header_is_stable(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    output_dir = tmp_path / "diagnostics"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_HEADER", q_detected=2, q_rescue=8),),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_HEADER", detected=2, rescued=8)),
    )

    code = report.main(
        [
            "--alignment-dir",
            str(alignment_dir),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    with (output_dir / "single_dr_gate_decision_families.tsv").open(
        newline="",
        encoding="utf-8",
    ) as handle:
        assert next(csv.reader(handle, delimiter="\t")) == [
            "feature_family_id",
            "neutral_loss_tag",
            "risk_classification",
            "rt_context",
            "include_in_primary_matrix",
            "q_detected",
            "q_rescue",
            "sample_count",
            "rescue_fraction",
            "duplicate_assigned_count",
            "row_flags",
            "promotion_state",
            "promotion_reason",
            "promotion_flags",
            "supported_rescue_count",
            "assessed_rescue_count",
            "seed_quality_status",
            "min_evidence_score",
            "min_seed_event_count",
            "max_abs_nl_ppm",
            "min_scan_support_score",
            "missing_detected_candidate_count",
            "targeted_istd_labels",
            "targeted_istd_statuses",
            "family_center_mz",
            "family_center_rt",
            "family_product_mz",
            "family_observed_neutral_loss_da",
        ]


def test_high_detected_rescue_heavy_row_remains_strong(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_STRONG", q_detected=20, q_rescue=60),),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_STRONG", detected=20, rescued=60, absent=5)),
    )

    result = report.build_decision_report(alignment_dir=alignment_dir)

    assert result["families"][0]["risk_classification"] == "strong"
    candidates = {row["gate_candidate_id"]: row for row in result["gate_candidates"]}
    assert candidates["dr_extreme_backfill_dependency"]["affected_primary_rows"] == 0
    assert candidates["dr_weak_seed_backfill_dependency"]["affected_primary_rows"] == 0
    assert candidates["dr_weak_seed_tolerated_watch"]["affected_primary_rows"] == 0
    assert candidates["dr_supported_backfill_capped"]["affected_primary_rows"] == 0


def test_duplicate_pressure_low_detected_rescue_heavy_row_is_watch_only(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (
            _review_row(
                "FAM_DUP",
                q_detected=4,
                q_rescue=6,
                duplicate_assigned=3,
                row_flags="duplicate_claim_pressure;rescue_heavy",
            ),
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_DUP", detected=4, rescued=6)),
    )

    result = report.build_decision_report(alignment_dir=alignment_dir)

    assert result["families"][0]["risk_classification"] == "watch_duplicate_rescue"
    duplicate = _candidate(
        result["gate_candidates"],
        "dr_duplicate_rescue_pressure",
    )
    assert duplicate["affected_primary_rows"] == 1
    assert duplicate["recommended_action"] == "keep_warning"


def test_rt_worsened_is_context_not_a_demotion_rule(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    rt_context = tmp_path / "rt_normalization_families.tsv"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_RT", q_detected=20, q_rescue=0),),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_RT", detected=20, rescued=0)),
    )
    _write_tsv(
        rt_context,
        (
            {
                "feature_family_id": "FAM_RT",
                "rt_context": "context_rt_worsened",
                "normalized_rt_support": "worsened",
                "rt_delta_before_min": "0.03",
                "rt_delta_after_min": "0.09",
            },
        ),
    )

    result = report.build_decision_report(
        alignment_dir=alignment_dir,
        rt_normalization_families_tsv=rt_context,
    )

    family = result["families"][0]
    assert family["risk_classification"] == "strong"
    assert family["rt_context"] == "context_rt_worsened"
    assert all(
        candidate["recommended_action"] != "implement"
        for candidate in result["gate_candidates"]
    )


def test_missing_optional_enrichment_still_outputs_unavailable(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_WEAKISH", q_detected=3, q_rescue=6),),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_WEAKISH", detected=3, rescued=6, absent=1)),
    )

    result = report.build_decision_report(alignment_dir=alignment_dir)

    family = result["families"][0]
    assert family["risk_classification"] == "weak"
    assert family["seed_quality_status"] == "unavailable"
    assert result["enrichment"]["discovery_batch_index"] == "not_provided"


def test_non_dr_primary_rows_are_out_of_scope(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (
            _review_row("FAM_DR", tag="DNA_dR", q_detected=2, q_rescue=8),
            _review_row("FAM_R", tag="RNA_R", q_detected=1, q_rescue=9),
            _review_row("FAM_MER", tag="DNA_MeR", q_detected=1, q_rescue=9),
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_DR", detected=2, rescued=8))
        + tuple(_cells("FAM_R", detected=1, rescued=9))
        + tuple(_cells("FAM_MER", detected=1, rescued=9)),
    )

    result = report.build_decision_report(alignment_dir=alignment_dir)

    assert [family["feature_family_id"] for family in result["families"]] == [
        "FAM_DR",
    ]


def test_istd_impact_blocks_automatic_implement_recommendation(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    benchmark_json = tmp_path / "targeted_istd_benchmark.json"
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        (_review_row("FAM_ISTD", q_detected=2, q_rescue=8),),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        tuple(_cells("FAM_ISTD", detected=2, rescued=8)),
    )
    benchmark_json.write_text(
        json.dumps(
            {
                "summaries": [
                    {
                        "target_label": "d3-N6-medA",
                        "selected_feature_id": "FAM_ISTD",
                        "primary_feature_ids": ["FAM_ISTD"],
                        "status": "FAIL",
                        "failure_modes": ["AREA_MISMATCH"],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    result = report.build_decision_report(
        alignment_dir=alignment_dir,
        targeted_istd_benchmark_json=benchmark_json,
    )

    family = result["families"][0]
    assert family["targeted_istd_labels"] == "d3-N6-medA"
    assert family["risk_classification"] == "supported_backfill_capped"
    extreme = _candidate(
        result["gate_candidates"],
        "dr_extreme_backfill_dependency",
    )
    assert extreme["affected_istd_rows"] == 0
    supported = _candidate(
        result["gate_candidates"],
        "dr_supported_backfill_capped",
    )
    assert supported["affected_istd_rows"] == 1
    assert supported["recommended_action"] == "keep_warning"


def test_diagnostic_tsv_adapter_matches_production_policy(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    matrix = AlignmentMatrix(
        clusters=(_alignment_feature("FAM_PARITY"),),
        cells=(
            _aligned_cell("seed1", "FAM_PARITY", "detected", 100.0),
            _aligned_cell("seed2", "FAM_PARITY", "detected", 90.0),
            _aligned_cell("rescue00", "FAM_PARITY", "rescued", 80.0, rt_delta_sec=-0.5),
            *tuple(
                _aligned_cell(f"rescue{i:02d}", "FAM_PARITY", "rescued", 80.0 + i)
                for i in range(1, 84)
            ),
        ),
        sample_order=(
            "seed1",
            "seed2",
            "rescue00",
            *(f"rescue{i:02d}" for i in range(1, 84)),
        ),
    )
    production = build_matrix_identity_decisions(matrix, AlignmentConfig()).row(
        "FAM_PARITY",
    )
    config = AlignmentConfig()
    write_alignment_review_tsv(
        alignment_dir / "alignment_review.tsv",
        matrix,
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
    )
    write_alignment_cells_tsv(alignment_dir / "alignment_cells.tsv", matrix)

    result = report.build_decision_report(alignment_dir=alignment_dir)

    family = result["families"][0]
    assert production.identity_reason == CELL_EVIDENCE_SUPPORTED_REASON
    assert family["promotion_reason"] == production.identity_reason
    assert family["promotion_flags"] == "high_backfill_dependency_capped"


def test_rt_held_owner_decision_is_unavailable_after_tsv_roundtrip(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    matrix = AlignmentMatrix(
        clusters=(_alignment_feature("FAM_RT_HELD"),),
        cells=(
            _aligned_cell("seed1", "FAM_RT_HELD", "detected", 100.0),
            _aligned_cell("seed2", "FAM_RT_HELD", "detected", 90.0),
            *tuple(
                _aligned_cell(
                    f"rescue{i:02d}",
                    "FAM_RT_HELD",
                    "rescued",
                    80.0 + i,
                    rt_delta_sec=120.0,
                )
                for i in range(8)
            ),
        ),
        sample_order=("seed1", "seed2", *(f"rescue{i:02d}" for i in range(8))),
    )
    config = AlignmentConfig()
    production = build_matrix_identity_decisions(matrix, config).row("FAM_RT_HELD")
    write_alignment_review_tsv(
        alignment_dir / "alignment_review.tsv",
        matrix,
        alignment_config=config,
        production_decisions=build_production_decisions(matrix, config),
    )
    write_alignment_cells_tsv(alignment_dir / "alignment_cells.tsv", matrix)

    result = report.build_decision_report(alignment_dir=alignment_dir)

    family = result["families"][0]
    assert production.include_in_primary_matrix
    assert production.review_rescue_count == 8
    assert production.quantifiable_rescue_count == 0
    assert family["risk_classification"] == "unavailable_backfill_owner_evidence"
    assert family["promotion_state"] == "unavailable"
    assert result["activation_decisions"] == []


def _review_row(
    feature_id: str,
    *,
    tag: str = "DNA_dR",
    primary: bool = True,
    q_detected: int,
    q_rescue: int,
    duplicate_assigned: int = 0,
    row_flags: str = "",
) -> dict[str, str]:
    return {
        "feature_family_id": feature_id,
        "neutral_loss_tag": tag,
        "family_center_mz": "300.0",
        "family_center_rt": "8.0",
        "family_product_mz": "184.0739",
        "family_observed_neutral_loss_da": "116.0474",
        "detected_count": str(q_detected),
        "accepted_rescue_count": str(q_rescue),
        "quantifiable_detected_count": str(q_detected),
        "quantifiable_rescue_count": str(q_rescue),
        "review_rescue_count": "0",
        "duplicate_assigned_count": str(duplicate_assigned),
        "include_in_primary_matrix": "TRUE" if primary else "FALSE",
        "identity_decision": "production_family" if primary else "audit_family",
        "identity_reason": "owner_complete_link",
        "family_evidence": "owner_complete_link",
        "primary_evidence": "owner_complete_link",
        "row_flags": row_flags,
        "warning": "",
        "present_rate": "1.0",
    }


def _cells(
    feature_id: str,
    *,
    detected: int,
    rescued: int,
    absent: int = 0,
) -> list[dict[str, str]]:
    rows = []
    for index in range(1, detected + 1):
        sample = f"S{index:03d}"
        rows.append(
            {
                "feature_family_id": feature_id,
                "sample_stem": sample,
                "status": "detected",
                "area": "1000",
                "primary_matrix_area": "1000",
                "primary_matrix_area_source": "gaussian15_positive_asls_residual",
                "primary_matrix_area_reason": "",
                "apex_rt": "8.0",
                "height": "100",
                "peak_start_rt": "7.95",
                "peak_end_rt": "8.05",
                "rt_delta_sec": "0.0",
                "trace_quality": "clean",
                "scan_support_score": "0.8",
                "source_candidate_id": f"{feature_id}_C{index:03d}",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "300.0",
                "family_center_rt": "8.0",
                "reason": "",
                "region_review_reason": "",
                **_backfill_evidence_row(supported=False),
            },
        )
    for index in range(detected + 1, detected + rescued + 1):
        rows.append(
            {
                "feature_family_id": feature_id,
                "sample_stem": f"S{index:03d}",
                "status": "rescued",
                "area": "500",
                "primary_matrix_area": "500",
                "primary_matrix_area_source": "gaussian15_positive_asls_residual",
                "primary_matrix_area_reason": "",
                "apex_rt": "8.0",
                "height": "100",
                "peak_start_rt": "7.95",
                "peak_end_rt": "8.05",
                "rt_delta_sec": "0.0",
                "trace_quality": "clean",
                "scan_support_score": "0.8",
                "source_candidate_id": "",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "300.0",
                "family_center_rt": "8.0",
                "reason": "owner_backfill",
                "region_review_reason": "",
                **_backfill_evidence_row(supported=True),
            },
        )
    for index in range(detected + rescued + 1, detected + rescued + absent + 1):
        rows.append(
            {
                "feature_family_id": feature_id,
                "sample_stem": f"S{index:03d}",
                "status": "absent",
                "area": "",
                "primary_matrix_area": "",
                "primary_matrix_area_source": "",
                "primary_matrix_area_reason": "absent",
                "apex_rt": "",
                "height": "",
                "peak_start_rt": "",
                "peak_end_rt": "",
                "rt_delta_sec": "",
                "trace_quality": "absent",
                "scan_support_score": "",
                "source_candidate_id": "",
                "neutral_loss_tag": "DNA_dR",
                "family_center_mz": "300.0",
                "family_center_rt": "8.0",
                "reason": "not_found",
                "region_review_reason": "",
                **_backfill_evidence_row(supported=False),
            },
        )
    return rows


def _write_discovery_index(
    discovery_dir: Path,
    candidate_rows: list[dict[str, str]],
) -> Path:
    discovery_dir.mkdir(parents=True, exist_ok=True)
    rows_by_sample: dict[str, list[dict[str, str]]] = {}
    for row in candidate_rows:
        rows_by_sample.setdefault(row["sample_stem"], []).append(row)

    index_rows = []
    for sample, rows in sorted(rows_by_sample.items()):
        candidate_csv = discovery_dir / f"{sample}_candidates.csv"
        _write_csv(candidate_csv, rows)
        index_rows.append(
            {
                "sample_stem": sample,
                "candidate_csv": candidate_csv.name,
            },
        )
    batch_index = discovery_dir / "discovery_batch_index.tsv"
    _write_tsv(batch_index, tuple(index_rows))
    return batch_index


def _candidate_row(
    sample: str,
    candidate_id: str,
    *,
    evidence_score: str = "80",
    seed_event_count: str = "3",
    nl_ppm: str = "3.0",
    scan_support: str = "0.80",
) -> dict[str, str]:
    return {
        "sample_stem": sample,
        "candidate_id": candidate_id,
        "evidence_score": evidence_score,
        "seed_event_count": seed_event_count,
        "neutral_loss_mass_error_ppm": nl_ppm,
        "ms1_scan_support_score": scan_support,
    }


def _family_row(family_id: str, risk_classification: str) -> dict[str, object]:
    return {
        "feature_family_id": family_id,
        "risk_classification": risk_classification,
        "include_in_primary_matrix": True,
        "targeted_istd_labels": "",
    }


def _candidate(
    rows: list[dict[str, object]],
    candidate_id: str,
) -> dict[str, object]:
    return next(row for row in rows if row["gate_candidate_id"] == candidate_id)


def _write_tsv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise AssertionError("test fixture must provide at least one row")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _alignment_feature(feature_family_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        feature_family_id=feature_family_id,
        neutral_loss_tag="DNA_dR",
        family_center_mz=300.0,
        family_center_rt=8.0,
        family_product_mz=184.0739,
        family_observed_neutral_loss_da=116.0474,
        has_anchor=True,
        event_cluster_ids=("OWN-s1-000001",),
        event_member_count=1,
        evidence="owner_complete_link;owner_count=2",
        review_only=False,
    )


def _aligned_cell(
    sample_stem: str,
    family_id: str,
    status: str,
    area: float,
    *,
    rt_delta_sec: float = 0.0,
) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=family_id,
        status=status,  # type: ignore[arg-type]
        area=area,
        apex_rt=8.0,
        height=100.0,
        peak_start_rt=7.95,
        peak_end_rt=8.05,
        rt_delta_sec=rt_delta_sec,
        trace_quality="clean",
        scan_support_score=0.8,
        source_candidate_id=(
            f"{sample_stem}#{family_id}" if status == "detected" else None
        ),
        source_raw_file=Path(f"{sample_stem}.raw") if status == "detected" else None,
        reason=status,
        selected_integration=_integration(raw_area=area, asls_area=area),
        **_backfill_evidence_fields(status=status),
    )


def _integration(*, raw_area: float, asls_area: float) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=7.95,
        rt_apex_min=8.0,
        rt_right_min=8.05,
        raw_apex_rt_min=8.0,
        rt_width_min=0.1,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=raw_area,
        area_baseline_corrected=asls_area,
        baseline_type="asls",
        boundary_sources=("test_single_dr_gate",),
        area_ms1_morphology=asls_area,
        ms1_morphology_area_source=(
            "gaussian15_positive_asls_residual" if asls_area is not None else ""
        ),
    )


def _backfill_evidence_row(*, supported: bool) -> dict[str, str]:
    if not supported:
        return {
            "backfill_ms1_pattern_status": "",
            "backfill_ms1_pattern_evidence_level": "",
            "backfill_qc_reference_status": "",
            "backfill_qc_reference_evidence_level": "",
            "backfill_matrix_rt_drift_status": "",
            "backfill_drift_evidence_level": "",
            "backfill_drift_compatible_status": "",
            "backfill_drift_corrected_delta_sec": "",
            "backfill_candidate_ms2_pattern_status": "",
            "backfill_candidate_ms2_evidence_level": "",
            "backfill_ms2_trigger_scan_count": "",
            "backfill_strict_nl_scan_count": "",
            "backfill_ms2_trace_strength": "",
            "backfill_dda_missing_nl_policy_status": "",
            "backfill_family_ms2_required_tag_status": "",
            "backfill_evidence_reason": "",
            **_blank_product_authority_fields(),
        }
    return {
        "backfill_ms1_pattern_status": "supportive",
        "backfill_ms1_pattern_evidence_level": "trace_constellation",
        "backfill_qc_reference_status": "supportive",
        "backfill_qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
        "backfill_matrix_rt_drift_status": "",
        "backfill_drift_evidence_level": "",
        "backfill_drift_compatible_status": "",
        "backfill_drift_corrected_delta_sec": "",
        "backfill_candidate_ms2_pattern_status": "partial_support",
        "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
        "backfill_ms2_trigger_scan_count": "3",
        "backfill_strict_nl_scan_count": "1",
        "backfill_ms2_trace_strength": "moderate",
        "backfill_dda_missing_nl_policy_status": "",
        "backfill_family_ms2_required_tag_status": "",
        "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        **_product_authority_fields(),
    }


def _backfill_evidence_fields(*, status: str) -> dict[str, object]:
    if status != "rescued":
        return {}
    return {
        "backfill_ms1_pattern_status": "supportive",
        "backfill_ms1_pattern_evidence_level": "trace_constellation",
        "backfill_qc_reference_status": "supportive",
        "backfill_qc_reference_evidence_level": "qc_consensus_with_local_qc_overlay",
        "backfill_candidate_ms2_pattern_status": "partial_support",
        "backfill_candidate_ms2_evidence_level": "sample_candidate_aligned",
        "backfill_ms2_trigger_scan_count": 3,
        "backfill_strict_nl_scan_count": 1,
        "backfill_ms2_trace_strength": "moderate",
        "backfill_evidence_reason": ANCHOR_OWN_MAX_MS1_SUPPORT_REASON,
        **_product_authority_fields(),
    }


def _product_authority_fields() -> dict[str, object]:
    return {
        "backfill_ms1_product_authority_status": "product_authorized",
        "backfill_ms1_product_authority_scope": "feature_family_sample",
        "backfill_ms1_product_authority_source": "unit_test_reviewed_allowlist",
        "backfill_ms1_product_authority_reason": "unit_test_authorized",
        "backfill_ms1_product_authority_evidence_sha256": "unit-test-ms1-sha256",
        "backfill_candidate_ms2_product_authority_status": "product_authorized",
        "backfill_candidate_ms2_product_authority_scope": "feature_family_sample",
        "backfill_candidate_ms2_product_authority_source": (
            "unit_test_reviewed_allowlist"
        ),
        "backfill_candidate_ms2_product_authority_reason": "unit_test_authorized",
        "backfill_candidate_ms2_product_authority_evidence_sha256": (
            "unit-test-ms2-sha256"
        ),
    }


def _blank_product_authority_fields() -> dict[str, str]:
    return {key: "" for key in _product_authority_fields()}
