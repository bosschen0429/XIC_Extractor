"""Cross-artifact identity contracts for Pt pattern diagnostic evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping

from xic_extractor.tabular_io import optional_float

_TEXT_IDENTITY_FIELDS = ("candidate_id", "sample_stem")
_NUMERIC_IDENTITY_FIELDS = ("base_mz", "base_rt_apex_min")
_REGENERATION_HINT = (
    " Regenerate the trace summary from the same candidate CSV before retrying."
)


def prepare_pt_trace_summary_rows_for_csv(
    rows: tuple[Mapping[str, object], ...],
) -> tuple[dict[str, object], ...]:
    """Preserve candidate identity coordinates across the summary CSV boundary."""

    prepared_rows: list[dict[str, object]] = []
    for row in rows:
        prepared = dict(row)
        for field in _NUMERIC_IDENTITY_FIELDS:
            value = _required_finite_number(row, field=field, side="trace summary")
            prepared[field] = format_pt_machine_float(value)
        prepared_rows.append(prepared)
    return tuple(prepared_rows)


def format_pt_machine_float(value: float) -> str:
    """Format a Pt machine-handoff float without losing binary precision."""

    if not math.isfinite(value):
        return ""
    if value.is_integer():
        return str(int(value))
    return repr(value)


def validate_pt_candidate_trace_summary_identity(
    candidate_row: Mapping[str, object],
    trace_summary_row: Mapping[str, object],
) -> None:
    """Fail closed unless candidate and trace summary identify the same peak."""

    candidate_text = {
        field: _required_text(candidate_row, field=field, side="candidate")
        for field in _TEXT_IDENTITY_FIELDS
    }
    summary_text = {
        field: _required_text(trace_summary_row, field=field, side="trace summary")
        for field in _TEXT_IDENTITY_FIELDS
    }
    for field in _TEXT_IDENTITY_FIELDS:
        if candidate_text[field] != summary_text[field]:
            raise ValueError(
                f"{field} does not match between candidate and trace summary: "
                f"{candidate_text[field]!r} != {summary_text[field]!r}."
                + _REGENERATION_HINT
            )

    candidate_numeric = {
        field: _required_finite_number(candidate_row, field=field, side="candidate")
        for field in _NUMERIC_IDENTITY_FIELDS
    }
    summary_numeric = {
        field: _required_finite_number(
            trace_summary_row,
            field=field,
            side="trace summary",
        )
        for field in _NUMERIC_IDENTITY_FIELDS
    }
    for field in _NUMERIC_IDENTITY_FIELDS:
        if candidate_numeric[field] != summary_numeric[field]:
            raise ValueError(
                f"{field} does not match between candidate and trace summary for "
                f"candidate_id {candidate_text['candidate_id']!r}: "
                f"{candidate_numeric[field]!r} != {summary_numeric[field]!r}."
                + _REGENERATION_HINT
            )


def _required_text(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
) -> str:
    raw_value = row.get(field)
    if raw_value is None or isinstance(raw_value, bool):
        raise ValueError(f"{side} {field} must be non-empty")
    value = str(raw_value).strip()
    if not value:
        raise ValueError(f"{side} {field} must be non-empty")
    return value


def _required_finite_number(
    row: Mapping[str, object],
    *,
    field: str,
    side: str,
) -> float:
    value = optional_float(row.get(field))
    if value is None:
        raise ValueError(f"{side} {field} must be a finite number")
    return value
