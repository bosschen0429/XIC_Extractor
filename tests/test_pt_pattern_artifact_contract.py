from __future__ import annotations

import pytest

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    validate_pt_candidate_trace_summary_identity,
)


def _candidate_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "candidate_id": "SampleA|pt|candidate-1",
        "sample_stem": "SampleA",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
    }
    row.update(overrides)
    return row


def _trace_summary_row(**overrides: object) -> dict[str, object]:
    row = _candidate_row()
    row.update(overrides)
    return row


def test_identity_contract_accepts_equivalent_numeric_serializations() -> None:
    validate_pt_candidate_trace_summary_identity(
        _candidate_row(base_mz="500", base_rt_apex_min="10.000000"),
        _trace_summary_row(base_mz=500.0, base_rt_apex_min=10),
    )


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [
        ("candidate_id", "SampleA|pt|candidate-2"),
        ("sample_stem", "SampleB"),
        ("base_mz", "501.0"),
        ("base_rt_apex_min", "10.5"),
    ],
)
def test_identity_contract_rejects_conflicting_shared_fields(
    field: str,
    conflicting_value: str,
) -> None:
    with pytest.raises(ValueError, match=rf"{field}.*does not match") as error:
        validate_pt_candidate_trace_summary_identity(
            _candidate_row(),
            _trace_summary_row(**{field: conflicting_value}),
        )

    assert "regenerate the trace summary from the same candidate csv" in str(
        error.value
    ).lower()


@pytest.mark.parametrize(
    ("side", "field", "invalid_value"),
    [
        ("candidate", "candidate_id", " "),
        ("trace summary", "sample_stem", ""),
        ("candidate", "sample_stem", None),
        ("trace summary", "candidate_id", True),
        ("candidate", "base_mz", "nan"),
        ("trace summary", "base_mz", "inf"),
        ("candidate", "base_rt_apex_min", "not-a-number"),
        ("trace summary", "base_rt_apex_min", True),
    ],
)
def test_identity_contract_rejects_blank_or_nonfinite_identity_fields(
    side: str,
    field: str,
    invalid_value: object,
) -> None:
    candidate = _candidate_row()
    summary = _trace_summary_row()
    target = candidate if side == "candidate" else summary
    target[field] = invalid_value

    with pytest.raises(ValueError, match=rf"{side} {field} must"):
        validate_pt_candidate_trace_summary_identity(candidate, summary)
