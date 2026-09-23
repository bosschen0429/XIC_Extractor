from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from tools.diagnostics.diagnostic_io import write_tsv

_REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "detected_count",
    "identity_decision",
    "include_in_primary_matrix",
)
_CELLS_REQUIRED_COLUMNS = ("feature_family_id", "sample_stem", "status")


@dataclass(frozen=True)
class _CellInputIndex:
    cells_by_feature: dict[str, tuple[dict[str, str], ...]]
    sample_order: tuple[str, ...]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = build_economics(
        alignment_dir=args.alignment_dir,
        owner_backfill_min_detected_samples=args.owner_backfill_min_detected_samples,
    )
    write_outputs(args.output_dir, result)
    return 0


def build_economics(
    *,
    alignment_dir: Path,
    owner_backfill_min_detected_samples: int,
) -> dict[str, Any]:
    if owner_backfill_min_detected_samples < 1:
        raise ValueError("owner_backfill_min_detected_samples must be >= 1")
    review_rows = _read_tsv(
        alignment_dir / "alignment_review.tsv",
        required_columns=_REVIEW_REQUIRED_COLUMNS,
    )
    cell_rows = _read_tsv(
        alignment_dir / "alignment_cells.tsv",
        required_columns=_CELLS_REQUIRED_COLUMNS,
    )
    cell_index = _index_cell_rows(cell_rows)
    features = [
        _feature_economics(
            row,
            cell_index.cells_by_feature.get(row["feature_family_id"], ()),
            sample_order=cell_index.sample_order,
            owner_backfill_min_detected_samples=(
                owner_backfill_min_detected_samples
            ),
        )
        for row in review_rows
    ]
    summary = _summary_rows(features)
    return {
        "alignment_dir": str(alignment_dir),
        "owner_backfill_min_detected_samples": owner_backfill_min_detected_samples,
        "sample_count": len(cell_index.sample_order),
        "totals": _totals(features),
        "summary": summary,
        "features": sorted(
            features,
            key=lambda row: (
                -int(row["request_extract_count_estimate"]),
                -int(row["request_target_count"]),
                str(row["feature_family_id"]),
            ),
        ),
    }


def write_outputs(output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_tsv(
        output_dir / "owner_backfill_request_economics_summary.tsv",
        result["summary"],
    )
    _write_tsv(
        output_dir / "owner_backfill_request_economics_features.tsv",
        result["features"],
    )
    (output_dir / "owner_backfill_request_economics.json").write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "owner_backfill_request_economics.md").write_text(
        _markdown(result),
        encoding="utf-8",
    )


def _feature_economics(
    review_row: dict[str, str],
    cells: tuple[dict[str, str], ...],
    *,
    sample_order: tuple[str, ...],
    owner_backfill_min_detected_samples: int,
) -> dict[str, Any]:
    feature_id = review_row["feature_family_id"]
    cells_by_sample = {cell["sample_stem"]: cell for cell in cells}
    detected_samples = {
        cell["sample_stem"] for cell in cells if cell.get("status") == "detected"
    }
    review_only = _is_review_only(review_row)
    eligible = (
        not review_only
        and len(detected_samples) >= owner_backfill_min_detected_samples
    )
    preconsolidated = _is_pre_backfill_consolidated(review_row)
    confirmable_detected_samples = (
        _confirmable_detected_samples(cells) if preconsolidated else set()
    )
    requested_samples = _requested_samples(
        sample_order,
        detected_samples=detected_samples,
        eligible=eligible,
        confirmable_detected_samples=confirmable_detected_samples,
    )
    status_counts = Counter(
        cells_by_sample.get(sample, {}).get("status", "missing_cell")
        for sample in requested_samples
    )
    seed_center_count_estimate = 2 if preconsolidated else 1
    request_target_count = len(requested_samples)
    include_in_primary = _is_true(review_row.get("include_in_primary_matrix", ""))
    return {
        "feature_family_id": feature_id,
        "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
        "identity_decision": review_row.get("identity_decision", ""),
        "include_in_primary_matrix": include_in_primary,
        "is_review_only_inferred": review_only,
        "is_pre_backfill_consolidated": preconsolidated,
        "detected_sample_count": len(detected_samples),
        "eligible_for_backfill": eligible,
        "seed_center_count_estimate": seed_center_count_estimate,
        "request_target_count": request_target_count,
        "request_extract_count_estimate": (
            request_target_count * seed_center_count_estimate
        ),
        "missing_sample_target_count": len(
            [sample for sample in requested_samples if sample not in detected_samples]
        ),
        "confirmation_target_count": len(
            [sample for sample in requested_samples if sample in detected_samples]
        ),
        "rescued_target_count": status_counts.get("backfilled", 0),
        "absent_target_count": status_counts.get("absent", 0),
        "duplicate_assigned_target_count": status_counts.get(
            "duplicate_assigned",
            0,
        ),
        "ambiguous_target_count": status_counts.get("ambiguous_ms1_owner", 0),
        "detected_confirmation_target_count": status_counts.get("detected", 0),
        "unchecked_target_count": status_counts.get("unchecked", 0),
        "missing_cell_target_count": status_counts.get("missing_cell", 0),
        "accepted_backfill_count": _int_value(
            review_row.get("accepted_backfill_count", "")
        ),
        "review_backfill_count": _int_value(
            review_row.get("review_backfill_count", "")
        ),
        "row_flags": review_row.get("row_flags", ""),
        "warning": review_row.get("warning", ""),
        "family_evidence": review_row.get("family_evidence", ""),
    }


def _requested_samples(
    sample_order: tuple[str, ...],
    *,
    detected_samples: set[str],
    eligible: bool,
    confirmable_detected_samples: set[str],
) -> tuple[str, ...]:
    if not eligible:
        return ()
    return tuple(
        sample
        for sample in sample_order
        if sample not in detected_samples or sample in confirmable_detected_samples
    )


def _confirmable_detected_samples(
    cells: tuple[dict[str, str], ...],
) -> set[str]:
    detected_cells = [cell for cell in cells if cell.get("status") == "detected"]
    areas_by_sample = {
        cell["sample_stem"]: area
        for cell in detected_cells
        for area in (_positive_finite(cell.get("area", "")),)
        if area is not None
    }
    if not areas_by_sample:
        return set()
    family_area = float(median(areas_by_sample.values()))
    return {
        sample
        for sample, area in areas_by_sample.items()
        if area <= family_area * 0.25
    }


def _summary_rows(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for feature in features:
        if not feature["eligible_for_backfill"]:
            continue
        key = (
            str(feature["identity_decision"]),
            str(feature["neutral_loss_tag"]),
            str(feature["include_in_primary_matrix"]),
        )
        grouped[key].append(feature)
    rows = []
    for (
        identity_decision,
        neutral_loss_tag,
        include_in_primary_matrix,
    ), group in sorted(grouped.items()):
        rows.append(
            {
                "identity_decision": identity_decision,
                "neutral_loss_tag": neutral_loss_tag,
                "include_in_primary_matrix": include_in_primary_matrix,
                **_aggregate(group),
            }
        )
    return rows


def _totals(features: list[dict[str, Any]]) -> dict[str, int]:
    eligible = [feature for feature in features if feature["eligible_for_backfill"]]
    aggregate = _aggregate(eligible)
    aggregate.update(
        {
            "family_count": len(features),
            "eligible_family_count": len(eligible),
            "skipped_review_only_family_count": sum(
                1 for feature in features if feature["is_review_only_inferred"]
            ),
            "skipped_min_detected_family_count": sum(
                1
                for feature in features
                if (
                    not feature["is_review_only_inferred"]
                    and not feature["eligible_for_backfill"]
                )
            ),
            "production_request_target_count": sum(
                int(feature["request_target_count"])
                for feature in eligible
                if feature["include_in_primary_matrix"]
            ),
            "non_primary_request_target_count": sum(
                int(feature["request_target_count"])
                for feature in eligible
                if not feature["include_in_primary_matrix"]
            ),
        }
    )
    return aggregate


def _aggregate(features: list[dict[str, Any]]) -> dict[str, int]:
    fields = (
        "request_target_count",
        "request_extract_count_estimate",
        "missing_sample_target_count",
        "confirmation_target_count",
        "rescued_target_count",
        "absent_target_count",
        "duplicate_assigned_target_count",
        "ambiguous_target_count",
        "detected_confirmation_target_count",
        "unchecked_target_count",
        "missing_cell_target_count",
        "accepted_backfill_count",
        "review_backfill_count",
    )
    result = {"eligible_group_family_count": len(features)}
    for field in fields:
        result[field] = sum(int(feature[field]) for feature in features)
    return result


def _is_review_only(row: dict[str, str]) -> bool:
    text = ";".join(
        (
            row.get("family_evidence", ""),
            row.get("primary_evidence", ""),
            row.get("identity_reason", ""),
        )
    ).lower()
    return "review_only" in text


def _is_pre_backfill_consolidated(row: dict[str, str]) -> bool:
    return "pre_backfill_identity_consolidated" in row.get(
        "family_evidence",
        "",
    )


def _index_cell_rows(
    rows: tuple[dict[str, str], ...],
) -> _CellInputIndex:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen_samples: set[str] = set()
    sample_order: list[str] = []
    for row in rows:
        grouped[row["feature_family_id"]].append(row)
        sample = row.get("sample_stem", "")
        if sample and sample not in seen_samples:
            seen_samples.add(sample)
            sample_order.append(sample)
    return _CellInputIndex(
        cells_by_feature={
            feature_id: tuple(items) for feature_id, items in grouped.items()
        },
        sample_order=tuple(sample_order),
    )


def _read_tsv(
    path: Path,
    *,
    required_columns: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            fieldnames = tuple(reader.fieldnames or ())
            missing = [
                column for column in required_columns if column not in fieldnames
            ]
            if missing:
                raise ValueError(
                    f"{path}: missing required columns: {', '.join(missing)}"
                )
            return tuple(dict(row) for row in reader)
    except OSError as exc:
        raise ValueError(f"{path}: could not read TSV: {exc}") from exc


def _write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    write_tsv(
        path,
        rows,
        tuple(rows[0]),
        extrasaction="raise",
        formatter=_format_value,
    )


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _markdown(result: dict[str, Any]) -> str:
    totals = result["totals"]
    lines = [
        "# Owner Backfill Request Economics",
        "",
        f"- alignment_dir: `{result['alignment_dir']}`",
        "- owner_backfill_min_detected_samples: "
        f"{result['owner_backfill_min_detected_samples']}",
        f"- sample_count: {result['sample_count']}",
        "",
        "## Totals",
        "",
        f"- eligible_family_count: {totals['eligible_family_count']}",
        f"- request_target_count: {totals['request_target_count']}",
        "- request_extract_count_estimate: "
        f"{totals['request_extract_count_estimate']}",
        "- production_request_target_count: "
        f"{totals['production_request_target_count']}",
        "- non_primary_request_target_count: "
        f"{totals['non_primary_request_target_count']}",
        f"- rescued_target_count: {totals['rescued_target_count']}",
        f"- absent_target_count: {totals['absent_target_count']}",
        "",
        "## Largest Feature Costs",
        "",
        (
            "| Feature | Tag | Identity | Primary | Targets | Extracts | "
            "Rescued | Absent |"
        ),
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for feature in result["features"][:20]:
        lines.append(
            "| "
            f"{feature['feature_family_id']} | "
            f"{feature['neutral_loss_tag']} | "
            f"{feature['identity_decision']} | "
            f"{feature['include_in_primary_matrix']} | "
            f"{feature['request_target_count']} | "
            f"{feature['request_extract_count_estimate']} | "
            f"{feature['rescued_target_count']} | "
            f"{feature['absent_target_count']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "t", "yes", "y"}


def _int_value(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        return 0


def _positive_finite(value: str) -> float | None:
    try:
        number = float(value)
    except ValueError:
        return None
    if math.isfinite(number) and number > 0:
        return number
    return None


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize owner-backfill request cost by final row identity.",
    )
    parser.add_argument("--alignment-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--owner-backfill-min-detected-samples", type=int, default=1)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
