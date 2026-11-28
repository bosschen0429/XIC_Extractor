"""Publish Delta Mass candidates: the batch file and a readable candidate table.

Only established candidates are kept; they are the result. The table is
rendered from the published file, one row per candidate.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.discovery.delta_mass_patterns import DeltaMassCandidate
from xic_extractor.discovery.store import (
    DISCOVERY_ARTIFACT_NAME,
    DiscoverySample,
    open_discovery,
    open_writer,
    publish,
    sample_row,
)
from xic_extractor.tabular_io import format_machine_float, write_delimited_rows

DELTA_MASS_TABLE_NAME = "delta_mass_candidates.csv"
DELTA_MASS_TABLE_COLUMNS = (
    "sample",
    "candidate_id",
    "pattern_id",
    "mz",
    "rt_min",
    "height",
    "positions_found",
    "positions",
    "cid_nl_peak_id",
)

FeatureBounds = Mapping[tuple[str, str], tuple[float, float | None, float | None]]
"""A detected feature's m/z and RT bounds, by (sample, feature ID)."""


@dataclass(frozen=True)
class DeltaMassDiscoveryOutputs:
    store: Path
    candidates_csv: Path
    candidate_count: int


def write_delta_mass_outputs(
    output_dir: Path,
    *,
    method_label: str,
    method: Mapping[str, object],
    samples: Sequence[DiscoverySample],
    candidates: Sequence[DeltaMassCandidate],
    features: FeatureBounds,
) -> DeltaMassDiscoveryOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    established = [c for c in candidates if c.status == "candidate"]
    store = publish_delta_mass_store(
        output_dir / DISCOVERY_ARTIFACT_NAME,
        method_label=method_label,
        method=json.loads(json.dumps(method, allow_nan=False)),
        samples=samples,
        candidates=established,
        features=features,
    )
    table = output_dir / DELTA_MASS_TABLE_NAME
    write_delimited_rows(
        table,
        [
            {
                **row,
                "mz": format_machine_float(row["mz"]),
                "rt_min": format_machine_float(row["rt_min"]),
                "height": ""
                if row["height"] is None
                else format_machine_float(row["height"]),
            }
            for row in read_delta_mass_table(store)
        ],
        DELTA_MASS_TABLE_COLUMNS,
        encoding="utf-8-sig",
    )
    return DeltaMassDiscoveryOutputs(store, table, len(established))


def publish_delta_mass_store(
    target: Path,
    *,
    method_label: str,
    method: Mapping[str, object],
    samples: Sequence[DiscoverySample],
    candidates: Sequence[DeltaMassCandidate],
    features: Mapping[tuple[str, str], tuple[float, float | None, float | None]],
) -> Path:
    """Publish a Delta Mass batch: its samples, method and established candidates."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    db = open_writer(temporary)
    try:
        sample_ids = {}
        for index, sample in enumerate(samples):
            db.execute(
                "INSERT INTO samples VALUES (?,?,?,?,?)", sample_row(index, sample)
            )
            sample_ids[sample.sample_stem] = index
        db.execute(
            "INSERT INTO delta_mass_run VALUES (1,?,?)",
            (method_label, json.dumps(method, sort_keys=True, allow_nan=False)),
        )
        for candidate in candidates:
            (row_id,) = db.execute(
                "INSERT INTO delta_mass_candidates "
                "(candidate_id,sample,pattern_id,base_mz,base_rt_apex_min,"
                "base_height,cid_nl_peak_id) VALUES (?,?,?,?,?,?,?) RETURNING id",
                (
                    candidate.candidate_id,
                    sample_ids[candidate.sample_stem],
                    candidate.pattern_id,
                    candidate.base_mz,
                    candidate.base_rt_apex_min,
                    candidate.base_height,
                    candidate.cid_nl_base_peak_id or None,
                ),
            ).fetchone()
            for ordinal, position in enumerate(candidate.positions):
                db.execute(
                    "INSERT INTO delta_mass_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row_id,
                        ordinal,
                        position.position_label,
                        position.delta_da,
                        position.expected_mz,
                        position.position_status,
                        position.feature_id,
                        position.observed_mz,
                        position.apex_rt_min,
                        position.height,
                        position.height_ratio,
                        position.shape_r,
                    ),
                )
        db.executemany(
            "INSERT INTO delta_mass_features VALUES (?,?,?,?,?)",
            (
                (sample_ids[stem], feature_id, *bounds)
                for (stem, feature_id), bounds in features.items()
            ),
        )
        return publish(db, temporary, target)
    except BaseException:
        db.close()
        temporary.unlink(missing_ok=True)
        raise


def read_delta_mass_table(path: Path) -> list[dict[str, Any]]:
    """One readable row per established candidate, in sample and m/z order."""
    with open_discovery(path) as db:
        return [
            dict(zip(DELTA_MASS_TABLE_COLUMNS, row, strict=True))
            for row in db.execute(
                "SELECT s.sample_stem,c.candidate_id,c.pattern_id,c.base_mz,"
                "c.base_rt_apex_min,c.base_height,"
                "(SELECT COUNT(*) FROM delta_mass_positions p "
                " WHERE p.candidate=c.id AND p.position_status='pass'),"
                "(SELECT COALESCE(group_concat(position_label,';'),'') FROM "
                " (SELECT position_label FROM delta_mass_positions p "
                "  WHERE p.candidate=c.id AND p.position_status='pass' "
                "  ORDER BY p.ordinal)),"
                "COALESCE(c.cid_nl_peak_id,'') "
                "FROM delta_mass_candidates c JOIN samples s ON s.id=c.sample "
                "ORDER BY s.id,c.base_mz,c.candidate_id"
            )
        ]
