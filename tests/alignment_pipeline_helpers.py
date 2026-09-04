import csv
from pathlib import Path
from types import SimpleNamespace

import xic_extractor.alignment.pipeline as pipeline_module
from xic_extractor.alignment.backfill.evaluation_coverage import (
    build_owner_backfill_evaluation_coverage,
    mint_owner_backfill_planning_epoch,
)
from xic_extractor.alignment.csv_io import read_discovery_candidates_csv
from xic_extractor.alignment.edge_scoring import OwnerEdgeEvidence
from xic_extractor.alignment.matrix import AlignedCell, AlignmentMatrix
from xic_extractor.alignment.models import AlignmentCluster
from xic_extractor.alignment.owner_backfill import OwnerBackfillResult
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.models import DISCOVERY_CANDIDATE_COLUMNS
from xic_extractor.processing_identity import build_acquisition_source_identity


def owner_backfill_result(
    *,
    cells=(),
    candidate_audit_rows=(),
    evaluation_coverage_context=None,
) -> OwnerBackfillResult:
    coverage = None
    if evaluation_coverage_context is not None:
        epoch = mint_owner_backfill_planning_epoch(
            processing_owners=evaluation_coverage_context.processing_owners,
            group_identities=(),
            samples=evaluation_coverage_context.samples,
            raw_sample_stems=evaluation_coverage_context.raw_sample_stems,
            alignment_config=evaluation_coverage_context.alignment_config,
            backend=evaluation_coverage_context.backend,
            window_strategy=evaluation_coverage_context.window_strategy,
            superwindow_span_factor=(
                evaluation_coverage_context.superwindow_span_factor
            ),
            validation_mode=evaluation_coverage_context.validation_mode,
            result_semantics=evaluation_coverage_context.result_semantics,
            scope=evaluation_coverage_context.scope,
            selected_family_ids=evaluation_coverage_context.selected_family_ids,
            positions=(),
        )
        coverage = build_owner_backfill_evaluation_coverage(epoch, ())
    return OwnerBackfillResult(
        cells=tuple(cells),
        candidate_audit_rows=tuple(candidate_audit_rows),
        evaluation_coverage=coverage,
    )


def patch_owner_pipeline_to_matrix(monkeypatch) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "build_sample_local_owners",
        lambda candidates, **kwargs: SimpleNamespace(
            owners=(),
            assignments=(),
            ambiguous_records=(),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "cluster_sample_local_owners",
        lambda owners, **kwargs: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "review_only_features_from_ambiguous_records",
        lambda records, *, start_index: (),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_backfill_result",
        lambda features, *, sample_order, raw_sources, **kwargs: owner_backfill_result(
            evaluation_coverage_context=kwargs.get("evaluation_coverage_context"),
        ),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_owner_alignment_matrix",
        lambda features, *, sample_order, **kwargs: matrix(sample_order),
    )


class FakeRawOpener:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []
        self.handles: list[FakeRawContext] = []

    def __call__(
        self,
        raw_path: Path,
        dll_dir: Path,
        *,
        expected_acquisition_source=None,
    ):
        self.calls.append((raw_path, dll_dir))
        handle = FakeRawContext(raw_path)
        self.handles.append(handle)
        return handle


class FakeRawContext:
    def __init__(self, raw_path: Path) -> None:
        self.raw_path = raw_path
        self.entered = False
        self.closed = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def extract_xic(self, mz, rt_min, rt_max, ppm_tol):
        raise AssertionError("fake pipeline tests monkeypatch backfill")


def write_batch(
    tmp_path: Path,
    sample_order: tuple[str, ...],
    *,
    raw_files: dict[str, str] | None = None,
    owner_closed: bool = True,
) -> Path:
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir(exist_ok=True)
    raw_files = raw_files or {}
    rows = []
    for sample_stem in sample_order:
        sample_dir = batch_dir / sample_stem
        sample_dir.mkdir(exist_ok=True)
        candidates_csv = sample_dir / "discovery_candidates.csv"
        write_csv(
            candidates_csv,
            DISCOVERY_CANDIDATE_COLUMNS,
            [candidate_row(sample_stem)],
        )
        row = {
            "sample_stem": sample_stem,
            "raw_file": raw_files.get(sample_stem, f"C:/stale/{sample_stem}.raw"),
            "candidate_csv": str(candidates_csv.relative_to(batch_dir)),
            "review_csv": "",
        }
        if owner_closed:
            row.update(_owner_closed_receipt_fields(sample_stem, candidates_csv))
        rows.append(row)
    batch_index = batch_dir / "discovery_batch_index.csv"
    write_csv(
        batch_index,
        (
            (
                "schema_version",
                "sample_stem",
                "raw_file",
                "candidate_csv",
                "review_csv",
                "candidate_count",
                *DISCOVERY_BATCH_RECEIPT_COLUMNS,
            )
            if owner_closed
            else ("sample_stem", "raw_file", "candidate_csv", "review_csv")
        ),
        rows,
    )
    return batch_index


def write_partial_batch(
    tmp_path: Path,
    *,
    sample_order: tuple[str, ...],
    candidate_samples: tuple[str, ...],
    owner_closed: bool = True,
) -> Path:
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir(exist_ok=True)
    rows = []
    for sample_stem in sample_order:
        sample_dir = batch_dir / sample_stem
        sample_dir.mkdir(exist_ok=True)
        candidates_csv = sample_dir / "discovery_candidates.csv"
        candidate_rows = (
            [candidate_row(sample_stem)] if sample_stem in candidate_samples else []
        )
        write_csv(candidates_csv, DISCOVERY_CANDIDATE_COLUMNS, candidate_rows)
        row = {
            "sample_stem": sample_stem,
            "raw_file": f"C:/stale/{sample_stem}.raw",
            "candidate_csv": str(candidates_csv.relative_to(batch_dir)),
            "review_csv": "",
        }
        if owner_closed:
            row.update(_owner_closed_receipt_fields(sample_stem, candidates_csv))
        rows.append(row)
    batch_index = batch_dir / "discovery_batch_index.csv"
    write_csv(
        batch_index,
        (
            (
                "schema_version",
                "sample_stem",
                "raw_file",
                "candidate_csv",
                "review_csv",
                "candidate_count",
                *DISCOVERY_BATCH_RECEIPT_COLUMNS,
            )
            if owner_closed
            else ("sample_stem", "raw_file", "candidate_csv", "review_csv")
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
    acquisition_source = build_acquisition_source_identity(
        source_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    candidates = read_discovery_candidates_csv(candidates_csv)
    receipt = mint_discovery_candidate_universe_receipt(
        sample_stem=sample_stem,
        acquisition_source=acquisition_source,
        candidate_csv=candidates_csv,
        candidates=candidates,
    )
    return {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "candidate_count": str(len(candidates)),
        **discovery_receipt_index_fields(receipt),
    }


def write_owner_closed_partial_batch(
    tmp_path: Path,
    *,
    sample_order: tuple[str, ...],
    candidate_samples: tuple[str, ...],
    raw_files: dict[str, Path],
) -> Path:
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir(exist_ok=True)
    rows = []
    for sample_stem in sample_order:
        sample_dir = batch_dir / sample_stem
        sample_dir.mkdir(exist_ok=True)
        candidates_csv = sample_dir / "discovery_candidates.csv"
        candidate_rows = (
            [candidate_row(sample_stem)] if sample_stem in candidate_samples else []
        )
        write_csv(candidates_csv, DISCOVERY_CANDIDATE_COLUMNS, candidate_rows)
        candidates = read_discovery_candidates_csv(candidates_csv)
        acquisition_source = build_acquisition_source_identity(
            raw_files[sample_stem],
            acquisition_format="thermo_raw",
            observation_namespace="thermo_raw_file_reader:Device.MS:1",
        )
        receipt = mint_discovery_candidate_universe_receipt(
            sample_stem=sample_stem,
            acquisition_source=acquisition_source,
            candidate_csv=candidates_csv,
            candidates=candidates,
        )
        rows.append(
            {
                "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
                "sample_stem": sample_stem,
                "raw_file": str(raw_files[sample_stem]),
                "candidate_csv": str(candidates_csv.relative_to(batch_dir)),
                "review_csv": "",
                "candidate_count": str(len(candidates)),
                **discovery_receipt_index_fields(receipt),
            }
        )
    batch_index = batch_dir / "discovery_batch_index.csv"
    write_csv(
        batch_index,
        (
            "schema_version",
            "sample_stem",
            "raw_file",
            "candidate_csv",
            "review_csv",
            "candidate_count",
            *DISCOVERY_BATCH_RECEIPT_COLUMNS,
        ),
        rows,
    )
    return batch_index


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
    return f"{sample_stem}#1@mz500_p384"


def candidate_row(sample_stem: str) -> dict[str, str]:
    return {
        "review_priority": "HIGH",
        "evidence_tier": "A",
        "evidence_score": "80",
        "ms2_support": "strong",
        "ms1_support": "clean",
        "rt_alignment": "aligned",
        "discovery_candidate_state": "ms1_feature_nl_supported",
        "ms1_feature_row_id": f"{sample_stem}|DNA_dR|500|8.5",
        "candidate_id": candidate_id(sample_stem),
        "feature_family_id": f"{sample_stem}@F0001",
        "feature_family_size": "1",
        "precursor_mz": "500.0",
        "product_mz": "384.0",
        "observed_neutral_loss_da": "116.0",
        "best_seed_rt": "8.5",
        "seed_event_count": "2",
        "ms1_peak_found": "TRUE",
        "ms1_apex_rt": "8.5",
        "ms1_area": "1000.0",
        "ms2_product_max_intensity": "500.0",
        "reason": "seed",
        "raw_file": f"C:/stale/{sample_stem}.raw",
        "sample_stem": sample_stem,
        "best_ms2_scan_id": "1",
        "seed_scan_ids": "1;2",
        "neutral_loss_tag": "DNA_dR",
        "configured_neutral_loss_da": "116.0",
        "neutral_loss_mass_error_ppm": "1.0",
        "neutral_loss_error_basis": "measured_scan_precursor_product",
        "precursor_mz_basis": "scan_precursor",
        "scan_precursor_mz": "500.0",
        "scan_precursor_delta_da": "0.0",
        "max_scan_precursor_abs_delta_da": "0.0",
        "rt_seed_min": "8.4",
        "rt_seed_max": "8.6",
        "ms1_search_rt_min": "8.2",
        "ms1_search_rt_max": "8.8",
        "ms1_seed_delta_min": "",
        "ms1_peak_rt_start": "8.4",
        "ms1_peak_rt_end": "8.6",
        "ms1_height": "100.0",
        "ms1_trace_quality": "clean",
        "ms1_scan_support_score": "0.8",
        "selected_tag_count": "1",
        "matched_tag_count": "1",
        "matched_tag_names": "DNA_dR",
        "primary_tag_name": "DNA_dR",
        "tag_combine_mode": "single",
        "tag_intersection_status": "not_required",
        "tag_evidence_json": '{"DNA_dR":{"scan_count":2}}',
    }


def cluster(cluster_id: str = "ALN000001") -> AlignmentCluster:
    return AlignmentCluster(
        cluster_id=cluster_id,
        neutral_loss_tag="DNA_dR",
        cluster_center_mz=500.0,
        cluster_center_rt=8.5,
        cluster_product_mz=384.0,
        cluster_observed_neutral_loss_da=116.0,
        has_anchor=True,
        members=(),
        anchor_members=(),
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
                source_raw_file=Path(f"{sample_stem}.raw"),
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
        status="rescued",
        area=100.0,
        apex_rt=8.5,
        height=50.0,
        peak_start_rt=8.4,
        peak_end_rt=8.6,
        rt_delta_sec=0.0,
        trace_quality="owner_backfill",
        scan_support_score=None,
        source_candidate_id=None,
        source_raw_file=None,
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
        injection_order_gap=1,
        owner_quality="clean",
        seed_support_level="strong",
        duplicate_context="none",
        score=100,
        reason="strong_edge: score=100",
    )
