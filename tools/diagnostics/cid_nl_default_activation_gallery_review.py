"""Build a Gallery-compatible CID-NL default activation review packet."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.tabular_io import (
    file_sha256,
    read_tsv_required,
    text_value,
    write_tsv,
)
from xic_inspector.reconciliation import (
    OVERLAY_INTERPRETATION_GUIDE_PATH,
    ReconciliationGroup,
    ReconciliationIndex,
    RepresentativeCell,
    TargetBenchmarkContext,
    write_reconciliation_gallery_html,
    write_reconciliation_outputs,
)

SCHEMA_VERSION = "cid_nl_default_activation_gallery_review_v1"
DEFAULT_SUCCESSOR_PACKET_DIR = Path(
    "output/validation/cid_nl_default_activation_successor_authority_contract_v1",
)
DEFAULT_TARGET_PREFLIGHT_SUMMARY = Path(
    "docs/superpowers/validation/cid_nl_default_activation_preflight_v1/"
    "cid_nl_default_activation_preflight_summary.json",
)
DEFAULT_OUTPUT_DIR = Path(
    "output/validation/cid_nl_default_activation_gallery_review_v1",
)

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
OVERLAY_SUMMARY_COLUMNS = (
    "rank",
    "feature_family_id",
    "seed_group_id",
    "mz",
    "rt_min",
    "rt_max",
    "family_center_rt",
    "output_prefix",
    "status",
    "family_verdict",
    "png_path",
    "trace_data_json",
    "shape_supported_fraction",
    "absolute_own_max_shape_supported_fraction",
    "local_apex_supported_fraction",
)
OVERLAY_QUEUE_COLUMNS = (
    "feature_family_id",
    "seed_group_id",
    "family_center_mz",
    "family_center_rt",
    "suggested_rt_min",
    "suggested_rt_max",
    "suggested_output_prefix",
    "cid_nl_review_reason",
    "successor_decision_counts",
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
_FAMILY_RE = re.compile(r"^FAM\d+$")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        payload = build_gallery_review_packet(
            successor_packet_dir=args.successor_packet_dir,
            target_preflight_summary_json=args.target_preflight_summary_json,
            output_dir=args.output_dir,
            source_root=args.source_root,
            alignment_review_tsv=args.alignment_review_tsv,
            alignment_cells_tsv=args.alignment_cells_tsv,
            overlay_batch_summary_tsvs=tuple(args.overlay_batch_summary_tsv or ()),
            rt_half_window_min=args.rt_half_window_min,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"CID-NL gallery review: {payload['gallery_html']}")
    print(f"CID-NL overlay queue: {payload['overlay_review_queue_tsv']}")
    print(
        "CID-NL review groups: "
        f"{payload['group_count']} "
        f"(overlay linked {payload['overlay_linked_group_count']})",
    )
    if args.require_pass and payload["overall_status"] != "pass":
        return 2
    return 0


def build_gallery_review_packet(
    *,
    successor_packet_dir: Path,
    target_preflight_summary_json: Path,
    output_dir: Path,
    source_root: Path = Path("."),
    alignment_review_tsv: Path | None = None,
    alignment_cells_tsv: Path | None = None,
    overlay_batch_summary_tsvs: Sequence[Path] = (),
    rt_half_window_min: float = 1.5,
) -> dict[str, object]:
    source_root = source_root.resolve()
    successor_packet_dir = _resolve(source_root, successor_packet_dir)
    target_preflight_summary_json = _resolve(
        source_root,
        target_preflight_summary_json,
    )
    output_dir = _resolve(source_root, output_dir)
    successor_summary_path = (
        successor_packet_dir
        / "cid_nl_default_activation_successor_authority_summary.json"
    )
    decisions_tsv = successor_packet_dir / "successor_authority_decisions.tsv"

    successor_summary = _read_json(successor_summary_path)
    target_summary = _read_json(target_preflight_summary_json)
    if text_value(successor_summary.get("overall_status")) != "pass":
        raise ValueError("successor authority packet is not pass")

    alignment_review_tsv = _resolve(
        source_root,
        alignment_review_tsv
        or _artifact_path(target_summary, "alignment_review_tsv"),
    )
    alignment_cells_tsv = _resolve(
        source_root,
        alignment_cells_tsv
        or _artifact_path(target_summary, "backfill_cell_evidence_tsv"),
    )

    decisions = read_tsv_required(decisions_tsv, DECISION_COLUMNS)
    alignment_review_rows = read_tsv_required(
        alignment_review_tsv,
        ALIGNMENT_REVIEW_COLUMNS,
    )
    alignment_by_family = {
        row["feature_family_id"]: row for row in alignment_review_rows
    }
    family_records = _family_records(decisions, target_summary)
    overlay_expected_rows = _overlay_expected_rows_by_family(
        family_records,
        alignment_by_family=alignment_by_family,
        rt_half_window_min=rt_half_window_min,
    )
    overlay_rows = _read_overlay_rows(
        source_root,
        overlay_batch_summary_tsvs,
        expected_rows_by_family=overlay_expected_rows,
    )
    overlay_cell_families = _families_in_alignment_cells(
        alignment_cells_tsv,
        family_records.keys(),
    )

    groups: list[ReconciliationGroup] = []
    representatives: list[RepresentativeCell] = []
    overlay_queue_rows: list[dict[str, object]] = []
    overlay_queue_skipped_cellless = 0
    for rank, family_id in enumerate(sorted(family_records), start=1):
        record = family_records[family_id]
        alignment_row = alignment_by_family.get(family_id, {})
        overlay_row = overlay_rows.get(family_id, {})
        group = _group_for_family(
            family_id,
            record,
            alignment_row=alignment_row,
            overlay_row=overlay_row,
            has_alignment_cells=family_id in overlay_cell_families,
        )
        groups.append(group)
        representatives.extend(
            _representatives_for_family(family_id, record, target_summary),
        )
        queue_row = _overlay_queue_row(
            rank,
            group,
            alignment_row=alignment_row,
            rt_half_window_min=rt_half_window_min,
        )
        if queue_row and family_id in overlay_cell_families:
            overlay_queue_rows.append(queue_row)
        elif queue_row:
            overlay_queue_skipped_cellless += 1

    target_contexts = _target_contexts(target_summary)
    index = ReconciliationIndex(
        groups=tuple(groups),
        representative_cells=tuple(representatives),
        target_benchmark_contexts=tuple(target_contexts),
        summary={
            "schema_version": SCHEMA_VERSION,
            "validation_label": "diagnostic_only",
            "overlay_interpretation_guide_path": str(
                OVERLAY_INTERPRETATION_GUIDE_PATH
            ),
            "input_artifacts": _input_artifacts(
                decisions_tsv=decisions_tsv,
                successor_summary_json=successor_summary_path,
                target_preflight_summary_json=target_preflight_summary_json,
                alignment_review_tsv=alignment_review_tsv,
                alignment_cells_tsv=alignment_cells_tsv,
                overlay_batch_summary_tsvs=overlay_batch_summary_tsvs,
            ),
            "successor_decision_counts": dict(
                sorted(Counter(row["successor_decision"] for row in decisions).items()),
            ),
            "matrix_contract_changed": False,
            "product_behavior_changed": False,
            "default_quant_matrix_changed": False,
            "product_writer_changed": False,
            "candidate_rows_are_matrix_rows": False,
            "review_frame": "feature_inclusion_then_identity_authority",
            "feature_inclusion_gate": (
                "CID-NL/MS2 plus MS1 feature evidence can support carrying a "
                "successor hypothesis forward as an untargeted feature."
            ),
            "identity_authority_gate": (
                "Source/successor replacement, merge, dedupe, or old-cell "
                "migration remains a separate expected-diff gate."
            ),
            "source_successor_not_mutually_exclusive": True,
            "authority_statement": (
                "Gallery review consumes the successor authority packet and "
                "overlay artifacts only; it does not install active "
                "ProductWriter or default matrix behavior."
            ),
        },
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = write_reconciliation_outputs(output_dir, index)
    gallery_html = output_dir / "backfill_evidence_reconciliation_gallery.html"
    write_reconciliation_gallery_html(gallery_html, index, output_paths=output_paths)
    overlay_review_queue_tsv = (
        output_dir / "cid_nl_default_activation_overlay_review_queue.tsv"
    )
    write_tsv(
        overlay_review_queue_tsv,
        overlay_queue_rows,
        OVERLAY_QUEUE_COLUMNS,
        lineterminator="\n",
    )
    differential_review_tsv = (
        output_dir / "cid_nl_discovery_identity_differential_review.tsv"
    )
    differential_rows = _differential_review_rows(
        decisions,
        alignment_by_family=alignment_by_family,
    )
    write_tsv(
        differential_review_tsv,
        differential_rows,
        DIFFERENTIAL_REVIEW_COLUMNS,
        lineterminator="\n",
    )

    overlay_linked_group_count = sum(1 for group in groups if group.overlay_png_path)
    requires_overlay_batch = len(overlay_queue_rows) > overlay_linked_group_count
    payload = {
        "schema_version": SCHEMA_VERSION,
        "packet_build_status": "pass",
        "overall_status": "needs_overlay_batch" if requires_overlay_batch else "pass",
        "group_count": len(groups),
        "representative_cell_count": len(representatives),
        "overlay_queue_count": len(overlay_queue_rows),
        "overlay_queue_skipped_cellless_count": overlay_queue_skipped_cellless,
        "overlay_linked_group_count": overlay_linked_group_count,
        "missing_overlay_group_count": len(groups) - overlay_linked_group_count,
        "successor_decision_counts": index.summary["successor_decision_counts"],
        "gallery_html": str(gallery_html),
        "overlay_review_queue_tsv": str(overlay_review_queue_tsv),
        "differential_review_tsv": str(differential_review_tsv),
        "differential_transition_count": len(differential_rows),
        "groups_tsv": str(output_paths["groups_tsv"]),
        "representative_cells_tsv": str(output_paths["representative_cells_tsv"]),
        "summary_json": str(output_paths["summary_json"]),
        "overlay_interpretation_guide_path": str(OVERLAY_INTERPRETATION_GUIDE_PATH),
        "requires_overlay_batch": requires_overlay_batch,
        "review_frame": "feature_inclusion_then_identity_authority",
        "feature_inclusion_gate": "diagnostic_only_candidate_feature_inclusion",
        "identity_authority_gate": "not_granted_requires_expected_diff",
        "source_successor_not_mutually_exclusive": True,
        "overlay_batch_command_hint": _overlay_batch_command_hint(
            overlay_review_queue_tsv=overlay_review_queue_tsv,
            alignment_cells_tsv=alignment_cells_tsv,
            output_dir=output_dir / "overlays",
        ),
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "candidate_rows_are_matrix_rows": False,
    }
    (output_dir / "cid_nl_default_activation_gallery_review_summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _family_records(
    decisions: Sequence[Mapping[str, str]],
    target_summary: Mapping[str, object],
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "decisions": [],
            "roles": Counter(),
            "decision_counts": Counter(),
            "target_labels": [],
        },
    )
    for row in decisions:
        decision = row["successor_decision"]
        successor = row["successor_peak_hypothesis_id"]
        candidate_ids = _split_families(row.get("candidate_new_peak_hypothesis_ids"))
        if successor:
            _add_record(records, successor, row, f"successor_{decision}")
        else:
            old_peak = row.get("old_peak_hypothesis_id", "")
            if old_peak:
                _add_record(records, old_peak, row, f"legacy_{decision}")
            for candidate in candidate_ids:
                _add_record(records, candidate, row, f"candidate_{decision}")
    for label, pair in _target_pairs(target_summary).items():
        family_id = text_value(pair.get("peak_hypothesis_id"))
        if family_id:
            records[family_id]["target_labels"].append(label)
            records[family_id]["roles"]["target_guardrail"] += 1
    return records


def _add_record(
    records: dict[str, dict[str, Any]],
    family_id: str,
    decision_row: Mapping[str, str],
    role: str,
) -> None:
    if not _FAMILY_RE.match(family_id):
        return
    record = records[family_id]
    record["decisions"].append((decision_row, role))
    record["roles"][role] += 1
    record["decision_counts"][decision_row["successor_decision"]] += 1


def _group_for_family(
    family_id: str,
    record: Mapping[str, Any],
    *,
    alignment_row: Mapping[str, str],
    overlay_row: Mapping[str, str],
    has_alignment_cells: bool,
) -> ReconciliationGroup:
    decision_counts: Counter[str] = record["decision_counts"]
    roles: Counter[str] = record["roles"]
    overlay_png = text_value(overlay_row.get("png_path"))
    overlay_trace = text_value(overlay_row.get("trace_data_json"))
    support_components: list[str] = []
    blockers: list[str] = []
    missing: list[str] = []
    visual: list[str] = []
    context: list[str] = []

    if decision_counts.get("write_authorized", 0):
        product_behavior = "cid_nl_feature_inclusion_candidate"
        evidence_state = "feature_inclusion_supported"
        reconciliation = "cid_nl_feature_inclusion_candidate"
        support_components.append("cid_nl_ms1_feature_inclusion_supported")
    elif decision_counts.get("no_write_omitted", 0):
        product_behavior = "cid_nl_feature_inclusion_not_assessable"
        evidence_state = "not_assessable"
        reconciliation = "not_assessable_missing_seed_provenance"
        blockers.append("cid_nl_successor_no_safe_write_target")
        missing.extend(_missing_tokens_for_omitted(record))
    elif decision_counts.get("no_write_detected_baseline_preserved", 0):
        product_behavior = "cid_nl_feature_already_present_preserve"
        evidence_state = "feature_inclusion_already_supported"
        reconciliation = "cid_nl_feature_inclusion_existing_row_preserved"
        support_components.append("cid_nl_existing_feature_preserved")
    else:
        product_behavior = "product_review_only"
        evidence_state = "dependent_context_only"
        reconciliation = "evidence_inconclusive"
        context.append("cid_nl_target_guardrail")

    if overlay_png:
        visual.append(text_value(overlay_row.get("family_verdict")) or "overlay_png")
    elif not has_alignment_cells:
        missing.append("missing_alignment_cell_evidence_for_overlay")
    else:
        missing.append("missing_overlay_png")

    target_labels = tuple(record.get("target_labels", ()))
    if target_labels:
        context.extend(f"target_guardrail:{label}" for label in target_labels)

    top_reason = _decision_counts_text(decision_counts)
    if roles:
        top_reason += "; roles " + _decision_counts_text(roles)

    return ReconciliationGroup(
        feature_family_id=family_id,
        seed_group_id=f"cid_nl_default_activation::{family_id}",
        seed_group_basis="cid_nl_feature_inclusion_review",
        seed_mz=text_value(alignment_row.get("family_center_mz")),
        seed_rt=text_value(alignment_row.get("family_center_rt")),
        seed_rt_window=_center_window_text(alignment_row.get("family_center_rt")),
        seed_ppm="10",
        tag_or_class=text_value(alignment_row.get("neutral_loss_tag")) or "DNA_dR",
        product_behavior_state=product_behavior,
        evidence_authority_state=evidence_state,
        reconciliation_class=reconciliation,
        include_in_primary_matrix=_bool_text(alignment_row.get("include_in_primary_matrix")),
        identity_decision=text_value(alignment_row.get("identity_decision")),
        row_flags=text_value(alignment_row.get("row_flags")),
        family_evidence=text_value(alignment_row.get("family_evidence")),
        accepted_cell_count=_int_text(alignment_row.get("accepted_cell_count")),
        detected_cell_count=decision_counts.get(
            "no_write_detected_baseline_preserved",
            0,
        ),
        rescued_cell_count=decision_counts.get("write_authorized", 0),
        provisional_cell_count=decision_counts.get("no_write_omitted", 0),
        cell_total_count=sum(decision_counts.values()),
        top_product_reason=top_reason,
        top_support_component=";".join(support_components),
        top_blocker=";".join(blockers),
        missing_evidence=tuple(sorted(set(filter(None, missing)))),
        overlay_png_path=overlay_png,
        overlay_trace_json_path=overlay_trace,
        overlay_evidence_notes=_overlay_notes(overlay_row),
        source_artifacts=(
            "successor_authority_decisions.tsv",
            "cid_nl_default_activation_preflight_summary.json",
        ),
        product_grade_support_components=tuple(support_components),
        review_only_visual_components=tuple(visual),
        dependent_context_components=tuple(context),
        blocker_components=tuple(blockers),
    )


def _representatives_for_family(
    family_id: str,
    record: Mapping[str, Any],
    target_summary: Mapping[str, object],
) -> tuple[RepresentativeCell, ...]:
    cells: list[RepresentativeCell] = []
    for row, role in record.get("decisions", ()):
        cells.append(
            RepresentativeCell(
                feature_family_id=family_id,
                seed_group_id=f"cid_nl_default_activation::{family_id}",
                representative_roles=(role,),
                sample_stem=row["sample_stem"],
                cell_status=row["input_resolution_status"],
                product_cell_state=row["matrix_effect"],
                source_peak_hypothesis_id=row["old_peak_hypothesis_id"],
                successor_peak_hypothesis_id=row["successor_peak_hypothesis_id"],
                successor_decision=row["successor_decision"],
                representative_reason=row["human_explanation"],
                source_row_key=(
                    f"{row['old_peak_hypothesis_id']}->"
                    f"{row['successor_peak_hypothesis_id'] or '<none>'}:"
                    f"{row['sample_stem']}"
                ),
            ),
        )
    for label, pair in _target_pairs(target_summary).items():
        if text_value(pair.get("peak_hypothesis_id")) != family_id:
            continue
        focus_row = _nested_mapping(pair, "provenance", "focus_sample_row")
        cells.append(
            RepresentativeCell(
                feature_family_id=family_id,
                seed_group_id=f"cid_nl_default_activation::{family_id}",
                representative_roles=(f"target_guardrail:{label}",),
                sample_stem=text_value(focus_row.get("sample_stem")),
                cell_status=text_value(focus_row.get("production_cell_status")),
                product_cell_state=text_value(focus_row.get("primary_matrix_area_source")),
                representative_reason=(
                    f"{pair.get('target_precursor_mz')} -> "
                    f"{pair.get('target_product_mz')} status={pair.get('status')}"
                ),
                source_row_key=text_value(focus_row.get("source_candidate_id")),
            ),
        )
    return tuple(cells)


def _overlay_queue_row(
    rank: int,
    group: ReconciliationGroup,
    *,
    alignment_row: Mapping[str, str],
    rt_half_window_min: float,
) -> dict[str, object] | None:
    row = _overlay_queue_identity_row(
        rank,
        group.feature_family_id,
        alignment_row=alignment_row,
        rt_half_window_min=rt_half_window_min,
    )
    if row is None:
        return None
    return {
        **row,
        "cid_nl_review_reason": group.top_product_reason,
        "successor_decision_counts": group.top_product_reason,
    }


def _overlay_queue_identity_row(
    rank: int,
    family_id: str,
    *,
    alignment_row: Mapping[str, str],
    rt_half_window_min: float,
) -> dict[str, object] | None:
    mz = _float_text(alignment_row.get("family_center_mz"))
    rt = _float_text(alignment_row.get("family_center_rt"))
    if mz is None or rt is None:
        return None
    rt_min = max(0.0, rt - rt_half_window_min)
    rt_max = rt + rt_half_window_min
    return {
        "feature_family_id": family_id,
        "seed_group_id": f"cid_nl_default_activation::{family_id}",
        "family_center_mz": f"{mz:.6g}",
        "family_center_rt": f"{rt:.6g}",
        "suggested_rt_min": f"{rt_min:.6g}",
        "suggested_rt_max": f"{rt_max:.6g}",
        "suggested_output_prefix": (
            f"{rank:03d}_{family_id.lower()}_cid_nl_activation_review"
        ),
    }


def _overlay_expected_rows_by_family(
    family_records: Mapping[str, Mapping[str, Any]],
    *,
    alignment_by_family: Mapping[str, Mapping[str, str]],
    rt_half_window_min: float,
) -> dict[str, Mapping[str, object]]:
    rows: dict[str, Mapping[str, object]] = {}
    for rank, family_id in enumerate(sorted(family_records), start=1):
        row = _overlay_queue_identity_row(
            rank,
            family_id,
            alignment_row=alignment_by_family.get(family_id, {}),
            rt_half_window_min=rt_half_window_min,
        )
        if row is not None:
            rows[family_id] = row
    return rows


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
            {}
            if successor == "<none>"
            else alignment_by_family.get(successor, {})
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


def _target_contexts(
    target_summary: Mapping[str, object],
) -> tuple[TargetBenchmarkContext, ...]:
    contexts: list[TargetBenchmarkContext] = []
    for label, pair in _target_pairs(target_summary).items():
        contexts.append(
            TargetBenchmarkContext(
                target_label=label,
                role="cid_nl_default_activation_guardrail",
                active_tag=text_value(pair.get("target_tag")) or "DNA_dR",
                status=text_value(pair.get("status")),
                selected_feature_id=text_value(pair.get("peak_hypothesis_id")),
                primary_feature_ids=(text_value(pair.get("peak_hypothesis_id")),),
                targeted_positive_count=text_value(
                    _nested_mapping(pair, "identity").get("accepted_cell_count"),
                ),
                untargeted_positive_count=text_value(
                    _nested_mapping(pair, "identity").get("accepted_sample_count"),
                ),
                note=(
                    f"{pair.get('target_precursor_mz')} -> "
                    f"{pair.get('target_product_mz')}"
                ),
            )
        )
    return tuple(contexts)


def _read_overlay_rows(
    source_root: Path,
    paths: Sequence[Path],
    *,
    expected_rows_by_family: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, str]]:
    by_family: dict[str, dict[str, str]] = {}
    mismatches: list[tuple[str, str]] = []
    for path in paths:
        resolved = _resolve(source_root, path)
        if not resolved.exists():
            continue
        for row in read_tsv_required(resolved, OVERLAY_SUMMARY_COLUMNS):
            if row.get("status") != "success":
                continue
            family_id = row["feature_family_id"]
            expected = expected_rows_by_family.get(family_id)
            if expected is None:
                continue
            mismatch = _overlay_identity_mismatch(row, expected)
            if mismatch:
                mismatches.append((family_id, f"{resolved}: {mismatch}"))
                continue
            if family_id not in by_family:
                by_family[family_id] = dict(row)
    unresolved = [
        message
        for family_id, message in mismatches
        if family_id not in by_family
    ]
    if unresolved:
        raise ValueError(
            "overlay summary row identity mismatch; refusing stale overlay link: "
            + "; ".join(unresolved[:5]),
        )
    return by_family


def _overlay_identity_mismatch(
    row: Mapping[str, str],
    expected: Mapping[str, object],
) -> str:
    text_checks = (
        ("seed_group_id", "seed_group_id"),
        ("output_prefix", "suggested_output_prefix"),
    )
    for observed_key, expected_key in text_checks:
        observed = text_value(row.get(observed_key))
        expected_value = text_value(expected.get(expected_key))
        if observed != expected_value:
            return f"{observed_key}={observed!r} expected {expected_value!r}"

    float_checks = (
        ("mz", "family_center_mz"),
        ("rt_min", "suggested_rt_min"),
        ("rt_max", "suggested_rt_max"),
        ("family_center_rt", "family_center_rt"),
    )
    for observed_key, expected_key in float_checks:
        observed = _float_text(row.get(observed_key))
        expected_value = _float_text(expected.get(expected_key))
        if observed is None or expected_value is None:
            return f"{observed_key}={row.get(observed_key)!r} is not numeric"
        tolerance = max(1e-6, abs(expected_value) * 1e-9)
        if abs(observed - expected_value) > tolerance:
            return (
                f"{observed_key}={observed:.6g} "
                f"expected {expected_value:.6g}"
            )
    return ""


def _families_in_alignment_cells(
    alignment_cells_tsv: Path,
    candidate_families: Sequence[str],
) -> set[str]:
    pending = set(candidate_families)
    found: set[str] = set()
    if not pending:
        return found
    with alignment_cells_tsv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if "feature_family_id" not in (reader.fieldnames or ()):
            raise ValueError(f"{alignment_cells_tsv}: missing feature_family_id")
        for row in reader:
            family_id = row.get("feature_family_id", "")
            if family_id in pending:
                found.add(family_id)
                pending.remove(family_id)
                if not pending:
                    break
    return found


def _input_artifacts(**paths: object) -> dict[str, object]:
    artifacts: dict[str, object] = {}
    for key, value in paths.items():
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Path)):
            artifacts[key] = [
                _artifact_entry(Path(item)) for item in value if text_value(item)
            ]
        elif value:
            artifacts[key] = _artifact_entry(Path(value))
    return artifacts


def _artifact_entry(path: Path) -> dict[str, object]:
    entry: dict[str, object] = {"path": str(path)}
    if path.exists() and path.stat().st_size <= 64 * 1024 * 1024:
        entry["sha256"] = file_sha256(path)
    elif path.exists():
        entry["size_bytes"] = path.stat().st_size
    return entry


def _overlay_notes(row: Mapping[str, str]) -> tuple[str, ...]:
    if not row:
        return ()
    notes = []
    for key in (
        "family_verdict",
        "shape_supported_fraction",
        "absolute_own_max_shape_supported_fraction",
        "local_apex_supported_fraction",
    ):
        value = text_value(row.get(key))
        if value:
            notes.append(f"{key}={value}")
    return tuple(notes)


def _missing_tokens_for_omitted(record: Mapping[str, Any]) -> list[str]:
    statuses = {
        row["input_resolution_status"]
        for row, _role in record.get("decisions", ())
        if row["successor_decision"] == "no_write_omitted"
    }
    tokens = []
    if any("missing_identity" in status for status in statuses):
        tokens.append("missing_seed_provenance_or_identity")
    if any("ambiguous" in status for status in statuses):
        tokens.append("ambiguous_successor_identity")
    return tokens or ["missing_seed_provenance"]


def _target_pairs(
    target_summary: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    value = target_summary.get("target_pairs")
    if not isinstance(value, Mapping):
        return {}
    return {
        text_value(key): item
        for key, item in value.items()
        if text_value(key) and isinstance(item, Mapping)
    }


def _artifact_path(summary: Mapping[str, object], key: str) -> Path:
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError(f"summary missing artifacts.{key}")
    entry = artifacts.get(key)
    if not isinstance(entry, Mapping):
        raise ValueError(f"summary missing artifacts.{key}")
    path = text_value(entry.get("path"))
    if not path:
        raise ValueError(f"summary artifacts.{key} missing path")
    return Path(path)


def _nested_mapping(value: Mapping[str, object], *keys: str) -> Mapping[str, object]:
    current: object = value
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return current if isinstance(current, Mapping) else {}


def _resolve(source_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else source_root / path


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _split_families(value: object) -> tuple[str, ...]:
    return tuple(
        item
        for item in (text.strip() for text in text_value(value).split(";"))
        if _FAMILY_RE.match(item)
    )


def _decision_counts_text(counts: Mapping[str, int]) -> str:
    return ";".join(f"{key}={value}" for key, value in sorted(counts.items()) if value)


def _bool_text(value: object) -> bool:
    return text_value(value).upper() in {"TRUE", "1", "YES"}


def _int_text(value: object) -> int:
    try:
        return int(float(text_value(value)))
    except ValueError:
        return 0


def _float_text(value: object) -> float | None:
    try:
        return float(text_value(value))
    except ValueError:
        return None


def _center_window_text(value: object, half_window: float = 1.5) -> str:
    center = _float_text(value)
    if center is None:
        return ""
    return f"{max(0.0, center - half_window):.6g}-{center + half_window:.6g}"


def _overlay_batch_command_hint(
    *,
    overlay_review_queue_tsv: Path,
    alignment_cells_tsv: Path,
    output_dir: Path,
) -> str:
    return (
        ".venv\\Scripts\\python.exe -m tools.diagnostics.family_ms1_overlay_batch "
        f"--review-queue-tsv {overlay_review_queue_tsv} "
        f"--alignment-cells {alignment_cells_tsv} "
        "--raw-dir C:\\Xcalibur\\data\\20260106_CSMU_NAA_Tissue_R "
        "--dll-dir C:\\Xcalibur\\system\\programs "
        f"--output-dir {output_dir} --limit <overlay_queue_count> "
        "--no-pdf --reuse-existing"
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--successor-packet-dir",
        type=Path,
        default=DEFAULT_SUCCESSOR_PACKET_DIR,
    )
    parser.add_argument(
        "--target-preflight-summary-json",
        type=Path,
        default=DEFAULT_TARGET_PREFLIGHT_SUMMARY,
    )
    parser.add_argument("--alignment-review-tsv", type=Path)
    parser.add_argument("--alignment-cells-tsv", type=Path)
    parser.add_argument("--overlay-batch-summary-tsv", type=Path, action="append")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--rt-half-window-min", type=float, default=1.5)
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
