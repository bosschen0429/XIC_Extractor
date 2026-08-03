from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path

import pytest

from tests.issue161_no_raw_fixture import (
    issue161_evidence,
    issue161_peak,
    issue161_support_policy,
    issue161_unique_support,
)
from tests.package2_no_raw_fixture import (
    Package2NoRawFixture,
    build_package2_no_raw_fixture,
)
from xic_extractor.alignment.cell_observations import (
    CellObservationContractError,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    CidNlReferenceSpine,
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.conservative_backfill import (
    ConservativeBackfillCellDecision,
    ConservativeBackfillContractError,
    ConservativeBackfillRecoveryEvidence,
    assess_conservative_backfill_group,
    assess_targeted_conservative_backfill_group,
    assess_untargeted_conservative_backfill_group,
    canonical_conservative_backfill_recovery_universe_bytes,
    map_conservative_backfill_candidates,
    minimum_direct_support_count,
    mint_conservative_backfill_recovery_candidate_universe,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentRef,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.sample_metadata import SampleRole


def test_support_threshold_formula_covers_small_and_large_runs() -> None:
    assert {
        n: minimum_direct_support_count(n)
        for n in (0, 19, 20, 39, 40, 41, 59, 60, 85, 100)
    } == {
        0: 2,
        19: 2,
        20: 2,
        39: 2,
        40: 2,
        41: 2,
        59: 2,
        60: 3,
        85: 4,
        100: 5,
    }


def test_roles_votes_and_full_support_trace_set_are_owner_derived(
    tmp_path: Path,
) -> None:
    roles: dict[str, SampleRole] = {
        "study": "study_sample",
        "qc": "qc",
        "pooled": "pooled_qc",
        "blank": "blank",
        "MixSTD": "calibrator",
    }
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=tuple(roles),
        sample_roles=roles,
    )
    spine = _spine(
        fixture,
        support_apex_by_sample={
            "study": 8.0,
            "qc": 8.0,
            "blank": 8.0,
            "MixSTD": 8.0,
        },
        invocation="roles",
    )

    targeted = assess_targeted_conservative_backfill_group(
        spine.targeted_projections,
        sample_population=fixture.samples,
    )
    untargeted = assess_untargeted_conservative_backfill_group(
        spine.untargeted_projections,
        sample_population=fixture.samples,
    )

    assert targeted == untargeted
    assert all(
        left is right
        for left, right in zip(
            targeted.source_cells,
            spine.cells,
            strict=True,
        )
    )
    assert targeted.sample_count_n == 3
    assert targeted.minimum_support_k == 2
    assert targeted.direct_support_count_s == 2
    assert targeted.may_attempt_recovery is True
    assert len(targeted.direct_support_sample_ids) == 4
    assert len(targeted.support_trace_refs) == 4
    assert all(
        trace is support.hypothesis.peak_observation_refs[0]
        for trace, support in zip(
            targeted.support_trace_refs,
            targeted.group_context.supports,
            strict=True,
        )
    )
    assert not any(
        "median" in field.name.lower()
        for field in fields(type(targeted))
    )
    assert targeted.product_authority == "none"
    assert targeted.validation_status == "shadow_ready"


def test_group_exists_below_threshold_but_cannot_trigger_recovery(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "s3"))
    spine = _spine(
        fixture,
        support_apex_by_sample={"s1": 8.0},
        invocation="below-threshold",
    )
    assert spine.group is not None
    facts = assess_conservative_backfill_group(
        spine.cells,
        sample_population=fixture.samples,
    )

    universe = _recovery_universe(
        fixture,
        (facts,),
        "s2",
        (_peak(fixture, "s2", apex_rt=8.0),),
    )
    decision = map_conservative_backfill_candidates(
        (facts,),
        sample=fixture.sample("s2"),
        candidate_universes=(universe,),
    )[0]

    assert facts.group_frozen is True
    assert facts.direct_support_count_s == 1
    assert facts.minimum_support_k == 2
    assert facts.may_attempt_recovery is False
    assert decision.disposition == "hold"
    assert decision.matched_candidate is None
    assert decision.reason_codes == ("direct_support_threshold_not_met",)
    assert _forbidden_product_fields(ConservativeBackfillCellDecision) == set()

    forged_candidate = ConservativeBackfillRecoveryEvidence(
        candidate_universe=universe,
        peak_observation=universe.candidate_peaks[0],
    )
    with pytest.raises(
        ConservativeBackfillContractError,
        match="minted by the conservative Backfill mapper",
    ):
        ConservativeBackfillCellDecision(
            group_facts=facts,
            target_cell=decision.target_cell,
            candidate_universe=universe,
            candidate_evidence=(forged_candidate,),
            matched_candidate=forged_candidate,
            disposition="candidate_mapped",
            reason_codes=("forged_mapping",),
            _mint_token=object(),
        )
    with pytest.raises(ValueError, match="InitVar '_mint_token'"):
        replace(
            decision,
            matched_candidate=forged_candidate,
            disposition="candidate_mapped",
            reason_codes=("forged_mapping",),
        )


def test_incomplete_group_snapshot_is_held_even_when_support_threshold_passes(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "s3", "s4"))
    spine = _spine(
        fixture,
        support_apex_by_sample={"s1": 8.0, "s2": 8.0},
        invocation="not-frozen",
    )
    retained_cells = tuple(
        cell
        for cell in spine.cells
        if cell.content.canonical_sample_identity_id
        != fixture.sample("s4").identity.sample_identity_id
    )
    facts = assess_conservative_backfill_group(
        retained_cells,
        sample_population=fixture.samples,
    )
    universe = _recovery_universe(
        fixture,
        (facts,),
        "s3",
        (_peak(fixture, "s3", apex_rt=8.0),),
    )

    decision = map_conservative_backfill_candidates(
        (facts,),
        sample=fixture.sample("s3"),
        candidate_universes=(universe,),
    )[0]

    assert facts.direct_support_count_s == 2
    assert facts.group_frozen is False
    assert facts.may_attempt_recovery is False
    assert decision.disposition == "hold"
    assert decision.reason_codes == ("group_not_frozen",)


def test_terminal_method_results_precede_group_recovery_eligibility(
    tmp_path: Path,
) -> None:
    below_threshold_dir = tmp_path / "below_threshold"
    below_threshold_dir.mkdir()
    below_threshold_fixture = _study_fixture(
        below_threshold_dir,
        ("support", "reject", "missing"),
    )
    below_threshold_spine = _spine(
        below_threshold_fixture,
        support_apex_by_sample={"support": 8.0},
        reject_sample_stems=frozenset({"reject"}),
        invocation="terminal-below-threshold",
    )
    below_threshold = assess_conservative_backfill_group(
        below_threshold_spine.cells,
        sample_population=below_threshold_fixture.samples,
    )

    support_decision = map_conservative_backfill_candidates(
        (below_threshold,),
        sample=below_threshold_fixture.sample("support"),
        candidate_universes=(),
    )[0]
    reject_decision = map_conservative_backfill_candidates(
        (below_threshold,),
        sample=below_threshold_fixture.sample("reject"),
        candidate_universes=(),
    )[0]

    assert below_threshold.group_frozen is True
    assert below_threshold.may_attempt_recovery is False
    assert support_decision.disposition == "not_applicable"
    assert support_decision.reason_codes == ("cell_already_has_direct_support",)
    assert reject_decision.disposition == "not_applicable"
    assert reject_decision.reason_codes == (
        "method_reject_is_not_backfill_eligible",
    )

    incomplete_dir = tmp_path / "incomplete"
    incomplete_dir.mkdir()
    incomplete_fixture = _study_fixture(
        incomplete_dir,
        ("s1", "s2", "reject", "omitted"),
    )
    incomplete_spine = _spine(
        incomplete_fixture,
        support_apex_by_sample={"s1": 8.0, "s2": 8.0},
        reject_sample_stems=frozenset({"reject"}),
        invocation="terminal-not-frozen",
    )
    retained_cells = tuple(
        cell
        for cell in incomplete_spine.cells
        if cell.content.canonical_sample_identity_id
        != incomplete_fixture.sample("omitted").identity.sample_identity_id
    )
    incomplete = assess_conservative_backfill_group(
        retained_cells,
        sample_population=incomplete_fixture.samples,
    )
    incomplete_reject = map_conservative_backfill_candidates(
        (incomplete,),
        sample=incomplete_fixture.sample("reject"),
        candidate_universes=(),
    )[0]

    assert incomplete.group_frozen is False
    assert incomplete_reject.disposition == "not_applicable"
    assert incomplete_reject.reason_codes == (
        "method_reject_is_not_backfill_eligible",
    )


def test_ordered_one_to_one_mapping_retains_evidence_without_backfill_votes(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("a1", "a2", "b1", "b2", "missing"))
    early = _spine(
        fixture,
        support_apex_by_sample={"a1": 7.8, "a2": 7.8},
        invocation="early",
    )
    late = _spine(
        fixture,
        support_apex_by_sample={"b1": 8.2, "b2": 8.2},
        invocation="late",
    )
    early_facts = assess_conservative_backfill_group(
        early.cells,
        sample_population=fixture.samples,
    )
    late_facts = assess_conservative_backfill_group(
        late.cells,
        sample_population=fixture.samples,
    )
    early_peak = _peak(fixture, "missing", apex_rt=7.8)
    late_peak = _peak(fixture, "missing", apex_rt=8.2)
    candidate_universe = _recovery_universe(
        fixture,
        (late_facts, early_facts),
        "missing",
        (early_peak, late_peak),
    )

    decisions = map_conservative_backfill_candidates(
        (late_facts, early_facts),
        sample=fixture.sample("missing"),
        candidate_universes=(candidate_universe,),
    )
    by_center = {
        decision.group_facts.group_context.group.group_center_rt: decision
        for decision in decisions
    }

    assert by_center[7.8].matched_candidate is not None
    assert by_center[8.2].matched_candidate is not None
    assert by_center[7.8].matched_candidate.peak_observation is early_peak
    assert by_center[8.2].matched_candidate.peak_observation is late_peak
    assert {decision.disposition for decision in decisions} == {"candidate_mapped"}
    assert all(
        decision.reason_codes == ("ordered_one_to_one_recovery_evidence",)
        for decision in decisions
    )
    assert early_facts.direct_support_count_s == 2
    assert late_facts.direct_support_count_s == 2
    assert all(
        decision.matched_candidate.peak_observation
        not in decision.group_facts.support_trace_refs
        for decision in decisions
        if decision.matched_candidate is not None
    )


def test_one_to_many_and_many_to_one_cardinality_remain_held(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("a1", "a2", "b1", "b2", "missing"))
    early = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"a1": 7.8, "a2": 7.8},
            invocation="cardinality-early",
        ).cells,
        sample_population=fixture.samples,
    )
    late = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"b1": 8.2, "b2": 8.2},
            invocation="cardinality-late",
        ).cells,
        sample_population=fixture.samples,
    )
    first_peak = _peak(fixture, "missing", apex_rt=7.8)
    second_peak = _peak(fixture, "missing", apex_rt=8.2)
    one_to_many_universe = _recovery_universe(
        fixture,
        (early,),
        "missing",
        (first_peak, second_peak),
    )
    many_to_one_universe = _recovery_universe(
        fixture,
        (early, late),
        "missing",
        (first_peak,),
    )

    one_to_many = map_conservative_backfill_candidates(
        (early,),
        sample=fixture.sample("missing"),
        candidate_universes=(one_to_many_universe,),
    )
    many_to_one = map_conservative_backfill_candidates(
        (early, late),
        sample=fixture.sample("missing"),
        candidate_universes=(many_to_one_universe,),
    )

    assert [item.disposition for item in one_to_many] == ["hold"]
    assert all(item.disposition == "hold" for item in many_to_one)
    assert all(item.matched_candidate is None for item in one_to_many + many_to_one)
    assert one_to_many[0].candidate_universe is one_to_many_universe
    assert tuple(
        item.peak_observation for item in one_to_many[0].candidate_evidence
    ) == one_to_many_universe.candidate_peaks
    assert all(
        item.reason_codes == ("uncertain_candidate_cardinality",)
        for item in one_to_many + many_to_one
    )


def test_mapping_retains_empty_receipt_and_rejects_duplicate_target_receipts(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "missing"))
    facts = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"s1": 8.0, "s2": 8.0},
            invocation="empty-recovery-universe",
        ).cells,
        sample_population=fixture.samples,
    )
    empty_universe = _recovery_universe(
        fixture,
        (facts,),
        "missing",
        (),
    )

    decision = map_conservative_backfill_candidates(
        (facts,),
        sample=fixture.sample("missing"),
        candidate_universes=(empty_universe,),
    )[0]
    missing_receipt = map_conservative_backfill_candidates(
        (facts,),
        sample=fixture.sample("missing"),
        candidate_universes=(),
    )[0]

    assert decision.candidate_universe is empty_universe
    assert decision.candidate_evidence == ()
    assert decision.disposition == "hold"
    assert decision.reason_codes == ("recovery_candidate_universe_empty",)
    assert missing_receipt.candidate_universe is None
    assert missing_receipt.reason_codes == ("recovery_evidence_missing",)
    with pytest.raises(
        ConservativeBackfillContractError,
        match="at most one recovery universe receipt",
    ):
        map_conservative_backfill_candidates(
            (facts,),
            sample=fixture.sample("missing"),
            candidate_universes=(empty_universe, empty_universe),
        )


def test_recovery_evidence_cannot_create_first_group_or_become_support(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "missing"))
    facts = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"s1": 8.0, "s2": 8.0},
            invocation="no-first-group",
        ).cells,
        sample_population=fixture.samples,
    )
    universe = _recovery_universe(
        fixture,
        (facts,),
        "missing",
        (_peak(fixture, "missing", apex_rt=8.0),),
    )
    candidate = ConservativeBackfillRecoveryEvidence(
        candidate_universe=universe,
        peak_observation=universe.candidate_peaks[0],
    )

    with pytest.raises(
        ConservativeBackfillContractError,
        match="cannot claim a direct-support source",
    ):
        replace(candidate, evidence_source="direct_support")

    assert facts.direct_support_count_s == 2
    assert candidate.peak_observation not in facts.support_trace_refs


def test_recovery_receipt_prevents_independent_near_isobaric_target_pairing(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "missing"))
    facts = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"s1": 8.0, "s2": 8.0},
            invocation="target-owner",
        ).cells,
        sample_population=fixture.samples,
    )
    other_facts = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"s1": 8.0, "s2": 8.0},
            invocation="near-isobaric-owner",
            target=fixture.other_target,
        ).cells,
        sample_population=fixture.samples,
    )
    universe = _recovery_universe(
        fixture,
        (facts,),
        "missing",
        (_peak(fixture, "missing", apex_rt=8.0),),
    )

    assert (
        fixture.target.definition.target.mz
        == fixture.other_target.definition.target.mz
    )
    assert universe.target_key == (
        facts.group_context.target_key_id,
        facts.group_context.target_definition_id,
    )
    with pytest.raises(
        ConservativeBackfillContractError,
        match="exact sample and target",
    ):
        mint_conservative_backfill_recovery_candidate_universe(
            processing_owners=fixture.owners,
            sample=fixture.sample("missing"),
            group_facts=(other_facts,),
            candidate_assessment=universe.candidate_assessment,
        )


def test_recovery_receipt_rejects_truncated_processing_request_population(
    tmp_path: Path,
) -> None:
    sample_stems = tuple(f"s{index:02d}" for index in range(1, 61))
    fixture = _study_fixture(tmp_path, sample_stems)
    spine = _spine(
        fixture,
        support_apex_by_sample={"s01": 8.0, "s02": 8.0},
        invocation="truncated-request-population",
    )
    truncated_population = fixture.samples[:3]
    truncated_sample_ids = {
        sample.identity.sample_identity_id for sample in truncated_population
    }
    truncated_cells = tuple(
        cell
        for cell in spine.cells
        if cell.content.canonical_sample_identity_id in truncated_sample_ids
    )
    facts = assess_conservative_backfill_group(
        truncated_cells,
        sample_population=truncated_population,
    )

    assert facts.group_frozen is True
    assert facts.sample_count_n == 3
    assert facts.minimum_support_k == 2
    assert facts.may_attempt_recovery is True
    with pytest.raises(
        ConservativeBackfillContractError,
        match="full processing request",
    ):
        _recovery_universe(
            fixture,
            (facts,),
            "s03",
            (_peak(fixture, "s03", apex_rt=8.0),),
        )

    full_facts = assess_conservative_backfill_group(
        spine.cells,
        sample_population=fixture.samples,
    )
    valid_universe = _recovery_universe(
        fixture,
        (full_facts,),
        "s03",
        (_peak(fixture, "s03", apex_rt=8.0),),
    )
    with pytest.raises(
        ConservativeBackfillContractError,
        match="full processing request",
    ):
        replace(valid_universe, group_facts=(facts,))

    payload = json.loads(
        canonical_conservative_backfill_recovery_universe_bytes(
            processing_owners=fixture.owners,
            sample=fixture.sample("s03"),
            group_facts=(full_facts,),
            candidate_assessment=valid_universe.candidate_assessment,
        )
    )
    assert payload["request_sample_population_ids"] == sorted(
        sample.identity.sample_identity_id for sample in fixture.samples
    )


def test_rt_mismatch_remains_nonnumeric_without_drift_or_split_logic(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "missing"))
    facts = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"s1": 8.0, "s2": 8.0},
            invocation="rt-mismatch",
        ).cells,
        sample_population=fixture.samples,
    )
    shifted_universe = _recovery_universe(
        fixture,
        (facts,),
        "missing",
        (_peak(fixture, "missing", apex_rt=8.5),),
    )

    decision = map_conservative_backfill_candidates(
        (facts,),
        sample=fixture.sample("missing"),
        candidate_universes=(shifted_universe,),
    )[0]

    assert decision.disposition == "hold"
    assert decision.matched_candidate is None
    assert decision.reason_codes == ("like_to_like_rt_mz_mismatch",)


def test_cross_attempt_recovery_evidence_fails_closed_without_retry_inference(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "missing"))
    facts = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"s1": 8.0, "s2": 8.0},
            invocation="attempt-owner",
        ).cells,
        sample_population=fixture.samples,
    )
    retry = fixture.retry()
    retry_universe = _recovery_universe(
        retry,
        (facts,),
        "missing",
        (_peak(retry, "missing", apex_rt=8.0),),
    )

    decision = map_conservative_backfill_candidates(
        (facts,),
        sample=fixture.sample("missing"),
        candidate_universes=(retry_universe,),
    )[0]

    assert decision.disposition == "hold"
    assert decision.matched_candidate is None
    assert decision.reason_codes == ("processing_attempt_mismatch",)


def test_group_support_closure_accepts_predecessor_but_rejects_sibling_attempt(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "missing"))
    predecessor_spine = _spine(
        fixture,
        support_apex_by_sample={"s1": 8.0, "s2": 8.0},
        invocation="predecessor-group",
    )
    retry = fixture.retry()
    retry_assessment = _assessment(
        retry,
        "missing",
        support_apex=None,
        scan_number=900,
        invocation="retry-cell",
    )

    inherited = mint_cell_observation_ref(
        processing_owners=retry.owners,
        sample=retry.sample("missing"),
        target=retry.target,
        group_context=predecessor_spine.cells[0].group_context,
        method_assessment=retry_assessment,
    )
    assert (
        inherited.artifact.producer_processing_attempt_id
        == retry.attempt.processing_attempt_id
    )

    sibling = fixture.retry()
    sibling_spine = _spine(
        sibling,
        support_apex_by_sample={"s1": 8.0, "s2": 8.0},
        invocation="sibling-group",
    )
    with pytest.raises(
        CellObservationContractError,
        match="current attempt or retry lineage",
    ):
        mint_cell_observation_ref(
            processing_owners=retry.owners,
            sample=retry.sample("missing"),
            target=retry.target,
            group_context=sibling_spine.cells[0].group_context,
            method_assessment=retry_assessment,
        )


def test_recovery_receipt_rejects_same_request_sibling_assessment(
    tmp_path: Path,
) -> None:
    fixture = _study_fixture(tmp_path, ("s1", "s2", "missing"))
    facts = assess_conservative_backfill_group(
        _spine(
            fixture,
            support_apex_by_sample={"s1": 8.0, "s2": 8.0},
            invocation="recovery-attempt-owner",
        ).cells,
        sample_population=fixture.samples,
    )
    retry = fixture.retry()
    sibling = fixture.retry()
    sibling_universe = _recovery_universe(
        sibling,
        (facts,),
        "missing",
        (_peak(sibling, "missing", apex_rt=8.0),),
    )

    with pytest.raises(
        ConservativeBackfillContractError,
        match="outside the exact processing attempt",
    ):
        mint_conservative_backfill_recovery_candidate_universe(
            processing_owners=retry.owners,
            sample=retry.sample("missing"),
            group_facts=(facts,),
            candidate_assessment=sibling_universe.candidate_assessment,
        )


def _study_fixture(
    tmp_path: Path,
    sample_stems: tuple[str, ...],
) -> Package2NoRawFixture:
    return build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=sample_stems,
        sample_roles={stem: "study_sample" for stem in sample_stems},
    )


def _spine(
    fixture: Package2NoRawFixture,
    *,
    support_apex_by_sample: dict[str, float],
    reject_sample_stems: frozenset[str] = frozenset(),
    invocation: str,
    target=None,
) -> CidNlReferenceSpine:
    effective_target = fixture.target if target is None else target
    assessments = tuple(
        (
            fixture.sample(sample.metadata.raw_stem),
            (
                _reject_assessment(
                    fixture,
                    sample.metadata.raw_stem,
                    scan_number=100 + index,
                    invocation=f"{invocation}:{index}",
                    target=effective_target,
                )
                if sample.metadata.raw_stem in reject_sample_stems
                else _assessment(
                    fixture,
                    sample.metadata.raw_stem,
                    support_apex=support_apex_by_sample.get(
                        sample.metadata.raw_stem
                    ),
                    scan_number=100 + index,
                    invocation=f"{invocation}:{index}",
                    target=effective_target,
                )
            ),
        )
        for index, sample in enumerate(fixture.samples, start=1)
    )
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=effective_target,
        sample_assessments=assessments,
    )
    assert spine.group is not None
    return spine


def _reject_assessment(
    fixture: Package2NoRawFixture,
    sample_stem: str,
    *,
    scan_number: int,
    invocation: str,
    target=None,
) -> CidNlMethodAssessmentRef:
    effective_target = fixture.target if target is None else target
    evidence = issue161_evidence(
        fixture,
        sample_stem,
        scan_number=scan_number,
        scan_rt_min=8.0,
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        target=effective_target,
        detector_invocation_id=invocation,
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=issue161_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=1,
            reason_code=None,
        ),
        evidence_refs=(evidence,),
    )


def _assessment(
    fixture: Package2NoRawFixture,
    sample_stem: str,
    *,
    support_apex: float | None,
    scan_number: int,
    invocation: str,
    target=None,
) -> CidNlMethodAssessmentRef:
    sample = fixture.sample(sample_stem)
    effective_target = fixture.target if target is None else target
    if support_apex is None:
        return mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=effective_target,
            detector_invocation_id=invocation,
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=7.5,
                query_rt_max=8.6,
                support_policy=issue161_support_policy(),
                acquisition_supported=False,
                search_completed=False,
                scanned_event_count=0,
                reason_code="dda_not_available",
            ),
            technical_state="unsupported",
        )
    peak = _peak(fixture, sample_stem, apex_rt=support_apex)
    evidence_refs = tuple(
        issue161_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number + offset,
            scan_rt_min=support_apex + offset * 0.00001,
        )
        for offset in (0, 1000)
    )
    support_refs = tuple(
        issue161_unique_support(
            fixture,
            sample_stem,
            peak=peak,
            evidence=evidence,
        )
        for evidence in evidence_refs
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=effective_target,
        detector_invocation_id=invocation,
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=issue161_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=2,
            reason_code=None,
        ),
        candidate_peaks=(peak,),
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )


def _peak(
    fixture: Package2NoRawFixture,
    sample_stem: str,
    *,
    apex_rt: float,
):
    return issue161_peak(
        fixture,
        sample_stem,
        apex_rt=apex_rt,
        rt_left_min=apex_rt - 0.1,
        rt_right_min=apex_rt + 0.1,
    )


def _recovery_universe(
    fixture: Package2NoRawFixture,
    group_facts,
    sample_stem: str,
    peaks,
):
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        detector_invocation_id=f"backfill-recovery:{sample_stem}",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.0,
            query_rt_max=9.0,
            support_policy=issue161_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=0,
            reason_code=None,
        ),
        candidate_peaks=tuple(peaks),
    )
    return mint_conservative_backfill_recovery_candidate_universe(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        group_facts=tuple(group_facts),
        candidate_assessment=assessment,
    )


def _forbidden_product_fields(
    model: type[ConservativeBackfillCellDecision],
) -> set[str]:
    forbidden_tokens = {"area", "matrix", "writer", "numeric", "counted"}
    return {
        field.name
        for field in fields(model)
        if any(token in field.name.lower() for token in forbidden_tokens)
    }
