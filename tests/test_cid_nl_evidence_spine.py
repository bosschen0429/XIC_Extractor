from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from tests.cid_nl_evidence_spine_fixtures import (
    _completed_receipt,
    _truth_table_fixture,
)
from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.processing_identity_no_raw_fixture import (
    NEUTRAL_LOSS_DA,
    PRECURSOR_MZ,
    build_processing_identity_no_raw_fixture,
)
from xic_extractor.alignment.cell_observation.observations import (
    CellObservationContractError,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.peak_detection import cid_nl_method_assessment as assessment_module
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelationContractError,
    resolve_dda_cid_nl_candidate_set_relation_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceContractError,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportContractError,
)
from xic_extractor.precursor_evidence import precursor_isolation_membership


@pytest.mark.parametrize(
    "selected,charge,width,offset,supported,membership",
    [
        (301.164978, 1, 1.6, 0.0, True, "outside_nominal"),
        (302.168210, 1, 1.6, 0.0, True, "outside_nominal"),
        (301.164978, 1, 2.4, 0.0, True, "inside_nominal"),
        (301.164978, 1, None, None, False, "unknown"),
        (301.164978, 1, 2.4, None, False, "unknown"),
        (301.164978, 1, 1.6, -0.5, True, "inside_nominal"),
        (301.20, 1, 1.6, 0.0, False, "outside_nominal"),
        (299.158145, 1, 1.6, 0.0, False, "outside_nominal"),
        (301.164978, None, 1.6, 0.0, False, "outside_nominal"),
        (301.164978, 1, 1.6, 2.0, False, "outside_nominal"),
    ],
)
@pytest.mark.parametrize("product_matches", (True, False))
def test_recorded_monoisotopic_reference_accepts_isolated_vendor_isotope(
    tmp_path: Path,
    selected,
    charge,
    width,
    offset,
    supported,
    membership,
    product_matches,
) -> None:
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("s1",))
    mono = 300.1615
    product = 184.11323547363 if product_matches else 184.21323547363
    loss = 116.0474
    evidence = mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        scan_number=19561,
        precursor_mz=mono,
        product_mz=product,
        configured_neutral_loss_da=loss,
        observed_neutral_loss_da=mono - product,
        observed_loss_error_ppm=abs(mono - product - loss) / loss * 1e6,
        scan_rt_min=8.0,
        product_intensity=278825.7,
        precursor_mz_basis="scan_monoisotopic",
        scan_precursor_mz=selected,
        precursor_metadata=Ms2PrecursorMetadata(
            monoisotopic_mz=mono,
            charge=charge,
            isolation_width_da=width,
            isolation_offset_da=offset,
        ),
    )
    individual, sets = assessment_module.resolve_cid_nl_method_relations(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        evidence_refs=(evidence,),
        candidate_peaks=(cid_nl_peak(fixture, "s1", mz=mono),),
        policy=cid_nl_support_policy(),
    )
    assert bool(individual) is (supported and product_matches)
    assert not sets
    observation = evidence.observation
    assert (
        precursor_isolation_membership(
            observation.precursor_mz,
            observation.scan_precursor_mz,
            observation.precursor_metadata,
        )
        == membership
    )
    assert observation.supports_direct_nl is supported
    if product_matches:
        assert evidence.observation.observed_loss_error_ppm == pytest.approx(
            7.44977, abs=1e-5
        )


@pytest.mark.parametrize(
    "centroids,selected,width,offset,parent_rt,status,supported",
    [
        ((500.001,), 500.5, 1.6, 0.0, 7.99, "compatible", True),
        ((), 500.5, 1.6, 0.0, 7.99, "parent_ms1_ion_missing", False),
        ((500.001, 500.002), 500.5, 1.6, 0.0, 7.99, "parent_ms1_ion_ambiguous", False),
        ((500.05,), 500.5, 1.6, 0.0, 7.99, "neutral_loss_mismatch", False),
        ((500.001,), 499.2005, 1.6, 0.0, 7.99, "outside_nominal_isolation", False),
        ((500.001,), 500.5, None, None, 7.99, "isolation_unavailable", False),
        ((500.001,), 500.5, 1.6, 0.0, 7.8, "compatible", False),
    ],
)
@pytest.mark.parametrize(
    "reference_requirement", ["independent_mass", "recorded_precursor"]
)
def test_parent_ms1_measurement_qualifies_inference_without_rewriting_origin(
    tmp_path,
    centroids,
    selected,
    width,
    offset,
    parent_rt,
    status,
    supported,
    reference_requirement,
):
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
    from xic_extractor.precursor_evidence import Ms1PrecursorObservation

    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("s1",))
    evidence = mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        scan_number=42,
        precursor_mz=500.0,
        product_mz=500.0 - 116.0474,
        configured_neutral_loss_da=116.0474,
        observed_neutral_loss_da=116.0474,
        observed_loss_error_ppm=None,
        scan_rt_min=8.0,
        product_intensity=400.0,
        precursor_mz_basis="product_plus_neutral_loss",
        scan_precursor_mz=selected,
        precursor_inference_tolerance_da=0.8,
        precursor_metadata=Ms2PrecursorMetadata(
            charge=1,
            isolation_width_da=width,
            isolation_offset_da=offset,
            master_scan_number=41,
        ),
        ms1_precursor_observation=Ms1PrecursorObservation(41, parent_rt, centroids),
    )
    policy = replace(
        cid_nl_support_policy(), precursor_reference_requirement=reference_requirement
    )
    supported = supported and reference_requirement == "independent_mass"
    verdict = evidence.observation.assess_precursor_loss(
        policy.neutral_loss_tolerance_ppm
    )
    assert verdict.status == status
    peak = cid_nl_peak(fixture, "s1")
    individual, sets = assessment_module.resolve_cid_nl_method_relations(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        evidence_refs=(evidence,),
        candidate_peaks=(peak,),
        policy=policy,
    )
    assert bool(individual) is supported
    assert not sets
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        target=fixture.target,
        detector_invocation_id="retained-precursor-evidence",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.5,
            support_policy=policy,
            acquisition_supported=True,
            search_completed=True,
            reason_code=None,
        ),
        candidate_peaks=(peak,),
        evidence_refs=(evidence,),
        individual_support_refs=individual,
        set_relation_refs=sets,
    )
    assert assessment.content.method_result == ("SUPPORT" if supported else "HOLD")
    observation = evidence.observation
    assert observation.precursor_mz_basis == "product_plus_neutral_loss"
    assert observation.precursor_mz == 500.0
    assert observation.observed_loss_error_ppm is None
    assert observation.ms1_precursor_observation is not None
    assert observation.ms1_precursor_observation.centroid_mzs == tuple(centroids)
    if supported:
        assert individual[0].content.precursor_mz == 500.001
        assert individual[0].content.observed_loss_error_ppm == pytest.approx(
            0.001 / 116.0474 * 1e6
        )
    with pytest.raises(CidNlEvidenceContractError, match="recorded parent"):
        replace(
            evidence.observation,
            ms1_precursor_observation=Ms1PrecursorObservation(40, 7.99, (500.001,)),
        )


def test_inferred_evidence_persists_scan_provenance_and_exact_projection(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("s1",))
    direct = cid_nl_evidence(fixture, "s1", scan_number=42, scan_rt_min=8.0)
    observation = direct.observation
    inferred = mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        scan_number=observation.scan_number,
        precursor_mz=observation.precursor_mz,
        product_mz=observation.product_mz,
        configured_neutral_loss_da=observation.configured_neutral_loss_da,
        observed_neutral_loss_da=observation.observed_neutral_loss_da,
        observed_loss_error_ppm=None,
        scan_rt_min=observation.scan_rt_min,
        product_intensity=observation.product_intensity,
        precursor_mz_basis="product_plus_neutral_loss",
        scan_precursor_mz=observation.precursor_mz + 0.25,
        precursor_inference_tolerance_da=0.25,
    )
    assert inferred.observation.precursor_mz_basis == "product_plus_neutral_loss"
    assert inferred.observation.observed_loss_error_ppm is None
    assert inferred.observation.supports_direct_nl is False
    assert assessment_module.resolve_cid_nl_method_relations(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        evidence_refs=(inferred,),
        candidate_peaks=(cid_nl_peak(fixture, "s1"),),
        policy=cid_nl_support_policy(),
    ) == ((), ())
    with pytest.raises(CidNlEvidenceContractError, match="no independent"):
        replace(inferred.observation, observed_loss_error_ppm=0.0)
    assert inferred.observation.scan_precursor_mz == observation.precursor_mz + 0.25
    assert inferred.observation.precursor_inference_tolerance_da == 0.25
    assert direct.observation.precursor_mz_basis == "scan_precursor"
    assert direct.observation.scan_precursor_mz == observation.precursor_mz
    assert direct.observation.precursor_inference_tolerance_da is None


@pytest.mark.parametrize(
    ("changed", "message"),
    (
        ({"scan_precursor_mz": None}, "explicit scan_precursor_mz"),
        ({"precursor_inference_tolerance_da": None}, "requires"),
        ({"precursor_inference_tolerance_da": 0.0}, "positive"),
        ({"precursor_inference_tolerance_da": float("nan")}, "finite"),
        ({"precursor_inference_tolerance_da": 0.249}, "exceeds"),
        ({"precursor_mz": PRECURSOR_MZ + 0.1}, "product_mz"),
        ({"scan_precursor_mz": float("inf")}, "finite"),
        ({"precursor_mz_basis": "scan_precursor"}, "must equal"),
        (
            {"precursor_mz_basis": "scan_precursor", "scan_precursor_mz": PRECURSOR_MZ},
            "cannot declare",
        ),
    ),
)
def test_inferred_evidence_rejects_unbound_or_crossed_precursor_provenance(
    tmp_path: Path, changed: dict, message: str
) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("s1",))
    arguments = {
        "processing_owners": fixture.owners,
        "sample": fixture.sample("s1"),
        "scan_number": 42,
        "precursor_mz": PRECURSOR_MZ,
        "product_mz": PRECURSOR_MZ - NEUTRAL_LOSS_DA,
        "configured_neutral_loss_da": NEUTRAL_LOSS_DA,
        "observed_neutral_loss_da": NEUTRAL_LOSS_DA,
        "observed_loss_error_ppm": 0.0,
        "scan_rt_min": 8.0,
        "product_intensity": 400.0,
        "precursor_mz_basis": "product_plus_neutral_loss",
        "scan_precursor_mz": PRECURSOR_MZ + 0.25,
        "precursor_inference_tolerance_da": 0.25,
    }
    with pytest.raises(CidNlEvidenceContractError, match=message):
        mint_cid_nl_evidence_ref(**(arguments | changed))


def test_s1_to_s5_truth_table_preserves_orthogonal_axes(tmp_path: Path) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)

    by_sample = {
        assessment.content.sample_key: assessment for assessment in assessments
    }

    assert by_sample[
        fixture.sample("s1").identity.sample_key
    ].content.method_result == ("SUPPORT")
    assert by_sample[
        fixture.sample("s2").identity.sample_key
    ].content.method_result == ("HOLD")
    assert by_sample[
        fixture.sample("s3").identity.sample_key
    ].content.method_result == ("REJECT")
    assert by_sample[
        fixture.sample("s4").identity.sample_key
    ].content.method_result == ("HOLD")
    s5 = by_sample[fixture.sample("s5").identity.sample_key]
    assert s5.content.technical_state == "unsupported"
    assert s5.content.method_result is None
    assert all(not hasattr(assessment, "peak_hypothesis") for assessment in assessments)


def test_set_relation_is_order_neutral_and_never_votes_twice(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("split",)
    )
    left = cid_nl_peak(
        fixture,
        "split",
        apex_rt=7.9,
        rt_left_min=7.8,
        rt_right_min=8.0,
    )
    right = cid_nl_peak(
        fixture,
        "split",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    evidence = cid_nl_evidence(
        fixture,
        "split",
        scan_number=44,
        scan_rt_min=8.0,
    )

    forward = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("split"),
        canonical_evidence=evidence,
        candidate_peaks=(left, right),
        policy=cid_nl_support_policy(),
    )
    reverse = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("split"),
        canonical_evidence=evidence,
        candidate_peaks=(right, left),
        policy=cid_nl_support_policy(),
    )

    assert forward == reverse
    assert forward.content.relationship == "supports_one_or_more_of"
    assert set(forward.content.matched_candidates) == {left.content, right.content}
    assert not hasattr(forward, "individual_support_votes")


def test_zero_buffer_boundary_is_set_level_hold(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("edge",))
    peak = cid_nl_peak(fixture, "edge")
    evidence = cid_nl_evidence(
        fixture,
        "edge",
        scan_number=45,
        scan_rt_min=peak.content.integration.rt_left_min,
    )
    relation = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("edge"),
        canonical_evidence=evidence,
        candidate_peaks=(peak,),
        policy=cid_nl_support_policy(),
    )
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("edge"),
        target=fixture.target,
        detector_invocation_id="gaussian15:edge",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(peak,),
        evidence_refs=(evidence,),
        set_relation_refs=(relation,),
    )

    assert relation.content.matched_candidates == (peak.content,)
    assert assessment.content.method_result == "HOLD"
    assert assessment.individual_support_refs == ()


@pytest.mark.parametrize(
    ("right_start", "scan_rt", "expected_result"),
    (
        pytest.param(8.0, 7.9, "SUPPORT", id="adjacent-interior"),
        pytest.param(8.0, 8.0, "HOLD", id="shared-endpoint"),
        pytest.param(7.95, 7.9, "HOLD", id="positive-overlap-outside-scan"),
    ),
)
@pytest.mark.parametrize("retain_relations", [False, True])
def test_assessment_distinguishes_adjacent_interior_from_boundary_and_overlap(
    tmp_path: Path,
    monkeypatch,
    right_start: float,
    scan_rt: float,
    expected_result: str,
    retain_relations: bool,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("adjacent",)
    )
    sample = fixture.sample("adjacent")
    left = cid_nl_peak(
        fixture, "adjacent", apex_rt=7.9, rt_left_min=7.8, rt_right_min=8.0
    )
    right = cid_nl_peak(
        fixture,
        "adjacent",
        apex_rt=right_start + 0.1,
        rt_left_min=right_start,
        rt_right_min=right_start + 0.2,
    )
    evidence = cid_nl_evidence(fixture, "adjacent", scan_number=46, scan_rt_min=scan_rt)
    for peaks in ((left, right), (right, left)):
        individual_refs = ()
        set_refs = ()
        if expected_result == "SUPPORT":
            individual_refs = (
                cid_nl_unique_support(
                    fixture,
                    "adjacent",
                    peak=left,
                    evidence=evidence,
                    candidate_peaks=peaks,
                ),
            )
        else:
            set_refs = (
                resolve_dda_cid_nl_candidate_set_relation_ref(
                    processing_owners=fixture.owners,
                    sample=sample,
                    canonical_evidence=evidence,
                    candidate_peaks=peaks,
                    policy=cid_nl_support_policy(),
                ),
            )
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id="adjacent-peaks",
            evaluation_receipt=_completed_receipt(),
            candidate_peaks=peaks,
            evidence_refs=(evidence,),
            individual_support_refs=individual_refs,
            set_relation_refs=set_refs,
        )
        relations = None
        if retain_relations:
            relations = assessment_module.evaluate_cid_nl_method_relations(
                candidate_intervals=tuple(
                    assessment_module.CidNlCandidateInterval(
                        peak.content.mz,
                        peak.content.integration.rt_left_min,
                        peak.content.integration.rt_right_min,
                    )
                    for peak in peaks
                ),
                event_queries=(
                    assessment_module.CidNlEventQuery(
                        evidence.observation.assess_precursor_loss(
                            cid_nl_support_policy().neutral_loss_tolerance_ppm
                        ),
                        evidence.observation.scan_rt_min,
                        evidence.observation.support_rt_min,
                    ),
                ),
                policy=cid_nl_support_policy(),
            )
        with monkeypatch.context() as binding:
            if retain_relations:
                binding.setattr(
                    assessment_module,
                    "evaluate_cid_nl_method_relations",
                    lambda **_kwargs: pytest.fail(
                        "artifact binding must reuse the relation answer"
                    ),
                )
            resolved_individual, resolved_sets = (
                assessment_module.resolve_cid_nl_method_relations(
                    processing_owners=fixture.owners,
                    sample=sample,
                    evidence_refs=(evidence,),
                    candidate_peaks=peaks,
                    policy=cid_nl_support_policy(),
                    relations=relations,
                )
            )
        assert resolved_individual == individual_refs
        assert resolved_sets == set_refs
        resolved_assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id="adjacent-peaks",
            evaluation_receipt=_completed_receipt(),
            candidate_peaks=peaks,
            evidence_refs=(evidence,),
            individual_support_refs=resolved_individual,
            set_relation_refs=resolved_sets,
        )
        assert resolved_assessment == assessment
        assert assessment.content.method_result == expected_result
        if expected_result == "SUPPORT":
            assert individual_refs[0].content.peak_observation == left.content
            with pytest.raises(
                DdaCidNlCandidateSetRelationContractError, match="individual support"
            ):
                resolve_dda_cid_nl_candidate_set_relation_ref(
                    processing_owners=fixture.owners,
                    sample=sample,
                    canonical_evidence=evidence,
                    candidate_peaks=peaks,
                    policy=cid_nl_support_policy(),
                )
        else:
            assert assessment.individual_support_refs == ()
            assert set(set_refs[0].content.matched_candidates) == {
                peak.content for peak in peaks
            }


@pytest.mark.parametrize(
    "case",
    ["empty", "no_evidence", "no_candidates", "outside_interval", "outside_mass"],
)
def test_method_relation_resolution_preserves_unrelated_and_empty_facts(
    tmp_path: Path, case: str
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("unrelated",)
    )
    peaks = (
        cid_nl_peak(
            fixture,
            "unrelated",
            mz=PRECURSOR_MZ + (0.02 if case == "outside_mass" else 0.0),
        ),
    )
    evidence = (
        cid_nl_evidence(
            fixture,
            "unrelated",
            scan_number=47,
            scan_rt_min=8.25 if case == "outside_interval" else 8.0,
        ),
    )
    if case in {"empty", "no_evidence"}:
        evidence = ()
    if case in {"empty", "no_candidates"}:
        peaks = ()
    assert assessment_module.resolve_cid_nl_method_relations(
        processing_owners=fixture.owners,
        sample=fixture.sample("unrelated"),
        evidence_refs=evidence,
        candidate_peaks=peaks,
        policy=cid_nl_support_policy(),
    ) == ((), ())


def test_assessment_requires_qualifying_evidence_before_reject(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("nonqual",)
    )
    sample = fixture.sample("nonqual")
    target = fixture.target.definition.target
    assert target.neutral_loss_da is not None
    observed_loss = target.neutral_loss_da + 1.0
    evidence = mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=sample,
        scan_number=74,
        precursor_mz=target.mz,
        product_mz=target.mz - observed_loss,
        configured_neutral_loss_da=target.neutral_loss_da,
        observed_neutral_loss_da=observed_loss,
        observed_loss_error_ppm=(
            abs(observed_loss - target.neutral_loss_da)
            / target.neutral_loss_da
            * 1_000_000.0
        ),
        scan_rt_min=8.0,
        product_intensity=400.0,
    )

    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id="gaussian15:nonqual",
        evaluation_receipt=_completed_receipt(),
        evidence_refs=(evidence,),
    )

    assert assessment.content.method_result == "HOLD"
    assert assessment.content.reason_codes == ("insufficient_method_evidence",)


def test_assessment_preserves_mixed_individual_and_set_scan_relations(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("mixed",)
    )
    sample = fixture.sample("mixed")
    peak = cid_nl_peak(fixture, "mixed", apex_rt=7.9, rt_left_min=7.8, rt_right_min=8.0)
    adjacent = cid_nl_peak(
        fixture, "mixed", apex_rt=8.1, rt_left_min=8.0, rt_right_min=8.2
    )
    peaks = (peak, adjacent)
    interior = cid_nl_evidence(
        fixture,
        "mixed",
        scan_number=75,
        scan_rt_min=7.9,
    )
    boundary = cid_nl_evidence(
        fixture,
        "mixed",
        scan_number=76,
        scan_rt_min=peak.content.integration.rt_right_min,
    )
    individual = cid_nl_unique_support(
        fixture,
        "mixed",
        peak=peak,
        evidence=interior,
        candidate_peaks=peaks,
    )
    set_relation = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        canonical_evidence=boundary,
        candidate_peaks=peaks,
        policy=cid_nl_support_policy(),
    )

    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id="gaussian15:mixed",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=peaks,
        evidence_refs=(interior, boundary),
        individual_support_refs=(individual,),
        set_relation_refs=(set_relation,),
    )

    assert assessment.content.method_result == "SUPPORT"
    assert assessment.individual_support_refs == (individual,)
    assert assessment.set_relation_refs == (set_relation,)
    assert assessment_module.resolve_cid_nl_method_relations(
        processing_owners=fixture.owners,
        sample=sample,
        evidence_refs=(boundary, interior),
        candidate_peaks=peaks,
        policy=cid_nl_support_policy(),
    ) == ((individual,), (set_relation,))
    assert set(set_relation.content.matched_candidates) == {
        item.content for item in peaks
    }


def test_cell_identity_binds_full_provisional_group_membership(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("a", "b")
    )
    sample_assessments = []
    for scan_number, stem in ((77, "a"), (78, "b")):
        sample = fixture.sample(stem)
        peak = cid_nl_peak(fixture, stem)
        evidence = cid_nl_evidence(
            fixture,
            stem,
            scan_number=scan_number,
            scan_rt_min=8.0,
        )
        support = cid_nl_unique_support(
            fixture,
            stem,
            peak=peak,
            evidence=evidence,
        )
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id=f"gaussian15:{stem}:group-membership",
            evaluation_receipt=_completed_receipt(),
            candidate_peaks=(peak,),
            evidence_refs=(evidence,),
            individual_support_refs=(support,),
        )
        sample_assessments.append((sample, assessment))

    config = AlignmentConfig(anchor_min_seed_events=1)
    single = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=(sample_assessments[0],),
        config=config,
    )
    combined = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(sample_assessments),
        config=config,
    )
    assert single.group is not None
    assert combined.group is not None
    combined_a = next(
        cell
        for cell in combined.cells
        if cell.content.sample_key == sample_assessments[0][0].identity.sample_key
    )

    assert single.group.group_hypothesis_id != combined.group.group_hypothesis_id
    assert single.group.owner_ids != combined.group.owner_ids
    assert single.cells[0].content != combined_a.content
    assert single.cells[0].content.group_owner_ids == tuple(
        sorted(single.group.owner_ids)
    )
    assert combined_a.content.group_owner_ids == tuple(sorted(combined.group.owner_ids))


def test_non_support_cell_rejects_group_from_another_target(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("support", "hold"),
    )
    support_assessment = _support_assessment(
        fixture,
        "support",
        scan_losses=((79, 0.0),),
    )
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((fixture.sample("support"), support_assessment),),
        config=AlignmentConfig(anchor_min_seed_events=1),
    )
    assert spine.group is not None
    hold_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("hold"),
        target=fixture.other_target,
        detector_invocation_id="gaussian15:hold:other-target",
        evaluation_receipt=_completed_receipt(),
    )

    with pytest.raises(CellObservationContractError, match="group recovery subject"):
        mint_cell_observation_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("hold"),
            target=fixture.other_target,
            group_context=spine.cells[0].group_context,
            method_assessment=hold_assessment,
        )


def test_reorder_is_neutral_and_multiple_scans_form_one_support_hypothesis(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("multi",)
    )
    peak = cid_nl_peak(fixture, "multi")
    first_evidence = cid_nl_evidence(
        fixture,
        "multi",
        scan_number=50,
        scan_rt_min=7.99,
    )
    second_evidence = cid_nl_evidence(
        fixture,
        "multi",
        scan_number=51,
        scan_rt_min=8.01,
    )
    first_support = cid_nl_unique_support(
        fixture,
        "multi",
        peak=peak,
        evidence=first_evidence,
    )
    second_support = cid_nl_unique_support(
        fixture,
        "multi",
        peak=peak,
        evidence=second_evidence,
    )
    kwargs = {
        "processing_owners": fixture.owners,
        "sample": fixture.sample("multi"),
        "target": fixture.target,
        "detector_invocation_id": "gaussian15:multi",
        "evaluation_receipt": _completed_receipt(),
        "candidate_peaks": (peak,),
    }
    forward = mint_cid_nl_method_assessment_ref(
        **kwargs,
        evidence_refs=(first_evidence, second_evidence),
        individual_support_refs=(first_support, second_support),
    )
    reverse = mint_cid_nl_method_assessment_ref(
        **kwargs,
        evidence_refs=(second_evidence, first_evidence),
        individual_support_refs=(second_support, first_support),
    )

    assert forward == reverse
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((fixture.sample("multi"), forward),),
    )
    assert len(spine.support_hypotheses) == 1
    support = spine.support_hypotheses[0].evidence.cid_nl_chromatographic_support_ref
    assert support is not None
    assert support.content.strict_nl_scan_count == 2


def test_multi_scan_owner_projection_is_order_neutral_across_samples(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("a", "b")
    )
    first = _support_assessment(
        fixture,
        "a",
        scan_losses=((80, -15.0), (81, 15.0)),
    )
    reverse = _support_assessment(
        fixture,
        "b",
        scan_losses=((90, 15.0), (91, -15.0)),
    )

    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=(
            (fixture.sample("a"), first),
            (fixture.sample("b"), reverse),
        ),
        config=AlignmentConfig(anchor_min_seed_events=1),
    )

    assert spine.group is not None
    assert len(spine.group.owner_ids) == 2
    aggregate_losses = tuple(
        owner.primary_identity_event.observed_neutral_loss_da
        for owner in spine.group.owners
    )
    assert aggregate_losses[0] == aggregate_losses[1]
    assert aggregate_losses[0] == pytest.approx(NEUTRAL_LOSS_DA)


def test_duplicate_candidates_fail_closed(tmp_path: Path) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample",)
    )
    left = cid_nl_peak(
        fixture,
        "sample",
        apex_rt=7.9,
        rt_left_min=7.8,
        rt_right_min=8.0,
    )
    evidence = cid_nl_evidence(
        fixture,
        "sample",
        scan_number=52,
        scan_rt_min=8.0,
    )
    with pytest.raises(
        DdaCidNlCandidateSetRelationContractError,
        match="unique",
    ):
        resolve_dda_cid_nl_candidate_set_relation_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample"),
            canonical_evidence=evidence,
            candidate_peaks=(left, left),
            policy=cid_nl_support_policy(),
        )


def test_failed_attempt_cannot_become_reject_or_human_unassessable(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("failed",)
    )
    assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("failed"),
        target=fixture.target,
        detector_invocation_id="gaussian15:failed",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=True,
            search_completed=False,
            reason_code="detector_worker_failed",
        ),
        technical_state="failed",
    )

    assert assessment.content.method_result is None
    assert assessment.content.reason_codes == ("detector_worker_failed",)
    assert "unassessable" not in repr(assessment).lower()


def test_unique_case_rejects_set_relation_and_split_case_rejects_individual_vote(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("sample",)
    )
    peak = cid_nl_peak(fixture, "sample")
    evidence = cid_nl_evidence(
        fixture,
        "sample",
        scan_number=49,
        scan_rt_min=8.0,
    )
    with pytest.raises(
        DdaCidNlCandidateSetRelationContractError,
        match="individual support",
    ):
        resolve_dda_cid_nl_candidate_set_relation_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample"),
            canonical_evidence=evidence,
            candidate_peaks=(peak,),
            policy=cid_nl_support_policy(),
        )

    right = cid_nl_peak(
        fixture,
        "sample",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    with pytest.raises(DdaCidNlPeakSupportContractError, match="ambiguous"):
        cid_nl_unique_support(
            fixture,
            "sample",
            peak=peak,
            evidence=evidence,
            candidate_peaks=(peak, right),
        )


def _measured_evidence(
    fixture,
    sample_stem: str,
    *,
    scan_number: int,
    loss_error_ppm: float,
):
    observed_loss = NEUTRAL_LOSS_DA * (1.0 + loss_error_ppm / 1_000_000.0)
    return mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        scan_number=scan_number,
        precursor_mz=PRECURSOR_MZ,
        product_mz=PRECURSOR_MZ - observed_loss,
        configured_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=observed_loss,
        observed_loss_error_ppm=abs(loss_error_ppm),
        scan_rt_min=8.0,
        product_intensity=400.0,
    )


def _support_assessment(
    fixture,
    sample_stem: str,
    *,
    scan_losses: tuple[tuple[int, float], ...],
):
    peak = cid_nl_peak(fixture, sample_stem)
    evidence_refs = tuple(
        _measured_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number,
            loss_error_ppm=loss_error_ppm,
        )
        for scan_number, loss_error_ppm in scan_losses
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
        detector_invocation_id=f"gaussian15:{sample_stem}:aggregate",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(peak,),
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )


def test_monoisotopic_canonical_evidence_binds_vendor_metadata(tmp_path):
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("s1",))
    direct = cid_nl_evidence(fixture, "s1", scan_number=42, scan_rt_min=8.0)
    observed = direct.observation
    metadata = Ms2PrecursorMetadata(
        monoisotopic_mz=observed.precursor_mz,
        charge=1,
        isolation_width_da=1.6,
        isolation_offset_da=0.2,
        master_scan_number=40,
    )
    mono = mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        **{
            field.name: getattr(observed, field.name)
            for field in fields(observed)
            if field.name
            not in {
                "sample_key",
                "acquisition_source_id",
                "acquisition_mode",
                "precursor_mz_basis",
                "scan_precursor_mz",
                "precursor_metadata",
            }
        },
        precursor_mz_basis="scan_monoisotopic",
        scan_precursor_mz=observed.precursor_mz + 1.0035,
        precursor_metadata=metadata,
    )
    assert mono.observation.precursor_metadata is not None
    assert mono.observation.precursor_metadata.master_scan_number == 40
    assert mono.observation.precursor_mz_basis == "scan_monoisotopic"
    assert mono.observation.scan_precursor_mz != mono.observation.precursor_mz
    with pytest.raises(CidNlEvidenceContractError, match="recorded precursor"):
        replace(mono.observation, precursor_metadata=None)
    with pytest.raises(CidNlEvidenceContractError, match="singly charged"):
        replace(mono.observation, precursor_metadata=replace(metadata, charge=2))
    # A product-derived claim still obeys physical isolation bounds.
    with pytest.raises(CidNlEvidenceContractError, match="inference tolerance"):
        replace(
            mono.observation,
            precursor_mz_basis="product_plus_neutral_loss",
            precursor_inference_tolerance_da=1.2,
        )


def test_cell_rejects_hypothesis_from_a_different_assessment_relation_set(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("cross",)
    )
    sample = fixture.sample("cross")
    peak = cid_nl_peak(fixture, "cross")
    first_evidence = cid_nl_evidence(
        fixture,
        "cross",
        scan_number=61,
        scan_rt_min=7.99,
    )
    second_evidence = cid_nl_evidence(
        fixture,
        "cross",
        scan_number=62,
        scan_rt_min=8.01,
    )
    first_support = cid_nl_unique_support(
        fixture,
        "cross",
        peak=peak,
        evidence=first_evidence,
    )
    second_support = cid_nl_unique_support(
        fixture,
        "cross",
        peak=peak,
        evidence=second_evidence,
    )
    common = {
        "processing_owners": fixture.owners,
        "sample": sample,
        "target": fixture.target,
        "evaluation_receipt": _completed_receipt(),
        "candidate_peaks": (peak,),
    }
    first_assessment = mint_cid_nl_method_assessment_ref(
        **common,
        detector_invocation_id="gaussian15:cross:first",
        evidence_refs=(first_evidence,),
        individual_support_refs=(first_support,),
    )
    second_assessment = mint_cid_nl_method_assessment_ref(
        **common,
        detector_invocation_id="gaussian15:cross:second",
        evidence_refs=(second_evidence,),
        individual_support_refs=(second_support,),
    )
    second_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((sample, second_assessment),),
    )
    assert second_spine.group is not None

    with pytest.raises(CellObservationContractError, match="relations"):
        mint_cell_observation_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            group_context=second_spine.cells[0].group_context,
            method_assessment=first_assessment,
            peak_observation=peak,
            peak_hypothesis=second_spine.support_hypotheses[0],
        )
