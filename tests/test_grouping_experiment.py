"""The experiment replays formal initial grouping and never schedules Backfill."""

import csv
import gzip
import json
from dataclasses import replace

import pytest

from tests.alignment_pipeline_helpers import _owner, resolved_elution_context
from tests.test_untargeted_cid_nl_bounded_runtime import _plan
from tools.diagnostics.grouping_experiment import main
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import BatchRtWarp
from xic_extractor.alignment.grouping_experiment import (
    GroupingInputs,
    GroupingWorkset,
    replay_grouping,
)
from xic_extractor.diagnostics.grouping_experiment_io import (
    group_signature,
    read_grouping_inputs,
    write_grouping_inputs,
)
from xic_extractor.peak_detection.ms1_shape_identity import prepare_local_peak_shape
from xic_extractor.untargeted_execution.grouping_experiment import (
    prepare_grouping_experiment,
)


@pytest.mark.parametrize("saved_acquisition_window", [None, 1.0, 2.0])
def test_snapshot_retains_elution_inputs_and_replays_separated_rows(
    tmp_path, saved_acquisition_window
):
    owners = []
    contexts = []
    for sample, shift, selected in (
        ("A", 0.0, 0),
        ("B", 0.05, 1),
        ("C", 0.6, 0),
        ("D", 0.65, 1),
    ):
        owner = _owner(
            sample,
            str(selected),
            apex_rt=9.0 + shift + 0.6 * selected,
            precursor_mz=500.0,
        )
        assert owner.cross_sample_member_subject is not None
        owners.append(
            replace(
                owner,
                owner_id=owner.cross_sample_member_subject.subject_id,
                peak_shape=prepare_local_peak_shape(
                    [-0.3, -0.2, -0.1, 0, 0.1, 0.2, 0.3],
                    [0, 0, 0.5, 1, 0.5, 0, 0],
                    apex_rt_min=0,
                ),
            )
        )
        contexts.append(
            resolved_elution_context(
                sample, ((9.0 + shift, 100.0), (9.6 + shift, 100.0))
            )
        )
    inputs = GroupingInputs(
        sample_stems=("A", "B", "C", "D"),
        injection_order={"A": 1, "B": 2, "C": 3, "D": 4},
        alignment_config=AlignmentConfig(),
        worksets=(
            GroupingWorkset("crowded", tuple(owners), (), None, tuple(contexts)),
        ),
        candidate_ids_by_owner={},
    )
    expected = {
        frozenset((owners[0].owner_id, owners[2].owner_id)),
        frozenset((owners[1].owner_id, owners[3].owner_id)),
    }
    path = tmp_path / "context.json.gz"
    write_grouping_inputs(path, inputs, provenance={"evidence": "synthetic"})
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    assert "backfill_half_window_min" not in payload
    if saved_acquisition_window is not None:
        # Retained diagnostic snapshots carry acquisition metadata. It is not
        # membership evidence and must not alter the restored grouping inputs.
        payload["backfill_half_window_min"] = saved_acquisition_window
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream)
    restored, _ = read_grouping_inputs(path)
    assert restored == inputs
    assert restored.worksets[0].elution_contexts == tuple(contexts)
    assert {
        frozenset(group.owner_ids) for group in replay_grouping(restored)
    } == expected

    # Legacy captures remain readable, but cannot invent measured resolution.
    for item in payload["worksets"][0]["owners"]:
        item["peak_shape"].pop("half_height_point_count")
        item["peak_shape"].pop("edge_intensity_ratio")
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    legacy, _ = read_grouping_inputs(path)
    for owner in legacy.worksets[0].owners:
        assert owner.peak_shape is not None
        assert owner.peak_shape.half_height_point_count is None
        assert owner.peak_shape.edge_intensity_ratio is None


def test_prepare_snapshot_and_cli_replay_preserve_formal_grouping(
    tmp_path, monkeypatch
):
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("Backfill must not run during grouping experiments")

    monkeypatch.setattr(workers, "_execute_backfill_batch", forbidden)
    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    prepared = prepare_grouping_experiment(
        plan,
        mz_ranges=((499, 501),),
        spool_directory=tmp_path,
        workers=1,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    assert prepared.selected_partition_count == 1
    assert len(prepared.groups) == 1
    assert prepared.inputs.sample_stems == ("S0", "S1", "S2", "S3")
    snapshot = tmp_path / "inputs.json.gz"
    write_grouping_inputs(
        snapshot, prepared.inputs, provenance={"case": "cross-subject"}
    )
    restored, provenance = read_grouping_inputs(snapshot)
    assert provenance == {"case": "cross-subject"}
    original_shapes = {
        owner.owner_id: owner.peak_shape
        for workset in prepared.inputs.worksets
        for owner in workset.owners
    }
    assert original_shapes
    assert all(shape is not None for shape in original_shapes.values())
    assert {
        owner.owner_id: owner.peak_shape
        for workset in restored.worksets
        for owner in workset.owners
    } == original_shapes
    assert group_signature(replay_grouping(restored)) == group_signature(
        prepared.groups
    )
    monkeypatch.setattr(workers, "_open_job_raw", forbidden)
    assert (
        main(
            [
                "replay",
                "--inputs",
                str(snapshot),
                "--output-dir",
                str(tmp_path / "replay"),
            ]
        )
        == 0
    )
    assert (tmp_path / "replay" / "members.tsv").is_file()
    assert (
        '"changed_observation_count": 0'
        in (tmp_path / "replay" / "summary.json").read_text()
    )


def test_istd_experiment_and_saved_replay_use_formal_batch_coordinates(
    tmp_path, monkeypatch
):
    import xic_extractor.untargeted_execution.partition.workers as workers
    from tests.test_alignment_known_istd_in_memory import _make_target
    from xic_extractor.alignment.drift_evidence import (
        mint_known_istd_reference_from_measurements,
    )
    from xic_extractor.alignment.rt_frame import SampleRtFrame

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    reference = mint_known_istd_reference_from_measurements(
        [_make_target("early"), _make_target("late")],
        {
            (label, sample): rt + (2 if sample == "S1" else 0)
            for label, rt in (("early", 3), ("late", 13))
            for sample in ("S0", "S1", "S2", "S3")
        },
    )
    prepared = prepare_grouping_experiment(
        plan,
        mz_ranges=((499, 501),),
        spool_directory=tmp_path,
        workers=1,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        known_istd_reference=reference,
    )
    formal_frame = plan.context.rt_frame.with_sources(
        known_istd_reference=reference
    ).with_batch_calibration(BatchRtWarp((), 0))
    assert formal_frame.common_rt_min("S1", 8) == pytest.approx(6)
    expected = replay_grouping(prepared.inputs, rt_coordinates=formal_frame)
    assert (
        len(expected) == 2
    )  # The observed 8 min peaks are 2 min apart after calibration.
    assert group_signature(prepared.groups) == group_signature(expected)
    snapshot = tmp_path / "istd.json.gz"
    write_grouping_inputs(snapshot, prepared.inputs, provenance={})
    restored, _ = read_grouping_inputs(snapshot)
    assert group_signature(replay_grouping(restored)) == group_signature(expected)
    assert group_signature(
        replay_grouping(restored, rt_coordinates=SampleRtFrame())
    ) != group_signature(expected)
    output = tmp_path / "istd-replay"
    assert main(["replay", "--inputs", str(snapshot), "--output-dir", str(output)]) == 0
    with (output / "members.tsv").open(encoding="utf-8") as stream:
        members = list(csv.DictReader(stream, delimiter="\t"))
    for member in members:
        expected_rt = float(member["observed_rt"]) - (
            2 if member["sample_stem"] == "S1" else 0
        )
        assert float(member["comparison_rt"]) == pytest.approx(expected_rt)


def test_rt_coordinate_override_changes_membership_without_rewriting_measurements(
    tmp_path, monkeypatch
):
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    inputs = prepare_grouping_experiment(
        plan,
        mz_ranges=((499, 501),),
        spool_directory=tmp_path,
        workers=1,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    ).inputs
    workset = inputs.worksets[0]
    shifted = replace(
        workset.owners[1], owner_apex_rt=workset.owners[1].owner_apex_rt + 2
    )
    inputs = replace(
        inputs,
        worksets=(replace(workset, owners=(workset.owners[0], shifted), rt_warp=None),),
    )

    class Coordinates:
        def corrected_rt_min(self, owner):
            return owner.owner_apex_rt - (
                2 if owner.owner_id == shifted.owner_id else 0
            )

    raw = replay_grouping(inputs)
    corrected = replay_grouping(inputs, rt_coordinates=Coordinates())
    assert group_signature(raw) != group_signature(corrected)
    assert len(corrected) == 1
    assert (
        next(
            owner for owner in corrected[0].owners if owner.owner_id == shifted.owner_id
        ).owner_apex_rt
        == shifted.owner_apex_rt
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("shift", [0.05, 1.65])
@pytest.mark.parametrize(
    "entry",
    ["replay", "regroup", "replay_override", "cli_calibration"],
)
def test_calibrated_patterns_reject_shifted_decoys_without_merging_real_peaks(
    tmp_path,
    reverse,
    shift,
    entry,
):
    from tests.cid_nl_peak_support_no_raw_fixture import (
        cid_nl_evidence,
        cid_nl_peak,
        cid_nl_support_policy,
        cid_nl_unique_support,
    )
    from tests.processing_identity_no_raw_fixture import (
        build_processing_identity_no_raw_fixture,
    )
    from xic_extractor.alignment.cross_sample.grouping_result import (
        CrossSampleGroupingResult,
    )
    from xic_extractor.alignment.cross_sample.peak_groups import (
        validate_cross_sample_peak_group_identity,
    )
    from xic_extractor.alignment.grouping_bootstrap import InitialGroupingScope
    from xic_extractor.alignment.grouping_workflow import (
        _partition_transition,
        group_cid_nl_owners,
        regroup_cid_nl_cross_sample_groups,
    )
    from xic_extractor.alignment.hypothesis_owner_adapter import (
        compose_cid_nl_peak_hypotheses_from_assessment,
        owner_from_cid_nl_assessment_support,
    )
    from xic_extractor.alignment.peak_trajectories import resolve_peak_trajectories
    from xic_extractor.alignment.rt_frame import SampleRtFrame
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlEvaluationReceipt,
        mint_cid_nl_method_assessment_ref,
    )

    # The independently specified calibration maps two physical peaks to 10.0
    # and 10.7 min. Direct owners alternate between them; no same-sample owner
    # exclusion can substitute for correctly matching their surrounding peaks.
    offsets = {"a": 0.0, "b": shift, "c": 0.02, "d": shift + 0.02}
    selected = {"a": 10.0, "b": 10.0, "c": 10.7, "d": 10.7}
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=tuple(offsets),
    )
    owners = []
    for name, common_rt in selected.items():
        rt = common_rt + offsets[name]
        sample = fixture.sample(name)
        peak = cid_nl_peak(
            fixture,
            name,
            apex_rt=rt,
            rt_left_min=rt - 0.1,
            rt_right_min=rt + 0.1,
        )
        evidence = cid_nl_evidence(fixture, name, scan_number=1, scan_rt_min=rt)
        support = cid_nl_unique_support(fixture, name, peak=peak, evidence=evidence)
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id=f"direct:{name}",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=rt - 0.1,
                query_rt_max=rt + 0.1,
                support_policy=cid_nl_support_policy(),
                acquisition_supported=True,
                search_completed=True,
                scanned_event_count=1,
                reason_code=None,
            ),
            candidate_peaks=(peak,),
            evidence_refs=(evidence,),
            individual_support_refs=(support,),
        )
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=fixture.owners,
            sample=sample,
            assessment=assessment,
        )
        owners.append(
            owner_from_cid_nl_assessment_support(
                sample=sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
        )
    owners = tuple(owners)
    contexts = tuple(
        resolved_elution_context(
            sample,
            tuple(
                (rt + offset, height)
                for rt, height in (
                    ((8.4, 100.0), (9.0, 10.0), (10.0, 10.0), (10.7, 100.0))
                    if sample in {"a", "c"}
                    else ((10.0, 100.0), (10.7, 10.0))
                )
            ),
        )
        for sample, offset in offsets.items()
    )
    warp = BatchRtWarp(
        tuple(
            (sample, ((8.0, offset), (15.0, offset)))
            for sample, offset in offsets.items()
        ),
        12,
    )
    inputs = GroupingInputs(
        sample_stems=tuple(offsets),
        injection_order={sample: i for i, sample in enumerate(offsets)},
        alignment_config=AlignmentConfig(),
        worksets=(
            GroupingWorkset(
                key="doublet",
                owners=owners[::-1] if reverse else owners,
                known_identity_bindings=(),
                rt_warp=warp,
                elution_contexts=contexts[::-1] if reverse else contexts,
            ),
        ),
        candidate_ids_by_owner={},
    )
    if entry == "replay":
        groups = replay_grouping(inputs)
    elif entry == "replay_override":
        groups = replay_grouping(
            inputs,
            rt_coordinates=SampleRtFrame().with_warp(warp),
        )
    elif entry == "cli_calibration":
        import csv
        from collections import defaultdict

        from xic_extractor.alignment.sample_rt_calibration import (
            BatchRtCalibration,
            RtCalibrationPolicy,
            SampleRtCalibration,
        )
        from xic_extractor.diagnostics.grouping_experiment_io import (
            write_rt_calibration,
        )

        calibration = BatchRtCalibration(
            RtCalibrationPolicy(),
            (),
            {
                sample: SampleRtCalibration(
                    sample,
                    "calibrated",
                    ((8 + offset, 8), (15 + offset, 15)),
                    None,
                    None,
                    "not_evaluated",
                )
                for sample, offset in offsets.items()
            },
            None,
            None,
            None,
        )
        snapshot, calibration_path = tmp_path / "inputs.gz", tmp_path / "rt.json"
        write_grouping_inputs(snapshot, inputs, provenance={"evidence": "synthetic"})
        write_rt_calibration(calibration_path, calibration)
        output = tmp_path / "calibrated"
        assert (
            main(
                [
                    "replay",
                    "--inputs",
                    str(snapshot),
                    "--output-dir",
                    str(output),
                    "--rt-calibration",
                    str(calibration_path),
                ]
            )
            == 0
        )
        members = defaultdict(set)
        with (output / "members.tsv").open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream, delimiter="\t"):
                members[row["group_id"]].add(row["sample_stem"])
        assert {frozenset(samples) for samples in members.values()} == {
            frozenset(("a", "b")),
            frozenset(("c", "d")),
        }
        return
    else:
        workset = inputs.worksets[0]
        raw_frame = SampleRtFrame(injection_order=inputs.injection_order)
        raw_model = resolve_peak_trajectories(
            workset.owners,
            workset.elution_contexts,
            config=inputs.alignment_config,
            injection_order=inputs.injection_order,
        )
        initial = group_cid_nl_owners(
            workset.owners,
            config=inputs.alignment_config,
            sample_universe=frozenset(offsets),
            rt_frame=raw_frame,
            peak_trajectories=raw_model,
        )
        population = tuple(
            sorted(
                (
                    validate_cross_sample_peak_group_identity(
                        group,
                        require_canonical=True,
                    )
                    for group in initial
                ),
                key=lambda group: group.group_hypothesis_id,
            )
        )
        scope, result = regroup_cid_nl_cross_sample_groups(
            processing_owners=fixture.owners,
            grouping_scope=InitialGroupingScope(
                (499.0, 501.0),
                (7.0, 16.0),
                initial,
                raw_model,
            ),
            grouping_result=CrossSampleGroupingResult(
                fixture.owners,
                _partition_transition(predecessors=population, successors=population),
                raw_frame,
            ),
            alignment_config=inputs.alignment_config,
            rt_frame=raw_frame.with_warp(warp),
        )
        groups = scope.groups
        assert {g.group_hypothesis_id for g in result.final_population} == {
            g.group_hypothesis_id for g in groups
        }
        for group in groups:
            # Localization consumes the rebuilt geometry but keeps raw positions.
            trajectory = scope.peak_trajectories.trajectory_for(group.owners)
            points = dict(trajectory.sample_apices)
            expected = selected[group.owners[0].sample_stem]
            assert points == pytest.approx(
                {sample: expected + offset for sample, offset in offsets.items()}
            )
    assert {
        frozenset(owner.sample_stem for owner in group.owners) for group in groups
    } == {
        frozenset(("a", "b")),
        frozenset(("c", "d")),
    }
    assert all(group.disposition == "established" for group in groups)
