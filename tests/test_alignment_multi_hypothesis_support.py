from __future__ import annotations

from pathlib import Path

import pytest

from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_bootstrap import (
    bootstrap_cid_nl_initial_population,
    measured_calibration_anchor_groups,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
    cid_nl_evidence_observation,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.precursor_evidence import Ms1PrecursorObservation


def test_one_assessment_preserves_two_individually_supported_peaks(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("dual",))
    sample = fixture.sample("dual")
    left = cid_nl_peak(
        fixture,
        "dual",
        apex_rt=7.8,
        rt_left_min=7.7,
        rt_right_min=7.9,
    )
    right = cid_nl_peak(
        fixture,
        "dual",
        apex_rt=8.2,
        rt_left_min=8.1,
        rt_right_min=8.3,
    )
    peaks = (left, right)
    left_evidence = cid_nl_evidence(
        fixture,
        "dual",
        scan_number=101,
        scan_rt_min=7.8,
    )
    right_evidence = cid_nl_evidence(
        fixture,
        "dual",
        scan_number=102,
        scan_rt_min=8.2,
    )
    left_support = cid_nl_unique_support(
        fixture,
        "dual",
        peak=left,
        evidence=left_evidence,
        candidate_peaks=peaks,
    )
    right_support = cid_nl_unique_support(
        fixture,
        "dual",
        peak=right,
        evidence=right_evidence,
        candidate_peaks=peaks,
    )
    assessment = mint_cid_nl_method_assessment_ref(
        sample=sample,
        acquisition_source=fixture.source(sample),
        target=fixture.target,
        detector_invocation_id="gaussian15:dual",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.5,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            reason_code=None,
        ),
        candidate_peaks=peaks,
        evidence_refs=(left_evidence, right_evidence),
        individual_support_refs=(left_support, right_support),
    )

    hypotheses = compose_cid_nl_peak_hypotheses_from_assessment(
        sample=sample,
        assessment=assessment,
    )

    assert len(hypotheses) == 2
    assert {
        hypothesis.peak_observation_refs[0].content.physical_peak_key
        for hypothesis in hypotheses
    } == {
        left.content.physical_peak_key,
        right.content.physical_peak_key,
    }
    for hypothesis in hypotheses:
        owner = owner_from_cid_nl_assessment_support(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        assert owner.owner_id == hypothesis.hypothesis_id
        assert owner.peak_shape is not None
        observation = hypothesis.peak_observation_refs[0].content
        assert owner.peak_shape.source_point_count == sum(
            abs(rt - observation.integration.rt_apex_min) <= 0.35
            for rt in observation.rt
        )

    grouping_scope, _candidates, _drift = bootstrap_cid_nl_initial_population(
        sample_assessments=((sample, assessment),),
        alignment_config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
    )

    assert sorted(group.owner_ids for group in grouping_scope.groups) == sorted(
        (hypothesis.hypothesis_id,) for hypothesis in hypotheses
    )


def test_identity_membership_is_geometry_not_group_center_reclassification(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("early", "late"),
    )
    sample_assessments = tuple(
        (
            fixture.sample(sample_stem),
            _assessment_with_observation_only_peak(
                fixture,
                sample_stem=sample_stem,
                direct_apex_rt=direct_apex_rt,
                observed_apex_rt=observed_apex_rt,
                scan_number=scan_number,
            ),
        )
        for sample_stem, direct_apex_rt, observed_apex_rt, scan_number in (
            ("early", 7.80, 8.40, 201),
            ("late", 7.82, 8.88, 202),
        )
    )

    grouping_scope, candidates, _drift = bootstrap_cid_nl_initial_population(
        sample_assessments=sample_assessments,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        rt_frame=SampleRtFrame(),
    )

    direct_ids = frozenset(
        candidate.peak_hypothesis_id
        for candidate in candidates
        if candidate.direct_support_assessment is not None
    )
    observation_ids = frozenset(
        candidate.peak_hypothesis_id
        for candidate in candidates
        if candidate.direct_support_assessment is None
    )
    assert len(direct_ids) == len(observation_ids) == 2
    assert {frozenset(group.owner_ids) for group in grouping_scope.groups} == {
        direct_ids,
        observation_ids,
    }
    assert {
        frozenset(owner.owner_id for owner in group)
        for group in measured_calibration_anchor_groups(grouping_scope, candidates)
    } == {direct_ids}


def _assessment_with_observation_only_peak(
    fixture,
    *,
    sample_stem: str,
    direct_apex_rt: float,
    observed_apex_rt: float,
    scan_number: int,
):
    sample = fixture.sample(sample_stem)
    direct_peak = cid_nl_peak(
        fixture,
        sample_stem,
        apex_rt=direct_apex_rt,
        rt_left_min=direct_apex_rt - 0.1,
        rt_right_min=direct_apex_rt + 0.1,
    )
    observed_peak = cid_nl_peak(
        fixture,
        sample_stem,
        apex_rt=observed_apex_rt,
        rt_left_min=observed_apex_rt - 0.1,
        rt_right_min=observed_apex_rt + 0.1,
    )
    peaks = (direct_peak, observed_peak)
    evidence = cid_nl_evidence(
        fixture,
        sample_stem,
        scan_number=scan_number,
        scan_rt_min=direct_apex_rt,
    )
    support = cid_nl_unique_support(
        fixture,
        sample_stem,
        peak=direct_peak,
        evidence=evidence,
        candidate_peaks=peaks,
    )
    return mint_cid_nl_method_assessment_ref(
        sample=sample,
        acquisition_source=fixture.source(sample),
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=9.2,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=True,
            reason_code=None,
        ),
        candidate_peaks=peaks,
        evidence_refs=(evidence,),
        individual_support_refs=(support,),
    )


def test_supported_identity_uses_independent_relation_masses_per_sample(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("mixed", "direct")
    )
    projected = {}
    for stem, physical_mz, height in (
        ("mixed", 500.0, 700.0),
        ("direct", 500.003, 900.0),
    ):
        sample = fixture.sample(stem)
        peak = cid_nl_peak(fixture, stem, mz=physical_mz, height=height)
        direct_precursor = 500.002 if stem == "mixed" else 500.003
        direct_product = 383.9536 if stem == "mixed" else 383.9546
        direct = cid_nl_evidence_observation(
            sample=sample,
            acquisition_source=fixture.source(sample),
            scan_number=301,
            precursor_mz=direct_precursor,
            product_mz=direct_product,
            configured_neutral_loss_da=116.0474,
            observed_neutral_loss_da=116.0484,
            observed_loss_error_ppm=0.001 / 116.0474 * 1e6,
            scan_rt_min=8.0,
            product_intensity=400.0,
        )
        supported: tuple[CidNlEvidenceObservation, ...] = (direct,)
        if stem == "mixed":
            inferred = cid_nl_evidence_observation(
                sample=sample,
                acquisition_source=fixture.source(sample),
                scan_number=302,
                precursor_mz=500.0,
                product_mz=383.9526,
                configured_neutral_loss_da=116.0474,
                observed_neutral_loss_da=116.0474,
                observed_loss_error_ppm=None,
                scan_rt_min=8.02,
                product_intensity=500.0,
                precursor_mz_basis="product_plus_neutral_loss",
                scan_precursor_mz=500.0,
                precursor_inference_tolerance_da=0.8,
                precursor_metadata=Ms2PrecursorMetadata(
                    charge=1,
                    isolation_width_da=1.6,
                    isolation_offset_da=0.0,
                    master_scan_number=300,
                ),
                ms1_precursor_observation=Ms1PrecursorObservation(
                    scan_number=300, rt_min=8.0, centroid_mzs=(500.006,)
                ),
            )
            supported += (inferred,)
        unsupported = cid_nl_evidence_observation(
            sample=sample,
            acquisition_source=fixture.source(sample),
            scan_number=303,
            precursor_mz=500.007,
            product_mz=383.9596,
            configured_neutral_loss_da=116.0474,
            observed_neutral_loss_da=116.0474,
            observed_loss_error_ppm=0.0,
            scan_rt_min=8.3,
            product_intensity=600.0,
        )
        relations = tuple(
            cid_nl_unique_support(fixture, stem, peak=peak, evidence=evidence)
            for evidence in supported
        )
        assessment = mint_cid_nl_method_assessment_ref(
            sample=sample,
            acquisition_source=fixture.source(sample),
            target=fixture.target,
            detector_invocation_id=f"gaussian15:{stem}",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=7.5,
                query_rt_max=8.5,
                support_policy=cid_nl_support_policy(),
                acquisition_supported=True,
                search_completed=True,
                reason_code=None,
            ),
            candidate_peaks=(peak,),
            evidence_refs=(*supported, unsupported),
            individual_support_refs=relations,
        )
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            sample=sample, assessment=assessment
        )
        owner = owner_from_cid_nl_assessment_support(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        projected[stem] = owner
        assert owner.sample_stem == stem
        assert owner.owner_id == hypothesis.hypothesis_id
        assert owner.precursor_mz == physical_mz
        assert owner.selected_integration == peak.content.integration
        assert owner.owner_apex_rt == 8.0
        assert owner.owner_area == height * 6.6
    mixed = projected["mixed"].primary_identity_event
    direct_event = projected["direct"].primary_identity_event
    assert mixed is not None and direct_event is not None
    # Analytical means of accepted independent references: (500.002, 500.006),
    # products (383.9536, 383.9526); the constructed 500.0 is not measured.
    assert mixed.precursor_mz == pytest.approx(500.004, abs=1e-9, rel=0)
    assert mixed.product_mz == pytest.approx(383.9531, abs=1e-9, rel=0)
    assert mixed.observed_neutral_loss_da == pytest.approx(116.0509, abs=1e-9, rel=0)
    assert mixed.seed_event_count == 2
    assert direct_event.precursor_mz == pytest.approx(500.003, abs=1e-9, rel=0)
    assert direct_event.product_mz == pytest.approx(383.9546, abs=1e-9, rel=0)
    assert direct_event.observed_neutral_loss_da == pytest.approx(
        116.0484, abs=1e-9, rel=0
    )
    assert direct_event.seed_event_count == 1
