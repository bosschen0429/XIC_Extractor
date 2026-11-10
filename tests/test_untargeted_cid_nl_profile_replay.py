"""Persisted Discovery replay through runtime, retained evidence and writer."""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import xic_extractor.untargeted_execution.alignment as alignment_adapter
from tests.alignment_explanation_fixtures import (
    explanation_discovery_facts,
    explanation_summary,
)
from tests.alignment_pipeline_helpers import (
    render,
    written_cells,
    written_groups,
    written_source_candidates,
)
from tests.discovery_csv_fixtures import representative_event
from tests.untargeted_cid_nl_production_batch_fixtures import (
    _assert_composite_no_input_exclusion_coverage,
    _assert_fresh_process_curve_parity,
    _assert_jobs_match_direct_construction,
    _expected_capture_curve,
    _expected_retained_curve,
)
from tests.untargeted_cid_nl_production_batch_fixtures import (
    profile_replay as profile_replay,
)
from tests.untargeted_cid_nl_production_batch_fixtures import (
    replay_indexed_input as replay_indexed_input,
)
from tests.untargeted_partition_fixtures import (
    fixture_assessment,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)
from xic_extractor.untargeted_execution.partition import workers as partition_workers


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
    assert publication.alignment_config == AlignmentConfig(anchor_min_seed_events=1)
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
        candidate.peak_hypothesis_id: candidate.peak_hypothesis
        for retained_candidates in trace.retained_candidate_batches
        for candidate in retained_candidates
    }
    assert all(
        capture.measured_hypothesis is None
        or retained_hypotheses_by_id[capture.measured_hypothesis.hypothesis_id]
        == capture.measured_hypothesis
        for capture in trace.retained_captures
    )
    assert all(
        retained_hypotheses_by_id[support.hypothesis.hypothesis_id]
        == support.hypothesis
        for cells in trace.retained_cell_batches
        for cell in cells
        for support in cell.group_context.supports
    )
    # Owner-produced arrays survive both sidecar and runtime partition handoff.
    retained_discovery = tuple(completed.discovery_physical_records)
    # Indexed input exposes published candidate bindings lazily; eager input
    # also retains the source-only records behind merged representatives.
    expected_retained = (
        inputs.source_records if case.indexed_input else inputs.source_evidence
    )
    assert {item.peak_id for item in retained_discovery} == set(expected_retained)
    for retained in retained_discovery:
        source = inputs.source_evidence[retained.peak_id]
        assert (
            retained.evaluation.retained_prepared_signal
            == source.evaluation.retained_prepared_signal
        )
        assert retained.rt == source.rt
        assert retained.raw == source.raw
    persisted_capture_evidence = tuple(completed.capture_evidence)
    runtime_captures = tuple(
        evidence
        for result in trace.runtime_results
        for sample_result in result.sample_results
        for evidence in (
            *sample_result.adapter_result.capture_evidence,
            *(
                item
                for attempt in sample_result.backfill_attempts
                for item in attempt.capture_evidence
            ),
        )
    )
    assert len(persisted_capture_evidence) == len(runtime_captures)
    assert persisted_capture_evidence
    for retained, source in zip(
        persisted_capture_evidence, runtime_captures, strict=True
    ):
        assert retained.sample_key == source.sample_key
        assert retained.candidate_id == source.candidate_id
        assert retained.capture_stage == source.capture_stage
        assert retained.physical_evaluation is not None
        assert source.physical_evaluation is not None
        assert (
            retained.physical_evaluation.retained_prepared_signal
            == source.physical_evaluation.retained_prepared_signal
        )
        assert retained.observation_ref == source.observation_ref
        if retained.observation_ref is not None:
            assert retained.observation_ref.capture_stage == retained.capture_stage
            assert (
                retained.observation_ref.physical_evaluation
                == retained.physical_evaluation
            )

    expected_curves = []
    expected_curve_links = {}
    application = completed.application_result
    samples_by_stem = {
        sample.raw_stem: sample for sample in application.canonical_samples
    }
    for record in retained_discovery:
        work = record.work_item
        source_trace = SimpleNamespace(
            rt=record.rt,
            intensity=record.raw,
            mz=work.xic_mz,
            rt_min=work.xic_rt_min,
            rt_max=work.xic_rt_max,
            ppm_tol=work.xic_ppm_tol,
            acquisition_source_id=work.acquisition_source_id,
        )
        expected = _expected_retained_curve(
            kind="discovery_physical",
            candidate_id=record.peak_id,
            sample_id=samples_by_stem[work.sample_stem].sample_name,
            trace=source_trace,
            evaluation=record.evaluation,
            attempt_id=application.processing_attempt_id,
        )
        expected_curves.append(expected)
        expected_curve_links.setdefault(record.peak_id, []).append(expected)
    for evidence in persisted_capture_evidence:
        expected = _expected_capture_curve(evidence)
        expected_curves.append(expected)
        if evidence.candidate_id is not None:
            expected_curve_links.setdefault(evidence.candidate_id, []).append(expected)

    def close_and_verify_persisted_curves():
        completed.close()
        _assert_fresh_process_curve_parity(
            tmp_path / "producer-evidence.sqlite", expected_curves, expected_curve_links
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
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        write_alignment_explanation,
    )

    write_alignment_explanation(
        tmp_path / "producer-evidence.sqlite",
        full_audit=True,
        completed=completed,
        **explanation_discovery_facts(completed),
        peaks_by_sample=inputs.candidates,
        matrix_path=matrix_path,
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
    )
    assert len(trace.retained_contexts) == len(completed.application_result.children)
    assert len(trace.retained_contexts) == len(trace.consumed_contexts)
    # This fixture keeps the frozen partition; Backfill can add risk decisions.
    # The completed content, including those decisions, must survive finalization
    # and the spool. Python object identity is not a persisted contract.
    assert len(trace.completed_contexts) == len(trace.retained_contexts)
    if case.family_precursor_delta:
        assert any(
            context.backfill_membership_risks for context in trace.completed_contexts
        )
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
    # Bootstrap captures cross a disk handoff; preserve content, not addresses.
    for retained, consumed in zip(
        trace.retained_captures,
        trace.consumed_captures,
        strict=True,
    ):
        assert retained.candidate_id == consumed.candidate_id
        assert retained.selected_hypothesis == consumed.selected_hypothesis
        assert list(retained.trace.rt) == list(consumed.trace.rt)
        assert list(retained.trace.intensity) == list(consumed.trace.intensity)
    for context, child in zip(
        trace.completed_contexts,
        completed.application_result.children,
        strict=True,
    ):
        assert child.scientific_core.final_population == context.final_population
        assert child.scientific_core.lineage == ()
        assert child.scientific_core.grouping_result == context
    assert tuple(completed.initial_edges) == tuple(trace.retained_initial_edges)
    children = completed.application_result.children
    # Completed owner answers flow through projection, spool and writer once.
    assert len(trace.projection_builds) == len(children)
    # Binding consumes the closed capture; it must not repeat physical selection.
    assert not any(trace.binding_rechecks)
    if case.method_mode != "intersection":
        assert trace.binding_rechecks
        assert trace.mint_rechecks
        assert trace.mint_args
    assert len(trace.mint_rechecks) == len(trace.mint_args)
    assert all(
        counts == {"prepare": 0, "evaluate": 0} for counts in trace.mint_rechecks
    )
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
            ) == [1, 2]
            sample_id_by_stem = {
                sample.raw_stem: sample.sample_name
                for sample in child.scientific_core.canonical_samples
            }
            joint_group = next(
                group
                for group in child.scientific_core.final_population
                if len(group.member_subjects) == 2
            )
            assert {member.sample_key for member in joint_group.member_subjects} == {
                sample_id_by_stem["S2"],
                sample_id_by_stem["S3"],
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
            assert all(
                assessment.reason_codes != ("no_search_eligible_discovery_input",)
                for assessment in completed.method_assessments
            )
            assert len(written_groups(writer)) == 3
            assert len(tuple(written_cells(writer))) == 3 * len(inputs.sample_stems)
            (matrix_row,) = matrix_rows
            assert matrix_row["S1"] == ""
            assert matrix_row["S2"]
            assert matrix_row["S3"]
            # The fixture XIC ignores m/z, so S4's group-window trace holds a
            # complete peak without CID-NL: an MS1-only Backfill value.
            (s4_cell,) = (
                cell
                for cell in child.scientific_core.cell_observations
                if cell.group_hypothesis_id == joint_group.group_hypothesis_id
                and cell.sample_key == sample_id_by_stem["S4"]
            )
            assert s4_cell.evidence_states == ("ms1_only_backfill_observation",)
            assert matrix_row["S4"]
        else:
            assert len(tuple(written_cells(writer))) == len(inputs.sample_stems)
        close_and_verify_persisted_curves()
        return
    if case.nearby:
        assert len(matrix_rows) == (0 if case.method_mode == "intersection" else 1)
        batch = trace.captured_batches[0]
        assert len(batch.subject_batch.subjects) == 1
        for result in trace.runtime_results[0].sample_results:
            stem = result.job.sample.raw_stem
            (candidate,) = batch.context.peaks_by_sample[stem]
            record = batch.context.physical_records_by_peak[candidate.peak_id]
            assert (
                record.work_item.precursor_mz
                == representative_event(candidate).precursor_mz
            )
            assert (
                record.acquired_trace.coordinate.request.mz
                == representative_event(candidate).precursor_mz
            )
            assert record.evaluation.facts == inputs.physical_facts[stem]
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
            close_and_verify_persisted_curves()
            return
        if case.method_mode == "union":
            (child,) = completed.application_result.children
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
                facts = inputs.physical_facts[sample_stem]
                assert cell.value == facts.area_counts_seconds
                assert cell.selected_integration is not None
                assert cell.selected_integration.rt_apex_min == facts.apex_rt_min
            close_and_verify_persisted_curves()
            return
    cells = tuple(written_cells(writer))
    assert len(cells) == len(case.group_sizes) * len(inputs.sample_stems)
    for _row, sample_stem, cell in cells:
        if cell.selected_integration is None:
            assert inputs.separate_groups
            assert cell.value is None
            continue
        facts = inputs.physical_facts[sample_stem]
        assert cell.value == facts.area_counts_seconds
        assert cell.selected_integration.area_raw_counts_seconds != cell.value
        assert cell.selected_integration.rt_apex_min == facts.apex_rt_min
        assert cell.selected_integration.height_smoothed == facts.height
    expected_row_count = sum(size > 1 for size in case.group_sizes)
    assert len(matrix_rows) == expected_row_count
    for sample_stem, facts in inputs.physical_facts.items():
        sample_values = [
            float(row[sample_stem]) for row in matrix_rows if row[sample_stem]
        ]
        assert sample_values == pytest.approx(
            [facts.area_counts_seconds] if expected_row_count else []
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
        close_and_verify_persisted_curves()
        return
    if inputs.separate_groups:
        (child,) = completed.application_result.children
        scientific = child.scientific_core
        assert len(scientific.final_population) == 2
        assert len(scientific.cell_observations) == 2 * len(inputs.sample_stems)
        # Each sample's SUPPORT belongs to its own group. A group with K direct
        # supports revisits the other samples' windows, which hold no peak;
        # below K the other samples stay unsearched outside-group cells.
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
        close_and_verify_persisted_curves()
        return

    close_and_verify_persisted_curves()


def test_profile_replay_rejects_crossed_physical_capture(profile_replay) -> None:
    trace = profile_replay.trace
    from xic_extractor.peak_detection import capture as capture_owner

    bound_capture, binding_args = trace.bound_captures[0]
    changed_source = capture_owner.capture_peak_hypotheses(
        scope=bound_capture.scope,
        candidate_id=bound_capture.candidate_id,
        trace=replace(bound_capture.trace, source="different-capture-source"),
    )
    with pytest.raises(capture_owner.SampleLocalCaptureError):
        capture_owner.bind_owner_backed_capture_hypothesis(
            changed_source, **binding_args
        )


def test_profile_replay_retains_positive_and_empty_backfill_evidence(
    tmp_path: Path,
    monkeypatch,
    profile_replay,
) -> None:
    inputs = profile_replay.inputs
    trace = profile_replay.trace

    from xic_extractor.peak_detection import capture as capture_owner

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
        processing_attempt_id=job.processing_attempt_id,
        sample=job.sample,
        acquisition_source=job.acquisition_source_expectation.source,
        method_subject=job.method_subject,
        group_center_rt=inputs.center_rt,
        group_center_mz=job.method_subject.precursor_mz,
        support_policy=DdaCidNlPeakSupportPolicy(
            precursor_tolerance_da=0.01, neutral_loss_tolerance_ppm=10.0
        ),
        peak_config=inputs.peak_config,
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
    (retained_recovery,) = recovered.capture_evidence
    assert retained_recovery.observation_ref is observation
    assert retained_recovery.physical_evaluation is observation.physical_evaluation
    assert tuple(retained_recovery.trace.rt) == tuple(acquired.trace.rt)
    assert tuple(retained_recovery.trace.intensity) == tuple(acquired.trace.intensity)
    integ = observation.integration
    physical_facts = observation.physical_evaluation.facts
    assert physical_facts is not None
    assert integ.area_baseline_corrected == physical_facts.area_counts_seconds
    assert integ.height_smoothed == physical_facts.height
    assert recovered.assessment.method_result == "HOLD"
    original_intensity = inputs.source_raws[inputs.paths[0]].intensity
    recovery_evaluations = []
    evaluate_recovery = capture_owner.evaluate_physical_peak

    def retain_recovery_evaluation(*args, **kwargs):
        evaluation = evaluate_recovery(*args, **kwargs)
        recovery_evaluations.append(evaluation)
        return evaluation

    monkeypatch.setattr(
        capture_owner,
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
            assert recovery_evaluations[-1].facts is None
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
        (retained_unavailable,) = unavailable.capture_evidence
        assert retained_unavailable.observation_ref is None
        assert retained_unavailable.physical_evaluation.state == "unavailable"
        signal = retained_unavailable.physical_evaluation.retained_prepared_signal
        assert signal is None  # The owner skips preparation for an all-zero trace.
        assert len(retained_unavailable.trace.rt) > 0
        assert all(value == 0.0 for value in retained_unavailable.trace.intensity)

        # The external vendor can return an empty chromatogram for a valid
        # native scan window. Keep the acquisition and scientific owners live.
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
        (retained_empty,) = empty.capture_evidence
        assert retained_empty.observation_ref is None
        assert retained_empty.physical_evaluation.state == "unavailable"
        assert retained_empty.physical_evaluation.reason == "no_signal_above_baseline"
        assert retained_empty.physical_evaluation.integration is None
        assert retained_empty.physical_evaluation.retained_prepared_signal is None
        assert len(retained_empty.trace.rt) == len(retained_empty.trace.intensity) == 0

    finally:
        inputs.source_raws[inputs.paths[0]].intensity = original_intensity
        recovery_raw.__exit__(None, None, None)

    # The actual recovery owner ran above under this request and attempt.
    # Exercise its production sink directly, without altering the completed
    # scientific population or manufacturing a new recovery identity.
    from xic_extractor.alignment_explanation.writer import (
        ExplanationReportWriter,
    )
    from xic_extractor.diagnostics.alignment_explanation_projection import (
        _emit_curve_payload,
    )

    recovery_path = tmp_path / "recovery-evidence.sqlite"
    recovery_sources = (retained_recovery, retained_unavailable, retained_empty)
    with ExplanationReportWriter(recovery_path) as sink:
        for evidence in recovery_sources:
            _emit_curve_payload(
                sink,
                evidence.observation_ref,
                evidence.sample_key,
                kind="backfill_observation",
                trace=evidence.trace,
                evaluation=evidence.physical_evaluation,
                provenance={
                    "processing_attempt_id": evidence.processing_attempt_id,
                    "method_subject_id": evidence.method_subject_id,
                },
            )
        sink.finalize(
            explanation_summary(
                {
                    "processing_attempt_id": (job.processing_attempt_id),
                },
                review_only=sink.review_only,
            )
        )
    _assert_fresh_process_curve_parity(
        recovery_path, [_expected_capture_curve(item) for item in recovery_sources]
    )


def test_profile_replay_rejects_changed_partition_inputs(
    tmp_path: Path, profile_replay
) -> None:
    inputs = profile_replay.inputs
    trace = profile_replay.trace
    case = profile_replay.case

    from xic_extractor.processing_identity import IdentityContractError

    batch = trace.captured_batches[0]

    if not case.indexed_input:
        wrong_records = dict(batch.context.physical_records_by_peak)
        keys = tuple(wrong_records)
        wrong_records[keys[0]] = wrong_records[keys[1]]
        with pytest.raises(IdentityContractError, match="crossed peak"):
            alignment_adapter.prepare_eager_untargeted_plan(
                raw_paths=inputs.paths,
                sample_metadata=tuple(sample for sample in batch.context.samples),
                settings=inputs.settings,
                peak_config=inputs.peak_config,
                peaks_by_sample=batch.context.peaks_by_sample,
                discovery_receipts_by_sample=inputs.receipts,
                dll_dir=tmp_path,
                signal_processing_profile=inputs.profile,
                physical_records_by_peak=wrong_records,
            )
