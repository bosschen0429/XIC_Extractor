"""Helpers shared by test_cell_observations and other test modules."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.cid_nl_assessment_fixtures import (
    _completed_receipt,
    _truth_table_fixture,
)
from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_unique_support,
)
from tests.cross_sample_association_lineage_fixtures import _final_receipt
from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
    BackfillPlanningPosition,
)
from xic_extractor.alignment.band_grouping import group_peak_bands
from xic_extractor.alignment.cid_nl_completion import _cell_planning_reason
from xic_extractor.alignment.cid_nl_predecessor_cells import (
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    mint_cid_nl_method_assessment,
)
from xic_extractor.sample_metadata import SampleMetadata


def _group_and_predecessor_cells(
    sample_assessments: tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...],
    *,
    known_label: str | None = None,
):
    """Arrange one fixture group using the real grouping and cell owners."""
    owners = tuple(
        owner_from_cid_nl_assessment_support(
            sample=sample, assessment=assessment, hypothesis=hypothesis
        )
        for sample, assessment in sample_assessments
        for hypothesis in compose_cid_nl_peak_hypotheses_from_assessment(
            sample=sample, assessment=assessment
        )
    )
    config = AlignmentConfig()
    rt_frame = SampleRtFrame.from_samples(
        tuple(sample for sample, _ in sample_assessments)
    )
    (group,) = group_peak_bands(
        owners,
        config=config,
        rt_frame=rt_frame,
        stage="targeted" if known_label is not None else "final",
        known_identity_bindings=(
            tuple((owner.owner_id, known_label) for owner in owners)
            if known_label is not None
            else ()
        ),
    )
    return group, compose_cid_nl_predecessor_cells(
        groups=(group,),
        sample_assessments=sample_assessments,
        alignment_config=config,
        rt_frame=rt_frame,
    )


def _owner_handoff(tmp_path: Path):
    fixture, assessments = _truth_table_fixture(tmp_path)
    group, cells = _group_and_predecessor_cells(
        tuple(zip(fixture.samples, assessments, strict=True)),
        known_label=fixture.target.label,
    )
    return fixture, cells, _owner_handoff_for_cells(fixture, group, cells)


def _owner_handoff_for_cells(
    fixture: Any, group: Any, cells: tuple[Any, ...], *, backfill_enabled: bool = True
):
    coverage = _coverage_for_groups(
        fixture,
        (group,),
        fixture.samples,
        predecessor_cells=cells,
        backfill_enabled=backfill_enabled,
    )
    receipt = _final_receipt(
        SimpleNamespace(context=SimpleNamespace(samples=fixture.samples)),
        predecessors=(group,),
        successors=(group,),
    )
    return build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )


def _support_assessment(
    fixture: Any,
    sample_stem: str,
    *,
    suffix: str,
    apex_rt: float,
    scan_number: int,
    seed_count: int = 1,
):
    peak = cid_nl_peak(
        fixture,
        sample_stem,
        apex_rt=apex_rt,
        rt_left_min=apex_rt - 0.1,
        rt_right_min=apex_rt + 0.1,
    )
    evidence_refs = tuple(
        cid_nl_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number + index,
            scan_rt_min=apex_rt,
        )
        for index in range(seed_count)
    )
    support_refs = tuple(
        cid_nl_unique_support(
            fixture,
            sample_stem,
            peak=peak,
            evidence=evidence,
        )
        for evidence in evidence_refs
    )
    return mint_cid_nl_method_assessment(
        sample=fixture.sample(sample_stem),
        acquisition_source=fixture.source(fixture.sample(sample_stem)),
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}:{suffix}",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(peak,),
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )


def _coverage_for_groups(
    fixture: Any,
    groups: tuple[Any, ...],
    samples: tuple[Any, ...],
    *,
    predecessor_cells: tuple[Any, ...],
    backfill_enabled: bool = True,
):
    cells = {
        (
            cell.group_context.group.group_hypothesis_id,
            cell.sample_key,
        ): cell
        for cell in predecessor_cells
    }
    positions = tuple(
        BackfillPlanningPosition(
            group=group,
            sample=sample,
            planning_reason=_cell_planning_reason(
                cell, backfill_enabled=backfill_enabled
            ),
            cell=cell,
        )
        for group in groups
        for sample in samples
        for cell in (cells.get((group.group_hypothesis_id, sample.sample_name)),)
    )
    return BackfillEvaluationCoverageBatch(
        groups=groups, samples=samples, positions=positions
    )


def _receipt_fixture(fixture: Any, samples: tuple[Any, ...]):
    acquisitions = {
        sample.raw_stem: fixture.source(sample)
        for sample in fixture.samples
        if sample in samples
    }
    return SimpleNamespace(
        context=SimpleNamespace(
            samples=samples,
        ),
        acquisitions=acquisitions,
    )
