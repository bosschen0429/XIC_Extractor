"""Strict physical peak evaluation persistence and rehydration."""

from __future__ import annotations

import json
from collections.abc import Mapping

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    canonical_signal_processing_profile_bytes,
    deserialize_signal_processing_profile,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.physical_peak_evaluation import (
    _physical_operation,
    _physical_state,
    evaluate_physical_peak,
    prepare_physical_signal,
    validate_bounded_physical_peak_evaluation,
)
from xic_extractor.peak_detection.physical_peak_identity import (
    _canonical_physical_json_bytes,
    _physical_float_text,
    _physical_question_basis,
    _physical_segment_basis,
    _physical_vector,
    validate_physical_trace_content_id,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PhysicalPeakAnswer,
    PhysicalPeakEvaluation,
    PhysicalPeakFacts,
    PhysicalPeakQuestion,
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignalProjection,
)

_PERSISTED_PHYSICAL_EVALUATION_SCHEMA = "physical_peak_evaluation_persistence"
_PERSISTED_PHYSICAL_KEYS = (
    "schema_version",
    "profile",
    "trace",
    "evaluation",
)
_PERSISTED_PROFILE_KEYS = ("profile_hash", "profile_id", "content")
_PERSISTED_TRACE_KEYS = ("trace_content_id", "rt", "raw")
_PERSISTED_EVALUATION_KEYS = (
    "state",
    "reason",
    "prepared",
    "segment_universe",
    "question",
    "answer",
    "facts",
    "integration",
    "operations",
)
_PREPARED_PROJECTION_KEYS = (
    "schema_version",
    "trace_content_id",
    "profile_id",
    "prepared_signal_id",
    "trace_point_count",
    "state",
    "reason",
)
_SEGMENT_KEYS = (
    "physical_segment_id",
    "schema_version",
    "start_index",
    "end_index",
    "apex_index",
    "left_rt_min",
    "right_rt_min",
    "apex_rt_min",
    "height",
    "scan_support_count",
    "area_counts_seconds",
    "segment_class",
    "boundary_stop_reason",
    "evidence_sources",
)
_SEGMENT_UNIVERSE_KEYS = (
    "schema_version",
    "prepared_signal_id",
    "context_rt_start_min",
    "context_rt_end_min",
    "state",
    "reason",
    "segments",
    "segment_universe_id",
)
_QUESTION_KEYS = (
    "schema_version",
    "trace_content_id",
    "prepared_signal_id",
    "segment_universe_id",
    "segment_ids",
    "selection_subject_id",
    "query_rt_min",
    "context_rt_start_min",
    "context_rt_end_min",
    "admissible_segment_ids",
    "selection_policy_version",
    "physical_question_id",
)
_ANSWER_KEYS = (
    "schema_version",
    "state",
    "reason",
    "prepared_signal_id",
    "physical_question_id",
    "physical_segment_id",
    "physical_answer_id",
)
_FACT_KEYS = (
    "schema_version",
    "prepared_signal_id",
    "physical_question_id",
    "physical_answer_id",
    "physical_segment_id",
    "apex_rt_min",
    "left_rt_min",
    "right_rt_min",
    "height",
    "scan_support_count",
    "area_counts_seconds",
    "area_source",
    "relative_shape_schema_version",
    "relative_shape_point_count",
    "relative_shape_content_hash",
    "fact_content_id",
)
_INTEGRATION_KEYS = (
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "raw_apex_rt_min",
    "rt_width_min",
    "height_raw",
    "height_smoothed",
    "area_raw_counts_seconds",
    "integration_method",
    "boundary_sources",
    "area_baseline_corrected",
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "baseline_type",
    "baseline_score",
    "raw_scan_indices",
    "area_ms1_morphology",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
)


def rehydrate_physical_peak_evaluation(
    rt_values: object,
    intensity_values: object,
    *,
    profile: SignalProcessingProfile,
    expected: PhysicalPeakEvaluation,
    request_context_rt_start_min: float | None = None,
    request_context_rt_end_min: float | None = None,
) -> PhysicalPeakEvaluation:
    if not isinstance(expected, PhysicalPeakEvaluation):
        raise TypeError("expected must be a PhysicalPeakEvaluation")
    prepared_result = prepare_physical_signal(
        rt_values,
        intensity_values,
        trace_content_id=expected.prepared.trace_content_id,
        profile=profile,
    )
    actual = evaluate_physical_peak(
        prepared_result,
        selection_subject_id=expected.question.selection_subject_id,
        query_rt_min=expected.question.query_rt_min,
        request_context_rt_start_min=request_context_rt_start_min,
        request_context_rt_end_min=request_context_rt_end_min,
        selection_policy_version=expected.question.selection_policy_version,
        search_rt_window=expected.question.search_rt_window,
    )
    if actual != expected:
        raise ValueError(
            "persisted physical evaluation does not match live owner result"
        )
    return actual


def serialize_physical_peak_evaluation(
    rt_values: object,
    intensity_values: object,
    *,
    profile: SignalProcessingProfile,
    evaluation: PhysicalPeakEvaluation,
) -> bytes:
    """Purely encode one complete owner-rehydratable physical evaluation."""

    if not isinstance(profile, SignalProcessingProfile):
        raise TypeError("profile must be a SignalProcessingProfile")
    if not isinstance(evaluation, PhysicalPeakEvaluation):
        raise TypeError("evaluation must be a PhysicalPeakEvaluation")
    rt = _physical_vector(rt_values, field="rt_values")
    raw = _physical_vector(intensity_values, field="intensity_values")
    validate_bounded_physical_peak_evaluation(evaluation)
    validate_physical_trace_content_id(
        evaluation.prepared.trace_content_id,
        rt,
        raw,
    )
    if evaluation.prepared.profile_id != profile.profile_id:
        raise ValueError("physical evaluation crosses signal-processing profile")
    return _canonical_physical_json_bytes(
        _persisted_physical_payload(rt, raw, profile, evaluation)
    )


def deserialize_physical_peak_evaluation(
    payload: bytes | str,
    *,
    expected_profile: SignalProcessingProfile | None = None,
    expected_trace_content_id: str | None = None,
) -> tuple[
    tuple[float, ...],
    tuple[float, ...],
    SignalProcessingProfile,
    PhysicalPeakEvaluation,
]:
    """Strictly parse and rehydrate one persisted physical evaluation."""

    raw_payload = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not isinstance(raw_payload, bytes):
        raise TypeError("physical evaluation payload must be bytes or str")
    try:
        decoded = raw_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("physical evaluation payload must be UTF-8") from exc
    try:
        value = json.loads(decoded, object_pairs_hook=_unique_physical_mapping)
    except json.JSONDecodeError as exc:
        raise ValueError("physical evaluation payload must be valid JSON") from exc
    root = _physical_mapping(value, label="physical evaluation payload")
    _require_physical_keys(root, _PERSISTED_PHYSICAL_KEYS, label="physical payload")
    if root["schema_version"] != _PERSISTED_PHYSICAL_EVALUATION_SCHEMA:
        raise ValueError("unsupported physical evaluation persistence schema")

    profile_envelope = _physical_mapping(root["profile"], label="profile")
    _require_physical_keys(profile_envelope, _PERSISTED_PROFILE_KEYS, label="profile")
    profile_content = _physical_mapping(
        profile_envelope["content"], label="profile content"
    )
    profile = deserialize_signal_processing_profile(
        _canonical_physical_json_bytes(profile_content),
        expected_profile_hash=_physical_text(
            profile_envelope["profile_hash"], field="profile_hash"
        ),
        expected_profile_id=_physical_text(
            profile_envelope["profile_id"], field="profile_id"
        ),
    )
    if expected_profile is not None and profile != expected_profile:
        raise ValueError("persisted physical profile crosses expected run profile")

    trace = _physical_mapping(root["trace"], label="trace")
    _require_physical_keys(trace, _PERSISTED_TRACE_KEYS, label="trace")
    rt = tuple(
        _physical_float_from_text(item, field="trace.rt")
        for item in _physical_list(trace["rt"], label="trace.rt")
    )
    raw = tuple(
        _physical_float_from_text(item, field="trace.raw")
        for item in _physical_list(trace["raw"], label="trace.raw")
    )
    trace_content_id = _physical_text(
        trace["trace_content_id"], field="trace_content_id"
    )
    validate_physical_trace_content_id(trace_content_id, rt, raw)
    if (
        expected_trace_content_id is not None
        and trace_content_id != expected_trace_content_id
    ):
        raise ValueError("persisted trace crosses expected trace identity")

    evaluation_value = _physical_mapping(root["evaluation"], label="evaluation")
    evaluation = physical_evaluation_from_payload(evaluation_value)
    if evaluation.prepared.trace_content_id != trace_content_id:
        raise ValueError("persisted physical evaluation crosses trace identity")
    if evaluation.prepared.profile_id != profile.profile_id:
        raise ValueError("persisted physical evaluation crosses profile identity")
    rehydrated = rehydrate_physical_peak_evaluation(
        rt,
        raw,
        profile=profile,
        expected=evaluation,
        request_context_rt_start_min=evaluation.question.context_rt_start_min,
        request_context_rt_end_min=evaluation.question.context_rt_end_min,
    )
    # Profile and trace tokens are already canonical and identity-checked.
    # Keep the live-owner evaluation check above and rebuild its payload, but
    # reuse the checked inputs instead of converting and hashing them again.
    canonical = _canonical_physical_json_bytes(
        {
            "schema_version": _PERSISTED_PHYSICAL_EVALUATION_SCHEMA,
            "profile": profile_envelope,
            "trace": trace,
            "evaluation": _physical_evaluation_payload(rehydrated),
        }
    )
    if canonical != raw_payload:
        raise ValueError("physical evaluation payload is not canonical")
    return rt, raw, profile, rehydrated


def _persisted_physical_payload(
    rt: tuple[float, ...],
    raw: tuple[float, ...],
    profile: SignalProcessingProfile,
    evaluation: PhysicalPeakEvaluation,
) -> dict[str, object]:
    return {
        "schema_version": _PERSISTED_PHYSICAL_EVALUATION_SCHEMA,
        "profile": {
            "profile_hash": profile.profile_hash,
            "profile_id": profile.profile_id,
            "content": json.loads(
                canonical_signal_processing_profile_bytes(profile).decode("utf-8")
            ),
        },
        "trace": {
            "trace_content_id": evaluation.prepared.trace_content_id,
            "rt": [_physical_float_text(item) for item in rt],
            "raw": [_physical_float_text(item) for item in raw],
        },
        "evaluation": _physical_evaluation_payload(evaluation),
    }


def _physical_evaluation_payload(
    evaluation: PhysicalPeakEvaluation,
) -> dict[str, object]:
    prepared = evaluation.prepared
    universe = evaluation.segment_universe
    question = evaluation.question
    answer = evaluation.answer
    facts = evaluation.facts
    integration = evaluation.integration
    return {
        "state": evaluation.state,
        "reason": evaluation.reason,
        "prepared": {
            "schema_version": prepared.schema_version,
            "trace_content_id": prepared.trace_content_id,
            "profile_id": prepared.profile_id,
            "prepared_signal_id": prepared.prepared_signal_id,
            "trace_point_count": prepared.trace_point_count,
            "state": prepared.state,
            "reason": prepared.reason,
        },
        "segment_universe": {
            "schema_version": universe.schema_version,
            "prepared_signal_id": universe.prepared_signal_id,
            "context_rt_start_min": _physical_float_text(universe.context_rt_start_min),
            "context_rt_end_min": _physical_float_text(universe.context_rt_end_min),
            "state": universe.state,
            "reason": universe.reason,
            "segments": [
                {
                    "physical_segment_id": segment.physical_segment_id,
                    **_physical_segment_basis(segment),
                }
                for segment in universe.segments
            ],
            "segment_universe_id": universe.segment_universe_id,
        },
        "question": {
            **_physical_question_basis(question),
            "physical_question_id": question.physical_question_id,
        },
        "answer": {
            "schema_version": answer.schema_version,
            "state": answer.state,
            "reason": answer.reason,
            "prepared_signal_id": answer.prepared_signal_id,
            "physical_question_id": answer.physical_question_id,
            "physical_segment_id": answer.physical_segment_id,
            "physical_answer_id": answer.physical_answer_id,
        },
        "facts": (
            None
            if facts is None
            else {
                "schema_version": facts.schema_version,
                "prepared_signal_id": facts.prepared_signal_id,
                "physical_question_id": facts.physical_question_id,
                "physical_answer_id": facts.physical_answer_id,
                "physical_segment_id": facts.physical_segment_id,
                "apex_rt_min": _physical_float_text(facts.apex_rt_min),
                "left_rt_min": _physical_float_text(facts.left_rt_min),
                "right_rt_min": _physical_float_text(facts.right_rt_min),
                "height": _physical_float_text(facts.height),
                "scan_support_count": facts.scan_support_count,
                "area_counts_seconds": _physical_float_text(facts.area_counts_seconds),
                "area_source": facts.area_source,
                "relative_shape_schema_version": (facts.relative_shape_schema_version),
                "relative_shape_point_count": facts.relative_shape_point_count,
                "relative_shape_content_hash": facts.relative_shape_content_hash,
                "fact_content_id": facts.fact_content_id,
            }
        ),
        "integration": (
            None if integration is None else _integration_payload(integration)
        ),
        "operations": list(evaluation.operations),
    }


def _integration_payload(integration: IntegrationResult) -> dict[str, object]:
    float_fields = {
        "rt_left_min",
        "rt_apex_min",
        "rt_right_min",
        "raw_apex_rt_min",
        "rt_width_min",
        "height_raw",
        "height_smoothed",
        "area_raw_counts_seconds",
        "area_baseline_corrected",
        "area_uncertainty",
        "baseline_residual_mad",
        "baseline_score",
        "area_ms1_morphology",
    }
    sequence_fields = {"boundary_sources", "raw_scan_indices"}
    payload: dict[str, object] = {}
    for key in _INTEGRATION_KEYS:
        value = getattr(integration, key)
        if key in float_fields:
            payload[key] = None if value is None else _physical_float_text(value)
        elif key in sequence_fields:
            payload[key] = list(value)
        else:
            payload[key] = value
    return payload


def physical_evaluation_from_payload(
    value: Mapping[str, object],
) -> PhysicalPeakEvaluation:
    _require_physical_keys(value, _PERSISTED_EVALUATION_KEYS, label="evaluation")
    prepared_value = _physical_mapping(value["prepared"], label="prepared")
    _require_physical_keys(prepared_value, _PREPARED_PROJECTION_KEYS, label="prepared")
    prepared = PreparedSignalProjection(
        schema_version=_physical_text(
            prepared_value["schema_version"], field="prepared.schema_version"
        ),
        trace_content_id=_physical_text(
            prepared_value["trace_content_id"], field="prepared.trace_content_id"
        ),
        profile_id=_physical_text(
            prepared_value["profile_id"], field="prepared.profile_id"
        ),
        prepared_signal_id=_physical_text(
            prepared_value["prepared_signal_id"],
            field="prepared.prepared_signal_id",
        ),
        trace_point_count=_physical_integer(
            prepared_value["trace_point_count"], field="prepared.trace_point_count"
        ),
        state=_physical_state(prepared_value["state"], field="prepared.state"),
        reason=_physical_text(prepared_value["reason"], field="prepared.reason"),
    )
    universe_value = _physical_mapping(
        value["segment_universe"], label="segment_universe"
    )
    _require_physical_keys(
        universe_value, _SEGMENT_UNIVERSE_KEYS, label="segment_universe"
    )
    segments: list[PhysicalSegment] = []
    for raw_segment in _physical_list(universe_value["segments"], label="segments"):
        segment = _physical_mapping(raw_segment, label="segment")
        _require_physical_keys(segment, _SEGMENT_KEYS, label="segment")
        segment_schema = _physical_text(
            segment["schema_version"], field="segment.schema_version"
        )
        if segment_schema != "physical_segment":
            raise ValueError("unsupported physical segment schema")
        segments.append(
            PhysicalSegment(
                physical_segment_id=_physical_text(
                    segment["physical_segment_id"], field="physical_segment_id"
                ),
                start_index=_physical_integer(
                    segment["start_index"], field="start_index"
                ),
                end_index=_physical_integer(segment["end_index"], field="end_index"),
                apex_index=_physical_integer(segment["apex_index"], field="apex_index"),
                left_rt_min=_physical_float_from_text(
                    segment["left_rt_min"], field="left_rt_min"
                ),
                right_rt_min=_physical_float_from_text(
                    segment["right_rt_min"], field="right_rt_min"
                ),
                apex_rt_min=_physical_float_from_text(
                    segment["apex_rt_min"], field="apex_rt_min"
                ),
                height=_physical_float_from_text(segment["height"], field="height"),
                scan_support_count=_physical_integer(
                    segment["scan_support_count"], field="scan_support_count"
                ),
                area_counts_seconds=_physical_float_from_text(
                    segment["area_counts_seconds"], field="area_counts_seconds"
                ),
                segment_class=_physical_text(
                    segment["segment_class"], field="segment_class"
                ),
                boundary_stop_reason=_physical_text(
                    segment["boundary_stop_reason"], field="boundary_stop_reason"
                ),
                evidence_sources=_physical_text_tuple(
                    segment["evidence_sources"], field="evidence_sources"
                ),
            )
        )
    universe = PhysicalSegmentUniverse(
        schema_version=_physical_text(
            universe_value["schema_version"], field="universe.schema_version"
        ),
        prepared_signal_id=_physical_text(
            universe_value["prepared_signal_id"], field="universe.prepared_signal_id"
        ),
        context_rt_start_min=_physical_float_from_text(
            universe_value["context_rt_start_min"], field="context_rt_start_min"
        ),
        context_rt_end_min=_physical_float_from_text(
            universe_value["context_rt_end_min"], field="context_rt_end_min"
        ),
        state=_physical_state(universe_value["state"], field="universe.state"),
        reason=_physical_text(universe_value["reason"], field="universe.reason"),
        segments=tuple(segments),
        segment_universe_id=_physical_text(
            universe_value["segment_universe_id"], field="segment_universe_id"
        ),
    )
    question_value = _physical_mapping(value["question"], label="question")
    bounded_search = (
        question_value.get("schema_version") == "bounded_physical_peak_question"
    )
    _require_physical_keys(
        question_value,
        (*_QUESTION_KEYS[:-1], "search_rt_window", _QUESTION_KEYS[-1])
        if bounded_search
        else _QUESTION_KEYS,
        label="question",
    )
    search_window = None
    if bounded_search:
        values = question_value["search_rt_window"]
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError("search window must contain two RT bounds")
        search_window = (
            _physical_float_from_text(values[0], field="search_rt_window"),
            _physical_float_from_text(values[1], field="search_rt_window"),
        )
    question = PhysicalPeakQuestion(
        schema_version=_physical_text(
            question_value["schema_version"], field="question.schema_version"
        ),
        trace_content_id=_physical_text(
            question_value["trace_content_id"], field="question.trace_content_id"
        ),
        prepared_signal_id=_physical_text(
            question_value["prepared_signal_id"],
            field="question.prepared_signal_id",
        ),
        segment_universe_id=_physical_text(
            question_value["segment_universe_id"], field="segment_universe_id"
        ),
        segment_ids=_physical_text_tuple(
            question_value["segment_ids"], field="segment_ids"
        ),
        selection_subject_id=_physical_text(
            question_value["selection_subject_id"], field="selection_subject_id"
        ),
        query_rt_min=_physical_float_from_text(
            question_value["query_rt_min"], field="query_rt_min"
        ),
        context_rt_start_min=_physical_float_from_text(
            question_value["context_rt_start_min"], field="context_rt_start_min"
        ),
        context_rt_end_min=_physical_float_from_text(
            question_value["context_rt_end_min"], field="context_rt_end_min"
        ),
        admissible_segment_ids=_physical_text_tuple(
            question_value["admissible_segment_ids"],
            field="admissible_segment_ids",
        ),
        selection_policy_version=_physical_text(
            question_value["selection_policy_version"],
            field="selection_policy_version",
        ),
        physical_question_id=_physical_text(
            question_value["physical_question_id"], field="physical_question_id"
        ),
        search_rt_window=search_window,
    )
    answer_value = _physical_mapping(value["answer"], label="answer")
    _require_physical_keys(answer_value, _ANSWER_KEYS, label="answer")
    answer = PhysicalPeakAnswer(
        schema_version=_physical_text(
            answer_value["schema_version"], field="answer.schema_version"
        ),
        state=_physical_state(answer_value["state"], field="answer.state"),
        reason=_physical_text(answer_value["reason"], field="answer.reason"),
        prepared_signal_id=_physical_text(
            answer_value["prepared_signal_id"], field="answer.prepared_signal_id"
        ),
        physical_question_id=_physical_text(
            answer_value["physical_question_id"], field="answer.physical_question_id"
        ),
        physical_segment_id=_optional_physical_text(
            answer_value["physical_segment_id"], field="physical_segment_id"
        ),
        physical_answer_id=_physical_text(
            answer_value["physical_answer_id"], field="physical_answer_id"
        ),
    )
    facts = _physical_facts_from_payload(value["facts"])
    integration = _physical_integration_from_payload(value["integration"])
    evaluation = PhysicalPeakEvaluation(
        state=_physical_state(value["state"], field="evaluation.state"),
        reason=_physical_text(value["reason"], field="evaluation.reason"),
        prepared=prepared,
        segment_universe=universe,
        question=question,
        answer=answer,
        facts=facts,
        integration=integration,
        operations=tuple(
            _physical_operation(item)
            for item in _physical_list(value["operations"], label="operations")
        ),
    )
    validate_bounded_physical_peak_evaluation(evaluation)
    return evaluation


def _physical_facts_from_payload(value: object) -> PhysicalPeakFacts | None:
    if value is None:
        return None
    facts = _physical_mapping(value, label="facts")
    _require_physical_keys(facts, _FACT_KEYS, label="facts")
    return PhysicalPeakFacts(
        schema_version=_physical_text(facts["schema_version"], field="facts.schema"),
        prepared_signal_id=_physical_text(
            facts["prepared_signal_id"], field="facts.prepared_signal_id"
        ),
        physical_question_id=_physical_text(
            facts["physical_question_id"], field="facts.physical_question_id"
        ),
        physical_answer_id=_physical_text(
            facts["physical_answer_id"], field="facts.physical_answer_id"
        ),
        physical_segment_id=_physical_text(
            facts["physical_segment_id"], field="facts.physical_segment_id"
        ),
        apex_rt_min=_physical_float_from_text(
            facts["apex_rt_min"], field="apex_rt_min"
        ),
        left_rt_min=_physical_float_from_text(
            facts["left_rt_min"], field="left_rt_min"
        ),
        right_rt_min=_physical_float_from_text(
            facts["right_rt_min"], field="right_rt_min"
        ),
        height=_physical_float_from_text(facts["height"], field="height"),
        scan_support_count=_physical_integer(
            facts["scan_support_count"], field="scan_support_count"
        ),
        area_counts_seconds=_physical_float_from_text(
            facts["area_counts_seconds"], field="area_counts_seconds"
        ),
        area_source=_physical_text(facts["area_source"], field="area_source"),
        relative_shape_schema_version=_physical_text(
            facts["relative_shape_schema_version"],
            field="relative_shape_schema_version",
        ),
        relative_shape_point_count=_physical_integer(
            facts["relative_shape_point_count"], field="relative_shape_point_count"
        ),
        relative_shape_content_hash=_physical_text(
            facts["relative_shape_content_hash"], field="relative_shape_content_hash"
        ),
        fact_content_id=_physical_text(
            facts["fact_content_id"], field="fact_content_id"
        ),
    )


def _physical_integration_from_payload(value: object) -> IntegrationResult | None:
    if value is None:
        return None
    integration = _physical_mapping(value, label="integration")
    _require_physical_keys(integration, _INTEGRATION_KEYS, label="integration")
    required_float_fields = (
        "rt_left_min",
        "rt_apex_min",
        "rt_right_min",
        "raw_apex_rt_min",
        "rt_width_min",
        "height_raw",
        "height_smoothed",
        "area_raw_counts_seconds",
    )
    optional_float_fields = (
        "area_baseline_corrected",
        "area_uncertainty",
        "baseline_residual_mad",
        "baseline_score",
        "area_ms1_morphology",
    )
    required = {
        key: _physical_float_from_text(integration[key], field=key)
        for key in required_float_fields
    }
    optional = {
        key: _optional_physical_float_from_text(integration[key], field=key)
        for key in optional_float_fields
    }
    return IntegrationResult(
        rt_left_min=required["rt_left_min"],
        rt_apex_min=required["rt_apex_min"],
        rt_right_min=required["rt_right_min"],
        raw_apex_rt_min=required["raw_apex_rt_min"],
        rt_width_min=required["rt_width_min"],
        height_raw=required["height_raw"],
        height_smoothed=required["height_smoothed"],
        area_raw_counts_seconds=required["area_raw_counts_seconds"],
        integration_method=_physical_text(
            integration["integration_method"], field="integration_method"
        ),
        boundary_sources=_physical_text_tuple(
            integration["boundary_sources"], field="boundary_sources"
        ),
        area_baseline_corrected=optional["area_baseline_corrected"],
        area_uncertainty=optional["area_uncertainty"],
        area_uncertainty_formula_version=_physical_text(
            integration["area_uncertainty_formula_version"],
            field="area_uncertainty_formula_version",
        ),
        baseline_residual_mad=optional["baseline_residual_mad"],
        area_uncertainty_noise_source=_physical_text(
            integration["area_uncertainty_noise_source"],
            field="area_uncertainty_noise_source",
        ),
        baseline_type=_physical_text(
            integration["baseline_type"], field="baseline_type"
        ),
        baseline_score=optional["baseline_score"],
        raw_scan_indices=tuple(
            _physical_integer(item, field="raw_scan_indices")
            for item in _physical_list(
                integration["raw_scan_indices"], label="raw_scan_indices"
            )
        ),
        area_ms1_morphology=optional["area_ms1_morphology"],
        ms1_morphology_area_source=_physical_text(
            integration["ms1_morphology_area_source"],
            field="ms1_morphology_area_source",
        ),
        ms1_morphology_trace_method=_physical_text(
            integration["ms1_morphology_trace_method"],
            field="ms1_morphology_trace_method",
        ),
        ms1_morphology_trace_window_points=_optional_physical_integer(
            integration["ms1_morphology_trace_window_points"],
            field="ms1_morphology_trace_window_points",
        ),
        ms1_morphology_trace_effective_points=_optional_physical_integer(
            integration["ms1_morphology_trace_effective_points"],
            field="ms1_morphology_trace_effective_points",
        ),
    )


def _unique_physical_mapping(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"physical payload has duplicate field: {key}")
        result[key] = value
    return result


def _physical_mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be one JSON object")
    return value


def _physical_list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be one JSON array")
    return value


def _require_physical_keys(
    value: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    label: str,
) -> None:
    actual = tuple(value)
    if actual != keys:
        raise ValueError(f"{label} fields must be exactly {keys}; got {actual}")


def _physical_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    return value


def _optional_physical_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _physical_text(value, field=field)


def _physical_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _optional_physical_integer(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _physical_integer(value, field=field)


def _physical_float_from_text(value: object, *, field: str) -> float:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be canonical hexadecimal text")
    if value == "nan":
        result = float("nan")
    elif value == "inf":
        result = float("inf")
    elif value == "-inf":
        result = float("-inf")
    else:
        try:
            result = float.fromhex(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be canonical hexadecimal text") from exc
    if _physical_float_text(result) != value:
        raise ValueError(f"{field} must be canonical hexadecimal text")
    return result


def _optional_physical_float_from_text(
    value: object,
    *,
    field: str,
) -> float | None:
    if value is None:
        return None
    return _physical_float_from_text(value, field=field)


def _physical_text_tuple(value: object, *, field: str) -> tuple[str, ...]:
    return tuple(
        _physical_text(item, field=field) for item in _physical_list(value, label=field)
    )
