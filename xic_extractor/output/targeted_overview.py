"""A reading guide to saved Targeted results, not a new detection policy."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from statistics import median


@dataclass(frozen=True)
class TargetOverview:
    label: str
    total: int
    counted: int
    count_unknown: int
    review: int
    reasons: tuple[str, ...]
    median_area: float | None
    rt_min: float | None
    rt_max: float | None


@dataclass(frozen=True)
class TargetedOverview:
    sample_count: int
    result_count: int
    counted_count: int
    count_unknown: int
    review_count: int
    review_sample_count: int
    targets: tuple[TargetOverview, ...]


def summarize_targeted_results(
    rows: Sequence[Mapping[str, object]],
    review_rows: Sequence[Mapping[str, object]],
) -> TargetedOverview:
    """Count recorded decisions once; never infer missing decisions from values."""
    by_target: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    review_by_target: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_target[str(row["Target"])].append(row)
    for row in review_rows:
        review_by_target[str(row["Target"])].append(row)
    targets = []
    for label, records in by_target.items():
        counted = [row for row in records if _counted(row) == "TRUE"]
        areas = [_number(row.get("Area")) for row in counted]
        rts = [_number(row.get("RT")) for row in counted]
        numeric_areas = [value for value in areas if value is not None]
        numeric_rts = [value for value in rts if value is not None]
        review = review_by_target[label]
        reasons = Counter(str(row.get("Why") or "原因未記錄") for row in review)
        targets.append(
            TargetOverview(
                label=label,
                total=len(records),
                counted=len(counted),
                count_unknown=sum(
                    _counted(row) not in {"TRUE", "FALSE"} for row in records
                ),
                review=len(review),
                reasons=tuple(
                    f"{reason} ({count})" for reason, count in reasons.most_common()
                ),
                median_area=median(numeric_areas) if numeric_areas else None,
                rt_min=min(numeric_rts) if numeric_rts else None,
                rt_max=max(numeric_rts) if numeric_rts else None,
            )
        )
    # Put the review work first without treating an unflagged target as accepted.
    targets.sort(key=lambda target: -target.review)
    return TargetedOverview(
        sample_count=len({row["SampleName"] for row in rows}),
        result_count=len(rows),
        counted_count=sum(target.counted for target in targets),
        count_unknown=sum(target.count_unknown for target in targets),
        review_count=len(review_rows),
        review_sample_count=len({row["Sample"] for row in review_rows}),
        targets=tuple(targets),
    )


def _counted(row: Mapping[str, object]) -> str:
    return str(row.get("Counted Detection", "")).strip().upper()


def _number(value: object) -> float | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if isfinite(number) else None
