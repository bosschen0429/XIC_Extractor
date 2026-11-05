"""Project existing CID-NL owners into diagnostic facts without deciding science."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence, Sized
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.alignment.cross_sample.neighborhood_graph import (
    INITIAL_GROUPING_POLICY,
    UNLISTED_PAIR_DISPOSITION,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.alignment.untargeted_finalized_matrix import feature_id_label
from xic_extractor.alignment_explanation.review import review_cell, review_record
from xic_extractor.alignment_explanation.writer import (
    ExplanationReportWriter,
)
from xic_extractor.peak_detection.cid_nl_method_subject import (
    cid_nl_method_subject_batch_from_request,
)
from xic_extractor.peak_detection.traces import Trace

if TYPE_CHECKING:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.result.application_result import (
        FinalizedUntargetApplicationResult,
    )
    from xic_extractor.discovery.models import DiscoveryCandidate
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlMethodAssessmentContent,
        CidNlMethodAssessmentRef,
    )
    from xic_extractor.untargeted_execution.execution_result import (
        CompletedUntargetBatchWorkflowResult,
    )


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
        "sample_key": (observation.canonical_sample.identity.sample_key),
        "predecessor_cells": [
            {"content": _cell_observation_content_payload(ref.content)}
            for ref in observation.predecessor_cells
        ],
        "backfill_cell": (
            None
            if observation.backfill_cell is None
            else {
                "content": _cell_observation_content_payload(
                    observation.backfill_cell.content
                )
            }
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
    # One envelope per actual content/binding; candidate associations are links.
    payload_id = sink.add_curve_payload(
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
            "physical_peak_key": obs_id,
            "record_id": record_id,
            "kind": kind,
        },
        related,
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
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    matrix_path: Path,
    alignment_config: AlignmentConfig,
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    known_istd_reference_provenance: Mapping[str, object] | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    full_audit: bool = False,
    curve_mz_ranges: Sequence[tuple[float, float]] = (),
) -> Path:
    """Persist exact owner identities, reasons and Matrix publication provenance."""
    with prepare_alignment_explanation(
        path,
        completed=completed,
        candidates_by_sample=candidates_by_sample,
        alignment_config=alignment_config,
        ms1_apex_mz_by_candidate=ms1_apex_mz_by_candidate,
        known_istd_reference_provenance=known_istd_reference_provenance,
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
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    alignment_config: AlignmentConfig,
    ms1_apex_mz_by_candidate: Mapping[str, float] | None = None,
    known_istd_reference_provenance: Mapping[str, object] | None = None,
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
        request = application.processing_request
        subjects = cid_nl_method_subject_batch_from_request(request)
        memberships = dict(subjects.candidate_memberships)
        membership_risks = {
            decision.owner_id: asdict(decision.accepted_risk)
            for group in completed.initial_groups
            for decision in group.inference_decisions
            if decision.accepted_risk is not None
        }
        samples = {
            sample.metadata.raw_stem: sample for sample in application.canonical_samples
        }
        sample_ordinals = {
            sample.identity.sample_key: index
            for index, sample in enumerate(samples.values())
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
        located_peaks: dict[str, Any] = {}

        def locate(content: Any) -> str:
            """Written locator of one physical peak; never shared by two peaks."""
            key: str = content.physical_peak_key
            identity = content.physical_identity
            if located_peaks.setdefault(key, identity) != identity:
                raise ValueError(f"distinct physical peaks share one locator: {key}")
            return key

        emitted_assessments: set[CidNlMethodAssessmentContent] = set()

        def report_progress(stage: str, completed: int, total: int | None) -> None:
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "alignment_explanation_progress",
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
            kind: str, identity: str | None, payload: dict[str, Any], related: Any
        ) -> None:
            if sink.add_record(
                kind,
                identity,
                payload if full_audit else review_record(kind, payload),
                related,
            ):
                if kind == "method_assessments":
                    reasons.update(payload["reason_codes"])
                elif kind == "exclusions":
                    reasons.update((payload["reason_code"],))
                elif kind == "initial_edges":
                    reasons.update((payload["initial_evidence"]["reason"],))
                elif kind == "rows":
                    reasons.update((payload["route"]["reason"],))

        for stem, declared in candidates_by_sample.items():
            sid = samples[stem].identity.sample_key
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
                        "sample_key": sid,
                        "ms1_apex_mz": measured_mz.get(candidate.candidate_id),
                        "method_subject_id": subject_id,
                    },
                )

        for record in phase_items(
            "discovery_physical",
            (
                completed.discovery_physical_records
                if full_audit or curve_mz_ranges
                else ()
            ),
        ):
            work = record.work_item
            sid = samples[work.sample_stem].identity.sample_key
            related = {(link.candidate_id, sid) for link in record.candidate_links}
            if not related:
                related = {(record.candidate_id, sid)}
                sink.add_candidate(
                    record.candidate_id,
                    sid,
                    {
                        "candidate_id": record.candidate_id,
                        "sample_key": sid,
                        "sample_stem": work.sample_stem,
                        "precursor_mz": work.precursor_mz,
                        "ms1_apex_mz": measured_mz.get(record.candidate_id),
                        "physical_state": record.evaluation.state,
                        "reason": record.evaluation.reason,
                        "rt_apex_min": (
                            record.evaluation.facts.apex_rt_min
                            if record.evaluation.facts is not None
                            else None
                        ),
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
                    "processing_attempt_id": (
                        application.processing_attempt.processing_attempt_id
                    ),
                },
                related=related,
            )

        capture_evidence = completed.capture_evidence
        for evidence in phase_items(
            "capture_evidence",
            capture_evidence if full_audit or curve_mz_ranges else (),
        ):
            sid = evidence.sample_key
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
            content = assessment.content
            # The same settled attempt can reach this projection through more
            # than one completed relation; its content is the record.
            if content in emitted_assessments:
                return
            related = method_candidates[
                content.method_subject.method_subject_id,
                content.sample_key,
            ]
            if (
                content.detector_invocation_id == "untargeted-cid-nl-backfill"
                and content.method_result == "SUPPORT"
            ):
                # Backfill has no Discovery terminal. Retain its exact physical
                # support links so the original candidate can explain the cell.
                for relation in assessment.individual_support_refs:
                    physical_candidates[
                        content.sample_key,
                        locate(relation.content.peak_observation),
                    ].update(related)
                if not capture_evidence:
                    for peak in assessment.candidate_peaks:
                        _emit_curve_payload(
                            sink,
                            peak,
                            content.sample_key,
                            kind="backfill_observation",
                            related=related,
                        )
            add(
                "method_assessments",
                None,
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
                    "candidate_physical_peak_keys": tuple(
                        locate(peak.content) for peak in assessment.candidate_peaks
                    ),
                    "method_subject_id": content.method_subject.method_subject_id,
                    **(
                        {"evaluation_receipt": asdict(content.evaluation_receipt)}
                        if full_audit
                        else {}
                    ),
                },
                related,
            )
            emitted_assessments.add(content)

        for assessment in phase_items(
            "method_assessments", completed.method_assessments
        ):
            emit_assessment(assessment)

        for excluded in phase_items("exclusions", application.excluded_candidates):
            key = excluded.candidate_id, excluded.sample_key
            refs = (*excluded.method_assessments, *excluded.composite_assessments)
            add(
                "exclusions",
                _key(*key),
                {
                    **_fields(
                        excluded,
                        "candidate_id",
                        "sample_key",
                        "method_subject_id",
                        "discovery_candidate_state",
                        "reason_code",
                    ),
                },
                (key,),
            )
            for assessment in refs:
                emit_assessment(assessment)

        def emit_group(
            stage: str, group: Any, methods: set[str], feature_id: str | None = None
        ) -> None:
            related = set()
            if stage == "initial_groups":
                methods = {
                    memberships[key[0]]
                    for member in group.member_subjects
                    for key in hypothesis_candidates[
                        member.sample_key, member.subject_id
                    ]
                }
            for member in group.member_subjects:
                sid = member.sample_key
                keys = (
                    hypothesis_candidates[sid, member.subject_id]
                    if member.subject_kind == "peak_hypothesis"
                    else physical_candidates[sid, member.subject_id]
                )
                # Exact physical bindings survive successor hypothesis identities;
                # provider scope prevents a shared peak crossing method subjects.
                for peak in member.physical_peaks:
                    keys = keys | physical_candidates[sid, locate(peak)]
                keys = {key for key in keys if memberships[key[0]] in methods}
                member_candidates[member.subject_id].update(keys)
                related.update(keys)
            if stage == "final_groups":
                group_candidates[group.group_hypothesis_id].update(related)
            group_payload = {
                **({} if feature_id is None else {"feature_id": feature_id}),
                "disposition": group.disposition,
                "member_subjects": [
                    {
                        **_fields(member, "sample_key", "subject_kind", "subject_id"),
                        "physical_peak_keys": [
                            locate(peak) for peak in member.physical_peaks
                        ],
                    }
                    for member in group.member_subjects
                ],
            }
            inference_basis = (
                group.inference_basis
                if isinstance(group, CrossSamplePeakGroupHypothesis)
                else None
            )
            if stage == "initial_groups" and inference_basis is not None:
                group_payload["inference_basis"] = asdict(inference_basis)
                group_payload["inference_decisions"] = [
                    asdict(decision) for decision in group.inference_decisions
                ]
            # A published group is located by its feature_id; others by rowid.
            add(stage, feature_id, group_payload, related)

        child_count = 0
        row_count = 0
        matrix_row_count = 0
        report_progress("children", 0, len(application.children))

        def consume_child(child: FinalizedUntargetApplicationResult) -> None:
            nonlocal child_count, row_count, matrix_row_count
            core = child.scientific_core
            for terminal in core.terminal_subjects:
                key = terminal.candidate_id, terminal.sample_key
                payload = _fields(
                    terminal,
                    "candidate_id",
                    "sample_key",
                    "trace_group_id",
                    "variant",
                )
                if isinstance(terminal, ObservedSearchSubject):
                    hypothesis = terminal.hypothesis
                    physical_ids = tuple(
                        locate(ref.content) for ref in hypothesis.peak_observation_refs
                    )
                    payload.update(
                        hypothesis_id=hypothesis.hypothesis_id,
                        physical_peak_keys=physical_ids,
                        observations=[
                            {
                                "physical_peak_key": locate(ref.content),
                                "rt_apex_min": ref.content.integration.rt_apex_min,
                            }
                            for ref in hypothesis.peak_observation_refs
                        ],
                    )
                    hypothesis_candidates[key[1], hypothesis.hypothesis_id].add(key)
                    for ref in hypothesis.peak_observation_refs:
                        physical_candidates[key[1], locate(ref.content)].add(key)
                        if not capture_evidence:
                            _emit_curve_payload(
                                sink,
                                ref,
                                sample_id=key[1],
                                kind=ref.capture_stage,
                                related=(key,),
                            )
                add("terminal_subjects", _key(*key), payload, (key,))

            methods = {
                provider.method_subject_id for provider in core.scope.evidence_providers
            }
            # Same child and row order as the TSV writer that labels rows.
            feature_ids = {}
            for row in child.completed_projection.rows:
                row_count += 1
                matrix_row_count += row.route.destination == "matrix"
                feature_ids[row.group.group_hypothesis_id] = feature_id_label(row_count)
            for group in core.final_population:
                emit_group(
                    "final_groups",
                    group,
                    methods,
                    feature_ids.get(group.group_hypothesis_id),
                )
            risks_by_group_sample: dict[tuple[str, str], list[dict[str, Any]]] = (
                defaultdict(list)
            )
            for group in core.final_population:
                for member in group.member_subjects:
                    if risk := membership_risks.get(member.subject_id):
                        risks_by_group_sample[
                            group.group_hypothesis_id,
                            member.sample_key,
                        ].append(risk)

            for (
                group_id,
                sample_id,
                backfill_risk,
            ) in core.grouping_result.backfill_membership_risks:
                risks_by_group_sample[group_id, sample_id].append(asdict(backfill_risk))

            for row in child.completed_projection.rows:
                group_id = row.group.group_hypothesis_id
                feature_id = feature_ids[group_id]
                related = group_candidates[group_id]
                cells = [
                    {
                        **review_cell(decision),
                        "membership_risks": risks_by_group_sample.get(
                            (
                                group_id,
                                decision.subject.canonical_sample.identity.sample_key,
                            ),
                            [],
                        ),
                    }
                    for decision in row.decisions
                ]
                add(
                    "rows",
                    feature_id,
                    {
                        "feature_id": feature_id,
                        "route": {
                            "destination": row.route.destination,
                            "reason": row.route.reason,
                        },
                        "membership_risk_flags": sorted(
                            {
                                flag
                                for cell in cells
                                for risk in cell["membership_risks"]
                                for flag in risk["risk_flags"]
                            }
                        ),
                    },
                    related,
                )
                if full_audit:
                    for decision, cell in zip(row.decisions, cells, strict=True):
                        cell["observation"] = _final_cell_observation_payload(
                            decision.evidence
                        )
                sink.add_row_cells(feature_id, cells, sample_ordinals)
            child_count += 1
            if child_count % 256 == 0:
                report_progress("children", child_count, len(application.children))

        def finalize(matrix_path: Path) -> Path:
            if child_count != len(application.children):
                raise ValueError("explanation did not consume every finalized child")
            if child_count % 256:
                report_progress("children", child_count, len(application.children))
            for group in phase_items("initial_groups", completed.initial_groups):
                emit_group("initial_groups", group, set())

            for edge in phase_items("initial_edges", completed.initial_edges):
                related = (
                    member_candidates[edge.left_owner.owner_id]
                    | member_candidates[edge.right_owner.owner_id]
                )
                add(
                    "initial_edges",
                    _key(edge.left_owner.owner_id, edge.right_owner.owner_id),
                    {
                        "left_subject_id": edge.left_owner.owner_id,
                        "right_subject_id": edge.right_owner.owner_id,
                        "disposition": edge.disposition,
                        "initial_evidence": asdict(edge.initial_evidence),
                        "initial_context": edge.initial_context,
                        "alignment_config": asdict(edge.config),
                    },
                    related,
                )

            report_progress("finalizing", sum(sink.record_counts.values()), None)
            with Path(matrix_path).open("rb") as handle:
                matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            initial_group_member_count = sum(
                len(group.member_subjects) for group in completed.initial_groups
            )
            summary = {
                **_fields(
                    request,
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
                        "sample_key": (sample.identity.sample_key),
                    }
                    for stem, sample in samples.items()
                ],
                "acquisition_sources": [
                    asdict(source) for source in request.acquisition_sources
                ],
                "candidate_count": sink.candidate_count,
                "matrix_row_count": matrix_row_count,
                "matrix_path": str(matrix_path),
                "matrix_sha256": matrix_hash,
                "alignment_config": asdict(alignment_config),
                "reason_counts": dict(sorted(reasons.items())),
                "record_counts": dict(sink.record_counts),
                "retention": "full_audit" if full_audit else "review",
                "selection_evidence_capability": "recorded_decisions",
                "curve_retention": "all"
                if full_audit
                else "selected"
                if curve_mz_ranges
                else "none",
                "curve_mz_ranges": list(curve_mz_ranges),
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
            summary["known_istd_reference"] = (
                dict(known_istd_reference_provenance)
                if known_istd_reference_provenance is not None
                else None
            )
            return sink.finalize(summary)

        yield PreparedAlignmentExplanation(consume_child, finalize)


write_alignment_explanation = write_alignment_explanation
