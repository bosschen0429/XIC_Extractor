"""Project completed Untargeted CID-NL owners into the published explanation report."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence, Sized
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any, cast

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.owner_group_delivery import feature_id_label
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.alignment_explanation.review import review_cell
from xic_extractor.alignment_explanation.storage import encode_review_curve
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.output.alignment.tsv_writer import UntargetChildOutputs
from xic_extractor.rt_calibration_codec import rt_calibration_payload

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_observation.observations import (
        FinalGroupCellObservation,
    )
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.alignment.rt_frame import SampleRtFrame
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
    from xic_extractor.discovery.models import DiscoveryPeak
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlMethodAssessment,
    )
    from xic_extractor.untargeted_execution.execution_result import (
        CompletedUntargetBatchWorkflowResult,
    )


_RUN_CONTRACT_SUMMARY = {
    "scientific_input_schema_version": "untargeted_cid_nl_real_data_inputs",
    "processing_algorithm_version": "untargeted_cid_nl_real_data_algorithm",
    "processing_contract_version": "untargeted_cid_nl_real_data_contract",
}


def _fields(value: Any, *names: str) -> dict[str, Any]:
    return {name: getattr(value, name) for name in names}


def _cell_observation_content_payload(content: Any) -> dict[str, Any]:
    return {
        "sample_key": content.sample_key,
        "group_owner_ids": list(content.group_owner_ids),
        "group_method_subject_id": content.group_method_subject_id,
        "group_target_label": content.group_target_label,
        "technical_state": content.technical_state,
        "method_result": content.method_result,
        "physical_peak_key": content.physical_peak_key,
        "peak_hypothesis_id": content.peak_hypothesis_id,
        "reason_codes": list(content.reason_codes),
        "outside_group": (
            asdict(content.outside_group) if content.outside_group is not None else None
        ),
    }


def _final_cell_observation_payload(observation: Any) -> dict[str, Any]:
    return {
        "evidence_states": observation.evidence_states,
        "sample_key": (observation.canonical_sample.sample_name),
        "predecessor_cells": [
            {"content": _cell_observation_content_payload(ref)}
            for ref in observation.predecessor_cells
        ],
        "backfill_cell": (
            None
            if observation.backfill_cell is None
            else {
                "content": _cell_observation_content_payload(observation.backfill_cell)
            }
        ),
    }


def _emit_curve_payload(
    sink: ExplanationReportWriter | _CurvesUnlessCaptureEvidence,
    ref: Any,
    sample_id: str,
    *,
    candidate_id: str | None = None,
    kind: str = "initial_capture",
    trace: Any = None,
    evaluation: Any = None,
    provenance: Mapping[str, Any] | None = None,
) -> None:
    """Serialize retained owner values; missing processing remains unavailable."""
    content = ref
    mz = trace.mz if trace is not None else content.mz if content is not None else None
    if sink.review_only and not any(
        lower <= mz <= upper for lower, upper in sink.curve_mz_ranges if mz is not None
    ):
        return
    if evaluation is None and ref is not None:
        evaluation = ref.physical_evaluation
    prepared = evaluation.retained_prepared_signal if evaluation is not None else None
    if trace is None and content is None:
        return
    generation = ref.generation_provenance if ref is not None else None
    actual = {
        "profile_version": None,
        "acquisition_source_id": None,
        "processing_attempt_id": None,
        "selected_trace_indices": None,
        "resolver_mode": None,
        "proposal_sources": None,
        "method_subject_id": None,
        "profile": None,
        "physical_state": None,
        "physical_reason": None,
        **(provenance or {}),
    }
    if evaluation is not None:
        actual.update(
            profile=asdict(evaluation.prepared.profile),
            physical_state=evaluation.state,
            physical_reason=evaluation.reason,
        )
    if prepared is not None:
        actual.update(
            profile_version=prepared.profile.schema_version,
            profile=asdict(prepared.profile),
        )
    if generation is not None:
        actual.update(
            _fields(
                generation,
                "acquisition_source_id",
                "selected_trace_indices",
                "resolver_mode",
                "proposal_sources",
            )
        )
    elif trace is not None:
        actual["acquisition_source_id"] = trace.acquisition_source_id
    obs_id = content.physical_peak_key if content is not None else None
    record_id = obs_id or candidate_id or actual.get("method_subject_id")
    if record_id is None:
        raise ValueError("retained curve lacks an actual owner binding")
    if trace is not None:
        rt, raw = trace.rt, trace.intensity
        coordinate = _fields(trace, "mz", "rt_min", "rt_max", "ppm_tol")
    else:
        assert content is not None and generation is not None
        rt, raw = content.rt, content.intensity
        coordinate = {
            "mz": content.mz,
            "rt_min": generation.query_rt_min,
            "rt_max": generation.query_rt_max,
            "ppm_tol": generation.query_ppm_tol,
        }
    integration = (
        content.integration
        if content is not None
        else (evaluation.integration if evaluation is not None else None)
    )
    envelope = {
        "schema_version": "curve_payload",
        "kind": kind,
        "sample_id": sample_id,
        "candidate_id": candidate_id,
        "physical_peak_key": obs_id,
        "trace_coordinate": {name: float(value) for name, value in coordinate.items()},
        "curves": {
            "rt": [float(x) for x in rt],
            "raw": [float(y) for y in raw],
            "baseline": list(prepared.baseline) if prepared is not None else None,
            "smoothed": list(prepared.smoothed) if prepared is not None else None,
            "positive": list(prepared.positive_signal)
            if prepared is not None
            else None,
        },
        "integration": asdict(integration) if integration is not None else None,
        "provenance": actual,
    }
    # One envelope per actual content/binding; candidate associations ride on it.
    sink.add_curve_payload(
        sample_id=sample_id,
        kind=kind,
        record_id=record_id,
        payload=envelope,
    )


def _signal_digest(content: Any) -> str:
    """Digest of the selected raw signal that identifies one physical peak."""
    return hashlib.blake2b(
        repr((content.sample_key, content.mz, content.rt, content.intensity)).encode(
            "utf-8"
        ),
        digest_size=16,
    ).hexdigest()


def _peak_coordinates(content: Any) -> tuple[float, float, float]:
    return (
        content.mz,
        content.integration.rt_apex_min,
        content.generation_provenance.query_ppm_tol,
    )


_Write = tuple[str, tuple[Any, ...], dict[str, Any]]


class _RecordedWrites:
    """Record one child's report writes by their natural keys.

    The writer numbers peaks, members, risk owners and records by first
    appearance, so the writes are replayed into it in the batch's child order
    instead of being written while each child composes.
    """

    def __init__(self, *, review_only: bool, curve_mz_ranges: Sequence[Any]):
        self.review_only = review_only
        self.curve_mz_ranges = tuple(curve_mz_ranges)
        self.writes: list[_Write] = []
        self.unless_capture_evidence = _CurvesUnlessCaptureEvidence(self)

    def __getattr__(self, name: str) -> Callable[..., None]:
        if name not in _RECORDED_WRITER_METHODS:
            raise AttributeError(name)

        def record(*args: Any, **kwargs: Any) -> None:
            self.writes.append((name, args, kwargs))

        return record


class _CurvesUnlessCaptureEvidence:
    """Curves written only when the batch retained no capture evidence."""

    def __init__(self, recorded: _RecordedWrites):
        self._recorded = recorded
        self.review_only = recorded.review_only
        self.curve_mz_ranges = recorded.curve_mz_ranges

    def add_curve_payload(self, **kwargs: Any) -> None:
        self._recorded.writes.append(("curve_unless_capture_evidence", (), kwargs))


_RECORDED_WRITER_METHODS = frozenset(
    {
        "add_terminal",
        "add_final_group",
        "set_row_destination",
        "add_cells",
        "set_cell_curve",
        "set_cell_position",
    }
)


def project_alignment_explanation_child(
    child: FinalizedUntargetApplicationResult,
    *,
    sample_rt_frame: SampleRtFrame | None,
    capture_evidence_retained: bool,
    full_audit: bool = False,
    curve_mz_ranges: Sequence[tuple[float, float]] = (),
) -> tuple[_Write, ...]:
    """Record the report writes of one finalized child without batch numbering.

    A final group's ``feature_id`` is recorded as its position among the
    child's written groups; replay turns it into the batch label. Terminal
    curves are written only when the batch retained no capture evidence, so
    a child whose partition retained some records none.
    """
    sink = _RecordedWrites(review_only=not full_audit, curve_mz_ranges=curve_mz_ranges)
    core = child.scientific_core

    def locate(content: Any) -> str:
        key: str = content.physical_peak_key
        sink.writes.append(
            (
                "locate",
                (content.sample_key, key, _peak_coordinates(content)),
                {"digest": _signal_digest(content)},
            )
        )
        return key

    for terminal in core.terminal_subjects:
        key = terminal.candidate_id, terminal.sample_key
        if not isinstance(terminal, ObservedSearchSubject):
            sink.add_terminal(*key, variant=terminal.variant)
            continue
        hypothesis = terminal.hypothesis
        ref = hypothesis.peak
        sink.add_terminal(
            *key,
            variant=terminal.variant,
            hypothesis_id=hypothesis.hypothesis_id,
            physical_peak_key=locate(ref),
        )
        if not capture_evidence_retained:
            _emit_curve_payload(
                sink.unless_capture_evidence,
                ref,
                sample_id=key[1],
                kind=ref.capture_stage,
            )

    # Same row order as the TSV writer that labels rows.
    feature_offsets = {
        group.group_hypothesis_id: offset
        for offset, (group, _cells) in enumerate(child.group_cells)
    }
    # Recorded group position of each empty cell, and the group centre and
    # query ppm used to inspect it.
    positions: dict[tuple[str, str], float] = {}
    centers: dict[str, tuple[float, float]] = {}
    context_by_group = {
        cell.group_hypothesis_id: cell.effective_cells[0].group_context
        for cell in core.cell_observations
        if cell.effective_cells
    }
    frame = sample_rt_frame
    sample_stems = {
        sample.sample_name: sample.raw_stem for sample in child.canonical_samples
    }
    group_positions = {}
    for group in core.final_population:
        peaks = [
            peak for member in group.member_subjects for peak in member.physical_peaks
        ]
        raw_positions = [peak.integration.rt_apex_min for peak in peaks]
        common_positions = [
            estimate.rt_min
            for peak in peaks
            if frame is not None
            and (
                estimate := frame.estimate_rt(
                    sample_stems[peak.sample_key], peak.integration.rt_apex_min
                )
            )
            is not None
        ]
        if raw_positions:
            group_positions[group.group_hypothesis_id] = (
                median(raw_positions),
                median(common_positions) if common_positions else None,
            )
    for final_cell in core.cell_observations:
        refs = {
            ref.physical_peak_key: ref
            for ref in (
                *final_cell.direct_observations,
                *(hypothesis.peak for hypothesis in final_cell.direct_hypotheses),
            )
        }
        for ref in refs.values():
            locate(ref)
        if refs:
            continue
        context = (
            final_cell.effective_cells[0].group_context
            if final_cell.effective_cells
            else context_by_group.get(final_cell.group_hypothesis_id)
        )
        if context is None:
            continue
        position = group_positions.get(final_cell.group_hypothesis_id)
        if position is None:
            continue
        raw_position, common_position = position
        estimate = (
            frame.estimate_observed_rt(
                final_cell.canonical_sample.raw_stem, common_position
            )
            if frame is not None and common_position is not None
            else None
        )
        gid = final_cell.group_hypothesis_id
        positions[gid, final_cell.sample_key] = (
            estimate.rt_min if estimate is not None else raw_position
        )
        center = (
            context.group.group_center_mz,
            context.method_subject.precursor_tolerance_ppm,
        )
        if centers.setdefault(gid, center) != center:
            raise ValueError(f"one group has two inspection centres: {gid}")
    for group in core.final_population:
        position = centers.get(group.group_hypothesis_id)
        members = [
            {
                **_fields(member, "sample_key", "subject_kind", "subject_id"),
                "physical_peak_keys": [locate(peak) for peak in member.physical_peaks],
            }
            for member in group.member_subjects
        ]
        sink.add_final_group(
            group.group_hypothesis_id,
            feature_id=feature_offsets.get(group.group_hypothesis_id),
            disposition=group.disposition,
            members=members,
            center_mz=position[0] if position else None,
            ppm=position[1] if position else None,
        )
    risks_by_group_sample: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for (
        group_id,
        sample_id,
        backfill_risk,
    ) in core.grouping_result.backfill_membership_risks:
        risks_by_group_sample[group_id, sample_id].append(asdict(backfill_risk))

    for group, group_cells in child.group_cells:
        group_id = group.group_hypothesis_id
        cells = [
            {
                **review_cell(decision),
                "membership_risks": risks_by_group_sample.get(
                    (
                        group_id,
                        decision.subject.canonical_sample.sample_name,
                    ),
                    [],
                ),
            }
            for decision in group_cells
        ]
        sink.set_row_destination(group_id, group.destination)
        if full_audit:
            for decision, cell in zip(group_cells, cells, strict=True):
                cell["observation"] = _final_cell_observation_payload(decision.evidence)
        sink.add_cells(group_id, cells)
        for decision in group_cells:
            if decision.value is None:
                continue
            evidence = cast("FinalGroupCellObservation", decision.evidence)
            trace = next(
                peak.source_trace
                for peak in evidence.value_peaks
                if peak.integration == decision.selected_integration
            )
            sink.set_cell_curve(
                group_id,
                decision.subject.canonical_sample.sample_name,
                encode_review_curve(trace.rt, trace.intensity),
            )
        for cell in cells:
            cell_position = positions.get((group_id, cell["sample_key"]))
            if cell_position is not None:
                sink.set_cell_position(group_id, cell["sample_key"], cell_position)
    return tuple(sink.writes)


@dataclass(frozen=True)
class PreparedAlignmentExplanation:
    """Consume projected children now; publish only after the Matrix exists."""

    consume_child: Callable[[UntargetChildOutputs, int], None]
    finalize: Callable[[Path], Path]


def write_alignment_explanation(
    path: Path,
    *,
    completed: CompletedUntargetBatchWorkflowResult,
    children: Sequence[FinalizedUntargetApplicationResult],
    peaks_by_sample: Mapping[str, Sequence[DiscoveryPeak]],
    matrix_path: Path,
    alignment_config: AlignmentConfig,
    precursor_mz_by_candidate: Mapping[str, float],
    neutral_loss_by_tag: Mapping[str, float],
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    full_audit: bool = False,
    curve_mz_ranges: Sequence[tuple[float, float]] = (),
) -> Path:
    """Persist in-memory composed children with Matrix publication provenance.

    ``children`` are the batch's finalized children in its child order.
    """
    with prepare_alignment_explanation(
        path,
        completed=completed,
        peaks_by_sample=peaks_by_sample,
        alignment_config=alignment_config,
        precursor_mz_by_candidate=precursor_mz_by_candidate,
        neutral_loss_by_tag=neutral_loss_by_tag,
        ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
        progress_callback=progress_callback,
        full_audit=full_audit,
        curve_mz_ranges=curve_mz_ranges,
    ) as explanation:
        first_group = 0
        for child in children:
            explanation.consume_child(
                UntargetChildOutputs(
                    groups=(),
                    explanation=project_alignment_explanation_child(
                        child,
                        sample_rt_frame=completed.sample_rt_frame,
                        capture_evidence_retained=bool(completed.capture_evidence),
                        full_audit=full_audit,
                        curve_mz_ranges=curve_mz_ranges,
                    ),
                ),
                first_group,
            )
            first_group += len(child.group_cells)
        return explanation.finalize(matrix_path)


@dataclass(frozen=True)
class ExplanationBatch:
    """One completed batch with the Discovery inputs its report declares."""

    completed: CompletedUntargetBatchWorkflowResult
    peaks_by_sample: Mapping[str, Sequence[DiscoveryPeak]]
    precursor_mz_by_candidate: Mapping[str, float]
    neutral_loss_by_tag: Mapping[str, float]
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None


@dataclass(frozen=True)
class BatchExplanationReplay:
    """Replay independently aligned batches' children into one report.

    ``consume(child, batch, label, merge_into)`` replays one child of the
    batch at index ``batch``. ``label(offset)`` is the feature ID of the
    child's written group at ``offset``; ``merge_into`` maps an offset to an
    already replayed final group whose result row the group joins. It returns
    each offset's final group ID.
    """

    consume: Callable[
        [UntargetChildOutputs, int, Callable[[int], str], Mapping[int, str]],
        dict[int, str],
    ]
    finalize: Callable[[Path], Path]


@contextmanager
def prepare_alignment_explanation(
    path: Path,
    *,
    completed: CompletedUntargetBatchWorkflowResult,
    peaks_by_sample: Mapping[str, Sequence[DiscoveryPeak]],
    alignment_config: AlignmentConfig,
    precursor_mz_by_candidate: Mapping[str, float],
    neutral_loss_by_tag: Mapping[str, float],
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    full_audit: bool = False,
    curve_mz_ranges: Sequence[tuple[float, float]] = (),
) -> Iterator[PreparedAlignmentExplanation]:
    """Keep one bounded transaction while the TSV pass replays each child.

    The children were projected with the same ``full_audit`` and
    ``curve_mz_ranges``.
    """
    frame = completed.sample_rt_frame
    with prepare_batch_alignment_explanation(
        path,
        (
            ExplanationBatch(
                completed,
                peaks_by_sample,
                precursor_mz_by_candidate,
                neutral_loss_by_tag,
                ms1_apex_mz_by_candidate,
            ),
        ),
        alignment_config=alignment_config,
        calibration=frame.calibration_for_snapshot if frame is not None else None,
        progress_callback=progress_callback,
        full_audit=full_audit,
        curve_mz_ranges=curve_mz_ranges,
    ) as replay:

        def consume_child(child: UntargetChildOutputs, first_group: int) -> None:
            replay.consume(
                child,
                0,
                lambda offset: feature_id_label(first_group + offset + 1),
                {},
            )

        yield PreparedAlignmentExplanation(consume_child, replay.finalize)


@contextmanager
def prepare_batch_alignment_explanation(
    path: Path,
    batches: Sequence[ExplanationBatch],
    *,
    alignment_config: AlignmentConfig,
    calibration: BatchRtCalibration | None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    full_audit: bool = False,
    curve_mz_ranges: Sequence[tuple[float, float]] = (),
) -> Iterator[BatchExplanationReplay]:
    """One report for independently aligned batches; the first is the reference.

    Each batch declares its own samples, candidates, subjects, assessments and
    initial groups; ``calibration`` is the run's replay calibration.
    """
    with ExplanationReportWriter(
        path,
        review_only=not full_audit,
        curve_mz_ranges=curve_mz_ranges,
    ) as sink:
        samples = {
            sample.raw_stem: sample
            for batch in batches
            for sample in batch.completed.application_result.canonical_samples
        }
        sink.declare_samples(
            [
                {"sample_key": sample.sample_name, "sample_stem": stem}
                for stem, sample in samples.items()
            ]
        )
        located_peaks: dict[str, str] = {}

        def add_located_peak(
            sample_key: str,
            key: str,
            coordinates: tuple[float, float, float],
            *,
            digest: str,
        ) -> None:
            """Written locator of one physical peak; never shared by two peaks.

            A peak is its selected raw signal. Reads of it in other XIC windows
            can integrate differently; each cell keeps its own value and curve.
            """
            if located_peaks.setdefault(key, digest) != digest:
                raise ValueError(f"distinct physical peaks share one locator: {key}")
            sink.add_peak(sample_key, key, coordinates)

        def locate(content: Any) -> str:
            key: str = content.physical_peak_key
            add_located_peak(
                content.sample_key,
                key,
                _peak_coordinates(content),
                digest=_signal_digest(content),
            )
            return key

        emitted_assessments: set[CidNlMethodAssessment] = set()

        def report_progress(stage: str, completed: int, total: int | None) -> None:
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "alignment_explanation_progress",
                        "stage": f"alignment.explanation.{stage}",
                        "completed": completed,
                        "total": total,
                        "records_written": sink.record_count,
                        "candidates_written": sink.candidate_count,
                    }
                )

        def phase_items(stage: str, values: Iterable[Any]) -> Iterator[Any]:
            if progress_callback is None:
                yield from values
                return
            total = len(values) if isinstance(values, Sized) else None
            report_progress(stage, 0, total)
            completed_count = 0
            for item in values:
                yield item
                completed_count += 1
                if completed_count % 256 == 0:
                    report_progress(stage, completed_count, total)
            if completed_count % 256:
                report_progress(stage, completed_count, total)

        def emit_assessment(
            assessment: CidNlMethodAssessment, capture_evidence: bool
        ) -> None:
            content = assessment
            # The same settled attempt can reach this projection through more
            # than one completed relation; its content is the record.
            if content in emitted_assessments:
                return
            if (
                content.detector_invocation_id == "untargeted-cid-nl-backfill"
                and content.method_result == "SUPPORT"
            ):
                if not capture_evidence:
                    for peak in assessment.candidate_peaks:
                        _emit_curve_payload(
                            sink,
                            peak,
                            content.sample_key,
                            kind="backfill_observation",
                        )
            sink.add_assessment(
                {
                    **_fields(
                        content,
                        "sample_key",
                        "acquisition_source_id",
                        "provider_id",
                        "method_id",
                        "method_version",
                        "detector_invocation_id",
                        "technical_state",
                        "method_result",
                        "reason_codes",
                    ),
                    "method_subject_id": content.method_subject.method_subject_id,
                    **(
                        {"evaluation_receipt": asdict(content.evaluation_receipt)}
                        if full_audit
                        else {}
                    ),
                },
                candidate_physical_peak_keys=tuple(
                    locate(peak) for peak in assessment.candidate_peaks
                ),
            )
            emitted_assessments.add(content)

        for batch in batches:
            completed = batch.completed
            application = completed.application_result
            subjects = application.method_subjects
            memberships = dict(subjects.candidate_memberships)
            measured_mz = batch.ms1_apex_mz_by_candidate or {}
            for stem, declared in batch.peaks_by_sample.items():
                sid = samples[stem].sample_name
                for candidate in phase_items("candidates", declared):
                    key = candidate.peak_id, sid
                    subject_id = memberships[candidate.peak_id]
                    sink.add_candidate(
                        *key,
                        precursor_mz=batch.precursor_mz_by_candidate[candidate.peak_id],
                        ms1_apex_mz=measured_mz.get(candidate.peak_id),
                        neutral_loss_tag=candidate.neutral_loss_tag,
                        peak_status=candidate.status,
                        method_subject_id=subject_id,
                    )

            capture_evidence = completed.capture_evidence
            for evidence in phase_items(
                "capture_evidence",
                capture_evidence if full_audit or curve_mz_ranges else (),
            ):
                sid = evidence.sample_key
                stage = (
                    "backfill_observation"
                    if evidence.capture_stage == "method_backfill"
                    else evidence.capture_stage
                )
                _emit_curve_payload(
                    sink,
                    evidence.observation_ref,
                    sid,
                    candidate_id=evidence.candidate_id,
                    kind=stage,
                    trace=evidence.trace,
                    evaluation=evidence.physical_evaluation,
                    provenance=_fields(
                        evidence,
                        "processing_attempt_id",
                        "method_subject_id",
                    ),
                )

            for subject in phase_items("method_subjects", subjects.subjects):
                sink.add_method_subject(
                    {
                        **_fields(
                            subject,
                            "method_subject_id",
                            "subject_kind",
                            "precursor_mz",
                            "precursor_tolerance_ppm",
                            "neutral_loss_da",
                        ),
                        "composition": asdict(subject.composition)
                        if subject.composition
                        else None,
                    }
                )

            for assessment in phase_items(
                "method_assessments", completed.method_assessments
            ):
                emit_assessment(assessment, bool(capture_evidence))

            for excluded in phase_items("exclusions", application.excluded_candidates):
                key = excluded.candidate_id, excluded.sample_key
                refs = (*excluded.method_assessments, *excluded.composite_assessments)
                sink.add_exclusion(*key, excluded.reason_code)
                for assessment in refs:
                    emit_assessment(assessment, bool(capture_evidence))

        child_count = 0
        matrix_row_count = 0
        child_total = sum(
            len(batch.completed.application_result.child_outputs) for batch in batches
        )
        report_progress("children", 0, child_total)

        def consume(
            child: UntargetChildOutputs,
            batch: int,
            label: Callable[[int], str],
            merge_into: Mapping[int, str],
        ) -> dict[int, str]:
            nonlocal child_count, matrix_row_count
            capture_evidence = bool(batches[batch].completed.capture_evidence)
            merged: dict[str, str] = {}
            final_groups: dict[int, str] = {}
            for name, args, kwargs in cast(
                "tuple[_Write, ...]", child.explanation or ()
            ):
                if name == "locate":
                    add_located_peak(*args, **kwargs)
                elif name == "curve_unless_capture_evidence":
                    if not capture_evidence:
                        sink.add_curve_payload(**kwargs)
                elif name == "add_final_group":
                    offset = kwargs["feature_id"]
                    if offset in merge_into:
                        merged[args[0]] = merge_into[offset]
                        sink.extend_final_group(
                            merge_into[offset],
                            disposition=kwargs["disposition"],
                            members=kwargs["members"],
                        )
                    else:
                        sink.add_final_group(
                            *args,
                            **{
                                **kwargs,
                                "feature_id": None if offset is None else label(offset),
                            },
                        )
                    if offset is not None:
                        final_groups[offset] = merged.get(args[0]) or args[0]
                else:
                    group_id = merged.get(args[0]) or args[0]
                    if name == "set_row_destination":
                        if args[0] in merged:
                            continue
                        matrix_row_count += args[1] == "matrix"
                    getattr(sink, name)(group_id, *args[1:], **kwargs)
            child_count += 1
            if child_count % 256 == 0:
                report_progress("children", child_count, child_total)
            return final_groups

        def finalize(matrix_path: Path) -> Path:
            if child_count != child_total:
                raise ValueError("explanation did not consume every finalized child")
            if child_count % 256:
                report_progress("children", child_count, child_total)
            for batch in batches:
                for group in phase_items(
                    "initial_groups", batch.completed.initial_groups
                ):
                    inference_basis = (
                        group.inference_basis
                        if isinstance(group, CrossSamplePeakGroupHypothesis)
                        else None
                    )
                    sink.add_initial_group(
                        disposition=group.disposition,
                        members=[
                            {
                                **_fields(
                                    member, "sample_key", "subject_kind", "subject_id"
                                ),
                                "physical_peak_keys": [
                                    locate(peak) for peak in member.physical_peaks
                                ],
                            }
                            for member in group.member_subjects
                        ],
                        inference_basis=None
                        if inference_basis is None
                        else asdict(inference_basis),
                        inference_decisions=None
                        if inference_basis is None
                        else [
                            asdict(decision) for decision in group.inference_decisions
                        ],
                    )

            report_progress("finalizing", sink.record_count, None)
            with Path(matrix_path).open("rb") as handle:
                matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            reference = batches[0].completed.application_result
            summary: dict[str, Any] = {
                **_RUN_CONTRACT_SUMMARY,
                "processing_attempt_id": reference.processing_attempt_id,
                "samples": [
                    {
                        "sample_stem": stem,
                        "sample_key": (sample.sample_name),
                    }
                    for stem, sample in samples.items()
                ],
                "acquisition_sources": [
                    asdict(source)
                    for source in sorted(
                        (
                            source
                            for batch in batches
                            for source in (
                                batch.completed.application_result.acquisition_sources
                            )
                        ),
                        key=lambda source: source.acquisition_source_id,
                    )
                ],
                "matrix_row_count": matrix_row_count,
                "matrix_path": str(matrix_path),
                "matrix_sha256": matrix_hash,
                "alignment_config": asdict(alignment_config),
                "neutral_loss_by_tag": dict(
                    sorted(
                        (tag, loss)
                        for batch in batches
                        for tag, loss in batch.neutral_loss_by_tag.items()
                    )
                ),
                "retention": "full_audit" if full_audit else "review",
                "selection_evidence_capability": "recorded_decisions",
                "curve_retention": "all"
                if full_audit
                else "selected"
                if curve_mz_ranges
                else "none",
                "curve_mz_ranges": list(curve_mz_ranges),
                "audit_availability": {
                    name: "recorded"
                    if all(getattr(batch.completed, name) for batch in batches)
                    else "unavailable"
                    for name in ("method_assessments", "initial_groups")
                },
            }
            summary["sample_rt_calibration"] = (
                rt_calibration_payload(calibration) if calibration is not None else None
            )
            summary["injection_order"] = {
                stem: sample.injection_order
                for stem, sample in samples.items()
                if sample.injection_order is not None
            }
            return sink.finalize(summary)

        yield BatchExplanationReplay(consume, finalize)
