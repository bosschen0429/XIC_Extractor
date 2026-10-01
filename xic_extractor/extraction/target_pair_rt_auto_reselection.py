from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from xic_extractor.configuration.models import Target
from xic_extractor.extraction.paired_area_ratio_projection import (
    MIN_PAIRED_AREA_RATIO_REFERENCE_POINTS,
    PAIRED_AREA_RATIO_BASIS,
    PAIRED_AREA_RATIO_ROBUST_BASIS,
    assess_paired_area_ratio_values,
)
from xic_extractor.extraction.target_sample_applicability import (
    target_sample_exclusion_reasons,
)
from xic_extractor.output.target_pair_rt_auto_reselection import (
    write_target_pair_rt_auto_reselection_summary_tsv,
    write_target_pair_rt_auto_reselection_tsv,
)
from xic_extractor.peak_detection.model_selection import expected_diff_stable_row_id
from xic_extractor.target_pair_rt_calibration import (
    TargetPairRTCalibrationRow,
    load_target_pair_rt_calibration,
)

_PRODUCT_CALIBRATION_LEVELS = frozenset({"biological_transfer", "row_approved"})
_PRODUCT_TRANSFER_STATUSES = frozenset({"validated", "row_approved"})
_MIN_PAIRED_AREA_RATIO_REFERENCE_POINTS = MIN_PAIRED_AREA_RATIO_REFERENCE_POINTS
_PAIRED_AREA_RATIO_BASIS = PAIRED_AREA_RATIO_BASIS
_PAIRED_AREA_RATIO_ROBUST_BASIS = PAIRED_AREA_RATIO_ROBUST_BASIS
_PAIR_RT_DELTA_REVIEW_TOLERANCE_MIN = 0.75


@dataclass(frozen=True)
class _PairedAreaRatioReferencePoint:
    sample_name: str
    ratio: float


class FileResultForTargetPairRT(Protocol):
    @property
    def sample_name(self) -> str: ...

    @property
    def results(self) -> Mapping[str, Any]: ...

    @property
    def peak_candidate_rows(self) -> Sequence[Mapping[str, str]]: ...


def write_target_pair_rt_auto_reselection_for_file_results(
    path: Path,
    file_results: Sequence[FileResultForTargetPairRT],
    *,
    targets: Sequence[Target],
    calibration_path: Path,
    target_config_hash: str | None = None,
    enabled: bool = True,
) -> None:
    if not enabled:
        return
    calibration_rows = load_target_pair_rt_calibration(
        calibration_path,
        expected_target_config_hash=target_config_hash,
    )
    rows = build_target_pair_rt_auto_reselection_rows(
        file_results,
        targets=targets,
        calibration_rows=calibration_rows,
    )
    write_target_pair_rt_auto_reselection_tsv(path, rows)
    write_target_pair_rt_auto_reselection_summary_tsv(
        path.with_name("target_pair_rt_auto_reselection_summary.tsv"),
        summarize_target_pair_rt_auto_reselection_rows(rows),
    )


def build_target_pair_rt_auto_reselection_rows(
    file_results: Sequence[FileResultForTargetPairRT],
    *,
    targets: Sequence[Target],
    calibration_rows: Sequence[TargetPairRTCalibrationRow],
) -> list[dict[str, str]]:
    calibration_by_key = {
        (row.target_label, row.paired_istd_label): row for row in calibration_rows
    }
    targets_by_label = {target.label: target for target in targets}
    area_ratio_reference = _paired_area_ratio_reference(file_results, targets)
    out: list[dict[str, str]] = []
    for file_result in file_results:
        candidate_lookup = _candidate_lookup(file_result)
        for target in targets:
            paired_label = target.label if target.is_istd else target.istd_pair
            calibration = calibration_by_key.get((target.label, paired_label))
            if calibration is None:
                continue
            result = file_result.results.get(target.label)
            out.append(
                _row_from_result(
                    file_result.sample_name,
                    target,
                    result,
                    calibration,
                    candidate_lookup=candidate_lookup,
                    area_ratio_reference=area_ratio_reference,
                    paired_istd_result=file_result.results.get(paired_label),
                    paired_istd_target=targets_by_label.get(paired_label),
                )
            )
    return out


def summarize_target_pair_rt_auto_reselection_rows(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    limited = sum(
        1
        for row in rows
        if row.get("evidence_comparison_policy") == "limited_evidence_shadow"
    )
    inconclusive = sum(
        1 for row in rows if row.get("selection_status") == "inconclusive"
    )
    blocked_diff = sum(
        1 for row in rows if row.get("selection_status") == "blocked_diff"
    )
    proposed = sum(
        1
        for row in rows
        if row.get("selection_action") == "shadow_auto_reselect_proposed"
    )
    auto_reselected = sum(
        1 for row in rows if row.get("selection_action") == "auto_reselected"
    )
    area_ratio_within_active = sum(
        1
        for row in rows
        if row.get("paired_area_ratio_status") == "within_robust_range"
    )
    area_ratio_outside_active = sum(
        1
        for row in rows
        if row.get("paired_area_ratio_status") == "outside_robust_range"
    )
    area_ratio_inconclusive = sum(
        1
        for row in rows
        if row.get("paired_area_ratio_status") == "inconclusive"
    )
    blocked = sum(
        1 for row in rows if row.get("selection_action") == "auto_reselect_blocked"
    )
    changed = sum(
        1
        for row in rows
        if row.get("previous_candidate_id")
        and row.get("selected_candidate_id")
        and row.get("previous_candidate_id") != row.get("selected_candidate_id")
    )
    false_positive_review_required = sum(
        1
        for row in rows
        if row.get("false_positive_review_status")
        == "false_positive_review_required"
    )
    row_approval_candidate = sum(
        1
        for row in rows
        if row.get("false_positive_review_status") == "row_approval_candidate"
    )
    product_switch_accepted = sum(
        1
        for row in rows
        if row.get("false_positive_review_status") == "product_switch_accepted"
    )
    strata = sorted(
        {
            reason
            for row in rows
            if row.get("false_positive_review_status") != "not_applicable"
            for reason in row.get("false_positive_review_reasons", "").split(";")
            if reason
        }
    )
    return {
        "limited_evidence_shadow_count": str(limited),
        "inconclusive_count": str(inconclusive),
        "blocked_diff_count": str(blocked_diff),
        "shadow_auto_reselect_proposed_count": str(proposed),
        "auto_reselect_blocked_count": str(blocked),
        "changed_row_denominator": str(changed),
        "false_positive_strata": ";".join(strata),
        "product_switch_allowed_true_count": str(
            sum(
                1
                for row in rows
                if str(row.get("product_switch_allowed", "")).upper() == "TRUE"
            )
        ),
        "auto_reselected_count": str(auto_reselected),
        "paired_area_ratio_within_active_count": str(area_ratio_within_active),
        "paired_area_ratio_outside_active_count": str(area_ratio_outside_active),
        "paired_area_ratio_inconclusive_count": str(area_ratio_inconclusive),
        "false_positive_review_required_count": str(
            false_positive_review_required
        ),
        "row_approval_candidate_count": str(row_approval_candidate),
        "product_switch_accepted_count": str(product_switch_accepted),
    }


def _row_from_result(
    sample_name: str,
    target: Target,
    result: object | None,
    calibration: TargetPairRTCalibrationRow,
    *,
    candidate_lookup: Mapping[tuple[str, str], Mapping[str, str]],
    area_ratio_reference: Mapping[
        tuple[str, str], tuple[_PairedAreaRatioReferencePoint, ...]
    ],
    paired_istd_result: object | None,
    paired_istd_target: Target | None,
) -> dict[str, str]:
    model = getattr(result, "model_selection_result", None)
    previous_candidate_id = getattr(model, "legacy_selected_candidate_id", "")
    selected_candidate_id = getattr(model, "selected_candidate_id", "")
    if not previous_candidate_id:
        selected_hypothesis = getattr(result, "selected_hypothesis", None)
        previous_candidate_id = getattr(selected_hypothesis, "hypothesis_id", "")
    if not selected_candidate_id:
        selected_candidate_id = previous_candidate_id
    selection_status = getattr(model, "selection_status", "inconclusive")
    morphology_override = False
    if (
        selection_status == "expected_diff"
        and previous_candidate_id != selected_candidate_id
    ):
        selected_candidate_id, morphology_override = (
            _morphology_supported_candidate_id(
                sample_name,
                target,
                selected_candidate_id,
                candidate_lookup=candidate_lookup,
                paired_istd_result=paired_istd_result,
                area_ratio_reference=area_ratio_reference,
            )
        )
    previous_rt = _candidate_rt(
        candidate_lookup,
        target.label,
        previous_candidate_id,
        fallback=(
            getattr(result, "reported_rt", None)
            if previous_candidate_id == selected_candidate_id
            else None
        ),
        allow_id_fallback=True,
    )
    selected_candidate_lookup_missing = (
        bool(selected_candidate_id)
        and selected_candidate_id != previous_candidate_id
        and (target.label, selected_candidate_id) not in candidate_lookup
    )
    selected_rt = _candidate_rt(
        candidate_lookup,
        target.label,
        selected_candidate_id,
        fallback=(
            None
            if selected_candidate_lookup_missing
            else getattr(result, "reported_rt", None)
        ),
        allow_id_fallback=False,
    )
    paired_istd_rt = getattr(paired_istd_result, "reported_rt", None)
    observed_delta = _observed_delta(selected_rt, paired_istd_rt)
    delta_error = (
        observed_delta - calibration.rt_delta_median_min
        if observed_delta is not None
        else None
    )
    evidence_policy = getattr(
        model,
        "evidence_comparison_policy",
        "limited_evidence_shadow",
    )
    activation_block_reasons = _activation_block_reasons(calibration)
    sample_exclusion_reasons = target_sample_exclusion_reasons(target, sample_name)
    role_policy = _role_policy(target, paired_istd_target, paired_istd_result)
    target_counted_detection = _target_counted_detection(result)
    product_switch_allowed = (
        bool(getattr(model, "product_switch_allowed", False))
        and not morphology_override
    )
    diff_reasons = tuple(getattr(model, "diff_reasons", ()))
    if morphology_override:
        diff_reasons = tuple(
            dict.fromkeys((*diff_reasons, "chrom_morphology_area_ratio_override"))
        )
    action, gate_decision, block_reason = _shadow_action(
        previous_candidate_id,
        selected_candidate_id,
        selection_status,
        (*activation_block_reasons, *sample_exclusion_reasons),
        product_switch_allowed=product_switch_allowed,
        target_counted_detection=target_counted_detection,
        role_policy=role_policy,
        diff_reasons=diff_reasons,
    )
    trace_group_id = getattr(model, "trace_group_id", "")
    stable_row_id = getattr(model, "stable_row_id", "")
    if morphology_override and previous_candidate_id and selected_candidate_id:
        stable_row_id = expected_diff_stable_row_id(
            legacy_selected_candidate_id=previous_candidate_id,
            successor_selected_candidate_id=selected_candidate_id,
        )
    row = {
        "sample_name": sample_name,
        "target_label": target.label,
        "role": "ISTD" if target.is_istd else "Analyte",
        "trace_group_id": trace_group_id,
        "previous_candidate_id": previous_candidate_id,
        "selected_candidate_id": selected_candidate_id,
        "selection_action": action,
        "selection_basis": _selection_basis(
            calibration,
            target,
            morphology_override=morphology_override,
        ),
        "selection_status": str(selection_status),
        "product_switch_allowed": "TRUE" if action == "auto_reselected" else "FALSE",
        "target_counted_detection": "TRUE"
        if target_counted_detection
        else "FALSE",
        "expected_diff_stable_row_id": stable_row_id,
        "evidence_comparison_policy": str(evidence_policy),
        "previous_candidate_rt": _format_optional_float(previous_rt),
        "selected_candidate_rt": _format_optional_float(selected_rt),
        "paired_istd_rt": _format_optional_float(paired_istd_rt),
        "pair_rt_delta_expected": _format_optional_float(
            calibration.rt_delta_median_min
        ),
        "pair_rt_delta_observed": _format_optional_float(observed_delta),
        "pair_rt_delta_error": _format_optional_float(delta_error),
        **_paired_area_ratio_fields(
            sample_name,
            target,
            result,
            selected_candidate_id,
            selected_candidate_lookup_missing=selected_candidate_lookup_missing,
            candidate_lookup=candidate_lookup,
            paired_istd_result=paired_istd_result,
            area_ratio_reference=area_ratio_reference,
        ),
        "calibration_source": calibration.delta_source,
        "calibration_status": calibration.calibration_status,
        "missing_ms2_explanation": _missing_ms2_explanation(result),
        "role_policy": role_policy,
        "gate_decision": gate_decision,
        "block_reason": block_reason,
    }
    status, reasons = _false_positive_review(
        row,
        previous_candidate_id=previous_candidate_id,
        selected_candidate_id=selected_candidate_id,
    )
    row["false_positive_review_status"] = status
    row["false_positive_review_reasons"] = ";".join(reasons)
    return row


def _candidate_lookup(
    file_result: FileResultForTargetPairRT,
) -> dict[tuple[str, str], Mapping[str, str]]:
    lookup: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in file_result.peak_candidate_rows:
        target_label = str(row.get("target_label", ""))
        candidate_id = str(row.get("candidate_id", ""))
        if target_label and candidate_id:
            lookup[(target_label, candidate_id)] = row
    return lookup


def _paired_area_ratio_reference(
    file_results: Sequence[FileResultForTargetPairRT],
    targets: Sequence[Target],
) -> dict[tuple[str, str], tuple[_PairedAreaRatioReferencePoint, ...]]:
    references: dict[tuple[str, str], list[_PairedAreaRatioReferencePoint]] = {}
    for file_result in file_results:
        for target in targets:
            if target.is_istd or not target.istd_pair:
                continue
            if target_sample_exclusion_reasons(target, file_result.sample_name):
                continue
            result = file_result.results.get(target.label)
            paired_istd_result = file_result.results.get(target.istd_pair)
            if not _target_counted_detection(
                result
            ) or not _credible_paired_istd_result(paired_istd_result):
                continue
            area = _optional_float(getattr(result, "reported_peak_area", None))
            paired_area = _optional_float(
                getattr(paired_istd_result, "reported_peak_area", None)
            )
            if not _positive(area) or not _positive(paired_area):
                continue
            assert area is not None
            assert paired_area is not None
            references.setdefault((target.label, target.istd_pair), []).append(
                _PairedAreaRatioReferencePoint(
                    sample_name=file_result.sample_name,
                    ratio=area / paired_area,
                )
            )
    return {key: tuple(value) for key, value in references.items()}


def _paired_area_ratio_fields(
    sample_name: str,
    target: Target,
    result: object | None,
    selected_candidate_id: str,
    *,
    selected_candidate_lookup_missing: bool,
    candidate_lookup: Mapping[tuple[str, str], Mapping[str, str]],
    paired_istd_result: object | None,
    area_ratio_reference: Mapping[
        tuple[str, str], tuple[_PairedAreaRatioReferencePoint, ...]
    ],
) -> dict[str, str]:
    empty = {
        "paired_area_ratio_observed": "",
        "paired_area_ratio_reference_n": "0",
        "paired_area_ratio_reference_min": "",
        "paired_area_ratio_reference_median": "",
        "paired_area_ratio_reference_max": "",
        "paired_area_ratio_status": "not_applicable",
        "paired_area_ratio_basis": "",
        "paired_area_ratio_robust_status": "",
        "paired_area_ratio_robust_reference_min": "",
        "paired_area_ratio_robust_reference_median": "",
        "paired_area_ratio_robust_reference_max": "",
        "paired_area_ratio_robust_reference_mad": "",
        "paired_area_ratio_robust_basis": "",
    }
    if target.is_istd or not target.istd_pair:
        return empty
    paired_area = _optional_float(
        getattr(paired_istd_result, "reported_peak_area", None)
    )
    if not _positive(paired_area):
        return {**empty, "paired_area_ratio_status": "missing_istd_area"}
    if selected_candidate_lookup_missing:
        return {**empty, "paired_area_ratio_status": "missing_candidate_area"}
    selected_area = _candidate_area(
        candidate_lookup,
        target.label,
        selected_candidate_id,
        fallback=getattr(result, "reported_peak_area", None),
    )
    if not _positive(selected_area):
        return {**empty, "paired_area_ratio_status": "missing_candidate_area"}
    assert selected_area is not None
    assert paired_area is not None
    observed = selected_area / paired_area
    reference = tuple(
        point.ratio
        for point in area_ratio_reference.get((target.label, target.istd_pair), ())
        if point.sample_name != sample_name and _positive(point.ratio)
    )
    common = {
        **empty,
        "paired_area_ratio_observed": _format_optional_float(observed),
        "paired_area_ratio_reference_n": str(len(reference)),
        "paired_area_ratio_basis": _PAIRED_AREA_RATIO_BASIS,
        "paired_area_ratio_robust_basis": _PAIRED_AREA_RATIO_ROBUST_BASIS,
    }
    if len(reference) < _MIN_PAIRED_AREA_RATIO_REFERENCE_POINTS:
        return {
            **common,
            "paired_area_ratio_status": "inconclusive",
            "paired_area_ratio_robust_status": "inconclusive",
        }

    assessment = assess_paired_area_ratio_values(
        observed_ratio=observed,
        reference_ratios=reference,
    )
    return {
        **common,
        "paired_area_ratio_reference_min": _format_optional_float(
            assessment.reference_min
        ),
        "paired_area_ratio_reference_median": _format_optional_float(
            assessment.reference_median
        ),
        "paired_area_ratio_reference_max": _format_optional_float(
            assessment.reference_max
        ),
        "paired_area_ratio_status": assessment.status,
        "paired_area_ratio_robust_status": assessment.robust_status,
        "paired_area_ratio_robust_reference_min": _format_optional_float(
            assessment.robust_reference_min
        ),
        "paired_area_ratio_robust_reference_median": _format_optional_float(
            assessment.robust_reference_median
        ),
        "paired_area_ratio_robust_reference_max": _format_optional_float(
            assessment.robust_reference_max
        ),
        "paired_area_ratio_robust_reference_mad": _format_optional_float(
            assessment.robust_reference_mad
        ),
    }


def _morphology_supported_candidate_id(
    sample_name: str,
    target: Target,
    selected_candidate_id: str,
    *,
    candidate_lookup: Mapping[tuple[str, str], Mapping[str, str]],
    paired_istd_result: object | None,
    area_ratio_reference: Mapping[
        tuple[str, str], tuple[_PairedAreaRatioReferencePoint, ...]
    ],
) -> tuple[str, bool]:
    if target.is_istd or not target.istd_pair:
        return selected_candidate_id, False
    if _candidate_has_source(
        candidate_lookup.get((target.label, selected_candidate_id)),
        "chrom_peak_segment",
    ):
        return selected_candidate_id, False
    paired_area = _optional_float(
        getattr(paired_istd_result, "reported_peak_area", None)
    )
    if not _positive(paired_area):
        return selected_candidate_id, False
    assert paired_area is not None
    reference = tuple(
        point.ratio
        for point in area_ratio_reference.get((target.label, target.istd_pair), ())
        if point.sample_name != sample_name and _positive(point.ratio)
    )
    if len(reference) < _MIN_PAIRED_AREA_RATIO_REFERENCE_POINTS:
        return selected_candidate_id, False
    ordered_reference = sorted(reference)
    ref_min = ordered_reference[0]
    ref_median = _median(ordered_reference)
    ref_max = ordered_reference[-1]

    candidates: list[tuple[float, float, float, str]] = []
    for (target_label, candidate_id), row in candidate_lookup.items():
        if target_label != target.label:
            continue
        if not _candidate_has_source(row, "chrom_peak_segment"):
            continue
        if _has_hard_morphology_contraindication(row):
            continue
        area = _candidate_row_area(row)
        if not _positive(area):
            continue
        assert area is not None
        observed = area / paired_area
        if not (ref_min <= observed <= ref_max):
            continue
        candidates.append(
            (
                abs(observed - ref_median),
                _candidate_rt_prior_concern_rank(row),
                -area,
                candidate_id,
            )
        )
    if not candidates:
        return selected_candidate_id, False

    chosen_id = min(candidates)[3]
    return chosen_id, chosen_id != selected_candidate_id


def _shadow_action(
    previous_candidate_id: str,
    selected_candidate_id: str,
    selection_status: str,
    activation_block_reasons: Sequence[str],
    *,
    product_switch_allowed: bool,
    target_counted_detection: bool,
    role_policy: str,
    diff_reasons: Sequence[str],
) -> tuple[str, str, str]:
    reasons = list(activation_block_reasons)
    hard_shadow_blocks = _hard_shadow_block_reasons(reasons)
    if hard_shadow_blocks:
        hard_shadow_blocks.extend(diff_reasons)
        return (
            "auto_reselect_blocked",
            "no_go",
            ";".join(dict.fromkeys(hard_shadow_blocks)),
        )
    if (
        previous_candidate_id != selected_candidate_id
        and selection_status == "expected_diff"
    ):
        if product_switch_allowed:
            product_blockers = _product_switch_block_reasons(
                reasons,
                role_policy=role_policy,
            )
            if not target_counted_detection:
                product_blockers.append("target_projection_not_counted")
            if product_blockers:
                product_blockers.extend(diff_reasons)
                return (
                    "auto_reselect_blocked",
                    "no_go",
                    ";".join(dict.fromkeys(product_blockers)),
                )
            return "auto_reselected", "promote", ""
        reasons.append("product_switch_not_activated")
        return (
            "shadow_auto_reselect_proposed",
            "externalize",
            ";".join(dict.fromkeys(reasons)),
        )
    if selection_status in {"blocked_diff", "inconclusive"} or reasons:
        reasons.extend(diff_reasons)
        return (
            "auto_reselect_blocked",
            "no_go" if reasons else "defer",
            ";".join(dict.fromkeys(reasons)) or str(selection_status),
        )
    return "none", "defer", ""


def _false_positive_review(
    row: Mapping[str, str],
    *,
    previous_candidate_id: str,
    selected_candidate_id: str,
) -> tuple[str, tuple[str, ...]]:
    if previous_candidate_id == selected_candidate_id:
        return "not_applicable", ()
    if row.get("role") != "Analyte":
        return "not_applicable", ()
    if row.get("role_policy") != "paired_analyte":
        return "false_positive_review_required", (
            row.get("role_policy", "") or "paired_analyte_not_supported",
        )

    hard_reasons: list[str] = []
    review_reasons: list[str] = []
    area_ratio_status = row.get("paired_area_ratio_status", "")
    for reason in _split_semicolon(row.get("block_reason", "")):
        if reason.startswith("target_sample_applicability:"):
            hard_reasons.append(reason)
    if row.get("selected_candidate_rt", "") == "":
        hard_reasons.append("selected_candidate_lookup_missing")
    if area_ratio_status != "within_robust_range":
        hard_reasons.append(
            f"paired_area_ratio:{area_ratio_status or 'not_assessed'}"
        )
    ms2_explanation = row.get("missing_ms2_explanation", "")
    delta_error = _optional_float(row.get("pair_rt_delta_error", ""))
    if (
        delta_error is not None
        and abs(delta_error) > _PAIR_RT_DELTA_REVIEW_TOLERANCE_MIN
        and ms2_explanation in {"contradicted", "not_observed"}
    ):
        hard_reasons.append("paired_rt_delta:outside_expected")
    if ms2_explanation == "contradicted":
        review_reasons.append("ms2_nl_contradicted")
    elif ms2_explanation == "not_observed":
        review_reasons.append("dda_missing_ms2_not_observed")

    action = row.get("selection_action", "")
    if action == "shadow_auto_reselect_proposed":
        review_reasons.append("row_specific_expected_diff_required")
    elif action == "auto_reselect_blocked":
        hard_reasons.append("product_gate_blocked")

    reasons = tuple(dict.fromkeys((*hard_reasons, *review_reasons)))
    if hard_reasons:
        return "false_positive_review_required", reasons
    if action == "shadow_auto_reselect_proposed":
        return "row_approval_candidate", reasons
    if action == "auto_reselected":
        return "product_switch_accepted", reasons
    return "not_applicable", reasons


def _hard_shadow_block_reasons(reasons: Sequence[str]) -> list[str]:
    return [
        reason
        for reason in reasons
        if reason
        in {
            "target_config_hash_mismatch",
            "source_hash_mismatch",
        }
        or reason.startswith("calibration_status:")
        or reason.startswith("target_sample_applicability:")
    ]


def _product_switch_block_reasons(
    activation_block_reasons: Sequence[str],
    *,
    role_policy: str,
) -> list[str]:
    reasons = list(activation_block_reasons)
    if role_policy == "unpaired_analyte":
        reasons.append("target_role_not_auto_reselection_eligible")
    elif role_policy == "paired_analyte_missing_credible_istd":
        reasons.append("paired_istd_not_credible_in_sample")
    return list(dict.fromkeys(reasons))


def _activation_block_reasons(
    calibration: TargetPairRTCalibrationRow,
) -> tuple[str, ...]:
    reasons = [
        reason
        for reason in calibration.activation_block_reason.split(";")
        if reason
    ]
    if calibration.target_hash_status == "mismatch":
        reasons.append("target_config_hash_mismatch")
    if calibration.source_hash_status == "mismatch":
        reasons.append("source_hash_mismatch")
    elif calibration.source_hash_status == "missing":
        reasons.append("missing_source_hash")
    if calibration.calibration_status != "usable":
        reasons.append(f"calibration_status:{calibration.calibration_status}")
    delta_source_block = _delta_source_block_reason(calibration)
    if delta_source_block:
        reasons.append(delta_source_block)
    if calibration.calibration_level not in _PRODUCT_CALIBRATION_LEVELS:
        reasons.append(f"calibration_level:{calibration.calibration_level}")
    if calibration.product_transfer_status not in _PRODUCT_TRANSFER_STATUSES:
        reasons.append(f"product_transfer_status:{calibration.product_transfer_status}")
    return tuple(dict.fromkeys(reasons))


def _selection_basis(
    calibration: TargetPairRTCalibrationRow,
    target: Target,
    *,
    morphology_override: bool = False,
) -> str:
    if target.is_istd:
        basis = "role_aware_istd_shadow"
    elif target.istd_pair:
        basis = f"paired_rt_{calibration.delta_source}"
    else:
        basis = "unpaired_target_no_auto_reselection"
    if morphology_override:
        return f"{basis};chrom_morphology_area_ratio"
    return basis


def _delta_source_block_reason(
    calibration: TargetPairRTCalibrationRow,
) -> str:
    if calibration.delta_source == "config_fallback":
        return "delta_source:config_fallback"
    if (
        calibration.delta_source == "mixstds_clean_standard"
        and calibration.calibration_level != "row_approved"
    ):
        return "delta_source:mixstds_clean_standard"
    return ""


def _role_policy(
    target: Target,
    paired_istd_target: Target | None,
    paired_istd_result: object | None,
) -> str:
    if target.is_istd:
        return "istd"
    if (
        target.istd_pair
        and paired_istd_target is not None
        and _credible_paired_istd_result(paired_istd_result)
    ):
        return "paired_analyte"
    if target.istd_pair:
        return "paired_analyte_missing_credible_istd"
    return "unpaired_analyte"


def _credible_paired_istd_result(result: object | None) -> bool:
    if result is None:
        return False
    projection = getattr(result, "targeted_product_projection", None)
    if projection is not None and hasattr(projection, "counted_detection"):
        return bool(getattr(projection, "counted_detection"))
    rt = _optional_float(getattr(result, "reported_rt", None))
    area = _optional_float(getattr(result, "reported_peak_area", None))
    if area is not None:
        return _finite(rt) and area > 0
    return _finite(rt)


def _target_counted_detection(result: object | None) -> bool:
    projection = getattr(result, "targeted_product_projection", None)
    if projection is None or not hasattr(projection, "counted_detection"):
        return True
    return bool(getattr(projection, "counted_detection"))


def _candidate_rt(
    lookup: Mapping[tuple[str, str], Mapping[str, str]],
    target_label: str,
    candidate_id: str,
    *,
    fallback: object | None,
    allow_id_fallback: bool = False,
) -> float | None:
    row = lookup.get((target_label, candidate_id))
    if row is not None:
        parsed = _optional_float(row.get("rt_apex_min", ""))
        if parsed is not None:
            return parsed
    if allow_id_fallback:
        parsed = _candidate_id_rt_apex(candidate_id)
        if parsed is not None:
            return parsed
    return _optional_float(fallback)


def _candidate_area(
    lookup: Mapping[tuple[str, str], Mapping[str, str]],
    target_label: str,
    candidate_id: str,
    *,
    fallback: object | None,
) -> float | None:
    row = lookup.get((target_label, candidate_id))
    if row is not None:
        parsed = _candidate_row_area(row)
        if parsed is not None:
            return parsed
    return _optional_float(fallback)


def _candidate_row_area(row: Mapping[str, str]) -> float | None:
    morphology_area = _optional_float(row.get("area_ms1_morphology", ""))
    if morphology_area is not None:
        return morphology_area
    return _optional_float(row.get("area_raw_counts_seconds", ""))


def _candidate_has_source(
    row: Mapping[str, str] | None,
    source: str,
) -> bool:
    if row is None:
        return False
    return source in _split_semicolon(row.get("proposal_sources", ""))


def _has_hard_morphology_contraindication(row: Mapping[str, str]) -> bool:
    concerns = set(_split_semicolon(row.get("concern_labels", "")))
    caps = set(_split_semicolon(row.get("cap_labels", "")))
    quality_flags = set(_split_semicolon(row.get("quality_flags", "")))
    support = set(_split_semicolon(row.get("support_labels", "")))
    if concerns.intersection({"rt_prior_far", "rt_centrality_poor"}):
        return True
    if caps.intersection(
        {"rt_window_cap", "trace_quality_cap", "hard_quality_flag_cap"}
    ):
        return True
    if quality_flags.intersection(
        {
            "edge_clipped",
            "low_scan_count",
            "low_scan_support",
            "poor_edge_recovery",
            "too_short",
        }
    ):
        return True
    return not support.intersection({"shape_clean", "trace_clean", "trace_coherent"})


def _candidate_rt_prior_concern_rank(row: Mapping[str, str]) -> float:
    concerns = set(_split_semicolon(row.get("concern_labels", "")))
    support = set(_split_semicolon(row.get("support_labels", "")))
    if "rt_prior_close" in support or "paired_istd_aligned" in support:
        return 0.0
    if "rt_prior_borderline" in concerns:
        return 1.0
    return 2.0


def _split_semicolon(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(";") if item.strip())


def _candidate_id_rt_apex(candidate_id: str) -> float | None:
    parts = candidate_id.split("|")
    if len(parts) < 3:
        return None
    return _optional_float(parts[-3])


def _observed_delta(
    selected_rt: float | None,
    paired_istd_rt: float | None,
) -> float | None:
    if not _finite(selected_rt) or not _finite(paired_istd_rt):
        return None
    assert selected_rt is not None
    assert paired_istd_rt is not None
    return selected_rt - paired_istd_rt


def _missing_ms2_explanation(result: object | None) -> str:
    evidence = getattr(result, "candidate_ms2_evidence", None)
    if evidence is None:
        token = getattr(result, "nl_token", "") or ""
        return "not_observed" if token == "NO_MS2" else ""
    if not getattr(evidence, "ms2_present", False):
        return "not_observed"
    if getattr(evidence, "strict_nl_scan_count", 0) == 0:
        return (
            "contradicted"
            if getattr(evidence, "trigger_scan_count", 0)
            else "not_observed"
        )
    return ""


def _optional_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(str(value).strip())
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _positive(value: object | None) -> bool:
    parsed = _optional_float(value)
    return parsed is not None and parsed > 0


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("_median requires at least one value")
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _format_optional_float(value: object | None) -> str:
    parsed = _optional_float(value)
    if parsed is None:
        return ""
    return f"{parsed:.5f}"
