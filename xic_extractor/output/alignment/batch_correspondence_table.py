"""Write the batch-to-batch correspondence: one row per feature, plus a summary."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from xic_extractor.alignment.batch_correspondence import (
    SEARCH_WINDOW_FLOOR_MIN,
    SEARCH_WINDOW_MULTIPLIER,
    BatchCorrespondence,
    BatchFeature,
)
from xic_extractor.tabular_io import write_tsv

BATCH_CORRESPONDENCE_ARTIFACT_NAME = "alignment_batch_correspondence.tsv"
BATCH_CORRESPONDENCE_COLUMNS = (
    "batch_id",
    "group_id",
    "neutral_loss_tag",
    "precursor_mz",
    "batch_rt_min",
    "reference_rt_min",
    "sample_count",
    "known_identity_label",
    "anchor",
    "state",
    "partner_group_id",
    "competitor_group_ids",
)


def write_batch_correspondence_table(
    path: Path,
    correspondence: BatchCorrespondence,
    features: Iterable[BatchFeature],
) -> None:
    by_key = {(item.batch_id, item.key): item for item in features}
    anchors: dict[tuple[str, str], str] = {}
    for batch_map in correspondence.maps:
        for anchor in batch_map.anchors:
            for key in anchor.feature_keys:
                anchors[key] = anchor.istd_label or "pair"
    write_tsv(
        path,
        (
            {
                "batch_id": decision.batch_id,
                "group_id": decision.key,
                "neutral_loss_tag": feature.neutral_loss_tag,
                "precursor_mz": feature.precursor_mz,
                "batch_rt_min": feature.reference_rt_min,
                "reference_rt_min": ""
                if decision.reference_rt_min is None
                else decision.reference_rt_min,
                "sample_count": feature.sample_count,
                "known_identity_label": feature.known_identity_label or "",
                "anchor": anchors.get((decision.batch_id, decision.key), ""),
                "state": decision.state,
                "partner_group_id": ";".join(key for _, key in decision.partners),
                "competitor_group_ids": ";".join(
                    key for _, key in decision.competitors
                ),
            }
            for decision in correspondence.features
            for feature in (by_key[(decision.batch_id, decision.key)],)
        ),
        BATCH_CORRESPONDENCE_COLUMNS,
        formatter=str,
        lineterminator="\n",
    )


def _segment(rt_min: float) -> str:
    start = int(rt_min // 4) * 4
    return f"{start}-{start + 4}"


def batch_correspondence_summary(
    correspondence: BatchCorrespondence,
) -> dict[str, Any]:
    """Map quality, anchor coverage and pairing counts for review."""
    batches: dict[str, Any] = {}
    for batch_map in correspondence.maps:
        calibration = batch_map.calibration
        anchors_by_segment = Counter(
            _segment(anchor.reference_rt_min) for anchor in batch_map.anchors
        )
        exclusions: dict[str, Counter[str]] = {}
        for excluded in batch_map.excluded_anchors:
            exclusions.setdefault(_segment(excluded.rt_min), Counter())[
                excluded.reason
            ] += 1
        batches[batch_map.batch_id] = {
            "map_status": "unavailable" if calibration is None else calibration.status,
            "map_knot_count": 0 if calibration is None else len(calibration.knots),
            "istd_anchor_count": sum(
                anchor.istd_label is not None for anchor in batch_map.anchors
            ),
            "pair_anchor_count": sum(
                anchor.istd_label is None for anchor in batch_map.anchors
            ),
            "coarse_istds": [
                {"label": label, "batch_rt_min": x, "reference_rt_min": y}
                for label, x, y in batch_map.coarse_istds
            ],
            "coarse_loo_error_min": dict(
                sorted(batch_map.coarse_loo_error_min.items())
            ),
            "coarse_half_width_min": batch_map.coarse_half_width_min,
            "loo_error_min": dict(sorted(batch_map.loo_error_min.items())),
            "search_window": [
                {"reference_rt_min": rt, "half_width_min": width}
                for rt, width in batch_map.search_window
            ],
            "anchors_by_reference_rt_min": _by_segment(anchors_by_segment),
            "anchor_exclusions_by_rt_min": {
                segment: dict(sorted(counts.items()))
                for segment, counts in sorted(
                    exclusions.items(), key=lambda item: int(item[0].split("-")[0])
                )
            },
        }
    states: dict[str, Counter[str]] = {}
    for decision in correspondence.features:
        states.setdefault(decision.batch_id, Counter())[decision.state] += 1
    rows = correspondence.rows()
    return {
        "reference_batch": correspondence.reference_batch,
        "search_window_multiplier": SEARCH_WINDOW_MULTIPLIER,
        "search_window_floor_min": SEARCH_WINDOW_FLOOR_MIN,
        "batches": batches,
        "feature_states": {
            batch: dict(sorted(counts.items()))
            for batch, counts in sorted(states.items())
        },
        "row_counts": {
            "paired": sum(len(row) > 1 for row in rows),
            "single_batch": sum(len(row) == 1 for row in rows),
        },
    }


def _by_segment(counts: Counter[str]) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda item: int(item[0].split("-")[0])))
