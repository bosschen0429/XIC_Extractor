"""Reader-oriented counts and ordering from saved routes and cell decisions."""

from collections import Counter

from xic_extractor.alignment_explanation.reader import (
    open_review_population,
)


def review_overview(path, summary):
    rows = {}
    coverage: Counter[str] = Counter()
    sample_count = len(summary["samples"])
    with open_review_population(path, summary) as population:
        for record in population:
            row, cells = record["row"], record["cells"]
            numeric = {c["sample_key"] for c in cells if c["variant"] == "numeric"}
            unresolved = sum(c["variant"] == "unresolved" for c in cells)
            destination = row["route"]["destination"]
            rows[row["feature_id"]] = {
                "destination": destination,
                "numeric_count": len(numeric),
                "unresolved_count": unresolved,
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
