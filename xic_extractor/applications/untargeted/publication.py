from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.alignment.owner_build_route_receipt import OwnerBuildRouteReceipt
from xic_extractor.diagnostics.alignment_explanation_projection import (
    PreparedAlignmentExplanation,
    prepare_alignment_explanation,
)
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.output.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
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
    """Public Matrix artifacts and optional explanation/fingerprint projections."""

    output_level: AlignmentOutputLevel = "production"
    emit_alignment_cells: bool = False
    emit_alignment_status_matrix: bool = False
    emit_cid_nl_explanation: bool = False
    explanation_full_audit: bool = False
    explanation_curve_mz_ranges: tuple[tuple[float, float], ...] = ()
    emit_alignment_backfill_seed_audit: bool = False
    capture_artifact_fingerprints: bool = False
    emit_provisional_matrix: bool = False

    def output_paths(self, output_dir: Path) -> AlignmentRunOutputs:
        return output_paths(
            output_dir,
            output_level=self.output_level,
            emit_alignment_cells=self.emit_alignment_cells,
            emit_alignment_status_matrix=self.emit_alignment_status_matrix,
            emit_alignment_backfill_seed_audit=self.emit_alignment_backfill_seed_audit,
            emit_provisional_matrix=self.emit_provisional_matrix,
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
    owner_build_route_receipt: OwnerBuildRouteReceipt,
) -> AlignmentRunOutputs:
    """Publish completed owner results and close their private stores on every exit."""
    with ExitStack() as output_stack:
        if hasattr(completed, "close"):
            output_stack.callback(completed.close)
        _ensure_output_publication_not_cancelled(should_cancel)
        inputs = completed.publication_inputs
        matrix_path = outputs.matrix_tsv
        write_explanation = (
            policy.emit_cid_nl_explanation
            or policy.explanation_full_audit
            or bool(policy.explanation_curve_mz_ranges)
            or EXPLANATION_ARTIFACT_NAME
            in (artifact_names_for_output_level(policy.output_level))
        )
        if write_explanation and matrix_path is None:
            raise RuntimeError("CID-NL explanation requires the published Matrix")
        output_dir.mkdir(parents=True, exist_ok=True)
        explanation = None
        if write_explanation:
            with timing_recorder.stage("alignment.prepare_explanation"):
                explanation = output_stack.enter_context(
                    prepare_alignment_explanation(
                        output_dir / EXPLANATION_ARTIFACT_NAME,
                        completed=completed,
                        candidates_by_sample=inputs.candidates_by_sample,
                        alignment_config=inputs.alignment_config,
                        known_istd_reference_provenance=inputs.known_istd_reference_provenance,
                        progress_callback=timing_recorder.heartbeat_event,
                        full_audit=policy.explanation_full_audit,
                        curve_mz_ranges=policy.explanation_curve_mz_ranges,
                    )
                )
        writer_row_count, artifact_fingerprint_batch = (
            write_completed_untargeted_outputs_atomic(
                outputs,
                completed.application_result,
                metadata=metadata,
                alignment_config=inputs.alignment_config,
                timing_recorder=timing_recorder,
                consume_child=_explanation_sink(explanation),
                ms1_apex_mz_by_candidate=inputs.ms1_apex_mz_by_candidate,
                before_write=lambda: _ensure_output_publication_not_cancelled(
                    should_cancel
                ),
                capture_artifact_fingerprints=policy.capture_artifact_fingerprints,
            )
        )
        if policy.capture_artifact_fingerprints:
            if not isinstance(
                artifact_fingerprint_batch, AlignmentArtifactFingerprintBatch
            ):
                raise TypeError(
                    "Alignment writer must return an owner-minted fingerprint batch"
                )
            outputs = replace(
                outputs, artifact_fingerprint_batch=artifact_fingerprint_batch
            )
        db_path: Path | None = None
        if explanation is not None:
            assert matrix_path is not None
            with timing_recorder.stage("alignment.write_cid_nl_explanation"):
                db_path = explanation.finalize(writer_row_count, matrix_path)
        evidence: EvidenceLocator | None = None
        if (
            db_path is not None
            and outputs.matrix_tsv is not None
            and (outputs.matrix_tsv.is_file())
        ):
            with outputs.matrix_tsv.open("rb") as handle:
                matrix_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()

            evidence = EvidenceLocator(
                report_path=db_path,
                matrix_path=outputs.matrix_tsv,
                matrix_sha256=matrix_sha256,
                processing_request_id=completed.processing_request_id,
                processing_attempt_id=completed.processing_attempt_id,
                schema_version="alignment_explanation"
                if policy.explanation_full_audit
                else "alignment_explanation_review",
            )

        return replace(
            outputs,
            owner_build_route_receipt=owner_build_route_receipt,
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
