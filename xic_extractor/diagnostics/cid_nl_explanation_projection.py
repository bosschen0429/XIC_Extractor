"""Project existing CID-NL owners into diagnostic facts without deciding science."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.diagnostics.cid_nl_explanation import ExplanationReportWriter
from xic_extractor.peak_detection.cid_nl_method_subject import (
    cid_nl_method_subject_batch_from_request,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.config import AlignmentConfig
    from xic_extractor.alignment.untargeted_finalized_matrix import (
        CompletedUntargetBatchWriterInputs,
    )
    from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
    from xic_extractor.discovery.models import DiscoveryCandidate


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


def _key(*parts: str) -> str:
    return json.dumps(parts, separators=(",", ":"))


def write_cid_nl_explanation(
    path: Path,
    *,
    completed: CompletedUntargetBatchWorkflowResult,
    writer_inputs: CompletedUntargetBatchWriterInputs,
    candidates_by_sample: Mapping[str, Sequence[DiscoveryCandidate]],
    matrix_path: Path,
    alignment_config: AlignmentConfig,
    known_istd_reference_provenance: Mapping[str, str] | None = None,
) -> Path:
    """Persist exact owner identities, reasons and Matrix publication provenance."""
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
            for candidate in declared:
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

        for subject in subjects.subjects:
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

        for excluded in application.excluded_candidates:
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

        assessments = chain(
            completed.method_assessments,
            (
                ref
                for excluded in application.excluded_candidates
                for refs in (
                    excluded.method_assessments,
                    excluded.composite_assessments,
                )
                for ref in refs
            ),
        )
        for assessment in assessments:
            content = assessment.content
            related = method_candidates[
                content.method_subject.method_subject_id,
                content.canonical_sample_identity_id,
            ]
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

        for child in application.children:
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
                    )
                    hypothesis_candidates[key[1], hypothesis.hypothesis_id].add(key)
                    for physical_id in physical_ids:
                        physical_candidates[key[1], physical_id].add(key)
                add("terminal_subjects", _key(*key), payload, (key,))
                sink.update_availability(
                    key, terminal=terminal.variant, final_groups="recorded"
                )
                if completed.initial_groups:
                    sink.update_availability(key, initial_groups="recorded")

        final_groups = (
            group
            for child in application.children
            for group in child.scientific_core.final_population
        )
        final_methods = {
            group.group_hypothesis_id: {
                provider.method_subject_id
                for provider in child.scientific_core.scope.evidence_providers
            }
            for child in application.children
            for group in child.scientific_core.final_population
        }
        for stage, groups in (
            ("initial_groups", completed.initial_groups),
            ("final_groups", final_groups),
        ):
            for group in groups:
                related = set()
                methods = final_methods.get(group.group_hypothesis_id, set())
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
                add(
                    stage,
                    group.group_hypothesis_id,
                    {
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
                    },
                    related,
                )

        for edge in completed.initial_edges:
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

        for child in application.children:
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
                                "content": asdict(ref.content),
                                "artifact": _artifact(ref.artifact),
                            }
                            for ref in observation.predecessor_cells
                        ],
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
                        "route": asdict(row.route),
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

        with Path(matrix_path).open("rb") as handle:
            matrix_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        with Path(matrix_path).open(encoding="utf-8-sig", newline="") as matrix_handle:
            matrix_rows = sum(1 for _ in csv.DictReader(matrix_handle, delimiter="\t"))
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
                    "canonical_sample_identity_id": sample.identity.sample_identity_id,
                }
                for stem, sample in samples.items()
            ],
            "acquisition_sources": [
                asdict(source) for source in request.acquisition_sources
            ],
            "candidate_count": sink.candidate_count,
            "matrix_row_count": matrix_rows,
            "candidate_examples": sink.candidate_examples(),
            "writer_row_count": len(writer_inputs.matrix.clusters),
            "matrix_path": str(matrix_path),
            "matrix_sha256": matrix_hash,
            "alignment_config": asdict(alignment_config),
            "reason_counts": dict(sorted(reasons.items())),
            "record_counts": dict(sink.record_counts),
            "audit_availability": {
                name: "recorded" if getattr(completed, name) else "unavailable"
                for name in ("method_assessments", "initial_groups", "initial_edges")
            },
        }
        if known_istd_reference_provenance is not None:
            summary["known_istd_reference"] = dict(known_istd_reference_provenance)
        return sink.finalize(summary)
