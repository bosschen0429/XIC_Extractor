"""CID-NL Backfill across several groups, through the formal Untargeted entry.

Scenario tests persist Discovery results, replace only the Thermo RAW boundary
and assert what ``run_untargeted_application`` and the replay CLI publish.
Expected values follow from the synthetic RAW: Gaussian centres give apex RTs,
and a sample without an admitted Discovery peak is filled only by Backfill.

Two tests keep their runtime seams. The shape-risk test exercises the Backfill
admission algorithm on in-memory domain models. The query-RT test injects a
batch RT warp, which the formal entry learns only from data, to show that a
Backfill query follows each sample's own RT rather than the group centre.
"""

import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.alignment_pipeline_helpers import written_cells
from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_support_policy,
    cid_nl_unique_support,
)
from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _FakeThermoRaw,
    _multi_tag_settings,
    _settings,
)
from tests.cid_nl_runtime_fixtures import install_runtime_raws
from tests.owner_peak_fixtures import trace_peak
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.untargeted_alignment_entry_fixtures import (
    SETTINGS,
    discover,
    publish_alignment,
    published_cells,
    published_matrix,
    raw_paths,
    read_tsv,
    sample_metadata_for,
)
from tests.untargeted_cid_nl_real_data_adapter_fixtures import _without_ms1_peak
from tests.untargeted_partition_fixtures import (
    build_dense_partition,
    complete_dense_partition,
    execute_partition_inline,
)
from xic_extractor.alignment.band_grouping import group_peak_bands
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.neighborhood_graph import (
    CrossSamplePartitionTransition,
)
from xic_extractor.alignment.grouping_workflow import (
    admit_cid_nl_group_backfills,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.diagnostics.stage_snapshot import load_snapshot
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.injection_timeline import InjectionSequence
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    mint_cid_nl_method_assessment,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.partition.models import (
    BACKFILL_HALF_WINDOW_MIN,
)

ALIGNMENT = AlignmentConfig()


def _gaussian_raw(
    ms1_centers: tuple[float, ...],
    nl_centers: tuple[float, ...] | None = None,
    *,
    rt_range: tuple[float, float, int] = (7.4, 8.7, 521),
    width: float = 0.035,
    height: float = 1000.0,
) -> _FakeRaw:
    """MS1 Gaussians at ``ms1_centers``; CID-NL scans at ``nl_centers``."""
    raw = _FakeRaw(ms1_centers if nl_centers is None else nl_centers)
    raw.rt = np.linspace(*rt_range)
    raw.intensity = sum(
        (
            height * np.exp(-0.5 * ((raw.rt - center) / width) ** 2)
            for center in ms1_centers
        ),
        np.zeros_like(raw.rt),
    )
    raw.scans = tuple(
        replace(scan, scan_number=int(np.argmin(np.abs(raw.rt - scan.rt))) + 1)
        for scan in raw.scans
    )
    return raw


def _cells_of(output, stem):
    return {
        feature: cell
        for (feature, sample), cell in published_cells(output).items()
        if sample == stem
    }


def _cells_at_supported_rt(output, stem):
    rows = published_cells(output)
    apexes = {}
    for (feature, _), cell in rows.items():
        if cell["status"] == "detected":
            apexes.setdefault(feature, []).append(float(cell["apex_rt"]))
    return {
        round(sum(apexes[feature]) / len(apexes[feature]), 1): cell
        for (feature, sample), cell in rows.items()
        if sample == stem
    }


@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize("with_order", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_backfill_batch_retains_shape_contradictions_as_soft_risk(
    tmp_path,
    conflicting,
    with_order,
    reverse,
):
    widths = {
        "reference1": 0.07,
        "reference2": 0.07,
        "narrow": 0.03,
        "other": 0.25 if conflicting else 0.05,
        "compatible": 0.07,
    }
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=tuple(widths),
    )
    owners, assessments = {}, {}
    offsets = np.linspace(-0.6, 0.6, 121)
    for name, width in widths.items():
        sample = fixture.sample(name)
        values = 1000 * np.exp(-0.5 * (offsets / width) ** 2)
        source = fixture.source(sample)
        trace = Trace.from_arrays(
            sample_name=name,
            mz=500.0,
            rt=offsets + 8,
            intensity=values,
            rt_min=7.4,
            rt_max=8.6,
            ppm_tol=10.0,
            source="synthetic_gaussian",
            acquisition_source_id=source.acquisition_source_id,
        )
        peak = trace_peak(
            sample=sample,
            trace=trace,
            integration=IntegrationResult(
                rt_left_min=7.4,
                rt_apex_min=8.0,
                rt_right_min=8.6,
                raw_apex_rt_min=8.0,
                rt_width_min=1.2,
                height_raw=1000.0,
                height_smoothed=1000.0,
                area_raw_counts_seconds=float(np.trapezoid(values, offsets) * 60),
                integration_method="raw_trapezoid",
                boundary_sources=("candidate_interval", "gaussian15"),
                raw_scan_indices=tuple(range(len(offsets))),
            ),
            proposal_sources=("local_minimum", "gaussian15"),
        )
        evidence = cid_nl_evidence(fixture, name, scan_number=101, scan_rt_min=8.0)
        support = cid_nl_unique_support(fixture, name, peak=peak, evidence=evidence)
        assessment = mint_cid_nl_method_assessment(
            sample=sample,
            acquisition_source=fixture.source(sample),
            target=fixture.target,
            detector_invocation_id=f"gaussian15:{name}",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=7.4,
                query_rt_max=8.6,
                support_policy=cid_nl_support_policy(),
                acquisition_supported=True,
                search_completed=True,
                reason_code=None,
            ),
            candidate_peaks=(peak,),
            evidence_refs=(evidence,),
            individual_support_refs=(support,),
        )
        assert assessment.method_result == "SUPPORT"
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            sample=sample,
            assessment=assessment,
        )
        owners[name] = owner_from_cid_nl_assessment_support(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        assessments[name] = assessment

    config = AlignmentConfig()
    frame = SampleRtFrame(
        injections=InjectionSequence(
            {name: index for index, name in enumerate(widths, 1)} if with_order else {}
        )
    )
    references = (owners["reference1"], owners["reference2"])
    (group,) = group_peak_bands(
        references,
        config=config,
        rt_frame=frame,
        known_identity_bindings=tuple(
            (owner.owner_id, "target") for owner in references
        ),
    )
    assert group.disposition == "established"
    identity = group
    initial = CrossSampleGroupingResult(
        samples=fixture.samples,
        rt_frame=frame,
        partition_transition=CrossSamplePartitionTransition(
            status="assessable",
            predecessors=(identity,),
            successors=(identity,),
            lineage=(),
            reason="unchanged_membership_no_lineage",
        ),
    )
    backfills = {}
    for name in ("narrow", "other", "compatible"):
        backfills[
            (
                group.group_hypothesis_id,
                fixture.sample(name).sample_name,
                assessments[name].method_subject.method_subject_id,
            )
        ] = (fixture.sample(name), assessments[name])
    if reverse:
        backfills = dict(reversed(tuple(backfills.items())))
    grouping, (successor,), successor_ids, retained, _ = admit_cid_nl_group_backfills(
        result=initial,
        groups=(group,),
        backfills=backfills,
        alignment_config=config,
        member_observations={
            peak.physical_identity: peak
            for assessment in assessments.values()
            for peak in assessment.candidate_peaks
        },
    )
    accepted = {"narrow", "other", "compatible"}
    if conflicting:
        assert any(
            "shape_disagreement" in risk.soft_contradictions
            for _, _, risk in grouping.backfill_membership_risks
        )
    assert {owner.sample_stem for owner in successor.owners} == (
        {"reference1", "reference2"} | accepted
    )
    assert set(references).issubset(successor.owners)
    assert successor_ids == {group.group_hypothesis_id: successor.group_hypothesis_id}
    assert set(grouping.partition_transition.admitted_members) == {
        owners[name].cross_sample_member_subject for name in accepted
    }
    assert set(retained) == {assessments[name] for name in accepted}
    assert {
        hypothesis.hypothesis_id
        for values in retained.values()
        for hypothesis in values
    } == {owners[name].owner_id for name in accepted}


@pytest.mark.parametrize("empty", [False, True])
def test_backfill_query_and_empty_completion_keep_sample_rt_separate_from_group(
    tmp_path, monkeypatch, empty
):
    from xic_extractor.alignment.cid_nl_initial_grouping import regroup_cid_nl_grouping
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
    from xic_extractor.untargeted_execution.partition_composition import (
        group_acquired_partition,
    )

    shifts = {"S1": 0.0, "S2": 0.0, "Late": 0.4, "Early": -0.4}
    settings = _settings()
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in shifts)
    raws, candidates = {}, {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw = _FakeRaw((8.0 + shifts[path.stem],))
        candidates[path.stem] = (
            _current_candidates(
                raw,
                raw_file=path,
                settings=settings,
            )
            if path.stem.startswith("S")
            else ()
        )
        if not path.stem.startswith("S"):
            raw.scans = ()
            if empty:
                raw.intensity[:] = 0.0
        raws[path] = raw
    batch = build_dense_partition(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=i,
                sample_role="sample",
            )
            for i, path in enumerate(paths, 1)
        ),
        settings=settings,
        peaks_by_sample=candidates,
        dll_dir=tmp_path,
        signal_processing_profile=TEST_PROFILE,
    )
    install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    config = AlignmentConfig()
    calibration = BatchRtCalibration.from_coordinate_knots(
        tuple(
            (stem, ((7.0 + offset, 7.0), (20.0 + offset, 20.0)))
            for stem, offset in shifts.items()
        )
    )

    def prepare(initial):
        bootstrap = group_acquired_partition(
            batch,
            initial,
            alignment_config=config,
            bootstrap_only=True,
        )
        return regroup_cid_nl_grouping(
            bootstrap, rt_frame=SampleRtFrame().with_sample_calibration(calibration)
        )

    result = execute_partition_inline(
        batch.jobs,
        partition_key="runtime-fixture",
        alignment_config=config,
        prepare_grouping=prepare,
    )
    assert result.succeeded
    for sample_result in result.sample_results:
        stem = sample_result.job.sample.raw_stem
        if stem.startswith("S"):
            continue
        expected = 8.0 + shifts[stem]
        if not empty:
            assert not sample_result.backfill_attempts
            (peak,) = (
                sample_result.adapter_result.assessment_input.assessment.candidate_peaks
            )
            assert peak.integration.rt_apex_min == pytest.approx(expected)
            continue
        (attempt,) = sample_result.backfill_attempts
        assert attempt.query_rt == pytest.approx(expected)
        assert attempt.query_rt_basis == "calibrated_trajectory_fit"
        receipt = attempt.assessment.evaluation_receipt
        assert (receipt.query_rt_min, receipt.query_rt_max) == pytest.approx(
            (expected - BACKFILL_HALF_WINDOW_MIN, expected + BACKFILL_HALF_WINDOW_MIN)
        )
        assert not attempt.assessment.candidate_peaks
    completed = complete_dense_partition(batch, result, alignment_config=config)
    writer = completed.application_result
    for _row, sample_stem, cell in written_cells(writer):
        if sample_stem not in {"Early", "Late"}:
            continue
        apex = 8.0 + shifts[sample_stem]
        if empty:
            assert cell.value is None
            assert cell.backfill_query_bounds == pytest.approx(
                (apex - BACKFILL_HALF_WINDOW_MIN, apex + BACKFILL_HALF_WINDOW_MIN)
            )
        else:
            assert cell.value is not None
            assert cell.selected_integration.rt_apex_min == pytest.approx(apex)
            assert cell.backfill_query_bounds == (None, None)


@pytest.mark.parametrize(
    "outcome", ["support", "empty", "below-quorum", "blank", "excluded", "failed"]
)
def test_rejected_discovery_input_cannot_supply_backfill_support(
    tmp_path, monkeypatch, outcome
):
    """A review-only Discovery candidate supplies no support; a new physical
    observation may be measured but cannot bypass sample role or support quorum.
    """
    stems = ("S1", "S2", "Rejected")
    paths = raw_paths(tmp_path, stems)
    raws = {stem: _FakeRaw((8.0,)) for stem in stems}
    peaks = {stem: discover(raws[stem], paths[stem]) for stem in stems}
    peaks["Rejected"] = tuple(_without_ms1_peak(peak) for peak in peaks["Rejected"])
    assert peaks["Rejected"]
    assert not any(peak.admitted for peak in peaks["Rejected"])
    if outcome == "empty":
        raws["Rejected"].intensity[:] = 0.0
    context_stem = {
        "blank": "Rejected",
        "excluded": "Rejected",
        "below-quorum": "S2",
    }.get(outcome)
    metadata = sample_metadata_for(
        stems,
        **(
            {
                context_stem: SampleMetadata(
                    sample_name=context_stem,
                    raw_stem=context_stem,
                    injection_order=stems.index(context_stem) + 1,
                    sample_role="sample" if outcome == "excluded" else "blank",
                    excluded=outcome == "excluded",
                    exclusion_reason="operator exclusion"
                    if outcome == "excluded"
                    else "",
                )
            }
            if context_stem
            else {}
        ),
    )
    if outcome == "failed":
        read = _FakeThermoRaw.GetChromatogramData

        def fail_rejected(self, *args):
            if self.source is raws["Rejected"]:
                raise OSError("recovery acquisition failed")
            return read(self, *args)

        monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", fail_rejected)
    opened: set[str] = set()
    run = dict(
        paths=paths,
        raws=raws,
        peaks=peaks,
        alignment_config=ALIGNMENT,
        sample_metadata=metadata,
        opened_samples=opened,
    )
    if outcome == "failed":
        with pytest.raises(Exception, match="recovery acquisition failed"):
            publish_alignment(tmp_path, monkeypatch, **run)
        assert not (tmp_path / "alignment" / "alignment_matrix.tsv").exists()
        return

    output = publish_alignment(tmp_path, monkeypatch, **run)

    cells = published_cells(output)
    rejected_ids = {peak.peak_id for peak in peaks["Rejected"]}
    assert not rejected_ids & {cell["source_candidate_id"] for cell in cells.values()}
    (rejected,) = (cell for (_f, stem), cell in cells.items() if stem == "Rejected")
    assert rejected["source_candidate_id"] == ""
    (matrix_row,) = read_tsv(output / "alignment_matrix.tsv")
    if outcome == "support":
        assert rejected["status"] == "backfilled"
        assert float(rejected["area"]) > 0
        assert float(matrix_row["Rejected"]) == pytest.approx(
            float(rejected["area"]), rel=1e-5
        )
    elif outcome == "empty":
        assert rejected["status"] == "unavailable"
        assert rejected["area"] == matrix_row["Rejected"] == ""
    else:
        assert rejected["status"] == "unresolved"
        assert rejected["area"] == matrix_row["Rejected"] == ""
    assert "Rejected" in opened


@pytest.mark.parametrize(
    "recovery_peaks",
    [(8.0, 12.0), (8.0,), ()],
    ids=["positive-positive", "positive-empty", "empty-empty"],
)
@pytest.mark.parametrize(
    "shared_support",
    ["separate", "shared", "below-quorum"],
    ids=["separate-support", "multi-hypothesis-support", "one-unauthorized-group"],
)
def test_backfill_recovers_every_authorized_rt_group(
    tmp_path, monkeypatch, recovery_peaks, shared_support
):
    """One direct support does not authorize a Backfill of the 12 min group."""
    support = {"S1": (8.0,), "S2": (8.0,), "S3": (12.0,), "S4": (12.0,)}
    if shared_support == "shared":
        support = {"S1": (8.0, 12.0), "S2": (8.0, 12.0)}
    if shared_support == "below-quorum":
        support.pop("S4")
    authorized = (8.0,) if shared_support == "below-quorum" else (8.0, 12.0)
    centers = {**support, "Missing": recovery_peaks}
    settings = replace(SETTINGS, neutral_loss_profile=None, rt_max=12.6)
    paths = raw_paths(tmp_path, tuple(centers))
    raws = {
        stem: _gaussian_raw(values, rt_range=(7.4, 12.7, 2121))
        for stem, values in centers.items()
    }
    peaks = {stem: discover(raws[stem], paths[stem], settings) for stem in support}
    held = recovery_peaks == (8.0, 12.0) and shared_support == "separate"
    peaks["Missing"] = (
        tuple(
            _without_ms1_peak(peak)
            for peak in discover(raws["Missing"], paths["Missing"], settings)
        )
        if held
        else ()
    )

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=ALIGNMENT,
    )

    missing = _cells_at_supported_rt(output, "Missing")
    assert set(missing) == {8.0, 12.0}
    assert all(cell["source_candidate_id"] == "" for cell in missing.values())
    for center in authorized:
        cell = missing[center]
        if center in recovery_peaks:
            assert cell["status"] == "backfilled"
            assert float(cell["area"]) > 0
            assert float(cell["apex_rt"]) == pytest.approx(center, abs=0.01)
        else:
            assert cell["status"] == "unavailable"
            assert cell["area"] == ""
    if shared_support == "below-quorum":
        assert missing[12.0]["area"] == ""
        assert missing[12.0]["status"] == (
            "unresolved" if 12.0 in recovery_peaks else "not_searched"
        )


def _shared_peak_raws(mz_separation_ppm, observed=("S1", "S2")):
    """Two groups 0.12 min apart; Missing has one MS1 peak between them."""
    centers = {**dict.fromkeys(observed, (8.0, 8.12)), "Missing": (8.06,)}
    raws = {}
    for index, stem in enumerate(centers, start=1):
        raw = _gaussian_raw(
            centers[stem],
            width=0.018,
            height=1000 if stem == "Missing" else 1000 * index,
        )
        raw.scans = tuple(
            replace(
                scan,
                precursor_mz=scan.precursor_mz + shift,
                masses=scan.masses + shift,
            )
            for scan in raw.scans
            for shift in (
                500.0
                * mz_separation_ppm
                * 1e-6
                * (0.5 if stem == "Missing" else float(scan.rt > 8.06)),
            )
        )
        raws[stem] = raw
    return raws


def _shared_peak_scenario(tmp_path, mz_separation_ppm, *, observed=("S1", "S2")):
    settings = replace(SETTINGS, neutral_loss_profile=None, rt_max=8.6)
    raws = _shared_peak_raws(mz_separation_ppm, observed)
    paths = raw_paths(tmp_path, tuple(raws))
    peaks = {}
    for stem, raw in raws.items():
        discovered = discover(raw, paths[stem], settings)
        peaks[stem] = (
            discovered
            if stem != "Missing"
            else tuple(_without_ms1_peak(peak) for peak in discovered)
        )
    return paths, raws, peaks, settings


@pytest.mark.parametrize("mz_separation_ppm", [0.0, 6.0])
def test_shared_physical_recovery_claim_is_not_assigned_to_two_groups(
    tmp_path, monkeypatch, mz_separation_ppm
):
    """A shared physical peak must not be assigned to both groups."""
    paths, raws, peaks, settings = _shared_peak_scenario(tmp_path, mz_separation_ppm)

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=ALIGNMENT,
    )

    assert len(read_tsv(output / "alignment_matrix.tsv")) == 2
    missing = list(_cells_of(output, "Missing").values())
    assert sorted(cell["status"] for cell in missing) == ["backfilled", "unresolved"]
    assert sum(bool(cell["area"]) for cell in missing) == 1


@pytest.mark.parametrize("mz_separation_ppm", [0.0, 6.0, 10.6])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("sloped_background", [False, True])
def test_shared_ms1_only_backfill_peak_is_not_written_to_two_rows(
    tmp_path, monkeypatch, mz_separation_ppm, reverse, sloped_background
):
    """Overlapping extraction windows cannot supply one peak to two rows."""
    from xic_extractor.alignment_explanation.reader import read_review_evidence

    stems = ("S1", "S2", "Missing")
    if reverse:
        stems = tuple(reversed(stems))
    first_mz = 500.0
    second_mz = first_mz * (1 + mz_separation_ppm * 1e-6)
    settings = replace(SETTINGS, neutral_loss_profile=None, rt_max=8.6)
    paths = raw_paths(tmp_path, stems)
    raws, peaks = {}, {}
    for stem in stems:
        observed = stem != "Missing"
        slope = sloped_background and not observed
        raw = _gaussian_raw(
            (8.0, 8.12) if observed else (8.06,),
            None if observed else (),
            rt_range=(6.8, 9.4, 1041) if slope else (7.4, 8.7, 521),
            width=0.018,
        )
        if observed and second_mz != first_mz:
            raw.additional_ms1_ions = {
                second_mz: 1000.0 * np.exp(-0.5 * ((raw.rt - 8.12) / 0.018) ** 2)
            }
            raw.intensity = 1000.0 * np.exp(-0.5 * ((raw.rt - 8.0) / 0.018) ** 2)
        elif not observed:
            raw.ms1_mz = (first_mz + second_mz) / 2
            raw.additional_ms1_ions = {}
        if slope:
            raw.intensity = raw.intensity + 40.0 * (raw.rt - 6.8)
        raw.scans = tuple(
            replace(
                scan,
                precursor_mz=second_mz if scan.rt > 8.06 else first_mz,
                masses=scan.masses + (second_mz - first_mz if scan.rt > 8.06 else 0),
            )
            for scan in raw.scans
        )
        raws[stem] = raw
        found = discover(raw, paths[stem], settings) if observed else ()
        peaks[stem] = tuple(reversed(found)) if reverse else found

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=ALIGNMENT,
    )

    cells = published_cells(output)
    features = {feature for feature, _stem in cells}
    assert len(features) == 2
    for feature in features:
        assert [cells[(feature, stem)]["status"] for stem in ("S1", "S2")] == [
            "detected",
            "detected",
        ]
    missing = [cells[(feature, "Missing")] for feature in features]
    assert sorted(cell["status"] for cell in missing) == ["backfilled", "unresolved"]
    assert sum(bool(cell["area"]) for cell in missing) == 1

    evidence = read_review_evidence(output / "alignment_explanation.sqlite")
    competing = [
        (cell, risk)
        for group in evidence.groups.values()
        for cell in group.cells.values()
        for risk in cell["membership_risks"]
        if risk["phase"] == "initial"
        and "accepted_competition" in risk["risk_flags"]
        and cell["sample_key"] == "Missing"
    ]
    assert len(competing) == 1
    ((cell, risk),) = competing
    assert risk["alternative_partition"]
    if mz_separation_ppm == 0 and not reverse and not sloped_background:
        from xic_inspector.api import SelectionRequest, prepare_report

        payload = prepare_report(
            output / "alignment_explanation.sqlite",
            SelectionRequest(strategy="query", group_ids=(cell["feature_id"],)),
        ).payload
        flagged = {
            sample["sample_id"]
            for item in payload["items"]
            for sample in item["samples"]
            if any("可能" in name for name in sample["risk_labels"])
        }
        assert cell["sample_key"] in flagged


@pytest.mark.parametrize(
    "missing_peaks",
    [(8.0,), (7.8, 8.2)],
    ids=["one-ms1-peak", "two-ms1-peaks"],
)
@pytest.mark.parametrize("composition", [None, "union", "intersection"])
def test_batch_ms1_only_publishes_only_one_selected_peak_in_the_established_row(
    tmp_path, monkeypatch, missing_peaks, composition
):
    """The Missing peak has the supports' shape, so it integrates alike."""
    centers = {"S1": (8.0,), "S2": (8.0,), "Missing": missing_peaks}
    settings = (
        replace(
            _multi_tag_settings(),
            tag_combine_mode=composition,
            mass_recalibration=SETTINGS.mass_recalibration,
        )
        if composition
        else replace(SETTINGS, neutral_loss_profile=None, rt_max=8.6)
    )
    paths = raw_paths(tmp_path, tuple(centers))
    raws, peaks = {}, {}
    for stem, values in centers.items():
        raw = _gaussian_raw(values, () if composition and stem == "Missing" else None)
        if composition == "intersection" and stem != "Missing":
            dual = _FakeRaw((7.98, 8.02), neutral_loss_das=(116.0474, 132.0423))
            raw.scans = tuple(
                replace(scan, scan_number=int(np.argmin(np.abs(raw.rt - scan.rt))) + 1)
                for scan in dual.scans
            )
        raws[stem] = raw
        peaks[stem] = discover(raw, paths[stem], settings) if stem != "Missing" else ()

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=ALIGNMENT,
        output_level="validation-minimal",
    )

    (matrix_row,) = read_tsv(output / "alignment_matrix.tsv")
    missing = _cells_of(output, "Missing")
    (feature,) = published_matrix(output)
    cell = missing[feature]
    assert len(missing) == len(missing_peaks)
    assert cell["status"] == "backfilled"
    assert cell["source_candidate_id"] == ""
    support_area = float(published_cells(output)[(feature, "S1")]["area"])
    assert float(cell["area"]) == pytest.approx(support_area, rel=0.05)
    assert float(matrix_row["Missing"]) == pytest.approx(float(cell["area"]), rel=1e-5)
    assert any(
        float(cell["apex_rt"]) == pytest.approx(apex, abs=0.01)
        for apex in missing_peaks
    )
    if len(missing_peaks) > 1:
        from xic_extractor.alignment_explanation.reader import read_review_evidence

        group = read_review_evidence(output / "alignment_explanation.sqlite").groups[
            feature
        ]
        assert "accepted_competition" in group.risk_flags


def test_backfill_reaches_a_sample_whose_support_belongs_to_another_group(
    tmp_path, monkeypatch
):
    ms1_centers = {"S1": (8.0,), "S2": (8.0,), "Missing": (8.0, 9.6)}
    nl_centers = {"S1": (8.0,), "S2": (8.0,), "Missing": (9.6,)}
    settings = replace(SETTINGS, neutral_loss_profile=None, rt_max=10.1)
    paths = raw_paths(tmp_path, tuple(ms1_centers))
    raws = {
        stem: _gaussian_raw(
            ms1_centers[stem], nl_centers[stem], rt_range=(7.4, 10.2, 1121)
        )
        for stem in ms1_centers
    }
    peaks = {stem: discover(raws[stem], paths[stem], settings) for stem in raws}
    (own,) = peaks["Missing"]

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=ALIGNMENT,
    )

    missing = _cells_at_supported_rt(output, "Missing")
    assert missing[8.0]["status"] == "backfilled"
    assert float(missing[8.0]["area"]) > 0
    assert missing[8.0]["source_candidate_id"] == ""
    assert missing[9.6]["status"] == "detected"
    assert missing[9.6]["source_candidate_id"] == own.peak_id


def test_alignment_uses_configured_recovery_groups(tmp_path, monkeypatch):
    """Only the established band requests a Backfill; a distant singleton cannot."""
    centers = {"S1": (8.0,), "S2": (8.0,), "S3": (8.52,), "Missing": ()}
    settings = replace(SETTINGS, neutral_loss_profile=None, rt_max=12.6)
    paths = raw_paths(tmp_path, tuple(centers))
    raws = {
        stem: _gaussian_raw(values, rt_range=(7.4, 12.7, 2121))
        for stem, values in centers.items()
    }
    for stem in ("S1", "S2"):
        raws[stem].scans = tuple(
            replace(
                raws[stem].scans[0],
                rt=rt,
                scan_number=int(np.argmin(np.abs(raws[stem].rt - rt))) + 1,
            )
            for rt in (7.99, 8.01)
        )
    peaks = {stem: discover(raws[stem], paths[stem], settings) for stem in centers}

    output = publish_alignment(
        tmp_path,
        monkeypatch,
        paths=paths,
        raws=raws,
        peaks=peaks,
        settings=settings,
        alignment_config=AlignmentConfig(preferred_rt_sec=2.0),
    )

    rows = published_cells(output)
    missing = {
        str(
            sum(r["status"] == "detected" for (f, _), r in rows.items() if f == feature)
        ): cell
        for feature, cell in _cells_of(output, "Missing").items()
    }
    assert set(missing) == {"1", "2"}
    assert missing["2"]["status"] == "unavailable"
    assert missing["1"]["status"] == "not_searched"
    assert all(cell["area"] == "" for cell in missing.values())


def _shared_peak_inputs(
    tmp_path, monkeypatch, mz_separation_ppm, *, observed=("S1", "S2")
):
    paths, raws, peaks, settings = _shared_peak_scenario(
        tmp_path, mz_separation_ppm, observed=observed
    )
    install_runtime_raws(
        monkeypatch,
        sources_by_path={paths[stem]: raws[stem] for stem in paths},
        tmp_path=tmp_path,
    )
    return dict(
        raw_paths=tuple(paths.values()),
        sample_metadata=sample_metadata_for(tuple(paths)),
        settings=settings,
        peaks_by_sample=peaks,
    )


def _run_backfill_cli(root, directory, output):
    """Run in a fresh interpreter, rereading the fixture's synthetic RAWs."""
    from scripts.run_alignment import main
    from xic_extractor.untargeted_execution.partition import workers

    root = Path(root)
    raws = _shared_peak_raws(0.0)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
        install_runtime_raws(
            patch,
            sources_by_path={
                root / "raw" / f"{stem}.raw": raw for stem, raw in raws.items()
            },
            tmp_path=root,
        )
        return main(["backfill", "--inputs", directory, "--output-dir", output])


def _backfill_request(tmp_path, options, config):
    from tests.cid_nl_runtime_fixtures import persist_discovery_batch
    from xic_extractor.applications.untargeted.request import UntargetedRequest
    from xic_extractor.configuration.execution_profile import UntargetedExecutionPolicy
    from xic_extractor.discovery.csv_reader import DiscoveryBatchIndex

    batch = persist_discovery_batch(
        raw_paths=options["raw_paths"],
        settings=options["settings"],
        peaks_by_sample=options["peaks_by_sample"],
        root=tmp_path / "batch",
    )
    return UntargetedRequest(
        discovery_batch_index=DiscoveryBatchIndex(
            path=tmp_path / "batch" / "index.csv",
            sample_order=batch.sample_order,
            raw_files=batch.raw_files,
            peak_csvs=batch.peak_csvs,
            event_csvs=batch.event_csvs,
            peak_receipts=batch.peak_receipts,
        ),
        sample_metadata=options["sample_metadata"],
        alignment_config=config,
        raw_dir=tmp_path / "raw",
        dll_dir=tmp_path,
        output_dir=tmp_path / "grouping",
        discovery_settings=options["settings"],
        execution=UntargetedExecutionPolicy(
            profile=ExecutionProfile(alignment_workers=1, backend="thread")
        ),
    )


def test_full_alignment_captures_competing_rows_and_single_batch_matrix(
    tmp_path, monkeypatch, capsys
):
    """Capturing replay inputs does not repeat any stage. A single batch captures no
    pairing input. S1 and S2 each own one peak of both groups. The cells TSV
    publishes areas to two decimals.
    """
    import subprocess
    import sys
    from collections import Counter, defaultdict

    from scripts.run_alignment import main
    from xic_extractor.applications.untargeted import application
    from xic_extractor.applications.untargeted.publication import (
        UntargetedPublicationPolicy,
    )
    from xic_extractor.applications.untargeted.request import (
        AlignmentReplayOptions,
        UntargetedExecutionContext,
    )

    options = _shared_peak_inputs(tmp_path, monkeypatch, 0.0)
    request = _backfill_request(tmp_path, options, AlignmentConfig())
    control_dir = tmp_path / "control"
    capture_dir = tmp_path / "capture"

    def run(changed_request):
        recorder = TimingRecorder("alignment")
        application.run_untargeted_application(
            changed_request, UntargetedExecutionContext(timing_recorder=recorder)
        )
        stages = Counter((r.stage, r.sample_stem) for r in recorder.records)
        assert [stage for stage, count in stages.items() if count > 1] == []

    run(
        replace(
            request,
            output_dir=control_dir,
            publication=UntargetedPublicationPolicy(emit_alignment_cells=True),
        )
    )
    run(
        replace(
            request,
            output_dir=capture_dir,
            replay=AlignmentReplayOptions(grouping=True, backfill=True, matrix=True),
        )
    )
    replay_names = {
        "grouping-inputs.json.gz",
        "backfill-inputs",
        "pairing-inputs",
        "matrix-cells.pickle.gz",
        "matrix-join.pickle.gz",
    }
    assert not replay_names.intersection(path.name for path in control_dir.iterdir())
    assert replay_names.intersection(path.name for path in capture_dir.iterdir()) == {
        "grouping-inputs.json.gz",
        "backfill-inputs",
        "matrix-cells.pickle.gz",
    }

    def replay(stage, *args):
        out = tmp_path / stage
        assert main([stage, *map(str, args), "--output-dir", str(out)]) == 0
        return out

    matrix = replay("matrix", "--backfill", capture_dir / "matrix-cells.pickle.gz")
    filename = "alignment_matrix.tsv"
    assert list(matrix.iterdir()) == [matrix / filename]
    assert (
        (matrix / filename).read_bytes()
        == (capture_dir / filename).read_bytes()
        == (control_dir / filename).read_bytes()
    )
    matrix_rows = read_tsv(matrix / filename)
    assert len(matrix_rows) == 2
    assert all(float(row["S1"]) < float(row["S2"]) for row in matrix_rows)
    assert sorted(bool(row["Missing"]) for row in matrix_rows) == [False, True]

    regrouped = replay("regroup", "--inputs", capture_dir / "grouping-inputs.json.gz")
    groups = defaultdict(set)
    for row in read_tsv(regrouped / "members.tsv"):
        groups[row["group_id"]].add(row["owner_id"])
    assert sorted(map(len, groups.values())) == [2, 3]
    assert (
        sum(
            owner.startswith("Missing|ms1_physical_observation|")
            for owners in groups.values()
            for owner in owners
        )
        == 1
    )

    directory = capture_dir / "backfill-inputs"
    backfilled = tmp_path / "backfill"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from tests.test_cid_nl_multi_group_recovery import "
            "_run_backfill_cli as run; sys.exit(run(*sys.argv[1:]))",
            str(tmp_path),
            str(directory),
            str(backfilled),
        ],
        check=True,
        timeout=60,
    )

    def published(rows):
        return sorted(
            (
                row["sample_stem"],
                row["status"],
                float(row["area"]) if row["area"] else 0,
            )
            for row in rows
        )

    actual = published(read_tsv(backfilled / "backfill-cells.tsv"))
    expected = published(read_tsv(control_dir / "alignment_cells.tsv"))
    assert [item[:2] for item in actual] == [item[:2] for item in expected]
    assert [item[2] for item in actual] == pytest.approx(
        [item[2] for item in expected], abs=0.006
    )
    missing = [(status, area) for stem, status, area in actual if stem == "Missing"]
    assert sorted(status for status, _area in missing) == ["backfilled", "unresolved"]
    assert sum(area > 0 for _status, area in missing) == 1

    from xic_extractor.diagnostics.stage_snapshot import save_snapshot
    from xic_extractor.untargeted_execution.execution import FrozenBackfillPartition

    before_questions = tmp_path / "capture-before-questions"
    shutil.copytree(directory, before_questions)
    partition = load_snapshot(before_questions / "0.pickle.gz")
    with monkeypatch.context() as patch:
        patch.setattr(
            FrozenBackfillPartition,
            "__getstate__",
            lambda frozen: [frozen.batch, frozen.grouping, frozen.initial_results],
        )
        save_snapshot(before_questions / "0.pickle.gz", partition)
    capsys.readouterr()
    output = tmp_path / "rejected-before-questions"
    assert (
        main(
            ["backfill", "--inputs", str(before_questions), "--output-dir", str(output)]
        )
        == 2
    )
    assert "capture it again" in capsys.readouterr().err

    (directory / "0.pickle.gz").write_bytes(b"not the captured partition")
    rejected = tmp_path / "rejected"
    assert (
        main(["backfill", "--inputs", str(directory), "--output-dir", str(rejected)])
        == 2
    )
    assert not (rejected / "backfill-cells.tsv").exists()

    earlier = tmp_path / "earlier-capture"
    earlier.mkdir()
    (earlier / "0.bin").write_bytes(b"partition from the previous capture format")
    assert (
        main(["backfill", "--inputs", str(earlier), "--output-dir", str(rejected)]) == 2
    )
    assert not (rejected / "backfill-cells.tsv").exists()


def test_backfill_replay_projects_each_partition_before_the_next(tmp_path, monkeypatch):
    """A replayed partition's composition goes into the Matrix input as soon as
    it completes; only its cell rows wait for the partitions before it."""
    from datetime import datetime

    from xic_extractor.applications.untargeted import application
    from xic_extractor.diagnostics import backfill_replay
    from xic_extractor.diagnostics.matrix_replay import CompletedMatrixCells

    options = _shared_peak_inputs(
        tmp_path, monkeypatch, 0.0, observed=("S1", "S2", "S3", "S4")
    )
    options["sample_metadata"] = tuple(
        replace(sample, batch_id="B" if sample.raw_stem in {"S3", "S4"} else "A")
        for sample in options["sample_metadata"]
    )
    request = _backfill_request(tmp_path, options, AlignmentConfig())
    monkeypatch.setattr(
        application,
        "latest_acquisition_reader",
        lambda *a, **kw: lambda batch: datetime(2026, 1, 1),
    )
    application.run_untargeted_grouping(request, save_backfill_inputs=True)
    events = []
    replay_partition = backfill_replay._replay_partition
    append = CompletedMatrixCells.append

    def replayed(task):
        events.append("replay")
        return replay_partition(task)

    def projected(cells, *args):
        events.append("project")
        return append(cells, *args)

    monkeypatch.setattr(backfill_replay, "_replay_partition", replayed)
    monkeypatch.setattr(CompletedMatrixCells, "append", projected)
    backfill_replay.replay_backfill(
        request.output_dir / "backfill-inputs",
        tmp_path / "replayed",
        execution=ExecutionProfile(alignment_workers=1, backend="thread"),
        save_matrix_inputs=True,
    )
    assert len(events) >= 4
    assert events == ["replay", "project"] * (len(events) // 2)


def test_matrix_preserves_empty_batch_sample_column(tmp_path, monkeypatch):
    import csv
    from datetime import datetime

    from scripts.run_alignment import main
    from xic_extractor.applications.untargeted import application
    from xic_extractor.applications.untargeted.request import AlignmentReplayOptions

    options = _shared_peak_inputs(tmp_path, monkeypatch, 0.0)
    options["peaks_by_sample"]["Missing"] = ()
    options["sample_metadata"] = tuple(
        replace(s, batch_id="empty" if s.raw_stem == "Missing" else "observed")
        for s in options["sample_metadata"]
    )
    request = _backfill_request(tmp_path, options, AlignmentConfig())
    monkeypatch.setattr(
        application,
        "latest_acquisition_reader",
        lambda *a, **kw: lambda batch: datetime(2026, 1, 1),
    )
    application.run_untargeted_grouping(
        request, save_backfill_inputs=True, save_pairing_inputs=True
    )

    def replay(stage, *args):
        out = tmp_path / stage
        assert main([stage, *map(str, args), "--output-dir", str(out)]) == 0
        return out

    captured = request.output_dir
    backfill = replay(
        "backfill", "--inputs", captured / "backfill-inputs", "--save-matrix-inputs"
    )
    cells = backfill / "matrix-cells.pickle.gz"
    empty = {source.batch_id: source for source in load_snapshot(cells)}["empty"]
    assert [
        (sample.raw_stem, sample.injection_order) for sample in empty.canonical_samples
    ] == [("Missing", 3)]
    assert [group for child in empty.child_outputs for group in child.groups] == []

    pair = replay(
        "pair-batches",
        "--inputs",
        *sorted((captured / "pairing-inputs").glob("*.pickle.gz")),
    )
    matrix = replay(
        "matrix", "--backfill", cells, "--pairing", pair / "matrix-join.pickle.gz"
    )
    with (matrix / "alignment_matrix.tsv").open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert rows and all(row["Missing"] == "" for row in rows)
    formal = tmp_path / "formal"
    application.run_untargeted_application(
        replace(request, output_dir=formal, replay=AlignmentReplayOptions(matrix=True))
    )
    assert not any(
        (formal / name).exists()
        for name in ("grouping-inputs.json.gz", "backfill-inputs", "pairing-inputs")
    )
    direct = tmp_path / "matrix-direct"
    assert (
        main(
            [
                "matrix",
                "--backfill",
                str(formal / "matrix-cells.pickle.gz"),
                "--pairing",
                str(formal / "matrix-join.pickle.gz"),
                "--output-dir",
                str(direct),
            ]
        )
        == 0
    )
    assert (
        (formal / "alignment_matrix.tsv").read_bytes()
        == (matrix / "alignment_matrix.tsv").read_bytes()
        == (direct / "alignment_matrix.tsv").read_bytes()
    )


def test_matrix_joins_independently_captured_named_batches(tmp_path, monkeypatch):
    import csv
    from datetime import datetime

    from scripts.run_alignment import main
    from xic_extractor.alignment.drift_evidence import (
        mint_known_istd_reference_from_measurements,
    )
    from xic_extractor.applications.untargeted import application
    from xic_extractor.applications.untargeted.request import AlignmentReplayOptions
    from xic_extractor.configuration.models import Target
    from xic_extractor.untargeted_execution.batch_split import split_discovery_batch

    options = _shared_peak_inputs(
        tmp_path, monkeypatch, 0.0, observed=("S1", "S2", "S3", "S4")
    )
    samples = tuple(
        replace(sample, batch_id="B" if sample.raw_stem in {"S3", "S4"} else "A")
        for sample in options["sample_metadata"]
    )
    options["sample_metadata"] = samples
    target = Target(
        label="shared",
        mz=500.0,
        rt_min=7.96,
        rt_max=8.04,
        ppm_tol=20,
        neutral_loss_da=116.0474,
        nl_ppm_warn=20,
        nl_ppm_max=20,
        is_istd=True,
        istd_pair="",
    )
    reference = mint_known_istd_reference_from_measurements(
        (target,), {("shared", stem): 8.0 for stem in ("S1", "S2", "S3", "S4")}
    )
    request = replace(
        _backfill_request(tmp_path, options, AlignmentConfig()),
        known_istd_reference=reference,
    )
    monkeypatch.setattr(
        application,
        "latest_acquisition_reader",
        lambda *a, **kw: lambda batch: datetime(2026, 1, 1),
    )

    def replay(stage, *args):
        out = tmp_path / stage
        assert main([stage, *map(str, args), "--output-dir", str(out)]) == 0
        return out

    captures = []
    for batch_id, batch_index in split_discovery_batch(
        request.discovery_batch_index, samples
    ):
        out = tmp_path / batch_id
        application.run_untargeted_application(
            replace(
                request,
                discovery_batch_index=batch_index,
                sample_metadata=tuple(s for s in samples if s.batch_id == batch_id),
                output_dir=out,
                replay=AlignmentReplayOptions(matrix=True, pairing=True),
            )
        )
        assert not (out / "matrix-join.pickle.gz").exists()
        captures.append(out)

    pair_inputs = [out / "pairing-inputs" / "batch-1.pickle.gz" for out in captures]
    cells = [out / "matrix-cells.pickle.gz" for out in captures]
    paired = replay("pair-batches", "--inputs", *pair_inputs)
    matrix = replay(
        "matrix", "--backfill", *cells, "--pairing", paired / "matrix-join.pickle.gz"
    )

    for pair_input, cell_input in zip(pair_inputs, cells, strict=True):
        batch, _, _ = load_snapshot(pair_input)
        (source,) = load_snapshot(cell_input)
        assert source.batch_id == batch.batch_id
        assert {
            source.feature_key(group)
            for child in source.child_outputs
            for group in child.groups
        } == {feature.key for feature in batch.features}

    formal = tmp_path / "formal"
    application.run_untargeted_application(replace(request, output_dir=formal))
    replayed = matrix / "alignment_matrix.tsv"
    assert replayed.read_bytes() == (formal / replayed.name).read_bytes()
    with replayed.open(encoding="utf-8", newline="") as stream:
        header, *rows = csv.reader(stream, delimiter="\t")
    assert header == ["Mz", "RT", "S1", "S2", "S3", "S4", "Missing"]
    assert len(rows) == 3
    (shared,) = [row for row in rows if row[2] and row[4]]
    areas = list(map(float, shared[2:6]))
    assert 0 < areas[0] < areas[1] < areas[2] < areas[3]
    assert any(not row[6] for row in rows)
