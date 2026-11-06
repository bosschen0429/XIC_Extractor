from __future__ import annotations

from pathlib import Path

import pytest

from tests.processing_identity_no_raw_fixture import (
    ProcessingIdentityNoRawFixture,
)
from tests.target_pre_cell_projection_fixtures import (
    _finalized_case,
)
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSamplePartitionTransition,
)
from xic_extractor.alignment.projection.target.pre_cell import (
    project_target_pre_cell,
)
from xic_extractor.alignment.result import (
    ObservedSearchSubject,
    SearchedNoObservationSubject,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame


@pytest.mark.parametrize(
    ("observed", "group_specs", "variant"),
    [
        ((), (((), "provisional"),), "unbound"),
        (
            ("sample-a", "sample-b"),
            ((("sample-a", "sample-b"), "established"),),
            "bound",
        ),
        (
            ("sample-a", "sample-b", "sample-c", "sample-d"),
            (
                (("sample-a", "sample-b"), "established"),
                (("sample-c", "sample-d"), "established"),
            ),
            "bound",
        ),
    ],
)
def test_projects_closed_row_binding_and_complete_cell_population(
    tmp_path: Path,
    observed: tuple[str, ...],
    group_specs: tuple[tuple[tuple[str, ...], str], ...],
    variant: str,
) -> None:
    stems = tuple(
        sorted(
            set(observed).union(stem for members, _ in group_specs for stem in members)
            or {"sample-a", "sample-b"}
        )
    )
    _, scope, result, _ = _finalized_case(
        tmp_path,
        sample_stems=stems,
        observed_stems=observed,
        group_specs=group_specs,
    )

    projection = project_target_pre_cell(result)

    assert projection.binding.variant == variant
    assert tuple(
        group.group_hypothesis_id for group in projection.binding.eligible_groups
    ) == tuple(
        sorted(
            group.group_hypothesis_id for group in projection.binding.eligible_groups
        )
    )
    assert len(projection.subjects) == len(scope.samples)
    assert tuple(subject.sample for subject in projection.subjects) == tuple(
        sorted(scope.samples, key=lambda sample: sample.identity.sample_key)
    )
    assert all(subject.target is scope.target for subject in projection.subjects)
    assert all(
        subject.processing_request is result.processing_request
        for subject in projection.subjects
    )
    assert all(
        subject.row_binding is projection.binding for subject in projection.subjects
    )
    assert all(
        subject.terminal_subject.sample_key == subject.sample.identity.sample_key
        for subject in projection.subjects
    )


def test_eligibility_uses_full_owner_member_equality_and_excludes_statuses(
    tmp_path: Path,
) -> None:
    # Unresolved and rejected groups are excluded from eligibility.
    _, _, result_unresolved, _ = _finalized_case(
        tmp_path,
        sample_stems=(
            "sample-a",
            "sample-b",
            "sample-c",
            "sample-d",
            "sample-e",
        ),
        observed_stems=(
            "sample-c",
            "sample-d",
            "sample-e",
        ),
        group_specs=(
            (("sample-a", "sample-b"), "established"),
            (("sample-c", "sample-d"), "unresolved"),
            (("sample-e",), "rejected"),
        ),
    )
    projection_unbound = project_target_pre_cell(result_unresolved)
    assert projection_unbound.binding.variant == "unbound"
    assert projection_unbound.binding.eligible_groups == ()

    # Matching established groups are retained as eligible.
    _, _, result_bound, _ = _finalized_case(
        tmp_path,
        sample_stems=(
            "sample-a",
            "sample-b",
            "sample-c",
            "sample-d",
            "sample-e",
        ),
        observed_stems=(
            "sample-a",
            "sample-b",
        ),
        group_specs=(
            (("sample-a", "sample-b"), "established"),
            (("sample-c", "sample-d"), "unresolved"),
            (("sample-e",), "rejected"),
        ),
    )
    projection_bound = project_target_pre_cell(result_bound)
    assert projection_bound.binding.variant == "bound"
    assert len(projection_bound.binding.eligible_groups) == 1
    assert projection_bound.binding.eligible_groups[0].disposition == "established"


def test_reversed_sample_order_and_no_observation_are_preserved(tmp_path: Path) -> None:
    _, scope, result, _ = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=((("sample-a", "sample-b"), "provisional"),),
        reverse_scope_samples=True,
    )

    projection = project_target_pre_cell(result)

    assert projection.binding.variant == "bound"
    assert tuple(
        subject.sample.identity.sample_key for subject in projection.subjects
    ) == tuple(sample.identity.sample_key for sample in result.canonical_samples)
    subjects_by_sample = {
        subject.sample.lookup_token: subject for subject in projection.subjects
    }
    assert isinstance(
        subjects_by_sample["sample-a"].terminal_subject,
        ObservedSearchSubject,
    )
    assert isinstance(
        subjects_by_sample["sample-b"].terminal_subject,
        SearchedNoObservationSubject,
    )
    assert scope.samples[0].lookup_token == "sample-b"


def _final_receipt(
    fixture: ProcessingIdentityNoRawFixture,
    groups: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSampleGroupingResult:
    population = tuple(sorted(groups, key=lambda group: group.group_hypothesis_id))
    return CrossSampleGroupingResult(
        processing_owners=fixture.owners,
        partition_transition=CrossSamplePartitionTransition(
            status="assessable",
            predecessors=population,
            successors=population,
            lineage=(),
            reason="unchanged_membership_no_lineage",
        ),
        rt_frame=SampleRtFrame(),
    )
