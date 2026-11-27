"""The experiment replays formal initial grouping and never schedules Backfill."""

import csv
import gzip
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.run_alignment import main as run_alignment
from tests.alignment_pipeline_helpers import _owner
from tests.untargeted_cid_nl_bounded_runtime_fixtures import _plan
from tools.diagnostics.grouping_experiment import main
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_experiment import (
    GroupingInputs,
    GroupingWorkset,
    replay_grouping,
)
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibrationAccumulator
from xic_extractor.configuration.execution_profile import (
    ExecutionProfile,
    UntargetedExecutionPolicy,
    in_process_execution,
)
from xic_extractor.diagnostics.grouping_experiment_io import group_signature
from xic_extractor.diagnostics.stage_snapshot import (
    read_grouping_inputs,
    write_grouping_inputs,
)
from xic_extractor.injection_timeline import InjectionSequence
from xic_extractor.untargeted_execution.alignment import execute_prepared_alignment


def _replay_without_execution(inputs, *, execution, **options):
    return replay_grouping(inputs, **options)


def _two_sample_snapshot(path: Path) -> Path:
    """Same apex; precursor masses 500.000 and 500.004 are 8 ppm apart."""
    owners = []
    for sample, mz in (("a", 500.0), ("b", 500.004)):
        owner = _owner(sample, "peak", precursor_mz=mz)
        assert owner.cross_sample_member_subject is not None
        owners.append(owner)
    write_grouping_inputs(
        path,
        GroupingInputs(
            sample_stems=("a", "b"),
            injection_order={"a": 1, "b": 2},
            alignment_config=AlignmentConfig(precursor_mz_tolerance_ppm=5.0),
            worksets=(GroupingWorkset("channel", tuple(owners), (), None),),
            candidate_ids_by_owner={},
        ),
        provenance={},
    )
    return path


def _rewrite(path: Path, edit) -> None:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    edit(payload)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)


def _inline_rt_model(payload):
    """The layout before batch RT models were shared by index."""
    for workset in payload["worksets"]:
        workset.update(payload["rt_models"][workset.pop("rt_model_index")])
    payload.pop("rt_models")


@pytest.mark.parametrize(
    "previous_format",
    (
        lambda path: path.write_bytes(json.dumps({"schema": "x"}).encode()),
        lambda path: _rewrite(
            path,
            lambda payload: payload["alignment_config"].update(anchor_priorities=[]),
        ),
        lambda path: _rewrite(path, _inline_rt_model),
    ),
    ids=("uncompressed", "retired_config_key", "inline_rt_model"),
)
def test_snapshot_from_a_previous_format_asks_for_a_new_capture(
    tmp_path, previous_format
):
    snapshot = _two_sample_snapshot(tmp_path / "grouping-inputs.json.gz")
    previous_format(snapshot)
    output = tmp_path / "replay"
    output.mkdir()
    with pytest.raises(SystemExit) as error:
        main(["replay", "--inputs", str(snapshot), "--output-dir", str(output)])
    assert error.value.code == 2
    with pytest.raises(ValueError, match="capture it again"):
        read_grouping_inputs(snapshot)
    assert list(output.iterdir()) == []


@pytest.mark.parametrize(
    "override",
    ("backfill_enabled=1", 'preferred_rt_sec="60"'),
)
def test_regroup_rejects_wrong_setting_types_in_json_and_cli(tmp_path, override):
    snapshot = _two_sample_snapshot(tmp_path / "grouping-inputs.json.gz")
    output = tmp_path / "replay"
    with pytest.raises(SystemExit) as error:
        run_alignment(
            [
                "regroup",
                "--inputs",
                str(snapshot),
                "--output-dir",
                str(output),
                "--set",
                override,
            ]
        )
    assert error.value.code == 2
    assert list(output.iterdir()) == []

    key, raw = override.split("=", 1)
    _rewrite(
        snapshot,
        lambda payload: payload["alignment_config"].update({key: json.loads(raw)}),
    )
    with pytest.raises(ValueError, match="capture it again"):
        read_grouping_inputs(snapshot)


@pytest.mark.parametrize("field", ["mz", "raw_scan_indices", "rt_width_min"])
def test_snapshot_rejects_corrupt_measurement_of_a_selected_peak(tmp_path, field):
    path = _two_sample_snapshot(tmp_path / "grouping-inputs.json.gz")

    def corrupt(payload):
        owner = payload["worksets"][0]["owners"][0]
        peak = owner["hypothesis"]["peak"]
        if field == "mz":
            peak[field] = 0
        elif field == "raw_scan_indices":
            peak["integration"][field] = [999]
        else:
            peak["integration"][field] = 999

    _rewrite(path, corrupt)
    with pytest.raises(ValueError, match="capture it again"):
        read_grouping_inputs(path)


def _snapshot_grouping(
    plan, *, spool_directory, workers, alignment_config, known_istd_reference=None
):
    return execute_prepared_alignment(
        plan,
        output_dir=spool_directory,
        execution=ExecutionProfile(alignment_workers=workers, backend="thread"),
        alignment_config=alignment_config,
        known_istd_reference=known_istd_reference,
        grouping_only=True,
    )


@pytest.mark.parametrize("raw_workers", [1, 4])
@pytest.mark.parametrize("save_snapshot", [False, True])
def test_application_outputs_formal_groups_with_an_optional_snapshot(
    tmp_path, monkeypatch, raw_workers, save_snapshot
):
    import xic_extractor.applications.untargeted.application as application
    import xic_extractor.untargeted_execution.partition.workers as workers
    from tests.alignment_pipeline_helpers import write_batch
    from xic_extractor.applications.untargeted.request import UntargetedRequest
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
        diagnostic_guard.setattr(experiment, "group_peak_bands", forbidden)
        output = application.run_untargeted_grouping(
            UntargetedRequest(
                discovery_batch_index=write_batch(tmp_path, ("S0", "S1", "S2", "S3")),
                raw_dir=tmp_path,
                dll_dir=tmp_path,
                output_dir=tmp_path / "snapshot",
                alignment_config=AlignmentConfig(),
                discovery_settings=plan.context.settings,
                execution=UntargetedExecutionPolicy(
                    profile=ExecutionProfile(
                        alignment_workers=raw_workers, backend="thread"
                    ),
                    max_partition_jobs=8,
                ),
            ),
            save_snapshot=save_snapshot,
        )
    with (output / "members.tsv").open(encoding="utf-8", newline="") as stream:
        formal_members = list(csv.DictReader(stream, delimiter="\t"))
    assert {row["sample_stem"] for row in formal_members} == {"S0", "S1", "S2", "S3"}
    assert len({row["group_id"] for row in formal_members}) == 1
    assert len(scheduled) == 1
    assert [phase.name for phase in scheduled[0]["phases"]] == [
        "initial-acquisition",
        "final-grouping",
    ]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["source"]["initial_job_count"] == 8
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
    (group,) = groups
    assert group.disposition == "established"
    assert {
        owner.sample_stem: owner.primary_identity_event is not None
        for owner in group.owners
    } == {"S0": True, "S1": True, "S2": False, "S3": False}
    assert {owner.owner_id for owner in group.owners} == {
        row["owner_id"] for row in formal_members
    }
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
    for name in ("groups", "members", "samples"):
        assert (output / f"{name}.tsv").read_bytes() == (
            tmp_path / "replay" / f"{name}.tsv"
        ).read_bytes()


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
        alignment_config=AlignmentConfig(),
        known_istd_reference=reference,
    )
    formal_frame = plan.context.rt_frame.with_sources(known_istd_reference=reference)
    formal_frame = formal_frame.with_sample_calibration(
        BatchRtCalibrationAccumulator(
            sample_stems=tuple(
                formal_frame.sample_universe or formal_frame.injections.stems
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
    counted_replay = Mock(wraps=_replay_without_execution)
    monkeypatch.setattr(
        "tools.diagnostics.grouping_experiment._replay_grouping", counted_replay
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
        injections=InjectionSequence({"a": 1, "b": 5, "c": 6}),
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
        owners.append(owner)
    kept = model_inputs if retained else None
    inputs = GroupingInputs(
        sample_stems=samples,
        injection_order=model_inputs.injections.snapshot_orders(),
        alignment_config=AlignmentConfig(),
        worksets=(
            GroupingWorkset("mass-window", tuple(owners), (), rt_calibration=stale),
            GroupingWorkset("empty", (), (), rt_calibration=stale),
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
        payload = json.load(stream)
    assert payload["injection_order"] == {"a": 1, "b": 5, "c": 6}
    [model] = payload["rt_models"]
    if retained:
        assert model["rt_model_inputs"]["injection_order"] == payload["injection_order"]
    restored, _ = read_grouping_inputs(snapshot)
    first, second = restored.worksets
    assert first.rt_model_inputs == kept
    assert first.rt_model_inputs is second.rt_model_inputs
    counted_replay = Mock(wraps=_replay_without_execution)
    monkeypatch.setattr(
        "tools.diagnostics.grouping_experiment._replay_grouping", counted_replay
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


def test_replay_only_recomputes_when_grouping_inputs_change(tmp_path, monkeypatch):
    snapshot = _two_sample_snapshot(tmp_path / "two-sample.json.gz")
    a, b = "a|hypothesis|peak", "b|hypothesis|peak"
    counted_replay = Mock(wraps=_replay_without_execution)
    monkeypatch.setattr(
        "tools.diagnostics.grouping_experiment._replay_grouping", counted_replay
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
    assert membership(baseline) == {frozenset((a,)), frozenset((b,))}

    backfill_only = run("no-backfill", "--set", "backfill_enabled=false")
    assert counted_replay.call_count == 1
    assert (backfill_only / "members.tsv").read_bytes() == (
        baseline / "members.tsv"
    ).read_bytes()
    assert rows(backfill_only, "changes.tsv") == []

    replayed_baseline = run(
        "replayed-baseline", "--set", "precursor_mz_tolerance_ppm=10"
    )
    assert counted_replay.call_count == 2
    variant = run(
        "variant",
        "--set",
        "precursor_mz_tolerance_ppm=10",
        "--compare-to",
        str(baseline),
    )
    assert counted_replay.call_count == 1
    assert membership(variant) == {frozenset((a, b))}
    assert {
        (row["owner_id"], row["before_members"], row["after_members"])
        for row in rows(variant, "changes.tsv")
    } == {(a, "1", "2"), (b, "1", "2")}
    assert (variant / "changes.tsv").read_bytes() == (
        replayed_baseline / "changes.tsv"
    ).read_bytes()
    summary = json.loads((variant / "summary.json").read_text())
    assert summary["changed_observation_count"] == 2
    assert summary["membership_diff"]["split_count"] == 0
    assert summary["membership_diff"]["merge_count"] == 1


def test_parallel_regroup_writes_the_sequential_tables(tmp_path, monkeypatch):
    import tools.diagnostics.grouping_experiment as experiment

    samples = ("a", "b", "c")
    worksets = []
    for index, members in enumerate((samples[:2], samples, samples[:1])):
        owners = []
        for sample in members:
            owner = _owner(sample, f"peak{index}", apex_rt=10.0 + index)
            owners.append(owner)
        worksets.append(GroupingWorkset(f"window{index}", tuple(owners), (), None))
    snapshot = tmp_path / "inputs.gz"
    write_grouping_inputs(
        snapshot,
        GroupingInputs(
            sample_stems=samples,
            injection_order=dict(zip(samples, (1, 2, 3), strict=True)),
            alignment_config=AlignmentConfig(),
            worksets=tuple(worksets),
            candidate_ids_by_owner={},
        ),
        provenance={"evidence": "synthetic"},
    )
    command = ["replay", "--inputs", str(snapshot), "--output-dir"]
    outputs = []
    for workers in (1, 2):
        monkeypatch.setattr(
            experiment,
            "load_execution_profile",
            lambda count=workers: ExecutionProfile(parallel_workers=count),
        )
        outputs.append(tmp_path / f"workers{workers}")
        assert main([*command, str(outputs[-1])]) == 0
    sequential, parallel = outputs
    with (parallel / "groups.tsv").open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert [round(float(row["rt"])) for row in rows] == [10, 11, 12]
    for name in ("groups.tsv", "members.tsv", "samples.tsv"):
        assert (parallel / name).read_bytes() == (sequential / name).read_bytes()


def test_saved_memberships_compare_splits_and_merges_without_replay(
    tmp_path, monkeypatch
):
    from xic_extractor.diagnostics.grouping_experiment_io import write_table

    monkeypatch.setattr(
        "tools.diagnostics.grouping_experiment._replay_grouping",
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
            execution=UntargetedExecutionPolicy(profile=in_process_execution()),
            discovery_batch_index=write_batch(tmp_path, ("S0", "S1")),
            raw_dir=tmp_path,
            dll_dir=tmp_path,
            output_dir=tmp_path / "grouping",
            alignment_config=AlignmentConfig(),
            discovery_settings=plan.context.settings,
        )
    )

    with (output / "rt_anchors.tsv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert [(r["kind"], r["istd_label"], r["reference_rt_min"]) for r in rows] == [
        ("istd", "ISTD1", "2.1")
    ]


def test_regroup_cannot_overwrite_previous_results(tmp_path):
    from scripts.run_alignment import main

    snapshot = _two_sample_snapshot(tmp_path / "two-sample.json.gz")
    output = tmp_path / "replay"
    args = ["regroup", "--inputs", str(snapshot), "--output-dir", str(output)]
    assert main(args) == 0
    before = {p: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(SystemExit) as error:
        main(
            [
                *args,
                "--set",
                "precursor_mz_tolerance_ppm=20",
                "--compare-to",
                str(tmp_path / "missing"),
            ]
        )
    assert error.value.code == 2
    assert before == {p: p.read_bytes() for p in output.iterdir()}
