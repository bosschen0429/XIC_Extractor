from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tests.matrix_identity_fixtures import current_matrix_identity_row
from tools.diagnostics import shadow_production_projection as projection_cli
from xic_extractor.alignment.matrix_output_contract import (
    ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
)
from xic_extractor.alignment.production_decisions import (
    ProductionCellDecision,
    ProductionDecisionSet,
    ProductionRowDecision,
)
from xic_extractor.diagnostics.shadow_production_projection import (
    SHADOW_PRODUCTION_PROJECTION_COLUMNS,
    _current_matrix_values_by_family_sample,
    build_shadow_production_projection_index,
    canonical_shadow_projection_sha256,
    run_shadow_production_projection,
    write_shadow_production_projection_outputs,
)


def test_projection_uses_production_decisions_for_current_matrix_state() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM001", "S_DET", "detected", True, 100.0),
            _cell_decision(
                "FAM001",
                "S_REVIEW",
                "review_backfill",
                False,
                None,
                blank_reason="backfill_ms1_pattern_blocked",
            ),
        ),
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM001", "S_DET", "detected"),
            _cell_row(
                "FAM001",
                "S_REVIEW",
                "backfilled",
                area="250.0",
            ),
        ),
        retained_gate_rows=(
            _gate_row("FAM001", "S_DET;S_REVIEW", detected="1"),
        ),
    )

    by_sample = {row["sample_stem"]: row for row in index.rows}
    assert by_sample["S_DET"]["current_matrix_written"] == "TRUE"
    assert by_sample["S_DET"]["shadow_decision"] == "context"
    assert by_sample["S_DET"]["projected_matrix_written"] == "TRUE"
    assert by_sample["S_DET"]["current_matrix_source"] == (
        "production_decision_snapshot"
    )
    assert by_sample["S_REVIEW"]["current_matrix_written"] == "FALSE"
    assert by_sample["S_REVIEW"]["current_production_status"] == "review_backfill"
    assert by_sample["S_REVIEW"]["shadow_decision"] == "context"
    assert by_sample["S_REVIEW"]["projected_matrix_written"] == "FALSE"
    assert by_sample["S_REVIEW"]["projected_matrix_value"] == "250"


def test_projection_carries_group_hypothesis_as_peak_identity() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_ID", "S_REVIEW", "review_backfill", False, None),
        ),
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row(
                "FAM_ID",
                "S_REVIEW",
                "backfilled",
                group_hypothesis_id="PH_FAM_ID",
            ),
        ),
        retained_gate_rows=(_gate_row("FAM_ID", "S_REVIEW", detected="1"),),
    )

    row = index.rows[0]
    assert row["peak_hypothesis_id"] == "PH_FAM_ID"
    assert row["activation_unit_scope"] == "peak_hypothesis"


def test_projection_leaves_identity_blank_without_peak_or_group_hypothesis() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_NO_ID", "S_REVIEW", "review_backfill", False, None),
        ),
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row(
                "FAM_NO_ID",
                "S_REVIEW",
                "backfilled",
            ),
        ),
        retained_gate_rows=(_gate_row("FAM_NO_ID", "S_REVIEW", detected="1"),),
    )

    row = index.rows[0]
    assert row["peak_hypothesis_id"] == ""
    assert row["activation_unit_scope"] == ""


def test_projection_row_hash_is_present_and_stable_across_identical_builds() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_HASH", "S_REVIEW", "review_backfill", False, None),
        ),
    )

    def build() -> str:
        index = build_shadow_production_projection_index(
            production_decisions=decisions,
            cell_rows=(
                _cell_row(
                    "FAM_HASH",
                    "S_REVIEW",
                    "backfilled",
                    group_hypothesis_id="PH_HASH",
                ),
            ),
            retained_gate_rows=(_gate_row("FAM_HASH", "S_REVIEW", detected="1"),),
        )
        return index.rows[0]["shadow_projection_row_sha256"]

    first_hash = build()
    second_hash = build()

    assert first_hash
    assert len(first_hash) == 64
    assert first_hash == second_hash


def test_projection_product_hash_excludes_review_overlay_path() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_HASH", "S_REVIEW", "review_backfill", False, None),
        ),
    )

    def build(overlay_png_path: str) -> dict[str, str]:
        gate = _gate_row("FAM_HASH", "S_REVIEW", detected="1")
        gate["overlay_png_path"] = overlay_png_path
        index = build_shadow_production_projection_index(
            production_decisions=decisions,
            cell_rows=(
                _cell_row(
                    "FAM_HASH",
                    "S_REVIEW",
                    "backfilled",
                    group_hypothesis_id="PH_HASH",
                ),
            ),
            retained_gate_rows=(gate,),
        )
        return index.rows[0]

    image_free = build("")
    rendered = build("plots/fam-hash.png")

    assert image_free["overlay_png_path"] == ""
    assert rendered["overlay_png_path"] == "plots/fam-hash.png"
    assert (
        image_free["shadow_projection_row_sha256"]
        == rendered["shadow_projection_row_sha256"]
    )
    assert canonical_shadow_projection_sha256((image_free,)) == (
        canonical_shadow_projection_sha256((rendered,))
    )


def test_projection_keeps_dup_without_product_authority_as_review_context() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_DUP", "S_DET", "detected", True, 100.0),
            _cell_decision(
                "FAM_DUP",
                "S_DUP",
                "review_backfill",
                False,
                None,
                blank_reason="backfill_ms1_pattern_blocked",
            ),
        ),
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM_DUP", "S_DET", "detected"),
            _cell_row(
                "FAM_DUP",
                "S_DUP",
                "backfilled",
                gap_fill_state="not_filled",
                gap_fill_reason="not_requested_duplicate_loser",
            ),
        ),
        retained_gate_rows=(
            _gate_row("FAM_DUP", "S_DET;S_DUP", detected="1"),
        ),
    )

    dup = next(row for row in index.rows if row["sample_stem"] == "S_DUP")
    assert dup["shadow_decision"] == "context"
    assert dup["shadow_reasons"] == "identity_supported_review"
    assert dup["shadow_warnings"] == "same_peak_multi_claim"
    assert dup["projected_matrix_written"] == "FALSE"


def test_projection_keeps_visual_same_peak_support_as_identity_review_context() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_VISUAL", "S_DET", "detected", True, 100.0),
            _cell_decision(
                "FAM_VISUAL",
                "S_REVIEW",
                "review_backfill",
                False,
                None,
                blank_reason="backfill_ms1_pattern_blocked",
            ),
        ),
    )
    gate = _gate_row("FAM_VISUAL", "S_DET;S_REVIEW", detected="5")
    gate["support_components"] = (
        "seed_request_provenance;ms1_shape_supports_family_backfill"
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM_VISUAL", "S_DET", "detected"),
            _cell_row(
                "FAM_VISUAL",
                "S_REVIEW",
                "backfilled",
                area="321.5",
                group_hypothesis_id="PH_VISUAL",
            ),
        ),
        retained_gate_rows=(gate,),
    )

    review = next(row for row in index.rows if row["sample_stem"] == "S_REVIEW")
    assert review["shadow_decision"] == "context"
    assert review["shadow_reasons"] == "identity_supported_review"
    assert review["projected_matrix_written"] == "FALSE"
    assert review["projected_matrix_value"] == "321.5"
    assert review["product_authority_chain"] == ""
    assert review["peak_hypothesis_id"] == "PH_VISUAL"
    assert index.summary["projected_new_write_count"] == 0


def test_projection_blocks_current_hypothesis_blocker() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_BLOCK", "S_DET", "detected", True, 100.0),
            _cell_decision(
                "FAM_BLOCK",
                "S_REVIEW",
                "review_backfill",
                False,
                None,
                blank_reason="backfill_wrong_peak_or_hypothesis_blocked",
            ),
        ),
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM_BLOCK", "S_DET", "detected"),
            _cell_row(
                "FAM_BLOCK",
                "S_REVIEW",
                "backfilled",
            ),
        ),
        retained_gate_rows=(
            _gate_row("FAM_BLOCK", "S_DET;S_REVIEW", detected="1"),
        ),
    )

    review = next(row for row in index.rows if row["sample_stem"] == "S_REVIEW")
    assert review["shadow_decision"] == "block"
    assert review["shadow_reasons"] == "backfill_wrong_peak_or_hypothesis_blocked"
    assert review["projected_matrix_written"] == "FALSE"


def test_projection_blocks_cell_hypothesis_loser_even_without_current_reason() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_CELL_BLOCK", "S_DET", "detected", True, 100.0),
            _cell_decision(
                "FAM_CELL_BLOCK",
                "S_REVIEW",
                "review_backfill",
                False,
                None,
                blank_reason="backfill_ms1_pattern_blocked",
            ),
        ),
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM_CELL_BLOCK", "S_DET", "detected"),
            _cell_row(
                "FAM_CELL_BLOCK",
                "S_REVIEW",
                "backfilled",
                consolidation_state="primary_loser",
            ),
        ),
        retained_gate_rows=(
            _gate_row("FAM_CELL_BLOCK", "S_DET;S_REVIEW", detected="1"),
        ),
    )

    review = next(row for row in index.rows if row["sample_stem"] == "S_REVIEW")
    assert review["shadow_decision"] == "block"
    assert review["shadow_reasons"] == "backfill_wrong_peak_or_hypothesis_blocked"
    assert review["shadow_warnings"] == ""
    assert review["projected_matrix_written"] == "FALSE"


def test_projection_blocks_activation_wrong_peak() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_ACT_BLOCK", "S_DET", "detected", True, 100.0),
            _cell_decision(
                "FAM_ACT_BLOCK",
                "S_REVIEW",
                "review_backfill",
                False,
                None,
                blank_reason="backfill_ms1_pattern_blocked",
            ),
        ),
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM_ACT_BLOCK", "S_DET", "detected"),
            _cell_row(
                "FAM_ACT_BLOCK",
                "S_REVIEW",
                "backfilled",
                activation_contract_rule_id="wrong_peak_conflict",
                activation_product_effect="block_rescue_cell",
                activation_reason="wrong peak",
            ),
        ),
        retained_gate_rows=(
            _gate_row("FAM_ACT_BLOCK", "S_DET;S_REVIEW", detected="1"),
        ),
    )

    review = next(row for row in index.rows if row["sample_stem"] == "S_REVIEW")
    assert review["shadow_decision"] == "block"
    assert review["shadow_reasons"] == "backfill_wrong_peak_or_hypothesis_blocked"
    assert review["projected_matrix_written"] == "FALSE"


def test_projection_keeps_evidence_conflicts_as_review_context() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_CONFLICT", "S_DET", "detected", True, 100.0),
            _cell_decision(
                "FAM_CONFLICT",
                "S_REVIEW",
                "review_backfill",
                False,
                None,
            ),
        ),
    )
    gate = _gate_row("FAM_CONFLICT", "S_DET;S_REVIEW", detected="1")
    gate["evidence_gate_status"] = "evidence_conflict"
    gate["challenge_blockers"] = "review_required_neighboring_ms1_interference"

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM_CONFLICT", "S_DET", "detected"),
            _cell_row("FAM_CONFLICT", "S_REVIEW", "backfilled"),
        ),
        retained_gate_rows=(gate,),
    )

    review = next(row for row in index.rows if row["sample_stem"] == "S_REVIEW")
    assert review["shadow_decision"] == "context"
    assert review["shadow_reasons"] == "evidence_gate_requires_review"
    assert review["shadow_warnings"] == "review_required_neighboring_ms1_interference"
    assert review["projected_matrix_written"] == "FALSE"


def test_projection_keeps_review_required_blockers_closed() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_REVIEW", "S_DET", "detected", True, 100.0),
            _cell_decision(
                "FAM_REVIEW",
                "S_REVIEW",
                "review_backfill",
                False,
                None,
            ),
        ),
    )
    gate = _gate_row("FAM_REVIEW", "S_DET;S_REVIEW", detected="1")
    gate["evidence_gate_status"] = "visual_support"
    gate["challenge_blockers"] = "review_required_neighboring_ms1_interference"

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM_REVIEW", "S_DET", "detected"),
            _cell_row(
                "FAM_REVIEW",
                "S_REVIEW",
                "backfilled",
            ),
        ),
        retained_gate_rows=(gate,),
    )

    review = next(row for row in index.rows if row["sample_stem"] == "S_REVIEW")
    assert review["shadow_decision"] == "context"
    assert review["shadow_reasons"] == "challenge_blockers_require_review"
    assert review["shadow_warnings"] == "review_required_neighboring_ms1_interference"
    assert review["projected_matrix_written"] == "FALSE"
    assert review["product_authority_chain"] == ""


def test_projection_blocks_only_hard_failures_for_review_backfills() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision(
                "FAM_NO_ANCHOR",
                "S_REVIEW",
                "review_backfill",
                False,
                None,
            ),
            _cell_decision("FAM_OUT", "S_DET", "detected", True, 100.0),
            _cell_decision("FAM_OUT", "S_REVIEW", "review_backfill", False, None),
        ),
    )

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM_NO_ANCHOR", "S_REVIEW", "backfilled"),
            _cell_row("FAM_OUT", "S_DET", "detected"),
            _cell_row(
                "FAM_OUT",
                "S_REVIEW",
                "backfilled",
                apex_rt="12.00",
                start="11.90",
                end="12.10",
            ),
        ),
        retained_gate_rows=(
            _gate_row("FAM_NO_ANCHOR", "S_REVIEW", detected="0"),
            _gate_row(
                "FAM_OUT",
                "S_DET;S_REVIEW",
                detected="1",
                rt_min="9.00",
                rt_max="10.00",
            ),
        ),
    )

    by_key = {
        (row["feature_family_id"], row["sample_stem"]): row for row in index.rows
    }
    assert by_key[("FAM_NO_ANCHOR", "S_REVIEW")]["shadow_decision"] == "block"
    assert by_key[("FAM_NO_ANCHOR", "S_REVIEW")]["shadow_reasons"] == (
        "no_detected_anchor"
    )
    assert by_key[("FAM_OUT", "S_REVIEW")]["shadow_decision"] == "block"
    assert by_key[("FAM_OUT", "S_REVIEW")]["shadow_reasons"] == (
        "outside_request_window"
    )


def test_projection_rejects_gate_rows_missing_blocker_columns() -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM_BAD_GATE", "S_REVIEW", "review_backfill", False, None),
        ),
    )
    gate = _gate_row("FAM_BAD_GATE", "S_REVIEW", detected="1")
    del gate["challenge_blockers"]

    with pytest.raises(ValueError, match="challenge_blockers"):
        build_shadow_production_projection_index(
            production_decisions=decisions,
            cell_rows=(_cell_row("FAM_BAD_GATE", "S_REVIEW", "backfilled"),),
            retained_gate_rows=(gate,),
        )


def test_projection_overlay_requires_exact_seed_group_match() -> None:
    family = "FAM_OVERLAY"
    first_seed = "seed::FAM_OVERLAY::mz=269.145::rt=9.5::window=9-10::ppm=10"
    second_seed = "seed::FAM_OVERLAY::mz=269.145::rt=9.8::window=9.3-10.3::ppm=10"
    decisions = _production_decisions(
        cells=(
            _cell_decision(family, "S_REVIEW", "review_backfill", False, None),
        ),
    )
    gate = _gate_row(family, "S_REVIEW", detected="1")
    gate["seed_group_id"] = second_seed
    gate["overlay_png_path"] = "gate-specific.png"
    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(_cell_row(family, "S_REVIEW", "backfilled"),),
        retained_gate_rows=(gate,),
        overlay_rows=(
            {
                "feature_family_id": family,
                "seed_group_id": first_seed,
                "png_path": "wrong-seed.png",
                "absolute_own_max_shape_supported_fraction": "0.99",
            },
        ),
    )

    row = index.rows[0]
    assert row["overlay_png_path"] == "gate-specific.png"
    assert row["local_global_ratio"] == ""


def test_projection_prefers_conflicting_duplicate_overlay_verdict() -> None:
    family = "FAM_OVERLAY_DUP"
    seed_group = f"seed::{family}::mz=269.145::rt=9.5::window=9-10::ppm=10"
    decisions = _production_decisions(
        cells=(
            _cell_decision(family, "S_REVIEW", "review_backfill", False, None),
        ),
    )
    gate = _gate_row(family, "S_REVIEW", detected="1")
    gate["seed_group_id"] = seed_group
    gate["overlay_png_path"] = "gate-specific.png"

    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(_cell_row(family, "S_REVIEW", "backfilled"),),
        retained_gate_rows=(gate,),
        overlay_rows=(
            {
                "feature_family_id": family,
                "seed_group_id": seed_group,
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "support.png",
                "absolute_own_max_shape_supported_fraction": "0.99",
            },
            {
                "feature_family_id": family,
                "seed_group_id": seed_group,
                "family_verdict": "review_required_neighboring_ms1_interference",
                "png_path": "conflict.png",
                "absolute_own_max_shape_supported_fraction": "0.25",
            },
        ),
    )

    row = index.rows[0]
    assert row["overlay_png_path"] == "conflict.png"
    assert row["local_global_ratio"] == "0.25"


def test_projection_writer_serializes_stable_schema_and_summary(tmp_path: Path) -> None:
    decisions = _production_decisions(
        cells=(
            _cell_decision("FAM001", "S_DET", "detected", True, 100.0),
            _cell_decision("FAM001", "S_REVIEW", "review_backfill", False, None),
        ),
    )
    index = build_shadow_production_projection_index(
        production_decisions=decisions,
        cell_rows=(
            _cell_row("FAM001", "S_DET", "detected"),
            _cell_row("FAM001", "S_REVIEW", "backfilled", product_evidence=True),
        ),
        retained_gate_rows=(
            _gate_row("FAM001", "S_DET;S_REVIEW", detected="1"),
        ),
        source_run_id="unit-run",
    )

    outputs = write_shadow_production_projection_outputs(tmp_path, index)

    with outputs.tsv.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        assert next(reader) == list(SHADOW_PRODUCTION_PROJECTION_COLUMNS)
    payload = json.loads(outputs.json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "shadow_production_projection"
    assert payload["source_run_id"] == "unit-run"
    assert payload["decision_counts"] == {"context": 2}
    assert payload["gate_row_count"] == 1
    assert payload["projectable_gate_row_count"] == 1
    assert payload["unprojectable_gate_row_count"] == 0
    assert payload["unprojectable_gate_reasons"] == {}
    assert payload["projected_new_write_count"] == 0
    assert payload["source_overlay_sha256s"] == []
    assert payload["current_matrix_source"] == "production_decision_snapshot"
    assert payload["alignment_matrix_cross_checked"] is False


def test_projection_summary_reports_unprojectable_missing_seed_audit_gate() -> None:
    gate = _gate_row("FAM_MISSING_SEED", "", detected="2")
    gate["seed_group_basis"] = "missing_seed_audit"

    index = build_shadow_production_projection_index(
        production_decisions=_production_decisions(cells=()),
        cell_rows=(),
        retained_gate_rows=(gate,),
    )

    assert index.rows == ()
    assert index.summary["gate_row_count"] == 1
    assert index.summary["projectable_gate_row_count"] == 0
    assert index.summary["unprojectable_gate_row_count"] == 1
    assert index.summary["unprojectable_gate_reasons"] == {
        "missing_seed_audit": 1,
    }


def test_package_runner_writes_projection_from_alignment_artifacts(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [
            {
                "feature_family_id": "FAM_RUNNER",
                "neutral_loss_tag": "DNA_dR",
                "detected_count": "1",
                "family_evidence": "owner_complete_link;owner_count=2",
            },
        ],
        ("feature_family_id", "neutral_loss_tag", "detected_count", "family_evidence"),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            _cell_row("FAM_RUNNER", "S_DET", "detected", area="100.0"),
            _cell_row(
                "FAM_RUNNER",
                "S_REVIEW",
                "backfilled",
                area="250.0",
            ),
        ],
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "area",
            "apex_rt",
            "height",
            "peak_start_rt",
            "peak_end_rt",
            "rt_delta_sec",
            "primary_matrix_area",
            "primary_matrix_area_source",
            "gap_fill_state",
            "gap_fill_reason",
            "group_claim_state",
            "consolidation_state",
            "peak_hypothesis_status",
            "product_selection_blocker",
            "rt_mode_status",
        ),
    )
    _write_tsv(
        alignment_dir / "retained_gate.tsv",
        [_gate_row("FAM_RUNNER", "S_DET;S_REVIEW", detected="1")],
        (
            "feature_family_id",
            "seed_group_id",
            "seed_group_basis",
            "seed_mz",
            "seed_rt",
            "suggested_rt_min",
            "suggested_rt_max",
            "detected_cell_count",
            "rescued_cell_count",
            "seed_source_samples",
            "support_components",
            "challenge_blockers",
            "missing_evidence",
            "evidence_gate_status",
            "overlay_family_verdict",
            "overlay_png_path",
        ),
    )

    outputs = run_shadow_production_projection(
        alignment_review_tsv=alignment_dir / "alignment_review.tsv",
        alignment_cells_tsv=alignment_dir / "alignment_cells.tsv",
        retained_gate_tsv=alignment_dir / "retained_gate.tsv",
        output_dir=tmp_path / "projection",
        source_run_id="package-runner-unit",
    )

    rows = _read_tsv(outputs.tsv)
    assert tuple(rows[0]) == SHADOW_PRODUCTION_PROJECTION_COLUMNS
    assert {row["sample_stem"] for row in rows} == {"S_DET", "S_REVIEW"}
    payload = json.loads(outputs.json.read_text(encoding="utf-8"))
    assert payload["source_run_id"] == "package-runner-unit"
    assert payload["source_review_sha256"]
    assert payload["source_cell_sha256"]
    assert payload["source_gate_sha256"]
    assert payload["product_behavior_changed"] is False
    assert payload["matrix_contract_changed"] is False


def test_current_matrix_values_limits_materialization_to_requested_keys(
    tmp_path: Path,
) -> None:
    matrix_tsv = tmp_path / "alignment_matrix.tsv"
    identity_tsv = tmp_path / "alignment_matrix_identity.tsv"
    _write_tsv(
        matrix_tsv,
        [
            {"Mz": "300.3000", "RT": "10.0", "S_KEEP": "111", "S_SKIP": "999"},
            {"Mz": "400.4000", "RT": "11.0", "S_KEEP": "222", "S_SKIP": "888"},
        ],
        ("Mz", "RT", "S_KEEP", "S_SKIP"),
    )
    _write_tsv(
        identity_tsv,
        [
            current_matrix_identity_row(
                index=1,
                mz="300.3",
                rt="10.0",
                peak_hypothesis_id="FAM_KEEP::mode_1",
                sample_values={"S_KEEP": "111", "S_SKIP": "999"},
                source_feature_family_ids="FAM_KEEP",
            ),
            current_matrix_identity_row(
                index=2,
                mz="400.4",
                rt="11.0",
                peak_hypothesis_id="FAM_UNUSED",
                sample_values={"S_KEEP": "222", "S_SKIP": "888"},
            ),
        ],
        ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
    )

    values = _current_matrix_values_by_family_sample(
        alignment_matrix_tsv=matrix_tsv,
        alignment_matrix_identity_tsv=identity_tsv,
        requested_keys={("FAM_KEEP", "S_KEEP")},
    )

    assert values == {("FAM_KEEP", "S_KEEP"): "111"}


def test_cli_writes_projection_from_alignment_artifacts(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [
            {
                "feature_family_id": "FAM_CLI",
                "neutral_loss_tag": "DNA_dR",
                "detected_count": "1",
                "family_evidence": "owner_complete_link;owner_count=2",
            },
        ],
        ("feature_family_id", "neutral_loss_tag", "detected_count", "family_evidence"),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            _cell_row("FAM_CLI", "S_DET", "detected", area="100.0"),
            _cell_row("FAM_CLI", "S_REVIEW", "backfilled", area="250.0"),
        ],
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "area",
            "apex_rt",
            "height",
            "peak_start_rt",
            "peak_end_rt",
            "rt_delta_sec",
            "primary_matrix_area",
            "primary_matrix_area_source",
            "gap_fill_state",
            "gap_fill_reason",
            "group_claim_state",
            "consolidation_state",
            "peak_hypothesis_status",
            "product_selection_blocker",
            "rt_mode_status",
        ),
    )
    _write_tsv(
        alignment_dir / "retained_gate.tsv",
        [_gate_row("FAM_CLI", "S_DET;S_REVIEW", detected="1")],
        (
            "feature_family_id",
            "seed_group_id",
            "seed_group_basis",
            "seed_mz",
            "seed_rt",
            "suggested_rt_min",
            "suggested_rt_max",
            "detected_cell_count",
            "rescued_cell_count",
            "seed_source_samples",
            "support_components",
            "challenge_blockers",
            "missing_evidence",
            "evidence_gate_status",
            "overlay_family_verdict",
            "overlay_png_path",
        ),
    )
    overlay_tsv = alignment_dir / "overlay.tsv"
    _write_tsv(
        overlay_tsv,
        [
            {
                "feature_family_id": "FAM_CLI",
                "seed_group_id": _gate_row(
                    "FAM_CLI",
                    "S_DET;S_REVIEW",
                    detected="1",
                )["seed_group_id"],
                "family_verdict": "ms1_shape_supports_family_backfill",
                "png_path": "overlay.png",
            },
        ],
        ("feature_family_id", "seed_group_id", "family_verdict", "png_path"),
    )
    output_dir = tmp_path / "projection"

    code = projection_cli.main(
        [
            "--alignment-review-tsv",
            str(alignment_dir / "alignment_review.tsv"),
            "--alignment-cells-tsv",
            str(alignment_dir / "alignment_cells.tsv"),
            "--retained-gate-tsv",
            str(alignment_dir / "retained_gate.tsv"),
            "--output-dir",
            str(output_dir),
            "--source-run-id",
            "cli-unit",
            "--overlay-batch-summary-tsv",
            str(overlay_tsv),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "shadow_production_projection_cells.tsv")
    assert tuple(rows[0]) == SHADOW_PRODUCTION_PROJECTION_COLUMNS
    assert any(row["sample_stem"] == "S_REVIEW" for row in rows)
    payload = json.loads(
        (output_dir / "shadow_production_projection_summary.json").read_text(
            encoding="utf-8",
        ),
    )
    assert payload["source_run_id"] == "cli-unit"
    assert payload["product_behavior_changed"] is False
    assert payload["matrix_contract_changed"] is False
    assert payload["source_overlay_sha256s"]


def test_cli_current_written_uses_actual_matrix_cell_when_identity_supplied(
    tmp_path: Path,
) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [
            {
                "feature_family_id": "FAM_STALE",
                "neutral_loss_tag": "DNA_dR",
                "detected_count": "1",
                "family_evidence": "owner_complete_link;owner_count=1",
                "family_center_mz": "300.3",
                "family_center_rt": "10.0",
            },
        ],
        (
            "feature_family_id",
            "neutral_loss_tag",
            "detected_count",
            "family_evidence",
            "family_center_mz",
            "family_center_rt",
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [
            _cell_row("FAM_STALE", "S_DET", "detected", area="100.0"),
            _cell_row(
                "FAM_STALE",
                "S_REVIEW",
                "backfilled",
                area="250.0",
                primary_matrix_area="250.0",
                primary_matrix_area_source="gaussian15_positive_asls_residual",
                height="1000.0",
                rt_delta_sec="0.0",
                backfill_evidence_reason=(
                    "shift_aware_standard_peak_gate_supported"
                ),
            ),
        ],
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "area",
            "apex_rt",
            "height",
            "peak_start_rt",
            "peak_end_rt",
            "rt_delta_sec",
            "primary_matrix_area",
            "primary_matrix_area_source",
            "gap_fill_state",
            "gap_fill_reason",
            "group_claim_state",
            "consolidation_state",
            "peak_hypothesis_status",
            "product_selection_blocker",
            "rt_mode_status",
            "backfill_evidence_level",
            "backfill_evidence_status",
            "backfill_evidence_reason",
        ),
    )
    _write_tsv(
        alignment_dir / "alignment_matrix.tsv",
        [{"Mz": "300.3000", "RT": "10.0", "S_DET": "100", "S_REVIEW": ""}],
        ("Mz", "RT", "S_DET", "S_REVIEW"),
    )
    _write_tsv(
        alignment_dir / "alignment_matrix_identity.tsv",
        [
            current_matrix_identity_row(
                index=1,
                mz="300.3",
                rt="10.0",
                peak_hypothesis_id="FAM_STALE",
                sample_values={"S_DET": "100", "S_REVIEW": ""},
            ),
        ],
        ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
    )
    _write_tsv(
        alignment_dir / "retained_gate.tsv",
        [_gate_row("FAM_STALE", "S_DET;S_REVIEW", detected="1")],
        (
            "feature_family_id",
            "seed_group_id",
            "seed_group_basis",
            "seed_mz",
            "seed_rt",
            "suggested_rt_min",
            "suggested_rt_max",
            "detected_cell_count",
            "rescued_cell_count",
            "seed_source_samples",
            "support_components",
            "challenge_blockers",
            "missing_evidence",
            "evidence_gate_status",
            "overlay_family_verdict",
            "overlay_png_path",
        ),
    )
    output_dir = tmp_path / "projection"

    code = projection_cli.main(
        [
            "--alignment-review-tsv",
            str(alignment_dir / "alignment_review.tsv"),
            "--alignment-cells-tsv",
            str(alignment_dir / "alignment_cells.tsv"),
            "--retained-gate-tsv",
            str(alignment_dir / "retained_gate.tsv"),
            "--alignment-matrix-tsv",
            str(alignment_dir / "alignment_matrix.tsv"),
            "--alignment-matrix-identity-tsv",
            str(alignment_dir / "alignment_matrix_identity.tsv"),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    rows = _read_tsv(output_dir / "shadow_production_projection_cells.tsv")
    review = next(row for row in rows if row["sample_stem"] == "S_REVIEW")
    assert review["current_matrix_written"] == "FALSE"
    assert review["shadow_decision"] == "context"
    assert review["projected_matrix_written"] == "FALSE"
    assert review["projected_matrix_value"] == "250"


def test_cli_fails_when_gate_schema_omits_challenge_blockers(tmp_path: Path) -> None:
    alignment_dir = tmp_path / "alignment"
    alignment_dir.mkdir()
    _write_tsv(
        alignment_dir / "alignment_review.tsv",
        [
            {
                "feature_family_id": "FAM_FAIL",
                "neutral_loss_tag": "DNA_dR",
                "detected_count": "1",
            },
        ],
        ("feature_family_id", "neutral_loss_tag", "detected_count"),
    )
    _write_tsv(
        alignment_dir / "alignment_cells.tsv",
        [_cell_row("FAM_FAIL", "S_REVIEW", "backfilled")],
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "area",
            "apex_rt",
            "height",
            "peak_start_rt",
            "peak_end_rt",
            "rt_delta_sec",
            "primary_matrix_area",
            "gap_fill_state",
            "gap_fill_reason",
            "group_claim_state",
            "consolidation_state",
            "peak_hypothesis_status",
            "product_selection_blocker",
            "rt_mode_status",
        ),
    )
    gate = _gate_row("FAM_FAIL", "S_REVIEW", detected="1")
    del gate["challenge_blockers"]
    _write_tsv(
        alignment_dir / "retained_gate.tsv",
        [gate],
        tuple(gate),
    )

    code = projection_cli.main(
        [
            "--alignment-review-tsv",
            str(alignment_dir / "alignment_review.tsv"),
            "--alignment-cells-tsv",
            str(alignment_dir / "alignment_cells.tsv"),
            "--retained-gate-tsv",
            str(alignment_dir / "retained_gate.tsv"),
            "--output-dir",
            str(tmp_path / "projection"),
        ],
    )

    assert code == 2


def _production_decisions(
    *,
    cells: tuple[ProductionCellDecision, ...],
) -> ProductionDecisionSet:
    rows: dict[str, ProductionRowDecision] = {}
    cells_by_key = {(cell.feature_family_id, cell.sample_stem): cell for cell in cells}
    families = sorted({cell.feature_family_id for cell in cells})
    for family in families:
        family_cells = [cell for cell in cells if cell.feature_family_id == family]
        rows[family] = ProductionRowDecision(
            feature_family_id=family,
            include_in_primary_matrix=any(
                cell.write_matrix_value for cell in family_cells
            ),
            identity_decision="production_family",
            identity_confidence="review",
            primary_evidence="unit_test",
            identity_reason="unit_test",
            quantifiable_detected_count=sum(
                1 for cell in family_cells if cell.production_status == "detected"
            ),
            quantifiable_backfill_count=sum(
                1 for cell in family_cells if cell.raw_status == "backfilled"
            ),
            accepted_cell_count=sum(
                1 for cell in family_cells if cell.write_matrix_value
            ),
            detected_count=sum(
                1 for cell in family_cells if cell.raw_status == "detected"
            ),
            accepted_backfill_count=sum(
                1
                for cell in family_cells
                if cell.production_status == "accepted_backfill"
            ),
            review_backfill_count=sum(
                1
                for cell in family_cells
                if cell.production_status == "review_backfill"
            ),
            duplicate_assigned_count=0,
            ambiguous_ms1_owner_count=0,
            row_flags=(),
        )
    return ProductionDecisionSet(cells=cells_by_key, rows=rows)


def _cell_decision(
    family: str,
    sample: str,
    status: str,
    write: bool,
    value: float | None,
    *,
    blank_reason: str = "",
) -> ProductionCellDecision:
    return ProductionCellDecision(
        feature_family_id=family,
        sample_stem=sample,
        raw_status=(
            "backfilled"
            if status in {"review_backfill", "accepted_backfill"}
            else status
        ),
        production_status=status,  # type: ignore[arg-type]
        backfill_tier="review_backfill" if status == "review_backfill" else "",
        write_matrix_value=write,
        matrix_value=value,
        blank_reason=blank_reason,
    )


def _cell_row(
    family: str,
    sample: str,
    status: str,
    *,
    area: str = "200.0",
    apex_rt: str = "9.50",
    start: str = "9.40",
    end: str = "9.60",
    gap_fill_state: str = "owner_backfill",
    gap_fill_reason: str = "owner_backfill",
    group_claim_state: str = "",
    consolidation_state: str = "",
    peak_hypothesis_status: str = "",
    product_selection_blocker: str = "",
    rt_mode_status: str = "",
    activation_contract_rule_id: str = "",
    activation_product_effect: str = "",
    activation_reason: str = "",
    group_hypothesis_id: str = "",
    peak_hypothesis_id: str = "",
    **extra_fields: str,
) -> dict[str, str]:
    row = {
        "feature_family_id": family,
        "sample_stem": sample,
        "status": status,
        "area": area,
        "primary_matrix_area": "",
        "apex_rt": apex_rt,
        "peak_start_rt": start,
        "peak_end_rt": end,
        "gap_fill_state": gap_fill_state,
        "gap_fill_reason": gap_fill_reason,
        "group_claim_state": group_claim_state,
        "consolidation_state": consolidation_state,
        "peak_hypothesis_status": peak_hypothesis_status,
        "product_selection_blocker": product_selection_blocker,
        "rt_mode_status": rt_mode_status,
    }
    if activation_contract_rule_id:
        row["activation_contract_rule_id"] = activation_contract_rule_id
    if activation_product_effect:
        row["activation_product_effect"] = activation_product_effect
    if activation_reason:
        row["activation_reason"] = activation_reason
    if group_hypothesis_id:
        row["group_hypothesis_id"] = group_hypothesis_id
    if peak_hypothesis_id:
        row["peak_hypothesis_id"] = peak_hypothesis_id
    row.update(extra_fields)
    return row


def _gate_row(
    family: str,
    samples: str,
    *,
    detected: str,
    rt_min: str = "9.00",
    rt_max: str = "10.00",
) -> dict[str, str]:
    return {
        "feature_family_id": family,
        "seed_group_id": f"seed::{family}::mz=269.145::rt=9.5::window=9-10::ppm=10",
        "seed_group_basis": "seed_audit",
        "seed_mz": "269.145",
        "seed_rt": "9.50",
        "suggested_rt_min": rt_min,
        "suggested_rt_max": rt_max,
        "detected_cell_count": detected,
        "rescued_cell_count": "1",
        "seed_source_samples": samples,
        "support_components": (
            "seed_request_provenance;ms1_shape_supports_family_backfill"
        ),
        "challenge_blockers": "",
        "missing_evidence": "",
        "evidence_gate_status": "visual_support",
        "overlay_family_verdict": "ms1_shape_supports_family_backfill",
        "overlay_png_path": "plots/fam.png",
    }


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: tuple[str, ...],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
