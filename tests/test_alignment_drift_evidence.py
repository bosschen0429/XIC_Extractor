from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from xic_extractor.alignment.drift_evidence import (
    DriftEvidenceLookup,
    LocalFeatureLandmarkEvidence,
    LocalTrajectoryEvidenceLookup,
    SampleDriftEvidence,
    assess_local_trajectory,
    build_targeted_istd_identity_anchor,
)
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import mint_target_list_snapshot


def _known_reference_workbook(path: Path) -> Workbook:
    workbook = Workbook()
    targets = workbook.active
    targets.title = "Targets"
    targets.append(
        [
            "Label",
            "Role",
            "m/z",
            "RT min",
            "RT max",
            "ppm tol",
            "NL (Da)",
            "NL ppm warn",
            "NL ppm max",
        ]
    )
    targets.append([" known ", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50])
    results = workbook.create_sheet("XIC Results")
    results.append(["SampleName", "Target", "Role", "RT", "Area"])
    results.append([" early ", "analyte", "Analyte", 8, 1])
    results.append([None, "known", "ISTD", 8, "not evidence"])
    results.append(["late", "analyte", "Analyte", 9, 1])
    results.append(["", "known", "ISTD", 9, -999])
    results.append(["missing", "analyte", "Analyte", "ND", 1])
    results.append([None, "known", "ISTD", "ND", 100])
    workbook.save(path)
    return workbook


def test_known_reference_uses_declared_targets_rt_and_source_bytes(
    tmp_path: Path,
) -> None:
    import hashlib

    from xic_extractor.acquisition.istd_reference import read_known_istd_reference

    path = tmp_path / "reference.xlsx"
    workbook = _known_reference_workbook(path)
    reference = read_known_istd_reference(path)
    assert reference.coordinates == (
        ("known", "early", 8, -0.5),
        ("known", "late", 9, 0.5),
    )
    assert reference.workbook_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    workbook["XIC Results"]["E3"] = 123456789
    workbook.save(path)
    changed = read_known_istd_reference(path)
    assert changed.coordinates == reference.coordinates
    assert changed.workbook_sha256 != reference.workbook_sha256
    assert changed.anchor.target_snapshot == reference.anchor.target_snapshot
    assert (
        changed.provenance["content_sha256"] == reference.provenance["content_sha256"]
    )


def test_known_reference_content_identity_tracks_declared_and_measured_content():
    from dataclasses import replace

    from xic_extractor.alignment.drift_evidence import (
        mint_known_istd_reference_from_measurements,
    )

    target = Target("known", 500, 7, 10, 20, 116.0474, 20, 50, True, "")
    coordinates = {("known", "early"): 8, ("known", "late"): 9}
    reference = mint_known_istd_reference_from_measurements(
        (target,), coordinates, source_sha256="a" * 64,
    )
    equivalent = mint_known_istd_reference_from_measurements(
        (target,), {("known", "late"): 9.0, ("known", "early"): 8.0},
        source_identifier="elsewhere.xlsx", source_sha256="b" * 64,
    )
    changed_rt = mint_known_istd_reference_from_measurements(
        (target,), {**coordinates, ("known", "late"): 9.5},
        source_sha256="a" * 64,
    )
    changed_target = mint_known_istd_reference_from_measurements(
        (replace(target, ppm_tol=10),), coordinates, source_sha256="a" * 64,
    )

    assert (
        reference.provenance["content_sha256"]
        == equivalent.provenance["content_sha256"]
    )
    assert reference.workbook_sha256 == changed_rt.workbook_sha256
    assert (
        reference.provenance["content_sha256"]
        != changed_rt.provenance["content_sha256"]
    )
    assert (
        reference.provenance["content_sha256"]
        != changed_target.provenance["content_sha256"]
    )


@pytest.mark.parametrize(
    "damage",
    (
        "duplicate_reference",
        "duplicate_target",
        "malformed_rt",
        "malformed_mass",
        "missing_header",
        "undeclared_target",
        "unbound_sample",
    ),
)
def test_known_reference_rejects_invalid_consumed_input(
    tmp_path: Path, damage: str
) -> None:
    from xic_extractor.acquisition.istd_reference import read_known_istd_reference

    path = tmp_path / "reference.xlsx"
    workbook = _known_reference_workbook(path)
    if damage == "duplicate_reference":
        workbook["XIC Results"].append(["early", "known", "ISTD", 8, 1])
    elif damage == "duplicate_target":
        workbook["Targets"].append(["known", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50])
    elif damage == "malformed_rt":
        workbook["XIC Results"]["D3"] = "broken"
    elif damage == "malformed_mass":
        workbook["Targets"]["C2"] = "broken"
    elif damage == "missing_header":
        workbook["XIC Results"]["D1"] = "time"
    elif damage == "unbound_sample":
        workbook["XIC Results"].delete_rows(2)
    else:
        workbook["XIC Results"]["B3"] = "undeclared"
    workbook.save(path)
    with pytest.raises(ValueError):
        read_known_istd_reference(path)


@pytest.mark.parametrize("use_batch_context", [False, True])
def test_known_reference_only_corrects_unique_same_target_support_pairs(
    tmp_path: Path, use_batch_context: bool,
) -> None:
    from dataclasses import replace

    from tests.test_alignment_edge_scoring import _owner
    from xic_extractor.acquisition.istd_reference import read_known_istd_reference
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.edge_scoring import evaluate_owner_edge

    path = tmp_path / "reference.xlsx"
    workbook = _known_reference_workbook(path)
    reference = read_known_istd_reference(path)
    if use_batch_context:
        reference = reference.prepare_batch(
            sample_universe=frozenset({"early", "late", "missing"}),
            injection_order={"early": 1, "late": 2, "missing": 3},
        )
    early, late, other = tuple(_owner(
        sample, precursor_mz=500, product_mz=383.9526,
        observed_neutral_loss_da=116.0474, owner_apex_rt=rt,
    ) for sample, rt in (("early", 8), ("late", 9), ("missing", 9.5)))
    lookup = reference.bind((early, late, other))
    config = AlignmentConfig()
    edge = evaluate_owner_edge(early, late, config=config, drift_lookup=lookup)
    assert edge.rt_drift_corrected_delta_sec == pytest.approx(0)
    assert edge.decision == "strong_edge"
    assert lookup.sample_delta_min("early") is None
    assert lookup.istd_identity_anchor is None
    no_event = replace(late, primary_identity_event=None)
    wrong_nl = replace(late, primary_identity_event=replace(
        late.primary_identity_event, observed_neutral_loss_da=132.0423,
    ))
    for invalid in (no_event, wrong_nl):
        rejected = reference.bind((early, invalid))
        assert rejected.corrected_pair_delta_sec(early, invalid) is None

    def assert_unavailable_pair_uses_only_raw(right, unavailable_lookup):
        # Lookup provenance is retained even when the trigger skips querying it.
        # Prove its unavailable binding separately, then pin every edge field.
        assert unavailable_lookup.corrected_pair_delta_sec(early, right) is None
        raw = evaluate_owner_edge(early, right, config=config)
        suffix = (
            ";drift=raw_sufficient" if raw.decision == "strong_edge"
            else ";drift=unavailable"
        )
        assert evaluate_owner_edge(
            early, right, config=config, drift_lookup=unavailable_lookup,
        ) == replace(
            raw, drift_prior_source="targeted_istd_trend", reason=raw.reason + suffix,
        )

    assert_unavailable_pair_uses_only_raw(other, lookup)
    other_rt = replace(late, owner_id="different-peak", owner_apex_rt=9.5,
                       owner_peak_start_rt=9.4, owner_peak_end_rt=9.6)
    assert_unavailable_pair_uses_only_raw(other_rt, lookup)
    # An overlapping SUPPORT owner prevents a unique binding; removing it later
    # from a group must not recreate reference authority.
    ambiguous = reference.bind((early, late, replace(late, owner_id="overlap")))
    assert_unavailable_pair_uses_only_raw(late, ambiguous)
    workbook["XIC Results"]["D5"] = "ND"
    workbook.save(path)
    partial = read_known_istd_reference(path).bind((early, late))
    assert_unavailable_pair_uses_only_raw(late, partial)
    workbook["Targets"].append(["second", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50])
    workbook["XIC Results"].append(["early", "second", "ISTD", 8, 1])
    workbook.save(path)
    two_targets = read_known_istd_reference(path).bind((early, late))
    assert not two_targets.bindings


@pytest.mark.parametrize("use_reference", (False, True))
def test_known_reference_grouping_preserves_main_peak_against_closer_competitor(
    tmp_path: Path, use_reference: bool,
) -> None:
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.acquisition.istd_reference import read_known_istd_reference
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        construct_cross_sample_peak_group_hypotheses,
    )

    path = tmp_path / "reference.xlsx"
    _known_reference_workbook(path)
    early = _owner("early", "main", apex_rt=8)
    competitor = _owner("early", "other", apex_rt=8.8)
    late = _owner("late", "main", apex_rt=9)
    owners = (early, competitor, late)
    lookup = read_known_istd_reference(path).bind(owners) if use_reference else None
    groups = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(),
        sample_universe=frozenset({"early", "late"}),
        drift_lookup=lookup,
    )
    paired = early if use_reference else competitor
    separate = competitor if use_reference else early
    assert {frozenset(group.owner_ids) for group in groups} == {
        frozenset((paired.owner_id, late.owner_id)),
        frozenset((separate.owner_id,)),
    }


def _batch_reference(tmp_path: Path, observations):
    from xic_extractor.acquisition.istd_reference import read_known_istd_reference

    path = tmp_path / "batch-reference.xlsx"
    workbook = _known_reference_workbook(path)
    targets = workbook["Targets"]
    targets.delete_rows(2, targets.max_row)
    results = workbook["XIC Results"]
    results.delete_rows(2, results.max_row)
    label_count = len(next(iter(observations.values())))
    for index in range(label_count):
        targets.append(
            [f"landmark-{index}", "ISTD", 300 + index * 50, 0, 60,
             20, 116.0474, 20, 50]
        )
    for sample, observed in observations.items():
        for index, rt in enumerate(observed):
            results.append([sample, f"landmark-{index}", "ISTD", rt, 100])
    workbook.save(path)
    return read_known_istd_reference(path)


@pytest.mark.parametrize(
    ("early_knots", "late_knots"),
    [((8, 20, 40), (10, 24, 46)),
     ((10, 24, 46), (8, 20, 40)),
     ((8, 20, 40), (8, 20, 40))],
)
def test_batch_reference_corrects_bootstrap_unknowns_without_merging_distinct_peaks(
    tmp_path: Path, early_knots, late_knots,
) -> None:
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.cross_sample_peak_groups import (
        construct_cross_sample_peak_group_hypotheses,
    )

    reference = _batch_reference(
        tmp_path, {"early": early_knots, "late": late_knots}
    )
    early = _owner("early", "main", apex_rt=sum(early_knots[:2]) / 2,
                   precursor_mz=600, product_mz=483.9526)
    late = _owner("late", "main", apex_rt=sum(late_knots[:2]) / 2,
                  precursor_mz=600, product_mz=483.9526)
    competitor = _owner("early", "other", apex_rt=early.owner_apex_rt + 2,
                        precursor_mz=600, product_mz=483.9526)
    owners = (early, late, competitor)
    original = tuple((o.owner_apex_rt, o.selected_integration) for o in owners)
    universe = frozenset({"early", "late"})
    order = {"early": 10, "late": 40}
    lookup = reference.bind(owners, sample_universe=universe, injection_order=order)
    assert not lookup.bindings  # These owners are not any declared ISTD.
    assert lookup.corrected_pair_delta_sec(early, late) == pytest.approx(0)
    assert lookup.corrected_pair_delta_sec(competitor, late) > 60
    groups = construct_cross_sample_peak_group_hypotheses(
        owners, config=AlignmentConfig(), sample_universe=universe,
        injection_order=order, drift_lookup=lookup, bootstrap=True,
    )
    assert {frozenset(group.owner_ids) for group in groups} == {
        frozenset((early.owner_id, late.owner_id)),
        frozenset((competitor.owner_id,)),
    }
    assert tuple((o.owner_apex_rt, o.selected_integration) for o in owners) == original


def test_prepared_calibration_receipt_preserves_owner_map_without_refitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.alignment.drift_evidence import interpolate_calibrated_rt_min

    reference = _batch_reference(tmp_path, {
        "early": (8, 20, 40), "late": (10, 24, 46),
    })
    assert reference.explanation_provenance.get("calibration") is None
    prepared = reference.prepare_batch(
        sample_universe=frozenset({"early", "late", "missing"}),
        injection_order={"early": 1, "late": 2},
    )

    def cannot_refit(*args, **kwargs):
        raise AssertionError("receipt must not recalculate calibration")

    monkeypatch.setattr(type(reference), "prepare_batch", cannot_refit)
    receipt = reference.explanation_provenance
    assert receipt == prepared.explanation_provenance
    assert {k: receipt[k] for k in reference.provenance} == reference.provenance
    calibration = receipt["calibration"]
    assert calibration["schema_version"] == "known_istd_calibration"
    assert calibration["extrapolation"] == "unavailable"
    assert len(calibration["measurements"]) == 6
    samples = {sample["sample_stem"]: sample for sample in calibration["samples"]}
    assert samples["missing"]["status"] == "unavailable"
    assert samples["missing"]["domain_rt_min"] is None
    knots = samples["early"]["knots"]
    assert samples["early"]["domain_rt_min"] == [8, 40]
    assert interpolate_calibrated_rt_min(14, knots) == pytest.approx(15.5)
    assert interpolate_calibrated_rt_min(7, knots) is None
    assert interpolate_calibrated_rt_min(41, knots) is None
    from xic_extractor.diagnostics.alignment_explanation import (
        ExplanationReportWriter,
        read_cid_nl_explanation,
    )

    with ExplanationReportWriter(tmp_path / "calibration.sqlite") as writer:
        path = writer.finalize({"known_istd_reference": receipt})
    saved = read_cid_nl_explanation(path)
    assert saved["schema_version"] == "alignment_explanation"
    assert saved["known_istd_reference"] == receipt


def test_batch_reference_interpolates_missing_landmark_in_canonical_order(
    tmp_path: Path,
) -> None:
    from tests.test_alignment_owner_clustering import _owner

    reference = _batch_reference(tmp_path, {
        "early": (8, 20, 40), "middle": (11, None, 48), "late": (10, 24, 46),
        "outside-batch": (30, 35, 55),
    })
    universe = frozenset({"early", "middle", "late"})
    order = {"early": 10, "middle": 20, "late": 40}
    early = _owner("early", "unknown", apex_rt=20, precursor_mz=600)
    middle = _owner("middle", "unknown", apex_rt=20 + 4 / 3, precursor_mz=600)
    late = _owner("late", "unknown", apex_rt=24, precursor_mz=600)
    lookup = reference.bind(
        (early, middle, late), sample_universe=universe, injection_order=order,
    )
    assert lookup.corrected_pair_delta_sec(early, middle) == pytest.approx(0)
    assert lookup.corrected_pair_delta_sec(middle, late) == pytest.approx(0)


@pytest.mark.parametrize(
    ("observations", "early_rt", "late_rt"),
    [({"early": (None, 20, 40), "late": (10, 24, 46)}, 8, 10),
     ({"early": (8, 20, 40), "late": (10, 24, None)}, 40, 46),
     ({"early": (8, 20, 40), "late": (10, 24, 46)}, 7, 9),
     ({"early": (8, 20, 40), "late": (10, 24, 46)}, 41, 47),
     ({"early": (8, 20, 40), "late": (10, 9, 46)}, 14, 15),
     ({"early": (8, 20, 40), "late": (12, 0, 46)}, 14, 15)],
)
def test_batch_reference_declines_extrapolation_or_nonmonotone_knots(
    tmp_path: Path, observations, early_rt, late_rt,
) -> None:
    from tests.test_alignment_owner_clustering import _owner

    reference = _batch_reference(tmp_path, observations)
    early = _owner("early", "unknown", apex_rt=early_rt, precursor_mz=600)
    late = _owner("late", "unknown", apex_rt=late_rt, precursor_mz=600)
    lookup = reference.bind(
        (early, late), sample_universe=frozenset(observations),
        injection_order={"early": 10, "late": 40},
    )
    assert lookup.corrected_pair_delta_sec(early, late) is None


def test_batch_reference_preparation_is_shared_across_method_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_alignment_owner_clustering import _owner
    from xic_extractor.alignment import drift_evidence

    reference = _batch_reference(tmp_path, {
        "early": (8, 20, 40), "late": (10, 24, 46), "unused": (15, 30, 50),
    })
    universe = frozenset({"early", "late"})
    order = {"early": 10, "late": 40}
    prepared = reference.prepare_batch(sample_universe=universe, injection_order=order)

    def unexpected_recomputation(*_args, **_kwargs):
        pytest.fail("unchanged prepared batch recomputed reference medians")

    monkeypatch.setattr(drift_evidence.statistics, "median", unexpected_recomputation)
    assert prepared.prepare_batch(
        sample_universe=universe, injection_order=dict(order),
    ) is prepared
    assert reference.prepare_batch(
        sample_universe=universe, injection_order=dict(order),
    ) is prepared
    for index in range(3):
        early = _owner("early", f"unknown-{index}", apex_rt=14, precursor_mz=600)
        late = _owner("late", f"unknown-{index}", apex_rt=17, precursor_mz=600)
        lookup = prepared.bind(
            (early, late), sample_universe=universe, injection_order=order,
        )
        assert lookup.reference is prepared
        assert lookup.corrected_pair_delta_sec(early, late) == pytest.approx(0)


def test_batch_reference_changed_universe_does_not_reuse_stale_medians(
    tmp_path: Path,
) -> None:
    from tests.test_alignment_owner_clustering import _owner

    reference = _batch_reference(tmp_path, {
        "early": (8, 20, 40), "late": (10, 24, 46), "extra": (15, 30, 50),
    })
    early = _owner("early", "unknown", apex_rt=14, precursor_mz=600)
    late = _owner("late", "unknown", apex_rt=17.5, precursor_mz=600)
    order = {"early": 1, "late": 2, "extra": 3}
    original = reference.bind(
        (early, late), sample_universe=frozenset({"early", "late"}),
        injection_order=order,
    )
    changed = reference.bind(
        (early, late), sample_universe=frozenset(order), injection_order=order,
    )
    assert original.corrected_pair_delta_sec(early, late) == pytest.approx(195 / 7)
    assert changed.corrected_pair_delta_sec(early, late) == pytest.approx(30)
    assert original.reference is not changed.reference


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


def test_istd_lookup_cannot_be_used_as_identity_trajectory_truth() -> None:
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
