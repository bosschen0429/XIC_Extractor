"""Evidence classification for the backfill reconciliation gallery."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics.diagnostic_io import (
    bool_value,
    optional_float,
    split_semicolon_labels,
    text_value,
)

from .backfill_reconciliation_gallery_models import (
    _ordered_unique,
    _SeedRecord,
    _ShiftAwareSamePatternEvidence,
)

SHIFT_AWARE_SAME_PATTERN_SUPPORT_MIN_R = 0.98
SHIFT_AWARE_SAME_PATTERN_CONFLICT_MAX_R = 0.90

_HUMAN_REVIEW_PREFIXES = ("review_required_",)
_HUMAN_REVIEW_TOKENS = {
    "neighbor_interference_review",
    "shape_insufficient_review",
}
_ANCHOR_SHAPE_SUPPORTED_REASON = (
    "family_ms1_overlay_anchor_peak_own_max_shape_supported"
)
_ANCHOR_SHAPE_REVIEW_REASON = (
    "family_ms1_overlay_anchor_peak_shape_below_threshold"
)


def _classify_evidence(
    *,
    family: str,
    seed_record: _SeedRecord,
    family_cells: Sequence[Mapping[str, str]],
    group_cells: Sequence[Mapping[str, str]],
    has_matrix_context: bool,
    seed_samples: frozenset[str],
    overlay_rows: Sequence[Mapping[str, str]],
    shift_aware_same_pattern_rows: Sequence[Mapping[str, str]],
    shift_aware_standard_peak_gate_rows: Sequence[Mapping[str, str]],
    legacy_overlay_rows: Sequence[Mapping[str, str]],
    seed_aware_row: Mapping[str, str],
    candidate_gate_row: Mapping[str, str],
    retained_gate_row: Mapping[str, str],
    source_hashes: Mapping[str, str],
    has_tier2_trace_evidence: bool,
) -> dict[str, Any]:
    product_grade: list[str] = []
    visual: list[str] = []
    dependent: list[str] = []
    blockers: list[str] = []
    human_review: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    artifacts: list[str] = ["alignment_review.tsv", "cell evidence TSV"]
    overlay_png_path = ""
    overlay_trace_json_path = ""
    family_pattern_png_path = ""
    family_pattern_trace_json_path = ""
    family_pattern_verdict = ""
    overlay_evidence_notes: list[str] = []

    if has_matrix_context:
        artifacts.append("alignment_matrix.tsv")
    if seed_record.seed_group_basis == "seed_audit":
        artifacts.append("alignment_owner_backfill_seed_audit.tsv")
        dependent.append("seed_request_provenance")
    else:
        missing.append("missing_seed_provenance")
    if seed_record.samples:
        cell_samples = {text_value(row.get("sample_stem")) for row in family_cells}
        if not seed_record.samples <= cell_samples:
            warnings.append("join_gap_seed_audit_sample_not_in_cells")
            missing.append("join_gap_seed_audit_sample_not_in_cells")
    if not family_cells:
        warnings.append("join_gap_family_missing_alignment_cells")
        missing.append("join_gap_family_missing_alignment_cells")

    candidate_status = text_value(candidate_gate_row.get("candidate_gate_status"))
    candidate_support = split_semicolon_labels(
        candidate_gate_row.get("support_components"),
    )
    candidate_blockers = split_semicolon_labels(
        candidate_gate_row.get("challenge_blockers"),
    )
    candidate_source_warnings = _candidate_gate_source_warnings(
        candidate_gate_row,
        source_hashes,
    )
    if candidate_status:
        artifacts.append("alignment_production_candidate_gate.tsv")
    if has_tier2_trace_evidence:
        artifacts.append("alignment_tier2_trace_evidence.tsv")
    if candidate_source_warnings:
        warnings.extend(candidate_source_warnings)
        missing.extend(candidate_source_warnings)
    if (
        candidate_status == "production_candidate"
        and candidate_support
        and not candidate_blockers
        and not candidate_source_warnings
    ):
        product_grade.extend(candidate_support)
    product_grade.extend(_product_authority_components(group_cells or family_cells))
    if candidate_blockers:
        blockers.extend(candidate_blockers)
        for blocker in candidate_blockers:
            if _is_stale_or_join_token(blocker):
                warnings.append(f"stale_candidate_gate_{blocker}")
                missing.append(f"stale_candidate_gate_{blocker}")

    retained_status = text_value(retained_gate_row.get("evidence_gate_status"))
    retained_action = text_value(retained_gate_row.get("recommended_action"))
    retained_support = split_semicolon_labels(
        retained_gate_row.get("support_components"),
    )
    retained_blockers = split_semicolon_labels(
        retained_gate_row.get("challenge_blockers"),
    )
    retained_missing = split_semicolon_labels(retained_gate_row.get("missing_evidence"))
    if retained_status:
        artifacts.append("alignment_retained_backfill_evidence_gate.tsv")
    if retained_status == "machine_support_no_overlay":
        dependent.extend(retained_support)
        dependent.append("overlay_not_required_machine_supported")
        if retained_action:
            dependent.append(retained_action)
    else:
        blockers.extend(retained_blockers)
        missing.extend(retained_missing)

    if seed_aware_row:
        artifacts.append("seed_aware_backfill_review_families.tsv")
        classification = text_value(seed_aware_row.get("review_classification"))
        if classification == "seed_shape_supported_review_candidate":
            visual.append(classification)
        elif classification in {
            "neighbor_interference_review",
            "shape_insufficient_review",
        }:
            human_review.append(classification)
        elif classification == "seed_context_missing":
            missing.append("missing_seed_provenance")
        elif classification == "not_assessable":
            missing.append("missing_overlay")
        overlay_png_path = _first_path(
            seed_aware_row.get("png_paths"),
            seed_aware_row.get("png_path"),
        )
        overlay_trace_json_path = _first_path(seed_aware_row.get("trace_json_paths"))
    if shift_aware_same_pattern_rows:
        artifacts.append("source_family_best_shift_summary.tsv")
        shift_evidence = _shift_aware_same_pattern_evidence(
            shift_aware_same_pattern_rows,
        )
        if shift_evidence.support:
            visual.append("shift_aware_same_pattern_support_review_only")
        elif shift_evidence.review_required:
            human_review.append("shift_aware_same_pattern_review_required")
        overlay_evidence_notes.extend(shift_evidence.notes)
    if shift_aware_standard_peak_gate_rows:
        artifacts.append("shift_aware_standard_peak_gate_calibration.tsv")
        gate_components = _shift_aware_standard_peak_gate_components(
            shift_aware_standard_peak_gate_rows,
        )
        visual.extend(gate_components["visual"])
        blockers.extend(gate_components["blockers"])
        overlay_evidence_notes.extend(gate_components["notes"])
    for row in overlay_rows:
        artifacts.append("family_ms1_overlay_batch_summary.tsv")
        verdict = text_value(row.get("family_verdict"))
        if verdict == "ms1_shape_supports_family_backfill":
            visual.append(verdict)
        elif _is_human_review_token(verdict):
            human_review.append(verdict)
        elif verdict:
            blockers.append(verdict)
        overlay_png_path = overlay_png_path or _first_path(row.get("png_path"))
        overlay_trace_json_path = overlay_trace_json_path or _first_path(
            row.get("trace_json_path"),
            row.get("json_path"),
            row.get("trace_data_json"),
        )
        overlay_evidence_notes.extend(_overlay_evidence_notes(row))
    if legacy_overlay_rows:
        row = legacy_overlay_rows[0]
        family_pattern_verdict = text_value(row.get("family_verdict"))
        family_pattern_png_path = _first_path(row.get("png_path"))
        family_pattern_trace_json_path = _first_path(
            row.get("trace_json_path"),
            row.get("json_path"),
            row.get("trace_data_json"),
        )
    if not overlay_rows and legacy_overlay_rows:
        if "family_ms1_overlay_batch_summary.tsv" not in artifacts:
            artifacts.append("family_ms1_overlay_batch_summary.tsv")
        row = legacy_overlay_rows[0]
        verdict = text_value(row.get("family_verdict"))
        dependent.append("legacy_family_overlay_context")
        if verdict:
            dependent.append(f"legacy_family_overlay:{verdict}")
        missing.append("missing_seed_specific_overlay")
    overlay_evidence_notes.extend(
        _anchor_peak_overlay_notes(
            family=family,
            scoring_cells=family_cells,
            note_cells=group_cells,
            overlay_trace_json_path=overlay_trace_json_path if overlay_rows else "",
        )
    )
    if (
        not product_grade
        and not visual
        and not blockers
        and not human_review
        and not missing
    ):
        if "high_detected_anchor_low_rescue_machine_support" in dependent:
            authority_state = "machine_support_no_overlay"
        elif dependent:
            authority_state = "dependent_context_only"
        else:
            authority_state = "evidence_inconclusive"
    elif any(
        token.startswith(("join_gap_", "stale_"))
        for token in [*missing, *warnings]
    ):
        authority_state = "not_assessable"
    elif (
        "missing_seed_provenance" in missing
        or "missing_overlay" in missing
        or "missing_seed_specific_overlay" in missing
    ):
        authority_state = "not_assessable"
    elif blockers and not product_grade:
        authority_state = "evidence_blocks_backfill"
    elif human_review and not product_grade:
        authority_state = "human_visual_judgment_only"
    elif product_grade:
        authority_state = "product_grade_support"
    elif visual:
        authority_state = "review_only_visual_support"
    else:
        authority_state = "evidence_inconclusive"

    return {
        "authority_state": authority_state,
        "product_grade_support_components": tuple(_ordered_unique(product_grade)),
        "review_only_visual_components": tuple(_ordered_unique(visual)),
        "dependent_context_components": tuple(_ordered_unique(dependent)),
        "blocker_components": tuple(_ordered_unique((*blockers, *human_review))),
        "missing_evidence": tuple(_ordered_unique(missing)),
        "source_artifacts": tuple(_ordered_unique(artifacts)),
        "source_warnings": tuple(_ordered_unique(warnings)),
        "overlay_png_path": overlay_png_path,
        "overlay_trace_json_path": overlay_trace_json_path,
        "family_pattern_png_path": family_pattern_png_path,
        "family_pattern_trace_json_path": family_pattern_trace_json_path,
        "family_pattern_verdict": family_pattern_verdict,
        "overlay_evidence_notes": tuple(_ordered_unique(overlay_evidence_notes)),
    }


def _overlay_evidence_notes(row: Mapping[str, str]) -> tuple[str, ...]:
    labels = (
        ("absolute_own_max_shape_supported_fraction", "own-max shape support"),
        ("absolute_trace_apex_cluster_fraction", "absolute apex cluster"),
        ("shape_supported_fraction", "detected-anchor apex-aligned support"),
        ("local_apex_supported_fraction", "local apex support"),
        ("global_apex_interference_fraction", "global apex interference"),
        ("low_selected_peak_dominance_fraction", "low selected peak dominance"),
    )
    notes = []
    for key, label in labels:
        value = text_value(row.get(key))
        if value:
            notes.append(f"{label}={value}")
    return tuple(notes)


def _shift_aware_standard_peak_gate_components(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, tuple[str, ...]]:
    visual: list[str] = []
    blockers: list[str] = []
    notes: list[str] = []
    for row in rows:
        call = text_value(row.get("standard_peak_gate_call"))
        if call == "standard_peak_gate_supported":
            visual.append("shift_aware_standard_peak_gate_supported_review_only")
            notes.append("standard peak gate=supported")
        elif call == "standard_peak_gate_blocked":
            blockers.append("shift_aware_standard_peak_gate_blocked")
            notes.append("standard peak gate=blocked")
        reasons = split_semicolon_labels(row.get("standard_peak_gate_reasons"))
        gate_blockers = split_semicolon_labels(row.get("standard_peak_gate_blockers"))
        if reasons:
            notes.append(f"standard peak gate reasons={';'.join(reasons)}")
        if gate_blockers:
            notes.append(f"standard peak gate blockers={';'.join(gate_blockers)}")
        min_r = text_value(row.get("min_shape_r_after_best_shift"))
        max_shift = text_value(row.get("max_abs_shift_sec"))
        if min_r or max_shift:
            parts = []
            if min_r:
                parts.append(f"min r={min_r}")
            if max_shift:
                parts.append(f"max shift={max_shift}s")
            notes.append(f"standard peak gate metrics={'; '.join(parts)}")
        outcome = text_value(row.get("calibration_outcome"))
        if outcome:
            notes.append(f"standard peak gate calibration={outcome}")
    return {
        "visual": tuple(_ordered_unique(visual)),
        "blockers": tuple(_ordered_unique(blockers)),
        "notes": tuple(_ordered_unique(notes)),
    }


def _shift_aware_same_pattern_evidence(
    rows: Sequence[Mapping[str, str]],
) -> _ShiftAwareSamePatternEvidence:
    candidates = [
        row
        for row in rows
        if text_value(row.get("shift_basis")) == "median_shape_correlation"
        and not bool_value(row.get("is_reference"))
    ]
    similarities = [
        value
        for row in candidates
        if (
            value := optional_float(
                row.get("shape_similarity_to_reference_after_group_shift"),
            )
        )
        is not None
    ]
    shift_seconds = [
        abs(value)
        for row in candidates
        if (value := optional_float(row.get("shift_to_reference_sec"))) is not None
    ]
    notes: list[str] = []
    if similarities:
        notes.append(
            "shift-aware same-pattern min r="
            f"{min(similarities):.3f}; max shift={max(shift_seconds or [0.0]):.1f}s",
        )
    if candidates:
        notes.append(f"shift-aware source-family groups={len(candidates)}")
    has_support = bool(
        similarities
        and min(similarities) >= SHIFT_AWARE_SAME_PATTERN_SUPPORT_MIN_R
    )
    has_conflict = any(
        value < SHIFT_AWARE_SAME_PATTERN_CONFLICT_MAX_R for value in similarities
    )
    return _ShiftAwareSamePatternEvidence(
        support=has_support and not has_conflict,
        review_required=bool(candidates and not has_support),
        notes=tuple(notes),
    )


def _product_authority_components(
    rows: Sequence[Mapping[str, str]],
) -> tuple[str, ...]:
    components: list[str] = []
    for row in rows:
        if text_value(row.get("status")).lower() != "rescued":
            continue
        _append_product_authority_component(
            components,
            row,
            prefix="backfill_ms1",
            label="product_authorized_ms1_pattern",
        )
        _append_product_authority_component(
            components,
            row,
            prefix="backfill_candidate_ms2",
            label="product_authorized_candidate_ms2",
        )
    return tuple(_ordered_unique(components))


def _append_product_authority_component(
    components: list[str],
    row: Mapping[str, str],
    *,
    prefix: str,
    label: str,
) -> None:
    status = text_value(row.get(f"{prefix}_product_authority_status"))
    if status != "product_authorized":
        return
    scope = text_value(row.get(f"{prefix}_product_authority_scope"))
    source = text_value(row.get(f"{prefix}_product_authority_source"))
    if scope != "feature_family_sample" or not source:
        return
    reason = text_value(row.get(f"{prefix}_product_authority_reason")) or source
    components.append(f"{label}:{reason}")


def _anchor_peak_overlay_notes(
    *,
    family: str,
    scoring_cells: Sequence[Mapping[str, str]],
    note_cells: Sequence[Mapping[str, str]],
    overlay_trace_json_path: str,
) -> tuple[str, ...]:
    path = _existing_path_from_text(overlay_trace_json_path)
    if path is None or not scoring_cells or not note_cells:
        return ()
    oracle_keys = tuple(
        (family, sample)
        for row in note_cells
        if (sample := text_value(row.get("sample_stem")))
    )
    if not oracle_keys:
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ("anchor peak evidence=unavailable",)
    if not isinstance(payload, Mapping):
        return ("anchor peak evidence=unavailable",)

    raw_rows = (
        payload.get("coherence_rows")
        or payload.get("ms1_pattern_coherence_rows")
        or payload.get("coherence")
        or payload.get("anchor_peak_evidence")
        or payload.get("anchor_evidence")
        or (
            payload.get("evidence_summary").get("coherence_rows")
            if isinstance(payload.get("evidence_summary"), Mapping)
            else None
        )
        or (
            payload.get("evidence_summary").get("ms1_pattern_coherence_rows")
            if isinstance(payload.get("evidence_summary"), Mapping)
            else None
        )
    )
    if not isinstance(raw_rows, Sequence) or not raw_rows:
        return ("anchor peak evidence=unavailable",)
    rows = tuple(row for row in raw_rows if isinstance(row, Mapping))
    if not rows:
        return ("anchor peak evidence=unavailable",)

    status_by_sample = {
        text_value(row.get("sample_stem")): text_value(row.get("status")).lower()
        for row in note_cells
    }
    anchor_rt = next(
        (
            text_value(row.get("anchor_peak_rt"))
            for row in rows
            if row.get("anchor_peak_rt")
        ),
        "",
    )
    if not anchor_rt:
        return ()
    support: list[str] = []
    review: list[str] = []
    blocked: list[str] = []
    for row in rows:
        sample = text_value(row.get("sample_stem"))
        if status_by_sample.get(sample) != "rescued":
            continue
        reason = text_value(row.get("reason"))
        score = text_value(row.get("shape_correlation_score"))
        token = f"{sample}({score})" if score else sample
        if reason == _ANCHOR_SHAPE_SUPPORTED_REASON:
            support.append(token)
        elif reason == _ANCHOR_SHAPE_REVIEW_REASON:
            review.append(token)
        elif reason:
            blocked.append(f"{sample}:{reason}")
    notes = [
        f"anchor peak RT={anchor_rt}",
        "anchor own-max shape threshold=0.5",
    ]
    if support:
        notes.append(
            "anchor same-peak candidate support="
            + _compact_note_items(tuple(support)),
        )
    if review:
        notes.append(
            "anchor same-peak review="
            + _compact_note_items(tuple(review)),
        )
    if blocked:
        notes.append("anchor blocked cells=" + _compact_note_items(tuple(blocked)))
    return tuple(notes)


def _existing_path_from_text(path_text: str) -> Path | None:
    value = text_value(path_text)
    if not value:
        return None
    raw = Path(value)
    for candidate in (raw, Path.cwd() / raw):
        if candidate.exists():
            return candidate.resolve()
    return None


def _compact_note_items(items: Sequence[str], *, limit: int = 4) -> str:
    shown = list(items[:limit])
    remaining = len(items) - len(shown)
    if remaining > 0:
        shown.append(f"+{remaining} more")
    return ", ".join(shown)


def _is_stale_or_join_token(token: str) -> bool:
    lowered = token.lower()
    return "source_hash_mismatch" in lowered or "stale" in lowered or "join" in lowered


def _is_human_review_token(token: str) -> bool:
    lowered = token.lower()
    return lowered.startswith(_HUMAN_REVIEW_PREFIXES) or lowered in _HUMAN_REVIEW_TOKENS


def _candidate_gate_source_warnings(
    candidate_gate_row: Mapping[str, str],
    source_hashes: Mapping[str, str],
) -> tuple[str, ...]:
    if not source_hashes or not candidate_gate_row:
        return ()
    checks = (
        ("review", "source_review_sha256", "alignment_review_sha256"),
        ("cell", "source_cell_sha256", "alignment_cells_sha256"),
        ("matrix", "source_matrix_sha256", "alignment_matrix_sha256"),
    )
    warnings: list[str] = []
    for label, row_key, input_key in checks:
        expected = text_value(source_hashes.get(input_key))
        if not expected:
            continue
        observed = text_value(candidate_gate_row.get(row_key))
        if not observed:
            warnings.append(f"stale_candidate_gate_missing_{label}_sha256")
        elif observed.lower() != expected.lower():
            warnings.append(f"stale_candidate_gate_{label}_sha256_mismatch")
    return tuple(warnings)


def _product_behavior(
    review_row: Mapping[str, str],
    cell_rows: Sequence[Mapping[str, str]],
) -> str:
    if not review_row and not cell_rows:
        return "product_unknown"
    include_primary = bool_value(review_row.get("include_in_primary_matrix"))
    rescued_cells = [
        row for row in cell_rows if text_value(row.get("status")).lower() == "rescued"
    ]
    primary_rescued = any(_cell_writes_primary_matrix(row) for row in rescued_cells)
    if include_primary and primary_rescued:
        return "product_primary_backfilled"
    if rescued_cells:
        return "product_rescued_context_only"
    identity = text_value(review_row.get("identity_decision")).lower()
    flags = text_value(review_row.get("row_flags")).lower()
    confidence = text_value(review_row.get("identity_confidence")).lower()
    if "provisional" in identity or "provisional" in flags:
        return "product_provisional"
    if "review" in identity or "review" in confidence:
        return "product_review_only"
    return "product_not_backfilled"


def _cell_writes_primary_matrix(row: Mapping[str, str]) -> bool:
    write_value = text_value(row.get("write_matrix_value"))
    if write_value:
        return bool_value(write_value) is True
    return bool(text_value(row.get("primary_matrix_area_source")))


def _cell_has_primary_area_context(row: Mapping[str, str]) -> bool:
    return bool(
        text_value(row.get("primary_matrix_area"))
        or text_value(row.get("primary_matrix_area_source"))
    )


def _reconciliation_class(
    product_behavior_state: str,
    evidence_authority_state: str,
    missing_evidence: tuple[str, ...],
    source_warnings: tuple[str, ...],
) -> str:
    tokens = set(missing_evidence) | set(source_warnings)
    if evidence_authority_state == "not_assessable":
        if any(token.startswith(("join_gap_", "stale_")) for token in tokens):
            return "not_assessable_join_gap"
        if "missing_seed_provenance" in tokens:
            return "not_assessable_missing_seed_provenance"
        return "not_assessable_missing_overlay"
    if evidence_authority_state == "evidence_inconclusive":
        return "evidence_inconclusive"
    if evidence_authority_state == "human_visual_judgment_only":
        return "evidence_inconclusive"
    if evidence_authority_state == "machine_support_no_overlay":
        return "machine_support_no_overlay"
    product_accepts = product_behavior_state == "product_primary_backfilled"
    if evidence_authority_state == "product_grade_support":
        return (
            "product_accepts_and_product_grade_supports"
            if product_accepts
            else "product_rejects_but_product_grade_supports"
        )
    if evidence_authority_state == "review_only_visual_support":
        return (
            "product_accepts_and_visual_supports"
            if product_accepts
            else "product_rejects_but_visual_supports"
        )
    if evidence_authority_state == "evidence_blocks_backfill":
        return (
            "product_accepts_but_evidence_conflicts"
            if product_accepts
            else "product_rejects_and_evidence_blocks"
        )
    return "evidence_inconclusive"


def _first_path(*values: object) -> str:
    for value in values:
        labels = split_semicolon_labels(value)
        if labels:
            return labels[0]
    return ""
