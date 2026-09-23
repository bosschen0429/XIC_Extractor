"""Diagnostic projection of provisional cross-sample groups into one matrix."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from xic_extractor.alignment.matrix import (
    AlignedCell,
    AlignmentMatrix,
    AlignmentRowLike,
)
from xic_extractor.alignment.models import AlignmentCluster
from xic_extractor.alignment.tsv_writer import write_safe_alignment_tsv

PROVISIONAL_VALIDATION_STATUS = "diagnostic_only"
PROVISIONAL_MATRIX_SCHEMA_VERSION = "untargeted_provisional_matrix"
PROVISIONAL_IDENTITY_SCHEMA_VERSION = "untargeted_provisional_row_identity"
PROVISIONAL_PROVENANCE_SCHEMA_VERSION = (
    "untargeted_provisional_cell_provenance"
)
PROVISIONAL_SUMMARY_SCHEMA_VERSION = "untargeted_provisional_matrix_summary"

_MATRIX_METADATA_COLUMNS = (
    "group_hypothesis_id",
    "public_family_id",
    "Mz",
    "RT",
)
_CELL_STATUS_VALUES = frozenset(
    {
        "detected",
        "backfilled",
        "absent",
        "unchecked",
        "ambiguous_ms1_owner",
        "duplicate_assigned",
    }
)

PROVISIONAL_IDENTITY_COLUMNS = (
    "schema_version",
    "group_hypothesis_id",
    "public_family_id",
    "row_identity_status",
    "row_identity_basis",
    "identity_settlement_status",
    "group_center_mz",
    "group_center_rt",
    "neutral_loss_tag",
    "member_count",
    "detected_cell_count",
    "backfill_candidate_cell_count",
    "quant_available_cell_count",
    "matrix_blank_cell_count",
    "review_only",
    "identity_conflict",
    "consolidation_state",
    "group_construction_role",
    "group_membership_source",
    "validation_status",
    "product_authority",
)

PROVISIONAL_PROVENANCE_COLUMNS = (
    "schema_version",
    "group_hypothesis_id",
    "public_family_id",
    "sample_stem",
    "cell_state",
    "alignment_status",
    "matrix_value",
    "matrix_value_basis",
    "source_candidate_id",
    "trace_quality",
    "reason",
    "backfill_seed_mz",
    "backfill_seed_rt",
    "group_construction_role",
    "group_membership_source",
    "validation_status",
    "product_authority",
)


@dataclass(frozen=True, slots=True)
class _ProvisionalRowCounts:
    detected: int
    backfill: int
    quant_available: int


@dataclass(slots=True)
class _MutableProvisionalRowCounts:
    detected: int = 0
    backfill: int = 0
    quant_available: int = 0


@dataclass(frozen=True, slots=True)
class ProvisionalMatrixProjection:
    matrix_header: tuple[str, ...]
    _clusters: tuple[AlignmentCluster | AlignmentRowLike, ...]
    _sample_order: tuple[str, ...]
    _cells_by_key: Mapping[tuple[str, str], AlignedCell]
    _row_counts: Mapping[str, _ProvisionalRowCounts]
    _summary: Mapping[str, object]

    def iter_matrix_rows(self) -> Iterator[dict[str, str]]:
        for cluster in self._clusters:
            public_family_id = _public_family_id(cluster)
            row = {
                "group_hypothesis_id": _group_hypothesis_id(cluster),
                "public_family_id": public_family_id,
                "Mz": _format_number(_group_center_mz(cluster)),
                "RT": _format_number(_group_center_rt(cluster)),
            }
            for sample_stem in self._sample_order:
                row[sample_stem] = _matrix_value(
                    self._cells_by_key.get((public_family_id, sample_stem))
                )
            yield row

    def iter_identity_rows(self) -> Iterator[dict[str, str]]:
        for cluster in self._clusters:
            public_family_id = _public_family_id(cluster)
            counts = self._row_counts[public_family_id]
            yield _identity_row(
                cluster,
                detected_count=counts.detected,
                backfill_count=counts.backfill,
                quant_count=counts.quant_available,
                missing_count=len(self._sample_order) - counts.quant_available,
            )

    def iter_provenance_rows(self) -> Iterator[dict[str, str]]:
        for cluster in self._clusters:
            public_family_id = _public_family_id(cluster)
            for sample_stem in self._sample_order:
                cell = self._cells_by_key.get((public_family_id, sample_stem))
                yield _provenance_row(
                    cluster,
                    sample_stem=sample_stem,
                    cell=cell,
                    matrix_value=_matrix_value(cell),
                )

    @property
    def matrix_rows(self) -> tuple[dict[str, str], ...]:
        """Compatibility view; the production writer uses the lazy iterator."""

        return tuple(self.iter_matrix_rows())

    @property
    def identity_rows(self) -> tuple[dict[str, str], ...]:
        """Compatibility view; the production writer uses the lazy iterator."""

        return tuple(self.iter_identity_rows())

    @property
    def provenance_rows(self) -> tuple[dict[str, str], ...]:
        """Compatibility view; the production writer uses the lazy iterator."""

        return tuple(self.iter_provenance_rows())

    @property
    def summary(self) -> dict[str, object]:
        return dict(self._summary)


def build_provisional_matrix_projection(
    matrix: AlignmentMatrix,
    *,
    backfill_scope: str,
    discovery_owner_closure: bool,
    selected_family_count: int,
) -> ProvisionalMatrixProjection:
    """Render reduced-claim rows without asserting final identity settlement."""

    if len(matrix.sample_order) != len(set(matrix.sample_order)):
        raise ValueError("provisional matrix sample_order contains duplicates")
    reserved_samples = sorted(set(matrix.sample_order) & set(_MATRIX_METADATA_COLUMNS))
    if reserved_samples:
        raise ValueError(
            "provisional matrix sample_order collides with metadata columns: "
            + ", ".join(reserved_samples)
        )
    if backfill_scope != "full-audit":
        raise ValueError("provisional matrix requires full-audit Backfill")
    if discovery_owner_closure is not True:
        raise ValueError("provisional matrix requires Discovery owner closure")
    if selected_family_count != 0:
        raise ValueError("provisional matrix requires an empty family selection")
    sample_set = frozenset(matrix.sample_order)
    clusters_by_public_id: dict[str, Any] = {}
    seen_group_ids: set[str] = set()
    for cluster in matrix.clusters:
        public_family_id = _public_family_id(cluster)
        group_hypothesis_id = _group_hypothesis_id(cluster)
        if public_family_id in clusters_by_public_id:
            raise ValueError(
                f"duplicate provisional public_family_id: {public_family_id}"
            )
        if group_hypothesis_id in seen_group_ids:
            raise ValueError(
                f"duplicate provisional group_hypothesis_id: {group_hypothesis_id}"
            )
        clusters_by_public_id[public_family_id] = cluster
        seen_group_ids.add(group_hypothesis_id)

    cells_by_key: dict[tuple[str, str], AlignedCell] = {}
    mutable_row_counts = {
        public_family_id: _MutableProvisionalRowCounts()
        for public_family_id in clusters_by_public_id
    }
    detected_cell_count = 0
    backfill_candidate_cell_count = 0
    quant_available_cell_count = 0
    for cell in matrix.cells:
        if cell.status not in _CELL_STATUS_VALUES:
            raise ValueError(f"unsupported provisional cell status: {cell.status}")
        if cell.sample_stem not in sample_set:
            raise ValueError(
                f"provisional cell sample is outside sample_order: {cell.sample_stem}"
            )
        cell_cluster = clusters_by_public_id.get(cell.cluster_id)
        if cell_cluster is None:
            raise ValueError(
                f"provisional cell references unknown public family: {cell.cluster_id}"
            )
        _validate_cell_owner(cell, cell_cluster)
        key = (cell.cluster_id, cell.sample_stem)
        if key in cells_by_key:
            raise ValueError(
                "provisional matrix requires at most one cell per group and sample: "
                f"{cell.cluster_id}/{cell.sample_stem}"
            )
        cells_by_key[key] = cell
        counts = mutable_row_counts[cell.cluster_id]
        if cell.status == "detected":
            counts.detected += 1
            detected_cell_count += 1
        elif cell.status == "backfilled":
            counts.backfill += 1
            backfill_candidate_cell_count += 1
        if _matrix_value(cell):
            counts.quant_available += 1
            quant_available_cell_count += 1

    matrix_header = (
        *_MATRIX_METADATA_COLUMNS,
        *matrix.sample_order,
    )
    row_count = len(matrix.clusters)
    row_counts = MappingProxyType(
        {
            public_family_id: _ProvisionalRowCounts(
                detected=counts.detected,
                backfill=counts.backfill,
                quant_available=counts.quant_available,
            )
            for public_family_id, counts in mutable_row_counts.items()
        }
    )
    summary = MappingProxyType(
        {
            "schema_version": PROVISIONAL_SUMMARY_SCHEMA_VERSION,
            "matrix_schema_version": PROVISIONAL_MATRIX_SCHEMA_VERSION,
            "validation_status": PROVISIONAL_VALIDATION_STATUS,
            "row_identity_status": "provisional",
            "identity_settlement_status": "not_run",
            "product_authority": False,
            "discovery_owner_closure": True,
            "backfill_scope": backfill_scope,
            "selected_family_count": selected_family_count,
            "sample_count": len(matrix.sample_order),
            "row_count": row_count,
            "detected_cell_count": detected_cell_count,
            "backfill_candidate_cell_count": backfill_candidate_cell_count,
            "quant_available_cell_count": quant_available_cell_count,
            "matrix_blank_cell_count": row_count * len(matrix.sample_order)
            - quant_available_cell_count,
        }
    )
    return ProvisionalMatrixProjection(
        matrix_header=matrix_header,
        _clusters=tuple(matrix.clusters),
        _sample_order=tuple(matrix.sample_order),
        _cells_by_key=MappingProxyType(cells_by_key),
        _row_counts=row_counts,
        _summary=summary,
    )


def write_provisional_matrix_tsv(
    path: Path,
    projection: ProvisionalMatrixProjection,
) -> Path:
    write_safe_alignment_tsv(
        path,
        projection.matrix_header,
        projection.iter_matrix_rows(),
    )
    return path


def write_provisional_identity_tsv(
    path: Path,
    projection: ProvisionalMatrixProjection,
) -> Path:
    write_safe_alignment_tsv(
        path,
        PROVISIONAL_IDENTITY_COLUMNS,
        projection.iter_identity_rows(),
    )
    return path


def write_provisional_provenance_tsv(
    path: Path,
    projection: ProvisionalMatrixProjection,
) -> Path:
    write_safe_alignment_tsv(
        path,
        PROVISIONAL_PROVENANCE_COLUMNS,
        projection.iter_provenance_rows(),
    )
    return path


def write_provisional_summary_json(
    path: Path,
    projection: ProvisionalMatrixProjection,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(projection.summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _identity_row(
    cluster: Any,
    *,
    detected_count: int,
    backfill_count: int,
    quant_count: int,
    missing_count: int,
) -> dict[str, str]:
    return {
        "schema_version": PROVISIONAL_IDENTITY_SCHEMA_VERSION,
        "group_hypothesis_id": _group_hypothesis_id(cluster),
        "public_family_id": _public_family_id(cluster),
        "row_identity_status": "provisional",
        "row_identity_basis": "cross_sample_peak_group_hypothesis",
        "identity_settlement_status": "not_run",
        "group_center_mz": _format_number(_group_center_mz(cluster)),
        "group_center_rt": _format_number(_group_center_rt(cluster)),
        "neutral_loss_tag": str(getattr(cluster, "neutral_loss_tag", "")),
        "member_count": str(len(tuple(getattr(cluster, "members", ())))),
        "detected_cell_count": str(detected_count),
        "backfill_candidate_cell_count": str(backfill_count),
        "quant_available_cell_count": str(quant_count),
        "matrix_blank_cell_count": str(missing_count),
        "review_only": _format_bool(bool(getattr(cluster, "review_only", False))),
        "identity_conflict": _format_bool(
            bool(getattr(cluster, "identity_conflict", False))
        ),
        "consolidation_state": str(
            getattr(cluster, "consolidation_state", "not_consolidated")
        ),
        "group_construction_role": str(
            getattr(cluster, "group_construction_role", "")
        ),
        "group_membership_source": str(
            getattr(cluster, "group_membership_source", "")
        ),
        "validation_status": PROVISIONAL_VALIDATION_STATUS,
        "product_authority": "FALSE",
    }


def _provenance_row(
    cluster: Any,
    *,
    sample_stem: str,
    cell: AlignedCell | None,
    matrix_value: str,
) -> dict[str, str]:
    return {
        "schema_version": PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
        "group_hypothesis_id": _group_hypothesis_id(cluster),
        "public_family_id": _public_family_id(cluster),
        "sample_stem": sample_stem,
        "cell_state": _cell_state(cell, matrix_value),
        "alignment_status": cell.status if cell is not None else "not_materialized",
        "matrix_value": matrix_value,
        "matrix_value_basis": (
            cell.matrix_area_source if cell is not None and matrix_value else ""
        ),
        "source_candidate_id": (
            cell.source_candidate_id or "" if cell is not None else ""
        ),
        "trace_quality": cell.trace_quality if cell is not None else "",
        "reason": cell.reason if cell is not None else "no_alignment_cell",
        "backfill_seed_mz": (
            _format_optional_number(cell.backfill_seed_mz)
            if cell is not None
            else ""
        ),
        "backfill_seed_rt": (
            _format_optional_number(cell.backfill_seed_rt)
            if cell is not None
            else ""
        ),
        "group_construction_role": (
            cell.group_construction_role
            if cell is not None
            else str(getattr(cluster, "group_construction_role", ""))
        ),
        "group_membership_source": (
            cell.group_membership_source
            if cell is not None
            else str(getattr(cluster, "group_membership_source", ""))
        ),
        "validation_status": PROVISIONAL_VALIDATION_STATUS,
        "product_authority": "FALSE",
    }


def _validate_cell_owner(cell: AlignedCell, cluster: Any) -> None:
    expected_group_id = _group_hypothesis_id(cluster)
    if cell.group_hypothesis_id != expected_group_id:
        raise ValueError(
            "cell group_hypothesis_id does not match its cluster owner: "
            f"{cell.group_hypothesis_id!r} != {expected_group_id!r}"
        )
    expected_public_family_id = _public_family_id(cluster)
    if cell.public_family_id != expected_public_family_id:
        raise ValueError(
            "cell public_family_id does not match its cluster projection: "
            f"{cell.public_family_id!r} != {expected_public_family_id!r}"
        )


def _cell_state(cell: AlignedCell | None, matrix_value: str) -> str:
    if cell is None or cell.status in {"absent", "unchecked"}:
        return "missing"
    if cell.status == "detected":
        return "observed" if matrix_value else "observed_unquantified"
    if cell.status == "backfilled":
        return "backfill_candidate" if matrix_value else "backfill_unquantified"
    if cell.status == "ambiguous_ms1_owner":
        return "ambiguous"
    if cell.status == "duplicate_assigned":
        return "duplicate_suppressed"
    return "missing"


def _matrix_value(cell: AlignedCell | None) -> str:
    if cell is None or cell.status not in {"detected", "backfilled"}:
        return ""
    value = cell.matrix_area
    if value is None or not math.isfinite(value) or value <= 0.0:
        return ""
    return _format_number(value)


def _group_hypothesis_id(cluster: Any) -> str:
    value = str(getattr(cluster, "group_hypothesis_id", "")).strip()
    if not value:
        raise ValueError("provisional row requires group_hypothesis_id")
    return value


def _public_family_id(cluster: Any) -> str:
    value = str(getattr(cluster, "public_family_id", "")).strip()
    if not value:
        raise ValueError("provisional row requires public_family_id")
    return value


def _group_center_mz(cluster: Any) -> float:
    return _finite_number(
        getattr(cluster, "family_center_mz", None),
        name="group center m/z",
    )


def _group_center_rt(cluster: Any) -> float:
    return _finite_number(
        getattr(cluster, "family_center_rt", None),
        name="group center RT",
    )


def _finite_number(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _format_optional_number(value: object) -> str:
    if value is None:
        return ""
    return _format_number(_finite_number(value, name="optional numeric value"))


def _format_number(value: float) -> str:
    return f"{value:.12g}"


def _format_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


__all__ = (
    "PROVISIONAL_IDENTITY_COLUMNS",
    "PROVISIONAL_IDENTITY_SCHEMA_VERSION",
    "PROVISIONAL_MATRIX_SCHEMA_VERSION",
    "PROVISIONAL_PROVENANCE_COLUMNS",
    "PROVISIONAL_PROVENANCE_SCHEMA_VERSION",
    "PROVISIONAL_SUMMARY_SCHEMA_VERSION",
    "PROVISIONAL_VALIDATION_STATUS",
    "ProvisionalMatrixProjection",
    "build_provisional_matrix_projection",
    "write_provisional_identity_tsv",
    "write_provisional_matrix_tsv",
    "write_provisional_provenance_tsv",
    "write_provisional_summary_json",
)
