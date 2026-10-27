from pathlib import Path

import pytest
from openpyxl import Workbook

from tests.test_cid_nl_explanation import _summary_fixture
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.drift_evidence import (
    DriftEvidenceLookup,
    SampleDriftEvidence,
)
from xic_extractor.alignment.edge_scoring import (
    evaluate_owner_edge as _evaluate_owner_edge,
)
from xic_extractor.alignment.feature_membership_inference import BatchRtWarp
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibrationAccumulator
from xic_extractor.configuration.models import Target


def evaluate_owner_edge(left, right, *, config, **kwargs):
    return _evaluate_owner_edge(
        left,
        right,
        config=config,
        method_evidence=CID_NL_GROUPING_RULES.pair_evidence(left, right, config),
        **kwargs,
    )


def test_measured_istd_binding_uses_observed_peak_not_expected_rt() -> None:
    from tests.test_alignment_edge_scoring import _owner
    from xic_extractor.alignment.drift_evidence import (
        mint_known_istd_reference_from_measurements,
    )
    from xic_extractor.configuration.targets import parse_target_rows

    targets = parse_target_rows(
        [
            {
                "label": "ISTD",
                "mz": "500",
                "expected_rt_min": "12",
                "cid_nl_tag": "dR",
                "role": "istd",
                "istd_pair": "",
            }
        ]
    )
    reference = mint_known_istd_reference_from_measurements(
        targets, {("ISTD", "early"): 8.0, ("ISTD", "late"): 9.0}
    )
    owners = tuple(
        _owner(
            sample,
            precursor_mz=500,
            product_mz=383.9526,
            observed_neutral_loss_da=116.0474,
            owner_apex_rt=rt,
        )
        for sample, rt in (("early", 8.0), ("late", 9.0))
    )
    lookup = reference.bind(owners)
    assert len(lookup.bindings) == 2
    assert lookup.corrected_pair_delta_sec(*owners) == pytest.approx(0)

    base = SampleRtFrame(
        injection_order={"early": 1, "late": 2},
        sample_cohorts={"early": "batch", "late": "batch"},
        sample_universe=frozenset({"early", "late"}),
    )
    with pytest.raises(ValueError, match="cannot be combined with generic drift"):
        base.with_sources(
            known_istd_reference=reference, drift_lookup=DriftEvidenceLookup(())
        )
    bootstrap = base.with_sources(known_istd_reference=reference).bind_owners(owners)
    frozen = bootstrap.with_warp(
        BatchRtWarp(
            (("early", ((7.0, 0.2), (12.0, 0.2))), ("late", ((7.0, 1.2), (12.0, 1.2)))),
            2,
        )
    )
    assert not bootstrap.has_coordinates
    assert tuple(bootstrap.corrected_rt_min(owner) for owner in owners) == (8.0, 9.0)
    assert frozen.has_coordinates
    assert tuple(frozen.corrected_rt_min(owner) for owner in owners) == pytest.approx(
        (7.8, 7.8)
    )
    for frame in (bootstrap, frozen):
        assert set(frame.known_identity_bindings) == {
            (owner.owner_id, "ISTD") for owner in owners
        }
        assert frame.has_direct_reference_pair(*owners)
        assert frame.corrected_pair_delta_sec(*owners) == pytest.approx(0)
        # The identity source supplies no order-gap evidence; metadata must not
        # silently replace that absence when the two contexts are consolidated.
        assert frame.drift_injection_order_gap("early", "late") is None
    assert tuple(owner.owner_apex_rt for owner in owners) == (8.0, 9.0)


def test_zero_rt_calibration_is_available_without_becoming_drift_pair_evidence():
    from tests.test_alignment_edge_scoring import _owner

    owner = _owner("measured", owner_apex_rt=8.0)
    raw = SampleRtFrame()
    measured_zero = raw.with_warp(
        BatchRtWarp((("measured", ((7.0, 0.0), (12.0, 0.0))),), 1)
    )
    assert raw.corrected_rt_min(owner) == measured_zero.corrected_rt_min(owner) == 8.0
    assert not raw.has_coordinates
    assert measured_zero.has_coordinates
    assert not raw.has_support("measured")
    assert measured_zero.has_support("measured")
    assert not measured_zero.has_support("unmeasured")
    assert not measured_zero.has_drift
    assert measured_zero.drift_source == "none"
    assert measured_zero.corrected_pair_delta_sec(owner, _owner("other")) is None


@pytest.mark.parametrize("shift", (0.0, 1.0))
def test_frozen_coordinate_replaces_rt_evidence_without_an_extra_score(shift):
    from tests.test_alignment_edge_scoring import _owner
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

    left = _owner("a", owner_apex_rt=10.0)
    right = _owner("b", owner_apex_rt=10.01 + shift)
    frame = SampleRtFrame().with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            (
                ("a", ((8.0, 8.0), (12.0, 12.0))),
                ("b", ((8.0 + shift, 8.0), (12.0 + shift, 12.0))),
            )
        )
    )
    common = evaluate_owner_edge(left, right, config=AlignmentConfig(), rt_frame=frame)
    raw = evaluate_owner_edge(
        left,
        _owner("b", owner_apex_rt=10.01),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(),
    )
    assert common.decision == raw.decision == "strong_edge"
    assert common.score == raw.score
    assert common.rt_basis == "drift_corrected"
    assert common.rt_drift_corrected_delta_sec == pytest.approx(0.6)
    assert common.rt_raw_delta_sec == pytest.approx(0.6 + shift * 60)


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

    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
    )

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
    assert changed.provenance["coordinates"] == reference.provenance["coordinates"]


def test_known_reference_content_identity_tracks_declared_and_measured_content():
    from dataclasses import replace

    from xic_extractor.alignment.drift_evidence import (
        mint_known_istd_reference_from_measurements,
    )

    target = Target("known", 500, 7, 10, 20, 116.0474, 20, 50, True, "")
    coordinates = {("known", "early"): 8, ("known", "late"): 9}
    reference = mint_known_istd_reference_from_measurements(
        (target,),
        coordinates,
        source_sha256="a" * 64,
    )
    equivalent = mint_known_istd_reference_from_measurements(
        (target,),
        {("known", "late"): 9.0, ("known", "early"): 8.0},
        source_identifier="elsewhere.xlsx",
        source_sha256="b" * 64,
    )
    changed_rt = mint_known_istd_reference_from_measurements(
        (target,),
        {**coordinates, ("known", "late"): 9.5},
        source_sha256="a" * 64,
    )
    changed_target = mint_known_istd_reference_from_measurements(
        (replace(target, ppm_tol=10),),
        coordinates,
        source_sha256="a" * 64,
    )

    assert (
        reference.anchor == equivalent.anchor
        and reference.coordinates == equivalent.coordinates
    )
    assert reference.workbook_sha256 == changed_rt.workbook_sha256
    assert reference.provenance["coordinates"] != changed_rt.provenance["coordinates"]
    assert (
        reference.provenance["target_snapshot"]
        != changed_target.provenance["target_snapshot"]
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
    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
    )

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
    tmp_path: Path,
    use_batch_context: bool,
) -> None:
    from dataclasses import replace

    from tests.test_alignment_edge_scoring import _owner
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
    )

    path = tmp_path / "reference.xlsx"
    workbook = _known_reference_workbook(path)
    reference = read_known_istd_reference(path)
    if use_batch_context:
        reference = reference.prepare_batch(
            sample_universe=frozenset({"early", "late", "missing"}),
            injection_order={"early": 1, "late": 2, "missing": 3},
        )
    early, late, other = tuple(
        _owner(
            sample,
            precursor_mz=500,
            product_mz=383.9526,
            observed_neutral_loss_da=116.0474,
            owner_apex_rt=rt,
        )
        for sample, rt in (("early", 8), ("late", 9), ("missing", 9.5))
    )
    lookup = reference.bind((early, late, other))
    config = AlignmentConfig()
    edge = evaluate_owner_edge(
        early,
        late,
        config=config,
        rt_frame=SampleRtFrame().with_sources(drift_lookup=lookup),
    )
    assert edge.rt_drift_corrected_delta_sec == pytest.approx(0)
    assert edge.decision == "strong_edge"
    assert lookup.sample_delta_min("early") is None
    assert lookup.istd_identity_anchor is None
    no_event = replace(late, primary_identity_event=None)
    wrong_nl = replace(
        late,
        primary_identity_event=replace(
            late.primary_identity_event,
            observed_neutral_loss_da=132.0423,
        ),
    )
    for invalid in (no_event, wrong_nl):
        rejected = reference.bind((early, invalid))
        assert rejected.corrected_pair_delta_sec(early, invalid) is None

    def assert_unavailable_pair_uses_only_raw(right, unavailable_lookup):
        # Lookup provenance is retained even when the trigger skips querying it.
        # Prove its unavailable binding separately, then pin every edge field.
        assert unavailable_lookup.corrected_pair_delta_sec(early, right) is None
        raw = evaluate_owner_edge(early, right, config=config, rt_frame=SampleRtFrame())
        suffix = (
            ";drift=raw_sufficient"
            if raw.decision == "strong_edge"
            else ";drift=unavailable"
        )
        assert evaluate_owner_edge(
            early,
            right,
            config=config,
            rt_frame=SampleRtFrame().with_sources(drift_lookup=unavailable_lookup),
        ) == replace(
            raw,
            drift_prior_source="targeted_istd_trend",
            reason=raw.reason + suffix,
        )

    assert_unavailable_pair_uses_only_raw(other, lookup)
    other_rt = replace(
        late,
        owner_id="different-peak",
        owner_apex_rt=9.5,
        owner_peak_start_rt=9.4,
        owner_peak_end_rt=9.6,
    )
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
    tmp_path: Path,
    use_reference: bool,
) -> None:
    from tests.alignment_pipeline_helpers import _owner
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.cross_sample.peak_groups import (
        construct_cross_sample_peak_group_hypotheses,
    )
    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
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
        rt_frame=SampleRtFrame().with_sources(drift_lookup=lookup),
        rules=CID_NL_GROUPING_RULES,
    )
    paired = early if use_reference else competitor
    separate = competitor if use_reference else early
    assert {frozenset(group.owner_ids) for group in groups} == {
        frozenset((paired.owner_id, late.owner_id)),
        frozenset((separate.owner_id,)),
    }


_LANDMARK_RTS = (8, 12, 16, 20, 24, 28, 32, 40)
_STRETCHED_RTS = tuple(1.25 * rt for rt in _LANDMARK_RTS)


def _batch_reference(tmp_path: Path, observations):
    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
    )

    path = tmp_path / "batch-reference.xlsx"
    workbook = _known_reference_workbook(path)
    targets = workbook["Targets"]
    targets.delete_rows(2, targets.max_row)
    results = workbook["XIC Results"]
    results.delete_rows(2, results.max_row)
    label_count = len(next(iter(observations.values())))
    for index in range(label_count):
        targets.append(
            [f"landmark-{index}", "ISTD", 300 + index * 50, 0, 60, 20, 116.0474, 20, 50]
        )
    for sample, observed in observations.items():
        for index, rt in enumerate(observed):
            results.append([sample, f"landmark-{index}", "ISTD", rt, 100])
    workbook.save(path)
    return read_known_istd_reference(path)


def test_batch_istd_coordinates_are_shared_by_grouping_and_backfill(tmp_path):
    reference = _batch_reference(
        tmp_path,
        {
            "early": _LANDMARK_RTS,
            "middle": tuple(rt + 0.2 for rt in _LANDMARK_RTS),
            "late": tuple(rt + 0.4 for rt in _LANDMARK_RTS),
        },
    )
    samples = frozenset({"early", "middle", "late"})
    orders = {"early": 1, "middle": 2, "late": 3}
    frame = SampleRtFrame(injection_order=orders, sample_universe=samples).with_sources(
        known_istd_reference=reference
    )
    frame = frame.with_sample_calibration(
        BatchRtCalibrationAccumulator(
            sample_stems=tuple(samples), sample_cohorts=dict.fromkeys(samples, "batch")
        ).freeze(measured_istds=frame.measured_istd_coordinates)
    )
    for sample, observed in (("early", 16), ("middle", 16.2), ("late", 16.4)):
        assert frame.has_support(sample)
        assert frame.common_rt_min(sample, observed) == pytest.approx(16.2)
        assert 16.2 + frame.offset_min(sample, 16.2) == pytest.approx(observed)
    assert frame.common_rt_min("middle", 3.0) == 3.0
    derived = SampleRtFrame().with_calibration(frame)
    assert derived.common_rt_min("late", 16.4) == pytest.approx(16.2)


@pytest.mark.parametrize(
    ("early_knots", "late_knots"),
    [
        (_LANDMARK_RTS, _STRETCHED_RTS),
        (_STRETCHED_RTS, _LANDMARK_RTS),
        (_LANDMARK_RTS, _LANDMARK_RTS),
    ],
)
def test_batch_reference_corrects_bootstrap_unknowns_without_merging_distinct_peaks(
    tmp_path: Path,
    early_knots,
    late_knots,
) -> None:
    from tests.alignment_pipeline_helpers import _owner
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.cross_sample.peak_groups import (
        construct_cross_sample_peak_group_hypotheses,
    )

    reference = _batch_reference(tmp_path, {"early": early_knots, "late": late_knots})
    early = _owner(
        "early",
        "main",
        apex_rt=sum(early_knots[:2]) / 2,
        precursor_mz=600,
        product_mz=483.9526,
    )
    late = _owner(
        "late",
        "main",
        apex_rt=sum(late_knots[:2]) / 2,
        precursor_mz=600,
        product_mz=483.9526,
    )
    competitor = _owner(
        "early",
        "other",
        apex_rt=early.owner_apex_rt + 2,
        precursor_mz=600,
        product_mz=483.9526,
    )
    owners = (early, late, competitor)
    original = tuple((o.owner_apex_rt, o.selected_integration) for o in owners)
    universe = frozenset({"early", "late"})
    order = {"early": 10, "late": 40}
    lookup = reference.bind(owners, sample_universe=universe, injection_order=order)
    assert not lookup.bindings  # These owners are not any declared ISTD.
    assert lookup.corrected_pair_delta_sec(early, late) == pytest.approx(0)
    assert lookup.corrected_pair_delta_sec(competitor, late) > 60
    groups = construct_cross_sample_peak_group_hypotheses(
        owners,
        config=AlignmentConfig(),
        sample_universe=universe,
        rt_frame=SampleRtFrame(injection_order=(order or {})).with_sources(
            drift_lookup=lookup
        ),
        bootstrap=True,
        rules=CID_NL_GROUPING_RULES,
    )
    assert {frozenset(group.owner_ids) for group in groups} == {
        frozenset((early.owner_id, late.owner_id)),
        frozenset((competitor.owner_id,)),
    }
    assert tuple((o.owner_apex_rt, o.selected_integration) for o in owners) == original


def test_prepared_calibration_receipt_preserves_owner_map_without_refitting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.alignment.sample_rt_calibration import (
        interpolate_calibrated_rt_min,
    )

    reference = _batch_reference(
        tmp_path,
        {
            "early": _LANDMARK_RTS,
            "late": _STRETCHED_RTS,
        },
    )
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
    assert len(calibration["measurements"]) == 16
    samples = {sample["sample_stem"]: sample for sample in calibration["samples"]}
    assert samples["missing"]["status"] == "unavailable"
    assert samples["missing"]["domain_rt_min"] is None
    knots = samples["early"]["knots"]
    assert samples["early"]["domain_rt_min"] == [8, 40]
    assert interpolate_calibrated_rt_min(14, knots) == pytest.approx(14 * 1.125)
    assert interpolate_calibrated_rt_min(7, knots) is None
    assert interpolate_calibrated_rt_min(41, knots) is None
    from xic_extractor.diagnostics.alignment_explanation import (
        ExplanationReportWriter,
        read_cid_nl_explanation,
    )

    with ExplanationReportWriter(tmp_path / "calibration.sqlite") as writer:
        path = writer.finalize(
            _summary_fixture(
                {"known_istd_reference": receipt}, review_only=writer.review_only
            )
        )
    saved = read_cid_nl_explanation(path)
    assert saved["schema_version"] == "alignment_explanation"
    assert saved["known_istd_reference"] == receipt


def test_batch_reference_does_not_invent_missing_measured_landmarks(
    tmp_path: Path,
) -> None:
    from tests.alignment_pipeline_helpers import _owner

    reference = _batch_reference(
        tmp_path,
        {
            "early": (8, 20, 40),
            "middle": (11, None, 48),
            "late": (10, 24, 46),
            "outside-batch": (30, 35, 55),
        },
    )
    universe = frozenset({"early", "middle", "late"})
    order = {"early": 10, "middle": 20, "late": 40}
    early = _owner("early", "unknown", apex_rt=20, precursor_mz=600)
    middle = _owner("middle", "unknown", apex_rt=20 + 4 / 3, precursor_mz=600)
    late = _owner("late", "unknown", apex_rt=24, precursor_mz=600)
    lookup = reference.bind(
        (early, middle, late),
        sample_universe=universe,
        injection_order=order,
    )
    receipt = lookup.reference.explanation_provenance["calibration"]
    middle_map = next(r for r in receipt["samples"] if r["sample_stem"] == "middle")
    assert middle_map["knots"] == []
    assert middle_map["status"] == "unavailable"
    # Neither other injections nor interpolation may manufacture the missing
    # measured positions needed to authorize a LOESS map.
    assert sum(r["sample_stem"] == "middle" for r in receipt["measurements"]) == 2
    assert lookup.corrected_pair_delta_sec(early, middle) is None
    assert lookup.corrected_pair_delta_sec(middle, late) is None


@pytest.mark.parametrize(
    ("observations", "early_rt", "late_rt"),
    [
        (
            {
                "early": (None, 12, 16, 20, 24, 28, 32, 36, 40),
                "late": (10, 15, 20, 25, 30, 35, 40, 45, 50),
            },
            8,
            10,
        ),
        (
            {
                "early": (8, 12, 16, 20, 24, 28, 32, 36, 40),
                "late": (10, 15, 20, 25, 30, 35, 40, 45, None),
            },
            40,
            50,
        ),
        ({"early": _LANDMARK_RTS, "late": _STRETCHED_RTS}, 7, 9),
        ({"early": _LANDMARK_RTS, "late": _STRETCHED_RTS}, 41, 51),
        ({"early": _LANDMARK_RTS, "late": (10, 9, 20, 25, 30, 35, 40, 50)}, 14, 15),
        ({"early": _LANDMARK_RTS, "late": (12, 0, 20, 25, 30, 35, 40, 50)}, 14, 15),
    ],
)
def test_batch_reference_declines_extrapolation_or_nonmonotone_knots(
    tmp_path: Path,
    observations,
    early_rt,
    late_rt,
) -> None:
    from tests.alignment_pipeline_helpers import _owner

    reference = _batch_reference(tmp_path, observations)
    early = _owner("early", "unknown", apex_rt=early_rt, precursor_mz=600)
    late = _owner("late", "unknown", apex_rt=late_rt, precursor_mz=600)
    lookup = reference.bind(
        (early, late),
        sample_universe=frozenset(observations),
        injection_order={"early": 10, "late": 40},
    )
    assert lookup.corrected_pair_delta_sec(early, late) is None


def test_batch_reference_preparation_is_shared_across_method_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.alignment_pipeline_helpers import _owner
    from xic_extractor.alignment import drift_evidence

    reference = _batch_reference(
        tmp_path,
        {
            "early": _LANDMARK_RTS,
            "late": _STRETCHED_RTS,
            "unused": tuple(1.5 * rt + 3 for rt in _LANDMARK_RTS),
        },
    )
    universe = frozenset({"early", "late"})
    order = {"early": 10, "late": 40}
    prepared = reference.prepare_batch(sample_universe=universe, injection_order=order)

    def unexpected_recomputation(*_args, **_kwargs):
        pytest.fail("unchanged prepared batch recomputed reference medians")

    monkeypatch.setattr(drift_evidence.statistics, "median", unexpected_recomputation)
    assert (
        prepared.prepare_batch(
            sample_universe=universe,
            injection_order=dict(order),
        )
        is prepared
    )
    assert (
        reference.prepare_batch(
            sample_universe=universe,
            injection_order=dict(order),
        )
        is prepared
    )
    for index in range(3):
        early = _owner("early", f"unknown-{index}", apex_rt=14, precursor_mz=600)
        late = _owner("late", f"unknown-{index}", apex_rt=17.5, precursor_mz=600)
        lookup = prepared.bind(
            (early, late),
            sample_universe=universe,
            injection_order=order,
        )
        assert lookup.reference is prepared
        assert lookup.corrected_pair_delta_sec(early, late) == pytest.approx(0)


def test_batch_reference_changed_universe_does_not_reuse_stale_medians(
    tmp_path: Path,
) -> None:
    from tests.alignment_pipeline_helpers import _owner

    reference = _batch_reference(
        tmp_path,
        {
            "early": _LANDMARK_RTS,
            "late": _STRETCHED_RTS,
            "extra": tuple(1.5 * rt + 3 for rt in _LANDMARK_RTS),
        },
    )
    early = _owner("early", "unknown", apex_rt=14, precursor_mz=600)
    late = _owner("late", "unknown", apex_rt=18, precursor_mz=600)
    order = {"early": 1, "late": 2, "extra": 3}
    original = reference.bind(
        (early, late),
        sample_universe=frozenset({"early", "late"}),
        injection_order=order,
    )
    changed = reference.bind(
        (early, late),
        sample_universe=frozenset(order),
        injection_order=order,
    )
    # The original gauge is 1.125*x; adding the third sample changes it to
    # 1.25*x. The two owners represent x=14 and x=14.4 respectively.
    assert original.corrected_pair_delta_sec(early, late) == pytest.approx(27)
    assert changed.corrected_pair_delta_sec(early, late) == pytest.approx(30)
    assert original.reference is not changed.reference


def test_lookup_returns_median_sample_delta_and_injection_order() -> None:
    lookup = DriftEvidenceLookup(
        points=(
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=7,
                istd_rt_min=1.9,
                local_trend_rt_min=2.1,
                rt_drift_delta_min=-0.2,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=7,
                istd_rt_min=2.0,
                local_trend_rt_min=2.0,
                rt_drift_delta_min=0.0,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-b",
                injection_order=10,
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
                istd_rt_min=1.9,
                local_trend_rt_min=2.1,
                rt_drift_delta_min=-0.2,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=8,
                istd_rt_min=2.0,
                local_trend_rt_min=2.0,
                rt_drift_delta_min=0.0,
                source="targeted_istd_trend",
            ),
        )
    )

    with pytest.raises(ValueError, match="conflicting injection order"):
        lookup.injection_order("sample-a")


def test_lookup_keeps_conflicting_injection_order_lazy_per_sample() -> None:
    lookup = DriftEvidenceLookup(
        points=(
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=7,
                istd_rt_min=1.9,
                local_trend_rt_min=2.1,
                rt_drift_delta_min=-0.2,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-a",
                injection_order=8,
                istd_rt_min=2.0,
                local_trend_rt_min=2.0,
                rt_drift_delta_min=0.0,
                source="targeted_istd_trend",
            ),
            SampleDriftEvidence(
                sample_stem="sample-b",
                injection_order=10,
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
