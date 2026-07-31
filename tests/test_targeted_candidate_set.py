from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.package2_no_raw_fixture import (
    build_package2_no_raw_fixture,
    package2_candidate_member,
    package2_candidate_set,
    package2_hypothesis,
)
from xic_extractor.discovery.targeted_candidate_set import (
    TargetedCandidateSetError,
    canonical_targeted_candidate_set_bytes,
    complete_targeted_candidate_discovery,
    mint_targeted_candidate_set_ref,
)


def test_complete_candidate_set_is_canonical_and_closes_all_lineage(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a",),
    )
    first = package2_hypothesis(fixture, "sample-a", apex_rt=8.0, scan_number=42)
    second = package2_hypothesis(fixture, "sample-a", apex_rt=8.3, scan_number=52)

    forward = package2_candidate_set(fixture, "sample-a", (first, second))
    reverse = package2_candidate_set(fixture, "sample-a", (second, first))

    assert forward == reverse
    assert forward.status == "complete"
    assert forward.failure_reason == ""
    assert forward.candidate_hypothesis_ids == tuple(
        sorted((first.hypothesis_id, second.hypothesis_id))
    )
    candidate_content_ids = []
    for candidate in forward.candidate_hypotheses:
        assert candidate.artifact is not None
        candidate_content_ids.append(
            candidate.artifact.content_identity.artifact_content_id
        )
    assert forward.candidate_hypothesis_content_ids == tuple(candidate_content_ids)
    assert forward.artifact in forward.lineage_artifacts
    assert forward.artifact.upstream_artifact_ids == tuple(
        artifact.evidence_artifact_id
        for artifact in forward.lineage_artifacts
        if artifact != forward.artifact
    )
    assert canonical_targeted_candidate_set_bytes(forward).endswith(b"\n")


def test_successful_empty_and_failed_candidate_sets_are_distinct(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("sample-a",))

    complete = package2_candidate_set(fixture, "sample-a", ())
    failed = package2_candidate_set(
        fixture,
        "sample-a",
        (),
        failure_reason="discovery_worker_failed",
    )

    assert complete.status == "complete"
    assert complete.candidate_hypotheses == ()
    assert failed.status == "failed"
    assert failed.candidate_hypotheses == ()
    assert failed.failure_reason == "discovery_worker_failed"
    assert complete.artifact.content_identity != failed.artifact.content_identity


def test_duplicate_projection_swap_and_missing_ancestor_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a",),
    )
    first = package2_hypothesis(fixture, "sample-a", apex_rt=8.0, scan_number=42)
    second = package2_hypothesis(fixture, "sample-a", apex_rt=8.3, scan_number=52)
    first_member = package2_candidate_member(fixture, first)
    second_member = package2_candidate_member(fixture, second)

    with pytest.raises(TargetedCandidateSetError, match="duplicate"):
        complete_targeted_candidate_discovery(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample-a"),
            target=fixture.target,
            hypotheses=(first, first),
            targeted_cid_nl_projections=(
                first_member.targeted_cid_nl_projection,
                first_member.targeted_cid_nl_projection,
            ),
            lineage_artifacts=fixture.lineage_for((first_member,)),
        )
    swapped_member = replace(
        first_member,
        targeted_cid_nl_projection=second_member.targeted_cid_nl_projection,
    )
    with pytest.raises(TargetedCandidateSetError, match="projection.*hypothesis"):
        complete_targeted_candidate_discovery(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample-a"),
            target=fixture.target,
            hypotheses=(first,),
            targeted_cid_nl_projections=(
                swapped_member.targeted_cid_nl_projection,
            ),
            lineage_artifacts=fixture.lineage_for((swapped_member,)),
        )
    assert first.artifact is not None
    with pytest.raises(TargetedCandidateSetError, match="missing upstream"):
        complete_targeted_candidate_discovery(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample-a"),
            target=fixture.target,
            hypotheses=(first,),
            targeted_cid_nl_projections=(
                first_member.targeted_cid_nl_projection,
            ),
            lineage_artifacts=(first.artifact,),
        )


def test_mixed_sample_target_attempt_and_tamper_fail_closed(tmp_path: Path) -> None:
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
    )
    first = package2_hypothesis(fixture, "sample-a", apex_rt=8.0, scan_number=42)
    foreign_sample = package2_hypothesis(
        fixture,
        "sample-b",
        apex_rt=8.0,
        scan_number=52,
    )
    first_member = package2_candidate_member(fixture, first)
    foreign_member = package2_candidate_member(fixture, foreign_sample)

    with pytest.raises(TargetedCandidateSetError, match="different canonical sample"):
        complete_targeted_candidate_discovery(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample-a"),
            target=fixture.target,
            hypotheses=(foreign_sample,),
            targeted_cid_nl_projections=(
                foreign_member.targeted_cid_nl_projection,
            ),
            lineage_artifacts=fixture.lineage_for((foreign_member,)),
        )

    wrong_target = fixture.other_target
    with pytest.raises(TargetedCandidateSetError, match="different target"):
        complete_targeted_candidate_discovery(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample-a"),
            target=wrong_target,
            hypotheses=(first,),
            targeted_cid_nl_projections=(
                first_member.targeted_cid_nl_projection,
            ),
            lineage_artifacts=fixture.lineage_for((first_member,)),
        )

    retry_fixture = fixture.retry()
    retry_hypothesis = package2_hypothesis(
        retry_fixture,
        "sample-a",
        apex_rt=8.0,
        scan_number=42,
    )
    retry_member = package2_candidate_member(retry_fixture, retry_hypothesis)
    with pytest.raises(TargetedCandidateSetError, match="different processing attempt"):
        complete_targeted_candidate_discovery(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample-a"),
            target=fixture.target,
            hypotheses=(retry_hypothesis,),
            targeted_cid_nl_projections=(
                retry_member.targeted_cid_nl_projection,
            ),
            lineage_artifacts=retry_fixture.lineage_for((retry_member,)),
        )

    receipt = package2_candidate_set(fixture, "sample-a", (first,))
    with pytest.raises(TargetedCandidateSetError, match="failure_reason"):
        replace(receipt, failure_reason="caller_tamper")


def test_two_discovered_candidates_cannot_be_recast_as_one_complete_candidate(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("sample-a",))
    first = package2_hypothesis(fixture, "sample-a", apex_rt=8.0, scan_number=42)
    second = package2_hypothesis(fixture, "sample-a", apex_rt=8.3, scan_number=52)
    members = tuple(
        package2_candidate_member(fixture, hypothesis)
        for hypothesis in (first, second)
    )
    completion = complete_targeted_candidate_discovery(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        target=fixture.target,
        hypotheses=(first, second),
        targeted_cid_nl_projections=tuple(
            member.targeted_cid_nl_projection for member in members
        ),
        lineage_artifacts=fixture.lineage_for(members),
    )

    with pytest.raises(
        TargetedCandidateSetError,
        match="outside the exact candidate closure",
    ):
        replace(completion, candidates=(completion.candidates[0],))
    with pytest.raises(TypeError, match="unexpected keyword argument 'candidates'"):
        mint_targeted_candidate_set_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample-a"),
            target=fixture.target,
            completion=completion,
            candidates=(completion.candidates[0],),  # type: ignore[call-arg]
        )

    receipt = mint_targeted_candidate_set_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample-a"),
        target=fixture.target,
        completion=completion,
    )
    assert receipt.candidate_hypothesis_ids == tuple(
        sorted((first.hypothesis_id, second.hypothesis_id))
    )
