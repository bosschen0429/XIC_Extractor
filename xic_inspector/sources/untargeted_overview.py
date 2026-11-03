"""Reader-oriented counts and ordering from saved routes and cell decisions."""

from collections import Counter

from xic_extractor.alignment_explanation.reader import (
    read_review_row_counts,
)


def review_overview(path, summary):
    rows = {}
    coverage: Counter[str] = Counter()
    sample_count = len(summary["samples"])
    for record in read_review_row_counts(path, summary):
        row, numeric = record["row"], record["numeric_samples"]
        destination = row["route"]["destination"]
        rows[row["feature_id"]] = {
            "destination": destination,
            "numeric_count": len(numeric),
            "unresolved_count": record["unresolved_count"],
        }
        if destination == "matrix":
            coverage.update(numeric)
    matrix = [r for r in rows.values() if r["destination"] == "matrix"]
    calibration = (summary["known_istd_reference"] or {}).get("calibration", {})
    return {
        "kind": "untargeted",
        "matrix_count": len(matrix),
        "unresolved_count": sum(r["unresolved_count"] > 0 for r in matrix),
        "complete_count": sum(r["numeric_count"] == sample_count for r in matrix),
        "archive_count": sum(r["destination"] == "archive" for r in rows.values()),
        "uncalibrated_count": sum(
            s.get("status") == "unavailable" for s in calibration.get("samples", [])
        ),
        "samples": [
            {
                "id": s["sample_key"],
                "label": s.get("sample_stem"),
                "numeric_count": coverage[s["sample_key"]],
            }
            for s in summary["samples"]
        ],
    }, rows


def ordered_review_items(headers, rows, scope):
    selected = []
    for item, kind, identifier in headers:
        facts = rows.get(identifier, {})
        destination = facts.get("destination")
        if (
            scope == "all"
            or scope == "candidates"
            and kind == "candidate"
            or scope == destination
            or scope == "unresolved"
            and destination == "matrix"
            and facts["unresolved_count"]
        ):
            selected.append((item, facts))
    selected.sort(
        key=lambda value: (
            -value[1].get("unresolved_count", 0),
            -value[1].get("numeric_count", 0),
            value[0],
        )
    )
    return [item for item, _ in selected]
