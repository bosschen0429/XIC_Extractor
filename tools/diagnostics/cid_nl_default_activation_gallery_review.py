"""Source/successor transition evidence for the feature-inclusion diagnostic.

Scientific transition classification and tabular regeneration remain here.
Interactive review of completed evidence uses the installed XIC Inspector.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.tabular_io import read_tsv_required, text_value, write_tsv

DECISION_COLUMNS = (
    "old_peak_hypothesis_id",
    "sample_stem",
    "successor_peak_hypothesis_id",
    "successor_decision",
    "write_authority",
    "matrix_write_allowed",
    "matrix_effect",
    "human_explanation",
    "input_resolution_status",
    "candidate_new_peak_hypothesis_ids",
    "candidate_baseline_values",
    "accepted_quant_value",
)


ALIGNMENT_REVIEW_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "family_product_mz",
    "detected_count",
    "accepted_cell_count",
    "include_in_primary_matrix",
    "identity_decision",
    "identity_confidence",
    "row_flags",
    "reason",
)


DIFFERENTIAL_REVIEW_COLUMNS = (
    "source_peak_hypothesis_id",
    "successor_peak_hypothesis_id",
    "transition_key",
    "sample_count",
    "write_authorized_count",
    "no_write_detected_baseline_preserved_count",
    "no_write_omitted_count",
    "source_mz",
    "source_rt",
    "source_product_mz",
    "source_neutral_loss_tag",
    "source_identity_decision",
    "source_accepted_cell_count",
    "successor_mz",
    "successor_rt",
    "successor_product_mz",
    "successor_neutral_loss_tag",
    "successor_identity_decision",
    "successor_accepted_cell_count",
    "mz_delta",
    "rt_delta",
    "feature_inclusion_gate",
    "identity_authority_gate",
    "source_successor_relationship",
    "transition_type",
    "differential_overlay_readiness",
    "review_note",
)


def _differential_review_rows(
    decisions: Sequence[Mapping[str, str]],
    *,
    alignment_by_family: Mapping[str, Mapping[str, str]],
) -> list[dict[str, object]]:
    transitions: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in decisions:
        source = text_value(row.get("old_peak_hypothesis_id"))
        if not source:
            continue
        successor = text_value(row.get("successor_peak_hypothesis_id")) or "<none>"
        transitions[(source, successor)][row["successor_decision"]] += 1

    rows: list[dict[str, object]] = []
    for source, successor in sorted(transitions):
        counts = transitions[(source, successor)]
        source_row = alignment_by_family.get(source, {})
        successor_row = (
            {} if successor == "<none>" else alignment_by_family.get(successor, {})
        )
        source_mz = _float_text(source_row.get("family_center_mz"))
        source_rt = _float_text(source_row.get("family_center_rt"))
        successor_mz = _float_text(successor_row.get("family_center_mz"))
        successor_rt = _float_text(successor_row.get("family_center_rt"))
        rows.append(
            {
                "source_peak_hypothesis_id": source,
                "successor_peak_hypothesis_id": successor,
                "transition_key": f"{source}->{successor}",
                "sample_count": sum(counts.values()),
                "write_authorized_count": counts.get("write_authorized", 0),
                "no_write_detected_baseline_preserved_count": counts.get(
                    "no_write_detected_baseline_preserved",
                    0,
                ),
                "no_write_omitted_count": counts.get("no_write_omitted", 0),
                "source_mz": _alignment_text(source_row, "family_center_mz"),
                "source_rt": _alignment_text(source_row, "family_center_rt"),
                "source_product_mz": _alignment_text(source_row, "family_product_mz"),
                "source_neutral_loss_tag": _alignment_text(
                    source_row,
                    "neutral_loss_tag",
                ),
                "source_identity_decision": _alignment_text(
                    source_row,
                    "identity_decision",
                ),
                "source_accepted_cell_count": _alignment_text(
                    source_row,
                    "accepted_cell_count",
                ),
                "successor_mz": _alignment_text(successor_row, "family_center_mz"),
                "successor_rt": _alignment_text(successor_row, "family_center_rt"),
                "successor_product_mz": _alignment_text(
                    successor_row,
                    "family_product_mz",
                ),
                "successor_neutral_loss_tag": _alignment_text(
                    successor_row,
                    "neutral_loss_tag",
                ),
                "successor_identity_decision": _alignment_text(
                    successor_row,
                    "identity_decision",
                ),
                "successor_accepted_cell_count": _alignment_text(
                    successor_row,
                    "accepted_cell_count",
                ),
                "mz_delta": _delta_text(successor_mz, source_mz),
                "rt_delta": _delta_text(successor_rt, source_rt),
                "feature_inclusion_gate": _feature_inclusion_gate(
                    successor=successor,
                    successor_row=successor_row,
                    counts=counts,
                ),
                "identity_authority_gate": _identity_authority_gate(
                    source=source,
                    successor=successor,
                    source_row=source_row,
                    successor_row=successor_row,
                ),
                "source_successor_relationship": _source_successor_relationship(
                    successor=successor,
                    source_row=source_row,
                    successor_row=successor_row,
                ),
                "transition_type": _transition_type(successor),
                "differential_overlay_readiness": _differential_overlay_readiness(
                    source=source,
                    successor=successor,
                    source_row=source_row,
                    successor_row=successor_row,
                ),
                "review_note": (
                    "No-RAW Discovery identity queue only; paired overlay needs "
                    "source and successor traces before product adoption."
                ),
            }
        )
    return rows


def _feature_inclusion_gate(
    *,
    successor: str,
    successor_row: Mapping[str, str],
    counts: Mapping[str, int],
) -> str:
    if successor == "<none>":
        return "not_assessable_no_successor_feature"
    if not successor_row:
        return "not_assessable_missing_successor_alignment_row"
    if counts.get("write_authorized", 0) or counts.get(
        "no_write_detected_baseline_preserved",
        0,
    ):
        return "candidate_ms1_feature_inclusion_supported"
    return "candidate_ms1_feature_inclusion_unresolved"


def _identity_authority_gate(
    *,
    source: str,
    successor: str,
    source_row: Mapping[str, str],
    successor_row: Mapping[str, str],
) -> str:
    if successor == "<none>":
        return "no_replacement_target"
    if not source_row or not successor_row:
        return "not_assessable_missing_alignment_row"
    return "replacement_merge_dedupe_requires_expected_diff"


def _source_successor_relationship(
    *,
    successor: str,
    source_row: Mapping[str, str],
    successor_row: Mapping[str, str],
) -> str:
    if successor == "<none>":
        return "old_identity_has_no_successor"
    if not source_row or not successor_row:
        return "relationship_not_assessable"
    return "source_and_successor_not_mutually_exclusive"


def _alignment_text(row: Mapping[str, str], key: str) -> str:
    return text_value(row.get(key))


def _delta_text(left: float | None, right: float | None) -> str:
    if left is None or right is None:
        return ""
    return f"{left - right:.6g}"


def _transition_type(successor: str) -> str:
    return "old_to_none" if successor == "<none>" else "old_to_successor"


def _differential_overlay_readiness(
    *,
    source: str,
    successor: str,
    source_row: Mapping[str, str],
    successor_row: Mapping[str, str],
) -> str:
    if successor == "<none>":
        return "no_successor_target"
    missing = []
    if not source_row:
        missing.append(source)
    if not successor_row:
        missing.append(successor)
    if missing:
        return "missing_alignment_row:" + ",".join(missing)
    return "ready_for_paired_overlay"


def _float_text(value: object) -> float | None:
    try:
        return float(text_value(value))
    except ValueError:
        return None


def write_transition_review_evidence(
    *,
    decisions_tsv: Path,
    alignment_review_tsv: Path,
    output_path: Path,
) -> Path:
    """Write the existing transition evidence consumed by differential diagnostics."""
    decisions = read_tsv_required(decisions_tsv, DECISION_COLUMNS)
    alignment = read_tsv_required(alignment_review_tsv, ALIGNMENT_REVIEW_COLUMNS)
    rows = _differential_review_rows(
        decisions,
        alignment_by_family={row["feature_family_id"]: row for row in alignment},
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_tsv(output_path, rows, DIFFERENTIAL_REVIEW_COLUMNS)
    return output_path
