from __future__ import annotations

import pytest

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_EVIDENCE_SCHEMA_VERSION,
    PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
    pt_pattern_candidate_assignment_fingerprint,
    validate_pt_candidate_evidence_envelope,
    validate_pt_candidate_evidence_identity,
    validate_pt_candidate_trace_summary_identity,
    validate_pt_pattern_evidence_row_contract,
)
from xic_extractor.discovery.pt_pattern_evidence_rule import (
    DEFAULT_THRESHOLDS,
    EVIDENCE_RULE_OWNER,
    pt_pattern_rule_id,
)


def _candidate_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "candidate_id": "SampleA|pt|candidate-1",
        "sample_stem": "SampleA",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_state": "pt_pattern_candidate",
        "base_feature_id": "base-1",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "pt_plus_1_feature_id": "plus1-1",
        "pt_plus_1_mz": "501.0020",
        "pt_plus_1_expected_mz": "501.0021",
        "pt_plus_2_feature_id": "plus2-1",
        "pt_plus_2_mz": "502.0021",
        "pt_plus_2_expected_mz": "502.0023",
        "pt_plus_4_feature_id": "plus4-1",
        "pt_plus_4_mz": "504.0050",
        "pt_plus_4_expected_mz": "504.0052",
    }
    row.update(overrides)
    return row


def _trace_summary_row(**overrides: object) -> dict[str, object]:
    candidate = _candidate_row()
    row = {
        **candidate,
        "schema_version": PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
        "candidate_assignment_sha256": (
            pt_pattern_candidate_assignment_fingerprint(candidate)
        ),
    }
    row.update(overrides)
    return row


def _evidence_row(**overrides: object) -> dict[str, object]:
    candidate = _candidate_row()
    row = {
        **candidate,
        "schema_version": PT_PATTERN_EVIDENCE_SCHEMA_VERSION,
        "candidate_assignment_sha256": (
            pt_pattern_candidate_assignment_fingerprint(candidate)
        ),
        "trace_schema_version": PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
        "trace_artifact_role": "full_context",
        "trace_extraction_sha256": "e" * 64,
        "trace_manifest_sha256": "f" * 64,
        "trace_evidence_status": "evaluated",
        "trace_rule_call": "accept",
        "trace_detection_status": "detected",
        "scan_pattern_seed_status": "full_pattern_seeded",
        "trace_rule_reason": "full_pattern_close_apex_shape_overlap",
        "evidence_rule_id": pt_pattern_rule_id(DEFAULT_THRESHOLDS),
        "evidence_rule_owner": EVIDENCE_RULE_OWNER,
        "required_combined_status": (
            DEFAULT_THRESHOLDS.required_combined_status
        ),
        "min_non_base_positions_supported": (
            DEFAULT_THRESHOLDS.min_non_base_positions_supported
        ),
        "configured_max_abs_apex_delta_threshold_sec": (
            DEFAULT_THRESHOLDS.max_abs_apex_delta_sec
        ),
        "max_abs_apex_delta_scan_step_multiplier": (
            DEFAULT_THRESHOLDS.max_abs_apex_delta_scan_step_multiplier
        ),
        "min_median_rt_step_sec": "",
        "effective_max_abs_apex_delta_threshold_sec": (
            DEFAULT_THRESHOLDS.max_abs_apex_delta_sec
        ),
        "min_pearson_r_to_base_threshold": (
            DEFAULT_THRESHOLDS.min_pearson_r_to_base
        ),
        "min_active_overlap_fraction_threshold": (
            DEFAULT_THRESHOLDS.min_active_overlap_fraction
        ),
        "min_shifted_pearson_r_to_base_threshold": (
            DEFAULT_THRESHOLDS.min_shifted_pearson_r_to_base
        ),
        "max_abs_shape_shift_sec_to_base_threshold": (
            DEFAULT_THRESHOLDS.max_abs_shape_shift_sec_to_base
        ),
        "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
        "non_base_positions_supported": 3,
        "max_abs_apex_delta_sec": 0.4,
        "min_pearson_r_to_base": 0.95,
        "min_active_overlap_fraction": 0.7,
        "min_shifted_pearson_r_to_base": 0.96,
        "max_abs_shape_shift_sec_to_base": 2.0,
        "trace_ambiguity_status": "clear",
        "trace_ambiguity_flags": "none",
        "shifted_only_position_labels": "none",
        "matrix_write_authority": "FALSE",
        "counted_detection_authority": "FALSE",
        "export_activation_authority": "FALSE",
    }
    row.update(overrides)
    return row


def test_identity_contract_accepts_equivalent_numeric_serializations() -> None:
    validate_pt_candidate_trace_summary_identity(
        _candidate_row(base_mz="500", base_rt_apex_min="10.000000"),
        _trace_summary_row(base_mz=500.0, base_rt_apex_min=10),
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("pattern_id", "not-pt", "pattern_id must be 'pt'"),
        (
            "pt_plus_1_expected_mz",
            "900",
            "pt_plus_1_expected_mz must equal base_mz",
        ),
        (
            "pt_plus_2_mz",
            "700",
            "pt_plus_2_mz must be within",
        ),
    ],
)
def test_assignment_fingerprint_rejects_non_pt_geometry(
    field: str,
    value: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        pt_pattern_candidate_assignment_fingerprint(
            _candidate_row(**{field: value})
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


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [
        ("candidate_id", "SampleA|pt|candidate-2"),
        ("sample_stem", "SampleB"),
        ("method_label", "other_method"),
        ("pattern_id", "other_pattern"),
        ("base_mz", "501.0"),
        ("base_rt_apex_min", "10.5"),
    ],
)
def test_candidate_evidence_identity_rejects_stale_payload(
    field: str,
    conflicting_value: str,
) -> None:
    with pytest.raises(ValueError, match=rf"{field}.*does not match") as error:
        validate_pt_candidate_evidence_identity(
            _candidate_row(),
            _evidence_row(**{field: conflicting_value}),
        )

    assert "regenerate the evidence" in str(error.value).lower()


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    [
        ("pt_plus_1_feature_id", "plus1-reassigned"),
        ("pt_plus_1_mz", "501.0022"),
        ("pt_plus_1_expected_mz", "501.00211"),
        ("pt_plus_4_feature_id", "plus4-reassigned"),
    ],
)
def test_trace_identity_rejects_stale_partner_assignment(
    field: str,
    conflicting_value: str,
) -> None:
    original_candidate = _candidate_row()
    stale_summary = _trace_summary_row()
    changed_candidate = {**original_candidate, field: conflicting_value}

    with pytest.raises(ValueError, match="candidate_assignment_sha256"):
        validate_pt_candidate_trace_summary_identity(
            changed_candidate,
            stale_summary,
        )


def test_evidence_identity_rejects_stale_partner_assignment() -> None:
    changed_candidate = _candidate_row(
        pt_plus_4_feature_id="plus4-reassigned",
    )

    with pytest.raises(ValueError, match="candidate_assignment_sha256"):
        validate_pt_candidate_evidence_identity(
            changed_candidate,
            _evidence_row(),
        )


def test_candidate_evidence_envelope_rejects_unsupported_schema() -> None:
    candidate = _candidate_row()
    evidence = _evidence_row(schema_version="unsupported_pt_pattern_evidence")

    with pytest.raises(ValueError, match=r"unsupported evidence.*schema_version"):
        validate_pt_candidate_evidence_envelope(
            {"candidate-1": candidate},
            {"candidate-1": evidence},
            evidence_rows=(evidence,),
        )


@pytest.mark.parametrize(
    "field",
    [
        "matrix_write_authority",
        "counted_detection_authority",
        "export_activation_authority",
    ],
)
def test_evidence_contract_requires_explicit_false_authority(field: str) -> None:
    row = _evidence_row(**{field: "TRUE"})

    with pytest.raises(ValueError, match=rf"{field} must be FALSE"):
        validate_pt_pattern_evidence_row_contract(row)


def test_evidence_contract_rejects_missing_authority_field() -> None:
    row = _evidence_row()
    del row["matrix_write_authority"]

    with pytest.raises(ValueError, match="matrix_write_authority must be FALSE"):
        validate_pt_pattern_evidence_row_contract(row)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_state", "garbage", "candidate_state"),
        ("scan_pattern_seed_status", "partial", "scan_pattern_seed_status"),
        ("base_mz", "0", "base_mz must be > 0"),
        ("base_rt_apex_min", "-1", "base_rt_apex_min must be >= 0"),
        ("non_base_positions_supported", "3.5", "must be an integer"),
        ("min_pearson_r_to_base", "2.0", "must be <= 1"),
        ("min_active_overlap_fraction", "nan", "finite number"),
        ("evidence_rule_owner", "other", "evidence_rule_owner"),
        ("trace_ambiguity_status", "clear", "does not match"),
    ],
)
def test_evidence_contract_rejects_semantically_invalid_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    row = _evidence_row(**{field: value})
    if field == "trace_ambiguity_status":
        row["trace_ambiguity_flags"] = "low_scan_density"

    with pytest.raises(ValueError, match=message):
        validate_pt_pattern_evidence_row_contract(row)


def test_evidence_contract_replays_evaluated_decision() -> None:
    row = _evidence_row(min_pearson_r_to_base="0.1")

    with pytest.raises(ValueError, match="trace decision fields do not match"):
        validate_pt_pattern_evidence_row_contract(row)


def test_evidence_contract_rejects_stale_metrics_when_not_evaluated() -> None:
    row = _evidence_row(
        trace_evidence_status="not_evaluated",
        trace_rule_call="not_evaluated",
        trace_detection_status="not_evaluated",
        trace_rule_reason="trace_summary_not_provided",
        effective_max_abs_apex_delta_threshold_sec="",
        combined_pt_pattern_status="",
        non_base_positions_supported="",
        min_pearson_r_to_base="",
        min_active_overlap_fraction="",
        min_shifted_pearson_r_to_base="",
        max_abs_shape_shift_sec_to_base="",
        trace_ambiguity_status="",
        trace_ambiguity_flags="",
        shifted_only_position_labels="",
        trace_schema_version="",
        trace_artifact_role="",
        trace_extraction_sha256="",
        trace_manifest_sha256="",
    )

    with pytest.raises(ValueError, match="max_abs_apex_delta_sec must be blank"):
        validate_pt_pattern_evidence_row_contract(row)


def test_evidence_identity_binds_candidate_state() -> None:
    with pytest.raises(ValueError, match="candidate_state.*does not match"):
        validate_pt_candidate_evidence_identity(
            _candidate_row(candidate_state="pt_pattern_cid_nl_supported"),
            _evidence_row(),
        )


@pytest.mark.parametrize(
    ("candidate_keys", "evidence_keys", "message"),
    [
        (("candidate-1",), (), "missing evidence keys: candidate-1"),
        ((), ("candidate-1",), "extra evidence keys: candidate-1"),
    ],
)
def test_candidate_evidence_envelope_requires_exact_key_set(
    candidate_keys: tuple[str, ...],
    evidence_keys: tuple[str, ...],
    message: str,
) -> None:
    candidate_by_key = {key: _candidate_row(candidate_id=key) for key in candidate_keys}
    evidence_by_key = {key: _evidence_row(candidate_id=key) for key in evidence_keys}

    with pytest.raises(ValueError, match=message):
        validate_pt_candidate_evidence_envelope(
            candidate_by_key,
            evidence_by_key,
            evidence_rows=tuple(evidence_by_key.values()),
        )


def test_candidate_evidence_envelope_rejects_collapsed_map_from_other_rows() -> None:
    candidate = _candidate_row()
    raw_evidence = _evidence_row()
    mapped_evidence = _evidence_row(trace_rule_reason="stale_reason")

    with pytest.raises(ValueError, match="must be built from evidence_rows"):
        validate_pt_candidate_evidence_envelope(
            {"candidate-1": candidate},
            {"candidate-1": mapped_evidence},
            evidence_rows=(raw_evidence,),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"trace_evidence_status": "garbage"},
        {"trace_rule_call": "garbage"},
        {"trace_detection_status": "garbage"},
        {"trace_evidence_status": "not_evaluated"},
        {"trace_rule_call": "reject"},
    ],
)
def test_candidate_evidence_envelope_rejects_invalid_trace_state(
    overrides: dict[str, object],
) -> None:
    candidate = _candidate_row()
    evidence = _evidence_row(**overrides)

    with pytest.raises(ValueError, match="trace evidence state"):
        validate_pt_candidate_evidence_envelope(
            {"candidate-1": candidate},
            {"candidate-1": evidence},
            evidence_rows=(evidence,),
        )
