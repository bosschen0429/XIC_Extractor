from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from xic_extractor.tabular_io import (
    read_tsv_required,
    text_value,
    write_tsv,
)

from .schema import (
    PEAK_HYPOTHESIS_SELECTION_COLUMNS,
    PEAK_HYPOTHESIS_SELECTION_SCHEMA_VERSION,
    RT_MODE_EVIDENCE_COLUMNS,
    validate_row_tokens,
)

_RT_MODE_REQUIRED_COLUMNS = tuple(
    column
    for column in RT_MODE_EVIDENCE_COLUMNS
    if column != "rt_mode_evidence_schema_version"
)


def load_rt_mode_rows(path: Path) -> tuple[dict[str, str], ...]:
    return read_tsv_required(path, _RT_MODE_REQUIRED_COLUMNS)


def build_peak_hypothesis_selection_rows(
    *,
    rt_mode_rows: Sequence[Mapping[str, str]],
    oracle_keys: Iterable[tuple[str, str]] = (),
) -> tuple[dict[str, str], ...]:
    """Convert selected-apex RT/iRT mode evidence into product candidate units."""

    rows_by_key = {
        (row["feature_family_id"], row["sample_stem"]): row for row in rt_mode_rows
    }
    keys = tuple(oracle_keys) or tuple(rows_by_key)
    return tuple(
        _row_for_rt_mode(
            rows_by_key.get((family_id, sample_stem)),
            family_id,
            sample_stem,
        )
        for family_id, sample_stem in sorted(keys)
    )


def write_peak_hypothesis_selection_rows(
    path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(
        path,
        rows,
        PEAK_HYPOTHESIS_SELECTION_COLUMNS,
        lineterminator="\n",
    )


def _row_for_rt_mode(
    rt_mode_row: Mapping[str, str] | None,
    family_id: str,
    sample_stem: str,
) -> dict[str, str]:
    if rt_mode_row is None:
        return _missing_row(family_id, sample_stem)
    status, scope, action, blocker, reason = _selection_decision(rt_mode_row)
    row = {
        "peak_hypothesis_selection_schema_version": (
            PEAK_HYPOTHESIS_SELECTION_SCHEMA_VERSION
        ),
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "peak_hypothesis_id": _peak_hypothesis_id(rt_mode_row),
        "peak_hypothesis_status": status,
        "product_unit_scope": scope,
        "selected_mode_id": text_value(rt_mode_row.get("selected_mode_id")),
        "selected_mode_role": text_value(rt_mode_row.get("selected_mode_role")),
        "selected_mode_tag_status": text_value(
            rt_mode_row.get("selected_mode_tag_status")
        ),
        "family_mode_class": text_value(rt_mode_row.get("family_mode_class")),
        "family_mode_count": text_value(rt_mode_row.get("family_mode_count")),
        "tag_bearing_mode_count": text_value(
            rt_mode_row.get("tag_bearing_mode_count")
        ),
        "product_selection_action": action,
        "product_selection_blocker": blocker,
        "reason": reason,
        "diagnostic_only": "TRUE",
    }
    validate_row_tokens(row)
    return row


def _selection_decision(row: Mapping[str, str]) -> tuple[str, str, str, str, str]:
    rt_mode_status = text_value(row.get("rt_mode_status"))
    evidence_level = text_value(row.get("rt_mode_evidence_level"))
    role = text_value(row.get("selected_mode_role"))
    family_class = text_value(row.get("family_mode_class"))
    if rt_mode_status == "not_available":
        return (
            "not_available",
            "not_available",
            "no_product_action",
            "not_available",
            "rt_mode_evidence_missing",
        )
    if rt_mode_status == "inconclusive" or not rt_mode_status:
        return (
            "inconclusive",
            "review_only",
            "require_review",
            "inconclusive_mode_evidence",
            "rt_mode_evidence_inconclusive",
        )
    if rt_mode_status == "tailing_confounded" or family_class == "tailing_confounded":
        return (
            "tailing_review_only",
            "review_only",
            "require_tailing_review",
            "tailing_confounded",
            "tailing_confounds_mode_split",
        )
    if rt_mode_status == "raw_mode_review_only":
        return (
            "raw_mode_review_only",
            "review_only",
            "require_raw_mode_review",
            "raw_mode_review_only",
            "raw_overlay_mode_split_requires_irt_or_tag_review",
        )
    if (
        rt_mode_status == "mode_supported"
        and evidence_level == "raw_selected_apex_modes"
    ):
        return (
            "raw_mode_review_only",
            "review_only",
            "require_raw_mode_review",
            "raw_mode_review_only",
            "raw_selected_apex_mode_requires_explicit_mode_hypothesis",
        )
    if rt_mode_status == "mode_conflict":
        return (
            "cross_mode_rescue_blocked",
            "sample_cell",
            "block_cross_mode_rescue",
            "cross_mode_rescue",
            "selected_cell_belongs_to_non_core_rt_mode",
        )
    if rt_mode_status == "consolidation_no_go":
        return (
            "consolidation_no_go",
            "candidate_container",
            "block_family_promotion",
            "consolidation_no_go",
            "family_has_no_unique_product_peak_hypothesis",
        )
    if rt_mode_status == "mode_split_required":
        return (
            "mode_split_required",
            "candidate_container",
            "require_mode_split_before_product",
            "mode_split_required",
            "family_must_split_modes_before_product_label",
        )
    if rt_mode_status == "mode_supported":
        if role in {"single_mode", "tag_bearing_core"}:
            return (
                "product_candidate_core",
                "mode_level",
                "select_mode_peak_hypothesis",
                "none",
                "selected_mode_is_product_peak_hypothesis_candidate",
            )
        return (
            "inconclusive",
            "review_only",
            "require_review",
            "inconclusive_mode_evidence",
            "mode_supported_without_core_role",
        )
    return (
        "inconclusive",
        "review_only",
        "require_review",
        "inconclusive_mode_evidence",
        "unsupported_rt_mode_status_for_peak_hypothesis_selection",
    )


def _peak_hypothesis_id(row: Mapping[str, str]) -> str:
    family_id = text_value(row.get("feature_family_id"))
    mode_id = text_value(row.get("selected_mode_id"))
    if not family_id or not mode_id:
        return ""
    return f"{family_id}::{mode_id}"


def _missing_row(family_id: str, sample_stem: str) -> dict[str, str]:
    row = {
        "peak_hypothesis_selection_schema_version": (
            PEAK_HYPOTHESIS_SELECTION_SCHEMA_VERSION
        ),
        "feature_family_id": family_id,
        "sample_stem": sample_stem,
        "peak_hypothesis_id": "",
        "peak_hypothesis_status": "not_available",
        "product_unit_scope": "not_available",
        "selected_mode_id": "",
        "selected_mode_role": "unknown",
        "selected_mode_tag_status": "unknown",
        "family_mode_class": "inconclusive",
        "family_mode_count": "0",
        "tag_bearing_mode_count": "0",
        "product_selection_action": "no_product_action",
        "product_selection_blocker": "not_available",
        "reason": "rt_mode_evidence_missing",
        "diagnostic_only": "TRUE",
    }
    validate_row_tokens(row)
    return row
