from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack, closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.applications.untargeted.alignment_explanation_projection import (
    PreparedAlignmentExplanation,
    prepare_alignment_explanation,
)
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.output_levels import (
    EXPLANATION_ARTIFACT_NAME,
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
    output_paths,
    write_completed_untargeted_outputs_atomic,
)

if TYPE_CHECKING:
    from xic_extractor.untargeted_execution.execution_result import (
        CompletedUntargetBatchWorkflowResult,
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


def publish_untargeted_outputs(
    completed: CompletedUntargetBatchWorkflowResult,
    *,
    output_dir: Path,
    outputs: AlignmentRunOutputs,
    policy: UntargetedPublicationPolicy,
    metadata: dict[str, str],
    timing_recorder: TimingRecorder,
    should_cancel: Callable[[], bool] | None,
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
        membership_risks = {
            decision.owner_id: decision.accepted_risk
            for group in completed.initial_groups
            for decision in group.inference_decisions
            if decision.accepted_risk is not None
        }
        write_explanation = (
            any(risk.risk_flags for risk in membership_risks.values())
            or any(
                risk.risk_flags
                for child in completed.application_result.children
                for _, _, risk in (
                    child.scientific_core.grouping_result.backfill_membership_risks
                )
            )
            or policy.emit_alignment_explanation
            or policy.explanation_full_audit
            or bool(policy.explanation_curve_mz_ranges)
            or EXPLANATION_ARTIFACT_NAME
            in (artifact_names_for_output_level(policy.output_level))
        )
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
        write_completed_untargeted_outputs_atomic(
            outputs,
            completed.application_result,
            metadata=metadata,
            timing_recorder=timing_recorder,
            consume_child=_explanation_sink(explanation),
            membership_risks_by_owner=membership_risks,
            before_write=lambda: _ensure_output_publication_not_cancelled(
                should_cancel
            ),
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


def _ensure_output_publication_not_cancelled(
    should_cancel: Callable[[], bool] | None,
) -> None:
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Alignment cancelled before output publication")


def _explanation_sink(
    explanation: PreparedAlignmentExplanation | None,
) -> Callable[[Any], None] | None:
    """Consume children only when a report will be written from them.

    Without a report nothing needs the children after they compose, which is
    what lets the run release them instead of retaining every one.
    """
    return None if explanation is None else explanation.consume_child


def _rt_drift_summary(completed: CompletedUntargetBatchWorkflowResult) -> str:
    """Batch-level RT model facts for review: anchors, failures and fitted groups."""
    frame = completed.sample_rt_frame
    calibration = frame.calibration_for_snapshot if frame is not None else None
    drift = frame.drift_model if frame is not None else None
    coefficients = [
        group.inference_basis.rt_drift
        for group in completed.initial_groups
        if group.inference_basis is not None
    ]
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
                    "sample_shift_member": anchor.sample_shift_member,
                }
                for anchor in (drift.anchors if drift else ())
            ],
            "sample_shift_unknown_samples": sorted(
                set(frame.sample_universe or frame.injection_order)
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
            "nonzero_coefficient_groups": {
                "time_only": sum(
                    c.time != 0 and c.sample_shift == 0 for c in coefficients
                ),
                "sample_shift_only": sum(
                    c.time == 0 and c.sample_shift != 0 for c in coefficients
                ),
                "both": sum(c.time != 0 and c.sample_shift != 0 for c in coefficients),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
