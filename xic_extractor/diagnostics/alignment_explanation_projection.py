"""Project existing CID-NL owners into diagnostic facts without deciding science."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence, Sized
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.alignment.cross_sample_neighborhood_graph import (
    INITIAL_GROUPING_POLICY,
    UNLISTED_PAIR_DISPOSITION,
)
from xic_extractor.diagnostics.alignment_explanation import ExplanationReportWriter
from xic_extractor.peak_detection.cid_nl_method_subject import (
    cid_nl_method_subject_batch_from_request,
)
from xic_extractor.peak_detection.traces import Trace

if TYPE_CHECKING:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.alignment.untargeted_finalized_matrix import (
        CompletedUntargetBatchWriterInputs,
    )
    from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
    from xic_extractor.discovery.models import DiscoveryCandidate
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlMethodAssessmentRef,
    )


def _fields(value: Any, *names: str) -> dict[str, Any]:
    return {name: getattr(value, name) for name in names}


def _artifact(value: Any) -> dict[str, Any]:
    return {
        **_fields(
            value,
            "evidence_artifact_id",
            "producer_processing_request_id",
            "producer_processing_attempt_id",
            "upstream_artifact_ids",
        ),
        "content_identity": asdict(value.content_identity),
        "semantic_contract": asdict(value.semantic_contract),
    }


def _cell_observation_content_payload(content: Any) -> dict[str, Any]:
    return {
        "canonical_sample_identity_id": content.canonical_sample_identity_id,
        "group_hypothesis_id": content.group_hypothesis_id,
        "group_owner_ids": list(content.group_owner_ids),
        "group_method_subject_id": content.group_method_subject_id,
        "group_target_key_id": content.group_target_key_id,
        "group_target_definition_id": content.group_target_definition_id,
        "group_support_assessment_artifact_content_ids": list(
            content.group_support_assessment_artifact_content_ids
        ),
        "technical_state": content.technical_state,
        "method_result": content.method_result,
        "peak_observation_content_id": content.peak_observation_content_id,
        "peak_hypothesis_id": content.peak_hypothesis_id,
        "method_assessment_artifact_content_id": (
            content.method_assessment_artifact_content_id
        ),
        "evidence_artifact_ids": list(content.evidence_artifact_ids),
        "reason_codes": list(content.reason_codes),
        "human_disposition_ref_id": content.human_disposition_ref_id,
        "outside_group": (
            asdict(content.outside_group) if content.outside_group is not None else None
        ),
    }


def _key(*parts: str) -> str:
    return json.dumps(parts, separators=(",", ":"))


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
    related: Iterable[tuple[str, str]] = (),
) -> None:
    """Serialize retained owner values; missing processing remains unavailable."""
    content = ref.content if ref is not None else None
    if evaluation is None and ref is not None:
        evaluation = ref.physical_evaluation
    prepared = evaluation.retained_prepared_signal if evaluation is not None else None
    if trace is None and content is None:
        return
    generation = ref.generation_provenance if ref is not None else None
    actual = dict(provenance or {})
    if evaluation is not None:
        actual.update(
            trace_content_id=evaluation.question.trace_content_id,
            prepared_signal_id=evaluation.prepared.prepared_signal_id,
            profile_id=evaluation.prepared.profile_id,
            physical_state=evaluation.state,
            physical_reason=evaluation.reason,
        )
    if prepared is not None:
        actual.update(
            profile_id=prepared.profile.profile_id,
            profile_version=prepared.profile.schema_version,
            profile=asdict(prepared.profile),
        )
    if generation is not None:
        actual.update(
            _fields(
                generation,
                "full_trace_sha256",
                "selection_trace_sha256",
                "acquisition_source_id",
                "selected_trace_indices",
                "resolver_mode",
                "proposal_sources",
            )
        )
        actual.update(
            observation_artifact_id=ref.artifact.evidence_artifact_id,
            processing_request_id=ref.artifact.producer_processing_request_id,
            processing_attempt_id=ref.artifact.producer_processing_attempt_id,
        )
    elif trace is not None:
        actual["acquisition_source_id"] = trace.acquisition_source_id
    obs_id = content.peak_observation_content_id if content is not None else None
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
        "peak_observation_content_id": obs_id,
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
    # One envelope per actual content/binding; candidate associations are links.
    payload_id = sink.add_curve_payload(
        payload_id=None,
        sample_id=sample_id,
        kind=kind,
        record_id=record_id,
        payload=envelope,
    )
    sink.add_record(
        "capture_evidence",
        payload_id,
        {
            "payload_id": payload_id,
            "sample_id": sample_id,
            "candidate_id": candidate_id,
            "peak_observation_content_id": obs_id,
            "record_id": record_id,
            "kind": kind,
        },
        related,
    )


@dataclass(frozen=True)
class PreparedAlignmentExplanation:
    """Consume owner results now; publish only after the Matrix is available."""

    consume_child: Callable[[FinalizedUntargetApplicationResult], None]
    finalize: Callable[[int, Path], Path]


def write_cid_nl_explanation(
    path: Path,
    *,
    completed: CompletedUntargetBatchWorkflowResult,
    writer_inputs: CompletedUntargetBatchWriterInputs,
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    matrix_path: Path,
    alignment_config: AlignmentConfig,
    known_istd_reference_provenance: Mapping[str, object] | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> Path:
    """Persist exact owner identities, reasons and Matrix publication provenance."""
    with prepare_alignment_explanation(
        path,
        completed=completed,
        candidates_by_sample=candidates_by_sample,
        alignment_config=alignment_config,
        known_istd_reference_provenance=known_istd_reference_provenance,
        progress_callback=progress_callback,
    ) as explanation:
        for child in completed.application_result.children:
            explanation.consume_child(child)
        return explanation.finalize(len(writer_inputs.matrix.clusters), matrix_path)


@contextmanager
def prepare_alignment_explanation(
    path: Path,
    *,
    completed: CompletedUntargetBatchWorkflowResult,
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    alignment_config: AlignmentConfig,
    known_istd_reference_provenance: Mapping[str, object] | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> Iterator[PreparedAlignmentExplanation]:
    """Keep one bounded transaction while outputs share the loaded children."""
    with ExplanationReportWriter(path) as sink:
        application = completed.application_result
        request = application.processing_request
        request_id = request.processing_request_id
        subjects = cid_nl_method_subject_batch_from_request(request)
        memberships = dict(subjects.candidate_memberships)
        samples = {
            sample.metadata.raw_stem: sample for sample in application.canonical_samples
        }
        reasons: Counter[str] = Counter()
        subject_candidates: dict[str, set[tuple[str, str]]] = defaultdict(set)
        method_candidates: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(
            set
        )
        member_candidates: dict[str, set[tuple[str, str]]] = defaultdict(set)
        group_candidates: dict[str, set[tuple[str, str]]] = defaultdict(set)
        hypothesis_candidates: dict[tuple[str, str], set[tuple[str, str]]] = (
            defaultdict(set)
        )
        physical_candidates: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(
            set
        )

        assessment_bindings: dict[str, int] = {}

        def report_progress(stage: str, completed: int, total: int | None) -> None:
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "cid_nl_explanation_progress",
                        "stage": f"alignment.explanation.{stage}",
                        "completed": completed,
                        "total": total,
                        "records_written": sum(sink.record_counts.values()),
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

        def add(
            kind: str, identity: str, payload: dict[str, Any], related: Any
        ) -> None:
            if sink.add_record(kind, identity, payload, related):
                if kind == "method_assessments":
                    reasons.update(payload["reason_codes"])
                elif kind == "exclusions":
                    reasons.update((payload["reason_code"],))
                elif kind == "initial_edges":
                    reasons.update((payload["initial_evidence"]["reason"],))
                elif kind == "rows":
                    reasons.update((payload["route"]["reason"],))

        for stem, declared in candidates_by_sample.items():
            sid = samples[stem].identity.sample_identity_id
            for candidate in phase_items("candidates", declared):
                key = candidate.candidate_id, sid
                subject_id = memberships[candidate.candidate_id]
                method_candidates[subject_id, sid].add(key)
                subject_candidates[subject_id].add(key)
                sink.add_candidate(
                    *key,
                    {
                        **_fields(
                            candidate,
                            "candidate_id",
                            "discovery_candidate_state",
                            "precursor_mz",
                            "configured_neutral_loss_da",
                            "neutral_loss_tag",
                        ),
                        "sample_stem": stem,
                        "canonical_sample_identity_id": sid,
                        "method_subject_id": subject_id,
                        "availability": {
                            "method_assessments": "unavailable",
                            "terminal": "unavailable",
                            "initial_groups": "unavailable",
                            "final_groups": "unavailable",
                        },
                    },
                )

        for record in phase_items(
            "discovery_physical", getattr(completed, "discovery_physical_records", ())
        ):
            work = record.work_item
            sid = samples[work.sample_stem].identity.sample_identity_id
            related = {(link.candidate_id, sid) for link in record.candidate_links}
            if not related:
                related = {(record.candidate_id, sid)}
                sink.add_candidate(
                    record.candidate_id,
                    sid,
                    {
                        "candidate_id": record.candidate_id,
                        "canonical_sample_identity_id": sid,
                        "sample_stem": work.sample_stem,
                        "precursor_mz": work.precursor_mz,
                        "physical_state": record.evaluation.state,
                        "reason": record.evaluation.reason,
                        "rt_apex_min": (
                            record.evaluation.facts.apex_rt_min
                            if record.evaluation.facts is not None
                            else None
                        ),
                        "availability": {"terminal": "unavailable"},
                    },
                )
            _emit_curve_payload(
                sink,
                None,
                sid,
                candidate_id=record.candidate_id,
                kind="discovery_physical",
                trace=Trace(
                    sample_name=work.sample_stem,
                    mz=work.xic_mz,
                    rt=record.acquired_trace.trace.rt,
                    intensity=record.acquired_trace.trace.intensity,
                    rt_min=work.xic_rt_min,
                    rt_max=work.xic_rt_max,
                    ppm_tol=work.xic_ppm_tol,
                    acquisition_source_id=work.acquisition_source_id,
                ),
                evaluation=record.evaluation,
                provenance={
                    "processing_request_id": request_id,
                    "processing_attempt_id": (
                        application.processing_attempt.processing_attempt_id
                    ),
                },
                related=related,
            )

        capture_evidence = getattr(completed, "capture_evidence", ())
        for evidence in phase_items("capture_evidence", capture_evidence):
            sid = evidence.canonical_sample_identity_id
            related = (
                {(evidence.candidate_id, sid)}
                if evidence.candidate_id is not None
                else method_candidates[evidence.method_subject_id, sid]
            )
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
                    "processing_request_id",
                    "processing_attempt_id",
                    "method_subject_id",
                ),
                related=related,
            )

        for subject in phase_items("method_subjects", subjects.subjects):
            related = subject_candidates[subject.method_subject_id]
            add(
                "method_subjects",
                subject.method_subject_id,
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
                },
                related,
            )

        def emit_assessment(assessment: CidNlMethodAssessmentRef) -> None:
            identity = assessment.artifact.evidence_artifact_id
            binding_key = (
                assessment.artifact.evidence_artifact_id,
                tuple(
                    ref.artifact.evidence_artifact_id
                    for ref in assessment.evidence_refs
                ),
            )
            binding_hash = hash(binding_key)
            if identity in assessment_bindings:
                if assessment_bindings[identity] != binding_hash:
                    raise ValueError(
                        "conflicting diagnostic owner identity: "
                        f"{('method_assessments', identity)}"
                    )
                return
            # The typed assessment owner already verified content against its
            # immutable artifact. Retain only its diagnostic identity bindings,
            # not the assessment's peak/evidence graph or another projection.
            content = assessment.content
            related = method_candidates[
                content.method_subject.method_subject_id,
                content.canonical_sample_identity_id,
            ]
            if (
                content.detector_invocation_id == "untargeted-cid-nl-backfill"
                and content.method_result == "SUPPORT"
            ):
                # Backfill has no Discovery terminal. Retain its exact physical
                # support links so the original candidate can explain the cell.
                for relation in assessment.individual_support_refs:
                    physical_candidates[
                        content.canonical_sample_identity_id,
                        relation.content.peak_observation_content_id,
                    ].update(related)
                if not capture_evidence:
                    for peak in assessment.candidate_peaks:
                        _emit_curve_payload(
                            sink,
                            peak,
                            content.canonical_sample_identity_id,
                            kind="backfill_observation",
                            related=related,
                        )
            add(
                "method_assessments",
                assessment.artifact.evidence_artifact_id,
                {
                    **_fields(
                        content,
                        "canonical_sample_identity_id",
                        "acquisition_source_id",
                        "provider_id",
                        "method_id",
                        "method_version",
                        "detector_invocation_id",
                        "technical_state",
                        "method_result",
                        "reason_codes",
                        "candidate_peak_content_ids",
                        "evidence_artifact_content_ids",
                        "individual_relation_artifact_content_ids",
                        "set_relation_artifact_content_ids",
                    ),
                    "method_subject_id": content.method_subject.method_subject_id,
                    "evaluation_receipt": asdict(content.evaluation_receipt),
                    "artifact": _artifact(assessment.artifact),
                    "evidence_refs": [
                        _artifact(ref.artifact) for ref in assessment.evidence_refs
                    ],
                },
                related,
            )
            for key in related:
                sink.update_availability(key, method_assessments="recorded")
            assessment_bindings[identity] = binding_hash

        for assessment in phase_items(
            "method_assessments", completed.method_assessments
        ):
            emit_assessment(assessment)

        for excluded in phase_items("exclusions", application.excluded_candidates):
            key = excluded.candidate_id, excluded.canonical_sample_identity_id
            refs = (*excluded.method_assessments, *excluded.composite_assessments)
            add(
                "exclusions",
                _key(*key),
                {
                    **_fields(
                        excluded,
                        "candidate_id",
                        "canonical_sample_identity_id",
                        "method_subject_id",
                        "discovery_candidate_state",
                        "reason_code",
                    ),
                    "assessment_artifact_ids": [
                        ref.artifact.evidence_artifact_id for ref in refs
                    ],
                },
                (key,),
            )
            sink.update_availability(key, terminal="excluded")
            for assessment in refs:
                emit_assessment(assessment)

        def emit_group(stage: str, group: Any, methods: set[str]) -> None:
            related = set()
            if stage == "initial_groups":
                methods = {
                    memberships[key[0]]
                    for member in group.member_subjects
                    for key in hypothesis_candidates[
                        member.canonical_sample_identity_id, member.subject_id
                    ]
                }
            for member in group.member_subjects:
                if member.processing_request_id != request_id:
                    raise ValueError("diagnostic group crossed processing request")
                sid = member.canonical_sample_identity_id
                keys = (
                    hypothesis_candidates[sid, member.subject_id]
                    if member.subject_kind == "peak_hypothesis"
                    else physical_candidates[sid, member.subject_id]
                )
                # Exact physical bindings survive successor hypothesis identities;
                # provider scope prevents a shared peak crossing method subjects.
                for pid in member.physical_observation_ids:
                    keys = keys | physical_candidates[sid, pid]
                keys = {key for key in keys if memberships[key[0]] in methods}
                member_candidates[member.member_subject_id].update(keys)
                related.update(keys)
            if stage == "final_groups":
                group_candidates[group.group_hypothesis_id].update(related)
            group_payload = {
                **_fields(
                    group,
                    "group_hypothesis_id",
                    "processing_request_id",
                    "disposition",
                ),
                "member_subjects": [
                    _fields(
                        member,
                        "member_subject_id",
                        "canonical_sample_identity_id",
                        "subject_kind",
                        "subject_id",
                        "physical_observation_ids",
                    )
                    for member in group.member_subjects
                ],
            }
            inference_basis = getattr(group, "inference_basis", None)
            if stage == "initial_groups" and inference_basis is not None:
                group_payload["inference_basis"] = asdict(inference_basis)
                group_payload["inference_decisions"] = [
                    asdict(decision) for decision in group.inference_decisions
                ]
            add(
                stage,
                group.group_hypothesis_id,
                group_payload,
                related,
            )

        child_count = 0
        report_progress("children", 0, len(application.children))

        def consume_child(child: FinalizedUntargetApplicationResult) -> None:
            nonlocal child_count
            core = child.scientific_core
            for terminal in core.terminal_subjects:
                key = terminal.candidate_id, terminal.canonical_sample_identity_id
                payload = _fields(
                    terminal,
                    "candidate_id",
                    "canonical_sample_identity_id",
                    "trace_group_id",
                    "variant",
                )
                hypothesis = getattr(terminal, "hypothesis", None)
                if hypothesis is not None:
                    physical_ids = tuple(
                        ref.content.peak_observation_content_id
                        for ref in hypothesis.peak_observation_refs
                    )
                    payload.update(
                        hypothesis_id=hypothesis.hypothesis_id,
                        physical_observation_ids=physical_ids,
                        observations=[
                            {
                                "peak_observation_content_id": (
                                    ref.content.peak_observation_content_id
                                ),
                                "rt_apex_min": ref.content.integration.rt_apex_min,
                            }
                            for ref in hypothesis.peak_observation_refs
                        ],
                    )
                    hypothesis_candidates[key[1], hypothesis.hypothesis_id].add(key)
                    for ref in hypothesis.peak_observation_refs:
                        physical_id = ref.content.peak_observation_content_id
                        physical_candidates[key[1], physical_id].add(key)
                        if not capture_evidence:
                            _emit_curve_payload(
                                sink,
                                ref,
                                sample_id=key[1],
                                kind=ref.capture_stage,
                                related=(key,),
                            )
                add("terminal_subjects", _key(*key), payload, (key,))
                sink.update_availability(
                    key, terminal=terminal.variant, final_groups="recorded"
                )
                if completed.initial_groups:
                    sink.update_availability(key, initial_groups="recorded")

            methods = {
                provider.method_subject_id for provider in core.scope.evidence_providers
            }
            for group in core.final_population:
                emit_group("final_groups", group, methods)

            for observation in child.scientific_core.cell_observations:
                related = group_candidates[observation.group.group_hypothesis_id]
                add(
                    "cell_observations",
                    observation.cell_observation_id,
                    {
                        **_fields(
                            observation,
                            "cell_observation_id",
                            "processing_request_id",
                            "evidence_states",
                        ),
                        "group_hypothesis_id": observation.group.group_hypothesis_id,
                        "canonical_sample_identity_id": (
                            observation.canonical_sample.identity.sample_identity_id
                        ),
                        "predecessor_cells": [
                            {
                                "content": _cell_observation_content_payload(
                                    ref.content
                                ),
                                "artifact": _artifact(ref.artifact),
                            }
                            for ref in observation.predecessor_cells
                        ],
                        "backfill_cell": (
                            None
                            if observation.backfill_cell is None
                            else {
                                "content": _cell_observation_content_payload(
                                    observation.backfill_cell.content
                                ),
                                "artifact": _artifact(
                                    observation.backfill_cell.artifact
                                ),
                            }
                        ),
                    },
                    related,
                )
            for row in child.completed_projection.rows:
                group_id = row.row_key.group_hypothesis_id
                related = group_candidates[group_id]
                add(
                    "rows",
                    row.row_key.untarget_row_key_id,
                    {
                        "row_key_id": row.row_key.untarget_row_key_id,
                        "group_hypothesis_id": group_id,
                        "route": {
                            "destination": row.route.destination,
                            "reason": row.route.reason,
                        },
                    },
                    related,
                )
                for decision in row.decisions:
                    add(
                        "cell_decisions",
                        decision.decision_id,
                        {
                            **_fields(
                                decision, "decision_id", "variant", "value", "source"
                            ),
                            "row_key_id": row.row_key.untarget_row_key_id,
                            "group_hypothesis_id": group_id,
                            "canonical_sample_identity_id": (
                                decision.subject.canonical_sample.identity.sample_identity_id
                            ),
                            **_fields(decision.evidence, "cell_observation_id"),
                        },
                        related,
                    )
            child_count += 1
            if child_count % 256 == 0:
                report_progress("children", child_count, len(application.children))

        def finalize(writer_row_count: int, matrix_path: Path) -> Path:
            if child_count != len(application.children):
                raise ValueError("explanation did not consume every finalized child")
            if child_count % 256:
                report_progress("children", child_count, len(application.children))
            for group in phase_items("initial_groups", completed.initial_groups):
                emit_group("initial_groups", group, set())

            for edge in phase_items("initial_edges", completed.initial_edges):
                related = (
                    member_candidates[edge.left_member_subject_id]
                    | member_candidates[edge.right_member_subject_id]
                )
                add(
                    "initial_edges",
                    _key(edge.left_member_subject_id, edge.right_member_subject_id),
                    {
                        **_fields(
                            edge,
                            "left_member_subject_id",
                            "right_member_subject_id",
                            "disposition",
                        ),
                        "initial_evidence": asdict(edge.initial_evidence),
                        "initial_context": edge.initial_context,
                        "alignment_config": asdict(edge.config),
                    },
                    related,
                )

            report_progress("finalizing", sum(sink.record_counts.values()), None)
            with Path(matrix_path).open("rb") as handle:
                matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            with Path(matrix_path).open(
                encoding="utf-8-sig", newline=""
            ) as matrix_handle:
                matrix_rows = sum(
                    1 for _ in csv.DictReader(matrix_handle, delimiter="\t")
                )
            initial_group_member_count = sum(
                len(group.member_subjects) for group in completed.initial_groups
            )
            summary = {
                **_fields(
                    request,
                    "processing_request_id",
                    "scientific_input_schema_version",
                    "processing_algorithm_version",
                    "processing_contract_version",
                ),
                "processing_attempt_id": (
                    application.processing_attempt.processing_attempt_id
                ),
                "samples": [
                    {
                        "sample_stem": stem,
                        "canonical_sample_identity_id": (
                            sample.identity.sample_identity_id
                        ),
                    }
                    for stem, sample in samples.items()
                ],
                "acquisition_sources": [
                    asdict(source) for source in request.acquisition_sources
                ],
                "candidate_count": sink.candidate_count,
                "matrix_row_count": matrix_rows,
                "candidate_examples": sink.candidate_examples(),
                "writer_row_count": writer_row_count,
                "matrix_path": str(matrix_path),
                "matrix_sha256": matrix_hash,
                "alignment_config": asdict(alignment_config),
                "reason_counts": dict(sorted(reasons.items())),
                "record_counts": dict(sink.record_counts),
                "initial_edge_coverage": {
                    "policy": INITIAL_GROUPING_POLICY,
                    "unlisted_pairs": UNLISTED_PAIR_DISPOSITION,
                    "rt_pruning": True,
                    # Retained records, not comparisons performed. The sparse
                    # path evaluates witness pairs without keeping a pair
                    # graph, so calling this "evaluated" reported zero
                    # comparisons for a run that made them.
                    "retained_pair_record_count": len(completed.initial_edges),
                    "compressed_group_count": len(completed.initial_groups),
                    "compressed_member_count": initial_group_member_count,
                },
                "audit_availability": {
                    name: (
                        "recorded"
                        if getattr(completed, name)
                        # Groups exist, so pairs were compared; what is absent
                        # is the retained pair graph, not the evaluation.
                        else "not_retained"
                        if name == "initial_edges" and completed.initial_groups
                        else "unavailable"
                    )
                    for name in (
                        "method_assessments",
                        "initial_groups",
                        "initial_edges",
                    )
                },
            }
            if known_istd_reference_provenance is not None:
                summary["known_istd_reference"] = dict(known_istd_reference_provenance)
            return sink.finalize(summary)

        yield PreparedAlignmentExplanation(consume_child, finalize)


write_alignment_explanation = write_cid_nl_explanation
