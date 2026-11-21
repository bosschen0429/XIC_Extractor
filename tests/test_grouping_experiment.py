"""The experiment replays formal initial grouping and never schedules Backfill."""

import csv
import gzip
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.run_alignment import main as run_alignment
from tests.alignment_pipeline_helpers import _owner, resolved_elution_context
from tests.owner_peak_fixtures import with_peak
from tests.untargeted_cid_nl_bounded_runtime_fixtures import _plan
from tools.diagnostics.grouping_experiment import main
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import BatchRtWarp
from xic_extractor.alignment.grouping_experiment import (
    GroupingInputs,
    GroupingWorkset,
    replay_grouping,
)
from xic_extractor.alignment.peak_trajectories import ordered_pattern_contexts
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibrationAccumulator
from xic_extractor.diagnostics.grouping_experiment_io import (
    group_signature,
    read_grouping_inputs,
    write_grouping_inputs,
)
from xic_extractor.peak_detection.ms1_shape_identity import prepare_local_peak_shape
from xic_extractor.untargeted_execution.alignment import execute_prepared_alignment


def _snapshot_grouping(
    plan, *, spool_directory, workers, alignment_config, known_istd_reference=None
):
    return execute_prepared_alignment(
        plan,
        output_dir=spool_directory,
        raw_workers=workers,
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
        grouping_only=True,
    )


def test_retained_snapshot_ignores_only_retired_alignment_keys(tmp_path):
    inputs = GroupingInputs(
        sample_stems=(),
        injection_order={},
        alignment_config=AlignmentConfig(),
        worksets=(),
        candidate_ids_by_owner={},
    )
    path = tmp_path / "legacy.json.gz"
    write_grouping_inputs(path, inputs, provenance={})
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    payload["alignment_config"].update(
        duplicate_fold_ppm=5.0,
        duplicate_fold_rt_sec=2.0,
        duplicate_fold_product_ppm=10.0,
        duplicate_fold_observed_loss_ppm=10.0,
        duplicate_fold_min_detected_overlap=0.8,
        duplicate_fold_min_shared_detected_count=3,
        duplicate_fold_min_detected_jaccard=0.6,
        duplicate_fold_min_present_overlap=0.8,
        anchor_priorities=["HIGH"],
        anchor_min_scan_support_score=0.5,
        mz_bucket_neighbor_radius=2,
        owner_window_overlap_fraction=0.5,
        owner_tail_seed_guard_sec=30.0,
        owner_tail_max_secondary_ratio=0.3,
        owner_backfill_min_detected_samples=1,
        identity_rt_candidate_window_sec=180.0,
    )
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    restored, _ = read_grouping_inputs(path)
    assert restored == inputs

    payload["alignment_config"]["max_ppmm"] = 30.0
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    with pytest.raises(TypeError, match="max_ppmm"):
        read_grouping_inputs(path)


@pytest.mark.parametrize("repeat_equal_priority", (False, True))
def test_compact_snapshot_keeps_curve_tie_priority_and_distinct_anchor_bindings(
    tmp_path,
    repeat_equal_priority,
):
    from xic_extractor.alignment.peak_trajectories import _local_patterns
    from xic_extractor.alignment.rt_drift import DriftAnchor, RtDriftModel
    from xic_extractor.peak_detection.elution_context import (
        ElutionLandmark,
        SampleElutionContext,
    )

    owner = _owner("A", "one", apex_rt=9.0, precursor_mz=500.0)
    assert owner.cross_sample_member_subject is not None
    owner = replace(owner, owner_id=owner.cross_sample_member_subject.subject_id)
    first = SampleElutionContext(
        "A",
        500.0,
        8.0,
        11.0,
        (ElutionLandmark(9.0, 100.0, 0.2),),
        rt=(8.0, 11.0),
        raw=(1.0, 0.0),
    )
    preferred = replace(
        first, landmarks=(ElutionLandmark(9.0, 500.0, 0.2),), raw=(0.0, 1.0)
    )
    contexts = (
        (first, replace(preferred, raw=first.raw), first)
        if repeat_equal_priority
        else (first, preferred)
    )
    anchor = DriftAnchor(
        "channel", 9.0, 0, False, {"A": 0.0}, owner_ids=frozenset({owner.owner_id})
    )
    drift = RtDriftModel((anchor,))
    other_drift = RtDriftModel((replace(anchor, owner_ids=frozenset({"other-owner"})),))
    workset = GroupingWorkset("first", (owner,), (), None, contexts, rt_drift=drift)
    inputs = GroupingInputs(
        ("A",),
        {"A": 1},
        AlignmentConfig(),
        (
            workset,
            replace(workset, key="same"),
            replace(workset, key="other", rt_drift=other_drift),
        ),
        {},
    )
    path = tmp_path / "compact.json.gz"
    write_grouping_inputs(path, inputs, provenance={})
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    assert len(payload["elution_contexts"]) == 2
    assert len(payload["rt_models"]) == 2
    restored, _ = read_grouping_inputs(path)
    assert restored.worksets[0].rt_drift.anchors[0].owner_ids == anchor.owner_ids
    assert restored.worksets[2].rt_drift.anchors[0].owner_ids == frozenset(
        {"other-owner"}
    )
    for contexts in (workset.elution_contexts, restored.worksets[0].elution_contexts):
        nodes, _, owner_nodes, _ = _local_patterns(
            (owner,), contexts, AlignmentConfig(), None
        )
        assert nodes[owner_nodes[owner.owner_id]].landmark.height == (
            100.0 if repeat_equal_priority else 500.0
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
        payload["backfill_half_window_min"] = saved_acquisition_window
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            json.dump(payload, stream)
    restored, _ = read_grouping_inputs(path)
    compact_contexts = tuple(
        replace(context, rt=(), raw=())
        for context in ordered_pattern_contexts(contexts)
    )
    assert restored == replace(
        inputs,
        worksets=(replace(inputs.worksets[0], elution_contexts=compact_contexts),),
    )
    assert all(
        "rt" not in context and "raw" not in context
        for context in payload["elution_contexts"]
    )
    assert {
        frozenset(group.owner_ids) for group in replay_grouping(restored)
    } == expected

    for workset in payload["worksets"]:
        workset.update(payload["rt_models"][workset.pop("rt_model_index")])
        workset["elution_contexts"] = [
            payload["elution_contexts"][index]
            for index in workset.pop("elution_context_indices")
        ]
    payload.pop("rt_models")
    payload.pop("elution_contexts")
    for item in payload["worksets"][0]["owners"]:
        item["peak_shape"].pop("half_height_point_count")
        item["peak_shape"].pop("edge_intensity_ratio")
        item["identity_conflict"] = False
        item["is_provisional"] = False
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)
    legacy, _ = read_grouping_inputs(path)
    assert {frozenset(group.owner_ids) for group in replay_grouping(legacy)} == expected
    assert [owner.peak for owner in legacy.worksets[0].owners] == [
        owner.peak for owner in restored.worksets[0].owners
    ]
    for owner in legacy.worksets[0].owners:
        assert owner.peak_shape is not None
        assert owner.peak_shape.half_height_point_count is None
        assert owner.peak_shape.edge_intensity_ratio is None


@pytest.mark.parametrize("raw_workers", [1, 4])
@pytest.mark.parametrize("save_snapshot", [False, True])
def test_application_outputs_formal_groups_with_an_optional_snapshot(
    tmp_path, monkeypatch, raw_workers, save_snapshot
):
    import xic_extractor.applications.untargeted.application as application
    import xic_extractor.untargeted_execution.partition.workers as workers
    from tests.alignment_pipeline_helpers import write_batch
    from xic_extractor.applications.untargeted.request import UntargetedRequest
    from xic_extractor.configuration.execution_profile import UntargetedExecutionPolicy
    from xic_extractor.execution.partitions import PartitionPipelineScheduler

    scheduled = []
    execute = PartitionPipelineScheduler.execute

    def observe_scheduler(adapter, **kwargs):
        scheduled.append(kwargs)
        return execute(adapter, **kwargs)

    monkeypatch.setattr(PartitionPipelineScheduler, "execute", observe_scheduler)

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("Backfill must not run during grouping experiments")

    monkeypatch.setattr(workers, "_execute_backfill_batch", forbidden)
    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    monkeypatch.setattr(
        application,
        "prepare_untargeted_alignment_inputs",
        lambda **kwargs: plan,
    )
    import xic_extractor.alignment.grouping_experiment as experiment

    with monkeypatch.context() as diagnostic_guard:
        diagnostic_guard.setattr(experiment, "group_cid_nl_owners", forbidden)
        output = application.run_untargeted_grouping(
            UntargetedRequest(
                discovery_batch_index=write_batch(tmp_path, ("S0", "S1", "S2", "S3")),
                raw_dir=tmp_path,
                dll_dir=tmp_path,
                output_dir=tmp_path / "snapshot",
                alignment_config=AlignmentConfig(anchor_min_seed_events=1),
                discovery_settings=plan.context.settings,
                peak_config=plan.context.peak_config,
                execution=UntargetedExecutionPolicy(
                    raw_workers=raw_workers,
                    raw_worker_backend="thread",
                    max_partition_jobs=8,
                ),
            ),
            save_snapshot=save_snapshot,
        )
    with (output / "members.tsv").open(encoding="utf-8", newline="") as stream:
        formal_members = list(csv.DictReader(stream, delimiter="\t"))
    assert {row["sample_stem"] for row in formal_members} == {"S0", "S1"}
    assert len({row["group_id"] for row in formal_members}) == 1
    assert len(scheduled) == 1
    assert [phase.name for phase in scheduled[0]["phases"]] == [
        "initial-acquisition",
        "final-grouping",
    ]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["source"]["initial_job_count"] == 2
    assert summary["group_count"] == 1
    assert not (output / "alignment_matrix.tsv").exists()
    snapshot = output / "grouping-inputs.json.gz"
    assert snapshot.exists() is save_snapshot
    if not save_snapshot:
        return
    inputs, provenance = read_grouping_inputs(snapshot)
    groups = replay_grouping(inputs)
    assert len(groups) == 1
    assert inputs.sample_stems == ("S0", "S1", "S2", "S3")
    assert {owner.sample_stem for group in groups for owner in group.owners} == {
        "S0",
        "S1",
    }
    restored, provenance = read_grouping_inputs(snapshot)
    original_shapes = {
        owner.owner_id: owner.peak_shape
        for workset in inputs.worksets
        for owner in workset.owners
    }
    assert original_shapes
    assert all(shape is not None for shape in original_shapes.values())
    assert {
        owner.owner_id: owner.peak_shape
        for workset in restored.worksets
        for owner in workset.owners
    } == original_shapes
    assert group_signature(replay_grouping(restored)) == group_signature(groups)
    monkeypatch.setattr(workers, "_open_job_raw", forbidden)
    assert (
        run_alignment(
            [
                "regroup",
                "--inputs",
                str(snapshot),
                "--compare-to",
                str(output),
                "--output-dir",
                str(tmp_path / "replay"),
            ]
        )
        == 0
    )
    replay_summary = json.loads((tmp_path / "replay" / "summary.json").read_text())
    assert all(workset.rt_model_inputs is not None for workset in inputs.worksets)
    assert (replay_summary["rt_model"], replay_summary["rt_model_changed"]) == (
        "refit",
        False,
    )
    diff = replay_summary["membership_diff"]
    assert (
        diff["compared_owner_count"] > 0
        and diff["split_count"] == diff["merge_count"] == 0
    )
    for name in ("groups", "members", "decisions", "samples"):
        assert (output / f"{name}.tsv").read_bytes() == (
            tmp_path / "replay" / f"{name}.tsv"
        ).read_bytes()
    with (tmp_path / "replay" / "decisions.tsv").open(
        encoding="utf-8", newline=""
    ) as stream:
        decisions = list(csv.DictReader(stream, delimiter="\t"))
    risks = [
        json.loads(d["accepted_risk_json"])
        for d in decisions
        if d["accepted_risk_json"]
    ]
    assert risks
    assert all(risk["phase"] == "initial" and risk["selected_owners"] for risk in risks)
    assert (
        '"changed_observation_count": 0'
        in (tmp_path / "replay" / "summary.json").read_text()
    )


def test_istd_experiment_and_saved_replay_use_formal_batch_coordinates(
    tmp_path, monkeypatch
):
    import xic_extractor.untargeted_execution.partition.workers as workers
    from tests.alignment_known_istd_in_memory_fixtures import _make_target
    from xic_extractor.alignment.drift_evidence import (
        mint_known_istd_reference_from_measurements,
    )
    from xic_extractor.alignment.rt_frame import SampleRtFrame

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    reference = mint_known_istd_reference_from_measurements(
        [_make_target(f"standard{i}") for i in range(8)],
        {
            (f"standard{i}", sample): 3 + 2 * i + (2 if sample == "S1" else 0)
            for i in range(8)
            for sample in ("S0", "S1", "S2", "S3")
        },
    )
    prepared = _snapshot_grouping(
        plan,
        spool_directory=tmp_path,
        workers=1,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        known_istd_reference=reference,
    )
    formal_frame = plan.context.rt_frame.with_sources(known_istd_reference=reference)
    formal_frame = formal_frame.with_sample_calibration(
        BatchRtCalibrationAccumulator(
            sample_stems=tuple(
                formal_frame.sample_universe or formal_frame.injection_order
            ),
            sample_cohorts=formal_frame.sample_cohorts,
        ).freeze(measured_istds=formal_frame.measured_istd_coordinates)
    )
    assert formal_frame.common_rt_min("S1", 8) == pytest.approx(6)
    expected = replay_grouping(prepared.inputs, rt_coordinates=formal_frame)
    assert len(expected) == 2
    assert group_signature(prepared.groups) == group_signature(expected)
    snapshot = tmp_path / "istd.json.gz"
    write_grouping_inputs(snapshot, prepared.inputs, provenance={})
    restored, _ = read_grouping_inputs(snapshot)
    assert group_signature(replay_grouping(restored)) == group_signature(expected)
    assert group_signature(
        replay_grouping(restored, rt_coordinates=SampleRtFrame())
    ) != group_signature(expected)
    counted_replay = Mock(wraps=replay_grouping)
    monkeypatch.setattr(
        "tools.diagnostics.grouping_experiment.replay_grouping", counted_replay
    )
    output = tmp_path / "istd-replay"
    assert main(["replay", "--inputs", str(snapshot), "--output-dir", str(output)]) == 0
    assert counted_replay.call_count == 1
    with (output / "members.tsv").open(encoding="utf-8") as stream:
        members = list(csv.DictReader(stream, delimiter="\t"))
    for member in members:
        expected_rt = float(member["observed_rt"]) - (
            2 if member["sample_stem"] == "S1" else 0
        )
        assert float(member["comparison_rt"]) == pytest.approx(expected_rt)


@pytest.mark.parametrize("mode", ("sparse", "outside", "missing_peer"))
def test_replay_members_report_the_selected_coordinate_basis(tmp_path, mode):
    from xic_extractor.alignment.sample_rt_calibration import (
        RtCalibrationPolicy,
        build_batch_rt_calibration,
    )

    samples = ("a", "b", "c")
    shifts = {"a": 0.0, "b": 0.5, "c": 0.5}
    query = 30.0 if mode == "outside" else 12.0
    positions = (10.0, 20.0) if mode == "sparse" else tuple(range(10, 18))
    calibration = build_batch_rt_calibration(
        {},
        sample_stems=samples,
        prior_injections={},
        policy=RtCalibrationPolicy(),
        measured_istds=tuple(
            (f"standard{i}", sample, position + shifts[sample])
            for i, position in enumerate(positions)
            for sample in samples
            if mode != "missing_peer" or sample != "c"
        ),
    )
    owners = []
    for sample in samples:
        owner = _owner(sample, "peak", apex_rt=query + shifts[sample])
        owners.append(
            replace(owner, owner_id=owner.cross_sample_member_subject.subject_id)
        )
    inputs = GroupingInputs(
        sample_stems=samples,
        injection_order=dict(zip(samples, (1, 2, 3), strict=True)),
        alignment_config=AlignmentConfig(),
        worksets=(
            GroupingWorkset("mass-window", tuple(owners), (), None, (), calibration),
        ),
        candidate_ids_by_owner={},
    )
    snapshot, output = tmp_path / "inputs.gz", tmp_path / "replay"
    write_grouping_inputs(snapshot, inputs, provenance={"evidence": "synthetic"})
    assert main(["replay", "--inputs", str(snapshot), "--output-dir", str(output)]) == 0
    with (output / "members.tsv").open(encoding="utf-8") as stream:
        members = list(csv.DictReader(stream, delimiter="\t"))
    with (output / "decisions.tsv").open(encoding="utf-8") as stream:
        risks = {
            row["owner_id"]: json.loads(row["accepted_risk_json"])
            for row in csv.DictReader(stream, delimiter="\t")
        }
    assert len(members) == 3
    expected_rts = {
        sample: query + (shifts[sample] if mode == "missing_peer" else 0.5)
        for sample in samples
    }
    assert {row["sample_stem"]: float(row["comparison_rt"]) for row in members} == (
        pytest.approx(expected_rts)
    )
    for member in members:
        measured = query + shifts[member["sample_stem"]]
        expected_rt = expected_rts[member["sample_stem"]]
        assert float(member["observed_rt"]) == measured
        assert float(member["comparison_rt"]) == pytest.approx(expected_rt)
        assert member["comparison_rt_basis"] == (
            "raw" if mode == "missing_peer" else "drift_corrected"
        )
        risk = risks[member["owner_id"]]
        assert risk["rt_basis"] == member["comparison_rt_basis"]
        if mode != "missing_peer":
            assert risk["rt_estimate"]["rt_min"] == pytest.approx(expected_rt)
            assert risk["rt_estimate"]["method"] == (
                "median_shift" if mode == "sparse" else "boundary_shift"
            )


@pytest.mark.parametrize("retained", [True, False])
def test_replay_refits_batch_rt_models_from_retained_anchors(
    tmp_path, monkeypatch, retained
):
    from xic_extractor.alignment.rt_anchors import (
        BatchRtModelInputs,
        RtAnchorObservation,
    )
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

    samples = ("a", "b", "c")
    shifts = {"a": 0.0, "b": 0.5, "c": 0.5}
    query = 12.0
    model_inputs = BatchRtModelInputs(
        sample_stems=samples,
        sample_cohorts=dict.fromkeys(samples, ""),
        injection_order=dict(zip(samples, (1, 2, 3), strict=True)),
        qc_samples=frozenset(),
        measured_istds=(),
        anchor_batches=(
            tuple(
                RtAnchorObservation(
                    f"channel{i}",
                    400.0 + i,
                    None if i % 2 else 116.0474,
                    sample,
                    f"g{i}-{sample}",
                    position + shifts[sample],
                    f"g{i}",
                )
                for i, position in enumerate(range(10, 18))
                for sample in samples
            ),
        ),
    )
    stale = BatchRtCalibration.from_coordinate_knots(
        tuple((sample, ((0.0, 0.0), (60.0, 60.0))) for sample in samples)
    )
    owners = []
    for sample in samples:
        owner = _owner(sample, "peak", apex_rt=query + shifts[sample])
        owners.append(
            replace(owner, owner_id=owner.cross_sample_member_subject.subject_id)
        )
    kept = model_inputs if retained else None
    inputs = GroupingInputs(
        sample_stems=samples,
        injection_order=dict(model_inputs.injection_order),
        alignment_config=AlignmentConfig(),
        worksets=(
            GroupingWorkset("mass-window", tuple(owners), (), None, (), stale),
            GroupingWorkset("empty", (), (), None, (), stale),
        ),
        candidate_ids_by_owner={},
    )
    inputs = replace(
        inputs,
        worksets=tuple(
            replace(workset, rt_model_inputs=kept) for workset in inputs.worksets
        ),
    )
    snapshot, output = tmp_path / "inputs.gz", tmp_path / "replay"
    write_grouping_inputs(snapshot, inputs, provenance={"evidence": "synthetic"})
    with gzip.open(snapshot, "rt", encoding="utf-8") as stream:
        assert len(json.load(stream)["rt_models"]) == 1
    restored, _ = read_grouping_inputs(snapshot)
    first, second = restored.worksets
    assert first.rt_model_inputs == kept
    assert first.rt_model_inputs is second.rt_model_inputs
    counted_replay = Mock(wraps=replay_grouping)
    monkeypatch.setattr(
        "tools.diagnostics.grouping_experiment.replay_grouping", counted_replay
    )
    assert main(["replay", "--inputs", str(snapshot), "--output-dir", str(output)]) == 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert (summary["rt_model"], summary["rt_model_changed"]) == (
        ("refit", True) if retained else ("snapshot", False)
    )
    assert counted_replay.call_count == (2 if retained else 1)
    with (output / "members.tsv").open(encoding="utf-8") as stream:
        members = list(csv.DictReader(stream, delimiter="\t"))
    assert {row["sample_stem"]: float(row["comparison_rt"]) for row in members} == (
        pytest.approx(
            dict.fromkeys(samples, query + 0.5)
            if retained
            else {sample: query + shifts[sample] for sample in samples}
        )
    )


def test_rt_coordinate_override_changes_membership_without_rewriting_measurements(
    tmp_path, monkeypatch
):
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "ensure_assemblies_loaded", lambda *_: None)
    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    inputs = _snapshot_grouping(
        plan,
        spool_directory=tmp_path,
        workers=1,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    ).inputs
    workset = inputs.worksets[0]
    moved = workset.owners[1].peak.integration
    shifted = with_peak(
        workset.owners[1],
        apex_rt=moved.rt_apex_min + 2,
        start_rt=moved.rt_left_min + 2,
        end_rt=moved.rt_right_min + 2,
    )
    inputs = replace(
        inputs,
        worksets=(replace(workset, owners=(workset.owners[0], shifted), rt_warp=None),),
    )

    class Coordinates:
        def corrected_rt_min(self, owner):
            return owner.peak.integration.rt_apex_min - (
                2 if owner.owner_id == shifted.owner_id else 0
            )

    raw = replay_grouping(inputs)
    corrected = replay_grouping(inputs, rt_coordinates=Coordinates())
    assert group_signature(raw) != group_signature(corrected)
    assert len(corrected) == 1
    assert {
        decision.accepted_risk.rt_basis for decision in corrected[0].inference_decisions
    } == {"diagnostic_override"}
    assert (
        next(
            owner for owner in corrected[0].owners if owner.owner_id == shifted.owner_id
        ).peak.integration.rt_apex_min
        == shifted.peak.integration.rt_apex_min
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
        mint_cid_nl_method_assessment,
    )

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
        assessment = mint_cid_nl_method_assessment(
            sample=sample,
            acquisition_source=fixture.source(sample),
            target=fixture.target,
            detector_invocation_id=f"direct:{name}",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=rt - 0.1,
                query_rt_max=rt + 0.1,
                support_policy=cid_nl_support_policy(),
                acquisition_supported=True,
                search_completed=True,
                reason_code=None,
            ),
            candidate_peaks=(peak,),
            evidence_refs=(evidence,),
            individual_support_refs=(support,),
        )
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
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
                tuple(initial),
                key=lambda group: group.group_hypothesis_id,
            )
        )
        scope, result = regroup_cid_nl_cross_sample_groups(
            samples=fixture.samples,
            grouping_scope=InitialGroupingScope(
                (499.0, 501.0),
                (7.0, 16.0),
                initial,
                raw_model,
            ),
            grouping_result=CrossSampleGroupingResult(
                fixture.samples,
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


def test_replay_only_recomputes_when_grouping_inputs_change(tmp_path, monkeypatch):
    snapshot = Path(__file__).parent / "fixtures" / "synthetic-grouping.json.gz"
    a, b = "a|hypothesis|peak", "b|hypothesis|peak"
    counted_replay = Mock(wraps=replay_grouping)
    monkeypatch.setattr(
        "tools.diagnostics.grouping_experiment.replay_grouping", counted_replay
    )

    def run(name, *options):
        output = tmp_path / name
        counted_replay.reset_mock()
        assert (
            run_alignment(
                [
                    "regroup",
                    "--inputs",
                    str(snapshot),
                    "--output-dir",
                    str(output),
                    *options,
                ]
            )
            == 0
        )
        return output

    def rows(output, filename):
        with (output / filename).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream, delimiter="\t"))

    def membership(output):
        groups = {}
        for row in rows(output, "members.tsv"):
            groups.setdefault(row["group_id"], set()).add(row["owner_id"])
        return {frozenset(owners) for owners in groups.values()}

    baseline = run("baseline")
    assert counted_replay.call_count == 1
    assert membership(baseline) == {frozenset((a, b))}

    backfill_only = run("no-backfill", "--set", "backfill_enabled=false")
    assert counted_replay.call_count == 1
    assert (backfill_only / "members.tsv").read_bytes() == (
        baseline / "members.tsv"
    ).read_bytes()
    assert rows(backfill_only, "changes.tsv") == []

    variant = run("variant", "--set", "max_ppm=20", "--compare-to", str(baseline))
    assert counted_replay.call_count == 2
    assert membership(variant) == {frozenset((a,)), frozenset((b,))}
    assert {
        (row["owner_id"], row["before_members"], row["after_members"])
        for row in rows(variant, "changes.tsv")
    } == {(a, "2", "1"), (b, "2", "1")}
    summary = json.loads((variant / "summary.json").read_text())
    assert summary["membership_diff"]["split_count"] == 1
    assert summary["membership_diff"]["merge_count"] == 0


def test_saved_memberships_compare_splits_and_merges_without_replay(
    tmp_path, monkeypatch
):
    from xic_extractor.diagnostics.grouping_experiment_io import write_table

    monkeypatch.setattr(
        "tools.diagnostics.grouping_experiment.replay_grouping",
        lambda *a, **kw: pytest.fail("comparison must not regroup observations"),
    )
    observations = [
        ("a", "s1", 10.0),
        ("b", "s2", 10.0),
        ("c", "s3", 10.1),
        ("d", "s1", 12.0),
        ("e", "s2", 12.05),
        ("f", "s3", 17.0),
    ]

    def members(name, groups):
        path = tmp_path / name
        write_table(
            path,
            [
                {
                    "group_id": group,
                    "owner_id": owner,
                    "sample_stem": sample,
                    "observed_rt": rt,
                    "mz": 300.0,
                }
                for group, (owner, sample, rt) in zip(groups, observations, strict=True)
            ],
            ("group_id", "owner_id", "sample_stem", "observed_rt", "mz"),
        )
        return path

    before = members("before.tsv", ["G1", "G1", "G2", "G3", "G3", "G4"])
    after = members("after.tsv", ["H1", "H1", "H1", "H2", "H3", "renamed"])
    out = tmp_path / "diff"
    args = [
        "compare",
        "--before",
        str(before),
        "--after",
        str(after),
        "--output-dir",
        str(out),
    ]
    assert main(args) == 0
    counts = json.loads((out / "summary.json").read_text())["membership_diff"]
    with (out / "membership-diff.tsv").open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert counts == {
        "compared_owner_count": 6,
        "before_only_owner_count": 0,
        "after_only_owner_count": 0,
        "merge_count": 1,
        "split_count": 1,
        "merge_within_10s_count": 1,
        "split_within_10s_count": 1,
    }
    merge = next(row for row in rows if row["change"] == "merge")
    assert (merge["group_id"], merge["part_sizes"]) == ("H1", "2|1")
    assert round(float(merge["apex_gap_sec"]), 6) == 6.0
    split = next(row for row in rows if row["change"] == "split")
    assert (split["group_id"], split["part_samples"]) == ("G3", "s1|s2")
    assert merge["part_owners"] == "a,b|c"
    after.write_text(after.read_text().splitlines()[0] + "\n")
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_grouping_anchor_table_lists_istd_knots_without_any_workset(
    tmp_path, monkeypatch
):
    import xic_extractor.applications.untargeted.application as application
    from tests.alignment_pipeline_helpers import write_batch
    from xic_extractor.alignment.sample_rt_calibration import (
        RtCalibrationPolicy,
        build_batch_rt_calibration,
    )
    from xic_extractor.applications.untargeted.request import UntargetedRequest
    from xic_extractor.untargeted_execution.grouping_experiment import (
        PreparedGroupingExperiment,
    )

    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    monkeypatch.setattr(
        application, "prepare_untargeted_alignment_inputs", lambda **kwargs: plan
    )
    calibration = build_batch_rt_calibration(
        {},
        sample_stems=("S0", "S1"),
        prior_injections={},
        policy=RtCalibrationPolicy(),
        measured_istds=(("ISTD1", "S0", 2.0), ("ISTD1", "S1", 2.2)),
    )

    def grouping_without_worksets(*_args, output_dir, **_kwargs):
        output_dir.mkdir(parents=True)
        return PreparedGroupingExperiment(
            inputs=GroupingInputs(
                sample_stems=("S0", "S1"),
                injection_order={},
                alignment_config=AlignmentConfig(),
                worksets=(),
                candidate_ids_by_owner={},
            ),
            groups=(),
            rt_calibration=calibration,
            selected_partition_count=0,
            total_partition_count=0,
            initial_job_count=0,
        )

    monkeypatch.setattr(
        application, "execute_prepared_alignment", grouping_without_worksets
    )

    output = application.run_untargeted_grouping(
        UntargetedRequest(
            discovery_batch_index=write_batch(tmp_path, ("S0", "S1")),
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "grouping",
            alignment_config=AlignmentConfig(),
            discovery_settings=plan.context.settings,
            peak_config=plan.context.peak_config,
        )
    )

    with (output / "rt_anchors.tsv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert [(r["kind"], r["istd_label"], r["reference_rt_min"]) for r in rows] == [
        ("istd", "ISTD1", "2.1")
    ]


def test_regroup_cannot_overwrite_previous_results(tmp_path):
    from scripts.run_alignment import main

    snapshot = Path(__file__).parent / "fixtures" / "synthetic-grouping.json.gz"
    args = ["regroup", "--inputs", str(snapshot), "--output-dir", str(tmp_path)]
    assert main(args) == 0
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(SystemExit) as error:
        main([*args, "--set", "max_ppm=20", "--compare-to", str(tmp_path / "missing")])
    assert error.value.code == 2
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}
