from pathlib import Path

import pytest

from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
    processing_identity_candidate_set,
    processing_identity_hypothesis,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.targeted_hypothesis_alignment import (
    TargetedHypothesisAlignmentError,
    TargetedHypothesisAlignmentInput,
    align_selected_targeted_hypotheses,
)
from xic_extractor.peak_detection.selection_decision import (
    mint_diagnostic_peak_hypothesis_selection_ref,
)


def test_multi_sample_alignment_preserves_selected_hypothesis_identity(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b", "sample-c"),
    )
    first = processing_identity_hypothesis(
        fixture, "sample-a", apex_rt=8.00, scan_number=42
    )
    second = processing_identity_hypothesis(
        fixture, "sample-b", apex_rt=8.02, scan_number=43
    )
    inputs = (
        _input(fixture, "sample-a", (first,)),
        _input(fixture, "sample-b", (second,)),
        _input(fixture, "sample-c", ()),
    )

    groups = align_selected_targeted_hypotheses(
        inputs,
        target=fixture.target,
        config=AlignmentConfig(),
    )
    reversed_groups = align_selected_targeted_hypotheses(
        tuple(reversed(inputs)),
        target=fixture.target,
        config=AlignmentConfig(),
    )

    assert groups == reversed_groups
    assert len(groups) == 1
    assert groups[0].owner_ids == (first.hypothesis_id, second.hypothesis_id)
    assert tuple(owner.owner_id for owner in groups[0].owners) == groups[0].owner_ids
    assert tuple(owner.sample_stem for owner in groups[0].owners) == (
        "sample-a",
        "sample-b",
    )


def test_ambiguity_missing_and_failure_cannot_enter_alignment(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("selected", "ambiguous", "missing", "failed"),
    )
    selected = processing_identity_hypothesis(
        fixture, "selected", apex_rt=8.0, scan_number=40
    )
    ambiguous_a = processing_identity_hypothesis(
        fixture, "ambiguous", apex_rt=8.0, scan_number=41
    )
    ambiguous_b = processing_identity_hypothesis(
        fixture, "ambiguous", apex_rt=8.3, scan_number=42
    )
    inputs = (
        _input(fixture, "selected", (selected,)),
        _input(fixture, "ambiguous", (ambiguous_a, ambiguous_b)),
        _input(fixture, "missing", ()),
        _input(fixture, "failed", (), failure_reason="discovery_worker_failed"),
    )

    groups = align_selected_targeted_hypotheses(
        inputs,
        target=fixture.target,
        config=AlignmentConfig(),
    )

    assert len(groups) == 1
    assert groups[0].owner_ids == (selected.hypothesis_id,)


def test_duplicate_sample_cell_and_foreign_candidate_universe_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
    )
    first = processing_identity_hypothesis(
        fixture, "sample-a", apex_rt=8.0, scan_number=42
    )
    second = processing_identity_hypothesis(
        fixture, "sample-b", apex_rt=8.0, scan_number=43
    )
    first_input = _input(fixture, "sample-a", (first,))

    with pytest.raises(TargetedHypothesisAlignmentError, match="duplicate sample"):
        align_selected_targeted_hypotheses(
            (first_input, first_input),
            target=fixture.target,
            config=AlignmentConfig(),
        )
    with pytest.raises(
        TargetedHypothesisAlignmentError,
        match="different canonical sample",
    ):
        TargetedHypothesisAlignmentInput(
            sample=fixture.sample("sample-a"),
            target=fixture.target,
            selection=first_input.selection,
            candidate_set=processing_identity_candidate_set(
                fixture,
                "sample-b",
                (second,),
            ),
        )


def _input(
    fixture,
    sample_stem: str,
    hypotheses,
    *,
    failure_reason: str | None = None,
) -> TargetedHypothesisAlignmentInput:
    candidate_set = processing_identity_candidate_set(
        fixture,
        sample_stem,
        hypotheses,
        failure_reason=failure_reason,
    )
    return TargetedHypothesisAlignmentInput(
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        candidate_set=candidate_set,
        selection=mint_diagnostic_peak_hypothesis_selection_ref(
            candidate_set=candidate_set,
        ),
    )
