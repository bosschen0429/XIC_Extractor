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
from xic_extractor.peak_detection.detector_invocations import GROUP_BACKFILL_DETECTOR
from xic_extractor.rt_calibration_codec import rt_calibration_payload

if TYPE_CHECKING:
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.alignment.rt_frame import SampleRtFrame
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
    from xic_extractor.discovery.models import DiscoveryPeak
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlMethodAssessment,
    )
    from xic_extractor.peak_detection.observations import PeakObservation
    from xic_extractor.untargeted_execution.execution_result import (
        CompletedUntargetBatchWorkflowResult,
    )


def _fields(value: Any, *names: str) -> dict[str, Any]:
    return {name: getattr(value, name) for name in names}


def _signal_digest(content: Any) -> str:
    """Digest of the selected raw signal that identifies one physical peak."""
    return hashlib.blake2b(
        repr((content.sample_key, content.mz, content.rt, content.intensity)).encode(
            "utf-8"
        ),
        digest_size=16,
    ).hexdigest()


def _peak_coordinates(content: PeakObservation) -> tuple[float, float, float]:
    return (
        content.mz,
        content.rt_apex_min,
        content.generation_provenance.query_ppm_tol,
    )


_Write = tuple[str, tuple[Any, ...], dict[str, Any]]


class _RecordedWrites:
    """Record one child's report writes by their natural keys.

    The writer numbers peaks, members, risk owners and records by first
    appearance, so the writes are replayed into it in the batch's child order
    instead of being written while each child composes.
    """

    def __init__(self):
        self.writes: list[_Write] = []

    def __getattr__(self, name: str) -> Callable[..., None]:
        if name not in _RECORDED_WRITER_METHODS:
            raise AttributeError(name)

        def record(*args: Any, **kwargs: Any) -> None:
            self.writes.append((name, args, kwargs))

        return record


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
) -> tuple[_Write, ...]:
    """Record the report writes of one finalized child without batch numbering.

    A final group's ``feature_id`` is recorded as its position among the
    child's written groups; replay turns it into the batch label.
    """
    sink = _RecordedWrites()
    core = child.scientific_core

    def locate(content: PeakObservation) -> str:
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
            continue
        hypothesis = terminal.hypothesis
        ref = hypothesis.peak
        sink.add_terminal(
            *key,
            hypothesis_id=hypothesis.hypothesis_id,
            physical_peak_key=locate(ref),
        )
    feature_offsets = {
        group.group_hypothesis_id: offset
        for offset, (group, _cells) in enumerate(child.group_cells)
    }
    positions: dict[tuple[str, str], float] = {}
    centers: dict[str, tuple[float, float]] = {}
    for final_cell in core.cell_observations:
        if not final_cell.effective_cells:
            continue
        context = final_cell.effective_cells[0].group_context
        center = (
            context.group.group_center_mz,
            context.method_subject.precursor_tolerance_ppm,
        )
        gid = final_cell.group_hypothesis_id
        if centers.setdefault(gid, center) != center:
            raise ValueError(f"one group has two inspection centres: {gid}")
    frame = sample_rt_frame
    sample_stems = {
        sample.sample_name: sample.raw_stem for sample in child.canonical_samples
    }
    group_positions = {}
    for group in core.final_population:
        peaks = [
            peak for member in group.member_subjects for peak in member.physical_peaks
        ]
        raw_positions = [peak.rt_apex_min for peak in peaks]
        common_positions = [
            estimate.rt_min
            for peak in peaks
            if frame is not None
            and (
                estimate := frame.estimate_rt(
                    sample_stems[peak.sample_key], peak.rt_apex_min
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
                *final_cell.ms1_only_group_observations,
                *(hypothesis.peak for hypothesis in final_cell.direct_hypotheses),
            )
        }
        for ref in refs.values():
            locate(ref)
        if refs or final_cell.group_hypothesis_id not in centers:
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
        sink.add_cells(group_id, cells)
        for decision in group_cells:
            if decision.value is None:
                continue
            trace = cast("PeakObservation", decision.value_peak).source_trace
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

    ``consume(child, label, merge_into)`` replays one child.
    ``label(offset)`` is the feature ID of the
    child's written group at ``offset``; ``merge_into`` maps an offset to an
    already replayed final group whose result row the group joins. It returns
    each offset's final group ID.
    """

    consume: Callable[
        [UntargetChildOutputs, Callable[[int], str], Mapping[int, str]],
        dict[int, str],
    ]
    finalize: Callable[[Path], Path]


@contextmanager
def prepare_alignment_explanation(
    path: Path,
    *,
    completed: CompletedUntargetBatchWorkflowResult,
    peaks_by_sample: Mapping[str, Sequence[DiscoveryPeak]],
    precursor_mz_by_candidate: Mapping[str, float],
    neutral_loss_by_tag: Mapping[str, float],
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> Iterator[PreparedAlignmentExplanation]:
    """Keep one bounded transaction while the TSV pass replays each child."""
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
        calibration=frame.calibration_for_snapshot if frame is not None else None,
        progress_callback=progress_callback,
    ) as replay:

        def consume_child(child: UntargetChildOutputs, first_group: int) -> None:
            replay.consume(
                child,
                lambda offset: feature_id_label(first_group + offset + 1),
                {},
            )

        yield PreparedAlignmentExplanation(consume_child, replay.finalize)


@contextmanager
def prepare_batch_alignment_explanation(
    path: Path,
    batches: Sequence[ExplanationBatch],
    *,
    calibration: BatchRtCalibration | None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> Iterator[BatchExplanationReplay]:
    """One report for independently aligned batches; the first is the reference.

    Each batch declares its own samples, candidates, Backfill support, exclusions
    and member risks; ``calibration`` is the run's replay calibration.
    """
    with ExplanationReportWriter(path) as sink:
        samples = {
            sample.raw_stem: sample
            for batch in batches
            for sample in batch.completed.application_result.canonical_samples
        }
        sink.declare_samples(
            [
                {
                    "sample_key": sample.sample_name,
                    "sample_stem": stem,
                    "injection_order": sample.injection_order,
                }
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

        emitted_support: set[CidNlMethodAssessment] = set()

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

        def emit_backfill_support(assessment: CidNlMethodAssessment) -> None:
            if (
                assessment.detector_invocation_id != GROUP_BACKFILL_DETECTOR
                or assessment.method_result != "SUPPORT"
                or assessment in emitted_support
            ):
                return
            sink.add_backfill_support(
                assessment.sample_key,
                assessment.method_subject.method_subject_id,
                tuple(locate(peak) for peak in assessment.candidate_peaks),
            )
            emitted_support.add(assessment)

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
                        method_subject_id=subject_id,
                    )

            for assessment in phase_items(
                "method_assessments", completed.method_assessments
            ):
                emit_backfill_support(assessment)

            for excluded in phase_items("exclusions", application.excluded_candidates):
                key = excluded.candidate_id, excluded.sample_key
                refs = (*excluded.method_assessments, *excluded.composite_assessments)
                sink.add_exclusion(*key, excluded.reason_code)
                for assessment in refs:
                    emit_backfill_support(assessment)

        child_count = 0
        child_total = sum(
            len(batch.completed.application_result.child_outputs) for batch in batches
        )
        report_progress("children", 0, child_total)

        def consume(
            child: UntargetChildOutputs,
            label: Callable[[int], str],
            merge_into: Mapping[int, str],
        ) -> dict[int, str]:
            nonlocal child_count
            merged: dict[str, str] = {}
            final_groups: dict[int, str] = {}
            for name, args, kwargs in cast(
                "tuple[_Write, ...]", child.explanation or ()
            ):
                if name == "locate":
                    add_located_peak(*args, **kwargs)
                elif name == "add_final_group":
                    offset = kwargs["feature_id"]
                    if offset in merge_into:
                        merged[args[0]] = merge_into[offset]
                        sink.extend_final_group(
                            merge_into[offset],
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
                    if name == "set_row_destination" and args[0] in merged:
                        continue
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
                    if not isinstance(group, CrossSamplePeakGroupHypothesis):
                        continue
                    risks = dict(group.member_risks)
                    sink.add_member_risks(
                        {
                            (member.sample_key, member.subject_id): asdict(
                                risks[member.subject_id]
                            )
                            for member in group.member_subjects
                            if member.subject_id in risks
                        }
                    )

            report_progress("finalizing", sink.record_count, None)
            with Path(matrix_path).open("rb") as handle:
                matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            summary = {
                "processing_attempt_id": batches[
                    0
                ].completed.application_result.processing_attempt_id,
                "matrix_path": matrix_path,
                "matrix_sha256": matrix_hash,
                "neutral_loss_by_tag": {
                    tag: loss
                    for batch in batches
                    for tag, loss in batch.neutral_loss_by_tag.items()
                },
                "sample_rt_calibration": rt_calibration_payload(calibration)
                if calibration is not None
                else None,
            }
            return sink.finalize(summary)

        yield BatchExplanationReplay(consume, finalize)
