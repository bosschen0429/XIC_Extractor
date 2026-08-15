from __future__ import annotations

import csv
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    AlignmentRowLike,
)
from xic_extractor.alignment.production_decisions import (
    ProductionDecisionSet,
    build_production_decisions,
)
from xic_extractor.diagnostics.standard_peak_hold_diagnostic import (
    StandardPeakDiagnosticEvidence,
    StandardPeakDiagnosticResult,
    build_standard_peak_hold_diagnostic_plan,
    explain_standard_peak_hold,
)
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult

_RETIRED_PRODUCT_AUTHORITY_PATHS = (
    "scripts/check_product_ready_preset_publication.py",
    "scripts/run_backfill_expansion_full_evidence_chain.py",
    "tools/diagnostics/dna_dr_product_ready_stage_replay.py",
    "tools/diagnostics/standard_peak_activation_scope_audit.py",
    "tools/diagnostics/standard_peak_backfill_chunk_consolidation.py",
    "tools/diagnostics/standard_peak_backfill_machine_pipeline.py",
    "tools/diagnostics/standard_peak_backfill_preset.py",
    "tools/diagnostics/standard_peak_backfill_productization.py",
    "tools/diagnostics/standard_peak_heldout_oracle_results.py",
    "tools/diagnostics/standard_peak_heldout_trace_oracle.py",
    "tools/diagnostics/standard_peak_ms1_authority_bundle.py",
    "tools/diagnostics/standard_peak_policy_observed_oracle.py",
    "tools/diagnostics/standard_peak_reintegration_stability_audit.py",
    "tools/diagnostics/standard_peak_shadow_activation_inputs.py",
    "xic_extractor/diagnostics/product_ready_preset_publication_check.py",
    "xic_extractor/diagnostics/standard_peak_activation_scope_audit.py",
    "xic_extractor/diagnostics/standard_peak_backfill_chunk_consolidation.py",
    "xic_extractor/diagnostics/standard_peak_backfill_productization.py",
    "xic_extractor/diagnostics/standard_peak_heldout_trace_oracle.py",
    "xic_extractor/diagnostics/standard_peak_ms1_authority_bundle.py",
    "xic_extractor/diagnostics/standard_peak_policy_observed_oracle.py",
    "xic_extractor/diagnostics/standard_peak_reintegration_stability_audit.py",
    "xic_extractor/diagnostics/standard_peak_shadow_activation_inputs.py",
)


def test_retired_standard_peak_product_authority_modules_are_absent() -> None:
    root = Path(__file__).resolve().parents[1]

    assert tuple(
        path
        for path in _RETIRED_PRODUCT_AUTHORITY_PATHS
        if (root / path).exists()
    ) == ()


def test_sp001_through_sp055_have_one_final_t5d_disposition() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = (
        root
        / "docs/superpowers/validation/standard_peak_t5d_contraction_v1"
        / "capability_disposition_v1.tsv"
    )
    with manifest.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle, delimiter="\t"))

    expected_ids = {f"SP-{index:03d}" for index in range(1, 56)}
    assert {row["rule_id"] for row in rows} == expected_ids
    assert len(rows) == len(expected_ids)
    assert {row["final_disposition"] for row in rows} <= {
        "owner_input",
        "diagnostic_oracle",
        "diagnostic_renderer",
        "delete",
    }
    for row in rows:
        if row["final_disposition"] == "delete":
            assert row["retained_locator"] == "deleted_by_t5d"
        else:
            assert (root / row["retained_locator"]).exists(), row["rule_id"]
    by_id = {row["rule_id"]: row for row in rows}
    for rule_id in (
        "SP-025",
        "SP-029",
        "SP-031",
        "SP-037",
        "SP-039",
        "SP-047",
        "SP-050",
        "SP-052",
        "SP-054",
        "SP-055",
    ):
        assert by_id[rule_id]["final_disposition"] == "delete"


def test_owner_replayed_hold_can_be_diagnosed_without_product_effect(
    tmp_path: Path,
) -> None:
    matrix, config, decisions = _owner_population()
    run_spec = _run_spec(tmp_path, matrix.sample_order)

    plan = build_standard_peak_hold_diagnostic_plan(
        matrix=matrix,
        config=config,
        production_decisions=decisions,
        processing_run_spec=run_spec,
        requested_cell_keys=(("FAM001", "held"),),
    )

    assert plan.source_population_size == 4
    assert plan.terminal_hold_count == 1
    assert len(plan.subjects) == 1
    subject = plan.subjects[0]
    assert subject.owner_hold_reason == "rt_outside_preferred"
    assert subject.processing_run_spec_id == run_spec.run_spec_id
    result = explain_standard_peak_hold(
        StandardPeakDiagnosticEvidence(
            subject=subject,
            dda_missingness="supports_explanation",
            multiple_peaks="contradicts_explanation",
            wrong_peak="unavailable",
            mode_conflict="unavailable",
            identity_conflict="unavailable",
            shape_or_shift="supports_explanation",
            source_artifact_ids=("artifact:sealed-overlay",),
        )
    )

    assert result.status == "explained"
    assert result.explanation_labels == (
        "possible_dda_missingness",
        "shape_or_shift_ambiguity",
    )
    assert result.contradicted_labels == ("multiple_peaks",)
    assert set(result.unavailable_labels) == {
        "wrong_peak",
        "mode_conflict",
        "identity_conflict",
    }
    assert tuple(field.name for field in fields(StandardPeakDiagnosticResult)) == (
        "diagnostic_subject_id",
        "status",
        "explanation_labels",
        "contradicted_labels",
        "unavailable_labels",
        "source_artifact_ids",
    )


@pytest.mark.parametrize(
    (
        "sample",
        "include_outside_max",
        "include_unavailable",
        "include_rejected",
        "expected_status",
    ),
    (
        ("detected-1", False, False, False, "detected"),
        ("outside-max", True, False, False, "review_rescue"),
        ("rejected", False, False, True, "rejected_rescue"),
        ("unavailable", False, True, False, "blank"),
    ),
)
def test_support_reject_and_unavailable_rows_fail_closed(
    tmp_path: Path,
    sample: str,
    include_outside_max: bool,
    include_unavailable: bool,
    include_rejected: bool,
    expected_status: str,
) -> None:
    matrix, config, decisions = _owner_population(
        include_outside_max=include_outside_max,
        include_unavailable=include_unavailable,
        include_rejected=include_rejected,
    )
    run_spec = _run_spec(tmp_path, matrix.sample_order)
    assert decisions.cell("FAM001", sample).production_status == expected_status

    with pytest.raises(ValueError, match="diagnostic_eligible terminal HOLD"):
        build_standard_peak_hold_diagnostic_plan(
            matrix=matrix,
            config=config,
            production_decisions=decisions,
            processing_run_spec=run_spec,
            requested_cell_keys=(("FAM001", sample),),
        )


def test_duplicate_cross_key_and_partial_population_fail_closed(tmp_path: Path) -> None:
    matrix, config, decisions = _owner_population()
    run_spec = _run_spec(tmp_path, matrix.sample_order)

    with pytest.raises(ValueError, match="duplicate requested diagnostic cell key"):
        build_standard_peak_hold_diagnostic_plan(
            matrix=matrix,
            config=config,
            production_decisions=decisions,
            processing_run_spec=run_spec,
            requested_cell_keys=(("FAM001", "held"), ("FAM001", "held")),
        )
    with pytest.raises(ValueError, match="unknown owner Backfill cell key"):
        build_standard_peak_hold_diagnostic_plan(
            matrix=matrix,
            config=config,
            production_decisions=decisions,
            processing_run_spec=run_spec,
            requested_cell_keys=(("FAM999", "held"),),
        )

    partial_run_spec = _run_spec(tmp_path / "partial", matrix.sample_order[:-1])
    with pytest.raises(ValueError, match="complete owner population"):
        build_standard_peak_hold_diagnostic_plan(
            matrix=matrix,
            config=config,
            production_decisions=decisions,
            processing_run_spec=partial_run_spec,
            requested_cell_keys=(("FAM001", "held"),),
        )


def test_stale_or_tampered_owner_decisions_fail_before_diagnostic(
    tmp_path: Path,
) -> None:
    matrix, config, decisions = _owner_population()
    run_spec = _run_spec(tmp_path, matrix.sample_order)
    cells = dict(decisions.cells)
    cells[("FAM001", "held")] = replace(
        cells[("FAM001", "held")],
        blank_reason="tampered",
    )
    tampered = ProductionDecisionSet(cells=cells, rows=decisions.rows)

    with pytest.raises(ValueError, match="live Owner Backfill replay"):
        build_standard_peak_hold_diagnostic_plan(
            matrix=matrix,
            config=config,
            production_decisions=tampered,
            processing_run_spec=run_spec,
            requested_cell_keys=(("FAM001", "held"),),
        )


def test_diagnostic_evidence_is_subject_bound_and_source_sealed(tmp_path: Path) -> None:
    matrix, config, decisions = _owner_population()
    run_spec = _run_spec(tmp_path, matrix.sample_order)
    subject = build_standard_peak_hold_diagnostic_plan(
        matrix=matrix,
        config=config,
        production_decisions=decisions,
        processing_run_spec=run_spec,
        requested_cell_keys=(("FAM001", "held"),),
    ).subjects[0]

    with pytest.raises(ValueError, match="source_artifact_ids"):
        StandardPeakDiagnosticEvidence(
            subject=subject,
            dda_missingness="supports_explanation",
        )
    with pytest.raises(ValueError, match="duplicate source_artifact_ids"):
        StandardPeakDiagnosticEvidence(
            subject=subject,
            dda_missingness="supports_explanation",
            source_artifact_ids=("artifact:one", "artifact:one"),
        )


def _owner_population(
    *,
    include_outside_max: bool = False,
    include_unavailable: bool = False,
    include_rejected: bool = False,
) -> tuple[AlignmentMatrix, AlignmentConfig, ProductionDecisionSet]:
    config = AlignmentConfig(preferred_rt_sec=10.0, max_rt_sec=30.0)
    cells = [
        _cell("detected-1", "detected", rt_delta_sec=0.0),
        _cell("detected-2", "detected", rt_delta_sec=0.0),
        _cell("detected-3", "detected", rt_delta_sec=0.0),
        _cell("held", "rescued", rt_delta_sec=20.0),
    ]
    if include_outside_max:
        cells.append(_cell("outside-max", "rescued", rt_delta_sec=31.0))
    if include_unavailable:
        cells.append(_cell("unavailable", "unchecked", rt_delta_sec=0.0))
    if include_rejected:
        cells.append(
            replace(
                _cell("rejected", "rescued", rt_delta_sec=0.0),
                area=None,
                selected_integration=None,
            )
        )
    matrix = AlignmentMatrix(
        clusters=(
            cast(
                AlignmentRowLike,
                SimpleNamespace(
                    feature_family_id="FAM001",
                    neutral_loss_tag="DNA_dR",
                    family_center_mz=500.123,
                    family_center_rt=8.49,
                    family_product_mz=384.076,
                    family_observed_neutral_loss_da=116.047,
                    has_anchor=True,
                    event_cluster_ids=("OWN-detected-1-000001",),
                    event_member_count=1,
                    evidence="owner_complete_link;owner_count=3",
                    review_only=False,
                ),
            ),
        ),
        cells=tuple(cells),
        sample_order=tuple(cell.sample_stem for cell in cells),
    )
    return matrix, config, build_production_decisions(matrix, config)


def _cell(sample: str, status: str, *, rt_delta_sec: float) -> AlignedCell:
    integration = IntegrationResult(
        rt_left_min=8.4,
        rt_apex_min=8.49,
        rt_right_min=8.6,
        raw_apex_rt_min=8.49,
        rt_width_min=0.2,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=80.0,
        area_baseline_corrected=80.0,
        baseline_type="asls",
        boundary_sources=("test",),
        area_ms1_morphology=80.0,
        ms1_morphology_area_source="gaussian15_positive_asls_residual",
    )
    return AlignedCell(
        sample_stem=sample,
        cluster_id="FAM001",
        status=status,  # type: ignore[arg-type]
        area=80.0,
        apex_rt=8.49,
        height=100.0,
        peak_start_rt=8.4,
        peak_end_rt=8.6,
        rt_delta_sec=rt_delta_sec,
        trace_quality="clean",
        scan_support_score=0.8,
        source_candidate_id=f"{sample}#1" if status == "detected" else None,
        source_raw_file=Path(f"{sample}.raw") if status == "detected" else None,
        reason=status,
        selected_integration=integration,
    )


def _run_spec(tmp_path: Path, samples: tuple[str, ...]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = []
    for sample in samples:
        path = tmp_path / f"{sample}.raw"
        path.write_bytes(sample.encode("utf-8"))
        paths.append(path)
    return compile_discovery_processing_run_spec(
        tuple(paths),
        discovery_settings=DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile(
                tag="DNA_dR",
                neutral_loss_da=116.0474,
            )
        ),
    )
