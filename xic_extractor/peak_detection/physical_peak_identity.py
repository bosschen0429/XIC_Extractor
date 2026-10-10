"""Canonical identity and strict identity primitives for physical peaks."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence

import numpy as np

from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_SEGMENT_UNIVERSE_SCHEMA,
    PhysicalPeakAnswer,
    PhysicalPeakFacts,
    PhysicalPeakQuestion,
    PhysicalPeakState,
    PhysicalSegment,
    PhysicalSegmentUniverse,
    PreparedSignalProjection,
)


def _physical_vector(values: object, *, field: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} must be a one-dimensional numeric sequence")
    try:
        array = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"{field} must be a one-dimensional numeric sequence"
        ) from exc
    if array.ndim != 1:
        raise ValueError(f"{field} must be one-dimensional")
    return tuple(float(value) for value in array)


def canonical_physical_trace_content_bytes(
    rt_values: object,
    intensity_values: object,
) -> bytes:
    rt = _physical_vector(rt_values, field="rt_values")
    raw = _physical_vector(intensity_values, field="intensity_values")
    return _canonical_physical_json_bytes(
        {
            "schema_version": "physical_trace_content",
            "rt": [_physical_float_text(value) for value in rt],
            "raw": [_physical_float_text(value) for value in raw],
        }
    )


def mint_physical_trace_content_id(
    rt_values: object,
    intensity_values: object,
) -> str:
    return _typed_physical_id(
        "trace-content:",
        canonical_physical_trace_content_bytes(rt_values, intensity_values),
    )


def validate_physical_trace_content_id(
    expected_trace_content_id: str,
    rt_values: object,
    intensity_values: object,
) -> None:
    if expected_trace_content_id != mint_physical_trace_content_id(
        rt_values, intensity_values
    ):
        raise ValueError("trace_content_id does not match exact RT/raw content")


def canonical_prepared_signal_identity_bytes(
    trace_content_id: str,
    profile_id: str,
) -> bytes:
    return _canonical_physical_json_bytes(
        {
            "schema_version": "prepared_signal_identity",
            "trace_content_id": _required_physical_text(
                trace_content_id, field="trace_content_id"
            ),
            "profile_id": _required_physical_text(profile_id, field="profile_id"),
        }
    )


def validate_prepared_signal_identity(projection: PreparedSignalProjection) -> None:
    if projection.schema_version != "prepared_signal_projection":
        raise ValueError("unsupported prepared signal projection schema")
    if projection.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported prepared signal state")
    if (projection.state == "available") != (projection.reason == ""):
        raise ValueError("prepared signal state/reason is not canonical")
    if (
        isinstance(projection.trace_point_count, bool)
        or not isinstance(projection.trace_point_count, int)
        or projection.trace_point_count < 0
    ):
        raise ValueError("trace_point_count must be a nonnegative integer")
    expected = _typed_physical_id(
        "prepared-signal:",
        canonical_prepared_signal_identity_bytes(
            projection.trace_content_id, projection.profile_id
        ),
    )
    if projection.prepared_signal_id != expected:
        raise ValueError("prepared_signal_id does not match owner content")


def canonical_physical_segment_bytes(segment: PhysicalSegment) -> bytes:
    if not isinstance(segment, PhysicalSegment):
        raise TypeError("segment must be a PhysicalSegment")
    return _canonical_physical_json_bytes(_physical_segment_basis(segment))


def canonical_physical_segment_universe_bytes(
    universe: PhysicalSegmentUniverse,
) -> bytes:
    if not isinstance(universe, PhysicalSegmentUniverse):
        raise TypeError("universe must be a PhysicalSegmentUniverse")
    return _canonical_physical_json_bytes(
        _physical_segment_universe_basis(
            universe.prepared_signal_id,
            universe.context_rt_start_min,
            universe.context_rt_end_min,
            universe.state,
            universe.reason,
            universe.segments,
            schema_version=universe.schema_version,
        )
    )


def validate_physical_segment_universe_identity(
    universe: PhysicalSegmentUniverse,
) -> None:
    if universe.schema_version != PHYSICAL_SEGMENT_UNIVERSE_SCHEMA:
        raise ValueError("unsupported physical segment universe schema")
    if universe.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported physical segment universe state")
    if (universe.state == "available") != (universe.reason == ""):
        raise ValueError("physical segment universe state/reason is not canonical")
    if universe.state == "available" and not universe.segments:
        raise ValueError("available physical segment universe must not be empty")
    context_start = _finite_physical_float(
        universe.context_rt_start_min,
        field="context_rt_start_min",
    )
    context_end = _finite_physical_float(
        universe.context_rt_end_min,
        field="context_rt_end_min",
    )
    if context_start > context_end:
        raise ValueError("physical segment universe context is reversed")
    if len(universe.segment_ids) != len(set(universe.segment_ids)):
        raise ValueError("physical segment IDs must be unique")
    for segment in universe.segments:
        expected_segment_id = _typed_physical_id(
            "physical-segment:", canonical_physical_segment_bytes(segment)
        )
        if segment.physical_segment_id != expected_segment_id:
            raise ValueError("physical_segment_id does not match owner content")
    expected_universe_id = _typed_physical_id(
        "segment-universe:",
        canonical_physical_segment_universe_bytes(universe),
    )
    if universe.segment_universe_id != expected_universe_id:
        raise ValueError("segment_universe_id does not match owner content")


def canonical_physical_peak_question_bytes(question: PhysicalPeakQuestion) -> bytes:
    if not isinstance(question, PhysicalPeakQuestion):
        raise TypeError("question must be a PhysicalPeakQuestion")
    return _canonical_physical_json_bytes(_physical_question_basis(question))


def canonical_physical_peak_answer_bytes(answer: PhysicalPeakAnswer) -> bytes:
    if not isinstance(answer, PhysicalPeakAnswer):
        raise TypeError("answer must be a PhysicalPeakAnswer")
    return _canonical_physical_json_bytes(
        {
            "schema_version": answer.schema_version,
            "state": answer.state,
            "reason": answer.reason,
            "prepared_signal_id": answer.prepared_signal_id,
            "physical_question_id": answer.physical_question_id,
            "physical_segment_id": answer.physical_segment_id,
        }
    )


def validate_physical_peak_answer_identity(answer: PhysicalPeakAnswer) -> None:
    if answer.schema_version != "physical_peak_answer":
        raise ValueError("unsupported physical peak answer schema")
    if answer.state not in {"available", "unavailable", "failed"}:
        raise ValueError("unsupported physical peak answer state")
    if (answer.state == "available") != (answer.reason == ""):
        raise ValueError("physical peak answer state/reason is not canonical")
    if (answer.state == "available") != (answer.physical_segment_id is not None):
        raise ValueError("physical peak answer selection is not canonical")
    expected = _typed_physical_id(
        "physical-answer:", canonical_physical_peak_answer_bytes(answer)
    )
    if answer.physical_answer_id != expected:
        raise ValueError("physical_answer_id does not match owner content")


def canonical_physical_peak_facts_bytes(
    facts: PhysicalPeakFacts,
    *,
    relative_shape_values: Sequence[float],
) -> bytes:
    if not isinstance(facts, PhysicalPeakFacts):
        raise TypeError("facts must be PhysicalPeakFacts")
    normalized = tuple(
        _finite_physical_float(value, field="relative_shape_values")
        for value in relative_shape_values
    )
    if len(normalized) != facts.relative_shape_point_count:
        raise ValueError("relative shape cardinality does not match facts")
    normalized_bytes = _canonical_physical_json_bytes(
        {
            "schema_version": facts.relative_shape_schema_version,
            "values": [_physical_float_text(value) for value in normalized],
        }
    )
    if (
        hashlib.sha256(normalized_bytes).hexdigest()
        != facts.relative_shape_content_hash
    ):
        raise ValueError("relative_shape_content_hash does not match live shape")
    return _canonical_physical_json_bytes(
        {
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
            "relative_shape_schema_version": facts.relative_shape_schema_version,
            "relative_shape_point_count": facts.relative_shape_point_count,
            "relative_shape_content_hash": facts.relative_shape_content_hash,
            "relative_shape_values": [
                _physical_float_text(value) for value in normalized
            ],
        }
    )


def validate_physical_peak_facts_identity(
    facts: PhysicalPeakFacts,
    *,
    relative_shape_values: Sequence[float],
) -> None:
    expected = _typed_physical_id(
        "physical-facts:",
        canonical_physical_peak_facts_bytes(
            facts,
            relative_shape_values=relative_shape_values,
        ),
    )
    if facts.fact_content_id != expected:
        raise ValueError("fact_content_id does not match complete live fact")


def _physical_segment_basis(segment: PhysicalSegment) -> dict[str, object]:
    return {
        "schema_version": "physical_segment",
        "start_index": segment.start_index,
        "end_index": segment.end_index,
        "apex_index": segment.apex_index,
        "left_rt_min": _physical_float_text(segment.left_rt_min),
        "right_rt_min": _physical_float_text(segment.right_rt_min),
        "apex_rt_min": _physical_float_text(segment.apex_rt_min),
        "height": _physical_float_text(segment.height),
        "scan_support_count": segment.scan_support_count,
        "area_counts_seconds": _physical_float_text(segment.area_counts_seconds),
        "segment_class": segment.segment_class,
        "boundary_stop_reason": segment.boundary_stop_reason,
        "evidence_sources": list(segment.evidence_sources),
    }


def _physical_segment_universe_basis(
    prepared_signal_id: str,
    context_rt_start_min: float,
    context_rt_end_min: float,
    state: PhysicalPeakState,
    reason: str,
    segments: tuple[PhysicalSegment, ...],
    *,
    schema_version: str,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "prepared_signal_id": prepared_signal_id,
        "context_rt_start_min": _physical_float_text(context_rt_start_min),
        "context_rt_end_min": _physical_float_text(context_rt_end_min),
        "state": state,
        "reason": reason,
        "segments": [
            {
                "physical_segment_id": segment.physical_segment_id,
                **_physical_segment_basis(segment),
            }
            for segment in segments
        ],
    }


def _physical_question_basis(question: PhysicalPeakQuestion) -> dict[str, object]:
    basis: dict[str, object] = {
        "schema_version": question.schema_version,
        "trace_content_id": question.trace_content_id,
        "prepared_signal_id": question.prepared_signal_id,
        "segment_universe_id": question.segment_universe_id,
        "segment_ids": list(question.segment_ids),
        "selection_subject_id": question.selection_subject_id,
        "query_rt_min": _physical_float_text(question.query_rt_min),
        "context_rt_start_min": _physical_float_text(question.context_rt_start_min),
        "context_rt_end_min": _physical_float_text(question.context_rt_end_min),
        "admissible_segment_ids": list(question.admissible_segment_ids),
        "selection_policy_version": question.selection_policy_version,
    }
    if question.search_rt_window is not None:
        basis["search_rt_window"] = [
            _physical_float_text(value) for value in question.search_rt_window
        ]
    return basis


def _physical_float_text(value: float) -> str:
    number = float(value)
    if number == 0.0:
        return (0.0).hex()
    if math.isnan(number):
        return "nan"
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    return number.hex()


def _finite_physical_float(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _required_physical_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{field} must be non-empty text")
    return value


def _canonical_physical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _typed_physical_id(prefix: str, payload: bytes) -> str:
    return f"{prefix}{hashlib.sha256(payload).hexdigest()}"


def _require_typed_physical_hash(value: str, *, prefix: str, field: str) -> None:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError(f"{field} has an invalid typed-hash prefix")
    digest = value[len(prefix) :]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{field} must carry one lowercase SHA-256 digest")
