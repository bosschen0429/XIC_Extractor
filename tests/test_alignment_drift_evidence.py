from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from xic_extractor.alignment.drift_evidence import (
    DriftEvidenceLookup,
    LocalFeatureLandmarkEvidence,
    LocalTrajectoryEvidenceLookup,
    SampleDriftEvidence,
    TargetedIstdIdentityAnchor,
    assess_local_trajectory,
    build_targeted_istd_identity_anchor,
    read_targeted_istd_drift_evidence,
    targeted_istd_identity_anchor_payload,
)
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import mint_target_list_snapshot


class _ForgedTargetedIstdIdentityAnchor(TargetedIstdIdentityAnchor):
    def __post_init__(self) -> None:
        pass


def test_targeted_istd_payload_rejects_forged_anchor_subclass() -> None:
    forged = _ForgedTargetedIstdIdentityAnchor(
        target_snapshot=object(),  # type: ignore[arg-type]
        observed_labels=(),
    )

    with pytest.raises(TypeError, match="owner-minted"):
        targeted_istd_identity_anchor_payload(forged)


def test_lookup_returns_median_sample_delta_and_injection_order() -> None:
    lookup = DriftEvidenceLookup(
        points=(
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=7,
                trend_id="trend-001",
                istd_rt_min=1.9,
                local_trend_rt_min=2.1,
                rt_drift_delta_min=-0.2,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=7,
                trend_id="trend-002",
                istd_rt_min=2.0,
                local_trend_rt_min=2.0,
                rt_drift_delta_min=0.0,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-b",
                injection_order=10,
                trend_id="trend-001",
                istd_rt_min=2.1,
                local_trend_rt_min=2.0,
                rt_drift_delta_min=0.1,
                source="batch_istd_trend",
            ),
        )
    )

    assert lookup.source == "targeted_istd_trend"
    assert lookup.sample_delta_min("sample-a") == -0.1
    assert lookup.sample_delta_min("sample-b") == 0.1
    assert lookup.sample_delta_min("missing") is None
    assert lookup.injection_order("sample-a") == 7
    assert lookup.injection_order("missing") is None


def test_lookup_rejects_conflicting_injection_orders_for_sample() -> None:
    lookup = DriftEvidenceLookup(
        points=(
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=7,
                trend_id="trend-001",
                istd_rt_min=1.9,
                local_trend_rt_min=2.1,
                rt_drift_delta_min=-0.2,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=8,
                trend_id="trend-002",
                istd_rt_min=2.0,
                local_trend_rt_min=2.0,
                rt_drift_delta_min=0.0,
                source="targeted_istd_trend",
            ),
        )
    )

    with pytest.raises(ValueError, match="conflicting injection order"):
        lookup.injection_order("sample-a")


def test_local_landmarks_retain_full_trace_and_explicit_quality() -> None:
    lookup = LocalTrajectoryEvidenceLookup(
        points=(
            LocalFeatureLandmarkEvidence(
                sample_stem="sample-a",
                injection_order=1,
                acquisition_source_id="acquisition-source:sample-a",
                processing_request_id="processing-request:current",
                processing_attempt_id="processing-attempt:current",
                landmark_id="landmark-001",
                observed_rt_min=4.10,
                local_trajectory_rt_min=4.00,
                rt_drift_delta_min=0.10,
                quality="accepted",
                evidence_ref="peak-observation:a1",
            ),
            LocalFeatureLandmarkEvidence(
                sample_stem="sample-a",
                injection_order=1,
                acquisition_source_id="acquisition-source:sample-a",
                processing_request_id="processing-request:current",
                processing_attempt_id="processing-attempt:current",
                landmark_id="landmark-002",
                observed_rt_min=8.30,
                local_trajectory_rt_min=8.10,
                rt_drift_delta_min=0.20,
                quality="accepted",
                evidence_ref="peak-observation:a2",
            ),
            LocalFeatureLandmarkEvidence(
                sample_stem="sample-b",
                injection_order=2,
                acquisition_source_id="acquisition-source:sample-b",
                processing_request_id="processing-request:current",
                processing_attempt_id="processing-attempt:current",
                landmark_id="landmark-001",
                observed_rt_min=4.00,
                local_trajectory_rt_min=4.00,
                rt_drift_delta_min=0.00,
                quality="poor",
                evidence_ref="peak-observation:b1",
                exclusion_reason="shape_unstable",
            ),
        )
    )

    assert not hasattr(lookup, "local_delta_min")
    assert not hasattr(lookup, "source")
    assert not hasattr(lookup, "sample_delta_min")
    assert not hasattr(lookup, "injection_order")
    assert lookup.points[0].landmark_id == "landmark-001"
    assert lookup.points[1].landmark_id == "landmark-002"
    sample_a = assess_local_trajectory(lookup, ("sample-a",))
    assert sample_a.status == "assessable"
    assert sample_a.landmark_points == lookup.points[:2]
    assert assess_local_trajectory(lookup, ("sample-b",)).status == "poor_landmarks"
    assert assess_local_trajectory(lookup, ("sample-c",)).status == (
        "missing_landmarks"
    )


def test_local_trajectory_conflict_requires_explicit_evidence_refs() -> None:
    point = LocalFeatureLandmarkEvidence(
        sample_stem="sample-a",
        injection_order=1,
        acquisition_source_id="acquisition-source:sample-a",
        processing_request_id="processing-request:current",
        processing_attempt_id="processing-attempt:current",
        landmark_id="landmark-001",
        observed_rt_min=4.10,
        local_trajectory_rt_min=4.00,
        rt_drift_delta_min=0.10,
        quality="accepted",
        evidence_ref="peak-observation:a1",
    )
    lookup = LocalTrajectoryEvidenceLookup(points=(point,))

    assessment = assess_local_trajectory(
        lookup,
        ("sample-a",),
        conflict_evidence_refs=(point.evidence_ref,),
    )
    assert assessment.status == "conflict"
    assert assessment.landmark_evidence_refs == (point.evidence_ref,)
    assert assessment.conflict_evidence_refs == (point.evidence_ref,)

    with pytest.raises(ValueError, match="conflict_evidence_refs.*duplicates"):
        assess_local_trajectory(
            lookup,
            ("sample-a",),
            conflict_evidence_refs=(point.evidence_ref, point.evidence_ref),
        )


def test_local_trajectory_rejects_duplicate_sample_inputs() -> None:
    with pytest.raises(ValueError, match="sample_stems.*duplicates"):
        assess_local_trajectory(
            LocalTrajectoryEvidenceLookup(points=()),
            ("sample-a", "sample-a"),
        )


def test_missing_landmarks_cannot_be_relabelled_as_conflict() -> None:
    assessment = assess_local_trajectory(
        LocalTrajectoryEvidenceLookup(points=()),
        ("sample-a",),
        conflict_evidence_refs=("manual:trajectory-conflict",),
    )

    assert assessment.status == "missing_landmarks"
    assert assessment.conflict_evidence_refs == ()


def test_istd_lookup_cannot_be_used_as_m3_local_trajectory_truth() -> None:
    with pytest.raises(TypeError, match="local feature landmarks"):
        assess_local_trajectory(DriftEvidenceLookup(points=()), ("sample-a",))


def test_leave_one_landmark_out_retains_points_without_a_median_template() -> None:
    points = (
        LocalFeatureLandmarkEvidence(
            sample_stem="sample-a",
            injection_order=1,
            acquisition_source_id="acquisition-source:sample-a",
            processing_request_id="processing-request:current",
            processing_attempt_id="processing-attempt:current",
            landmark_id="landmark-001",
            observed_rt_min=4.10,
            local_trajectory_rt_min=4.00,
            rt_drift_delta_min=0.10,
            quality="accepted",
            evidence_ref="peak-observation:a1",
        ),
        LocalFeatureLandmarkEvidence(
            sample_stem="sample-a",
            injection_order=1,
            acquisition_source_id="acquisition-source:sample-a",
            processing_request_id="processing-request:current",
            processing_attempt_id="processing-attempt:current",
            landmark_id="landmark-002",
            observed_rt_min=8.30,
            local_trajectory_rt_min=8.10,
            rt_drift_delta_min=0.20,
            quality="accepted",
            evidence_ref="peak-observation:a2",
        ),
    )
    full = LocalTrajectoryEvidenceLookup(points=points)
    leave_one_out = LocalTrajectoryEvidenceLookup(points=points[1:])

    full_assessment = assess_local_trajectory(full, ("sample-a",))
    leave_one_out_assessment = assess_local_trajectory(
        leave_one_out,
        ("sample-a",),
    )

    assert full_assessment.status == "assessable"
    assert full_assessment.landmark_points == points
    assert leave_one_out_assessment.status == "assessable"
    assert leave_one_out_assessment.landmark_points == points[1:]


def test_local_landmarks_reject_duplicate_physical_refs_across_lookup() -> None:
    with pytest.raises(ValueError, match="physical evidence refs must be unique"):
        LocalTrajectoryEvidenceLookup(
            points=(
                LocalFeatureLandmarkEvidence(
                    sample_stem="sample-a",
                    injection_order=1,
                    acquisition_source_id="acquisition-source:sample-a",
                    processing_request_id="processing-request:current",
                    processing_attempt_id="processing-attempt:current",
                    landmark_id="landmark-001",
                    observed_rt_min=4.10,
                    local_trajectory_rt_min=4.00,
                    rt_drift_delta_min=0.10,
                    quality="accepted",
                    evidence_ref="peak-observation:shared",
                ),
                LocalFeatureLandmarkEvidence(
                    sample_stem="sample-b",
                    injection_order=2,
                    acquisition_source_id="acquisition-source:sample-b",
                    processing_request_id="processing-request:current",
                    processing_attempt_id="processing-attempt:current",
                    landmark_id="landmark-001",
                    observed_rt_min=8.30,
                    local_trajectory_rt_min=8.00,
                    rt_drift_delta_min=0.30,
                    quality="accepted",
                    evidence_ref="peak-observation:shared",
                ),
            )
        )


def test_local_landmarks_reject_conflicting_sample_injection_order() -> None:
    with pytest.raises(ValueError, match="one injection order"):
        LocalTrajectoryEvidenceLookup(
            points=(
                LocalFeatureLandmarkEvidence(
                    sample_stem="sample-a",
                    injection_order=1,
                    acquisition_source_id="acquisition-source:sample-a",
                    processing_request_id="processing-request:current",
                    processing_attempt_id="processing-attempt:current",
                    landmark_id="landmark-001",
                    observed_rt_min=4.10,
                    local_trajectory_rt_min=4.00,
                    rt_drift_delta_min=0.10,
                    quality="accepted",
                    evidence_ref="peak-observation:a1",
                ),
                LocalFeatureLandmarkEvidence(
                    sample_stem="sample-a",
                    injection_order=2,
                    acquisition_source_id="acquisition-source:sample-a",
                    processing_request_id="processing-request:current",
                    processing_attempt_id="processing-attempt:current",
                    landmark_id="landmark-002",
                    observed_rt_min=8.30,
                    local_trajectory_rt_min=8.10,
                    rt_drift_delta_min=0.20,
                    quality="accepted",
                    evidence_ref="peak-observation:a2",
                ),
            )
        )


def test_local_landmarks_reject_conflicting_sample_acquisition_source() -> None:
    with pytest.raises(ValueError, match="one acquisition source"):
        LocalTrajectoryEvidenceLookup(
            points=(
                LocalFeatureLandmarkEvidence(
                    sample_stem="sample-a",
                    injection_order=1,
                    acquisition_source_id="acquisition-source:first",
                    processing_request_id="processing-request:current",
                    processing_attempt_id="processing-attempt:current",
                    landmark_id="landmark-001",
                    observed_rt_min=4.10,
                    local_trajectory_rt_min=4.00,
                    rt_drift_delta_min=0.10,
                    quality="accepted",
                    evidence_ref="peak-observation:a1",
                ),
                LocalFeatureLandmarkEvidence(
                    sample_stem="sample-a",
                    injection_order=1,
                    acquisition_source_id="acquisition-source:second",
                    processing_request_id="processing-request:current",
                    processing_attempt_id="processing-attempt:current",
                    landmark_id="landmark-002",
                    observed_rt_min=8.30,
                    local_trajectory_rt_min=8.10,
                    rt_drift_delta_min=0.20,
                    quality="accepted",
                    evidence_ref="peak-observation:a2",
                ),
            )
        )


@pytest.mark.parametrize(
    ("field_name", "expected_message"),
    (
        ("processing_request_id", "one processing request"),
        ("processing_attempt_id", "one processing attempt"),
    ),
)
def test_local_landmarks_reject_crossed_processing_owner(
    field_name: str,
    expected_message: str,
) -> None:
    common = {
        "sample_stem": "sample-a",
        "injection_order": 1,
        "acquisition_source_id": "acquisition-source:sample-a",
        "processing_request_id": "processing-request:current",
        "processing_attempt_id": "processing-attempt:current",
        "observed_rt_min": 4.1,
        "local_trajectory_rt_min": 4.0,
        "rt_drift_delta_min": 0.1,
        "quality": "accepted",
    }
    crossed = {**common, field_name: f"{field_name}:foreign"}

    with pytest.raises(ValueError, match=expected_message):
        LocalTrajectoryEvidenceLookup(
            points=(
                LocalFeatureLandmarkEvidence(
                    **common,
                    landmark_id="landmark-001",
                    evidence_ref="peak-observation:a1",
                ),
                LocalFeatureLandmarkEvidence(
                    **crossed,
                    landmark_id="landmark-002",
                    evidence_ref="peak-observation:a2",
                ),
            )
        )


def test_lookup_keeps_conflicting_injection_order_lazy_per_sample() -> None:
    lookup = DriftEvidenceLookup(
        points=(
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=7,
                trend_id="trend-001",
                istd_rt_min=1.9,
                local_trend_rt_min=2.1,
                rt_drift_delta_min=-0.2,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=8,
                trend_id="trend-002",
                istd_rt_min=2.0,
                local_trend_rt_min=2.0,
                rt_drift_delta_min=0.0,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-b",
                injection_order=10,
                trend_id="trend-001",
                istd_rt_min=2.2,
                local_trend_rt_min=2.0,
                rt_drift_delta_min=0.2,
                source="targeted_istd_trend",
            ),
        )
    )

    assert lookup.injection_order("sample-b") == 10
    assert lookup.sample_delta_min("sample-b") == 0.2
    with pytest.raises(ValueError, match="conflicting injection order"):
        lookup.injection_order("sample-a")


def test_read_targeted_istd_drift_evidence_uses_opaque_trends_and_no_target_context(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "targeted.xlsx"
    sample_info = tmp_path / "sample_info.csv"
    sample_info.write_text(
        "Sample_Name,Injection_Order\nsample-a,1\nsample-b,2\nsample-c,3\n",
        encoding="utf-8",
    )
    _write_targeted_workbook(workbook)

    lookup = read_targeted_istd_drift_evidence(
        targeted_workbook=workbook,
        sample_info=sample_info,
        local_window=10,
    )

    assert lookup.source == "targeted_istd_trend"
    assert [point.trend_id for point in lookup.points] == [
        "trend-001",
        "trend-001",
        "trend-001",
    ]
    assert [point.sample_stem for point in lookup.points] == [
        "sample-a",
        "sample-b",
        "sample-c",
    ]
    assert [point.injection_order for point in lookup.points] == [1, 2, 3]
    assert [round(point.rt_drift_delta_min, 2) for point in lookup.points] == [
        -0.10,
        0.0,
        0.10,
    ]
    assert [point.source for point in lookup.points] == [
        "targeted_istd_trend",
        "targeted_istd_trend",
        "targeted_istd_trend",
    ]
    assert lookup.sample_delta_min("sample-a") == pytest.approx(-0.10)
    assert lookup.sample_delta_min("sample-b") == 0.0
    assert lookup.sample_delta_min("sample-c") == pytest.approx(0.10)

    point = lookup.points[0]
    assert not hasattr(point, "istd_label")
    assert not hasattr(point, "target_label")
    assert lookup.istd_identity_anchor is None


def test_read_targeted_istd_drift_evidence_treats_whitespace_sample_as_blank(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "targeted.xlsx"
    sample_info = tmp_path / "sample_info.csv"
    sample_info.write_text(
        "Sample_Name,Injection_Order\nsample-a,1\nsample-b,2\nsample-c,3\n",
        encoding="utf-8",
    )
    _write_targeted_workbook_with_whitespace_sample(workbook)

    lookup = read_targeted_istd_drift_evidence(
        targeted_workbook=workbook,
        sample_info=sample_info,
        local_window=10,
    )

    assert [point.sample_stem for point in lookup.points] == [
        "sample-a",
        "sample-b",
        "sample-c",
    ]
    assert lookup.points[0].istd_rt_min == 2.0


def test_read_targeted_istd_drift_evidence_maps_sorted_labels_to_opaque_trends(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "targeted.xlsx"
    sample_info = tmp_path / "sample_info.csv"
    sample_info.write_text(
        "Sample_Name,Injection_Order\nsample-a,1\nsample-b,2\nsample-c,3\n",
        encoding="utf-8",
    )
    _write_targeted_workbook_with_reverse_istd_labels(workbook)

    lookup = read_targeted_istd_drift_evidence(
        targeted_workbook=workbook,
        sample_info=sample_info,
        local_window=10,
    )

    points_by_trend = {
        (point.trend_id, point.sample_stem): point for point in lookup.points
    }
    assert sorted(points_by_trend) == [
        ("trend-001", "sample-a"),
        ("trend-001", "sample-b"),
        ("trend-001", "sample-c"),
        ("trend-002", "sample-a"),
        ("trend-002", "sample-b"),
        ("trend-002", "sample-c"),
    ]
    assert points_by_trend[("trend-001", "sample-a")].istd_rt_min == 1.1
    assert points_by_trend[("trend-001", "sample-b")].istd_rt_min == 1.2
    assert points_by_trend[("trend-001", "sample-c")].istd_rt_min == 1.3
    assert points_by_trend[("trend-002", "sample-a")].istd_rt_min == 2.1
    assert points_by_trend[("trend-002", "sample-b")].istd_rt_min == 2.2
    assert points_by_trend[("trend-002", "sample-c")].istd_rt_min == 2.3

    point = lookup.points[0]
    assert not hasattr(point, "istd_label")
    assert not hasattr(point, "target_label")


def test_targeted_istd_anchor_matches_only_observed_dna_dr_target() -> None:
    target = Target(
        label="d3-5-hmdC",
        mz=261.127276,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
        isotope_label_type="deuterated",
        paired_rt_relation="none",
        sample_applicability="all",
    )
    anchor = build_targeted_istd_identity_anchor(
        mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd",
        ),
        observed_labels=("d3-5-hmdC",),
    )

    with pytest.raises(TypeError, match="target_snapshot"):
        build_targeted_istd_identity_anchor(
            (target,),  # type: ignore[arg-type]
            observed_labels=("d3-5-hmdC",),
        )

    assert anchor.matching_target(
        SimpleNamespace(
            feature_family_id="FAM_ISTD",
            neutral_loss_tag="DNA_dR",
            family_center_mz=261.1270,
            family_center_rt=9.0,
        )
    ) == target
    assert anchor.matching_target(
        SimpleNamespace(
            feature_family_id="FAM_OTHER",
            neutral_loss_tag="DNA_dR",
            family_center_mz=300.1605,
            family_center_rt=23.0,
        )
    ) is None
    assert anchor.matches_observation(
        target,
        neutral_loss_tag="DNA_dR",
        precursor_mz=261.127276,
        apex_rt=9.0,
    )
    assert not anchor.matches_observation(
        target,
        neutral_loss_tag="DNA_dR",
        precursor_mz=261.127276,
        apex_rt=10.1,
    )
    assert anchor.matching_family_ids(
        (
            SimpleNamespace(
                feature_family_id="FAM_ISTD",
                neutral_loss_tag="DNA_dR",
                family_center_mz=261.1270,
                family_center_rt=9.0,
            ),
        )
    ) == frozenset({"FAM_ISTD"})


def test_reader_mints_anchor_from_explicit_target_snapshot(tmp_path: Path) -> None:
    workbook = tmp_path / "targeted.xlsx"
    sample_info = tmp_path / "sample_info.csv"
    sample_info.write_text(
        "Sample_Name,Injection_Order\nsample-a,1\nsample-b,2\n",
        encoding="utf-8",
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "XIC Results"
    ws.append(["SampleName", "Target", "Role", "RT"])
    ws.append(["sample-a", "d3-5-hmdC", "ISTD", 8.5])
    ws.append(["sample-b", "d3-5-hmdC", "ISTD", 8.6])
    wb.save(workbook)

    target = Target(
        label="d3-5-hmdC",
        mz=261.127276,
        rt_min=8.0,
        rt_max=10.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )

    lookup = read_targeted_istd_drift_evidence(
        targeted_workbook=workbook,
        sample_info=sample_info,
        local_window=2,
        target_snapshot=mint_target_list_snapshot(
            (target,),
            identity_namespace="test-targeted-istd",
        ),
    )

    assert lookup.istd_identity_anchor is not None
    assert lookup.istd_identity_anchor.matching_family_ids(
        (
            SimpleNamespace(
                feature_family_id="FAM_ISTD",
                neutral_loss_tag="DNA_dR",
                family_center_mz=261.127276,
                family_center_rt=9.0,
            ),
        )
    ) == frozenset({"FAM_ISTD"})


def _write_targeted_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "XIC Results"
    ws.append(["SampleName", "Target", "Role", "RT", "GT Pass"])
    ws.append(["sample-a", "Analyte A", "Target", 4.0, "PASS"])
    ws.append([None, "ISTD-B", "ISTD", 1.9, "FAIL"])
    ws.append([None, "ISTD-B", "ISTD", "not numeric", "PASS"])
    ws.append(["sample-b", "Analyte B", "Target", 4.1, "PASS"])
    ws.append([None, "ISTD-B", "ISTD", 2.0, "PASS"])
    ws.append(["sample-c", "Analyte C", "Target", 4.2, "PASS"])
    ws.append([None, "ISTD-B", "ISTD", 2.1, "PASS"])
    wb.save(path)


def _write_targeted_workbook_with_whitespace_sample(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "XIC Results"
    ws.append(["SampleName", "Target", "Role", "RT"])
    ws.append(["sample-a", "Analyte A", "Target", 4.0])
    ws.append([None, "ISTD-B", "ISTD", 1.9])
    ws.append(["   ", "ISTD-B", "ISTD", 2.0])
    ws.append(["sample-b", "Analyte B", "Target", 4.1])
    ws.append([None, "ISTD-B", "ISTD", 2.1])
    ws.append(["sample-c", "Analyte C", "Target", 4.2])
    ws.append([None, "ISTD-B", "ISTD", 2.2])
    wb.save(path)


def _write_targeted_workbook_with_reverse_istd_labels(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "XIC Results"
    ws.append(["SampleName", "Target", "Role", "RT"])
    ws.append(["sample-a", "Analyte A", "Target", 4.0])
    ws.append([None, "ISTD-Z", "ISTD", 2.1])
    ws.append([None, "ISTD-A", "ISTD", 1.1])
    ws.append(["sample-b", "Analyte B", "Target", 4.1])
    ws.append([None, "ISTD-Z", "ISTD", 2.2])
    ws.append([None, "ISTD-A", "ISTD", 1.2])
    ws.append(["sample-c", "Analyte C", "Target", 4.2])
    ws.append([None, "ISTD-Z", "ISTD", 2.3])
    ws.append([None, "ISTD-A", "ISTD", 1.3])
    wb.save(path)
