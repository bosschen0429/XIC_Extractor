import csv
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from tests.discovery_store_fixtures import single_peak_sample, write_discovery
from tests.untargeted_partition_fixtures import ComposedChildOutputs, children_of
from xic_extractor.alignment.cell_decision.owner import (
    NoWriteCellDecision,
    NoWriteState,
    NumericCellDecision,
    ProductionCellDecision,
)
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.result.scientific_alignment import (
    ObservedSearchSubject,
)
from xic_extractor.output.alignment.tsv_writer import (
    project_untarget_child_rows,
    untarget_sample_order,
    write_alignment_tsvs,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import MeasuredPeakHypothesis


def write_batch(
    tmp_path: Path,
    sample_order: tuple[str, ...],
    *,
    raw_files: dict[str, str] | None = None,
) -> Path:
    """One admitted candidate per sample, published as a Discovery file."""
    raw_files = raw_files or {}
    batch_dir = tmp_path / "batch"
    return write_discovery(
        batch_dir,
        [
            single_peak_sample(
                batch_dir,
                stem,
                raw_file=Path(raw_files.get(stem, f"C:/stale/{stem}.raw")),
                peak_id=candidate_id(stem),
            )
            for stem in sample_order
        ],
    )


def candidate_id(sample_stem: str) -> str:
    return f"{sample_stem}@mz500_rt8.5000"


@dataclass(frozen=True)
class WriterPeak:
    """A member peak as the writers read it: key, measured m/z and apex RT."""

    physical_peak_key: str
    mz: float
    rt: float
    sample_key: str = ""
    intensity: tuple[float, ...] = ()

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
    def rt_apex_min(self) -> float:
        return self.rt

    @property
    def integration(self) -> SimpleNamespace:
        return SimpleNamespace(rt_apex_min=self.rt_apex_min)


@dataclass(frozen=True)
class WriterEvidence:
    """The ``FinalGroupCellObservation`` facts the Alignment writers read."""

    direct_observations: tuple[object, ...] = ()
    own_cid_nl_support: bool = False
    ms1_only_group_observations: tuple[object, ...] = ()
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
    backfill_query: tuple[float, float] | None = None,
) -> ProductionCellDecision:
    """One owner cell decision as the writers read it.

    A cell with an origin and an area is numeric. A detected cell without an
    area keeps its direct observation and is ``not_quantifiable``. A cell without
    an origin is the ``no_write`` state and has no peak. ``backfill_query`` is
    the RT window of a retained group-bounded Backfill search; a backfilled
    cell has one by default. ``writer_batch`` binds the cell to its sample,
    group and peak.
    """

    peak = () if origin is None else (None,)
    if backfill_query is None and origin == "backfilled":
        backfill_query = (8.0, 9.0)
    assessments = (
        ()
        if backfill_query is None
        else (
            SimpleNamespace(
                group_backfill=True,
                evaluation_receipt=SimpleNamespace(
                    query_rt_min=backfill_query[0],
                    query_rt_max=backfill_query[1],
                ),
            ),
        )
    )
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
            own_cid_nl_support=origin == "detected",
            ms1_only_backfill_observations=peak if origin == "backfilled" else (),
            method_assessments=assessments,
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


@dataclass(frozen=True)
class WriterBatch:
    """Writer-facing children not yet projected into their written rows."""

    canonical_samples: tuple[Any, ...]
    children: tuple[Any, ...]


def projected(batch: Any, **inputs: Any) -> Any:
    """``batch`` with its children projected as composition projects them."""

    if not isinstance(batch, WriterBatch):
        return batch
    sample_order = untarget_sample_order(batch.canonical_samples)
    return SimpleNamespace(
        canonical_samples=batch.canonical_samples,
        child_outputs=tuple(
            ComposedChildOutputs(
                project_untarget_child_rows(
                    child,
                    outputs=("matrix", "backfill", "cells", "status"),
                    sample_order=sample_order,
                    **inputs,
                ),
                None,
                child,
            )
            for child in batch.children
        ),
    )


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
            inference_basis=None,
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
    return WriterBatch(
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

    write_alignment_tsvs({kind: path}, projected(batch, **inputs))
    return path


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def written_cells(
    completed: Any,
) -> Iterator[tuple[Any, str, ProductionCellDecision]]:
    """Every written cell with its group and sample, in writer order."""

    sample_order = untarget_sample_order(completed.canonical_samples)
    for child in _children(completed):
        for group, cells in child.group_cells:
            by_stem = {cell.subject.canonical_sample.raw_stem: cell for cell in cells}
            for sample_stem in sample_order:
                yield group, sample_stem, by_stem[sample_stem]


def written_groups(completed: Any) -> tuple[Any, ...]:
    """Every finalized group in written order."""

    return tuple(
        group for child in _children(completed) for group, _cells in child.group_cells
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
        kind: directory / f"{kind}.tsv" for kind in ("matrix", "backfill", "cells")
    }
    write_alignment_tsvs(paths, projected(completed, **inputs))
    return {kind: read_tsv(path) for kind, path in paths.items()}


def _children(completed: Any) -> tuple[Any, ...]:
    if isinstance(completed, WriterBatch):
        return completed.children
    return children_of(completed)


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
        intensity=(1.0 + sum(map(ord, suffix)) / 1000.0, 100.0, 1.0),
        integration=replace(_integration(apex_rt), raw_scan_indices=(0, 1, 2)),
        generation_provenance=None,
        source_trace=None,
    )
    hypothesis_id = f"{sample_stem}|hypothesis|{suffix}"
    return SampleLocalMS1Owner(
        hypothesis=MeasuredPeakHypothesis(hypothesis_id, "dda_cid_nl", physical_peak),
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        primary_identity_event=event,
        supporting_events=(),
        assignment_reason="owner_exact_apex_match",
    )
