"""Persisted Discovery replay through runtime, retained evidence and writer."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tests.alignment_pipeline_helpers import (
    render,
    written_cells,
    written_groups,
    written_source_candidates,
)
from tests.discovery_csv_fixtures import representative_event
from tests.untargeted_cid_nl_production_batch_fixtures import (
    _assert_composite_no_input_exclusion_coverage,
    _assert_jobs_match_direct_construction,
)
from tests.untargeted_cid_nl_production_batch_fixtures import (
    profile_replay as profile_replay,
)
from tests.untargeted_partition_fixtures import (
    children_of,
    fixture_assessment,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.untargeted_execution.partition import workers as partition_workers
from xic_extractor.untargeted_execution.partition.models import (
    BACKFILL_HALF_WINDOW_MIN,
)


@pytest.mark.parametrize(
    "profile_replay",
    (
        pytest.param(((2,), "single", False, False, 0.0), id="one-group"),
        pytest.param(((1, 1), "single", False, False, 0.0), id="two-singletons"),
        pytest.param(((2, 2), "single", False, False, 0.0), id="two-supported-groups"),
        pytest.param(((2,), "single", True, False, 0.0), id="nearby-single"),
        pytest.param(((2,), "union", True, False, 0.0), id="nearby-or-different-tags"),
        pytest.param(
            ((2,), "intersection", True, False, 0.0), id="nearby-and-incomplete"
        ),
        pytest.param(
            ((2,), "single", False, True, 0.0), id="overlapping-family-one-peak"
        ),
        pytest.param(
            ((4,), "single", False, True, 0.0065),
            id="overlapping-family-across-fixed-subject-boundary",
        ),
    ),
    indirect=True,
)
def test_profile_replay_closes_persisted_discovery_through_runtime_and_writer(
    tmp_path: Path,
    profile_replay,
) -> None:
    completed = profile_replay.completed
    inputs = profile_replay.inputs
    trace = profile_replay.trace
    case = profile_replay.case

    publication = completed.publication_inputs
    assert publication.alignment_config == AlignmentConfig()
    assert dict(publication.ms1_apex_mz_by_candidate) == {
        candidate.candidate_id: candidate.ms1_apex_mz
        for sample_candidates in inputs.candidates.values()
        for candidate in sample_candidates
        if candidate.ms1_apex_mz is not None
    }
    assert {
        sample: tuple(candidate.peak_id for candidate in sample_candidates)
        for sample, sample_candidates in publication.peaks_by_sample.items()
    } == {
        sample: tuple(candidate.peak_id for candidate in sample_candidates)
        for sample, sample_candidates in inputs.candidates.items()
    }
    assert completed.processing_attempt_id == (
        completed.application_result.processing_attempt_id
    )
    composed_universes = [item[1:] for item in trace.hypothesis_universe_compositions]
    assert len(composed_universes) == len(set(composed_universes)), (
        {
            owner_name: sum(
                item[0] == owner_name for item in trace.hypothesis_universe_compositions
            )
            for owner_name in {
                item[0] for item in trace.hypothesis_universe_compositions
            }
        },
        trace.hypothesis_universe_compositions,
    )
    retained_hypotheses_by_id = {
        member.owner_id: member.hypothesis for member in trace.retained_band_members
    }
    assert all(
        retained_hypotheses_by_id[terminal.hypothesis.hypothesis_id]
        == terminal.hypothesis
        for terminal in trace.retained_terminals
    )
    assert all(
        retained_hypotheses_by_id[support.hypothesis.hypothesis_id]
        == support.hypothesis
        for cells in trace.retained_cell_batches
        for cell in cells
        for support in cell.group_context.supports
    )
    assert trace.preparation_fingerprints == [], "preparation must not read RAW content"
    assert trace.obsolete_marker.read_bytes() == b"obsolete attempt status"
    assert trace.runtime_fingerprints == [], (
        "RAW opens must reuse the input owner's source verification",
        trace.runtime_fingerprints,
    )
    _assert_jobs_match_direct_construction(trace.captured_batches[0])
    writer = completed.application_result
    matrix_path = tmp_path / "canonical-matrix.tsv"
    render("matrix", matrix_path, writer)
    with matrix_path.open(encoding="utf-8", newline="") as handle:
        matrix_rows = tuple(csv.DictReader(handle, delimiter="\t"))
    assert len(trace.retained_contexts) == len(
        children_of(completed.application_result)
    )
    assert len(trace.retained_contexts) == len(trace.consumed_contexts)
    assert len(trace.completed_contexts) == len(trace.retained_contexts)
    assert all(
        retained.partition_transition == final.partition_transition
        and retained.samples == final.samples
        for retained, final in zip(
            trace.retained_contexts,
            trace.completed_contexts,
            strict=True,
        )
    )
    assert all(
        retained == consumed
        for retained, consumed in zip(
            trace.completed_contexts,
            trace.consumed_contexts,
            strict=True,
        )
    )
    assert sorted(trace.retained_terminals, key=lambda item: item.candidate_id) == (
        sorted(trace.consumed_terminals, key=lambda item: item.candidate_id)
    )
    for context, child in zip(
        trace.completed_contexts,
        children_of(completed.application_result),
        strict=True,
    ):
        assert child.scientific_core.final_population == context.final_population
        assert child.scientific_core.lineage == ()
        assert child.scientific_core.grouping_result == context
    children = children_of(completed.application_result)
    assert len(trace.projection_builds) == len(children)
    if case.family_context:
        assert len(matrix_rows) == 1
        if case.family_precursor_delta:
            batch = trace.captured_batches[0]
            assert len(batch.subject_batch.subjects) == 3
            assert len(children) == 2
            assert sorted(
                len(child.scientific_core.scope.evidence_providers)
                for child in children
            ) == [1, 2]
            child = next(
                child
                for child in children
                if len(child.scientific_core.scope.evidence_providers) == 2
            )
            assert tuple(
                provider.method_subject_id
                for provider in child.scientific_core.scope.evidence_providers
            ) != tuple(
                subject.method_subject_id for subject in batch.subject_batch.subjects
            )
            assert sorted(
                len(group.member_subjects)
                for group in child.scientific_core.final_population
            ) == [1, 3]
            sample_id_by_stem = {
                sample.raw_stem: sample.sample_name
                for sample in child.scientific_core.canonical_samples
            }
            joint_group = next(
                group
                for group in child.scientific_core.final_population
                if len(group.member_subjects) == 3
            )
            assert {member.sample_key for member in joint_group.member_subjects} == {
                sample_id_by_stem["S2"],
                sample_id_by_stem["S3"],
                sample_id_by_stem["S4"],
            }
            joint_final_cell = next(
                cell
                for cell in child.scientific_core.cell_observations
                if cell.group_hypothesis_id == joint_group.group_hypothesis_id
                and cell.sample_key == sample_id_by_stem["S3"]
            )
            (joint_cell,) = joint_final_cell.predecessor_cells
            assert any(
                support.assessment is joint_cell.method_assessment
                and support.sample.sample_name == sample_id_by_stem["S3"]
                for support in joint_cell.group_context.supports
            )
            assert len(written_groups(writer)) == 3
            assert len(tuple(written_cells(writer))) == 3 * len(inputs.sample_stems)
            (matrix_row,) = matrix_rows
            assert matrix_row["S1"] == ""
            assert matrix_row["S2"]
            assert matrix_row["S3"]
            (s4_cell,) = (
                cell
                for cell in child.scientific_core.cell_observations
                if cell.group_hypothesis_id == joint_group.group_hypothesis_id
                and cell.sample_key == sample_id_by_stem["S4"]
            )
            (local_peak,) = s4_cell.ms1_only_group_observations
            assert local_peak.mz == pytest.approx(307.10847543945314, abs=1e-10)
            assert not s4_cell.direct_hypotheses
            assert not s4_cell.ms1_only_backfill_observations
            assert matrix_row["S4"]
        else:
            assert len(tuple(written_cells(writer))) == len(inputs.sample_stems)
        completed.close()
        return
    if case.nearby:
        assert len(matrix_rows) == (0 if case.method_mode == "intersection" else 1)
        batch = trace.captured_batches[0]
        assert len(batch.subject_batch.subjects) == 1
        for result in trace.runtime_results[0].sample_results:
            stem = result.job.sample.raw_stem
            (candidate,) = batch.context.peaks_by_sample[stem]
            assert candidate.peak_mz == representative_event(candidate).precursor_mz
            ((_, observation),) = result.adapter_result.candidate_observations
            assert observation.mz == pytest.approx(candidate.peak_mz, abs=1e-10)
            assert within_ppm(
                observation.mz,
                observation.source_trace.mz,
                result.job.settings.precursor_mz_tolerance_ppm,
            )
            assert observation.physical_evaluation is not None
            assert (
                observation.physical_evaluation.answer.selected_segment
                == inputs.physical_peaks[stem]
            )
            assert fixture_assessment(result).method_result == (
                "HOLD" if case.method_mode == "intersection" else "SUPPORT"
            )
        if case.method_mode == "intersection":
            assert written_groups(writer) == ()
            assert all(
                "incomplete_composite_requirement"
                in fixture_assessment(result).reason_codes
                for result in trace.runtime_results[0].sample_results
            )
            _assert_composite_no_input_exclusion_coverage(
                completed, trace.runtime_results[0], batch
            )
            completed.close()
            return
        if case.method_mode == "union":
            (child,) = children_of(completed.application_result)
            assert sorted(
                len(group.member_subjects)
                for group in child.scientific_core.final_population
            ) == [2]
            selected_cells = tuple(
                (sample_stem, cell)
                for _row, sample_stem, cell in written_cells(writer)
                if cell.selected_integration
            )
            assert {sample_stem for sample_stem, _cell in selected_cells} == set(
                inputs.sample_stems
            )
            for sample_stem, cell in selected_cells:
                peak = inputs.physical_peaks[sample_stem]
                assert cell.value == peak.area_counts_seconds
                assert cell.selected_integration is not None
                assert cell.selected_integration.rt_apex_min == peak.apex_rt_min
            completed.close()
            return
    cells = tuple(written_cells(writer))
    assert len(cells) == len(case.group_sizes) * len(inputs.sample_stems)
    for _row, sample_stem, cell in cells:
        if cell.selected_integration is None:
            assert inputs.separate_groups
            assert cell.value is None
            continue
        peak = inputs.physical_peaks[sample_stem]
        assert cell.value == peak.area_counts_seconds
        assert cell.selected_integration.area_raw_counts_seconds != cell.value
        assert cell.selected_integration.rt_apex_min == peak.apex_rt_min
        assert cell.selected_integration.height_smoothed > peak.height
    expected_row_count = sum(size > 1 for size in case.group_sizes)
    assert len(matrix_rows) == expected_row_count
    for sample_stem, peak in inputs.physical_peaks.items():
        sample_values = [
            float(row[sample_stem]) for row in matrix_rows if row[sample_stem]
        ]
        assert sample_values == pytest.approx(
            [peak.area_counts_seconds] if expected_row_count else []
        )
    if case.nearby:
        sources = written_source_candidates(tmp_path / "written", writer)
        assert (
            len(
                {
                    sources[row.group_hypothesis_id, sample_stem] or None
                    for row, sample_stem, _cell in written_cells(writer)
                }
            )
            == 2
        )
        assert len(matrix_rows) == 1
        completed.close()
        return
    if inputs.separate_groups:
        (child,) = children_of(completed.application_result)
        scientific = child.scientific_core
        assert len(scientific.final_population) == 2
        assert len(scientific.cell_observations) == 2 * len(inputs.sample_stems)
        revisited = min(case.group_sizes) >= 2
        assert sum(
            cell.evidence_states
            == (("unavailable",) if revisited else ("not_searched",))
            for cell in scientific.cell_observations
        ) == len(inputs.sample_stems)
        assert all(
            sum(bool(row[stem]) for stem in inputs.sample_stems) == 2
            for row in matrix_rows
        )
        assert all(
            result.adapter_result.assessment_input.assessment.method_result == "SUPPORT"
            for result in trace.runtime_results[0].sample_results
        )
        assert (
            any(
                source.outside_group is not None
                for cell in scientific.cell_observations
                for source in cell.predecessor_cells
            )
            != revisited
        )
        completed.close()
        return

    completed.close()


def test_profile_replay_retains_positive_and_empty_backfill_evidence(
    tmp_path: Path,
    monkeypatch,
    profile_replay,
) -> None:
    inputs = profile_replay.inputs
    trace = profile_replay.trace

    batch = trace.captured_batches[0]
    from xic_extractor.peak_detection.cid_nl_peak_support import (
        DdaCidNlPeakSupportPolicy,
    )
    from xic_extractor.untargeted_execution.partition import (
        cid_nl_backfill_acquisition,
    )

    job = batch.jobs[0]
    recovery_raw = partition_workers.open_raw(
        inputs.paths[0],
        tmp_path,
        expected_acquisition_source=job.acquisition_source_expectation.source,
    )
    recovery_args = dict(
        sample=job.sample,
        acquisition_source=job.acquisition_source_expectation.source,
        method_subject=job.method_subject,
        group_center_rt=inputs.center_rt,
        group_hypothesis_id="group-1",
        group_center_mz=job.method_subject.precursor_mz,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01, neutral_loss_tolerance_ppm=10.0
        ),
        half_window_min=BACKFILL_HALF_WINDOW_MIN,
        raw=recovery_raw,
        settings=inputs.settings,
        signal_processing_profile=inputs.profile,
    )
    recovered = cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
        **recovery_args
    )
    (observation,) = recovered.assessment.candidate_peaks
    acquired = recovered.acquisition.traces[0]
    recovery_prepared = prepare_physical_signal(
        acquired.trace.rt,
        acquired.trace.intensity,
        profile=inputs.profile,
    )
    assert observation.capture_stage == "method_backfill"
    assert observation.physical_evaluation is not None
    assert (
        observation.physical_evaluation.retained_prepared_signal
        == recovery_prepared.prepared
    )
    integ = observation.integration
    selected = observation.physical_evaluation.answer.selected_segment
    assert selected is not None
    assert integ.area_ms1_morphology == selected.area_counts_seconds
    prepared = observation.physical_evaluation.retained_prepared_signal
    assert prepared is not None
    assert integ.height_smoothed == prepared.smoothed[selected.apex_index]
    assert integ.height_smoothed > selected.height
    assert recovered.assessment.method_result == "HOLD"
    original_intensity = inputs.source_raws[inputs.paths[0]].intensity
    recovery_evaluations = []
    evaluate_recovery = cid_nl_backfill_acquisition.evaluate_physical_peak

    def retain_recovery_evaluation(*args, **kwargs):
        evaluation = evaluate_recovery(*args, **kwargs)
        recovery_evaluations.append(evaluation)
        return evaluation

    monkeypatch.setattr(
        cid_nl_backfill_acquisition,
        "evaluate_physical_peak",
        retain_recovery_evaluation,
    )
    try:
        for offsets, reason in (
            ((0,), "insufficient_original_xic_support"),
            ((-2, 2), "insufficient_original_xic_continuity"),
        ):
            isolated = original_intensity * 0.0
            for offset in offsets:
                isolated[len(isolated) // 2 + offset] = float(max(original_intensity))
            inputs.source_raws[inputs.paths[0]].intensity = isolated
            unavailable = (
                cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
                    **recovery_args
                )
            )
            assert unavailable.assessment.candidate_peaks == ()
            assert unavailable.assessment.method_result == "HOLD"
            assert unavailable.backfill_input is None
            assert recovery_evaluations[-1].reason == reason
            assert recovery_evaluations[-1].answer.selected_segment is None
            assert recovery_evaluations[-1].integration is None
            assert recovery_evaluations[-1].question.selection_policy_version == (
                "sample_local_background_qualified_peak_in_context"
            )

        inputs.source_raws[inputs.paths[0]].intensity = original_intensity * 0.0
        unavailable = (
            cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
                **recovery_args
            )
        )
        assert unavailable.assessment.candidate_peaks == ()
        assert unavailable.assessment.method_result == "HOLD"
        assert unavailable.backfill_input is None
        vendor = inputs.opened_raws[inputs.paths[0]][-1]
        vendor_chromatogram = vendor.GetChromatogramData

        def empty_vendor_chromatogram(*args):
            data = vendor_chromatogram(*args)
            data.PositionsArray = [[] for _ in data.PositionsArray]
            data.IntensitiesArray = [[] for _ in data.IntensitiesArray]
            return data

        with monkeypatch.context() as empty_vendor:
            empty_vendor.setattr(
                vendor, "GetChromatogramData", empty_vendor_chromatogram
            )
            empty = (
                cid_nl_backfill_acquisition.backfill_untargeted_cid_nl_sample_subject(
                    **recovery_args
                )
            )
        assert empty.assessment.candidate_peaks == ()
        assert empty.assessment.method_result == "HOLD"
        assert empty.backfill_input is None
        assert empty.acquisition.traces[0].coordinate.trace_point_count == 0
    finally:
        inputs.source_raws[inputs.paths[0]].intensity = original_intensity
        recovery_raw.__exit__(None, None, None)
