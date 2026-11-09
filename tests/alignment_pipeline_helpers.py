import csv
import hashlib
import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleGroupMemberSubject,
)
from xic_extractor.alignment.edge_scoring import OwnerEdgeEvidence
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.production_decisions import (
    BackfillTier,
    ProductionCellDecision,
    ProductionDecisionSet,
    ProductionRowDecision,
    ProductionStatus,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    FinalizedUntargetMatrixRow,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    discovery_receipt_index_fields,
    mint_discovery_peak_universe_receipt,
)
from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
from xic_extractor.discovery.models import DISCOVERY_PEAK_COLUMNS
from xic_extractor.peak_detection.elution_context import (
    SampleElutionContext,
    project_elution_context,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservationContent
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.processing_identity import (
    FileContentFingerprint,
    build_local_acquisition_source_identity,
    fingerprint_file_content,
)


def physical_facts_fingerprint_beside(candidates_csv: Path) -> FileContentFingerprint:
    """Fingerprint the sample's physical-facts sidecar, or empty bytes if absent."""

    path = candidates_csv.with_name("discovery_physical_facts.json")
    if path.exists():
        return fingerprint_file_content(path)
    return FileContentFingerprint(
        content_sha256=hashlib.sha256(b"").hexdigest(), byte_size=0
    )


def resolved_elution_context(
    sample: str, peaks: tuple[tuple[float, float], ...], *, mz: float = 500.0
) -> SampleElutionContext:
    """Separated Gaussian peaks with physical ownership, not landmark-only hints."""
    apices = tuple(rt for rt, _ in peaks)
    rt = np.arange(max(0.0, min(apices) - 0.5), max(apices) + 0.5, 0.005)
    for apex in apices:
        rt[np.argmin(abs(rt - apex))] = apex
    signal = sum(
        height * np.exp(-0.5 * ((rt - apex) / 0.035) ** 2) for apex, height in peaks
    )
    context = project_elution_context(
        prepare_physical_signal(rt, signal), sample_stem=sample, mz=mz
    )
    assert context is not None
    assert tuple(p.apex_rt for p in context.landmarks) == tuple(sorted(apices))
    assert len({p.physical_segment for p in context.landmarks}) == len(peaks)
    assert all(p.physical_segment is not None for p in context.landmarks)
    return context


def write_batch(
    tmp_path: Path,
    sample_order: tuple[str, ...],
    *,
    raw_files: dict[str, str] | None = None,
) -> Path:
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir(exist_ok=True)
    raw_files = raw_files or {}
    rows = []
    for sample_stem in sample_order:
        sample_dir = batch_dir / sample_stem
        sample_dir.mkdir(exist_ok=True)
        candidates_csv = sample_dir / "discovery_peaks.csv"
        write_csv(
            candidates_csv,
            DISCOVERY_PEAK_COLUMNS,
            [candidate_row(sample_stem)],
        )
        row = {
            "sample_stem": sample_stem,
            "raw_file": raw_files.get(sample_stem, f"C:/stale/{sample_stem}.raw"),
            "peak_csv": str(candidates_csv.relative_to(batch_dir)),
            "event_csv": "",
        }
        row.update(_owner_closed_receipt_fields(sample_stem, candidates_csv))
        rows.append(row)
    batch_index = batch_dir / "discovery_batch_index.csv"
    write_csv(
        batch_index,
        (
            "schema_version",
            "sample_stem",
            "raw_file",
            "peak_csv",
            "event_csv",
            "peak_count",
            *DISCOVERY_BATCH_RECEIPT_COLUMNS,
        ),
        rows,
    )
    return batch_index


def _owner_closed_receipt_fields(
    sample_stem: str,
    candidates_csv: Path,
) -> dict[str, str]:
    """Build a test-only receipt from one stable synthetic acquisition."""

    source_path = candidates_csv.with_name("owner_closed_acquisition.raw")
    source_path.write_bytes(f"owner-closed-acquisition:{sample_stem}".encode())
    acquisition_source = build_local_acquisition_source_identity(
        source_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    candidates = read_discovery_peaks_csv(candidates_csv)
    receipt = mint_discovery_peak_universe_receipt(
        sample_stem=sample_stem,
        acquisition_source=acquisition_source,
        peak_csv=candidates_csv,
        physical_facts=physical_facts_fingerprint_beside(candidates_csv),
        peaks=candidates,
    )
    return {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "peak_count": str(len(candidates)),
        **discovery_receipt_index_fields(receipt),
    }


def write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def candidate_id(sample_stem: str) -> str:
    return f"{sample_stem}@mz500_rt8.5000"


def candidate_row(sample_stem: str) -> dict[str, str]:
    return {
        "review_priority": "HIGH",
        "evidence_tier": "A",
        "evidence_score": "80",
        "ms2_support": "strong",
        "ms1_support": "clean",
        "rt_alignment": "aligned",
        "status": "admitted",
        "peak_id": candidate_id(sample_stem),
        "peak_mz": "500.0",
        "xic_mz": "500.0",
        "best_event_precursor_mz": "500.0",
        "best_event_product_mz": "384.0",
        "best_event_neutral_loss_da": "116.0",
        "best_event_rt": "8.5",
        "attached_event_count": "2",
        "ms1_apex_rt": "8.5",
        "ms1_area": "1000.0",
        "ms2_product_max_intensity": "500.0",
        "reason": "seed",
        "raw_file": f"C:/stale/{sample_stem}.raw",
        "sample_stem": sample_stem,
        "best_ms2_scan_id": "1",
        "event_scan_ids": "1;2",
        "neutral_loss_tag": "DNA_dR",
        "configured_neutral_loss_da": "116.0",
        "neutral_loss_mass_error_ppm": "1.0",
        "neutral_loss_error_basis": "measured_scan_precursor_product",
        "precursor_mz_basis": "scan_precursor",
        "scan_precursor_mz": "500.0",
        "scan_precursor_delta_da": "0.0",
        "max_scan_precursor_abs_delta_da": "0.0",
        "event_rt_min": "8.4",
        "event_rt_max": "8.6",
        "ms1_search_rt_min": "8.2",
        "ms1_search_rt_max": "8.8",
        "ms1_event_delta_min": "0.0",
        "ms1_peak_rt_start": "8.4",
        "ms1_peak_rt_end": "8.6",
        "ms1_height": "100.0",
        "ms1_trace_quality": "clean",
        "is_provisional": "FALSE",
        "ms1_scan_support_score": "0.8",
        "selected_tag_count": "1",
        "matched_tag_count": "1",
        "matched_tag_names": "DNA_dR",
        "primary_tag_name": "DNA_dR",
        "tag_combine_mode": "single",
        "tag_intersection_status": "not_required",
        "tag_evidence_json": '{"DNA_dR":{"scan_count":2}}',
    }


def cluster(cluster_id: str = "ALN000001") -> FinalizedUntargetMatrixRow:
    return FinalizedUntargetMatrixRow(
        feature_id=cluster_id,
        group_hypothesis_id=cluster_id,
        destination="matrix",
        family_center_mz=500.0,
        family_center_rt=8.5,
        physical_peak_keys=(),
        family_product_mz=384.0,
        family_observed_neutral_loss_da=116.0,
        neutral_loss_tag="DNA_dR",
        has_anchor=True,
    )


def matrix(sample_order: tuple[str, ...]) -> AlignmentMatrix:
    return AlignmentMatrix(
        clusters=(cluster(),),
        cells=tuple(
            AlignedCell(
                sample_stem=sample_stem,
                cluster_id="ALN000001",
                status="detected",
                area=100.0,
                apex_rt=8.5,
                height=50.0,
                peak_start_rt=8.4,
                peak_end_rt=8.6,
                rt_delta_sec=0.0,
                trace_quality="clean",
                scan_support_score=0.8,
                source_candidate_id=candidate_id(sample_stem),
                reason="detected",
            )
            for sample_stem in sample_order
        ),
        sample_order=sample_order,
    )


def cell(sample_stem: str, *, cluster_id: str) -> AlignedCell:
    return AlignedCell(
        sample_stem=sample_stem,
        cluster_id=cluster_id,
        status="backfilled",
        area=100.0,
        apex_rt=8.5,
        height=50.0,
        peak_start_rt=8.4,
        peak_end_rt=8.6,
        rt_delta_sec=0.0,
        trace_quality="owner_backfill",
        scan_support_score=None,
        source_candidate_id=None,
        reason="owner-centered MS1 backfill",
    )


def peak_config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("."),
        dll_dir=Path("."),
        output_csv=Path("output.csv"),
        diagnostics_csv=Path("diagnostics.csv"),
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )


def owner_edge_evidence() -> OwnerEdgeEvidence:
    return OwnerEdgeEvidence(
        left_owner_id="OWN-Sample_A-000001",
        right_owner_id="OWN-Sample_B-000001",
        left_sample_stem="Sample_A",
        right_sample_stem="Sample_B",
        neutral_loss_tag="DNA_dR",
        left_precursor_mz=500.0,
        right_precursor_mz=500.1,
        left_rt_min=8.5,
        right_rt_min=8.6,
        decision="strong_edge",
        failure_reason="",
        rt_raw_delta_sec=6.0,
        rt_drift_corrected_delta_sec=1.0,
        drift_prior_source="targeted_istd_trend",
        rt_basis="drift_corrected",
        injection_order_gap=1,
        owner_quality="clean",
        seed_support_level="strong",
        duplicate_context="none",
        score=100,
        reason="strong_edge: score=100",
        failure_allows_rt_continuity=False,
    )


def _integration(apex_rt: float) -> IntegrationResult:
    return IntegrationResult(
        rt_left_min=apex_rt - 0.05,
        rt_apex_min=apex_rt,
        rt_right_min=apex_rt + 0.05,
        raw_apex_rt_min=apex_rt,
        rt_width_min=0.1,
        height_raw=100.0,
        height_smoothed=100.0,
        area_raw_counts_seconds=1_000.0,
        area_baseline_corrected=900.0,
        baseline_type="asls",
        boundary_sources=("test_owner",),
        area_ms1_morphology=900.0,
        ms1_morphology_area_source="gaussian15_positive_asls_residual",
    )


def _owner(
    sample_stem: str,
    suffix: str,
    *,
    apex_rt: float = 8.5,
    neutral_loss_tag: str = "NL116",
    precursor_mz: float = 500.0,
    product_mz: float = 383.9526,
    observed_loss: float = 116.0474,
    evidence_score: int = 80,
    seed_event_count: int = 2,
    is_provisional: bool = False,
) -> SampleLocalMS1Owner:
    event = IdentityEvent(
        candidate_id=f"{sample_stem}#{suffix}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=observed_loss,
        seed_rt=apex_rt,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
    )
    physical_peak = PeakObservationContent(
        sample_key=f"test-sample:{sample_stem}",
        mz=precursor_mz,
        rt=(apex_rt - 0.05, apex_rt, apex_rt + 0.05),
        # The suffix separates distinct owners that share one apex in a sample.
        intensity=(1.0 + sum(map(ord, suffix)) / 1000.0, 100.0, 1.0),
        integration=replace(_integration(apex_rt), raw_scan_indices=(0, 1, 2)),
    )
    hypothesis_id = f"{sample_stem}|hypothesis|{suffix}"
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample_stem}-{suffix}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        precursor_mz=precursor_mz,
        owner_apex_rt=apex_rt,
        owner_peak_start_rt=apex_rt - 0.05,
        owner_peak_end_rt=apex_rt + 0.05,
        owner_area=1_000.0,
        owner_height=100.0,
        primary_identity_event=event,
        supporting_events=(),
        assignment_reason="owner_exact_apex_match",
        selected_integration=_integration(apex_rt),
        cross_sample_member_subject=CrossSampleGroupMemberSubject(
            sample_key=f"test-sample:{sample_stem}",
            subject_kind="peak_hypothesis",
            subject_id=hypothesis_id,
            physical_peaks=(physical_peak,),
        ),
        is_provisional=is_provisional,
    )


_OWNER_NO_WRITE_VARIANT = {
    "detected": "not_quantifiable",
    "backfilled": "not_quantifiable",
    "absent": "searched_no_observation",
    "unchecked": "not_searched",
    "ambiguous_ms1_owner": "unresolved",
    "duplicate_assigned": "rejected_observation",
}


def finalized_writer_decisions(matrix: AlignmentMatrix) -> ProductionDecisionSet:
    """Writer inputs shaped like the finalized Untargeted owner's decisions.

    A detected or backfilled cell with a finite positive area is numeric; every
    other cell is a no-write decision. Row inclusion follows ``destination``.
    """

    cells_by_row: dict[str, list[AlignedCell]] = {}
    for item in matrix.cells:
        cells_by_row.setdefault(item.cluster_id, []).append(item)
    cell_decisions = {}
    row_decisions = {}
    for row in matrix.clusters:
        row_cells = tuple(cells_by_row.get(row.feature_id, ()))
        decisions = tuple(
            _writer_cell_decision(row.feature_id, item) for item in row_cells
        )
        for decision in decisions:
            cell_decisions[(decision.feature_id, decision.sample_stem)] = decision
        numeric_count = sum(item.write_matrix_value for item in decisions)
        accepted_backfill = sum(
            item.production_status == "accepted_backfill" for item in decisions
        )
        row_decisions[row.feature_id] = ProductionRowDecision(
            feature_id=row.feature_id,
            include_in_primary_matrix=row.destination == "matrix",
            identity_decision=(
                "finalized_matrix"
                if row.destination == "matrix"
                else "finalized_archive"
            ),
            identity_confidence="owner_finalized",
            primary_evidence="completed_untarget_batch",
            identity_reason="test_route",
            quantifiable_detected_count=sum(
                item.write_matrix_value and item.raw_status == "detected"
                for item in decisions
            ),
            quantifiable_backfill_count=accepted_backfill,
            accepted_cell_count=numeric_count,
            detected_count=sum(item.status == "detected" for item in row_cells),
            accepted_backfill_count=accepted_backfill,
            review_backfill_count=0,
            duplicate_assigned_count=sum(
                item.status == "duplicate_assigned" for item in row_cells
            ),
            ambiguous_ms1_owner_count=sum(
                item.status == "ambiguous_ms1_owner" for item in row_cells
            ),
            row_flags=(f"finalized_route:{row.destination}",),
        )
    return ProductionDecisionSet(cells=cell_decisions, rows=row_decisions)


def _writer_cell_decision(feature_id: str, item: AlignedCell) -> ProductionCellDecision:
    # The owner selects the primary area from the chosen integration when present.
    area = item.matrix_area if item.selected_integration is not None else item.area
    write = (
        item.status in {"detected", "backfilled"}
        and isinstance(area, (int, float))
        and not isinstance(area, bool)
        and math.isfinite(area)
        and area > 0
    )
    if write and item.status == "detected":
        production_status: ProductionStatus = "detected"
        backfill_tier: BackfillTier = ""
    elif write:
        production_status = "accepted_backfill"
        backfill_tier = "accepted_backfill"
    else:
        production_status = "blank"
        backfill_tier = ""
    return ProductionCellDecision(
        feature_id=feature_id,
        sample_stem=item.sample_stem,
        raw_status=item.status,
        production_status=production_status,
        backfill_tier=backfill_tier,
        write_matrix_value=write,
        matrix_value=float(area) if write else None,
        blank_reason="" if write else _OWNER_NO_WRITE_VARIANT[item.status],
    )
