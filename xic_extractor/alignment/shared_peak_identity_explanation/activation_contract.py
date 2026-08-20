from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .schema import (
    ACTIVATION_ACCEPTANCE_SCHEMA_VERSION,
    ACTIVATION_DECISION_SCHEMA_VERSION,
    ACTIVATION_MUST_NOT_REGRESS_SCHEMA_VERSION,
    validate_row_tokens,
)

_POSITIVE_MACHINE_LABELS = frozenset(
    {"detected", "rescued", "selected", "present", "provisional_discovery"},
)
_CONTEXT_STATUSES = frozenset({"context_only", "not_evaluable"})
_DIRECT_PRODUCT_STATUSES = frozenset({"auto_activate", "auto_block"})
_SAMPLE_NEGATIVE_CLASSES = frozenset(
    {
        "no_candidate_ms1_evidence",
        "pattern_mismatch",
        "rt_not_explained",
        "local_peak_not_decisive",
    },
)
_WRONG_PEAK_TOKENS = frozenset(
    {
        "family_ms1_overlay_competing_peak_matches_family_consensus",
        "rt_mode_status=mode_conflict",
        "rt_mode_status=mode_split_required",
        "rt_mode_status=consolidation_no_go",
        "peak_hypothesis_status=cross_mode_rescue_blocked",
        "product_selection_action=block_cross_mode_rescue",
    },
)
_PEAK_HYPOTHESIS_FAMILY_BLOCK_TOKENS = frozenset(
    {
        "peak_hypothesis_status=mode_split_required",
        "peak_hypothesis_status=consolidation_no_go",
        "product_selection_action=require_mode_split_before_product",
        "product_selection_action=block_family_promotion",
    },
)
_PEAK_HYPOTHESIS_REVIEW_TOKENS = frozenset(
    {
        "peak_hypothesis_status=tailing_review_only",
        "product_selection_action=require_tailing_review",
        "peak_hypothesis_status=raw_mode_review_only",
        "product_selection_action=require_raw_mode_review",
    },
)
_RAW_MODE_REVIEW_TOKENS = frozenset(
    {
        "peak_hypothesis_status=raw_mode_review_only",
        "product_selection_action=require_raw_mode_review",
    },
)
_DDA_REVIEW_TOKENS = frozenset(
    {
        "dda_opportunity_policy",
        "dda_missing_nl_policy_status=policy_evidence_missing",
    },
)
_DDA_NON_DISPOSITIVE_TOKENS = frozenset(
    {
        "dda_missing_nl_policy_status=not_dispositive",
    },
)
_MATRIX_DRIFT_SUPPORT_TOKENS = frozenset(
    {
        "matrix_rt_drift_status=drift_supported",
        "drift_compatible_status=compatible",
    },
)
_SHAPE_INCOMPLETE_TOKENS = frozenset(
    {
        "formal_shape_metric",
        "shape_metric_inconclusive_apex_or_height",
        "pattern_metric_inconclusive_apex_or_height",
        "qc_ms1_pattern_reference_inconclusive",
    },
)
_PRODUCT_PEAK_HYPOTHESIS_AUTHORITY_SOURCES = frozenset(
    {
        "locked_oracle_manifest",
        "typed_mode_hypothesis_assignment",
    }
)


@dataclass(frozen=True)
class ActivationAcceptanceThresholds:
    max_product_affecting_fraction: float = 0.02
    max_product_affecting_rows: int = 50

    def allowed_rows(self, assessed_rows: int) -> int:
        if assessed_rows <= 0:
            return 0
        fraction_limit = max(
            1,
            int(assessed_rows * self.max_product_affecting_fraction),
        )
        return min(self.max_product_affecting_rows, fraction_limit)


def build_activation_decision_rows(
    support_rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    rows = [_activation_decision(row) for row in support_rows]
    return tuple(
        sorted(rows, key=lambda row: (row["feature_family_id"], row["sample_id"])),
    )


def summarize_activation_acceptance(
    decision_rows: Sequence[Mapping[str, str]],
    *,
    blast_radius_current: bool,
    assessed_85raw_rows: int | None = None,
    assessed_rows_basis: str = "activation_decision_rows_fallback",
    activation_decision_scope: str = "manual_oracle_seed_rows",
    must_not_regress_status: str = "not_assessed",
    must_not_regress_basis: str = "manual_status_flag",
    must_not_regress_failure_reasons: Sequence[str] = (),
    thresholds: ActivationAcceptanceThresholds = ActivationAcceptanceThresholds(),
) -> dict[str, str]:
    assessed_rows = (
        assessed_85raw_rows
        if assessed_85raw_rows is not None
        else len(decision_rows)
    )
    if assessed_85raw_rows is None:
        assessed_rows_basis = "activation_decision_rows_fallback"
    status_counts = Counter(row.get("activation_status", "") for row in decision_rows)
    product_affecting = sum(
        1
        for row in decision_rows
        if row.get("activation_status") in _DIRECT_PRODUCT_STATUSES
    )
    max_allowed = thresholds.allowed_rows(assessed_rows)
    hard_fail_reasons: list[str] = []
    if not blast_radius_current:
        hard_fail_reasons.append("blast_radius_not_current")
    if must_not_regress_status != "pass":
        hard_fail_reasons.append("must_not_regress_not_passed")
    if product_affecting > max_allowed:
        hard_fail_reasons.append("product_affecting_rows_exceed_threshold")

    row = {
        "activation_acceptance_schema_version": ACTIVATION_ACCEPTANCE_SCHEMA_VERSION,
        "activation_mode": "sidecar_to_product_label_contract",
        "activation_decision_scope": activation_decision_scope,
        "blast_radius_current": "TRUE" if blast_radius_current else "FALSE",
        "decision_rows_total": str(len(decision_rows)),
        "assessed_rows": str(assessed_rows),
        "assessed_rows_basis": assessed_rows_basis,
        "product_affecting_rows": str(product_affecting),
        "product_affecting_rows_basis": "activation_decision_rows",
        "auto_activate_count": str(status_counts["auto_activate"]),
        "auto_block_count": str(status_counts["auto_block"]),
        "confidence_only_count": str(status_counts["confidence_only"]),
        "review_required_count": str(status_counts["review_required"]),
        "not_applicable_count": str(
            status_counts["not_applicable"] + status_counts["no_change"],
        ),
        "product_affecting_fraction": _format_fraction(
            product_affecting / assessed_rows if assessed_rows else 0.0,
        ),
        "max_allowed_product_affecting_rows": str(max_allowed),
        "must_not_regress_status": must_not_regress_status,
        "must_not_regress_basis": must_not_regress_basis,
        "must_not_regress_failure_reasons": ";".join(
            must_not_regress_failure_reasons
        ),
        "hard_fail_count": str(len(hard_fail_reasons)),
        "acceptance_status": "pass" if not hard_fail_reasons else "fail",
        "hard_fail_reasons": ";".join(hard_fail_reasons),
        "next_action": (
            "eligible_for_explicit_product_activation_flag"
            if not hard_fail_reasons
            else "review_activation_blast_radius_before_product_wiring"
        ),
    }
    validate_row_tokens(row)
    return row


def load_must_not_regress_expectations(
    path: Path,
) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = tuple(dict(row) for row in reader)
    if not rows:
        raise ValueError("activation must-not-regress TSV must contain rows")
    for row in rows:
        schema_version = row.get("expectation_schema_version", "")
        if schema_version != ACTIVATION_MUST_NOT_REGRESS_SCHEMA_VERSION:
            raise ValueError(
                "unsupported activation must-not-regress schema version: "
                f"{schema_version!r}"
            )
        if (
            not row.get("expectation_id")
            or not row.get("feature_family_id")
            or not row.get("sample_id")
        ):
            raise ValueError(
                "activation must-not-regress rows require expectation_id, "
                "feature_family_id, and sample_id"
            )
    return rows


def evaluate_must_not_regress(
    decision_rows: Sequence[Mapping[str, str]],
    expectation_rows: Sequence[Mapping[str, str]],
) -> tuple[str, tuple[str, ...]]:
    decisions_by_key = {
        (row.get("feature_family_id", ""), row.get("sample_id", "")): row
        for row in decision_rows
    }
    failures: list[str] = []
    for expectation in expectation_rows:
        expectation_id = expectation.get("expectation_id", "")
        key = (
            expectation.get("feature_family_id", ""),
            expectation.get("sample_id", ""),
        )
        decision = decisions_by_key.get(key)
        if decision is None:
            failures.append(f"{expectation_id}:missing_decision")
            continue
        _check_allowed(
            failures,
            expectation_id=expectation_id,
            field="activation_status",
            actual=decision.get("activation_status", ""),
            allowed=_split_tokens(expectation.get("allowed_activation_statuses", "")),
        )
        _check_allowed(
            failures,
            expectation_id=expectation_id,
            field="contract_rule_id",
            actual=decision.get("contract_rule_id", ""),
            allowed=_split_tokens(expectation.get("allowed_contract_rule_ids", "")),
        )
        _check_allowed(
            failures,
            expectation_id=expectation_id,
            field="product_label_candidate",
            actual=decision.get("product_label_candidate", ""),
            allowed=_split_tokens(
                expectation.get("allowed_product_label_candidates", "")
            ),
        )
        _check_disallowed(
            failures,
            expectation_id=expectation_id,
            field="activation_status",
            actual=decision.get("activation_status", ""),
            disallowed=_split_tokens(
                expectation.get("disallowed_activation_statuses", "")
            ),
        )
        _check_disallowed(
            failures,
            expectation_id=expectation_id,
            field="contract_rule_id",
            actual=decision.get("contract_rule_id", ""),
            disallowed=_split_tokens(
                expectation.get("disallowed_contract_rule_ids", "")
            ),
        )
    return ("pass", ()) if not failures else ("fail", tuple(failures))


def infer_85raw_assessed_rows(
    blast_radius_summary_rows: Sequence[Mapping[str, str]],
) -> tuple[int | None, str]:
    """Return the cohort blast-radius denominator without summing class repeats."""
    row_counts = {
        count
        for row in blast_radius_summary_rows
        if row.get("scope") == "all_available_85raw"
        and row.get("artifact_id") == "85raw_alignment_cells"
        for count in (_positive_int(row.get("assessed_row_count")),)
        if count is not None
    }
    if not row_counts:
        return None, "activation_decision_rows_fallback"
    return (
        max(row_counts),
        "blast_radius_summary:all_available_85raw:assessed_row_count",
    )


def _activation_decision(row: Mapping[str, str]) -> dict[str, str]:
    missing = _split_tokens(row.get("missing_machine_evidence", ""))
    metrics = _split_tokens(row.get("observed_machine_metrics", ""))
    evidence_status = row.get("evidence_support_status", "")
    machine_label = row.get("machine_current_label", "")
    family_id = row.get("feature_family_id", "")
    peak_hypothesis_id = _metric_value(metrics, "peak_hypothesis_id=")
    base = {
        "activation_schema_version": ACTIVATION_DECISION_SCHEMA_VERSION,
        "feature_family_id": family_id,
        "candidate_container_id": family_id,
        "sample_id": row.get("sample_id", ""),
        "peak_hypothesis_id": peak_hypothesis_id,
        "machine_current_label": machine_label,
        "evidence_support_status": evidence_status,
        "diagnostic_only": "TRUE",
    }

    if (
        evidence_status in _CONTEXT_STATUSES
        or row.get("sample_id") == "__family_context__"
    ):
        return _row(
            base,
            status="not_applicable",
            action="no_product_change",
            label="unchanged",
            effect="none",
            confidence="none",
            hard_block=False,
            rule="context_or_not_evaluable",
            reason="row is context-only or not evaluable by machine contract",
            review="",
            tokens=(),
        )

    if "family_required_tag_gate" in missing:
        return _row(
            base,
            status="auto_block",
            action="activate_fail",
            label="fail",
            effect="block_family_promotion",
            confidence="high",
            hard_block=True,
            rule="family_required_tag_gate",
            reason=(
                "family has no observed required NL/PI tag, so rescue/promotion "
                "must fail closed"
            ),
            review="",
            tokens=(
                "family_required_tag_gate",
                *_matching(metrics, "dda_missing_nl_policy_status="),
            ),
        )

    if _has_any(metrics, _PEAK_HYPOTHESIS_FAMILY_BLOCK_TOKENS):
        return _row(
            base,
            status="auto_block",
            action="activate_fail",
            label="fail",
            effect="block_family_promotion",
            confidence="high",
            hard_block=True,
            rule="peak_hypothesis_split_required",
            reason=(
                "family must be split into mode-level PeakHypothesis units "
                "before product promotion"
            ),
            review="",
            tokens=(
                *_matching(metrics, "peak_hypothesis_status="),
                *_matching(metrics, "product_selection_action="),
                *_matching(metrics, "product_selection_blocker="),
                *_matching(metrics, "peak_hypothesis_reason="),
            ),
        )

    if _has_wrong_peak_block(metrics, missing):
        return _row(
            base,
            status="auto_block",
            action="block_rescue",
            label="fail",
            effect="block_rescue_cell",
            confidence="high",
            hard_block=True,
            rule="wrong_peak_conflict",
            reason=(
                "machine-observed MS1/QC pattern points to a different peak "
                "than the rescued cell"
            ),
            review="",
            tokens=(
                *_matching(metrics, "ms1_pattern_reason="),
                *_matching(metrics, "ms1_peak_quality_vector_reason="),
                *_matching(metrics, "qc_ms1_reference_status="),
                *_matching(metrics, "rt_mode_status="),
                *_matching(metrics, "selected_mode_role="),
                *_matching(metrics, "family_mode_class="),
                *_matching(metrics, "peak_hypothesis_status="),
                *_matching(metrics, "product_selection_action="),
                *_matching(metrics, "product_selection_blocker="),
                (
                    "rt_pattern_conflict_gate"
                    if "rt_pattern_conflict_gate" in missing
                    else ""
                ),
            ),
        )

    if (
        row.get("negative_evidence_basis_status") == "machine_observed"
        and row.get("negative_evidence_class") in _SAMPLE_NEGATIVE_CLASSES
    ):
        return _row(
            base,
            status="auto_block",
            action="activate_fail",
            label="fail",
            effect="block_rescue_cell",
            confidence="high",
            hard_block=True,
            rule="sample_negative_evidence",
            reason="sample-level machine negative evidence directly supports fail",
            review="",
            tokens=(
                row.get("negative_evidence_class", ""),
                row.get("negative_evidence_detail", ""),
            ),
        )

    if _has_any(missing, _DDA_REVIEW_TOKENS):
        return _row(
            base,
            status="review_required",
            action="require_review",
            label="unchanged",
            effect="review_only",
            confidence="review",
            hard_block=False,
            rule="dda_opportunity_policy_missing",
            reason=(
                "missing DDA NL/PI is not negative evidence until opportunity "
                "is proven"
            ),
            review="dda_opportunity_policy",
            tokens=_matching(missing, "dda"),
        )

    if _matrix_drift_supported_but_shape_incomplete(row, missing, metrics):
        return _row(
            base,
            status="review_required",
            action="require_review",
            label="unchanged",
            effect="review_only",
            confidence="review",
            hard_block=False,
            rule="matrix_rt_drift_requires_shape_support",
            reason=(
                "RT drift support cannot activate a label while MS1 "
                "shape/pattern is incomplete"
            ),
            review="complete_ms1_shape_or_pattern_metric",
            tokens=(
                *_matching(metrics, "matrix_rt_drift_status="),
                *_matching(metrics, "drift_compatible_status="),
                *_matching(missing, "shape"),
                *_matching(missing, "pattern"),
            ),
        )

    if _dda_non_dispositive(metrics):
        return _row(
            base,
            status="confidence_only",
            action="demote_confidence",
            label="unchanged",
            effect="confidence_demote_only",
            confidence="medium",
            hard_block=False,
            rule="dda_missing_nl_not_dispositive",
            reason=(
                "family/sample evidence can explain missing NL/PI, but DDA "
                "stochasticity alone must not change the label"
            ),
            review="",
            tokens=(
                *_matching(metrics, "dda_missing_nl_policy_status="),
                *_matching(metrics, "family_ms2_required_tag_status="),
                *_matching(metrics, "candidate_ms2_pattern_status="),
            ),
        )

    if _has_any(metrics, _PEAK_HYPOTHESIS_REVIEW_TOKENS):
        raw_mode_review = _has_any(metrics, _RAW_MODE_REVIEW_TOKENS)
        if not (
            raw_mode_review
            and _raw_mode_review_has_positive_product_support(
                row,
                missing,
                metrics,
            )
        ):
            return _row(
                base,
                status="review_required",
                action="require_review",
                label="unchanged",
                effect="review_only",
                confidence="review",
                hard_block=False,
                rule=(
                    "peak_hypothesis_raw_mode_review_only"
                    if raw_mode_review
                    else "peak_hypothesis_tailing_review_only"
                ),
                reason=(
                    "raw-overlay-only mode evidence requires candidate-aligned "
                    "MS1 and MS2 support before product activation"
                    if raw_mode_review
                    else (
                        "tailing-confounded mode evidence cannot activate a "
                        "product label without manual review"
                    )
                ),
                review=(
                    "raw_mode_review_only"
                    if raw_mode_review
                    else "tailing_confounded_peak_hypothesis"
                ),
                tokens=(
                    *_matching(metrics, "peak_hypothesis_status="),
                    *_matching(metrics, "product_selection_action="),
                    *_matching(metrics, "product_selection_blocker="),
                    *_matching(metrics, "peak_hypothesis_reason="),
                ),
            )

    if evidence_status == "machine_observed_conflict":
        return _row(
            base,
            status="review_required",
            action="require_review",
            label="unchanged",
            effect="review_only",
            confidence="review",
            hard_block=False,
            rule="unclassified_machine_observed_conflict",
            reason=(
                "machine conflict exists but is not one of the direct product "
                "block rules"
            ),
            review="classify_conflict_before_product_activation",
            tokens=missing,
        )

    if (
        evidence_status == "machine_observed_sufficient"
        and machine_label in _POSITIVE_MACHINE_LABELS
        and row.get("shape_basis_status") == "machine_observed"
        and row.get("pattern_basis_status") == "machine_observed"
    ):
        if not peak_hypothesis_id:
            return _row(
                base,
                status="review_required",
                action="require_review",
                label="unchanged",
                effect="review_only",
                confidence="review",
                hard_block=False,
                rule="peak_hypothesis_unit_required",
                reason=(
                    "positive activation requires a mode-level PeakHypothesis "
                    "unit; feature_family_id is provenance only"
                ),
                review="peak_hypothesis_id_required_for_auto_activation",
                tokens=missing,
            )
        if not (
            _peak_hypothesis_has_product_authority(metrics)
            or _raw_mode_review_has_positive_product_support(row, missing, metrics)
        ):
            return _row(
                base,
                status="review_required",
                action="require_review",
                label="unchanged",
                effect="review_only",
                confidence="review",
                hard_block=False,
                rule="peak_hypothesis_authority_not_product_facing",
                reason=(
                    "product activation requires a typed mode-hypothesis "
                    "assignment or locked oracle manifest; legacy RT-mode "
                    "selection and raw overlay context stay review-only"
                ),
                review="typed_mode_hypothesis_or_locked_oracle_required",
                tokens=(
                    *_matching(metrics, "peak_hypothesis_authority_source="),
                    *_matching(metrics, "peak_hypothesis_status="),
                    *_matching(metrics, "product_selection_action="),
                    *_matching(metrics, "peak_hypothesis_reason="),
                ),
            )
        return _row(
            base,
            status="auto_activate",
            action="activate_pass",
            label="pass",
            effect="accept_label_or_rescue",
            confidence="high",
            hard_block=False,
            rule="machine_observed_sufficient_positive_identity",
            reason=(
                "machine-observed RT/shape/pattern evidence is sufficient and "
                "current label is positive"
            ),
            review="",
            tokens=(
                row.get("shape_basis_status", ""),
                row.get("pattern_basis_status", ""),
                row.get("opportunity_basis_status", ""),
            ),
        )

    if evidence_status in {
        "machine_observed_partial",
        "machine_proxy_only",
        "manual_derived_only",
    }:
        return _row(
            base,
            status="review_required",
            action="require_review",
            label="unchanged",
            effect="review_only",
            confidence="review",
            hard_block=False,
            rule="insufficient_machine_observed_basis",
            reason=(
                "sidecar evidence is not strong enough to change product label "
                "automatically"
            ),
            review="machine_observed_basis_incomplete",
            tokens=missing,
        )

    return _row(
        base,
        status="no_change",
        action="no_product_change",
        label="unchanged",
        effect="none",
        confidence="none",
        hard_block=False,
        rule="no_activation_rule_matched",
        reason="no product activation rule matched",
        review="",
        tokens=missing,
    )


def _row(
    base: Mapping[str, str],
    *,
    status: str,
    action: str,
    label: str,
    effect: str,
    confidence: str,
    hard_block: bool,
    rule: str,
    reason: str,
    review: str,
    tokens: Sequence[str],
) -> dict[str, str]:
    row = {
        **base,
        "activation_unit_scope": _activation_unit_scope(
            peak_hypothesis_id=base.get("peak_hypothesis_id", ""),
            status=status,
            action=action,
            effect=effect,
            rule=rule,
        ),
        "activation_status": status,
        "activation_action": action,
        "product_label_candidate": label,
        "product_effect": effect,
        "activation_confidence": confidence,
        "hard_product_block": "TRUE" if hard_block else "FALSE",
        "contract_rule_id": rule,
        "activation_reason": reason,
        "required_review_reason": review,
        "source_evidence_tokens": ";".join(token for token in tokens if token),
    }
    validate_row_tokens(row)
    return row


def _matrix_drift_supported_but_shape_incomplete(
    row: Mapping[str, str],
    missing: Sequence[str],
    metrics: Sequence[str],
) -> bool:
    if not _has_any(metrics, _MATRIX_DRIFT_SUPPORT_TOKENS):
        return False
    if row.get("shape_basis_status") != "machine_observed":
        return True
    return _has_any(missing, _SHAPE_INCOMPLETE_TOKENS)


def _has_wrong_peak_block(metrics: Sequence[str], missing: Sequence[str]) -> bool:
    if _has_any(metrics, _WRONG_PEAK_TOKENS) or "rt_pattern_conflict_gate" in missing:
        return True
    if "qc_ms1_reference_status=conflict" not in metrics:
        return False
    return _has_any(
        metrics,
        frozenset(
            {
                "candidate_ms2_pattern_status=conflict",
                "ms1_pattern_status=conflict",
            },
        ),
    ) or _has_any(
        missing,
        frozenset(
            {
                "matrix_rt_drift_policy_not_supportive",
                "peak_hypothesis_not_supportive",
                "rt_mode_not_supportive",
            },
        ),
    )


def _split_tokens(value: object) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(value or "").split(";") if part.strip())


def _has_any(values: Sequence[str], candidates: frozenset[str]) -> bool:
    return any(
        value == candidate or candidate in value
        for value in values
        for candidate in candidates
    )


def _dda_non_dispositive(metrics: Sequence[str]) -> bool:
    if _has_any(metrics, _DDA_NON_DISPOSITIVE_TOKENS):
        return True
    return (
        "family_ms2_required_tag_status=observed_in_family" in metrics
        and "candidate_ms2_pattern_status=not_observed" in metrics
    )


def _peak_hypothesis_has_product_authority(metrics: Sequence[str]) -> bool:
    source = _metric_value(metrics, "peak_hypothesis_authority_source=")
    return source in _PRODUCT_PEAK_HYPOTHESIS_AUTHORITY_SOURCES


def _raw_mode_review_has_positive_product_support(
    row: Mapping[str, str],
    missing: Sequence[str],
    metrics: Sequence[str],
) -> bool:
    if not _has_any(metrics, _RAW_MODE_REVIEW_TOKENS):
        return False
    if missing:
        return False
    if row.get("shape_basis_status") != "machine_observed":
        return False
    if row.get("pattern_basis_status") != "machine_observed":
        return False
    return (
        "ms1_pattern_status=supportive" in metrics
        and "candidate_ms2_pattern_status=supportive" in metrics
    )


def _matching(values: Sequence[str], prefix: str) -> tuple[str, ...]:
    return tuple(value for value in values if value.startswith(prefix))


def _metric_value(values: Sequence[str], prefix: str) -> str:
    for value in values:
        if value.startswith(prefix):
            return value[len(prefix) :]
    return ""


def _activation_unit_scope(
    *,
    peak_hypothesis_id: str,
    status: str,
    action: str,
    effect: str,
    rule: str,
) -> str:
    if status in {"not_applicable", "no_change"}:
        return "not_applicable"
    if effect == "block_family_promotion" or rule == "family_required_tag_gate":
        return "candidate_container"
    if effect == "block_rescue_cell" or action == "block_rescue":
        return "sample_cell"
    if peak_hypothesis_id:
        return "peak_hypothesis"
    return "legacy_family_row"


def _format_fraction(value: float) -> str:
    return f"{value:.6f}"


def _check_allowed(
    failures: list[str],
    *,
    expectation_id: str,
    field: str,
    actual: str,
    allowed: Sequence[str],
) -> None:
    if allowed and actual not in allowed:
        failures.append(f"{expectation_id}:{field}={actual}:not_allowed")


def _check_disallowed(
    failures: list[str],
    *,
    expectation_id: str,
    field: str,
    actual: str,
    disallowed: Sequence[str],
) -> None:
    if actual in disallowed:
        failures.append(f"{expectation_id}:{field}={actual}:disallowed")


def _positive_int(value: object) -> int | None:
    try:
        count = int(str(value or "").strip())
    except ValueError:
        return None
    return count if count > 0 else None
