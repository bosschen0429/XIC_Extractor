"""The experiment replays formal initial grouping and never schedules Backfill."""

from dataclasses import replace

from tests.alignment_pipeline_helpers import _owner
from tests.test_untargeted_cid_nl_bounded_runtime import _plan
from tools.diagnostics.grouping_experiment import main
from xic_extractor.alignment.config import AlignmentConfig
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
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
)
from xic_extractor.untargeted_execution.grouping_experiment import (
    prepare_grouping_experiment,
)


def test_snapshot_retains_elution_inputs_and_replays_separated_rows(tmp_path):
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
            replace(owner, owner_id=owner.cross_sample_member_subject.subject_id)
        )
        contexts.append(
            SampleElutionContext(
                sample,
                500.0,
                8.0,
                11.0,
                (
                    ElutionLandmark(9.0 + shift, 100.0, 0.2),
                    ElutionLandmark(9.6 + shift, 100.0, 0.2),
                ),
            )
        )
    inputs = GroupingInputs(
        sample_stems=("A", "B", "C", "D"),
        injection_order={"A": 1, "B": 2, "C": 3, "D": 4},
        alignment_config=AlignmentConfig(),
        backfill_half_window_min=1.0,
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
    restored, _ = read_grouping_inputs(path)
    assert restored.worksets[0].elution_contexts == tuple(contexts)
    assert {
        frozenset(group.owner_ids) for group in replay_grouping(restored)
    } == expected


def test_prepare_snapshot_and_cli_replay_preserve_formal_grouping(
    tmp_path, monkeypatch
):
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "_ensure_assemblies_loaded", lambda *_: None)

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


def test_rt_coordinate_override_changes_membership_without_rewriting_measurements(
    tmp_path, monkeypatch
):
    import xic_extractor.untargeted_execution.partition.workers as workers

    monkeypatch.setattr(workers, "_ensure_assemblies_loaded", lambda *_: None)
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
