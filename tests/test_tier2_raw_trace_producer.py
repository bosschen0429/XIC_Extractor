from __future__ import annotations

import csv
from pathlib import Path

from xic_extractor.alignment.production_candidate_gate import (
    TIER2_DIAGNOSTIC_CRITERIA,
    TIER2_RAW_MANIFEST_REQUIRED_COLUMNS,
    TIER2_TRACE_EVIDENCE_REQUIRED_COLUMNS,
    GateSourceContext,
    evaluate_production_candidate_gate,
    load_tier2_trace_evidence,
    tier2_candidate_subset_signature,
)
from xic_extractor.alignment.tier2_trace_producer import (
    Tier2TraceProducerConfig,
    build_tier2_trace_evidence_rows,
)


def test_tier2_trace_producer_rows_are_diagnostic_only(
    tmp_path: Path,
) -> None:
    review_rows = [_candidate_review_row()]
    cell_rows = _candidate_cell_rows()
    source_context = _source_context(tmp_path)
    raw_manifest_path = tmp_path / "alignment_tier2_raw_manifest.tsv"
    _write_tsv(
        raw_manifest_path,
        _raw_manifest_rows(tmp_path, ("Seed_A", "Rescue_A", "Rescue_B")),
        TIER2_RAW_MANIFEST_REQUIRED_COLUMNS,
    )
    subset = tier2_candidate_subset_signature(review_rows)

    rows = build_tier2_trace_evidence_rows(
        candidate_rows=review_rows,
        cells_by_family={"FAM001": tuple(cell_rows)},
        source_context=source_context,
        raw_manifest_sha256=_sha256(raw_manifest_path),
        source_expected_sample_count=3,
        trace_loader=_passing_trace_loader,
        config=Tier2TraceProducerConfig(ppm_tolerance=20.0, rt_padding_min=0.02),
        producer_command="pytest fake producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
        dll_dir="C:\\Xcalibur\\system\\programs",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["feature_family_id"] == "FAM001"
    assert row["criteria_version"] == TIER2_DIAGNOSTIC_CRITERIA
    assert row["evidence_status"] == "inconclusive"
    assert row["support_component"] == ""
    assert row["raw_trace_reread_status"] == "pass"
    assert row["coherence_status"] == "inconclusive"
    assert "tier2_diagnostic_only" in row["challenge_blockers"]
    assert "neighbor_interference_not_assessed" in row["dependent_context"]
    assert row["source_candidate_subset_sha256"] == subset.sha256
    assert row["source_candidate_subset_count"] == "1"
    for column in (
        "scan_availability_score",
        "trace_apex_intensity",
        "trace_baseline_noise",
        "trace_signal_to_noise_proxy",
        "trace_apex_prominence_score",
        "scan_support_basis",
        "seed_rescued_boundary_overlap_min",
        "rescued_pairwise_boundary_overlap_min",
        "family_consensus_boundary_overlap_min",
        "seed_rescued_apex_span_sec",
        "rescued_only_apex_span_sec",
        "neighbor_interference_status",
    ):
        assert column in row
    assert row["scan_support_basis"] == "scan_count_only"
    assert row["neighbor_interference_status"] == "not_assessed"

    sidecar_path = tmp_path / "alignment_tier2_trace_evidence.tsv"
    _write_tsv(sidecar_path, rows, TIER2_TRACE_EVIDENCE_REQUIRED_COLUMNS)
    evidence = load_tier2_trace_evidence(
        sidecar_path=sidecar_path,
        raw_manifest_path=raw_manifest_path,
        candidate_rows=review_rows,
        source_context=source_context,
    )["FAM001"]
    decision = evaluate_production_candidate_gate(
        review_rows[0],
        cell_rows,
        source_context=source_context,
        tier2_evidence=evidence,
    )

    assert decision.candidate_gate_status == "audit"
    assert decision.tier2_evidence_available is False
    assert decision.support_components == ()
    assert "tier2_diagnostic_only" in decision.challenge_blockers


def test_tier2_trace_producer_emits_signal_shape_and_reference_views(
    tmp_path: Path,
) -> None:
    rows = build_tier2_trace_evidence_rows(
        candidate_rows=[_candidate_review_row()],
        cells_by_family={"FAM001": tuple(_candidate_cell_rows())},
        source_context=_source_context(tmp_path),
        raw_manifest_sha256="ABC123",
        source_expected_sample_count=3,
        trace_loader=_passing_trace_loader,
        config=Tier2TraceProducerConfig(ppm_tolerance=20.0, rt_padding_min=0.02),
        producer_command="pytest fake producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
        dll_dir="C:\\Xcalibur\\system\\programs",
    )

    row = rows[0]

    assert float(row["scan_availability_score"]) >= 1.0
    assert float(row["trace_apex_intensity"]) > 0.0
    assert float(row["trace_signal_to_noise_proxy"]) >= 0.0
    assert float(row["trace_apex_prominence_score"]) >= 0.0
    assert float(row["seed_rescued_boundary_overlap_min"]) > 0.0
    assert float(row["family_consensus_boundary_overlap_min"]) > 0.0
    assert float(row["seed_rescued_apex_span_sec"]) >= 0.0
    assert float(row["rescued_only_apex_span_sec"]) >= 0.0


def test_tier2_trace_producer_marks_raw_seed_trace_failures_inconclusive(
    tmp_path: Path,
) -> None:
    review_rows = [_candidate_review_row()]
    cell_rows = _candidate_cell_rows()
    source_context = _source_context(tmp_path)

    def failing_loader(
        *_args: object,
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        raise RuntimeError("raw read failed")

    rows = build_tier2_trace_evidence_rows(
        candidate_rows=review_rows,
        cells_by_family={"FAM001": tuple(cell_rows)},
        source_context=source_context,
        raw_manifest_sha256="ABC123",
        source_expected_sample_count=3,
        trace_loader=failing_loader,
        config=Tier2TraceProducerConfig(),
        producer_command="pytest fake producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
        dll_dir="C:\\Xcalibur\\system\\programs",
    )

    row = rows[0]
    assert row["evidence_status"] == "inconclusive"
    assert row["support_component"] == ""
    assert row["raw_trace_reread_status"] == "inconclusive"
    assert "raw_unavailable" in row["challenge_blockers"]
    assert "tier2_diagnostic_only" in row["challenge_blockers"]
    assert "neighbor_interference_not_assessed" in row["dependent_context"]
    assert "raw_trace_reread" in row["dependent_context"]
    assert row["neighbor_interference_status"] == "not_assessed"


def test_tier2_trace_producer_rejects_multiple_detected_seed_cells(
    tmp_path: Path,
) -> None:
    cell_rows = (
        _cell("Seed_A", "detected", "8.000", "7.960", "8.040", "1.0"),
        _cell("Seed_B", "detected", "8.010", "7.970", "8.050", "1.0"),
        _cell("Rescue_A", "backfilled", "8.020", "7.980", "8.060", "0.9"),
    )

    rows = build_tier2_trace_evidence_rows(
        candidate_rows=[_candidate_review_row()],
        cells_by_family={"FAM001": cell_rows},
        source_context=_source_context(tmp_path),
        raw_manifest_sha256="ABC123",
        source_expected_sample_count=3,
        trace_loader=_passing_trace_loader,
        config=Tier2TraceProducerConfig(),
        producer_command="pytest fake producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
        dll_dir="C:\\Xcalibur\\system\\programs",
    )

    row = rows[0]
    assert row["evidence_status"] == "blocked"
    assert row["raw_trace_reread_status"] == "fail"
    assert "multiple_detected_seed_cells" in row["challenge_blockers"]
    assert "tier2_diagnostic_only" in row["challenge_blockers"]


def test_tier2_trace_producer_marks_seed_metric_unavailable_coherence_inconclusive(
    tmp_path: Path,
) -> None:
    rows = build_tier2_trace_evidence_rows(
        candidate_rows=[_candidate_review_row()],
        cells_by_family={"FAM001": tuple(_candidate_cell_rows())},
        source_context=_source_context(tmp_path),
        raw_manifest_sha256="ABC123",
        source_expected_sample_count=3,
        trace_loader=_passing_trace_loader,
        config=Tier2TraceProducerConfig(scans_target=10, rt_padding_min=0.02),
        producer_command="pytest fake producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
        dll_dir="C:\\Xcalibur\\system\\programs",
    )

    row = rows[0]
    assert row["evidence_status"] == "inconclusive"
    assert row["raw_trace_reread_status"] == "inconclusive"
    assert row["coherence_status"] == "inconclusive"
    assert "metric_unavailable" in row["challenge_blockers"]
    assert "tier2_diagnostic_only" in row["challenge_blockers"]


def test_tier2_trace_producer_uses_contract_status_for_hard_metric_failure(
    tmp_path: Path,
) -> None:
    rows = build_tier2_trace_evidence_rows(
        candidate_rows=[_candidate_review_row()],
        cells_by_family={"FAM001": tuple(_candidate_cell_rows())},
        source_context=_source_context(tmp_path),
        raw_manifest_sha256="ABC123",
        source_expected_sample_count=3,
        trace_loader=_low_scan_trace_loader,
        config=Tier2TraceProducerConfig(scans_target=5, rt_padding_min=0.0),
        producer_command="pytest fake producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
        dll_dir="C:\\Xcalibur\\system\\programs",
    )

    row = rows[0]
    assert row["evidence_status"] == "blocked"
    assert row["support_component"] == ""
    assert row["raw_trace_reread_status"] == "fail"
    assert "low_scan_support" in row["challenge_blockers"]
    assert "tier2_diagnostic_only" in row["challenge_blockers"]
    assert "neighbor_interference_not_assessed" in row["dependent_context"]
    assert "raw_trace_reread" in row["dependent_context"]
    assert row["neighbor_interference_status"] == "not_assessed"


def test_tier2_trace_producer_uses_contract_status_for_backfilled_coherence_failure(
    tmp_path: Path,
) -> None:
    cell_rows = (
        _cell("Seed_A", "detected", "8.000", "7.960", "8.040", "1.0"),
        _cell("Rescue_A", "backfilled", "9.000", "8.960", "9.040", "0.9"),
        _cell("Rescue_B", "backfilled", "9.020", "8.980", "9.060", "0.9"),
    )

    rows = build_tier2_trace_evidence_rows(
        candidate_rows=[_candidate_review_row()],
        cells_by_family={"FAM001": tuple(cell_rows)},
        source_context=_source_context(tmp_path),
        raw_manifest_sha256="ABC123",
        source_expected_sample_count=3,
        trace_loader=_passing_trace_loader,
        config=Tier2TraceProducerConfig(ppm_tolerance=20.0, rt_padding_min=0.02),
        producer_command="pytest fake producer",
        generated_at_utc="2026-05-29T00:00:00Z",
        python_executable=".venv\\Scripts\\python.exe",
        dll_dir="C:\\Xcalibur\\system\\programs",
    )

    row = rows[0]
    assert row["evidence_status"] == "inconclusive"
    assert row["raw_trace_reread_status"] == "pass"
    assert row["coherence_status"] == "inconclusive"
    assert "tier2_diagnostic_only" in row["challenge_blockers"]
    assert "rescued_apex_span_wide" in row["challenge_blockers"]


def _candidate_review_row() -> dict[str, str]:
    return {
        "feature_family_id": "FAM001",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": "242.114",
        "family_center_rt": "8.000",
        "identity_decision": "provisional_discovery",
        "identity_confidence": "review",
        "identity_reason": "candidate",
        "primary_evidence": "owner_complete_link",
        "quantifiable_detected_count": "1",
        "quantifiable_backfill_count": "2",
        "duplicate_assigned_count": "0",
        "ambiguous_ms1_owner_count": "0",
        "row_flags": "single_detected_seed;provisional_retention_candidate",
        "include_in_primary_matrix": "FALSE",
    }


def _candidate_cell_rows() -> tuple[dict[str, str], ...]:
    return (
        _cell("Seed_A", "detected", "8.000", "7.960", "8.040", "1.0"),
        _cell("Rescue_A", "backfilled", "8.020", "7.980", "8.060", "0.9"),
        _cell("Rescue_B", "backfilled", "8.030", "7.990", "8.070", "0.9"),
    )


def _cell(
    sample_stem: str,
    status: str,
    apex_rt: str,
    start_rt: str,
    end_rt: str,
    scan_support_score: str,
) -> dict[str, str]:
    return {
        "feature_family_id": "FAM001",
        "sample_stem": sample_stem,
        "status": status,
        "area": "1000",
        "apex_rt": apex_rt,
        "height": "500",
        "peak_start_rt": start_rt,
        "peak_end_rt": end_rt,
        "rt_delta_sec": "0",
        "trace_quality": "ok",
        "scan_support_score": scan_support_score,
        "reason": "",
    }


def _passing_trace_loader(
    _sample_stem: str,
    _mz: float,
    rt_min: float,
    rt_max: float,
    _ppm_tolerance: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    step = (rt_max - rt_min) / 8.0
    rt = tuple(rt_min + step * index for index in range(9))
    center = (rt_min + rt_max) / 2.0
    intensity = tuple(1000.0 - abs(value - center) * 10000.0 for value in rt)
    return rt, intensity


def _low_scan_trace_loader(
    _sample_stem: str,
    _mz: float,
    rt_min: float,
    rt_max: float,
    _ppm_tolerance: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    step = (rt_max - rt_min) / 4.0
    rt = tuple(rt_min + step * index for index in range(5))
    return rt, tuple(0.0 for _ in rt)


def _source_context(tmp_path: Path) -> GateSourceContext:
    review = tmp_path / "alignment_review.tsv"
    cells = tmp_path / "alignment_cells.tsv"
    matrix = tmp_path / "alignment_matrix.tsv"
    review.write_text("review\n", encoding="utf-8")
    cells.write_text("cells\n", encoding="utf-8")
    matrix.write_text("matrix\n", encoding="utf-8")
    return GateSourceContext(
        review_path=review,
        review_sha256=_sha256(review),
        cell_path=cells,
        cell_sha256=_sha256(cells),
        matrix_path=matrix,
        matrix_sha256=_sha256(matrix),
    )


def _raw_manifest_rows(
    tmp_path: Path,
    sample_stems: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "sample_stem": sample_stem,
            "raw_file_path": str(tmp_path / f"{sample_stem}.raw"),
            "raw_file_size_bytes": "10",
            "raw_file_mtime_utc": "2026-05-29T00:00:00Z",
            "raw_reader_runtime": "pythonnet",
            "python_executable": ".venv\\Scripts\\python.exe",
            "dll_dir": "C:\\Xcalibur\\system\\programs",
        }
        for sample_stem in sample_stems
    )


def _write_tsv(
    path: Path,
    rows: tuple[dict[str, str], ...],
    columns: tuple[str, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
