"""Capture and replay the per-batch facts used by cross-batch correspondence."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from xic_extractor.alignment.batch_correspondence import (
    BatchFeature,
    BatchIstd,
    GroupedBatch,
    pair_grouped_batches,
)
from xic_extractor.diagnostics.grouping_experiment_io import write_json
from xic_extractor.diagnostics.matrix_replay import save_matrix_join
from xic_extractor.output.alignment.batch_correspondence_table import (
    batch_correspondence_summary,
    write_batch_correspondence_table,
)
from xic_extractor.validation_primitives import (
    require_finite_real,
    require_integer,
    require_list,
    require_mapping,
    require_optional_text,
    require_text,
)

_SCHEMA = "batch_pairing_input_v1"


def save_pairing_input(
    path: Path,
    batch: GroupedBatch,
    *,
    latest_acquisition: datetime,
    precursor_tolerance_ppm: float,
) -> None:
    write_json(
        path,
        {
            "schema": _SCHEMA,
            "batch_id": batch.batch_id,
            "sample_count": batch.sample_count,
            "latest_acquisition": latest_acquisition.isoformat(),
            "precursor_tolerance_ppm": precursor_tolerance_ppm,
            "features": [
                {
                    "key": f.key,
                    "neutral_loss_tag": f.neutral_loss_tag,
                    "precursor_mz": f.precursor_mz,
                    "reference_rt_min": f.reference_rt_min,
                    "sample_count": f.sample_count,
                    "known_identity_label": f.known_identity_label,
                }
                for f in batch.features
            ],
            "istds": [
                {"label": i.label, "reference_rt_min": i.reference_rt_min}
                for i in batch.istds
            ],
        },
    )


def _load(path: Path) -> tuple[GroupedBatch, datetime, float]:
    try:
        doc = require_mapping(json.loads(path.read_text(encoding="utf-8")))
        if doc["schema"] != _SCHEMA:
            raise ValueError("not a supported batch pairing input")
        batch = require_text(doc["batch_id"], field="batch_id")
        size = require_integer(doc["sample_count"], field="sample_count", positive=True)
        latest = datetime.fromisoformat(
            require_text(doc["latest_acquisition"], field="latest_acquisition")
        )
        ppm = require_finite_real(
            doc["precursor_tolerance_ppm"],
            field="precursor_tolerance_ppm",
            positive=True,
        )
        features = []
        seen = set()
        for item in require_list(doc["features"], label="features"):
            row = require_mapping(item, label="feature")
            key = require_text(row["key"], field="key")
            count = require_integer(
                row["sample_count"], field="sample_count", positive=True
            )
            if key in seen or count > size:
                raise ValueError(
                    f"duplicate key or invalid sample count for feature {key!r}"
                )
            seen.add(key)
            features.append(
                BatchFeature(
                    batch,
                    key,
                    require_text(row["neutral_loss_tag"], field="neutral_loss_tag"),
                    require_finite_real(
                        row["precursor_mz"], field="precursor_mz", positive=True
                    ),
                    require_finite_real(
                        row["reference_rt_min"], field="reference_rt_min"
                    ),
                    count,
                    require_optional_text(
                        row["known_identity_label"], field="known_identity_label"
                    ),
                )
            )
        istds = []
        for item in require_list(doc["istds"], label="istds"):
            row = require_mapping(item, label="ISTD")
            istds.append(
                BatchIstd(
                    batch,
                    require_text(row["label"], field="label"),
                    require_finite_real(
                        row["reference_rt_min"], field="reference_rt_min"
                    ),
                )
            )
        return GroupedBatch(batch, size, tuple(features), tuple(istds)), latest, ppm
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid pairing input {path}: {exc}") from exc


def replay_pairing(inputs: Sequence[Path], output_dir: Path) -> None:
    if len(inputs) < 2:
        raise ValueError("pair-batches requires at least two batch inputs")
    table = output_dir / "batch_correspondence.tsv"
    summary = output_dir / "summary.json"
    join = output_dir / "matrix-join.json"
    if table.exists() or summary.exists() or join.exists():
        raise ValueError(f"Pairing output already exists: {output_dir}")
    captures = [_load(path) for path in inputs]
    latest = {batch.batch_id: date for batch, date, _ in captures}
    if len(latest) != len(captures):
        raise ValueError("Pairing inputs must have distinct batch IDs")
    if len({ppm for _, _, ppm in captures}) != 1:
        raise ValueError("Pairing inputs must use the same precursor_tolerance_ppm")
    if len({date.utcoffset() is None for date in latest.values()}) > 1:
        raise ValueError("Pairing input acquisition times use incompatible time zones")
    result, features = pair_grouped_batches(
        [batch for batch, _, _ in captures],
        latest_acquisition=latest.__getitem__,
        precursor_tolerance_ppm=captures[0][2],
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    save_matrix_join(join, result)
    write_batch_correspondence_table(table, result, features)
    write_json(summary, {"batch_correspondence": batch_correspondence_summary(result)})
