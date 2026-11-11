import csv
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np

from tests.owner_peak_fixtures import peak_query_provenance
from xic_extractor.alignment.cell_decision.owner import (
    NoWriteCellDecision,
    NoWriteState,
    NumericCellDecision,
    ProductionCellDecision,
)
from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleGroupMemberSubject,
)
from xic_extractor.alignment.edge_scoring import OwnerEdgeEvidence
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.result.scientific_alignment import (
    ObservedSearchSubject,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_COLUMNS,
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    discovery_receipt_index_fields,
    mint_discovery_peak_universe_receipt,
)
from xic_extractor.discovery.csv_reader import read_discovery_peaks_csv
from xic_extractor.discovery.models import DISCOVERY_PEAK_COLUMNS
from xic_extractor.output.alignment.tsv_writer import (
    completed_untarget_batch_sample_order,
    write_alignment_tsvs,
)
from xic_extractor.peak_detection.elution_context import (
    SampleElutionContext,
    project_elution_context,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservation
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
    write_csv(batch_index, DISCOVERY_BATCH_INDEX_COLUMNS, rows)
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
        "status": "admitted",
        "evidence_score": "80",
        "attached_event_count": "2",
        "peak_id": candidate_id(sample_stem),
        "sample_stem": sample_stem,
        "peak_mz": "500.0",
        "precursor_mz_basis": "scan_precursor",
        "ms1_apex_mz": "",
        "ms1_apex_rt": "8.5",
        "ms1_peak_rt_start": "8.4",
        "ms1_peak_rt_end": "8.6",
        "ms1_area": "1000.0",
        "ms1_height": "100.0",
        "best_ms2_scan_id": "1",
        "neutral_loss_tag": "DNA_dR",
        "matched_tag_names": "DNA_dR",
    }


@dataclass(frozen=True)
class WriterPeak:
    """A member peak as the writers read it: key, measured m/z and apex RT."""

    physical_peak_key: str
    mz: float
    rt: float

    @property
    def content(self) -> "WriterPeak":
        return self

    @property
    def physical_identity(self) -> "WriterPeak":
        return self

    @property
    def measured_order_key(self) -> str:
        return self.physical_peak_key

    @property
    def integration(self) -> SimpleNamespace:
        return SimpleNamespace(rt_apex_min=self.rt)


@dataclass(frozen=True)
class WriterEvidence:
    """The ``FinalGroupCellObservation`` facts the Alignment writers read."""

    direct_observations: tuple[object, ...] = ()
    ms1_only_backfill_observations: tuple[object, ...] = ()
    method_assessments: tuple[object, ...] = ()
    direct_hypotheses: tuple[object, ...] = ()
    group: object = None
    sample_key: str = ""
    effective_cells: tuple[object, ...] = ()


def owner_cell(
    origin: Literal["detected", "backfilled"] | None = "detected",
    *,
    area: float | None = 100.0,
    no_write: NoWriteState = "searched_no_observation",
    integration: IntegrationResult | None = None,
    source: str = "gaussian15_positive_asls_residual",
) -> ProductionCellDecision:
    """One owner cell decision as the writers read it.

    A cell with an origin and an area is numeric. A detected cell without an
    area keeps its direct observation and is ``not_quantifiable``. A cell without
    an origin is the ``no_write`` state and has no peak. ``writer_batch`` binds
    the cell to its sample, group and peak.
    """

    peak = () if origin is None else (None,)
    payload: NumericCellDecision | NoWriteCellDecision
    if origin is not None and area is not None:
        payload = NumericCellDecision(value=area, source=source)
    elif origin is not None:
        payload = NoWriteCellDecision("not_quantifiable")
    else:
        payload = NoWriteCellDecision(no_write)
    return ProductionCellDecision(
        subject=None,
        evidence=WriterEvidence(
            direct_observations=peak,
            ms1_only_backfill_observations=peak if origin == "backfilled" else (),
        ),
        payload=payload,
        selected_integration=(
            integration
            if integration is not None or origin is None or area is None
            else _integration(8.5)
        ),
    )


@dataclass(frozen=True)
class WriterGroup:
    """One synthetic finalized group and its cells, before sample binding."""

    cells: tuple[ProductionCellDecision, ...]
    candidates: tuple[tuple[str, str], ...] = ()
    center_mz: float = 500.0
    center_rt: float = 8.5
    neutral_loss_tag: str = "DNA_dR"
    product_mz: float | None = 384.0
    loss_da: float | None = 116.0
    has_anchor: bool = True
    member_count: int = 1
    destination: Literal["matrix", "archive"] = "matrix"
    disposition: str = "test_route"


def writer_group(*cells: ProductionCellDecision, **facts: Any) -> WriterGroup:
    return WriterGroup(cells, **facts)


def writer_batch(sample_order: tuple[str, ...], *groups: WriterGroup) -> Any:
    """A completed batch with one child holding ``groups`` in this order.

    Every cell with an origin carries one direct peak. The group's centre is
    ``center_mz``; the first ``member_count`` peaks are its member peaks, all
    measured at that centre, so the reported RT equals ``center_rt``.
    """

    samples = tuple(
        SimpleNamespace(raw_stem=stem, sample_name=stem, injection_order=index)
        for index, stem in enumerate(sample_order, start=1)
    )
    terminal_subjects = []
    group_cells = []
    for ordinal, spec in enumerate(groups, start=1):
        context = SimpleNamespace(
            group=SimpleNamespace(
                neutral_loss_tag=spec.neutral_loss_tag,
                group_product_mz=spec.product_mz,
                group_observed_neutral_loss_da=spec.loss_da,
                has_anchor=spec.has_anchor,
            )
        )
        members = []
        peaks: dict[str, WriterPeak] = {}
        for index, (stem, cell) in enumerate(
            zip(sample_order, spec.cells, strict=True)
        ):
            if not cell.evidence.direct_observations:
                continue
            peak = WriterPeak(f"F{ordinal:06d}#peak-{index}", 0.0, 0.0)
            if len(members) < spec.member_count:
                peak = replace(peak, mz=spec.center_mz, rt=spec.center_rt)
                members.append(
                    SimpleNamespace(
                        sample_key=stem,
                        subject_id=f"{ordinal}:{stem}",
                        physical_peaks=(peak,),
                    )
                )
            peaks[stem] = peak
        group = SimpleNamespace(
            group_hypothesis_id=f"group-{ordinal}",
            group_center_mz=spec.center_mz,
            member_subjects=tuple(members),
            destination=spec.destination,
            disposition=spec.disposition,
        )
        candidates = dict(spec.candidates)
        cells = []
        for stem, cell, sample in zip(sample_order, spec.cells, samples, strict=True):
            hypotheses: tuple[object, ...] = ()
            if stem in peaks:
                hypotheses = (SimpleNamespace(hypothesis_id=f"{ordinal}:{stem}"),)
            if stem in candidates:
                (hypothesis,) = hypotheses
                terminal_subjects.append(
                    ObservedSearchSubject(
                        candidate_id=candidates[stem],
                        sample_key=stem,
                        trace_group_id=f"trace:{ordinal}:{stem}",
                        hypothesis=hypothesis,  # type: ignore[arg-type]
                    )
                )
            evidence = cell.evidence
            observed = (peaks[stem],) if stem in peaks else ()
            cells.append(
                replace(
                    cell,
                    subject=SimpleNamespace(canonical_sample=sample),
                    evidence=replace(
                        evidence,
                        direct_observations=observed,
                        ms1_only_backfill_observations=(
                            observed if evidence.ms1_only_backfill_observations else ()
                        ),
                        direct_hypotheses=hypotheses,
                        group=group,
                        sample_key=stem,
                        effective_cells=(SimpleNamespace(group_context=context),),
                    ),
                )
            )
        group_cells.append((group, tuple(cells)))
    return SimpleNamespace(
        canonical_samples=samples,
        children=(
            SimpleNamespace(
                scientific_core=SimpleNamespace(
                    terminal_subjects=tuple(terminal_subjects),
                    grouping_result=SimpleNamespace(backfill_membership_risks=()),
                ),
                group_cells=tuple(group_cells),
            ),
        ),
    )


def render(kind: str, path: Path, batch: Any, **inputs: Any) -> Path:
    """Write one Alignment TSV of ``batch`` and return its path.

    ``kind`` is ``matrix``, ``review``, ``backfill``, ``cells`` or ``status``.
    """

    write_alignment_tsvs({kind: path}, batch, **inputs)
    return path


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def written_cells(
    completed: Any,
) -> Iterator[tuple[Any, str, ProductionCellDecision]]:
    """Every written cell with its group and sample, in writer order."""

    sample_order = completed_untarget_batch_sample_order(completed)
    for child in completed.children:
        for group, cells in child.group_cells:
            by_stem = {cell.subject.canonical_sample.raw_stem: cell for cell in cells}
            for sample_stem in sample_order:
                yield group, sample_stem, by_stem[sample_stem]


def written_groups(completed: Any) -> tuple[Any, ...]:
    """Every finalized group in written order."""

    return tuple(
        group for child in completed.children for group, _cells in child.group_cells
    )


def written_source_candidates(
    directory: Path, completed: Any
) -> dict[tuple[str, str], str]:
    """Each written cell's source candidate ("" for none), by group and sample."""

    feature_ids = {
        f"F{ordinal:06d}": group.group_hypothesis_id
        for ordinal, group in enumerate(written_groups(completed), start=1)
    }
    return {
        (feature_ids[row["feature_id"]], row["sample_stem"]): row["source_candidate_id"]
        for row in written_rows(directory, completed)["cells"]
    }


def written_rows(
    directory: Path, completed: Any, **inputs: Any
) -> dict[str, list[dict[str, str]]]:
    """Every Alignment TSV of ``completed``, read back as rows by file kind."""

    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        kind: directory / f"{kind}.tsv"
        for kind in ("matrix", "review", "backfill", "cells", "status")
    }
    write_alignment_tsvs(paths, completed, **inputs)
    return {kind: read_tsv(path) for kind, path in paths.items()}


def matrix(sample_order: tuple[str, ...]) -> Any:
    """One detected group whose every sample reads its own Discovery candidate."""

    return writer_batch(
        sample_order,
        writer_group(
            *(owner_cell() for _ in sample_order),
            candidates=tuple(
                (sample_stem, candidate_id(sample_stem)) for sample_stem in sample_order
            ),
        ),
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
    physical_peak = PeakObservation(
        sample_key=f"test-sample:{sample_stem}",
        mz=precursor_mz,
        rt=(apex_rt - 0.05, apex_rt, apex_rt + 0.05),
        # The suffix separates distinct owners that share one apex in a sample.
        intensity=(1.0 + sum(map(ord, suffix)) / 1000.0, 100.0, 1.0),
        integration=replace(_integration(apex_rt), raw_scan_indices=(0, 1, 2)),
        generation_provenance=peak_query_provenance(
            (apex_rt - 0.05, apex_rt, apex_rt + 0.05)
        ),
        source_trace=None,
    )
    hypothesis_id = f"{sample_stem}|hypothesis|{suffix}"
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample_stem}-{suffix}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        peak=physical_peak,
        primary_identity_event=event,
        supporting_events=(),
        assignment_reason="owner_exact_apex_match",
        cross_sample_member_subject=CrossSampleGroupMemberSubject(
            sample_key=f"test-sample:{sample_stem}",
            subject_kind="peak_hypothesis",
            subject_id=hypothesis_id,
            physical_peaks=(physical_peak,),
        ),
    )
