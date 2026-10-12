"""End-to-end bounded cohort closure through the retained scientific owners."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Lock, get_ident
from time import perf_counter, sleep

import pytest
from openpyxl import Workbook

from tests.cid_nl_raw_fixture import _peak_config, _settings
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_untargeted_cid_nl_batch_runtime import _nearby_subject_candidates
from tests.test_untargeted_cid_nl_real_data_runtime import (
    _current_candidates,
    _discovery_receipts,
    _FakeRaw,
    _FakeThermoRaw,
    _install_runtime_raws,
)
from tests.untargeted_partition_fixtures import (
    complete_dense_partition,
    execute_dense_partition,
)
from xic_extractor.acquisition.istd_reference import read_known_istd_reference
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.untargeted_finalized_matrix import (
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.execution._finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
)
from xic_extractor.execution._lifecycle import AttemptTerminalOutcome
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution import (
    materialize_untargeted_partition,
)
from xic_extractor.untargeted_execution.execution_result import (
    CompletedUntargetBatchWorkflowResult,
)
from xic_extractor.untargeted_execution.input_planning import (
    prepare_eager_untargeted_plan,
)


@pytest.fixture(autouse=True)
def _disable_real_dll_loading(monkeypatch) -> None:
    import xic_extractor.untargeted_execution.partition.execution as runtime_owner

    monkeypatch.setattr(runtime_owner, "ensure_assemblies_loaded", lambda *_: None)


def _plan(
    tmp_path, monkeypatch, *, scenario="positive", opened_sink=None, sample_count=4
):
    paths = tuple(tmp_path / f"S{i}.synthetic" for i in range(sample_count))
    settings = replace(
        _settings(),
        neutral_loss_profile=None,
        precursor_mz_tolerance_ppm=(
            5.0 if scenario == "cross_subject_recovery" else 1.0
        ),
    )
    peak_config = _peak_config(tmp_path)
    raws = {}
    candidates = {}
    for path in paths:
        path.write_bytes(path.stem.encode())
        raw, sample_candidates = _nearby_subject_candidates(
            path, settings=settings, peak_config=peak_config
        )
        third_raw = _FakeRaw(
            (7.98,), scan_precursor_mz=500.008, neutral_loss_das=(116.0474,)
        )
        third_scan_number = raw.scans[-1].scan_number + 1
        third_raw.scans = (
            replace(
                third_raw.scans[0],
                scan_number=third_scan_number,
                rt=float(raw.rt[third_scan_number - 1]),
            ),
        )
        if scenario == "mixed_eligibility":
            third_raw.intensity[:] = 0.0
        third_candidates = _current_candidates(
            third_raw, raw_file=path, settings=settings, peak_config=peak_config
        )
        sample_candidates = tuple(
            sorted(
                (*sample_candidates, *third_candidates),
                key=lambda item: item.candidate_id,
            )
        )
        raw.scans = (*raw.scans, *third_raw.scans)
        if scenario == "ineligible":
            raw.intensity[:] = 0.0
            sample_candidates = _current_candidates(
                raw, raw_file=path, settings=settings, peak_config=peak_config
            )
        raws[path] = raw
        if scenario == "recovery" and path == paths[-1]:
            candidates[path.stem] = ()
        elif scenario == "below_threshold" and path != paths[0]:
            candidates[path.stem] = ()
        elif scenario == "blank_missing" and path == paths[-1]:
            candidates[path.stem] = ()
        else:
            candidates[path.stem] = sample_candidates
        if scenario == "partial_recovery" and path == paths[-1]:
            candidates[path.stem] = sample_candidates[:1]
        if scenario == "cross_subject_recovery":
            precursor = 500.0 if path == paths[0] else 500.004
            candidates[path.stem] = (
                tuple(c for c in sample_candidates if c.precursor_mz == precursor)
                if path in paths[:2]
                else ()
            )
    plan = prepare_eager_untargeted_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=index + 1,
                sample_role=(
                    "blank"
                    if scenario == "blank_missing" and path == paths[-1]
                    else "study_sample"
                ),
            )
            for index, path in enumerate(paths)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:bounded-runtime",
    )
    opened = _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)
    if opened_sink is not None:
        opened_sink.append(opened)
    import xic_extractor.untargeted_execution.partition.execution as runtime_owner

    monkeypatch.setattr(
        runtime_owner,
        "ensure_assemblies_loaded",
        lambda *_args: None,
    )
    reference = None
    if scenario == "istd":
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
                "ISTD Pair",
            ]
        )
        targets.append(["known", "ISTD", 500, 7, 10, 20, 116.0474, 20, 50, ""])
        results = workbook.create_sheet("XIC Results")
        results.append(["SampleName", "Target", "Role", "RT", "Area"])
        for path in paths:
            results.append([path.stem, "known", "ISTD", 7.98, -999])
        reference_path = tmp_path / "reference.xlsx"
        workbook.save(reference_path)
        reference = read_known_istd_reference(reference_path)
    assert len(plan.subject_batch.subjects) == (
        2 if scenario == "cross_subject_recovery" else 3
    )
    return plan, reference


def _phase_partitions(adapter, store, *, reverse_initial=False):
    for index, phase in enumerate(adapter.execution_phases()):
        if phase.prepare_before is not None:
            phase.prepare_before(store, lambda: None)
        partitions = phase.partitions
        if index == 0 and reverse_initial:
            partitions = tuple(reversed(partitions))
        yield from partitions


def test_batch_rt_calibration_and_grouping_are_independent_of_partition_capacity(
    tmp_path, monkeypatch
):
    import numpy as np

    from xic_extractor.execution.result_store import StreamedResultStore
    from xic_extractor.untargeted_execution.execution import (
        UntargetedPartitionPipelineAdapter,
    )

    settings = replace(_settings(), precursor_mz_tolerance_ppm=1.0)
    peak_config = _peak_config(tmp_path)
    paths = tuple(tmp_path / f"S{i:02}.synthetic" for i in range(24))
    raws, candidates = {}, {}
    for index, path in enumerate(paths):
        path.write_bytes(path.stem.encode())
        shift = 0.1 if index == 12 else 0.0
        raw = _FakeRaw(tuple(7.985 + feature * 0.0025 + shift for feature in range(12)))
        raw.intensity = 1000 * np.exp(-0.5 * ((raw.rt - 8.0 - shift) / 0.035) ** 2)
        raw.scans = tuple(
            replace(
                scan,
                precursor_mz=500.0 + feature,
                masses=np.asarray((500.0 + feature - 116.0474, 450.0 + feature)),
            )
            for feature, scan in enumerate(raw.scans)
        )
        raws[path] = raw
        candidates[path.stem] = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            peak_config=peak_config,
        )
        assert len(candidates[path.stem]) == 12
    plan = prepare_eager_untargeted_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=i,
                sample_role="study_sample",
            )
            for i, path in enumerate(paths, 1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:partition-invariant-warp",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)

    def run(capacity, *, reverse_initial=False):
        adapter = UntargetedPartitionPipelineAdapter(
            plan,
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            max_partition_jobs=capacity,
            raw_workers=1,
        )
        store = StreamedResultStore(
            tmp_path / f"spool-{capacity}-{reverse_initial}",
            adapter.get_shared_objects(),
        )
        warps, memberships = [], []
        try:
            for partition in _phase_partitions(
                adapter, store, reverse_initial=reverse_initial
            ):
                acquired = adapter.execute_acquisition(partition)
                assert acquired.succeeded
                prepared = acquired.result[1].prepared_grouping
                assert prepared is not None
                if prepared.rt_warp is not None:
                    warps.append(prepared.rt_warp)
                    memberships.extend(
                        tuple(group.owner_ids)
                        for workset in prepared.worksets
                        for group in workset.grouping_scope.groups
                    )
                adapter.execute_composition(partition, acquired, store)
            assert warps and all(warp == warps[0] for warp in warps)
            return warps[0], tuple(sorted(memberships))
        finally:
            adapter.close_process_worker_pools()
            store.cleanup()

    one_feature_at_a_time = run(24)
    all_features_together = run(24 * 12)
    assert one_feature_at_a_time == all_features_together
    assert run(24, reverse_initial=True) == one_feature_at_a_time
    warp, groups = one_feature_at_a_time
    assert warp.anchor_group_count == 12
    assert warp.has_support("S12")
    assert warp.offset_min("S12", 8.0) == pytest.approx(0.1, abs=0.005)
    assert len(groups) == 12 and all(len(group) == 24 for group in groups)


def test_backfill_queries_are_partition_invariant_and_never_supply_ms2_support(
    tmp_path, monkeypatch
):
    import numpy as np

    from xic_extractor.execution.result_store import StreamedResultStore
    from xic_extractor.untargeted_execution.execution import (
        UntargetedPartitionPipelineAdapter,
    )

    settings = replace(_settings(), precursor_mz_tolerance_ppm=1.0)
    peak_config = _peak_config(tmp_path)
    paths = tuple(tmp_path / f"S{i:02}.synthetic" for i in range(22))
    raws, candidates = {}, {}
    for index, path in enumerate(paths):
        path.write_bytes(path.stem.encode())
        shift = {20: 0.1, 21: -0.1}.get(index, 0.0)
        raw = _FakeRaw(tuple(7.985 + feature * 0.0025 + shift for feature in range(2)))
        raw.intensity = 1000 * np.exp(-0.5 * ((raw.rt - 8.0 - shift) / 0.035) ** 2)
        raw.scans = tuple(
            replace(
                scan,
                precursor_mz=500.0 + feature * 1.00335483507,
                masses=np.asarray(
                    (500.0 + feature * 1.00335483507 - 116.0474, 450.0 + feature)
                ),
            )
            for feature, scan in enumerate(raw.scans)
        )
        raws[path] = raw
        candidates[path.stem] = _current_candidates(
            raw,
            raw_file=path,
            settings=settings,
            peak_config=peak_config,
        )
        assert len(candidates[path.stem]) == 2
        if index >= 20:
            candidates[path.stem] = tuple(
                c for c in candidates[path.stem] if c.precursor_mz < 500.5
            )
        if index == 21:
            # The target MS1 signal remains without independent MS2 support.
            # A correct Backfill query cannot supply it.
            raw.scans = tuple(s for s in raw.scans if s.precursor_mz < 500.5)
    plan = prepare_eager_untargeted_plan(
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=path.stem,
                raw_stem=path.stem,
                injection_order=i,
                sample_role="study_sample",
            )
            for i, path in enumerate(paths, 1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=_discovery_receipts(tmp_path, paths, candidates),
        dll_dir=tmp_path,
        sample_identity_namespace="study:partition-invariant-warp",
    )
    _install_runtime_raws(monkeypatch, sources_by_path=raws, tmp_path=tmp_path)

    def run(capacity):
        adapter = UntargetedPartitionPipelineAdapter(
            plan,
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            max_partition_jobs=capacity,
            raw_workers=1,
        )
        store = StreamedResultStore(
            tmp_path / f"spool-{capacity}", adapter.get_shared_objects()
        )
        queries = {}
        try:
            for partition in _phase_partitions(adapter, store):
                acquired = adapter.execute_acquisition(partition)
                assert acquired.succeeded
                for result in acquired.result[1].sample_results:
                    for attempt in result.backfill_attempts:
                        stem = result.job.sample.metadata.raw_stem
                        if stem == "S21":
                            assert attempt.assessment.content.method_result != "SUPPORT"
                        receipt = attempt.assessment.content.evaluation_receipt
                        queries[result.job.sample.metadata.raw_stem] = (
                            attempt.query_rt,
                            attempt.query_rt_basis,
                            receipt.query_rt_min,
                            receipt.query_rt_max,
                            attempt.assessment.candidate_peaks[
                                0
                            ].content.integration.rt_apex_min,
                        )
                adapter.execute_composition(partition, acquired, store)
            assert set(queries) == {"S20", "S21"}
            for sample, (rt, basis, lower, upper, apex) in queries.items():
                expected = {"S20": 8.1, "S21": 7.9}[sample]
                assert apex == pytest.approx(expected, abs=0.005)
                assert rt == pytest.approx(8.0, abs=0.005)
                assert basis.startswith("trajectory_")
                half_window = peak_config.nl_rt_anchor_half_window_min
                assert lower == pytest.approx(rt - half_window)
                assert upper == pytest.approx(rt + half_window)
            return queries
        finally:
            adapter.close_process_worker_pools()
            store.cleanup()

    separate = run(22)
    together = run(44)
    assert separate == together


def test_bounded_partition_keeps_alignment_workset_atomic(
    tmp_path,
    monkeypatch,
) -> None:
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
        mint_untargeted_cid_nl_method_subject_batch,
    )
    from xic_extractor.untargeted_execution.execution import (
        UntargetedPartitionPipelineAdapter,
    )

    plan, _reference = _plan(tmp_path, monkeypatch)
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=mz,
                    precursor_tolerance_ppm=20.0,
                    neutral_loss_da=116.0474,
                )
                for mz in (500.0, 500.015, 500.05)
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    memberships = tuple(
        (f"candidate-{index}", subject.method_subject_id)
        for index, subject in enumerate(subjects)
    )
    packing_plan = replace(
        plan,
        subject_batch=mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects,
            candidate_memberships=memberships,
        ),
        runtime_subjects=subjects,
    )
    sample_count = len(plan.context.samples)

    adapter = UntargetedPartitionPipelineAdapter(
        packing_plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=sample_count * 2,
    )

    assert {
        frozenset(subject.precursor_mz for subject in partition.payload)
        for partition in adapter.partitions
    } == {frozenset({500.0, 500.015}), frozenset({500.05})}
    with pytest.raises(
        ValueError,
        match="cannot contain one alignment subject workset",
    ):
        UntargetedPartitionPipelineAdapter(
            packing_plan,
            alignment_config=AlignmentConfig(),
            max_partition_jobs=sample_count,
        )


def test_indexed_mass_partition_keeps_closed_worksets_and_budgets_neighbors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.peak_detection.cid_nl_method_subject import (
        cid_nl_alignment_subject_worksets,
        mint_untargeted_cid_nl_method_subject,
        mint_untargeted_cid_nl_method_subject_batch,
    )
    from xic_extractor.untargeted_execution import execution as bounded_owner
    from xic_extractor.untargeted_execution.input_planning import (
        PreparedUntargetedAlignmentWorksets,
    )

    plan, _reference = _plan(tmp_path, monkeypatch)
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=mz,
                    precursor_tolerance_ppm=20.0,
                    neutral_loss_da=loss,
                )
                for mz, loss in (
                    (99.0, 116.0474),
                    (201.0, 116.0474),
                    (250.0, 114.0),
                    (251.0, 112.0),
                )
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    worksets = cid_nl_alignment_subject_worksets(
        subjects,
        comparison_mz_intervals=((90.0, 210.0),),
    )
    closed = next(workset for workset in worksets if len(workset) == 2)
    assert {subject.precursor_mz for subject in closed} == {99.0, 201.0}
    membership_counts = {
        subject.method_subject_id: (
            600 if subject.precursor_mz in {250.0, 251.0} else 1
        )
        for subject in subjects
    }
    memberships = tuple(
        sorted(
            (
                f"candidate-{subject.method_subject_id}-{index:04d}",
                subject.method_subject_id,
            )
            for subject in subjects
            for index in range(membership_counts[subject.method_subject_id])
        )
    )
    packing_plan = replace(
        plan,
        subject_batch=mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects,
            candidate_memberships=memberships,
        ),
        runtime_subjects=subjects,
        partition_input_loader=lambda _candidate_ids: ({}, {}, {}),
    )
    memberships_by_subject = {
        subject.method_subject_id: tuple(
            membership
            for membership in memberships
            if membership[1] == subject.method_subject_id
        )
        for subject in subjects
    }
    prepared = PreparedUntargetedAlignmentWorksets(
        plan=packing_plan,
        worksets=worksets,
        subjects_by_id={subject.method_subject_id: subject for subject in subjects},
        workset_ids_by_subject={
            subject.method_subject_id: frozenset(
                member.method_subject_id for member in workset
            )
            for workset in worksets
            for subject in workset
        },
        memberships_by_subject=memberships_by_subject,
    )
    monkeypatch.setattr(
        bounded_owner,
        "prepare_untargeted_alignment_worksets",
        lambda active_plan, **_kwargs: (
            prepared
            if active_plan is packing_plan
            else pytest.fail("partition preparation crossed its indexed plan")
        ),
    )

    adapter = bounded_owner.UntargetedPartitionPipelineAdapter(
        packing_plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=len(plan.context.samples) * len(subjects),
    )
    payloads = tuple(
        adapter_partition.payload for adapter_partition in adapter.bootstrap_partitions
    )

    assert any(set(payload) == set(closed) for payload in payloads)
    assert all(
        not ({250.0, 251.0} <= {subject.precursor_mz for subject in payload})
        for payload in payloads
    )
    assert len(payloads) == 3


def test_indexed_finalization_orders_children_by_canonical_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from xic_extractor.untargeted_execution import execution as bounded_owner
    from xic_extractor.untargeted_execution import (
        partition_composition as composition_owner,
    )

    plan, _reference = _plan(tmp_path, monkeypatch)
    indexed_plan = replace(
        plan,
        partition_input_loader=lambda _candidate_ids: ({}, {}, {}),
    )
    adapter = bounded_owner.UntargetedPartitionPipelineAdapter(
        indexed_plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=len(plan.context.samples),
    )
    canonical_ids = tuple(
        subject.method_subject_id for subject in plan.subject_batch.subjects
    )
    mass_execution_ids = tuple(reversed(canonical_ids))

    class StoredChildren:
        def sequence(self, field_name: str):
            assert field_name == "children"
            return (None,) * len(mass_execution_ids)

        def read_selected(
            self,
            index: int,
            field_name: str,
            positions: tuple[int, ...],
        ):
            assert field_name == "children"
            assert positions == (0,)
            return (mass_execution_ids[index],)

    outputs = tuple(
        SimpleNamespace(
            result=SimpleNamespace(
                excluded_candidate_ids=(),
                child_order_keys=(subject_id,),
                sealed=SimpleNamespace(index=index),
            )
        )
        for index, subject_id in enumerate(mass_execution_ids)
    )
    captured = []

    def capture_finalization(request):
        captured.append(request)
        return request

    monkeypatch.setattr(
        composition_owner,
        "finalize_alignment_result",
        capture_finalization,
    )
    monkeypatch.setattr(
        composition_owner,
        "UntargetBatchApplicationResultRequest",
        lambda **values: SimpleNamespace(**values),
    )

    finalized = adapter.finalize_application(StoredChildren(), outputs)

    assert captured == [finalized]
    assert tuple(finalized.children) == canonical_ids


def test_prepared_workset_index_stores_one_id_set_per_workset(
    tmp_path,
    monkeypatch,
) -> None:
    """Members of a workset share its id set instead of each rebuilding it.

    Content equality alone does not catch the difference: an index built per
    subject holds the same k ids k times, so a cohort of wide worksets pays
    O(k**2) for an index whose content is O(k).
    """

    from dataclasses import replace

    from xic_extractor.peak_detection.cid_nl_method_subject import (
        mint_untargeted_cid_nl_method_subject,
        mint_untargeted_cid_nl_method_subject_batch,
    )
    from xic_extractor.untargeted_execution import execution as bounded_owner

    plan, _reference = _plan(tmp_path, monkeypatch)
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=precursor_mz,
                    precursor_tolerance_ppm=20.0,
                    neutral_loss_da=neutral_loss_da,
                )
                for precursor_mz, neutral_loss_da in (
                    (300.0, 116.0474),
                    (300.005, 116.0474),
                    (300.003, 114.0),
                    (300.004, 114.0),
                )
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    packing_plan = replace(
        plan,
        subject_batch=mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects,
            candidate_memberships=tuple(
                (f"candidate-{index}", subject.method_subject_id)
                for index, subject in enumerate(subjects)
            ),
        ),
        runtime_subjects=subjects,
    )
    prepared = bounded_owner.prepare_untargeted_alignment_worksets(packing_plan)

    index = prepared.workset_ids_by_subject
    assert any(len(workset) > 1 for workset in prepared.worksets)
    assert set(index) == {subject.method_subject_id for subject in subjects}
    for workset in prepared.worksets:
        expected = frozenset(subject.method_subject_id for subject in workset)
        for subject in workset:
            assert index[subject.method_subject_id] == expected
    assert len({id(member_ids) for member_ids in index.values()}) == len(
        prepared.worksets
    )


def test_bounded_partition_real_entry_mints_interleaved_worksets_canonically(
    tmp_path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from xic_extractor.peak_detection.cid_nl_method_subject import (
        cid_nl_alignment_subject_worksets,
        mint_untargeted_cid_nl_method_subject,
        mint_untargeted_cid_nl_method_subject_batch,
    )
    from xic_extractor.untargeted_execution import execution as bounded_owner

    plan, _reference = _plan(tmp_path, monkeypatch)
    subjects = tuple(
        sorted(
            (
                mint_untargeted_cid_nl_method_subject(
                    precursor_mz=precursor_mz,
                    precursor_tolerance_ppm=20.0,
                    neutral_loss_da=neutral_loss_da,
                )
                for precursor_mz, neutral_loss_da in (
                    (300.0, 116.0474),
                    (300.005, 116.0474),
                    (300.003, 114.0),
                    (300.004, 114.0),
                )
            ),
            key=lambda subject: subject.method_subject_id,
        )
    )
    worksets = cid_nl_alignment_subject_worksets(subjects)
    assert tuple(len(workset) for workset in worksets) == (2, 2)
    assert tuple(subject for workset in worksets for subject in workset) != subjects
    memberships = tuple(
        (f"candidate-{index}", subject.method_subject_id)
        for index, subject in enumerate(subjects)
    )
    packing_plan = replace(
        plan,
        subject_batch=mint_untargeted_cid_nl_method_subject_batch(
            subjects=subjects,
            candidate_memberships=memberships,
        ),
        runtime_subjects=subjects,
    )
    prepare_worksets = bounded_owner.prepare_untargeted_alignment_worksets
    prepared = prepare_worksets(packing_plan)
    assert (
        materialize_untargeted_partition(
            packing_plan,
            packing_plan.subject_batch,
            prepared_worksets=prepared,
        ).jobs
        == materialize_untargeted_partition(
            packing_plan, packing_plan.subject_batch
        ).jobs
    )
    split = worksets[0][:1]
    split_ids = {subject.method_subject_id for subject in split}
    split_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=split,
        candidate_memberships=tuple(
            membership for membership in memberships if membership[1] in split_ids
        ),
    )
    with pytest.raises(ValueError, match="cannot split one alignment subject workset"):
        materialize_untargeted_partition(
            packing_plan,
            split_batch,
            prepared_worksets=prepared,
        )
    foreign = mint_untargeted_cid_nl_method_subject(
        precursor_mz=900.0,
        precursor_tolerance_ppm=20.0,
        neutral_loss_da=116.0474,
    )
    foreign_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=(foreign,),
        candidate_memberships=(("foreign-candidate", foreign.method_subject_id),),
    )
    with pytest.raises(ValueError, match="foreign alignment subject"):
        materialize_untargeted_partition(
            packing_plan,
            foreign_batch,
            prepared_worksets=prepared,
        )
    crossed_memberships = tuple(
        (candidate_id, subjects[(index + 1) % len(subjects)].method_subject_id)
        for index, (candidate_id, _subject_id) in enumerate(memberships)
    )
    crossed_batch = mint_untargeted_cid_nl_method_subject_batch(
        subjects=subjects,
        candidate_memberships=crossed_memberships,
    )
    with pytest.raises(ValueError, match="crossed prepared candidate memberships"):
        materialize_untargeted_partition(
            packing_plan,
            crossed_batch,
            prepared_worksets=prepared,
        )
    captured_batches = []
    prepare_calls = []
    original_materialize_partition = bounded_owner.materialize_untargeted_partition

    def capture_prepare(_plan, *, known_istd_reference=None):
        prepare_calls.append(_plan.runtime_subjects)
        return prepare_worksets(
            _plan,
            known_istd_reference=known_istd_reference,
        )

    def capture_materialized_partition(
        _plan,
        subject_batch,
        *,
        prepared_worksets,
        acquisition_cache_dir=None,
        sparse=False,
    ):
        assert prepared_worksets.plan is _plan
        assert sparse is True
        captured_batches.append(subject_batch)
        return original_materialize_partition(
            _plan,
            subject_batch,
            prepared_worksets=prepared_worksets,
            acquisition_cache_dir=acquisition_cache_dir,
            sparse=sparse,
        )

    def close_without_raw(*_args, **_kwargs):
        return SimpleNamespace(
            succeeded=True,
            worker_batch=SimpleNamespace(resource_settlements=()),
            acquisitions=(),
            prepared_grouping=None,
        )

    def forbid_raw_open(*_args, **_kwargs):
        pytest.fail("partition batch mint must complete before RAW acquisition")

    monkeypatch.setattr(
        bounded_owner,
        "materialize_untargeted_partition",
        capture_materialized_partition,
    )
    monkeypatch.setattr(
        bounded_owner,
        "prepare_untargeted_alignment_worksets",
        capture_prepare,
    )
    monkeypatch.setattr(
        bounded_owner,
        "execute_partition",
        close_without_raw,
    )
    monkeypatch.setattr(_FakeThermoRaw, "__init__", forbid_raw_open)
    adapter = bounded_owner.UntargetedPartitionPipelineAdapter(
        packing_plan,
        alignment_config=AlignmentConfig(),
        max_partition_jobs=len(plan.context.samples) * 2,
    )

    assert len(adapter.bootstrap_partitions) == 2
    results = tuple(
        adapter.execute_acquisition(partition)
        for partition in adapter.bootstrap_partitions
    )

    assert all(result.succeeded for result in results)
    assert prepare_calls == [subjects]
    assert tuple(batch.subjects for batch in captured_batches) == tuple(
        partition.payload for partition in adapter.bootstrap_partitions
    )
    assert {
        subject.method_subject_id
        for batch in captured_batches
        for subject in batch.subjects
    } == {subject.method_subject_id for subject in subjects}


@pytest.mark.parametrize("scenario", ["mixed_eligibility", "ineligible"])
@pytest.mark.parametrize("raw_workers", [1, 4])
def test_bounded_runtime_skips_subjects_without_ms1_features(
    tmp_path, monkeypatch, scenario, raw_workers
):
    from xic_extractor.discovery.models import MS1_FEATURE_BACKED_STATES
    from xic_extractor.untargeted_execution.execution import (
        UntargetedPartitionPipelineAdapter,
        execute_untargeted_plan,
    )

    plan, reference = _plan(tmp_path, monkeypatch, scenario=scenario)
    request_payload = plan.subject_batch.request_payload()
    candidate_by_id = {
        candidate.candidate_id: candidate
        for candidates in plan.context.candidates_by_sample.values()
        for candidate in candidates
    }
    eligible_subject_ids = {
        subject_id
        for candidate_id, subject_id in plan.subject_batch.candidate_memberships
        if candidate_by_id[candidate_id].ms1_peak_found
        and candidate_by_id[candidate_id].discovery_candidate_state
        in MS1_FEATURE_BACKED_STATES
    }
    assert len(eligible_subject_ids) == (2 if scenario == "mixed_eligibility" else 0)
    config = AlignmentConfig(anchor_min_seed_events=1)
    adapter = UntargetedPartitionPipelineAdapter(
        plan, alignment_config=config, max_partition_jobs=len(plan.context.samples)
    )
    assert {
        subject.method_subject_id
        for partition in adapter.partitions
        for subject in partition.payload
    } == eligible_subject_ids
    eager = None
    if eligible_subject_ids:
        batch = materialize_untargeted_partition(plan, plan.subject_batch)
        runtime = execute_dense_partition(
            batch,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=OwnerPathRuntimeMarker(tmp_path / "eager.json", "eager"),
            alignment_config=config,
            known_istd_reference=reference,
        )
        eager = complete_dense_partition(
            batch,
            runtime,
            alignment_config=config,
            known_istd_reference=reference,
        )
    if not eligible_subject_ids:

        def forbid_raw_open(*args, **kwargs):
            pytest.fail("an ineligible cohort must close without opening RAW")

        monkeypatch.setattr(_FakeThermoRaw, "__init__", forbid_raw_open)
    result = execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "eligibility-output",
        alignment_config=config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "eligibility.json", "eligibility"
        ),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=raw_workers,
        pipeline_overlap=True,
    )
    assert plan.subject_batch.request_payload() == request_payload
    assert set(result.application_result.candidate_ids) == set(candidate_by_id)
    assert {
        assessment.content.method_subject.method_subject_id
        for assessment in result.method_assessments
    } == eligible_subject_ids
    skipped = {
        candidate_id
        for candidate_id, subject_id in plan.subject_batch.candidate_memberships
        if subject_id not in eligible_subject_ids
    }
    exclusions = {
        exclusion.candidate_id: exclusion
        for exclusion in result.application_result.excluded_candidates
    }
    assert skipped <= exclusions.keys()
    for candidate_id in skipped:
        exclusion = exclusions[candidate_id]
        assert exclusion.reason_code == "discovery_candidate_not_search_eligible"
        assert not exclusion.method_assessments
        assert not exclusion.composite_assessments
    assert bool(result.application_result.children) is bool(eligible_subject_ids)
    writer = project_completed_untarget_batch_writer_inputs(result.application_result)
    if eager is not None:
        assert writer == project_completed_untarget_batch_writer_inputs(
            eager.application_result
        )
        assert {
            assessment.artifact.evidence_artifact_id: assessment
            for assessment in result.method_assessments
        } == {
            assessment.artifact.evidence_artifact_id: assessment
            for assessment in eager.method_assessments
            if assessment.content.method_subject.method_subject_id
            in eligible_subject_ids
        }
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_cid_nl_explanation,
    )
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    matrix_path = tmp_path / "eligibility.tsv"
    write_alignment_matrix_tsv(
        matrix_path,
        writer.matrix,
        alignment_config=config,
        production_decisions=writer.production_decisions,
    )
    report = write_cid_nl_explanation(
        tmp_path / "eligibility.sqlite",
        completed=result,
        writer_inputs=writer,
        candidates_by_sample=plan.context.candidates_by_sample,
        matrix_path=matrix_path,
        alignment_config=config,
    )
    with sqlite3.connect(report) as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[
            0
        ] == len(candidate_by_id)
        assert connection.execute(
            "SELECT COUNT(*) FROM records WHERE kind = 'method_assessments'"
        ).fetchone()[0] == len(result.method_assessments)


@pytest.mark.parametrize("subjects_per_partition", [1, 2])
@pytest.mark.parametrize("raw_workers", [1, 2, 4])
@pytest.mark.parametrize("scenario", ["positive", "recovery", "istd"])
def test_bounded_runtime_matches_eager_after_disk_owner_round_trip(
    tmp_path: Path,
    monkeypatch,
    scenario: str,
    subjects_per_partition: int,
    raw_workers: int,
):
    import xic_extractor.execution.timing as timing_owner
    from xic_extractor.execution.timing import TimingRecorder
    from xic_extractor.untargeted_execution.execution import (
        execute_untargeted_plan,
        prepare_untargeted_alignment_worksets,
    )

    plan, reference = _plan(tmp_path, monkeypatch, scenario=scenario)
    prepared_worksets = prepare_untargeted_alignment_worksets(
        plan,
        known_istd_reference=reference,
    )
    if scenario == "istd":
        assert tuple(len(workset) for workset in prepared_worksets.worksets) == (3,)
    import xic_extractor.untargeted_execution.partition.execution as runtime_owner

    coordinator_thread = get_ident()
    warm_threads = []
    monkeypatch.setattr(
        runtime_owner,
        "ensure_assemblies_loaded",
        lambda *_args: warm_threads.append(get_ident()),
        raising=False,
    )
    config = AlignmentConfig(anchor_min_seed_events=1)
    batch = materialize_untargeted_partition(plan, plan.subject_batch)
    eager_runtime = execute_dense_partition(
        batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "eager.json", "eager"),
        alignment_config=config,
        known_istd_reference=reference,
    )
    eager = complete_dense_partition(
        batch, eager_runtime, alignment_config=config, known_istd_reference=reference
    )
    warm_threads.clear()
    marker = OwnerPathRuntimeMarker(tmp_path / "bounded.json", "bounded")
    progress_events = []
    live_path = tmp_path / "alignment-live.json"
    recorder = TimingRecorder("alignment", live_output_path=live_path)
    monkeypatch.setattr(timing_owner, "perf_counter", lambda: 1.0)
    writes = []
    write_live = recorder._write_live

    def record_write():
        writes.append(dict(recorder._heartbeat))
        write_live()

    monkeypatch.setattr(recorder, "_write_live", record_write)

    def report_progress(event):
        assert get_ident() == coordinator_thread
        recorder.heartbeat_event(event)
        progress_events.append(event)

    with recorder.stage("alignment.untargeted_cid_nl_runtime"):
        partition_subject_capacity = (
            len(prepared_worksets.worksets[0])
            if scenario == "istd"
            else subjects_per_partition
        )
        bounded = execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "bounded-output",
            alignment_config=config,
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            known_istd_reference=reference,
            progress_callback=report_progress,
            max_partition_jobs=(partition_subject_capacity * len(plan.context.samples)),
            raw_workers=raw_workers,
        )
    assert type(bounded) is CompletedUntargetBatchWorkflowResult
    assert bool(warm_threads) is (raw_workers > 1)
    assert all(thread == coordinator_thread for thread in warm_threads)
    assert type(bounded.application_result.children) is ImmutableSequence
    bounded_writer = project_completed_untarget_batch_writer_inputs(
        bounded.application_result
    )
    eager_writer = project_completed_untarget_batch_writer_inputs(
        eager.application_result
    )
    assert bounded_writer.matrix == eager_writer.matrix
    if scenario == "istd":
        expected_areas = {
            (cell.cluster_id, cell.sample_stem): cell.area
            for cell in eager_writer.matrix.cells
            if cell.area is not None
        }
        actual_areas = {
            (cell.cluster_id, cell.sample_stem): cell.area
            for cell in bounded_writer.matrix.cells
            if cell.area is not None
        }
        assert expected_areas
        assert actual_areas == pytest.approx(expected_areas)
        assert all(area > 0 for area in actual_areas.values())
    actual = bounded.application_result
    expected = eager.application_result
    assert actual.candidate_ids == expected.candidate_ids
    assert actual.physical_observation_ids == expected.physical_observation_ids
    assert tuple(actual.excluded_candidates) == expected.excluded_candidates
    owners = plan.context.processing_owners
    assert actual.processing_request is owners.request
    assert actual.processing_attempt is owners.attempt
    assert bounded.runtime_receipt.supervisor.attempt is owners.attempt
    assert all(
        left is right
        for left, right in zip(
            actual.canonical_samples, expected.canonical_samples, strict=True
        )
    )
    for child in actual.children:
        assert child.processing_request is owners.request
        assert (
            child.scientific_core.cell_observation_handoff.coverage.epoch.processing_owners
            is owners
        )
        assert all(
            left is right
            for left, right in zip(
                child.canonical_samples, expected.canonical_samples, strict=True
            )
        )
    assert sorted(
        item.artifact.evidence_artifact_id for item in bounded.method_assessments
    ) == sorted(item.artifact.evidence_artifact_id for item in eager.method_assessments)
    _assert_explanation_parity(tmp_path, plan, config, eager, bounded)
    partition_stages = [
        (event["stage"], event["completed"])
        for event in progress_events
        if event.get("event") == "partition_progress"
        and event["stage"]
        in {
            "partition_start",
            "partition_compose",
            "partition_released",
            "global_closure",
            "batch_completed",
        }
    ]
    partition_count = (
        len(plan.subject_batch.subjects) + partition_subject_capacity - 1
    ) // partition_subject_capacity
    if scenario == "istd" and subjects_per_partition == 1:
        assert partition_count < len(plan.subject_batch.subjects)
    phase_count = 2 * partition_count
    assert partition_stages == [
        event
        for index in range(phase_count)
        for event in (
            ("partition_start", index),
            ("partition_compose", index),
            ("partition_released", index + 1),
        )
    ] + [("global_closure", phase_count), ("batch_completed", phase_count)]
    seal_writes = [row for row in writes if row.get("stage") == "partition_seal"]
    assert len(seal_writes) <= 2 * phase_count
    heartbeat = json.loads(live_path.read_text(encoding="utf-8"))["heartbeat"]
    assert heartbeat["stage"] == "batch_completed"
    assert heartbeat["completed"] == heartbeat["total"] == phase_count
    eager_manifest = eager_runtime.finalization.coordinator.state.runtime.manifest
    assert len(eager_manifest.work_unit_keys) == len(batch.jobs)
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert len(snapshot.manifest_work_unit_keys) == (
        partition_count * len(plan.context.samples) * 2 + 1
    )
    assert actual.children
    if scenario == "recovery":
        assert any(result.backfill_attempts for result in eager_runtime.sample_results)


def _assert_explanation_parity(tmp_path, plan, config, eager, bounded):
    from xic_extractor.diagnostics.alignment_explanation import read_cid_nl_explanation
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_cid_nl_explanation,
    )
    from xic_extractor.output.alignment.tsv_writer import write_alignment_matrix_tsv

    candidate_id = plan.subject_batch.candidate_memberships[0][0]
    records = []
    for name, completed in (("eager", eager), ("bounded", bounded)):
        writer = project_completed_untarget_batch_writer_inputs(
            completed.application_result
        )
        matrix = tmp_path / f"{name}.tsv"
        write_alignment_matrix_tsv(
            matrix,
            writer.matrix,
            alignment_config=config,
            production_decisions=writer.production_decisions,
        )
        report = write_cid_nl_explanation(
            tmp_path / f"{name}.sqlite",
            completed=completed,
            writer_inputs=writer,
            candidates_by_sample=plan.context.candidates_by_sample,
            matrix_path=matrix,
            alignment_config=config,
        )
        detail = read_cid_nl_explanation(report, candidate_id=candidate_id)
        records.append(detail["records"])
    assert records[0] == records[1]


@pytest.mark.parametrize("failure_seam", ["later_partition", "later_spool"])
def test_bounded_runtime_failure_never_publishes_completed_batch(
    tmp_path: Path, monkeypatch, failure_seam: str
):
    import xic_extractor.untargeted_execution.execution as bounded_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    marker = OwnerPathRuntimeMarker(tmp_path / "failed.json", "failed")
    original_error = OSError("second partition storage unavailable")
    calls = []
    if failure_seam == "later_partition":
        seam_owner = bounded_owner
        seam_name = "execute_partition"
    else:
        seam_owner = bounded_owner.StreamedResultStore
        seam_name = "append_children"
    original = getattr(seam_owner, seam_name)

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise original_error
        return original(*args, **kwargs)

    monkeypatch.setattr(seam_owner, seam_name, fail_second)

    def forbid_completed(*args, **kwargs):
        pytest.fail("An incomplete partition must not publish a completed result")

    monkeypatch.setattr(
        bounded_owner, "CompletedUntargetBatchWorkflowResult", forbid_completed
    )
    with pytest.raises(RuntimeError, match="failed before batch publication") as error:
        bounded_owner.execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "failed-output",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
        )
    assert len(calls) == 2
    assert error.value.__cause__ is original_error
    (retained,) = (tmp_path / "failed-output").glob(
        ".partition_spool-*/failed-results.pickle"
    )
    assert any(str(retained) in note for note in error.value.__notes__)
    snapshot = marker.read()
    assert snapshot.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert snapshot.processing_attempt_id == (
        plan.context.processing_owners.attempt.processing_attempt_id
    )


def test_failed_attempt_retry_reuses_completed_raw_facts_and_recomposes(
    tmp_path: Path, monkeypatch
) -> None:
    import xic_extractor.untargeted_execution.execution as owner

    ms2_reads: dict[int, int] = {}
    original_filter = _FakeThermoRaw.GetFilterForScanNumber

    def count_filter(vendor, scan_number):
        key = id(vendor)
        ms2_reads[key] = ms2_reads.get(key, 0) + 1
        return original_filter(vendor, scan_number)

    monkeypatch.setattr(_FakeThermoRaw, "GetFilterForScanNumber", count_filter)
    baseline_plan, _ = _plan(tmp_path, monkeypatch)
    baseline = owner.execute_untargeted_plan(
        baseline_plan,
        output_dir=tmp_path / "cold-output",
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "cold.json", "cold"),
        max_partition_jobs=len(baseline_plan.context.samples),
        pipeline_overlap=False,
    )
    baseline_writer = project_completed_untarget_batch_writer_inputs(
        baseline.application_result
    )
    opened_runs = []
    plan, _ = _plan(tmp_path, monkeypatch, opened_sink=opened_runs)
    output_dir = tmp_path / "retry-output"
    marker = OwnerPathRuntimeMarker(
        tmp_path / "retry.json",
        "retry",
        allow_failed_attempt_retry=True,
    )
    original_append = owner.StreamedResultStore.append_children
    append_calls = 0

    def fail_second(store, *args, **kwargs):
        nonlocal append_calls
        append_calls += 1
        if append_calls == 2:
            raise OSError("second partition storage unavailable")
        return original_append(store, *args, **kwargs)

    monkeypatch.setattr(owner.StreamedResultStore, "append_children", fail_second)
    with pytest.raises(RuntimeError, match="failed before batch publication"):
        owner.execute_untargeted_plan(
            plan,
            output_dir=output_dir,
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
            pipeline_overlap=False,
        )
    failed_attempt_id = plan.context.processing_owners.attempt.processing_attempt_id
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED

    monkeypatch.setattr(owner.StreamedResultStore, "append_children", original_append)
    retry_plan, _ = _plan(tmp_path, monkeypatch, opened_sink=opened_runs)
    completed = owner.execute_untargeted_plan(
        retry_plan,
        output_dir=output_dir,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=marker,
        max_partition_jobs=len(retry_plan.context.samples),
        pipeline_overlap=False,
    )

    assert completed.runtime_receipt.supervisor.attempt is (
        retry_plan.context.processing_owners.attempt
    )
    assert (
        retry_plan.context.processing_owners.attempt.processing_attempt_id
        != failed_attempt_id
    )
    writer = project_completed_untarget_batch_writer_inputs(
        completed.application_result
    )
    assert writer.matrix.cells
    assert writer.matrix == baseline_writer.matrix
    assert completed.application_result.candidate_ids == (
        baseline.application_result.candidate_ids
    )
    assert completed.application_result.physical_observation_ids == (
        baseline.application_result.physical_observation_ids
    )
    completed_samples = tuple(
        sample.metadata for sample in completed.application_result.canonical_samples
    )
    baseline_samples = tuple(
        sample.metadata for sample in baseline.application_result.canonical_samples
    )
    assert completed_samples == baseline_samples
    assert all(cell.area is None or cell.area > 0 for cell in writer.matrix.cells)
    assert {
        sample.metadata.raw_stem
        for sample in completed.application_result.canonical_samples
    } == {path.stem for path in retry_plan.context.raw_paths_by_sample.values()}

    retry_vendors = tuple(
        vendor for vendors in opened_runs[1].values() for vendor in vendors
    )
    failed_vendors = tuple(
        vendor for vendors in opened_runs[0].values() for vendor in vendors
    )
    # Fully cached samples need no native handle. Measure avoided work across
    # the complete attempt, including those samples absent from retry_vendors.
    failed_queries = sum(
        sum(vendor.chromatogram_request_counts) for vendor in failed_vendors
    )
    retry_queries = sum(
        sum(vendor.chromatogram_request_counts) for vendor in retry_vendors
    )
    assert retry_queries < failed_queries
    assert sum(ms2_reads.get(id(vendor), 0) for vendor in retry_vendors) < sum(
        ms2_reads.get(id(vendor), 0) for vendor in failed_vendors
    )


def test_partition_acquisitions_overlap_with_shared_worker_budget_and_ordered_output(
    tmp_path: Path, monkeypatch
) -> None:
    import xic_extractor.untargeted_execution.execution as owner
    import xic_extractor.untargeted_execution.partition.execution as runtime_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_owner, "ensure_assemblies_loaded", lambda *_args: None)
    config = AlignmentConfig(anchor_min_seed_events=1)
    serial = owner.execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "serial-partitions",
        alignment_config=config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "serial-partitions.json", "serial-partitions"
        ),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=4,
        pipeline_overlap=False,
    )

    original_leaf = owner.execute_partition
    original_append = owner.StreamedResultStore.append_children
    first_two = {"partition-00000-initial", "partition-00001-initial"}
    barrier = Barrier(2)
    second_completed = Event()
    lock = Lock()
    active_budgets: dict[str, int] = {}
    observed_budgets: dict[str, int] = {}
    peak_budget = 0
    completion_order = []
    append_order = []
    composition_active = 0

    def leaf(*args, **kwargs):
        nonlocal peak_budget
        partition_key = kwargs["partition_key"]
        budget = kwargs["raw_workers"]
        assert kwargs["pool"].max_workers == budget
        with lock:
            active_budgets[partition_key] = budget
            observed_budgets[partition_key] = budget
            peak_budget = max(peak_budget, sum(active_budgets.values()))
        try:
            if partition_key in first_two:
                barrier.wait(timeout=10)
            result = original_leaf(*args, **kwargs)
            if partition_key == "partition-00000-initial":
                assert second_completed.wait(timeout=10)
            with lock:
                completion_order.append(partition_key)
                if partition_key == "partition-00001-initial":
                    second_completed.set()
            return result
        finally:
            with lock:
                active_budgets.pop(partition_key)

    def append(store, partition_key, *args, **kwargs):
        nonlocal composition_active
        with lock:
            assert composition_active == 0
            composition_active = 1
        try:
            sleep(0.01)
            result = original_append(store, partition_key, *args, **kwargs)
            append_order.append(partition_key)
            return result
        finally:
            with lock:
                composition_active = 0

    monkeypatch.setattr(owner, "execute_partition", leaf)
    monkeypatch.setattr(owner.StreamedResultStore, "append_children", append)
    overlapped = owner.execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "overlapped-partitions",
        alignment_config=config,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "overlapped-partitions.json", "overlapped-partitions"
        ),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=4,
        pipeline_overlap=True,
    )

    assert completion_order.index("partition-00001-initial") < completion_order.index(
        "partition-00000-initial"
    )
    assert append_order == [
        f"partition-{index:05d}-backfill" for index in range(len(observed_budgets) // 2)
    ]
    assert set(observed_budgets.values()) == {2}
    assert peak_budget == 4
    assert composition_active == 0 and not active_budgets
    assert project_completed_untarget_batch_writer_inputs(
        overlapped.application_result
    ) == (project_completed_untarget_batch_writer_inputs(serial.application_result))
    assert overlapped.application_result.candidate_ids == (
        serial.application_result.candidate_ids
    )
    assert overlapped.application_result.physical_observation_ids == (
        serial.application_result.physical_observation_ids
    )


def test_overlapped_partition_acquisition_failure_propagates(
    tmp_path: Path, monkeypatch
) -> None:
    import xic_extractor.untargeted_execution.execution as owner
    import xic_extractor.untargeted_execution.partition.execution as runtime_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_owner, "ensure_assemblies_loaded", lambda *_args: None)
    original_leaf = owner.execute_partition
    barrier = Barrier(2)
    failure_released = Event()
    injected_error = OSError("injected concurrent acquisition failure")
    observed_budgets = {}

    def leaf(*args, **kwargs):
        partition_key = kwargs["partition_key"]
        observed_budgets[partition_key] = kwargs["raw_workers"]
        if partition_key in {"partition-00000-initial", "partition-00001-initial"}:
            barrier.wait(timeout=10)
        result = original_leaf(*args, **kwargs)
        if partition_key == "partition-00001-initial":
            failure_released.set()
            raise injected_error
        if partition_key == "partition-00000-initial":
            assert failure_released.wait(timeout=10)
        return result

    monkeypatch.setattr(owner, "execute_partition", leaf)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "overlapped-failure.json", "overlapped-failure"
    )
    with pytest.raises(
        RuntimeError, match="injected concurrent acquisition failure"
    ) as error:
        owner.execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "overlapped-failure",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
            raw_workers=4,
            pipeline_overlap=True,
        )
    assert error.value.__cause__ is injected_error
    assert {
        "partition-00000-initial",
        "partition-00001-initial",
    } <= observed_budgets.keys()
    assert all(budget == 2 for budget in observed_budgets.values())
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED
    assert list((tmp_path / "overlapped-failure").glob(".cid_nl_partitions-*")) == []


def test_last_bootstrap_failure_prevents_backfill_admission(tmp_path, monkeypatch):
    import xic_extractor.untargeted_execution.execution as owner

    plan, _ = _plan(tmp_path, monkeypatch)
    waiting_final = Event()
    acquire = owner.UntargetedPartitionPipelineAdapter.execute_acquisition
    compose = owner.UntargetedPartitionPipelineAdapter.execute_composition

    def observed_acquire(adapter, partition, **kwargs):
        if partition.partition_key.endswith("-backfill"):
            waiting_final.set()
        return acquire(adapter, partition, **kwargs)

    def failing_compose(adapter, partition, *args, **kwargs):
        if partition is adapter.bootstrap_partitions[-1]:
            assert not waiting_final.is_set()
            raise OSError("last bootstrap cannot be sealed")
        return compose(adapter, partition, *args, **kwargs)

    monkeypatch.setattr(
        owner.UntargetedPartitionPipelineAdapter,
        "execute_acquisition",
        observed_acquire,
    )
    monkeypatch.setattr(
        owner.UntargetedPartitionPipelineAdapter, "execute_composition", failing_compose
    )
    marker = OwnerPathRuntimeMarker(tmp_path / "last-bootstrap.json", "last-bootstrap")
    with pytest.raises(RuntimeError, match="last bootstrap cannot be sealed"):
        owner.execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "last-bootstrap",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
            raw_workers=4,
            pipeline_overlap=True,
        )
    assert not waiting_final.is_set()
    assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED


@pytest.mark.parametrize("cancel_during_grouping", [False, True])
@pytest.mark.parametrize("pipeline_overlap", [False, True])
def test_backfill_grouping_failure_stops_its_attempt_without_a_batch_barrier(
    tmp_path, monkeypatch, cancel_during_grouping, pipeline_overlap
):
    import xic_extractor.untargeted_execution.execution as owner

    plan, _ = _plan(tmp_path, monkeypatch)
    cancellation = Event()
    timeline_events = []
    lock = Lock()
    finalized = []
    admitted = []
    acquire = owner.UntargetedPartitionPipelineAdapter.execute_acquisition
    finalize = owner.UntargetedBatchGroupingPreparation.finalize_grouping

    def subject_ids(subjects):
        return frozenset(subject.method_subject_id for subject in subjects)

    def observed_acquire(adapter, partition, **kwargs):
        if partition.partition_key.endswith("-backfill"):
            with lock:
                admitted.append(subject_ids(partition.payload))
        return acquire(adapter, partition, **kwargs)

    def finalize_or_abort(preparation, batch, prepared, **kwargs):
        subjects = subject_ids(batch.subject_batch.subjects)
        with lock:
            admitted_first = subjects in admitted
        result = finalize(preparation, batch, prepared, **kwargs)
        with lock:
            finalized.append((subjects, admitted_first))
            count = len(finalized)
        if count == 2:
            if cancel_during_grouping:
                cancellation.set()
                raise InterruptedError("grouping finalization cancelled")
            raise ValueError("grouping finalization failed")
        return result

    def forbid_completed(*args, **kwargs):
        pytest.fail("A failed Backfill grouping must not publish a batch")

    monkeypatch.setattr(
        owner.UntargetedPartitionPipelineAdapter,
        "execute_acquisition",
        observed_acquire,
    )
    monkeypatch.setattr(
        owner.UntargetedBatchGroupingPreparation, "finalize_grouping", finalize_or_abort
    )
    monkeypatch.setattr(owner, "CompletedUntargetBatchWorkflowResult", forbid_completed)
    marker = OwnerPathRuntimeMarker(tmp_path / "grouping.json", "grouping")
    with pytest.raises(RuntimeError, match="failed before batch publication") as error:
        owner.execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "grouping",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            max_partition_jobs=len(plan.context.samples),
            raw_workers=4,
            pipeline_overlap=pipeline_overlap,
            should_cancel=cancellation.is_set,
            progress_callback=lambda event: (
                timeline_events.append(event)
                if event.get("event") == "partition_timeline"
                else None
            ),
        )
    # Each Backfill partition finalizes only its own grouping after admission;
    # no partition waits for every other grouping in the batch.
    assert len(finalized) >= 2
    assert all(admitted_first for _, admitted_first in finalized)
    assert len({subjects for subjects, _ in finalized}) == len(finalized)
    assert len(timeline_events) == 1
    backfill_statuses = {
        record["status"]
        for record in timeline_events[0]["timeline"]
        if record["partition_key"].endswith("-backfill")
    }
    if cancel_during_grouping:
        assert "cancelled" in backfill_statuses
        assert "failed" not in backfill_statuses
        assert marker.read().terminal_outcome is AttemptTerminalOutcome.CANCELLED
    else:
        assert "failed" in backfill_statuses
        assert "grouping finalization failed" in str(error.value)
        assert marker.read().terminal_outcome is AttemptTerminalOutcome.FAILED


def test_backfill_requires_frozen_batch_calibration(tmp_path, monkeypatch):
    import xic_extractor.untargeted_execution.execution as owner

    plan, _ = _plan(tmp_path, monkeypatch)
    adapter = owner.UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=1,
    )
    try:
        with pytest.raises(RuntimeError, match="requires frozen batch calibration"):
            adapter.execute_acquisition(adapter.partitions[-1])
    finally:
        adapter.close_process_worker_pools()


@pytest.mark.parametrize("raw_workers", [1, 2, 4])
@pytest.mark.parametrize("managed_call", [False, True])
def test_sample_parallelism_keeps_partition_and_acquisition_counts_fixed(
    tmp_path: Path, monkeypatch, managed_call: bool, raw_workers: int
):
    import xic_extractor.untargeted_execution.execution as owner
    import xic_extractor.untargeted_execution.partition.execution as runtime_owner

    if managed_call:
        pytest.importorskip("clr")
        from System.Threading import Thread

        def pause():
            Thread.Sleep(100)
    else:

        def pause():
            sleep(0.1)

    plan, _ = _plan(tmp_path, monkeypatch, scenario="recovery")
    monkeypatch.setattr(runtime_owner, "ensure_assemblies_loaded", lambda *_args: None)
    overlap_count = min(raw_workers, 3)  # Three samples have initial candidates.
    barrier = Barrier(overlap_count)
    lock = Lock()
    coordinator = get_ident()
    intervals = []
    selected_calls = 0
    handle_threads = {}
    active_handles = set()
    disposed = set()
    peak_handles = 0
    live_jobs = 0
    job_counts = []
    append_order = []
    barrier_checks = []
    acquisitions = []
    original_materialize = owner.materialize_untargeted_partition
    original_chromatogram = _FakeThermoRaw.GetChromatogramData
    original_init = _FakeThermoRaw.__init__
    original_dispose = _FakeThermoRaw.Dispose
    original_append = owner.StreamedResultStore.append_children
    original_prepare = runtime_owner._prepare_group_authorized_backfills
    original_execute = owner.execute_runtime_attempt

    def materialize(*args, **kwargs):
        nonlocal live_jobs
        assert get_ident() == coordinator and live_jobs == 0
        batch = original_materialize(*args, **kwargs)
        from xic_extractor.untargeted_execution.partition.models import (
            _request_subject_candidates,
        )

        expected_initial_sample_ids = {
            sample.identity.sample_identity_id
            for sample in plan.context.samples
            if plan.context.candidates_by_sample[sample.metadata.raw_stem]
        }
        assert {
            job.sample.identity.sample_identity_id for job in batch.jobs
        } == expected_initial_sample_ids
        assert all(_request_subject_candidates(job) for job in batch.jobs)
        assert all(
            job.processing_owners is plan.context.processing_owners
            for job in batch.jobs
        )
        live_jobs = len(batch.jobs)
        job_counts.append(live_jobs)
        return batch

    def initialize(handle, *args):
        nonlocal peak_handles
        original_init(handle, *args)
        with lock:
            handle_threads[handle] = get_ident()
            active_handles.add(handle)
            peak_handles = max(peak_handles, len(active_handles))

    def dispose(handle):
        assert handle_threads[handle] == get_ident()
        original_dispose(handle)
        with lock:
            disposed.add(handle)
            active_handles.remove(handle)

    def chromatogram(handle, *args):
        nonlocal selected_calls
        assert handle_threads[handle] == get_ident()
        with lock:
            selected = selected_calls < overlap_count
            selected_calls += 1
        if selected:
            barrier.wait(timeout=10)
            start = perf_counter()
            pause()
            intervals.append((start, perf_counter()))
        return original_chromatogram(handle, *args)

    def prepare(*args, **kwargs):
        assert get_ident() == coordinator and not active_handles
        barrier_checks.append(len(disposed))
        return original_prepare(*args, **kwargs)

    def append(store, partition_key, *args, **kwargs):
        assert get_ident() == coordinator and not active_handles
        append_order.append(partition_key)
        return original_append(store, partition_key, *args, **kwargs)

    def progress(event):
        nonlocal live_jobs
        assert get_ident() == coordinator
        if event["stage"] == "partition_released":
            live_jobs = 0

    def execute(*args, worker_batch_provider, **kwargs):
        def observe(aggregate):
            batch = worker_batch_provider(aggregate)
            acquisitions.extend(batch.acquisitions)
            return batch

        return original_execute(*args, worker_batch_provider=observe, **kwargs)

    monkeypatch.setattr(owner, "materialize_untargeted_partition", materialize)
    monkeypatch.setattr(owner, "execute_runtime_attempt", execute)
    monkeypatch.setattr(runtime_owner, "_prepare_group_authorized_backfills", prepare)
    monkeypatch.setattr(_FakeThermoRaw, "__init__", initialize)
    monkeypatch.setattr(_FakeThermoRaw, "Dispose", dispose)
    monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", chromatogram)
    monkeypatch.setattr(owner.StreamedResultStore, "append_children", append)
    result = owner.execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "parallel",
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "parallel.json", "parallel"),
        max_partition_jobs=8,
        raw_workers=raw_workers,
        progress_callback=progress,
    )
    assert type(result) is CompletedUntargetBatchWorkflowResult
    initial_sample_count = sum(
        bool(plan.context.candidates_by_sample[sample.metadata.raw_stem])
        for sample in plan.context.samples
    )
    assert job_counts == [initial_sample_count * 2, initial_sample_count]
    assert live_jobs == 0
    assert append_order == ["partition-00000-backfill", "partition-00001-backfill"]
    assert len(handle_threads) == len(acquisitions) == 8
    # Every initial acquisition finishes before either Backfill phase.
    assert barrier_checks == [6, 7]
    assert overlap_count <= peak_handles <= raw_workers
    assert len(intervals) == overlap_count
    if raw_workers > 1:
        assert min(end for _, end in intervals) > max(start for start, _ in intervals)
        assert coordinator not in handle_threads.values()
    else:
        assert set(handle_threads.values()) == {coordinator}
    assert set(handle_threads) == disposed and not active_handles


@pytest.mark.parametrize(
    "failure_mode", ["cancel", "vendor_failure", "cleanup_failure", "spool_failure"]
)
def test_sample_failure_drains_without_more_samples_or_partition_publication(
    tmp_path: Path, monkeypatch, failure_mode: str
):
    import xic_extractor.untargeted_execution.execution as owner
    import xic_extractor.untargeted_execution.partition.execution as runtime_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_owner, "ensure_assemblies_loaded", lambda *_args: None)
    barrier = Barrier(2)
    lock = Lock()
    caller_cancel = Event()
    caller_observed_cancel = Event()
    failed_sample_observed = Event()
    entered = set()
    opened = {}
    closed = set()
    batches = []
    original_leaf = owner.execute_partition
    original_chromatogram = _FakeThermoRaw.GetChromatogramData
    original_init = _FakeThermoRaw.__init__
    original_dispose = _FakeThermoRaw.Dispose
    original_execute = owner.execute_runtime_attempt

    def leaf(*args, **kwargs):
        entered.add(kwargs["partition_key"])
        return original_leaf(*args, **kwargs)

    def initialize(handle, *args):
        original_init(handle, *args)
        with lock:
            opened[handle] = (get_ident(), len(opened))

    def dispose(handle):
        thread, ordinal = opened[handle]
        assert thread == get_ident()
        original_dispose(handle)
        closed.add(handle)
        if failure_mode == "cleanup_failure" and ordinal == 1:
            raise OSError("injected cleanup failure")

    def chromatogram(handle, *args):
        thread, ordinal = opened[handle]
        assert thread == get_ident()
        if ordinal < 2:
            barrier.wait(timeout=10)
            if failure_mode == "cancel":
                caller_cancel.set()
                assert caller_observed_cancel.wait(timeout=10)
            elif failure_mode in {"vendor_failure", "cleanup_failure"}:
                if ordinal == 1 and failure_mode == "vendor_failure":
                    raise OSError("injected vendor failure")
                if ordinal == 0:
                    assert failed_sample_observed.wait(timeout=10)
        return original_chromatogram(handle, *args)

    def progress(event):
        if event["stage"] == "partition_initial" and event["completed"]:
            failed_sample_observed.set()

    def should_cancel():
        if caller_cancel.is_set():
            caller_observed_cancel.set()
            return True
        return False

    def execute(*args, worker_batch_provider, **kwargs):
        def observe(aggregate):
            batch = worker_batch_provider(aggregate)
            assert set(opened) == closed
            batches.append(batch)
            return batch

        return original_execute(*args, worker_batch_provider=observe, **kwargs)

    def fail_spool(*_args):
        raise OSError("injected spool failure")

    monkeypatch.setattr(owner, "execute_partition", leaf)
    monkeypatch.setattr(owner, "execute_runtime_attempt", execute)
    monkeypatch.setattr(_FakeThermoRaw, "__init__", initialize)
    monkeypatch.setattr(_FakeThermoRaw, "Dispose", dispose)
    monkeypatch.setattr(_FakeThermoRaw, "GetChromatogramData", chromatogram)
    if failure_mode == "spool_failure":
        monkeypatch.setattr(owner.StreamedResultStore, "append", fail_spool)
    marker = OwnerPathRuntimeMarker(tmp_path / "failure.json", "parallel-failure")
    with pytest.raises(RuntimeError, match="failed before batch publication") as error:
        owner.execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "failed",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=marker,
            raw_workers=2,
            max_partition_jobs=8,
            should_cancel=should_cancel,
            progress_callback=progress,
        )
    if failure_mode in {"vendor_failure", "cleanup_failure"}:
        assert "injected " + failure_mode.replace("_", " ") in str(error.value)
    assert entered == {"partition-00000-initial"}
    assert set(opened) == closed
    assert len(opened) == (4 if failure_mode == "spool_failure" else 2)
    expected_outcome = (
        AttemptTerminalOutcome.CANCELLED
        if failure_mode == "cancel"
        else AttemptTerminalOutcome.FAILED
    )
    assert marker.read().terminal_outcome is expected_outcome
    assert len(batches) == 1
    resources = batches[0].resource_settlements
    assert sum(
        receipt.cleanup_outcome is RuntimeResourceCleanupOutcome.FAILED
        for receipt in resources
    ) == (1 if failure_mode == "cleanup_failure" else 0)
    assert sum(
        receipt.open_outcome is RuntimeResourceOpenOutcome.OPENED
        for receipt in resources
    ) == len(opened)
    assert all(
        receipt.open_outcome is RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
        for receipt in resources
        if "partition-00001-initial:" in receipt.resource_key
    )
    assert list((tmp_path / "failed").glob(".cid_nl_partitions-*")) == []


def _round_trip_partition_job(job):
    return os.getpid(), job


def test_sample_worker_transport_references_global_request_owners(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from xic_extractor.execution._serialization import (
        owner_pickle_dumps,
        owner_pickle_loads,
    )
    from xic_extractor.execution.workers import WorkerAdmissionPool
    from xic_extractor.untargeted_execution.execution import (
        _sample_worker_shared_objects,
    )

    plan, _reference = _plan(tmp_path, monkeypatch, sample_count=2)
    batch = materialize_untargeted_partition(plan, plan.subject_batch)
    shared = _sample_worker_shared_objects(
        plan.context.processing_owners,
        (batch.jobs[0]._request_subjects, batch.subject_batch),
    )
    source = batch.jobs[0]

    restored = owner_pickle_loads(owner_pickle_dumps(source, shared), shared)

    assert restored.processing_owners is source.processing_owners
    assert restored.processing_owners.request is source.processing_owners.request
    assert restored.processing_owners.attempt is source.processing_owners.attempt
    assert restored._request_subjects is source._request_subjects
    assert restored.sample == source.sample
    assert restored.candidates == source.candidates

    transported = []
    with WorkerAdmissionPool(
        max_workers=2, backend="process", shared_objects=shared
    ) as pool:
        pool.run_windowed(
            batch.jobs,
            _round_trip_partition_job,
            on_completed=lambda _done, _total, _job, result: transported.append(result),
        )
    assert all(pid != os.getpid() for pid, _job in transported)
    expected = {job.sample.identity.sample_identity_id: job for job in batch.jobs}
    assert len(expected) == 2
    assert len(transported) == len(batch.jobs)
    for _pid, job in transported:
        original = expected[job.sample.identity.sample_identity_id]
        assert job.processing_owners is original.processing_owners
        assert job._request_subjects is original._request_subjects
        assert job.sample == original.sample
        assert job.candidates == original.candidates


def test_process_recovery_dispatches_only_missing_subjects(tmp_path, monkeypatch):
    from xic_extractor.execution.workers import WorkerAdmissionPool
    from xic_extractor.untargeted_execution.partition import execution as runtime
    from xic_extractor.untargeted_execution.partition import workers as worker

    # Exercise process message construction/consumption with synthetic RAW I/O.
    # Native spawning and transport identity have their own existing coverage.
    class LocalProcessMessages(WorkerAdmissionPool):
        @property
        def backend(self):
            return "process"

    plan, _ = _plan(tmp_path, monkeypatch, scenario="partial_recovery")
    batch = materialize_untargeted_partition(plan, plan.subject_batch)
    monkeypatch.setattr(worker, "ensure_assemblies_loaded", lambda *_: None)
    requests = []
    execute_recovery = runtime._execute_sample_backfill_worker

    def observe_request(request):
        requests.append(request)
        keys = {job.work_unit_key for job in request.jobs}
        assert keys == set(request.backfill_targets_by_key)
        assert set(request.backfill_support_by_key) == keys
        # Recovery consumes only the support policy and the missing-MS1
        # initial assessment. Full initial adapter results stay in the parent.
        assert not hasattr(request, "adapter_results_by_key")
        assert all(
            isinstance(ctx, worker._BackfillSupportContext)
            for ctx in request.backfill_support_by_key.values()
        )
        assert len(keys) < len(plan.subject_batch.subjects)
        return execute_recovery(request)

    monkeypatch.setattr(runtime, "_execute_sample_backfill_worker", observe_request)
    with LocalProcessMessages(max_workers=1) as pool:
        result = runtime.execute_partition(
            batch.jobs,
            partition_key="sparse-recovery",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            pool=pool,
        )
    assert result.succeeded
    assert len(requests) == 1
    assert len(result.sample_results) == len(batch.jobs)
    selected = {job.work_unit_key for job in requests[0].jobs}
    for sample_result in result.sample_results:
        assert bool(sample_result.backfill_attempts) is (
            sample_result.job.work_unit_key in selected
        )


def test_two_sample_process_messages_match_serial_results_and_attribution(
    tmp_path, monkeypatch
):
    from xic_extractor.execution.workers import WorkerAdmissionPool
    from xic_extractor.untargeted_execution.partition import execution as runtime

    # Exercise the process request/response route with synthetic RAW I/O;
    # native spawn and transport identity have separate existing coverage.
    class LocalProcessMessages(WorkerAdmissionPool):
        @property
        def backend(self):
            return "process"

    plan, _ = _plan(tmp_path, monkeypatch, sample_count=2)
    batch = materialize_untargeted_partition(plan, plan.subject_batch)
    config = AlignmentConfig(anchor_min_seed_events=1)
    with WorkerAdmissionPool(max_workers=1) as pool:
        serial = runtime.execute_partition(
            batch.jobs,
            partition_key="two-sample-serial",
            alignment_config=config,
            pool=pool,
        )
    response_sizes = []

    def reordered_response(worker):
        def execute(request):
            response = worker(request)
            response_sizes.append(len(response.job_outcomes))
            return replace(
                response, job_outcomes=tuple(reversed(response.job_outcomes))
            )

        return execute

    for entry in ("_execute_sample_initial_worker", "_execute_sample_backfill_worker"):
        monkeypatch.setattr(runtime, entry, reordered_response(getattr(runtime, entry)))
    with LocalProcessMessages(max_workers=2) as pool:
        process = runtime.execute_partition(
            batch.jobs,
            partition_key="two-sample-process",
            alignment_config=config,
            pool=pool,
        )

    assert serial.succeeded and process.succeeded
    serial_results = tuple(
        (
            result.job.sample.identity.sample_identity_id,
            result.job.method_subject.method_subject_id,
            result.result_ref,
        )
        for result in serial.sample_results
    )
    process_results = tuple(
        (
            result.job.sample.identity.sample_identity_id,
            result.job.method_subject.method_subject_id,
            result.result_ref,
        )
        for result in process.sample_results
    )
    assert len({sample_id for sample_id, _, _ in serial_results}) == 2
    assert any(size > 1 for size in response_sizes)
    assert process_results == serial_results


@pytest.mark.parametrize("corruption", ["missing", "duplicate"])
def test_process_outcome_must_cover_the_exact_work_units(
    tmp_path, monkeypatch, corruption
):
    from xic_extractor.execution.workers import WorkerAdmissionPool
    from xic_extractor.untargeted_execution.partition import execution as runtime

    class LocalProcessMessages(WorkerAdmissionPool):
        @property
        def backend(self):
            return "process"

    plan, _ = _plan(tmp_path, monkeypatch, sample_count=1)
    batch = materialize_untargeted_partition(plan, plan.subject_batch)
    initial = runtime._execute_sample_initial_worker

    def corrupt_response(request):
        response = initial(request)
        outcomes = response.job_outcomes
        assert len(outcomes) > 1
        return replace(
            response,
            job_outcomes=(
                outcomes[:-1]
                if corruption == "missing"
                else (outcomes[0], *outcomes[:-1])
            ),
        )

    monkeypatch.setattr(runtime, "_execute_sample_initial_worker", corrupt_response)
    with LocalProcessMessages(max_workers=1) as pool:
        result = runtime.execute_partition(
            batch.jobs,
            partition_key="crossed-worker-population",
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            pool=pool,
        )

    assert not result.succeeded
    assert result.sample_results == ()


def test_sparse_partition_materializes_only_samples_with_request_candidates(
    tmp_path, monkeypatch
):
    from xic_extractor.untargeted_execution.partition.models import (
        _request_subject_candidates,
    )

    plan, _ = _plan(tmp_path, monkeypatch, scenario="recovery")
    batch = materialize_untargeted_partition(
        plan,
        plan.subject_batch,
        sparse=True,
    )

    assert len(batch.sample_job_templates) == len(plan.context.samples)
    assert batch.jobs
    assert all(_request_subject_candidates(job) for job in batch.jobs)
    initial_sample_ids = {job.sample.identity.sample_identity_id for job in batch.jobs}
    assert len(initial_sample_ids) >= 2
    missing_sample_id = plan.context.samples[-1].identity.sample_identity_id
    assert missing_sample_id not in initial_sample_ids


@pytest.mark.parametrize(
    "scenario,expected_sparse_jobs,expected_sparse_results",
    [("recovery", 9, 12), ("below_threshold", 3, 3), ("blank_missing", 9, 9)],
)
def test_sparse_recovery_partition_matches_dense_composition(
    tmp_path, monkeypatch, scenario, expected_sparse_jobs, expected_sparse_results
):
    from tests.untargeted_partition_fixtures import (
        execute_dense_partition,
    )
    from xic_extractor.untargeted_execution.partition.execution import (
        execute_partition,
    )
    from xic_extractor.untargeted_execution.partition_composition import (
        compose_untargeted_partition,
    )

    plan, reference = _plan(tmp_path, monkeypatch, scenario=scenario)
    config = AlignmentConfig(anchor_min_seed_events=1)
    dense_batch = materialize_untargeted_partition(plan, plan.subject_batch)
    assert len(dense_batch.jobs) == 12
    dense_runtime = execute_dense_partition(
        dense_batch,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "dense.json", "dense"),
        alignment_config=config,
    )
    assert dense_runtime.succeeded
    dense_composition = compose_untargeted_partition(
        dense_batch,
        dense_runtime.sample_results,
        alignment_config=config,
        known_istd_reference=reference,
    )

    sparse_batch = materialize_untargeted_partition(
        plan, plan.subject_batch, sparse=True
    )
    assert len(sparse_batch.jobs) == expected_sparse_jobs
    sparse_runtime = execute_partition(
        sparse_batch.jobs,
        backfill_templates=sparse_batch.sample_job_templates,
        partition_key="sparse-recovery",
        alignment_config=config,
    )
    assert sparse_runtime.succeeded
    assert len(sparse_runtime.sample_results) == expected_sparse_results
    sparse_composition = compose_untargeted_partition(
        sparse_batch,
        sparse_runtime.sample_results,
        alignment_config=config,
        known_istd_reference=reference,
    )

    dense_by_key = {
        (
            result.job.sample.metadata.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in dense_runtime.sample_results
    }
    sparse_by_key = {
        (
            result.job.sample.metadata.raw_stem,
            result.job.method_subject.method_subject_id,
        ): result
        for result in sparse_runtime.sample_results
    }
    missing_stem = plan.context.samples[-1].metadata.raw_stem
    if scenario == "recovery":
        missing_results = [
            result
            for result in sparse_runtime.sample_results
            if result.job.sample.metadata.raw_stem == missing_stem
        ]
        assert missing_results
        assert all(result.backfill_attempts for result in missing_results)
        assert set(sparse_by_key) == set(dense_by_key)
    else:
        assert missing_stem not in {
            result.job.sample.metadata.raw_stem
            for result in sparse_runtime.sample_results
        }
    assert dense_composition.children == sparse_composition.children
    assert dense_composition.initial_groups == sparse_composition.initial_groups
    assert dense_composition.initial_edges == sparse_composition.initial_edges
    assert (
        dense_composition.excluded_candidates == sparse_composition.excluded_candidates
    )


def test_formal_partition_reuses_prepared_group_ids_for_recovery_and_composition(
    tmp_path, monkeypatch
):
    import xic_extractor.untargeted_execution.execution as bounded_owner
    import xic_extractor.untargeted_execution.partition_composition as production_owner
    from xic_extractor.execution.result_store import StreamedResultStore

    plan, _ = _plan(tmp_path, monkeypatch, scenario="cross_subject_recovery")
    calls = []
    original_group_builder = production_owner.build_initial_cid_nl_cross_sample_groups

    def observe_group_build(*args, **kwargs):
        calls.append(1)
        return original_group_builder(*args, **kwargs)

    monkeypatch.setattr(
        production_owner,
        "build_initial_cid_nl_cross_sample_groups",
        observe_group_build,
    )
    adapter = bounded_owner.UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=100,
    )
    assert len(adapter.bootstrap_partitions) == 1
    store = StreamedResultStore(tmp_path / "formal-spool", adapter.get_shared_objects())
    bootstrap = adapter.bootstrap_partitions[0]
    initial = adapter.execute_acquisition(bootstrap)
    adapter.execute_composition(bootstrap, initial, store)
    adapter.prepare_batch_search(store, lambda: None)
    partition = adapter.partitions[-1]
    acquisition = adapter.execute_acquisition(partition)
    assert acquisition.succeeded
    batch, leaf = acquisition.result
    prepared = leaf.prepared_grouping
    assert prepared is not None
    assert len(prepared.worksets) == 1
    # N=4 and K=2: neither subject qualifies alone, but their one SUPPORT
    # sample each belongs to the same compatible group.
    assert len(batch.jobs) == 2
    assert len({job.method_subject.method_subject_id for job in batch.jobs}) == 2
    assert len(leaf.sample_results) == 4
    assert calls == [1] * len(prepared.worksets)
    prepared_group_ids = {
        group.group_hypothesis_id
        for workset in prepared.worksets
        for group in workset.grouping_scope.groups
    }
    assert len(prepared_group_ids) == 1
    backfill_group_ids = {
        attempt.group_hypothesis_id
        for result in leaf.sample_results
        for attempt in result.backfill_attempts
        if attempt.group_hypothesis_id is not None
    }
    assert backfill_group_ids
    assert backfill_group_ids == prepared_group_ids
    assert {
        result.job.sample.metadata.raw_stem
        for result in leaf.sample_results
        if result.backfill_attempts
    } == {"S2", "S3"}
    recovery_result = next(
        result for result in leaf.sample_results if result.backfill_attempts
    )
    backfill_attempt = recovery_result.backfill_attempts[0]
    same_rt_different_groups = replace(
        recovery_result,
        backfill_attempts=(
            replace(backfill_attempt, group_hypothesis_id="group-a"),
            replace(backfill_attempt, group_hypothesis_id="group-b"),
        ),
    )
    assert (
        same_rt_different_groups.backfill_for_group(
            0.0, group_hypothesis_id="group-b"
        ).group_hypothesis_id
        == "group-b"
    )
    assert (
        same_rt_different_groups.backfill_for_group(
            0.0, group_hypothesis_id="wrong-group"
        )
        is None
    )
    calls_before_composition = len(calls)
    composed = adapter.execute_composition(
        partition,
        acquisition,
        store,
        retain_memory=True,
    )
    assert composed.result.child_count == 1
    assert len(calls) == calls_before_composition
    assert batch.prepared_grouping is prepared
    persisted = store.read(composed.sealed.index)
    assert {
        group.group_hypothesis_id for group in persisted.initial_groups
    } == prepared_group_ids
    assert {
        group.group_hypothesis_id
        for child in persisted.children
        for group in child.scientific_core.grouping_result.final_population
    } == prepared_group_ids


def test_completed_partition_retains_only_light_parent_capture_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent closure keeps IDs while children retain exact capture evidence."""

    import xic_extractor.untargeted_execution.execution as bounded_owner
    from xic_extractor.execution.result_store import StreamedResultStore

    plan, _reference = _plan(tmp_path, monkeypatch, scenario="mixed_eligibility")
    adapter = bounded_owner.UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=100,
    )
    store = StreamedResultStore(
        tmp_path / "retention-spool", adapter.get_shared_objects()
    )
    for partition in _phase_partitions(adapter, store):
        acquisition = adapter.execute_acquisition(partition)
        assert acquisition.succeeded
        composed = adapter.execute_composition(partition, acquisition, store)
    persisted = composed.result

    stored = store.read(composed.sealed.index)
    child_before = store.read_selected(composed.sealed.index, "children", (0,))[0]
    child_scope = child_before.scientific_core.scope
    assert child_scope.capture_bindings
    child_observations = child_before.scientific_core.cell_observations
    child_decision = child_before.completed_projection.decisions[0]
    expected_excluded_ids = tuple(
        exclusion.candidate_id for exclusion in stored.excluded_candidates
    )

    assert persisted.excluded_candidate_ids == expected_excluded_ids
    assert not hasattr(persisted, "excluded_candidates")
    assert not hasattr(persisted, "capture_bindings")
    assert stored.capture_bindings == ()
    assert stored.capture_evidence

    store.seal()
    finalized = adapter.finalize_application(store, (composed,))
    assert finalized.scope.capture_bindings == ()
    assert tuple(finalized.excluded_candidates) == tuple(plan.excluded_candidates)
    child_after = store.read_selected(composed.sealed.index, "children", (0,))[0]
    assert (
        child_after.scientific_core.scope.capture_bindings
        == child_scope.capture_bindings
    )
    assert child_after.scientific_core.cell_observations == child_observations
    assert (
        child_after.completed_projection.decisions[0].basis_bytes
        == child_decision.basis_bytes
    )
    assert child_after.completed_projection.decisions[0].value == child_decision.value


def test_process_backend_reuses_one_shared_pool_and_closes_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import xic_extractor.untargeted_execution.execution as owner

    created = []

    class RecordingPool:
        def __init__(self, **kwargs):
            self.max_workers = kwargs["max_workers"]
            self.backend = kwargs["backend"]
            self.initializer = kwargs["initializer"]
            self.shared_objects = kwargs["shared_objects"]
            self.stopped = False
            self.first_error = None
            self.transport_metrics = None
            self.exit_calls = 0
            created.append(self)

        def __exit__(self, *_args):
            self.exit_calls += 1

        def stop(self, error):
            self.first_error = error
            self.stopped = True

    monkeypatch.setattr(owner, "WorkerAdmissionPool", RecordingPool)
    plan, _reference = _plan(tmp_path, monkeypatch)
    adapter = owner.UntargetedPartitionPipelineAdapter(
        plan,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        max_partition_jobs=len(plan.context.samples),
        raw_workers=8,
        raw_worker_backend="process",
        acquisition_workers=2,
    )

    assert adapter.acquisition_workers == 2
    assert adapter.partition_raw_workers == 8
    assert len(created) == 1
    assert created[0].max_workers == 8
    assert sum(pool.max_workers for pool in created) == 8
    assert {id(pool.shared_objects) for pool in created} == {
        id(created[0].shared_objects)
    }
    shared = created[0].shared_objects
    for partition in adapter.partitions:
        subject_batch = adapter._partition_subject_batches[partition.partition_key]
        assert any(value is subject_batch for value in shared)

    created[0].stop(RuntimeError("failure stopped admission"))
    with pytest.raises(RuntimeError, match="failure stopped admission"):
        adapter.execute_acquisition(adapter.partitions[0])

    adapter.close_process_worker_pools()
    adapter.close_process_worker_pools()
    assert [pool.exit_calls for pool in created] == [1]

    with pytest.raises(RuntimeError, match="already closed"):
        adapter.execute_acquisition(adapter.partitions[0])


def _spool_dirs(output_dir):
    return sorted(output_dir.glob(".partition_spool-*"))


def _run_for_spool(tmp_path, monkeypatch, *, retain):
    from xic_extractor.untargeted_execution.execution import (
        execute_untargeted_plan,
    )

    plan, reference = _plan(tmp_path, monkeypatch)
    output_dir = tmp_path / ("retained" if retain else "released")
    result = execute_untargeted_plan(
        plan,
        output_dir=output_dir,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / f"spool-{retain}.json", "spool"
        ),
        known_istd_reference=reference,
        max_partition_jobs=len(plan.context.samples),
        retain_partition_spool=retain,
    )
    spools = _spool_dirs(output_dir)
    assert len(spools) == 1
    child_ids = tuple(
        tuple(
            observation.cell_observation_id
            for observation in child.scientific_core.cell_observations
        )
        for child in result.application_result.children
    )
    alias = replace(result)
    result.close()
    alias.close()
    result.close()
    return spools[0], child_ids


def test_released_spool_is_deleted_when_the_workflow_result_closes(
    tmp_path, monkeypatch
):
    """The default still owns the spool and removes it; nothing is left behind."""
    spool, _ = _run_for_spool(tmp_path, monkeypatch, retain=False)
    assert not spool.exists()


def test_retained_spool_keeps_its_partitions_and_publishes_a_readable_index(
    tmp_path, monkeypatch
):
    """Retention leaves an index a separate reader can restore records from."""
    from xic_extractor.execution.result_store import (
        PRESERVED_INDEX_NAME,
        open_preserved_results,
    )

    spool, child_ids = _run_for_spool(tmp_path, monkeypatch, retain=True)
    assert spool.is_dir()
    index = spool / PRESERVED_INDEX_NAME
    assert index.is_file()
    assert sorted(path.name for path in spool.glob("*.pickle")) != [
        PRESERVED_INDEX_NAME
    ], "retention must keep the partition files, not only the index"

    restored = open_preserved_results(index)
    recovered = tuple(
        tuple(
            observation.cell_observation_id
            for observation in child.scientific_core.cell_observations
        )
        for child in restored.sequence("children")
    )
    assert recovered == child_ids

    # The point of the index: one record, addressed by position, off disk.
    first_child_partition = next(
        position
        for position, lengths in enumerate(restored._field_lengths)
        if lengths["children"]
    )
    first = restored.read_selected(first_child_partition, "children", (0,))[0]
    assert (
        tuple(
            observation.cell_observation_id
            for observation in first.scientific_core.cell_observations
        )
        == child_ids[0]
    )


@pytest.mark.parametrize("retain", (False, True))
def test_result_store_close_failure_still_releases_adopted_discovery_inputs(
    tmp_path, monkeypatch, retain
):
    from contextlib import ExitStack

    from xic_extractor.execution.result_store import StreamedResultStore
    from xic_extractor.untargeted_execution.execution import execute_untargeted_plan

    plan, _ = _plan(tmp_path, monkeypatch)
    result = execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "runtime",
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / "runtime.json", "cleanup"),
        max_partition_jobs=len(plan.context.samples),
        retain_partition_spool=retain,
    )
    marker = tmp_path / "discovery-resource"
    marker.write_text("owned", encoding="utf-8")

    class DiscoveryInputs:
        def retained_physical_records(self):
            return ()

        def take_result_resources(self):
            resources = ExitStack()
            resources.callback(marker.unlink)
            return resources

    result = result.with_discovery_inputs(DiscoveryInputs())
    alias = replace(result)
    operation = "preserve_results" if retain else "cleanup"
    original_close = getattr(StreamedResultStore, operation)
    calls = []

    def fail_close(store):
        calls.append(store)
        raise OSError("result store close failed")

    monkeypatch.setattr(StreamedResultStore, operation, fail_close)
    try:
        with pytest.raises(OSError, match="result store close failed"):
            result.close()
        assert not marker.exists()
        alias.close()
        result.close()
        assert len(calls) == 1
    finally:
        # Release the deliberately failed store without retrying workflow close.
        if retain:
            StreamedResultStore.cleanup(calls[0])
        else:
            original_close(calls[0])


def _run_for_cache(tmp_path, monkeypatch, *, retain, tag, shared=None):
    from xic_extractor.untargeted_execution.execution import (
        execute_untargeted_plan,
    )

    plan, reference = _plan(tmp_path, monkeypatch)
    output_dir = tmp_path / tag
    result = execute_untargeted_plan(
        plan,
        output_dir=output_dir,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(tmp_path / f"{tag}.json", "cache"),
        known_istd_reference=reference,
        max_partition_jobs=len(plan.context.samples),
        retain_acquisition_cache=retain,
        acquisition_cache_dir=shared,
    )
    result.close()
    return output_dir / ".cid_nl_acquisition_cache"


def test_acquisition_cache_is_released_after_a_successful_run(tmp_path, monkeypatch):
    """A one-shot run leaves no cache behind; it only accelerates acquisition."""
    cache = _run_for_cache(tmp_path, monkeypatch, retain=False, tag="released")
    assert not cache.exists()


def test_acquisition_cache_is_kept_when_asked(tmp_path, monkeypatch):
    """Repeated development runs can keep it and skip rereading every RAW."""
    cache = _run_for_cache(tmp_path, monkeypatch, retain=True, tag="retained")
    assert cache.is_dir()


def test_a_shared_acquisition_cache_outlives_the_run(tmp_path, monkeypatch):
    """Runs into new output directories reuse one cache, e.g. on a fast disk."""
    shared = tmp_path / "ssd-cache"
    own = _run_for_cache(
        tmp_path, monkeypatch, retain=False, tag="shared", shared=shared
    )
    entries = sorted(path for path in shared.rglob("*.json"))
    assert entries
    assert not own.exists()


def test_acquisition_cache_survives_a_failed_attempt(tmp_path, monkeypatch):
    """A retry allowed by the marker must not have to reread every RAW file."""
    import xic_extractor.untargeted_execution.execution as bounded_owner

    plan, _ = _plan(tmp_path, monkeypatch)
    calls = []
    original = bounded_owner.StreamedResultStore.append_children

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("second partition storage unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        bounded_owner.StreamedResultStore, "append_children", fail_second
    )
    output_dir = tmp_path / "failed-cache-output"
    with pytest.raises(RuntimeError, match="failed before batch publication"):
        bounded_owner.execute_untargeted_plan(
            plan,
            output_dir=output_dir,
            alignment_config=AlignmentConfig(anchor_min_seed_events=1),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
            recovery_marker=OwnerPathRuntimeMarker(
                tmp_path / "failed-cache.json", "failed-cache"
            ),
            max_partition_jobs=len(plan.context.samples),
        )
    assert (output_dir / ".cid_nl_acquisition_cache").is_dir()
