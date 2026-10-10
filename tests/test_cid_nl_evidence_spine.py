from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path

import pytest

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
from xic_extractor.alignment import cid_nl_evidence_spine as spine_module
from xic_extractor.alignment.cell_observations import (
    CellObservationContractError,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    CidNlEvidenceSpineError,
    TargetedCellObservationReferenceProjection,
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.cid_nl_evidence_projection import (
    UntargetedCidNlProjectionError,
    mint_untargeted_cid_nl_projection_ref,
)
from xic_extractor.discovery.models import DiscoverySeed
from xic_extractor.peak_detection import cid_nl_method_assessment as assessment_module
from xic_extractor.peak_detection.cid_nl_candidate_set_relation import (
    DdaCidNlCandidateSetRelationContractError,
    resolve_dda_cid_nl_candidate_set_relation_ref,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceContractError,
    canonical_cid_nl_evidence_bytes,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodAssessmentContractError,
    mint_cid_nl_method_assessment_ref,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportContractError,
)
from xic_extractor.processing_identity import compose_processing_owner_references
from xic_extractor.review_actions.models import ReviewAction
from xic_extractor.review_records import (
    mint_review_record_reference,
    mint_review_record_registry_snapshot,
    resolve_current_review_record,
)


@pytest.mark.parametrize(
    "changes",
    [
        {"query_rt_min": 7.5},
        {"query_rt_max": 8.5},
        {"search_completed": True},
        {"scanned_event_count": 1},
        {"evidence_scope": "subject_window"},
    ],
)
def test_absent_rt_receipt_is_only_valid_for_unattempted_declared_search(
    changes,
) -> None:
    values = dict(
        query_rt_min=None,
        query_rt_max=None,
        support_policy=cid_nl_support_policy(),
        acquisition_supported=True,
        search_completed=False,
        scanned_event_count=0,
        reason_code="no_search_eligible_discovery_input",
        evidence_scope="declared_scans",
    )
    assert CidNlEvaluationReceipt(**values).query_rt_min is None
    with pytest.raises(
        CidNlMethodAssessmentContractError, match="absent evaluation query"
    ):
        CidNlEvaluationReceipt(**(values | changes))


@pytest.mark.parametrize(
    "width,offset,supported",
    [
        (1.6, 0.0, False),
        (2.4, 0.0, True),
        (None, None, False),
        (2.4, None, False),
        (1.6, -0.5, True),
    ],
)
def test_recorded_monoisotopic_reference_requires_isolation_support(
    tmp_path: Path, width, offset, supported
) -> None:
    from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata

    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("s1",))
    mono = 300.1615
    product = 184.11323547363
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
        scan_precursor_mz=301.16497802734,
        precursor_metadata=Ms2PrecursorMetadata(
            monoisotopic_mz=mono,
            charge=1,
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
    assert bool(individual) is supported
    assert not sets
    payload = json.loads(canonical_cid_nl_evidence_bytes(evidence.observation))
    assert payload["precursor_isolation_membership"] == (
        "unknown"
        if width is None or offset is None
        else "inside_nominal"
        if supported
        else "outside_nominal"
    )
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
def test_parent_ms1_measurement_qualifies_inference_without_rewriting_origin(
    tmp_path, centroids, selected, width, offset, parent_rt, status, supported
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
    policy = cid_nl_support_policy()
    verdict = evidence.observation.assess_precursor_loss(
        policy.neutral_loss_tolerance_ppm
    )
    assert verdict.status == status
    individual, sets = assessment_module.resolve_cid_nl_method_relations(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        evidence_refs=(evidence,),
        candidate_peaks=(cid_nl_peak(fixture, "s1"),),
        policy=policy,
    )
    assert bool(individual) is supported
    assert not sets
    payload = json.loads(canonical_cid_nl_evidence_bytes(evidence.observation))
    assert payload["precursor_mz_basis"] == "product_plus_neutral_loss"
    assert payload["precursor_mz"] == 500.0
    assert payload["observed_loss_error_ppm"] is None
    assert payload["ms1_precursor_observation"]["centroid_mzs"] == list(centroids)
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
    payload = json.loads(canonical_cid_nl_evidence_bytes(inferred.observation))
    assert payload["schema_version"] == "cid_nl_evidence_observation"
    assert payload["precursor_mz_basis"] == "product_plus_neutral_loss"
    assert payload["observed_loss_error_ppm"] is None
    assert payload["supports_direct_nl"] is False
    assert assessment_module.resolve_cid_nl_method_relations(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        evidence_refs=(inferred,),
        candidate_peaks=(cid_nl_peak(fixture, "s1"),),
        policy=cid_nl_support_policy(),
    ) == ((), ())
    with pytest.raises(CidNlEvidenceContractError, match="no independent"):
        replace(inferred.observation, observed_loss_error_ppm=0.0)
    assert payload["scan_precursor_mz"] == observation.precursor_mz + 0.25
    assert payload["precursor_inference_tolerance_da"] == 0.25
    assert direct.observation.precursor_mz_basis == "scan_precursor"
    assert direct.observation.scan_precursor_mz == observation.precursor_mz
    assert direct.observation.precursor_inference_tolerance_da is None
    assert (
        inferred.artifact.evidence_artifact_id != direct.artifact.evidence_artifact_id
    )
    with pytest.raises(CidNlEvidenceContractError, match="mismatches"):
        replace(
            inferred,
            observation=replace(
                inferred.observation, precursor_inference_tolerance_da=0.5
            ),
        )

    seed = DiscoverySeed(
        raw_file=Path("s1.raw"),
        sample_stem="s1",
        scan_number=observation.scan_number,
        rt=observation.scan_rt_min,
        precursor_mz=observation.precursor_mz,
        product_mz=observation.product_mz,
        product_intensity=observation.product_intensity,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=observation.configured_neutral_loss_da,
        observed_neutral_loss_da=observation.observed_neutral_loss_da,
        observed_loss_error_ppm=None,
        precursor_mz_basis="product_plus_neutral_loss",
        scan_precursor_mz=inferred.observation.scan_precursor_mz,
        acquisition_source_id=observation.acquisition_source_id,
    )
    projection = mint_untargeted_cid_nl_projection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        canonical_evidence=inferred,
        discovery_seed=seed,
    )
    assert projection.canonical_evidence_artifact_id == (
        inferred.artifact.evidence_artifact_id
    )
    for changed_seed, message in (
        (replace(seed, precursor_mz_basis="scan_precursor"), "precursor_mz_basis"),
        (
            replace(seed, scan_precursor_mz=observation.precursor_mz),
            "scan_precursor_mz",
        ),
    ):
        with pytest.raises(UntargetedCidNlProjectionError, match=message):
            mint_untargeted_cid_nl_projection_ref(
                processing_owners=fixture.owners,
                sample=fixture.sample("s1"),
                canonical_evidence=inferred,
                discovery_seed=changed_seed,
            )


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


def test_s1_to_s5_truth_table_preserves_orthogonal_axes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)

    def unexpected_reevaluation(**kwargs: object) -> None:
        pytest.fail("assessment handoff must reuse the completed method decision")

    monkeypatch.setattr(assessment_module, "_method_result", unexpected_reevaluation)
    by_sample = {
        assessment.content.canonical_sample_identity_id: assessment
        for assessment in assessments
    }

    assert by_sample[
        fixture.sample("s1").identity.sample_identity_id
    ].content.method_result == ("SUPPORT")
    assert by_sample[
        fixture.sample("s2").identity.sample_identity_id
    ].content.method_result == ("HOLD")
    assert by_sample[
        fixture.sample("s3").identity.sample_identity_id
    ].content.method_result == ("REJECT")
    assert by_sample[
        fixture.sample("s4").identity.sample_identity_id
    ].content.method_result == ("HOLD")
    s5 = by_sample[fixture.sample("s5").identity.sample_identity_id]
    assert s5.content.technical_state == "unsupported"
    assert s5.content.method_result is None
    assert all(not hasattr(assessment, "peak_hypothesis") for assessment in assessments)

    for assessment in assessments:
        validate_cid_nl_method_assessment_binding(
            assessment,
            processing_owners=fixture.owners,
            sample=next(
                sample
                for sample in fixture.samples
                if sample.identity.sample_identity_id
                == assessment.content.canonical_sample_identity_id
            ),
            target=fixture.target,
        )

    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(zip(fixture.samples, assessments, strict=True)),
    )
    assert all(
        cell.content.group_target_key_id
        == fixture.target.definition.target_key.target_key_id
        and cell.content.group_target_definition_id
        == fixture.target.definition.target_definition_id
        for cell in spine.cells
    )


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
    assert forward.content.matched_candidate_content_ids == tuple(
        sorted(
            (
                left.content.peak_observation_content_id,
                right.content.peak_observation_content_id,
            )
        )
    )
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
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(peak,),
        evidence_refs=(evidence,),
        set_relation_refs=(relation,),
    )

    assert relation.content.matched_candidate_content_ids == (
        peak.content.peak_observation_content_id,
    )
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
def test_assessment_distinguishes_adjacent_interior_from_boundary_and_overlap(
    tmp_path: Path,
    right_start: float,
    scan_rt: float,
    expected_result: str,
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
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
            candidate_peaks=peaks,
            evidence_refs=(evidence,),
            individual_support_refs=individual_refs,
            set_relation_refs=set_refs,
        )
        resolved_individual, resolved_sets = (
            assessment_module.resolve_cid_nl_method_relations(
                processing_owners=fixture.owners,
                sample=sample,
                evidence_refs=(evidence,),
                candidate_peaks=peaks,
                policy=cid_nl_support_policy(),
            )
        )
        assert resolved_individual == individual_refs
        assert resolved_sets == set_refs
        resolved_assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id="adjacent-peaks",
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
            candidate_peaks=peaks,
            evidence_refs=(evidence,),
            individual_support_refs=resolved_individual,
            set_relation_refs=resolved_sets,
        )
        assert resolved_assessment == assessment
        assert assessment_module.canonical_cid_nl_method_assessment_bytes(
            resolved_assessment.content
        ) == assessment_module.canonical_cid_nl_method_assessment_bytes(
            assessment.content
        )
        assert assessment.content.method_result == expected_result
        if expected_result == "SUPPORT":
            assert individual_refs[0].content.peak_observation_content_id == (
                left.content.peak_observation_content_id
            )
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
            assert set_refs[0].content.matched_candidate_content_ids == tuple(
                sorted(peak.content.peak_observation_content_id for peak in peaks)
            )


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


def test_reference_spine_mints_once_and_fans_out_same_cell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    calls: list[str] = []
    mint_counts = {"hypothesis": 0, "group": 0, "cell": 0}

    def forbidden(*_args, **_kwargs):
        calls.append("writer")
        raise AssertionError("diagnostic evidence spine invoked product output")

    for path in (
        "xic_extractor.extraction.output_dispatch.write_outputs",
        "xic_extractor.output.csv_writers.write_wide_csv",
        "xic_extractor.output.csv_writers.write_long_csv",
        "xic_extractor.output.csv_writers.write_diagnostics_csv",
        "xic_extractor.output.csv_writers.write_score_breakdown_csv",
        "xic_extractor.output.workbook_builder.write_workbook_from_rows",
        "xic_extractor.output.alignment.tsv_writer.write_alignment_matrix_tsv",
        "xic_extractor.output.alignment.product_matrix.build_product_matrix_rows",
        "xic_extractor.output.alignment.pipeline_outputs.write_outputs_atomic",
    ):
        monkeypatch.setattr(path, forbidden)

    original_hypothesis = spine_module.compose_cid_nl_peak_hypothesis
    original_group = spine_module.construct_cross_sample_peak_group_hypotheses
    original_cell = spine_module.mint_cell_observation_ref

    def counting_hypothesis(*args, **kwargs):
        mint_counts["hypothesis"] += 1
        return original_hypothesis(*args, **kwargs)

    def counting_group(*args, **kwargs):
        mint_counts["group"] += 1
        return original_group(*args, **kwargs)

    def counting_cell(*args, **kwargs):
        mint_counts["cell"] += 1
        return original_cell(*args, **kwargs)

    monkeypatch.setattr(
        spine_module, "compose_cid_nl_peak_hypothesis", counting_hypothesis
    )
    monkeypatch.setattr(
        spine_module,
        "construct_cross_sample_peak_group_hypotheses",
        counting_group,
    )
    monkeypatch.setattr(spine_module, "mint_cell_observation_ref", counting_cell)

    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(
            (fixture.sample(sample_stem), assessment)
            for sample_stem, assessment in zip(
                ("s1", "s2", "s3", "s4", "s5"),
                assessments,
                strict=True,
            )
        ),
    )

    assert calls == []
    assert mint_counts == {"hypothesis": 1, "group": 1, "cell": 5}
    assert spine.group is not None
    assert len(spine.support_hypotheses) == 1
    assert len(spine.cells) == 5
    assert len({cell.artifact.evidence_artifact_id for cell in spine.cells}) == 5
    for cell, targeted, untargeted in zip(
        spine.cells,
        spine.targeted_projections,
        spine.untargeted_projections,
        strict=True,
    ):
        assert targeted.cell is cell
        assert untargeted.cell is cell
        assert targeted.group is spine.group
        assert untargeted.group is spine.group
        assert targeted.validation_status == "diagnostic_only"
        assert untargeted.validation_status == "diagnostic_only"
        assert targeted.product_authority == "none"
        assert untargeted.product_authority == "none"

    by_sample = {
        cell.content.canonical_sample_identity_id: cell for cell in spine.cells
    }
    assert by_sample[fixture.sample("s1").identity.sample_identity_id].peak_hypothesis
    assert all(
        by_sample[fixture.sample(stem).identity.sample_identity_id].peak_hypothesis
        is None
        for stem in ("s2", "s3", "s4", "s5")
    )

    forbidden_field_tokens = ("area", "matrix", "writer", "backfill", "counted")
    field_names = {field.name.lower() for field in fields(spine.cells[0].content)}
    assert not any(
        token in field_name
        for field_name in field_names
        for token in forbidden_field_tokens
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
        "evaluation_receipt": _completed_receipt(scanned_event_count=1),
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


def test_reference_spine_and_cell_content_reject_crossed_manual_reassembly(
    tmp_path: Path,
) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(
            (fixture.sample(sample_stem), assessment)
            for sample_stem, assessment in zip(
                ("s1", "s2", "s3", "s4", "s5"),
                assessments,
                strict=True,
            )
        ),
    )

    with pytest.raises(CidNlEvidenceSpineError, match="assessment"):
        replace(spine, assessments=tuple(reversed(spine.assessments)))
    with pytest.raises(CidNlEvidenceSpineError, match="hypotheses"):
        replace(spine, support_hypotheses=())
    with pytest.raises(CellObservationContractError, match="evaluated"):
        replace(
            spine.cells[0].content,
            method_result=None,
            peak_observation_content_id=None,
            peak_hypothesis_id=None,
        )


def test_without_support_assessments_do_not_invent_group_or_cells(
    tmp_path: Path,
) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=(
            (fixture.sample("s2"), assessments[1]),
            (fixture.sample("s3"), assessments[2]),
        ),
    )

    assert spine.group is None
    assert spine.cells == ()
    assert spine.targeted_projections == ()
    assert spine.untargeted_projections == ()


def test_assessment_tamper_and_crossed_sample_fail_closed(tmp_path: Path) -> None:
    fixture, assessments = _truth_table_fixture(tmp_path)
    support = assessments[0]
    with pytest.raises(CidNlMethodAssessmentContractError):
        replace(
            support,
            content=replace(
                support.content,
                canonical_sample_identity_id=(
                    fixture.sample("s2").identity.sample_identity_id
                ),
            ),
        )
    with pytest.raises(CidNlMethodAssessmentContractError, match="sample"):
        validate_cid_nl_method_assessment_binding(
            support,
            processing_owners=fixture.owners,
            sample=fixture.sample("s2"),
            target=fixture.target,
        )


def test_assessment_rejects_target_precursor_and_query_window_substitution(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("bound",)
    )
    sample = fixture.sample("bound")
    wrong_precursor_mz = fixture.target.definition.target.mz + 100.0
    wrong_peak = cid_nl_peak(fixture, "bound", mz=wrong_precursor_mz)
    wrong_evidence = mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=sample,
        scan_number=71,
        precursor_mz=wrong_precursor_mz,
        product_mz=wrong_precursor_mz - NEUTRAL_LOSS_DA,
        configured_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_loss_error_ppm=0.0,
        scan_rt_min=8.0,
        product_intensity=400.0,
    )

    with pytest.raises(CidNlMethodAssessmentContractError, match="target precursor"):
        mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id="gaussian15:bound:wrong-peak",
            evaluation_receipt=_completed_receipt(scanned_event_count=0),
            candidate_peaks=(wrong_peak,),
        )
    with pytest.raises(CidNlMethodAssessmentContractError, match="target precursor"):
        mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id="gaussian15:bound:wrong-evidence",
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
            evidence_refs=(wrong_evidence,),
        )

    late_evidence = cid_nl_evidence(
        fixture,
        "bound",
        scan_number=72,
        scan_rt_min=20.0,
    )
    with pytest.raises(CidNlMethodAssessmentContractError, match="query RT window"):
        mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id="gaussian15:bound:late-evidence",
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
            evidence_refs=(late_evidence,),
        )


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
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
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
        evaluation_receipt=_completed_receipt(scanned_event_count=2),
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
    assert set_relation.content.matched_candidate_content_ids == tuple(
        sorted(item.content.peak_observation_content_id for item in peaks)
    )


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
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
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
        if cell.content.canonical_sample_identity_id
        == sample_assessments[0][0].identity.sample_identity_id
    )

    assert single.group.group_hypothesis_id != combined.group.group_hypothesis_id
    assert single.group.owner_ids != combined.group.owner_ids
    assert single.cells[0].artifact.evidence_artifact_id != (
        combined_a.artifact.evidence_artifact_id
    )
    assert single.cells[0].content.group_owner_ids == tuple(
        sorted(single.group.owner_ids)
    )
    assert combined_a.content.group_owner_ids == tuple(sorted(combined.group.owner_ids))

    substituted_group = replace(
        combined.group,
        group_center_rt=combined.group.group_center_rt + 0.01,
    )
    with pytest.raises(CidNlEvidenceSpineError, match="exact group context"):
        TargetedCellObservationReferenceProjection(
            cell=combined_a,
            group=substituted_group,
        )


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
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
    )

    with pytest.raises(CellObservationContractError, match="group recovery subject"):
        mint_cell_observation_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("hold"),
            target=fixture.other_target,
            group_context=spine.cells[0].group_context,
            method_assessment=hold_assessment,
        )


def test_cell_rejects_human_disposition_from_a_different_sample(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("support", "reviewed", "crossed"),
    )
    support_peak = cid_nl_peak(fixture, "support")
    support_evidence = cid_nl_evidence(
        fixture,
        "support",
        scan_number=73,
        scan_rt_min=8.0,
    )
    support_relation = cid_nl_unique_support(
        fixture,
        "support",
        peak=support_peak,
        evidence=support_evidence,
    )
    support_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("support"),
        target=fixture.target,
        detector_invocation_id="gaussian15:support:review-subject",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(support_peak,),
        evidence_refs=(support_evidence,),
        individual_support_refs=(support_relation,),
    )
    reviewed_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("reviewed"),
        target=fixture.target,
        detector_invocation_id="gaussian15:reviewed:review-subject",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=False,
            search_completed=False,
            scanned_event_count=0,
            reason_code="dda_not_available",
        ),
        technical_state="unsupported",
    )
    replacement_reviewed_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("reviewed"),
        target=fixture.target,
        detector_invocation_id="gaussian15:reviewed:replacement-assessment",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=False,
            search_completed=False,
            scanned_event_count=0,
            reason_code="dda_not_available",
        ),
        technical_state="unsupported",
    )
    crossed_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("crossed"),
        target=fixture.target,
        detector_invocation_id="gaussian15:crossed:review-subject",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=False,
            search_completed=False,
            scanned_event_count=0,
            reason_code="dda_not_available",
        ),
        technical_state="unsupported",
    )
    crossed_target_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("reviewed"),
        target=fixture.other_target,
        detector_invocation_id="gaussian15:reviewed:crossed-target",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=False,
            search_completed=False,
            scanned_event_count=0,
            reason_code="dda_not_available",
        ),
        technical_state="unsupported",
    )
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=(
            (fixture.sample("support"), support_assessment),
            (fixture.sample("reviewed"), reviewed_assessment),
            (fixture.sample("crossed"), crossed_assessment),
        ),
    )
    assert spine.group is not None

    action = ReviewAction(
        sample_name=fixture.sample("reviewed").metadata.sample_name,
        target_label=fixture.target.definition.target.label,
        action_type="mark_unresolved",
        comment="reviewed sample remains unresolved",
        reviewer="analyst",
        reviewed_at="2026-08-02T12:00:00Z",
    )
    review_reference = mint_review_record_reference(
        record=action,
        sample=fixture.sample("reviewed"),
        target=fixture.target,
        processing_request_id=fixture.request.processing_request_id,
        evidence_artifacts=(reviewed_assessment.artifact,),
    )
    review_snapshot = mint_review_record_registry_snapshot((review_reference,))
    review_record = resolve_current_review_record(
        review_snapshot,
        review_record_id=review_reference.review_record_id,
        expected_record=action,
        expected_sample=fixture.sample("reviewed"),
        expected_target=fixture.target,
        expected_processing_request_id=fixture.request.processing_request_id,
        expected_evidence_artifacts=(reviewed_assessment.artifact,),
    )
    review_owners = compose_processing_owner_references(
        request=fixture.request,
        attempt=fixture.attempt,
        sample_snapshot=fixture.samples[0].snapshot,
        sample_resolutions=fixture.samples,
        target_snapshot=fixture.target.snapshot,
        evidence_artifacts=(
            reviewed_assessment.artifact,
            replacement_reviewed_assessment.artifact,
        ),
        review_target=fixture.target,
        review_snapshot=review_snapshot,
        review_record=review_record,
    )

    with pytest.raises(CellObservationContractError, match="different sample"):
        mint_cell_observation_ref(
            processing_owners=review_owners,
            sample=fixture.sample("crossed"),
            target=fixture.target,
            group_context=spine.cells[0].group_context,
            method_assessment=crossed_assessment,
            human_disposition=review_record,
        )
    # Keep the group valid for this target so the human-disposition guard is reached.
    other_fixture = replace(fixture, target=fixture.other_target)
    other_support = _support_assessment(
        other_fixture, "support", scan_losses=((80, 0.0),)
    )
    other_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.other_target,
        sample_assessments=((fixture.sample("support"), other_support),),
        config=AlignmentConfig(anchor_min_seed_events=1),
    )
    with pytest.raises(CellObservationContractError, match="different target"):
        mint_cell_observation_ref(
            processing_owners=review_owners,
            sample=fixture.sample("reviewed"),
            target=fixture.other_target,
            group_context=other_spine.cells[0].group_context,
            method_assessment=crossed_target_assessment,
            human_disposition=review_record,
        )
    with pytest.raises(CellObservationContractError, match="current method assessment"):
        mint_cell_observation_ref(
            processing_owners=review_owners,
            sample=fixture.sample("reviewed"),
            target=fixture.target,
            group_context=spine.cells[0].group_context,
            method_assessment=replacement_reviewed_assessment,
            human_disposition=review_record,
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
        "evaluation_receipt": _completed_receipt(scanned_event_count=2),
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


def test_assessment_rejects_duplicate_acquisition_scan_evidence(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path, sample_stems=("same-scan",)
    )
    peak = cid_nl_peak(fixture, "same-scan")
    first = _measured_evidence(
        fixture,
        "same-scan",
        scan_number=92,
        loss_error_ppm=-5.0,
    )
    second = _measured_evidence(
        fixture,
        "same-scan",
        scan_number=92,
        loss_error_ppm=5.0,
    )
    supports = tuple(
        cid_nl_unique_support(
            fixture,
            "same-scan",
            peak=peak,
            evidence=evidence,
        )
        for evidence in (first, second)
    )

    with pytest.raises(CidNlMethodAssessmentContractError, match="acquisition scan"):
        mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("same-scan"),
            target=fixture.target,
            detector_invocation_id="gaussian15:same-scan",
            evaluation_receipt=_completed_receipt(scanned_event_count=2),
            candidate_peaks=(peak,),
            evidence_refs=(first, second),
            individual_support_refs=supports,
        )


def test_partial_duplicate_and_cross_attempt_inputs_fail_closed(tmp_path: Path) -> None:
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
    right = cid_nl_peak(
        fixture,
        "sample",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    evidence = cid_nl_evidence(
        fixture,
        "sample",
        scan_number=52,
        scan_rt_min=8.0,
    )
    relation = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("sample"),
        canonical_evidence=evidence,
        candidate_peaks=(left, right),
        policy=cid_nl_support_policy(),
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
    with pytest.raises(
        CidNlMethodAssessmentContractError,
        match="different candidate universe",
    ):
        mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("sample"),
            target=fixture.target,
            detector_invocation_id="gaussian15:sample",
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
            candidate_peaks=(left,),
            evidence_refs=(evidence,),
            set_relation_refs=(relation,),
        )

    retry = fixture.retry()
    with pytest.raises(CidNlMethodAssessmentContractError, match="attempt"):
        mint_cid_nl_method_assessment_ref(
            processing_owners=retry.owners,
            sample=retry.sample("sample"),
            target=retry.target,
            detector_invocation_id="gaussian15:sample:retry",
            evaluation_receipt=_completed_receipt(scanned_event_count=1),
            candidate_peaks=(left, right),
            evidence_refs=(evidence,),
            set_relation_refs=(relation,),
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
            scanned_event_count=0,
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


def _truth_table_fixture(tmp_path: Path):
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2", "s3", "s4", "s5"),
    )

    s1_peak = cid_nl_peak(fixture, "s1")
    s1_evidence = cid_nl_evidence(
        fixture,
        "s1",
        scan_number=41,
        scan_rt_min=8.0,
    )
    s1_support = cid_nl_unique_support(
        fixture,
        "s1",
        peak=s1_peak,
        evidence=s1_evidence,
    )
    s1 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s1"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s1",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(s1_peak,),
        evidence_refs=(s1_evidence,),
        individual_support_refs=(s1_support,),
    )

    s2_peak = cid_nl_peak(fixture, "s2")
    s2 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s2"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s2",
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
        candidate_peaks=(s2_peak,),
    )

    s3_evidence = cid_nl_evidence(
        fixture,
        "s3",
        scan_number=43,
        scan_rt_min=8.0,
    )
    s3 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s3"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s3",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        evidence_refs=(s3_evidence,),
    )

    s4_left = cid_nl_peak(
        fixture,
        "s4",
        apex_rt=7.9,
        rt_left_min=7.8,
        rt_right_min=8.0,
    )
    s4_right = cid_nl_peak(
        fixture,
        "s4",
        apex_rt=8.1,
        rt_left_min=8.0,
        rt_right_min=8.2,
    )
    s4_evidence = cid_nl_evidence(
        fixture,
        "s4",
        scan_number=44,
        scan_rt_min=8.0,
    )
    s4_relation = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s4"),
        canonical_evidence=s4_evidence,
        candidate_peaks=(s4_left, s4_right),
        policy=cid_nl_support_policy(),
    )
    s4 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s4"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s4",
        evaluation_receipt=_completed_receipt(scanned_event_count=1),
        candidate_peaks=(s4_left, s4_right),
        evidence_refs=(s4_evidence,),
        set_relation_refs=(s4_relation,),
    )

    s5 = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample("s5"),
        target=fixture.target,
        detector_invocation_id="gaussian15:s5",
        evaluation_receipt=CidNlEvaluationReceipt(
            query_rt_min=7.5,
            query_rt_max=8.6,
            support_policy=cid_nl_support_policy(),
            acquisition_supported=False,
            search_completed=False,
            scanned_event_count=0,
            reason_code="dda_not_available",
        ),
        technical_state="unsupported",
    )
    return fixture, (s1, s2, s3, s4, s5)


@pytest.mark.parametrize("scope_override", ["declared_scans", "physical_query_mz"])
def test_targeted_assessment_retains_its_subject_mass_window(
    tmp_path: Path,
    scope_override: str,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("s1",))
    with pytest.raises(CidNlMethodAssessmentContractError, match="Untargeted"):
        mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("s1"),
            target=fixture.target,
            detector_invocation_id="targeted-scope",
            evaluation_receipt=replace(
                _completed_receipt(scanned_event_count=0),
                evidence_scope=(
                    "declared_scans"
                    if scope_override == "declared_scans"
                    else "subject_window"
                ),
                physical_query_mz=(
                    500.012 if scope_override == "physical_query_mz" else None
                ),
            ),
        )


def _completed_receipt(*, scanned_event_count: int) -> CidNlEvaluationReceipt:
    return CidNlEvaluationReceipt(
        query_rt_min=7.5,
        query_rt_max=8.6,
        support_policy=cid_nl_support_policy(),
        acquisition_supported=True,
        search_completed=True,
        scanned_event_count=scanned_event_count,
        reason_code=None,
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
        evaluation_receipt=_completed_receipt(scanned_event_count=len(evidence_refs)),
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
                "canonical_sample_identity_id",
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
    payload = json.loads(canonical_cid_nl_evidence_bytes(mono.observation))
    assert payload["precursor_metadata"]["master_scan_number"] == 40
    assert payload["precursor_mz_basis"] == "scan_monoisotopic"
    assert payload["scan_precursor_mz"] != payload["precursor_mz"]
    with pytest.raises(CidNlEvidenceContractError, match="recorded precursor"):
        replace(mono.observation, precursor_metadata=None)
    with pytest.raises(CidNlEvidenceContractError, match="singly charged"):
        replace(mono.observation, precursor_metadata=replace(metadata, charge=2))
    with pytest.raises(CidNlEvidenceContractError, match="mismatches"):
        replace(
            mono,
            observation=replace(
                mono.observation,
                precursor_metadata=replace(metadata, master_scan_number=39),
            ),
        )
    # A product-derived claim still obeys physical isolation bounds.
    with pytest.raises(CidNlEvidenceContractError, match="inference tolerance"):
        replace(
            mono.observation,
            precursor_mz_basis="product_plus_neutral_loss",
            precursor_inference_tolerance_da=1.2,
        )
