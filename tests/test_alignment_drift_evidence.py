from pathlib import Path

import pytest

from tests.alignment_drift_evidence_fixtures import (
    _known_reference_workbook,
)
from tests.owner_peak_fixtures import with_peak
from tests.rt_calibration_fixtures import calibration_from_knots
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibrationAccumulator,
)
from xic_extractor.configuration.models import Target
from xic_extractor.injection_timeline import InjectionSequence


def test_measured_istd_binding_uses_observed_peak_not_expected_rt() -> None:
    from tests.alignment_edge_scoring_fixtures import _owner
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
                "cid_nl_loss_da": "116.0474",
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

    base = SampleRtFrame(
        injections=InjectionSequence({"early": 1, "late": 2}),
        sample_cohorts={"early": "batch", "late": "batch"},
        sample_universe=frozenset({"early", "late"}),
    )
    bootstrap = base.with_sources(known_istd_reference=reference).bind_owners(owners)
    frozen = bootstrap.with_sample_calibration(
        calibration_from_knots(
            (
                ("early", ((7.2, 7.0), (12.2, 12.0))),
                ("late", ((8.2, 7.0), (13.2, 12.0))),
            )
        )
    )
    assert tuple(bootstrap.corrected_rt_min(owner) for owner in owners) == (8.0, 9.0)
    assert tuple(frozen.corrected_rt_min(owner) for owner in owners) == pytest.approx(
        (7.8, 7.8)
    )
    for frame in (bootstrap, frozen):
        assert set(frame.known_identity_bindings) == {
            (owner.owner_id, "ISTD") for owner in owners
        }
    assert tuple(owner.peak.integration.rt_apex_min for owner in owners) == (8.0, 9.0)


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
    workbook["XIC Results"]["D3"] = 123456789
    workbook.save(path)
    changed = read_known_istd_reference(path)
    assert changed.coordinates == reference.coordinates
    assert changed.workbook_sha256 != reference.workbook_sha256
    assert changed.anchor.targets == reference.anchor.targets
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
        "zero_width_rt",
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
        workbook["XIC Results"].append(["early", "known", 8, 1])
    elif damage == "duplicate_target":
        workbook["Targets"].append(["known", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50])
    elif damage == "malformed_rt":
        workbook["XIC Results"]["C3"] = "broken"
    elif damage == "zero_width_rt":
        workbook["Targets"]["E2"] = workbook["Targets"]["D2"].value
    elif damage == "malformed_mass":
        workbook["Targets"]["C2"] = "broken"
    elif damage == "missing_header":
        workbook["XIC Results"]["C1"] = "time"
    elif damage == "unbound_sample":
        workbook["XIC Results"].delete_rows(2)
    else:
        workbook["XIC Results"]["B3"] = "undeclared"
    workbook.save(path)
    with pytest.raises(ValueError):
        read_known_istd_reference(path)


def test_known_reference_binds_only_unique_same_target_owners(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from tests.alignment_edge_scoring_fixtures import _owner
    from xic_extractor.applications.untargeted.istd_reference import (
        read_known_istd_reference,
    )

    path = tmp_path / "reference.xlsx"
    workbook = _known_reference_workbook(path)
    reference = read_known_istd_reference(path)
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

    def bound(lookup):
        return {owner for owner, _ in lookup.bindings}

    lookup = reference.bind((early, late, other))
    assert lookup.bindings == ((early.owner_id, "known"), (late.owner_id, "known"))
    no_event = replace(late, primary_identity_event=None)
    wrong_nl = replace(
        late,
        primary_identity_event=replace(
            late.primary_identity_event,
            observed_neutral_loss_da=132.0423,
        ),
    )
    for invalid in (no_event, wrong_nl):
        assert bound(reference.bind((early, invalid))) == {early.owner_id}
    other_rt = with_peak(late, apex_rt=9.5, start_rt=9.4, end_rt=9.6)
    other_rt = replace(
        other_rt,
        hypothesis=replace(other_rt.hypothesis, hypothesis_id="different-peak"),
    )
    assert bound(reference.bind((early, other_rt))) == {early.owner_id}
    ambiguous = reference.bind(
        (
            early,
            late,
            replace(late, hypothesis=replace(late.hypothesis, hypothesis_id="overlap")),
        )
    )
    assert bound(ambiguous) == {early.owner_id}
    workbook["XIC Results"]["C5"] = "ND"
    workbook.save(path)
    partial = read_known_istd_reference(path).bind((early, late))
    assert bound(partial) == {early.owner_id}
    workbook["Targets"].append(["second", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50])
    workbook["XIC Results"].append(["early", "second", 8, 1])
    workbook.save(path)
    two_targets = read_known_istd_reference(path).bind((early, late))
    assert not two_targets.bindings


_LANDMARK_RTS = (8, 12, 16, 20, 24, 28, 32, 40)


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
            results.append([sample, f"landmark-{index}", rt, 100])
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
    frame = SampleRtFrame(
        injections=InjectionSequence(orders), sample_universe=samples
    ).with_sources(known_istd_reference=reference)
    frame = frame.with_sample_calibration(
        BatchRtCalibrationAccumulator(
            sample_stems=tuple(samples), sample_cohorts=dict.fromkeys(samples, "batch")
        ).freeze(measured_istds=frame.measured_istd_coordinates)
    )
    for sample, observed in (("early", 16), ("middle", 16.2), ("late", 16.4)):
        assert frame.common_rt_min(sample, observed) == pytest.approx(16.2)
        assert 16.2 + frame.offset_min(sample, 16.2) == pytest.approx(observed)
    assert frame.common_rt_min("middle", 3.0) == 3.0
    derived = SampleRtFrame().with_calibration(frame)
    assert derived.common_rt_min("late", 16.4) == pytest.approx(16.2)
