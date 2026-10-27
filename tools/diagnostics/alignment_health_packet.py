"""Build a no-RAW alignment health packet from existing alignment artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from xic_extractor.tabular_io import (
    file_sha256,
    optional_float,
    read_tsv_with_header,
    split_semicolon_labels,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "alignment_health_packet"

REVIEW_REQUIRED = (
    "feature_id",
    "detected_count",
    "ambiguous_ms1_owner_count",
    "duplicate_assigned_count",
    "unchecked_count",
    "accepted_cell_count",
    "accepted_backfill_count",
    "review_backfill_count",
    "identity_decision",
    "identity_confidence",
    "primary_evidence",
    "row_flags",
    "reason",
)
MATRIX_REQUIRED = ("Mz", "RT")
CELL_OPTIONAL_REQUIRED = ("feature_id", "sample_stem", "status")
SEED_AUDIT_REQUIRED = ("feature_id", "sample_stem", "status")

SUMMARY_COLUMNS = ("metric", "value", "detail")
SENTINEL_COLUMNS = (
    "rank",
    "feature_id",
    "issue_class",
    "severity_score",
    "detected_count",
    "accepted_cell_count",
    "accepted_backfill_count",
    "review_backfill_count",
    "ambiguous_ms1_owner_count",
    "duplicate_assigned_count",
    "unchecked_count",
    "row_flags",
    "identity_decision",
    "identity_confidence",
    "primary_evidence",
    "reason",
    "cell_status_counts",
    "seed_audit_row_count",
    "recommended_action",
)


@dataclass(frozen=True)
class AlignmentHealthOutputs:
    summary_json: Path
    summary_tsv: Path
    sentinels_tsv: Path


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = build_alignment_health_packet(
            alignment_dir=args.alignment_dir,
            output_dir=args.output_dir,
            sentinel_limit=args.sentinel_limit,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Alignment health summary JSON: {outputs.summary_json}")
    print(f"Alignment health summary TSV: {outputs.summary_tsv}")
    print(f"Alignment health sentinel TSV: {outputs.sentinels_tsv}")
    return 0


def build_alignment_health_packet(
    *,
    alignment_dir: Path,
    output_dir: Path,
    sentinel_limit: int = 50,
) -> AlignmentHealthOutputs:
    if sentinel_limit < 1:
        raise ValueError("sentinel_limit must be >= 1")
    alignment_dir = alignment_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    review_path = alignment_dir / "alignment_review.tsv"
    matrix_path = alignment_dir / "alignment_matrix.tsv"
    review_header, review_rows = read_tsv_with_header(
        review_path,
        required_columns=REVIEW_REQUIRED,
        encoding="utf-8-sig",
    )
    matrix_header, matrix_rows = read_tsv_with_header(
        matrix_path,
        required_columns=MATRIX_REQUIRED,
        encoding="utf-8-sig",
    )
    identity_rows = [
        row for row in review_rows if row.get("matrix_row_index", "").strip()
    ]
    del review_header

    cell_path = _first_existing(
        alignment_dir / "alignment_backfill_cell_evidence.tsv",
        alignment_dir / "alignment_cells.tsv",
    )
    cell_rows: list[dict[str, str]] = []
    if cell_path is not None:
        _cell_header, cell_rows = read_tsv_with_header(
            cell_path,
            required_columns=CELL_OPTIONAL_REQUIRED,
            encoding="utf-8-sig",
        )
    seed_audit_path = alignment_dir / "alignment_owner_backfill_seed_audit.tsv"
    seed_rows: list[dict[str, str]] = []
    if seed_audit_path.is_file():
        _seed_header, seed_rows = read_tsv_with_header(
            seed_audit_path,
            required_columns=SEED_AUDIT_REQUIRED,
            encoding="utf-8-sig",
        )

    cell_status_by_family = _status_counts_by_family(cell_rows)
    seed_count_by_family = Counter(
        text_value(row.get("feature_id")) for row in seed_rows
    )
    seed_count_by_family.pop("", None)

    sentinels = _sentinel_rows(
        review_rows,
        cell_status_by_family=cell_status_by_family,
        seed_count_by_family=seed_count_by_family,
        limit=sentinel_limit,
    )
    summary_metrics = _summary_metrics(
        review_rows=review_rows,
        matrix_rows=matrix_rows,
        matrix_header=matrix_header,
        identity_rows=identity_rows,
        cell_rows=cell_rows,
        seed_rows=seed_rows,
        sentinels=sentinels,
    )
    inputs = _input_descriptors(
        review_path=review_path,
        matrix_path=matrix_path,
        cell_path=cell_path,
        seed_audit_path=seed_audit_path if seed_audit_path.is_file() else None,
    )
    packet = {
        "schema_version": SCHEMA_VERSION,
        "alignment_dir": str(alignment_dir),
        "inputs": inputs,
        "summary_metrics": summary_metrics,
        "sentinel_count": len(sentinels),
        "sentinel_limit": sentinel_limit,
        "sentinel_rows": sentinels,
        "status": "diagnostic_only",
    }

    summary_json = output_dir / "alignment_health_summary.json"
    summary_tsv = output_dir / "alignment_health_summary.tsv"
    sentinels_tsv = output_dir / "alignment_health_family_sentinels.tsv"
    summary_json.write_text(
        json.dumps(packet, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_tsv(summary_tsv, _summary_tsv_rows(summary_metrics), SUMMARY_COLUMNS)
    write_tsv(sentinels_tsv, sentinels, SENTINEL_COLUMNS)
    return AlignmentHealthOutputs(
        summary_json=summary_json,
        summary_tsv=summary_tsv,
        sentinels_tsv=sentinels_tsv,
    )


def _summary_metrics(
    *,
    review_rows: Sequence[Mapping[str, str]],
    matrix_rows: Sequence[Mapping[str, str]],
    matrix_header: Sequence[str],
    identity_rows: Sequence[Mapping[str, str]],
    cell_rows: Sequence[Mapping[str, str]],
    seed_rows: Sequence[Mapping[str, str]],
    sentinels: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    sample_columns = [column for column in matrix_header if column not in {"Mz", "RT"}]
    row_flag_counts: Counter[str] = Counter()
    identity_counts: Counter[str] = Counter()
    for row in review_rows:
        identity_counts[text_value(row.get("identity_decision")) or "blank"] += 1
        for flag in split_semicolon_labels(row.get("row_flags")):
            row_flag_counts[flag] += 1
    cell_status_counts = Counter(text_value(row.get("status")) for row in cell_rows)
    cell_status_counts.pop("", None)
    seed_status_counts = Counter(text_value(row.get("status")) for row in seed_rows)
    seed_status_counts.pop("", None)
    return {
        "review_family_count": len(review_rows),
        "matrix_row_count": len(matrix_rows),
        "matrix_sample_column_count": len(sample_columns),
        "matrix_review_row_count": len(identity_rows),
        "cell_evidence_row_count": len(cell_rows),
        "seed_audit_row_count": len(seed_rows),
        "detected_count_total": _sum_int(review_rows, "detected_count"),
        "accepted_cell_count_total": _sum_int(review_rows, "accepted_cell_count"),
        "accepted_backfill_count_total": _sum_int(
            review_rows,
            "accepted_backfill_count",
        ),
        "review_backfill_count_total": _sum_int(review_rows, "review_backfill_count"),
        "ambiguous_ms1_owner_count_total": _sum_int(
            review_rows,
            "ambiguous_ms1_owner_count",
        ),
        "duplicate_assigned_count_total": _sum_int(
            review_rows,
            "duplicate_assigned_count",
        ),
        "unchecked_count_total": _sum_int(review_rows, "unchecked_count"),
        "identity_decision_counts": dict(sorted(identity_counts.items())),
        "row_flag_counts": dict(sorted(row_flag_counts.items())),
        "cell_status_counts": dict(sorted(cell_status_counts.items())),
        "seed_audit_status_counts": dict(sorted(seed_status_counts.items())),
        "sentinel_count": len(sentinels),
    }


def _sentinel_rows(
    review_rows: Sequence[Mapping[str, str]],
    *,
    cell_status_by_family: Mapping[str, Counter[str]],
    seed_count_by_family: Mapping[str, int],
    limit: int,
) -> list[dict[str, object]]:
    candidates = []
    for row in review_rows:
        family_id = text_value(row.get("feature_id"))
        if not family_id:
            continue
        issues = _issue_classes(row)
        score = _severity_score(row, issues)
        if not issues and score <= 0:
            continue
        candidates.append(
            {
                "feature_id": family_id,
                "issue_class": ";".join(issues),
                "severity_score": score,
                "detected_count": _int_value(row.get("detected_count")),
                "accepted_cell_count": _int_value(row.get("accepted_cell_count")),
                "accepted_backfill_count": _int_value(
                    row.get("accepted_backfill_count")
                ),
                "review_backfill_count": _int_value(row.get("review_backfill_count")),
                "ambiguous_ms1_owner_count": _int_value(
                    row.get("ambiguous_ms1_owner_count"),
                ),
                "duplicate_assigned_count": _int_value(
                    row.get("duplicate_assigned_count"),
                ),
                "unchecked_count": _int_value(row.get("unchecked_count")),
                "row_flags": text_value(row.get("row_flags")),
                "identity_decision": text_value(row.get("identity_decision")),
                "identity_confidence": text_value(row.get("identity_confidence")),
                "primary_evidence": text_value(row.get("primary_evidence")),
                "reason": text_value(row.get("reason")),
                "cell_status_counts": _format_counter(
                    cell_status_by_family.get(family_id, Counter()),
                ),
                "seed_audit_row_count": seed_count_by_family.get(family_id, 0),
                "recommended_action": _recommended_action(issues),
            },
        )
    candidates.sort(
        key=lambda row: (
            -int(cast(int, row["severity_score"])),
            text_value(row["feature_id"]),
        ),
    )
    output: list[dict[str, object]] = []
    for rank, candidate in enumerate(candidates[:limit], start=1):
        output.append({"rank": rank, **candidate})
    return output


def _issue_classes(row: Mapping[str, str]) -> tuple[str, ...]:
    issues: list[str] = []
    if _int_value(row.get("ambiguous_ms1_owner_count")) > 0:
        issues.append("owner_ambiguity")
    if _int_value(row.get("duplicate_assigned_count")) > 0:
        issues.append("duplicate_claim")
    if _int_value(row.get("review_backfill_count")) > 0:
        issues.append("backfill_review_dependency")
    if _int_value(row.get("accepted_backfill_count")) > 0:
        issues.append("accepted_backfill_dependency")
    if _int_value(row.get("unchecked_count")) > 0:
        issues.append("unchecked_pressure")
    flags = set(split_semicolon_labels(row.get("row_flags")))
    if "family_consolidation_loser" in flags:
        issues.append("consolidation_loser")
    if "ambiguous_ms1_owner_pressure" in flags:
        issues.append("owner_pressure_flag")
    return tuple(dict.fromkeys(issues))


def _severity_score(row: Mapping[str, str], issues: Sequence[str]) -> int:
    score = 0
    score += _int_value(row.get("ambiguous_ms1_owner_count")) * 4
    score += _int_value(row.get("duplicate_assigned_count")) * 5
    score += _int_value(row.get("review_backfill_count")) * 2
    score += _int_value(row.get("accepted_backfill_count")) * 3
    score += _int_value(row.get("unchecked_count"))
    if "consolidation_loser" in issues:
        score += 8
    if "owner_pressure_flag" in issues:
        score += 3
    return score


def _recommended_action(issues: Sequence[str]) -> str:
    issue_set = set(issues)
    if "owner_ambiguity" in issue_set or "duplicate_claim" in issue_set:
        return "inspect_owner_assignment"
    if "consolidation_loser" in issue_set:
        return "inspect_primary_consolidation"
    if issue_set & {"backfill_review_dependency", "accepted_backfill_dependency"}:
        return "inspect_backfill_dependency"
    if "unchecked_pressure" in issue_set:
        return "inspect_unchecked_cells"
    return "context_only"


def _status_counts_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, Counter[str]]:
    output: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        family_id = text_value(row.get("feature_id"))
        status = text_value(row.get("status"))
        if family_id and status:
            output[family_id][status] += 1
    return dict(output)


def _summary_tsv_rows(metrics: Mapping[str, object]) -> list[dict[str, object]]:
    rows = []
    for key, value in metrics.items():
        if isinstance(value, Mapping):
            rows.append(
                {
                    "metric": key,
                    "value": sum(int(v) for v in value.values() if isinstance(v, int)),
                    "detail": json.dumps(value, sort_keys=True),
                },
            )
        else:
            rows.append({"metric": key, "value": value, "detail": ""})
    return rows


def _input_descriptors(
    *,
    review_path: Path,
    matrix_path: Path,
    cell_path: Path | None,
    seed_audit_path: Path | None,
) -> dict[str, dict[str, object]]:
    inputs = {
        "alignment_review_tsv": _descriptor(review_path),
        "alignment_matrix_tsv": _descriptor(matrix_path),
    }
    if cell_path is not None:
        inputs["alignment_cell_evidence_tsv"] = _descriptor(cell_path)
    if seed_audit_path is not None:
        inputs["alignment_owner_backfill_seed_audit_tsv"] = _descriptor(
            seed_audit_path,
        )
    return inputs


def _descriptor(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "sha256": file_sha256(path, uppercase=True),
        "size_bytes": path.stat().st_size,
    }


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _sum_int(rows: Sequence[Mapping[str, str]], column: str) -> int:
    return sum(_int_value(row.get(column)) for row in rows)


def _int_value(value: object) -> int:
    parsed = optional_float(value)
    return int(parsed) if parsed is not None else 0


def _format_counter(counter: Mapping[str, int]) -> str:
    return ";".join(f"{key}:{counter[key]}" for key in sorted(counter))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alignment-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sentinel-limit", type=int, default=50)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
