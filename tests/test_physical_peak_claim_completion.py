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
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment_ref,
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


def _view(
    fixture,
    stem,
    *,
    center,
    crop=0,
    support=True,
    broad_context=False,
    narrow_context=False,
    mz=500.0,
    off_apex_difference=False,
    unreliable=False,
):
    sample = fixture.sample(stem)
    rt = np.linspace(7.0, 9.2, 221)[crop:]
    raw = 1000 * np.exp(-0.5 * ((rt - center) / 0.25) ** 2) + 100 * (rt - 7) ** 2
    if broad_context:
        # Overlapping shoulders share one lobe in complete acquired context.
        # Cropping the right context changes baseline, apex and selected bounds.
        full_rt = np.linspace(7.0, 13.0, 301)
        full_raw = 1000 * np.exp(-0.5 * ((full_rt - 9.3) / 0.5) ** 2)
        full_raw += 700 * np.exp(-0.5 * ((full_rt - 10.4) / 0.5) ** 2)
        selected = (
            (full_rt >= (9.8 if crop else 8.7)) & (full_rt <= (11.0 if crop else 9.9))
            if narrow_context
            else (full_rt >= 9.0) & (full_rt <= (11.5 if crop else 11.0))
        )
        rt, raw = full_rt[selected] + center - 9.8, full_raw[selected]
    if off_apex_difference:
        raw[np.argmin(abs(rt - center - 0.3))] += 100.0
    prepared = prepare_physical_signal(rt, raw)
    query_rt = center + ((0.6 if crop else -0.5) if narrow_context else 0.0)
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        PHYSICAL_PEAK_POINT_POLICY,
        PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
    )

    evaluation = evaluate_physical_peak(
        prepared,
        query_rt_min=query_rt,
        search_rt_window=(center - 0.2, center + 0.2) if unreliable else None,
        selection_policy_version=PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY
        if unreliable
        else PHYSICAL_PEAK_POINT_POLICY,
    )
    assert evaluation.facts is not None
    peak = mint_peak_observation_ref(
        sample=sample,
        trace=Trace.from_arrays(
            sample_name=stem,
            mz=mz,
            rt=rt,
            intensity=raw,
            rt_min=float(rt[0]),
            rt_max=float(rt[-1]),
            ppm_tol=20.0,
            acquisition_source_id=fixture.source(sample).acquisition_source_id,
        ),
        integration=physical_peak_observation_integration(evaluation),
        physical_evaluation=evaluation,
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum", "gaussian15"),
    )
    evidence = cid_nl_evidence(fixture, stem, scan_number=101, scan_rt_min=query_rt)
    assessment = mint_cid_nl_method_assessment_ref(
        sample=sample,
        acquisition_source=fixture.source(sample),
        target=fixture.target,
        detector_invocation_id="untargeted-cid-nl-backfill",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=float(rt[0]),
            query_rt_max=float(rt[-1]),
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=True,
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
@pytest.mark.parametrize(
    "broad_context,fresh_context,off_apex_difference",
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (False, False, True),
    ],
    ids=[
        "selected-overlap",
        "retained-envelope",
        "fresh-envelope",
        "off-apex-difference",
    ],
)
def test_one_measured_lobe_keeps_one_direct_claim_or_one_backfill_winner(
    tmp_path,
    support,
    direct,
    reverse,
    broad_context,
    fresh_context,
    off_apex_difference,
    unreliable=False,
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
            narrow_context=fresh_context,
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
            sample=sample, assessment=assessment
        )
        owners[stem] = owner_from_cid_nl_assessment_support(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
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
    identities = tuple(group.membership for group in groups)
    initial = CrossSampleGroupingResult(
        samples=fixture.samples,
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
            crop=0
            if off_apex_difference or unreliable
            else index
            if broad_context
            else 45 + 15 * index,
            support=support,
            broad_context=broad_context,
            narrow_context=fresh_context,
            mz=500.001 if fresh_context and index else 500.0,
            off_apex_difference=off_apex_difference and index == 1,
            unreliable=unreliable,
        )
        assert assessment.content.method_result == ("SUPPORT" if support else "HOLD")
        backfills[
            (
                group.group_hypothesis_id,
                sample.sample_name,
                assessment.content.method_subject.method_subject_id,
            )
        ] = (sample, assessment)
    comparison_peaks = [pair[1].candidate_peaks[0] for pair in backfills.values()]
    if direct:
        comparison_peaks.append(pairs["S"][1].candidate_peaks[0])
    assert len({peak.content.integration.rt_apex_min for peak in comparison_peaks}) == (
        1 if off_apex_difference or unreliable else 2
    )
    assert len({peak.content.physical_identity for peak in comparison_peaks}) == (
        1 if unreliable else 2
    )
    if fresh_context:
        integrations = [peak.content.integration for peak in comparison_peaks]
        lower = max(i.rt_left_min for i in integrations)
        upper = min(i.rt_right_min for i in integrations)
        assert not all(lower < i.rt_apex_min < upper for i in integrations)
    comparison_views = {}
    if fresh_context:
        from xic_extractor.acquisition_contract import (
            TraceCoordinateKind,
            acquire_trace_batch,
            mint_trace_acquisition_capability,
        )
        from xic_extractor.peak_detection.physical_peak_equivalence import (
            physical_peak_view_from_observation,
        )
        from xic_extractor.untargeted_execution.physical_comparison_context import (
            comparison_views_from_acquisitions,
        )
        from xic_extractor.xic_models import XICRequest, XICTrace

        assert (
            compare_physical_peak_views(
                *(physical_peak_view_from_observation(p) for p in comparison_peaks)
            )
            == "unresolved"
        )
        source = fixture.source(fixture.sample("S"))
        full_rt = np.linspace(7.0, 13.0, 301)
        full_raw = 1000 * np.exp(-0.5 * ((full_rt - 9.3) / 0.5) ** 2)
        full_raw += 700 * np.exp(-0.5 * ((full_rt - 10.4) / 0.5) ** 2)
        shifted_rt = full_rt + 8.06 - 9.8

        class ComparisonRaw:
            raw_chromatogram_call_count = 0

            def extract_xic_many(self, requests):
                self.raw_chromatogram_call_count += 1
                result = []
                for request in requests:
                    raw = full_raw.copy()
                    if request.mz > 500.0:
                        # A different ion far outside the original questions is
                        # not part of this comparison, even in a shared batch.
                        raw[-10] += 2000.0
                    result.append(XICTrace.from_arrays(shifted_rt, raw))
                return tuple(result)

        acquisition = acquire_trace_batch(
            mint_trace_acquisition_capability(
                source,
                adapter_namespace="test:comparison-context",
                coordinate_kind=TraceCoordinateKind.RT_ONLY,
            ),
            tuple(
                XICRequest(
                    peak.content.mz, float(shifted_rt[0]), float(shifted_rt[-1]), 20.0
                )
                for peak in comparison_peaks
            ),
            ComparisonRaw(),
        )
        comparison_views = comparison_views_from_acquisitions(
            ((tuple(comparison_peaks), acquisition),)
        )
        assert all(
            view.comparison_trace is not None for view in comparison_views.values()
        )
    if reverse:
        backfills = dict(reversed(tuple(backfills.items())))
        groups = tuple(reversed(groups))
    result, successors, _, retained, contested = admit_cid_nl_group_backfills(
        result=initial,
        groups=groups,
        backfills=backfills,
        alignment_config=config,
        member_observations={
            peak.content.physical_identity: peak
            for _, assessment in pairs.values()
            for peak in assessment.candidate_peaks
        },
        physical_peak_views=comparison_views,
    )
    assert len(result.partition_transition.admitted_members) == int(
        support and not direct
    )
    assert bool(retained) == (support and not direct)
    assert len(contested) == 1
    if not direct:
        assert len(result.backfill_membership_risks) == 1
        risk = result.backfill_membership_risks[0][2]
        assert risk.phase == "backfill"
        assert "backfill_competition" in risk.risk_flags
        assert risk.cost_gap >= 0
        if unreliable:
            assert "backfill_low_localization_confidence" in risk.risk_flags
    cells = compose_cid_nl_predecessor_cells(
        groups=successors,
        sample_assessments=tuple(pairs.values()),
        alignment_config=config,
        rt_frame=frame,
        sample_universe=frozenset(pairs),
        backfill_assessments_by_group={
            (
                next(
                    s.group_hypothesis_id
                    for s in successors
                    if set(g.owner_ids) <= set(s.owner_ids)
                ),
                sample_id,
                subject,
            ): pair
            for (group_id, sample_id, subject), pair in backfills.items()
            for g in groups
            if g.group_hypothesis_id == group_id
        },
        retained_hypotheses_by_assessment=retained,
        rejected_backfill_cells=contested,
    )
    sample_cells = [
        cell
        for cell in cells
        if cell.content.sample_key == fixture.sample("S").sample_name
    ]
    assert len(sample_cells) == 2
    numeric_sources = [
        cell.peak_observation
        for cell in sample_cells
        if cell.peak_observation is not None
    ]
    assert len(numeric_sources) == 1
    if direct:
        assert numeric_sources == list(pairs["S"][1].candidate_peaks)


def test_low_localization_still_selects_one_shared_acquired_peak(tmp_path):
    test_one_measured_lobe_keeps_one_direct_claim_or_one_backfill_winner(
        tmp_path,
        support=False,
        direct=False,
        reverse=False,
        broad_context=False,
        fresh_context=False,
        off_apex_difference=False,
        unreliable=True,
    )


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
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
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
    left = PhysicalPeakView(
        "source",
        4.8,
        4.0,
        6.0,
        500.0,
        20.0,
        rt,
        raw,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    right = PhysicalPeakView(
        "source",
        5.2,
        4.1,
        6.1,
        500.001,
        20.0,
        rt,
        other_raw,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    assert compare_physical_peak_views(left, right) == "same"
    assert compare_physical_peak_views(right, left) == "same"


@pytest.mark.parametrize("window_points", [3, 21])
def test_acquired_valley_keeps_lobes_distinct_at_any_smoothing_width(window_points):
    rt = np.linspace(4.0, 6.0, 201)
    raw = sum(
        1000.0 * np.exp(-0.5 * ((rt - center) / 0.01) ** 2) for center in (4.94, 5.06)
    )
    profile = SignalProcessingProfile(window_points=window_points)
    views = [
        PhysicalPeakView(
            "source", apex, 4.8, 5.2, 500.0, 20.0, rt, raw, profile=profile
        )
        for apex in (4.94, 5.06)
    ]
    # The split is read from the acquired scans, so canonical smoothing width
    # cannot merge two lobes that the scans separate.
    assert compare_physical_peak_views(*views) == "distinct"


@pytest.mark.parametrize("difference", ["peak_height", "flanks"])
def test_same_apex_does_not_hide_changed_peak_height_or_substantial_flanks(difference):
    rt = np.linspace(4.0, 6.0, 201)
    raw = 1000 * np.exp(-0.5 * ((rt - 5.0) / 0.3) ** 2)
    other = raw.copy()
    if difference == "peak_height":
        # Even a small amplitude change is not evidence of one measured maximum.
        other *= 1.01
    else:
        other[(abs(rt - 5.0) > 0.4) & (abs(rt - 5.0) < 0.6)] += 200
        assert max(other) == max(raw)
    views = [
        PhysicalPeakView(
            "source",
            5.0,
            4.0,
            6.0,
            mz,
            20.0,
            rt,
            signal,
            profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
        )
        for mz, signal in ((500.0, raw), (500.001, other))
    ]
    assert compare_physical_peak_views(*views) == "unresolved"
    assert compare_physical_peak_views(*reversed(views)) == "unresolved"
