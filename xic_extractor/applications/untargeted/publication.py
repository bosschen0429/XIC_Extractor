from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
from xic_extractor.alignment.backfill_peak_fact_provenance import (
    EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.discovery_input_source import DiscoveryInputSource
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.output_levels import (
    EXPLANATION_ARTIFACT_NAME,
    AlignmentOutputLevel,
    artifact_names_for_output_level,
)
from xic_extractor.alignment.owner_build_route_receipt import OwnerBuildRouteReceipt
from xic_extractor.alignment.pipeline_outputs import (
    AlignmentRunOutputs,
    supports_streamed_untargeted_outputs,
    write_outputs_atomic,
    write_streamed_untargeted_outputs_atomic,
)
from xic_extractor.alignment.untargeted_finalized_matrix import (
    completed_untarget_batch_sample_order,
    iter_completed_untarget_batch_writer_inputs,
    project_completed_untarget_batch_writer_inputs,
)
from xic_extractor.diagnostics.alignment_explanation import EvidenceLocator
from xic_extractor.diagnostics.alignment_explanation_projection import (
    PreparedAlignmentExplanation,
    prepare_alignment_explanation,
)
from xic_extractor.diagnostics.timing import TimingRecorder
from xic_extractor.discovery.models import DiscoveryCandidate

if TYPE_CHECKING:
    from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult


def publish_untargeted_outputs(
    completed: CompletedUntargetBatchWorkflowResult,
    *,
    output_dir: Path,
    outputs: AlignmentRunOutputs,
    candidates_by_sample: Mapping[str, tuple[DiscoveryCandidate, ...]],
    alignment_config: AlignmentConfig,
    output_level: AlignmentOutputLevel,
    emit_cid_nl_explanation: bool,
    known_istd_reference: KnownIstdReference | None,
    metadata: dict[str, str],
    timing_recorder: TimingRecorder,
    capture_artifact_fingerprints: bool,
    should_cancel: Callable[[], bool] | None,
    owner_build_route_receipt: OwnerBuildRouteReceipt,
) -> AlignmentRunOutputs:
    """Publish completed owner results and close their private stores on every exit."""
    with ExitStack() as output_stack:
        if hasattr(completed, "close"):
            output_stack.callback(completed.close)
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Alignment cancelled before output publication")
        matrix_path = outputs.matrix_tsv
        write_explanation = emit_cid_nl_explanation or EXPLANATION_ARTIFACT_NAME in (
            artifact_names_for_output_level(output_level)
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
                        candidates_by_sample=(
                            candidates_by_sample.feature_view()
                            if isinstance(candidates_by_sample, DiscoveryInputSource)
                            else candidates_by_sample
                        ),
                        alignment_config=alignment_config,
                        known_istd_reference_provenance=(
                            None
                            if known_istd_reference is None
                            else known_istd_reference.explanation_provenance
                        ),
                        progress_callback=timing_recorder.heartbeat_event,
                    )
                )
        stream_outputs = supports_streamed_untargeted_outputs(outputs)
        ms1_apex_mz_by_candidate = (
            candidates_by_sample.ms1_apex_mz_by_candidate
            if isinstance(candidates_by_sample, DiscoveryInputSource)
            else {
                candidate.candidate_id: candidate.ms1_apex_mz
                for candidates in candidates_by_sample.values()
                for candidate in candidates
                if candidate.ms1_apex_mz is not None
            }
        )
        writer_inputs = None
        if not stream_outputs:
            with timing_recorder.stage("alignment.project_outputs"):
                writer_inputs = project_completed_untarget_batch_writer_inputs(
                    completed,
                    consume_child=_explanation_sink(explanation),
                    ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
                )
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Alignment cancelled before output publication")
        with timing_recorder.stage("alignment.write_outputs"):
            if stream_outputs:
                writer_row_count, artifact_fingerprint_batch = (
                    write_streamed_untargeted_outputs_atomic(
                        outputs,
                        iter_completed_untarget_batch_writer_inputs(
                            completed,
                            consume_child=_explanation_sink(explanation),
                            ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
                        ),
                        sample_order=completed_untarget_batch_sample_order(completed),
                        metadata=metadata,
                        alignment_config=alignment_config,
                        timing_recorder=timing_recorder,
                        capture_artifact_fingerprints=capture_artifact_fingerprints,
                    )
                )
            else:
                assert writer_inputs is not None
                writer_row_count = len(writer_inputs.matrix.clusters)
                artifact_fingerprint_batch = write_outputs_atomic(
                    outputs,
                    writer_inputs.matrix,
                    metadata=metadata,
                    alignment_config=alignment_config,
                    backfill_peak_fact_provenance=EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH,
                    production_decisions=writer_inputs.production_decisions,
                    timing_recorder=timing_recorder,
                    capture_artifact_fingerprints=capture_artifact_fingerprints,
                )
        if capture_artifact_fingerprints:
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

            app = getattr(completed, "application_result", None)
            req = getattr(app, "processing_request", None)
            att = getattr(app, "processing_attempt", None)
            evidence = EvidenceLocator(
                report_path=db_path,
                matrix_path=outputs.matrix_tsv,
                matrix_sha256=matrix_sha256,
                processing_request_id=getattr(req, "processing_request_id", ""),
                processing_attempt_id=getattr(att, "processing_attempt_id", ""),
                schema_version="alignment_explanation",
            )

        return replace(
            outputs,
            owner_build_route_receipt=owner_build_route_receipt,
            evidence=evidence,
        )


def _explanation_sink(
    explanation: PreparedAlignmentExplanation | None,
) -> Callable[[Any], None] | None:
    """Consume children only when a report will be written from them.

    Without a report nothing needs the children after they compose, which is
    what lets the run release them instead of retaining every one.
    """
    return None if explanation is None else explanation.consume_child
