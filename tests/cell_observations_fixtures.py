"""Helpers shared by test_cell_observations and other test modules."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.cid_nl_evidence_spine_fixtures import (
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
    BackfillPlanningReason,
)
from xic_extractor.alignment.cid_nl_completion import _cell_planning_reason
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment_ref,
)


def _owner_handoff(tmp_path: Path):
    fixture, assessments = _truth_table_fixture(tmp_path)
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(zip(fixture.samples, assessments, strict=True)),
    )
    assert spine.group is not None
    group = spine.group.membership
    return fixture, spine, _owner_handoff_for_cells(fixture, group, spine.cells)


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
        SimpleNamespace(context=SimpleNamespace(processing_owners=fixture.owners)),
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
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}:{suffix}",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(peak,),
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )


def _not_planned_coverage(
    owners: Any,
    groups: tuple[Any, ...],
    samples: tuple[Any, ...],
    *,
    planning_reason: Callable[[Any, Any], BackfillPlanningReason],
):
    """Coverage shaped like CID-NL completion: no planned requests or terminals."""
    positions = tuple(
        BackfillPlanningPosition(
            group_identity=group,
            sample=sample,
            planning_reason=planning_reason(group, sample),
        )
        for group in groups
        for sample in samples
    )
    return BackfillEvaluationCoverageBatch(
        processing_owners=owners,
        group_identities=groups,
        samples=samples,
        positions=positions,
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
            cell.group_context.group_identity.group_hypothesis_id,
            cell.content.sample_key,
        ): cell
        for cell in predecessor_cells
    }
    return _not_planned_coverage(
        fixture.owners,
        groups,
        samples,
        planning_reason=lambda group, sample: _cell_planning_reason(
            cells.get((group.group_hypothesis_id, sample.identity.sample_key)),
            backfill_enabled=backfill_enabled,
        ),
    )


def _receipt_fixture(fixture: Any, samples: tuple[Any, ...]):
    acquisitions = {
        sample.lookup_token: source
        for sample, source in zip(
            fixture.samples,
            fixture.request.acquisition_sources,
            strict=True,
        )
        if sample in samples
    }
    return SimpleNamespace(
        context=SimpleNamespace(
            samples=samples,
            processing_owners=fixture.owners,
        ),
        acquisitions=acquisitions,
    )
