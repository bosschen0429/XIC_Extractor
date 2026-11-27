from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack, closing
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from xic_extractor.alignment.batch_correspondence import (
    BatchCorrespondence,
    BatchFeature,
    compose_calibrations,
)
from xic_extractor.applications.untargeted.alignment_explanation_projection import (
    ExplanationBatch,
    PreparedAlignmentExplanation,
    prepare_alignment_explanation,
    prepare_batch_alignment_explanation,
    project_alignment_explanation_child,
)
from xic_extractor.diagnostics.matrix_replay import save_matrix_cells, save_matrix_join
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.batch_correspondence_table import (
    BATCH_CORRESPONDENCE_ARTIFACT_NAME,
    batch_correspondence_summary,
    write_batch_correspondence_table,
)
from xic_extractor.output.alignment.output_levels import (
    EXPLANATION_ARTIFACT_NAME,
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
    output_paths,
    row_output_paths,
    write_completed_untargeted_outputs_atomic,
)
from xic_extractor.output.alignment.tsv_writer import (
    BatchRowSource,
    UntargetChildOutputs,
    feature_key_by_peaks,
    project_untarget_child_rows,
    untarget_sample_order,
    write_batch_alignment_tsvs,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
    from xic_extractor.untargeted_execution.execution_result import (
        CompletedUntargetBatchWorkflowResult,
    )
    from xic_extractor.untargeted_execution.partition_composition import (
        ChildOutputProjection,
        ChildProjectionContext,
    )


@dataclass(frozen=True, slots=True)
class UntargetedPublicationPolicy:
    """Public Matrix artifacts and optional explanation projections."""

    output_level: AlignmentOutputLevel = "production"
    emit_alignment_cells: bool = False
    emit_alignment_status_matrix: bool = False
    emit_alignment_explanation: bool = False
    explanation_full_audit: bool = False
    explanation_curve_mz_ranges: tuple[tuple[float, float], ...] = ()

    def output_paths(self, output_dir: Path) -> AlignmentRunOutputs:
        return output_paths(
            output_dir,
            output_level=self.output_level,
            emit_alignment_cells=self.emit_alignment_cells,
            emit_alignment_status_matrix=self.emit_alignment_status_matrix,
        )


def untargeted_child_projection(
    outputs: AlignmentRunOutputs,
    policy: UntargetedPublicationPolicy,
) -> ChildOutputProjection:
    """Project each composed child into the requested TSV rows and its review
    database writes.

    Whether the database is written is known only once every Backfill risk
    is, so every child records its writes; publication drops them otherwise.
    """
    row_names = tuple(row_output_paths(outputs))

    def project(
        child: FinalizedUntargetApplicationResult, context: ChildProjectionContext
    ) -> UntargetChildOutputs:
        return UntargetChildOutputs(
            groups=project_untarget_child_rows(
                child,
                outputs=row_names,
                sample_order=untarget_sample_order(context.samples),
                membership_risks_by_owner=context.membership_risks_by_owner,
            ),
            explanation=project_alignment_explanation_child(
                child,
                sample_rt_frame=context.sample_rt_frame,
                capture_evidence_retained=context.capture_evidence_retained,
                full_audit=policy.explanation_full_audit,
                curve_mz_ranges=policy.explanation_curve_mz_ranges,
            ),
        )

    return project


def publish_untargeted_outputs(
    completed: CompletedUntargetBatchWorkflowResult,
    *,
    output_dir: Path,
    outputs: AlignmentRunOutputs,
    policy: UntargetedPublicationPolicy,
    metadata: dict[str, str],
    timing_recorder: TimingRecorder,
    should_cancel: Callable[[], bool] | None,
    save_matrix_inputs: bool = False,
) -> AlignmentRunOutputs:
    """Publish completed owner results and close their private stores on every exit."""
    with closing(completed), ExitStack() as output_stack:
        _ensure_output_publication_not_cancelled(should_cancel)
        metadata = {
            **metadata,
            "processing_attempt_id": completed.processing_attempt_id,
            "rt_drift_summary": _rt_drift_summary(completed),
        }
        inputs = completed.publication_inputs
        matrix_path = outputs.matrix_tsv
        write_explanation = _explanation_required((completed,), policy)
        if write_explanation and matrix_path is None:
            raise RuntimeError("Alignment explanation requires the published Matrix")
        output_dir.mkdir(parents=True, exist_ok=True)
        explanation = None
        if write_explanation:
            with timing_recorder.stage("alignment.prepare_explanation"):
                explanation = output_stack.enter_context(
                    prepare_alignment_explanation(
                        output_dir / EXPLANATION_ARTIFACT_NAME,
                        completed=completed,
                        peaks_by_sample=inputs.peaks_by_sample,
                        alignment_config=inputs.alignment_config,
                        ms1_apex_mz_by_candidate=inputs.ms1_apex_mz_by_candidate,
                        precursor_mz_by_candidate=inputs.precursor_mz_by_candidate,
                        neutral_loss_by_tag=inputs.neutral_loss_by_tag,
                        progress_callback=timing_recorder.heartbeat_event,
                        full_audit=policy.explanation_full_audit,
                        curve_mz_ranges=policy.explanation_curve_mz_ranges,
                    )
                )
        frame = completed.sample_rt_frame
        extra_tables = {}
        if save_matrix_inputs:
            result = completed.application_result
            batch_id = result.canonical_samples[0].batch_id
            source = BatchRowSource(
                batch_id,
                result.canonical_samples,
                result.child_outputs,
                feature_key_by_peaks(completed.initial_groups)
                if batch_id
                else lambda _: None,
            )
            extra_tables[output_dir / "matrix-cells.pickle.gz"] = lambda path: (
                save_matrix_cells(path, (source,))
            )
        write_completed_untargeted_outputs_atomic(
            outputs,
            completed.application_result,
            metadata=metadata,
            rt_calibration=(
                frame.calibration_for_snapshot if frame is not None else None
            ),
            timing_recorder=timing_recorder,
            consume_child=_explanation_sink(explanation),
            before_write=lambda: _ensure_output_publication_not_cancelled(
                should_cancel
            ),
            extra_tables=extra_tables,
        )
        db_path: Path | None = None
        if explanation is not None:
            assert matrix_path is not None
            with timing_recorder.stage("alignment.write_alignment_explanation"):
                db_path = explanation.finalize(matrix_path)
        evidence: EvidenceLocator | None = None
        if (
            db_path is not None
            and outputs.matrix_tsv is not None
            and (outputs.matrix_tsv.is_file())
        ):
            evidence = EvidenceLocator(
                report_path=db_path,
                matrix_path=outputs.matrix_tsv,
                processing_attempt_id=completed.processing_attempt_id,
            )

        return replace(
            outputs,
            evidence=evidence,
        )


def _explanation_required(
    completed: Iterable[CompletedUntargetBatchWorkflowResult],
    policy: UntargetedPublicationPolicy,
) -> bool:
    """A flagged membership or Backfill risk, or the requested outputs."""
    return (
        any(
            item.application_result.has_flagged_membership_risk
            or item.application_result.has_flagged_backfill_risk
            for item in completed
        )
        or policy.emit_alignment_explanation
        or policy.explanation_full_audit
        or bool(policy.explanation_curve_mz_ranges)
        or EXPLANATION_ARTIFACT_NAME
        in (artifact_names_for_output_level(policy.output_level))
    )


def _ensure_output_publication_not_cancelled(
    should_cancel: Callable[[], bool] | None,
) -> None:
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Alignment cancelled before output publication")


def _explanation_sink(
    explanation: PreparedAlignmentExplanation | None,
) -> Callable[[UntargetChildOutputs, int], None] | None:
    """Replay children's recorded writes only when a report is written."""
    return None if explanation is None else explanation.consume_child


def _rt_drift_summary(completed: CompletedUntargetBatchWorkflowResult) -> str:
    """Batch-level RT model facts for review: anchors, failures and fitted groups."""
    frame = completed.sample_rt_frame
    calibration = frame.calibration_for_snapshot if frame is not None else None
    drift = frame.drift_model if frame is not None else None
    knots_by_segment: Counter[str] = Counter()
    for knot in calibration.knots if calibration is not None else ():
        start = int(knot.reference_rt_min // 4) * 4
        knots_by_segment[f"{start}-{start + 4}"] += 1
    return json.dumps(
        {
            "batch_state_basis": drift.batch_state_basis if drift else "none",
            "drift_anchors": [
                {
                    "channel": anchor.channel,
                    "reference_rt_min": anchor.reference_rt_min,
                    "family": anchor.family,
                }
                for anchor in (drift.anchors if drift else ())
            ],
            "sample_shift_unknown_samples": sorted(
                set(frame.sample_universe or frame.injections.stems)
                - set(drift.sample_shift_min)
            )
            if drift is not None and frame is not None
            else [],
            "calibration_knots_by_rt_min": dict(
                sorted(
                    knots_by_segment.items(),
                    key=lambda item: float(item[0].split("-")[0]),
                )
            ),
            "calibration_failed_samples": {
                sample: fitted.status
                for sample, fitted in sorted(
                    (calibration.samples if calibration is not None else {}).items()
                )
                if fitted.status != "calibrated"
            },
            "nonzero_coefficient_groups": asdict(
                completed.application_result.rt_drift_groups
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def publish_batch_untargeted_outputs(
    completed_by_batch: Mapping[str, CompletedUntargetBatchWorkflowResult],
    correspondence: BatchCorrespondence,
    features: Sequence[BatchFeature],
    *,
    output_dir: Path,
    outputs: AlignmentRunOutputs,
    policy: UntargetedPublicationPolicy,
    metadata: dict[str, str],
    timing_recorder: TimingRecorder,
    should_cancel: Callable[[], bool] | None,
    save_matrix_inputs: bool = False,
) -> AlignmentRunOutputs:
    """Publish independently aligned batches as one Matrix and its reports.

    Each batch keeps its own groups, cells and RT model; the correspondence
    decides which rows share a Matrix row. In the explanation a paired row is
    the reference batch's group, joined by its partners' members and cells.
    """
    with ExitStack() as stack:
        for completed in completed_by_batch.values():
            stack.enter_context(closing(completed))
        _ensure_output_publication_not_cancelled(should_cancel)
        reference_batch = correspondence.reference_batch
        reference = completed_by_batch[reference_batch]
        metadata = {
            **metadata,
            "processing_attempt_id": reference.processing_attempt_id,
            "rt_drift_summary": json.dumps(
                {
                    batch_id: json.loads(_rt_drift_summary(completed))
                    for batch_id, completed in sorted(completed_by_batch.items())
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
            "batch_correspondence": json.dumps(
                batch_correspondence_summary(correspondence),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
        }
        matrix_path = outputs.matrix_tsv
        write_explanation = _explanation_required(completed_by_batch.values(), policy)
        if write_explanation and matrix_path is None:
            raise RuntimeError("Alignment explanation requires the published Matrix")
        output_dir.mkdir(parents=True, exist_ok=True)
        decisions = {
            (decision.batch_id, decision.key): decision
            for decision in correspondence.features
        }
        batch_order = sorted(
            completed_by_batch,
            key=lambda batch_id: (batch_id != reference_batch, batch_id),
        )
        sources = [
            BatchRowSource(
                batch_id,
                completed_by_batch[batch_id].application_result.canonical_samples,
                completed_by_batch[batch_id].application_result.child_outputs,
                feature_key_by_peaks(completed_by_batch[batch_id].initial_groups),
            )
            for batch_id in batch_order
        ]
        calibration = compose_calibrations(
            {
                batch_id: completed.sample_rt_frame.calibration_for_snapshot
                if completed.sample_rt_frame is not None
                else None
                for batch_id, completed in completed_by_batch.items()
            },
            correspondence,
        )
        labels: dict[tuple[str, int, int], str] = {}
        joins: dict[tuple[str, int, int], tuple[str, int, int]] = {}

        def record_row(feature_id: str, places: Sequence[tuple[str, int, int]]) -> None:
            labels[places[0]] = feature_id
            for place in places[1:]:
                joins[place] = places[0]

        def render_rows(paths: Mapping[str, Path]) -> int:
            return write_batch_alignment_tsvs(
                paths,
                sources,
                reference_batch=reference_batch,
                partners={key: item.partners for key, item in decisions.items()},
                states={key: item.state for key, item in decisions.items()},
                to_reference_rt=correspondence.to_reference_rt,
                consume_row=record_row if write_explanation else None,
            )

        extra_tables: dict[Path, Callable[[Path], None]] = {
            output_dir / BATCH_CORRESPONDENCE_ARTIFACT_NAME: lambda path: (
                write_batch_correspondence_table(path, correspondence, features)
            )
        }
        if save_matrix_inputs:
            extra_tables[output_dir / "matrix-cells.pickle.gz"] = lambda path: (
                save_matrix_cells(path, sources)
            )
            extra_tables[output_dir / "matrix-join.pickle.gz"] = lambda path: (
                save_matrix_join(path, correspondence)
            )
        write_completed_untargeted_outputs_atomic(
            outputs,
            None,
            metadata=metadata,
            rt_calibration=calibration,
            timing_recorder=timing_recorder,
            before_write=lambda: _ensure_output_publication_not_cancelled(
                should_cancel
            ),
            render_rows=render_rows,
            extra_tables=extra_tables,
        )
        evidence: EvidenceLocator | None = None
        if write_explanation:
            assert matrix_path is not None
            with timing_recorder.stage("alignment.write_alignment_explanation"):
                db_path = _write_batch_explanation(
                    output_dir / EXPLANATION_ARTIFACT_NAME,
                    [
                        (batch_id, completed_by_batch[batch_id])
                        for batch_id in batch_order
                    ],
                    labels,
                    joins,
                    calibration=calibration,
                    matrix_path=matrix_path,
                    policy=policy,
                    progress_callback=timing_recorder.heartbeat_event,
                )
            if matrix_path.is_file():
                evidence = EvidenceLocator(
                    report_path=db_path,
                    matrix_path=matrix_path,
                    processing_attempt_id=reference.processing_attempt_id,
                )
        return replace(outputs, evidence=evidence)


def _write_batch_explanation(
    path: Path,
    batches: Sequence[tuple[str, CompletedUntargetBatchWorkflowResult]],
    labels: Mapping[tuple[str, int, int], str],
    joins: Mapping[tuple[str, int, int], tuple[str, int, int]],
    *,
    calibration: BatchRtCalibration | None,
    matrix_path: Path,
    policy: UntargetedPublicationPolicy,
    progress_callback: Callable[[Mapping[str, object]], None] | None,
) -> Path:
    """Replay every batch's children, the reference batch's first.

    ``labels`` names each written row by its first group's place; ``joins``
    sends a partner's place to the place whose row it joins.
    """
    inputs = [completed.publication_inputs for _batch_id, completed in batches]
    with prepare_batch_alignment_explanation(
        path,
        [
            ExplanationBatch(
                completed,
                item.peaks_by_sample,
                item.precursor_mz_by_candidate,
                item.neutral_loss_by_tag,
                item.ms1_apex_mz_by_candidate,
            )
            for (_batch_id, completed), item in zip(batches, inputs, strict=True)
        ],
        alignment_config=inputs[0].alignment_config,
        calibration=calibration,
        progress_callback=progress_callback,
        full_audit=policy.explanation_full_audit,
        curve_mz_ranges=policy.explanation_curve_mz_ranges,
    ) as replay:
        joins_by_child: dict[tuple[str, int], dict[int, tuple[str, int, int]]] = {}
        for (batch_id, child_index, offset), owner in joins.items():
            joins_by_child.setdefault((batch_id, child_index), {})[offset] = owner
        final_groups: dict[tuple[str, int, int], str] = {}
        for index, (batch_id, completed) in enumerate(batches):
            children = completed.application_result.child_outputs
            for child_index, child in enumerate(children):

                def label(
                    offset: int, at: tuple[str, int] = (batch_id, child_index)
                ) -> str:
                    return labels[(*at, offset)]

                written = replay.consume(
                    cast(UntargetChildOutputs, child),
                    index,
                    label,
                    {
                        offset: final_groups[owner]
                        for offset, owner in joins_by_child.get(
                            (batch_id, child_index), {}
                        ).items()
                    },
                )
                final_groups.update(
                    ((batch_id, child_index, offset), group_id)
                    for offset, group_id in written.items()
                )
        return replay.finalize(matrix_path)
