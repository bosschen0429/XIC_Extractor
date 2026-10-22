"""Different views cannot acquire a second numeric claim through Backfill."""

import numpy as np
import pytest

from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from xic_extractor.alignment.cid_nl_predecessor_cells import (
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePartitionTransition,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.grouping_workflow import (
    admit_cid_nl_group_backfills,
    group_cid_nl_owners,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CidNlPhysicalCandidateFact,
    group_cid_nl_physical_subject_units,
)
from xic_extractor.peak_detection.observations import (
    mint_peak_observation_ref,
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.physical_peak_equivalence import (
    PhysicalPeakView,
    compare_physical_peak_views,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import resolve_request_acquisition_source


def _view(fixture, stem, *, center, crop=0, support=True, broad_context=False):
    sample = fixture.sample(stem)
    rt = np.linspace(7.0, 9.2, 221)[crop:]
    raw = 1000 * np.exp(-0.5 * ((rt - center) / 0.25) ** 2) + 100 * (rt - 7) ** 2
    if broad_context:
        # Overlapping shoulders share one lobe in complete acquired context.
        # Cropping the right context changes baseline, apex and selected bounds.
        full_rt = np.linspace(7.0, 13.0, 301)
        full_raw = 1000 * np.exp(-0.5 * ((full_rt - 9.3) / 0.5) ** 2)
        full_raw += 700 * np.exp(-0.5 * ((full_rt - 10.4) / 0.5) ** 2)
        selected = (full_rt >= 9.0) & (full_rt <= (11.5 if crop else 11.0))
        rt, raw = full_rt[selected] + center - 9.8, full_raw[selected]
    prepared = prepare_physical_signal(rt, raw)
    evaluation = evaluate_physical_peak(
        prepared, selection_subject_id=stem, query_rt_min=center
    )
    assert evaluation.facts is not None
    peak = mint_peak_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        trace=Trace.from_arrays(
            sample_name=stem,
            mz=500.0,
            rt=rt,
            intensity=raw,
            rt_min=float(rt[0]),
            rt_max=float(rt[-1]),
            ppm_tol=20.0,
            acquisition_source_id=resolve_request_acquisition_source(
                processing_owners=fixture.owners, sample=sample
            ).acquisition_source_id,
        ),
        integration=physical_peak_observation_integration(evaluation),
        physical_evaluation=evaluation,
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        selection_intensity=prepared.prepared.positive_signal,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum", "gaussian15"),
    )
    evidence = cid_nl_evidence(fixture, stem, scan_number=101, scan_rt_min=center)
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id="untargeted-cid-nl-backfill",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=float(rt[0]),
            query_rt_max=float(rt[-1]),
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            scanned_event_count=1 if support else 0,
            reason_code=None,
        ),
        candidate_peaks=(peak,),
        evidence_refs=(evidence,) if support else (),
        individual_support_refs=(
            cid_nl_unique_support(fixture, stem, peak=peak, evidence=evidence),
        )
        if support
        else (),
    )
    return sample, assessment


@pytest.mark.parametrize("support", [False, True], ids=["ms1-only", "support"])
@pytest.mark.parametrize(
    "direct", [False, True], ids=["backfill-backfill", "direct-backfill"]
)
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("broad_context", [False, True])
def test_one_measured_lobe_keeps_one_direct_claim_or_no_backfill_winner(
    tmp_path, support, direct, reverse, broad_context
):
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("A", "B", "C", "D", "S")
    )
    pairs = {
        stem: _view(
            fixture,
            stem,
            center=center,
            crop=int(broad_context and stem in {"C", "D"}),
            broad_context=broad_context,
        )
        for stem, center in (
            ("A", 8.0),
            ("B", 8.0),
            ("C", 8.18),
            ("D", 8.18),
            ("S", 8.06),
        )
    }
    owners = {}
    for stem, (sample, assessment) in pairs.items():
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=fixture.owners, sample=sample, assessment=assessment
        )
        owners[stem] = owner_from_cid_nl_assessment_support(
            sample=sample, assessment=assessment, hypothesis=hypothesis
        )
    config, frame = AlignmentConfig(), SampleRtFrame()
    groups = tuple(
        group_cid_nl_owners(
            tuple(owners[stem] for stem in stems),
            config=config,
            sample_universe=frozenset(pairs),
            rt_frame=frame,
        )[0]
        for stems in (("A", "B", "S") if direct else ("A", "B"), ("C", "D"))
    )
    assert all(group.disposition == "established" for group in groups)
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    initial = CrossSampleGroupingResult(
        processing_owners=fixture.owners,
        rt_frame=frame,
        partition_transition=CrossSamplePartitionTransition(
            status="assessable",
            predecessors=identities,
            successors=identities,
            lineage=(),
            reason="unchanged_membership_no_lineage",
        ),
    )
    backfills = {}
    for index, group in enumerate(groups):
        if direct and index == 0:
            continue
        sample, assessment = _view(
            fixture,
            "S",
            center=8.06,
            crop=index if broad_context else 45 + 15 * index,
            support=support,
            broad_context=broad_context,
        )
        assert assessment.content.method_result == ("SUPPORT" if support else "HOLD")
        backfills[
            (
                group.group_hypothesis_id,
                sample.identity.sample_identity_id,
                assessment.content.method_subject.method_subject_id,
            )
        ] = (sample, assessment)
    comparison_peaks = [pair[1].candidate_peaks[0] for pair in backfills.values()]
    if direct:
        comparison_peaks.append(pairs["S"][1].candidate_peaks[0])
    assert len({peak.content.integration.rt_apex_min for peak in comparison_peaks}) == 2
    assert (
        len({peak.content.peak_observation_content_id for peak in comparison_peaks})
        == 2
    )
    if broad_context:
        integrations = [peak.content.integration for peak in comparison_peaks]
        lower = max(i.rt_left_min for i in integrations)
        upper = min(i.rt_right_min for i in integrations)
        assert not all(lower < i.rt_apex_min < upper for i in integrations)
    # Method planning must recognize the same views as the claim owner, rather
    # than keeping an exact-apex prefilter ahead of the shared evidence rule.
    units = group_cid_nl_physical_subject_units(
        tuple(
            CidNlPhysicalCandidateFact(
                str(index),
                peak.content.mz,
                116.0474,
                PhysicalPeakView(
                    acquisition_source_id=peak.generation_provenance.acquisition_source_id,
                    apex_rt_min=peak.content.integration.rt_apex_min,
                    left_rt_min=peak.content.integration.rt_left_min,
                    right_rt_min=peak.content.integration.rt_right_min,
                    xic_mz=peak.content.mz,
                    xic_ppm_tol=peak.generation_provenance.query_ppm_tol,
                    rt=peak.source_trace.rt,
                    raw=peak.source_trace.intensity,
                ),
            )
            for index, peak in enumerate(comparison_peaks)
        ),
        composition=None,
        precursor_tolerance_ppm=20.0,
    )
    assert len(units) == 1
    if reverse:
        backfills = dict(reversed(tuple(backfills.items())))
        groups = tuple(reversed(groups))
    result, successors, _, retained, contested = admit_cid_nl_group_backfills(
        result=initial,
        groups=groups,
        backfills=backfills,
        alignment_config=config,
        member_observations={
            peak.content.peak_observation_content_id: peak
            for _, assessment in pairs.values()
            for peak in assessment.candidate_peaks
        },
    )
    assert result.partition_transition.admitted_members == ()
    assert {g.group_hypothesis_id for g in successors} == {
        g.group_hypothesis_id for g in groups
    }
    assert not retained
    assert len(contested) == (1 if direct else 2)
    cells = compose_cid_nl_predecessor_cells(
        processing_owners=fixture.owners,
        groups=groups,
        sample_assessments=tuple(pairs.values()),
        alignment_config=config,
        rt_frame=frame,
        sample_universe=frozenset(pairs),
        backfill_assessments_by_group=backfills,
        contested_backfill_peaks=contested,
    )
    sample_cells = [
        cell
        for cell in cells
        if cell.content.canonical_sample_identity_id
        == fixture.sample("S").identity.sample_identity_id
    ]
    assert len(sample_cells) == 2
    numeric_sources = [
        cell.peak_observation
        for cell in sample_cells
        if cell.peak_observation is not None
    ]
    assert len(numeric_sources) == int(direct)
    if direct:
        assert numeric_sources == list(pairs["S"][1].candidate_peaks)


@pytest.mark.parametrize("second_apex, expected", [(5.2, "same"), (7.0, "distinct")])
def test_full_acquired_lobes_not_selected_interval_overlap_define_identity(
    second_apex, expected
):
    # One broad lobe plus a separated second peak. Context-dependent selections
    # may put apices outside the other view's integral; identity uses the full
    # acquired signal without changing either reported integral.
    rt = np.linspace(3.0, 9.0, 301)
    raw = 1000 * np.exp(-0.5 * ((rt - 5.0) / 0.35) ** 2)
    raw += 800 * np.exp(-0.5 * ((rt - 7.0) / 0.2) ** 2)

    def view(apex, lower, upper):
        return PhysicalPeakView(
            acquisition_source_id="sample-source",
            apex_rt_min=apex,
            left_rt_min=lower,
            right_rt_min=upper,
            xic_mz=500.0,
            xic_ppm_tol=20.0,
            rt=rt,
            raw=raw,
        )

    left = view(4.8, 4.3, 5.1)
    right = view(second_apex, 4.9, 7.5)
    assert compare_physical_peak_views(left, right) == expected
    assert compare_physical_peak_views(right, left) == expected


def test_unrelated_acquired_tail_does_not_erase_shared_selected_lobe_evidence():
    rt = np.linspace(3.0, 9.0, 301)
    raw = 1000 * np.exp(-0.5 * ((rt - 5.0) / 0.35) ** 2)
    other_raw = raw.copy()
    other_raw[-10] += 20.0
    left = PhysicalPeakView("source", 4.8, 4.0, 6.0, 500.0, 20.0, rt, raw)
    right = PhysicalPeakView("source", 5.2, 4.1, 6.1, 500.001, 20.0, rt, other_raw)
    assert compare_physical_peak_views(left, right) == "same"
    assert compare_physical_peak_views(right, left) == "same"
