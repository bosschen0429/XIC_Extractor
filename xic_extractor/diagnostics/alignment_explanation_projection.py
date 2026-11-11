"""Project existing CID-NL owners into diagnostic facts without deciding science."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence, Sized
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.owner_group_delivery import feature_id_label
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.alignment_explanation.review import review_cell
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.rt_calibration_codec import rt_calibration_payload

if TYPE_CHECKING:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.discovery.models import DiscoveryPeak
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlMethodAssessment,
    )
    from xic_extractor.untargeted_execution.execution_result import (
        CompletedUntargetBatchWorkflowResult,
    )


# Persisted summary labels of the Untargeted CID-NL run contract.
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
    sink: ExplanationReportWriter,
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


@dataclass(frozen=True)
class PreparedAlignmentExplanation:
    """Consume owner results now; publish only after the Matrix is available."""

    consume_child: Callable[[FinalizedUntargetApplicationResult], None]
    finalize: Callable[[Path], Path]


def write_alignment_explanation(
    path: Path,
    *,
    completed: CompletedUntargetBatchWorkflowResult,
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
    """Persist exact owner identities, reasons and Matrix publication provenance."""
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
        for child in completed.application_result.children:
            explanation.consume_child(child)
        return explanation.finalize(matrix_path)


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
    """Keep one bounded transaction while outputs share the loaded children."""
    measured_mz = ms1_apex_mz_by_candidate or {}
    with ExplanationReportWriter(
        path,
        review_only=not full_audit,
        curve_mz_ranges=curve_mz_ranges,
    ) as sink:
        application = completed.application_result
        subjects = application.method_subjects
        memberships = dict(subjects.candidate_memberships)
        samples = {sample.raw_stem: sample for sample in application.canonical_samples}
        sink.declare_samples(
            [
                {"sample_key": sample.sample_name, "sample_stem": stem}
                for stem, sample in samples.items()
            ]
        )
        located_peaks: dict[str, Any] = {}

        def locate(content: Any) -> str:
            """Written locator of one physical peak; never shared by two peaks.

            A peak is its selected raw signal. Reads of it in other XIC windows
            can integrate differently; each cell keeps its own value and curve.
            """
            key: str = content.physical_peak_key
            signal = (content.sample_key, content.mz, content.rt, content.intensity)
            if located_peaks.setdefault(key, signal) != signal:
                raise ValueError(f"distinct physical peaks share one locator: {key}")
            sink.add_peak(
                content.sample_key,
                key,
                (
                    content.mz,
                    content.integration.rt_apex_min,
                    content.generation_provenance.query_ppm_tol,
                ),
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

        for stem, declared in peaks_by_sample.items():
            sid = samples[stem].sample_name
            for candidate in phase_items("candidates", declared):
                key = candidate.peak_id, sid
                subject_id = memberships[candidate.peak_id]
                sink.add_candidate(
                    *key,
                    precursor_mz=precursor_mz_by_candidate[candidate.peak_id],
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

        def emit_assessment(assessment: CidNlMethodAssessment) -> None:
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

        for assessment in phase_items(
            "method_assessments", completed.method_assessments
        ):
            emit_assessment(assessment)

        for excluded in phase_items("exclusions", application.excluded_candidates):
            key = excluded.candidate_id, excluded.sample_key
            refs = (*excluded.method_assessments, *excluded.composite_assessments)
            sink.add_exclusion(*key, excluded.reason_code)
            for assessment in refs:
                emit_assessment(assessment)

        def emit_group(
            stage: str,
            group: Any,
            feature_id: str | None = None,
            position: tuple[float, float] | None = None,
        ) -> None:
            members = [
                {
                    **_fields(member, "sample_key", "subject_kind", "subject_id"),
                    "physical_peak_keys": [
                        locate(peak) for peak in member.physical_peaks
                    ],
                }
                for member in group.member_subjects
            ]
            if stage == "final_groups":
                sink.add_final_group(
                    group.group_hypothesis_id,
                    feature_id=feature_id,
                    disposition=group.disposition,
                    members=members,
                    center_mz=position[0] if position else None,
                    ppm=position[1] if position else None,
                )
                return
            inference_basis = (
                group.inference_basis
                if isinstance(group, CrossSamplePeakGroupHypothesis)
                else None
            )
            sink.add_initial_group(
                disposition=group.disposition,
                members=members,
                inference_basis=None
                if inference_basis is None
                else asdict(inference_basis),
                inference_decisions=None
                if inference_basis is None
                else [asdict(decision) for decision in group.inference_decisions],
            )

        child_count = 0
        row_count = 0
        matrix_row_count = 0
        report_progress("children", 0, len(application.children))

        def consume_child(child: FinalizedUntargetApplicationResult) -> None:
            nonlocal child_count, row_count, matrix_row_count
            core = child.scientific_core
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
                if not capture_evidence:
                    _emit_curve_payload(
                        sink,
                        ref,
                        sample_id=key[1],
                        kind=ref.capture_stage,
                    )

            # Same child and row order as the TSV writer that labels rows.
            feature_ids = {}
            for group, _cells in child.group_cells:
                row_count += 1
                matrix_row_count += group.destination == "matrix"
                feature_ids[group.group_hypothesis_id] = feature_id_label(row_count)
            # Recorded group position of each empty cell, and the group centre
            # and query ppm used to inspect it.
            positions: dict[tuple[str, str], float] = {}
            centers: dict[str, tuple[float, float]] = {}
            context_by_group = {
                cell.group_hypothesis_id: cell.effective_cells[0].group_context
                for cell in core.cell_observations
                if cell.effective_cells
            }
            frame = completed.sample_rt_frame
            sample_stems = {
                sample.sample_name: sample.raw_stem for sample in samples.values()
            }
            group_positions = {}
            for group in core.final_population:
                peaks = [
                    peak
                    for member in group.member_subjects
                    for peak in member.physical_peaks
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
                        *(
                            hypothesis.peak
                            for hypothesis in final_cell.direct_hypotheses
                        ),
                    )
                }
                for ref in refs.values():
                    locate(ref)
                if refs:
                    continue
                # Empty cells remain inspectable at the recorded physical group
                # position. This is explicitly diagnostic, not an invented peak.
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
                emit_group(
                    "final_groups",
                    group,
                    feature_ids.get(group.group_hypothesis_id),
                    centers.get(group.group_hypothesis_id),
                )
            # Member risks are stored with the member; a cell keeps only the
            # Backfill risks no member owns.
            risks_by_group_sample: dict[tuple[str, str], list[dict[str, Any]]] = (
                defaultdict(list)
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
                        cell["observation"] = _final_cell_observation_payload(
                            decision.evidence
                        )
                sink.add_cells(group_id, cells)
                for cell in cells:
                    cell_position = positions.get((group_id, cell["sample_key"]))
                    if cell_position is not None:
                        sink.set_cell_position(
                            group_id, cell["sample_key"], cell_position
                        )
            child_count += 1
            if child_count % 256 == 0:
                report_progress("children", child_count, len(application.children))

        def finalize(matrix_path: Path) -> Path:
            if child_count != len(application.children):
                raise ValueError("explanation did not consume every finalized child")
            if child_count % 256:
                report_progress("children", child_count, len(application.children))
            for group in phase_items("initial_groups", completed.initial_groups):
                emit_group("initial_groups", group)

            report_progress("finalizing", sink.record_count, None)
            with Path(matrix_path).open("rb") as handle:
                matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            summary: dict[str, Any] = {
                **_RUN_CONTRACT_SUMMARY,
                "processing_attempt_id": application.processing_attempt_id,
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
                        application.acquisition_sources,
                        key=lambda source: source.acquisition_source_id,
                    )
                ],
                "matrix_row_count": matrix_row_count,
                "matrix_path": str(matrix_path),
                "matrix_sha256": matrix_hash,
                "alignment_config": asdict(alignment_config),
                "neutral_loss_by_tag": dict(sorted(neutral_loss_by_tag.items())),
                "retention": "full_audit" if full_audit else "review",
                "selection_evidence_capability": "recorded_decisions",
                "curve_retention": "all"
                if full_audit
                else "selected"
                if curve_mz_ranges
                else "none",
                "curve_mz_ranges": list(curve_mz_ranges),
                "audit_availability": {
                    name: "recorded" if getattr(completed, name) else "unavailable"
                    for name in ("method_assessments", "initial_groups")
                },
            }
            frame = completed.sample_rt_frame
            calibration = frame.calibration_for_snapshot if frame is not None else None
            summary["sample_rt_calibration"] = (
                rt_calibration_payload(calibration) if calibration is not None else None
            )
            summary["injection_order"] = {
                stem: sample.injection_order
                for stem, sample in samples.items()
                if sample.injection_order is not None
            }
            return sink.finalize(summary)

        yield PreparedAlignmentExplanation(consume_child, finalize)


write_alignment_explanation = write_alignment_explanation
