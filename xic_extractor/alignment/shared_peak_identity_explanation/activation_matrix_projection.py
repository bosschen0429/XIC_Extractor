from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from xic_extractor.alignment.matrix_output_contract import (
    MATRIX_IDENTITY_SCHEMA_VERSION,
    bind_matrix_identity_rows,
    canonical_mz_text,
    project_public_matrix_rows,
    validate_matrix_identity_row,
    validate_matrix_identity_rows,
)
from xic_extractor.alignment.product_matrix import (
    ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
)


@dataclass(frozen=True)
class LegacyRtRowReference:
    row_number: int
    mz: float
    rt: float

    @property
    def peak_hypothesis_id(self) -> str:
        return (
            f"mzmine_rtrow_{self.row_number}_mz{self.mz:.4f}_rt{self.rt:.2f}min"
        )


@dataclass(frozen=True)
class FormalMatrixStats:
    family_projection_rows: int = 0
    legacy_rt_row_context_rows: int = 0
    matrix_value_conflict_cells: int = 0
    family_projection_rows_excluded: int = 0
    family_projection_cells_excluded: int = 0


@dataclass
class _RtCenterAccumulator:
    weighted_sum: float = 0.0
    weight_sum: float = 0.0
    unweighted_sum: float = 0.0
    unweighted_count: int = 0

    def add(self, rt: float, weight: float | None) -> None:
        if weight is not None and weight > 0:
            self.weighted_sum += rt * weight
            self.weight_sum += weight
        self.unweighted_sum += rt
        self.unweighted_count += 1

    @property
    def center(self) -> float | None:
        if self.weight_sum > 0:
            return self.weighted_sum / self.weight_sum
        if self.unweighted_count > 0:
            return self.unweighted_sum / self.unweighted_count
        return None

    @property
    def basis(self) -> str:
        if self.weight_sum > 0:
            return "activation_rt_mode_area_weighted_raw_selected_rt"
        return "activation_rt_mode_unweighted_raw_selected_rt"


_META = frozenset(
    {"feature_family_id", "neutral_loss_tag", "family_center_mz", "family_center_rt"}
)
_PUBLIC_MATRIX_META = frozenset({"Mz", "RT"})
_PUBLIC_PRODUCT_ROW_IDENTITY_BASIS = frozenset(
    {"no_split_peak_hypothesis", "split_peak_hypothesis"}
)
_INPUT_PEAK_HYPOTHESIS_ID = "_input_peak_hypothesis_id"
_INPUT_ROW_IDENTITY_BASIS = "_input_row_identity_basis"
_INPUT_LEGACY_RT_ROW_CONTEXT_ID = "_input_legacy_rt_row_context_id"
_INPUT_SOURCE_FEATURE_FAMILY_ID = "_input_source_feature_family_id"
_HYPOTHESIS_CENTER_RT = "_hypothesis_center_rt"
_CENTER_RT_BASIS = "_center_rt_basis"

_MATRIX_IDENTITY_REQUIRED_COLUMNS = (
    "matrix_row_index",
    "Mz",
    "RT",
    "peak_hypothesis_id",
    "row_identity_basis",
    "source_feature_family_ids",
)

_FORMAL_MATRIX_META = (
    "peak_hypothesis_id",
    "feature_family_id",
    "candidate_container_id",
    "row_identity_basis",
    "legacy_rt_row_context_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
)


def _formal_matrix_header(sample_columns: Sequence[str]) -> tuple[str, ...]:
    return (*_FORMAL_MATRIX_META, *sample_columns)


def _product_matrix_header(sample_columns: Sequence[str]) -> tuple[str, ...]:
    return ("Mz", "RT", *sample_columns)


def _matrix_input_by_family(
    *,
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, str]],
    identity_header: Sequence[str] | None,
    identity_rows: Sequence[Mapping[str, str]],
    review_by_family: Mapping[str, Mapping[str, str]],
) -> tuple[tuple[str, ...], dict[str, dict[str, str]]]:
    if "feature_family_id" in matrix_header:
        sample_columns = tuple(
            column for column in matrix_header if column not in _META
        )
        return (
            sample_columns,
            {row["feature_family_id"]: dict(row) for row in matrix_rows},
        )
    _require_columns(matrix_header, ("Mz", "RT"))
    if identity_header is None:
        raise ValueError(
            "public Mz/RT alignment_matrix.tsv requires "
            "alignment_matrix_identity_tsv"
        )
    sample_columns = tuple(
        column for column in matrix_header if column not in _PUBLIC_MATRIX_META
    )
    return (
        sample_columns,
        _public_matrix_rows_by_family(
            matrix_rows=matrix_rows,
            sample_columns=sample_columns,
            identity_header=identity_header,
            identity_rows=identity_rows,
            review_by_family=review_by_family,
        ),
    )


def _public_matrix_rows_by_family(
    *,
    matrix_rows: Sequence[Mapping[str, str]],
    sample_columns: Sequence[str],
    identity_header: Sequence[str],
    identity_rows: Sequence[Mapping[str, str]],
    review_by_family: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    _require_columns(identity_header, _MATRIX_IDENTITY_REQUIRED_COLUMNS)
    matrix_header = ("Mz", "RT", *sample_columns)
    validate_matrix_identity_rows(
        matrix_header=matrix_header,
        matrix_rows=matrix_rows,
        identity_columns=identity_header,
        identity_rows=identity_rows,
    )
    rows_by_key: dict[str, dict[str, str]] = {}
    for index, (matrix_row, identity) in enumerate(
        zip(matrix_rows, identity_rows, strict=True),
        start=1,
    ):
        _validate_public_identity_basis(identity, index)
        family_id = _single_source_family_id(identity, index)
        row_key = _public_matrix_row_key(identity, index)
        if row_key in rows_by_key:
            raise ValueError(
                "public Mz/RT matrix identity row peak_hypothesis_id must be "
                f"unique; duplicate {row_key} at matrix_row_index {index}"
            )
        review_row = review_by_family.get(family_id, {})
        row = {
            "feature_family_id": family_id,
            "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
            "family_center_mz": canonical_mz_text(identity.get("Mz", "")),
            "family_center_rt": matrix_row.get("RT", ""),
            _INPUT_PEAK_HYPOTHESIS_ID: identity.get("peak_hypothesis_id", ""),
            _INPUT_ROW_IDENTITY_BASIS: identity.get("row_identity_basis", ""),
            _INPUT_LEGACY_RT_ROW_CONTEXT_ID: identity.get(
                "legacy_rt_row_context_id",
                "",
            ),
            _INPUT_SOURCE_FEATURE_FAMILY_ID: family_id,
        }
        row.update({sample: matrix_row.get(sample, "") for sample in sample_columns})
        rows_by_key[row_key] = row
    return rows_by_key


def _public_matrix_row_key(identity_row: Mapping[str, str], index: int) -> str:
    peak_hypothesis_id = identity_row.get("peak_hypothesis_id", "").strip()
    if not peak_hypothesis_id:
        raise ValueError(
            "public Mz/RT matrix identity row requires peak_hypothesis_id "
            f"at matrix_row_index {index}"
        )
    return peak_hypothesis_id


def _validate_public_identity_basis(
    identity: Mapping[str, str],
    row_index: int,
) -> None:
    row_identity_basis = identity.get("row_identity_basis", "")
    if row_identity_basis in _PUBLIC_PRODUCT_ROW_IDENTITY_BASIS:
        return
    raise ValueError(
        "public Mz/RT matrix identity row requires product row_identity_basis "
        f"at matrix_row_index {row_index}; got {row_identity_basis or '<blank>'}"
    )


def _positive_row_index(value: str) -> int:
    try:
        index = int(value)
    except ValueError:
        return -1
    return index if index > 0 else -1


def _validate_identity_coordinate(
    index: int,
    matrix_row: Mapping[str, str],
    identity_row: Mapping[str, str],
) -> None:
    validate_matrix_identity_row(
        matrix_header=tuple(matrix_row),
        matrix_row=matrix_row,
        identity_columns=tuple(identity_row),
        identity_row=identity_row,
        expected_index=index,
    )


def _single_source_family_id(identity_row: Mapping[str, str], index: int) -> str:
    source_ids = tuple(
        part.strip()
        for part in identity_row.get("source_feature_family_ids", "").split(";")
        if part.strip()
    )
    if len(source_ids) != 1:
        raise ValueError(
            "public Mz/RT matrix activation requires exactly one "
            "source_feature_family_id per row until multi-source "
            f"PeakHypothesis activation is implemented: matrix_row_index {index}"
        )
    return source_ids[0]


def _matrix_source_family_id(
    matrix_row: Mapping[str, str],
    row_key: str,
) -> str:
    return (
        matrix_row.get(_INPUT_SOURCE_FEATURE_FAMILY_ID, "")
        or matrix_row.get("feature_family_id", "")
        or row_key
    )


def _matrix_source_family_ids(
    matrix_by_family: Mapping[str, Mapping[str, str]],
) -> set[str]:
    return {
        _matrix_source_family_id(row, row_key)
        for row_key, row in matrix_by_family.items()
    }


def _matrix_row_keys_for_source_family(
    matrix_by_family: Mapping[str, Mapping[str, str]],
    family_id: str,
) -> tuple[str, ...]:
    return tuple(
        row_key
        for row_key, row in matrix_by_family.items()
        if _matrix_source_family_id(row, row_key) == family_id
    )


def _pop_matrix_rows_for_source_family(
    matrix_by_family: dict[str, dict[str, str]],
    family_id: str,
) -> None:
    for row_key in _matrix_row_keys_for_source_family(matrix_by_family, family_id):
        matrix_by_family.pop(row_key, None)


def _matrix_row_key_for_decision(
    matrix_by_family: Mapping[str, Mapping[str, str]],
    *,
    family_id: str,
    sample_id: str,
    decision: Mapping[str, str],
) -> str | None:
    peak_hypothesis_id = decision.get("peak_hypothesis_id", "").strip()
    if peak_hypothesis_id and peak_hypothesis_id in matrix_by_family:
        return peak_hypothesis_id
    if family_id in matrix_by_family:
        return family_id
    candidate_keys = _matrix_row_keys_for_source_family(matrix_by_family, family_id)
    if not candidate_keys:
        return None
    if peak_hypothesis_id:
        raise ValueError(
            f"{family_id}/{sample_id}: activation decision peak_hypothesis_id "
            f"{peak_hypothesis_id} does not match a public product matrix row"
        )
    valued_keys = tuple(
        row_key
        for row_key in candidate_keys
        if sample_id and matrix_by_family[row_key].get(sample_id)
    )
    if len(valued_keys) == 1:
        return valued_keys[0]
    if len(valued_keys) > 1:
        raise ValueError(
            f"{family_id}/{sample_id}: activation decision is ambiguous across "
            "public product matrix rows; provide peak_hypothesis_id"
        )
    if len(candidate_keys) == 1:
        return candidate_keys[0]
    return None


def _product_matrix_rows_from_hypotheses(
    hypothesis_rows: Sequence[Mapping[str, str]],
    sample_columns: Sequence[str],
) -> list[dict[str, str]]:
    machine_rows: list[dict[str, str]] = []
    for hypothesis_row in hypothesis_rows:
        row = {
            "Mz": hypothesis_row.get("family_center_mz", ""),
            "RT": _matrix_center_rt(hypothesis_row),
        }
        row.update(
            {
                sample: hypothesis_row.get(sample, "")
                for sample in sample_columns
            }
        )
        machine_rows.append(row)
    return [
        {column: str(value) for column, value in row.items()}
        for row in project_public_matrix_rows(
            machine_rows,
            sample_columns=sample_columns,
        )
    ]


def _alignment_matrix_identity_rows(
    hypothesis_rows: Sequence[Mapping[str, str]],
    sample_columns: Sequence[str],
    matrix_header: Sequence[str],
    matrix_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, hypothesis_row in enumerate(hypothesis_rows, start=1):
        source_family_ids = tuple(
            part
            for part in hypothesis_row.get("feature_family_id", "").split(";")
            if part
        )
        row_identity_basis = hypothesis_row.get("row_identity_basis", "")
        accepted_count = sum(
            1 for sample in sample_columns if hypothesis_row.get(sample)
        )
        if len(source_family_ids) != 1:
            peak_hypothesis_id = hypothesis_row.get("peak_hypothesis_id", "")
            raise ValueError(
                f"{peak_hypothesis_id}: product matrix row requires exactly one "
                "source_feature_family_id, got "
                f"{';'.join(source_family_ids) or '<blank>'}"
            )
        is_projection = row_identity_basis == "family_projection_no_split_evidence"
        split_evaluation_status = _split_evaluation_status(row_identity_basis)
        rows.append(
            {
                "identity_schema_version": MATRIX_IDENTITY_SCHEMA_VERSION,
                "matrix_row_index": str(index),
                "Mz": canonical_mz_text(
                    hypothesis_row.get("family_center_mz", "")
                ),
                "RT": _matrix_center_rt(hypothesis_row),
                "peak_hypothesis_id": hypothesis_row.get("peak_hypothesis_id", ""),
                "row_identity_basis": row_identity_basis,
                "split_evaluation_status": split_evaluation_status,
                "projection_status": (
                    "family_projection" if is_projection else "not_projection"
                ),
                "source_feature_family_ids": ";".join(source_family_ids),
                "source_feature_family_count": str(len(source_family_ids)),
                "center_mz_basis": "activation_hypothesis_family_center_mz",
                "center_rt_basis": _matrix_center_rt_basis(hypothesis_row),
                "center_weight_basis": "activation_formal_matrix_row",
                "accepted_cell_count": str(accepted_count),
                "accepted_sample_count": str(accepted_count),
                "evidence_status": (
                    "family_projection_not_split_proof"
                    if is_projection
                    else _evidence_status(row_identity_basis)
                ),
                "parent_peak_hypothesis_id": (
                    _parent_peak_hypothesis_id(hypothesis_row, source_family_ids)
                ),
                "child_peak_hypothesis_ids": "",
                "matrix_row_binding_sha256": "",
            }
        )
    return list(
        bind_matrix_identity_rows(
            matrix_header=matrix_header,
            matrix_rows=matrix_rows,
            identity_columns=ALIGNMENT_MATRIX_IDENTITY_COLUMNS,
            identity_rows=rows,
        )
    )


def _matrix_center_rt(hypothesis_row: Mapping[str, str]) -> str:
    return (
        hypothesis_row.get(_HYPOTHESIS_CENTER_RT, "")
        or hypothesis_row.get("family_center_rt", "")
    )


def _matrix_center_rt_basis(hypothesis_row: Mapping[str, str]) -> str:
    return (
        hypothesis_row.get(_CENTER_RT_BASIS, "")
        or "activation_hypothesis_family_center_rt"
    )


def _split_evaluation_status(row_identity_basis: str) -> str:
    if row_identity_basis == "split_peak_hypothesis":
        return "complete_product_ready_split"
    if row_identity_basis == "no_split_peak_hypothesis":
        return "complete_no_product_ready_split"
    return "incomplete_scope"


def _evidence_status(row_identity_basis: str) -> str:
    if row_identity_basis == "split_peak_hypothesis":
        return "product_matrix_split_identity_complete"
    if row_identity_basis == "no_split_peak_hypothesis":
        return "product_matrix_identity_complete"
    return "product_matrix_identity_incomplete"


def _parent_peak_hypothesis_id(
    hypothesis_row: Mapping[str, str],
    source_family_ids: Sequence[str],
) -> str:
    if hypothesis_row.get("row_identity_basis", "") != "split_peak_hypothesis":
        return ""
    peak_hypothesis_id = hypothesis_row.get("peak_hypothesis_id", "")
    if "::" in peak_hypothesis_id:
        return peak_hypothesis_id.split("::", 1)[0]
    if len(source_family_ids) == 1:
        return source_family_ids[0]
    return ";".join(source_family_ids)


def _peak_hypothesis_matrix_rows(
    *,
    matrix_by_family: Mapping[str, Mapping[str, str]],
    review_by_family: Mapping[str, Mapping[str, str]],
    decisions_by_key: Mapping[tuple[str, str], Mapping[str, str]],
    sample_columns: Sequence[str],
    legacy_rt_row_oracle: Sequence[LegacyRtRowReference],
    legacy_rt_row_oracle_mz_ppm: float,
    legacy_rt_row_oracle_rt_tolerance_min: float,
    include_family_projections: bool = False,
    rt_mode_evidence_rows: Sequence[Mapping[str, str]] = (),
) -> tuple[list[dict[str, str]], FormalMatrixStats]:
    rows_by_hypothesis: dict[str, dict[str, str]] = {}
    rt_mode_by_key = _rt_mode_by_key(rt_mode_evidence_rows)
    rt_centers_by_hypothesis: dict[str, _RtCenterAccumulator] = {}
    family_projection_ids: set[str] = set()
    excluded_family_projection_ids: set[str] = set()
    excluded_family_projection_cells = 0
    legacy_rt_row_context_row_ids: set[str] = set()
    matrix_value_conflict_cells = 0
    for row_key in sorted(matrix_by_family):
        family_row = matrix_by_family[row_key]
        family_id = _matrix_source_family_id(family_row, row_key)
        review_row = review_by_family.get(family_id, {})
        for sample_id in sample_columns:
            value = family_row.get(sample_id, "")
            if not value:
                continue
            decision = decisions_by_key.get((family_id, sample_id), {})
            peak_hypothesis_id = _decision_peak_hypothesis_id(decision)
            row_identity_basis = _decision_row_identity_basis(
                family_id,
                peak_hypothesis_id,
            )
            candidate_container_id = decision.get("candidate_container_id", family_id)
            if not peak_hypothesis_id:
                input_peak_hypothesis_id = family_row.get(
                    _INPUT_PEAK_HYPOTHESIS_ID,
                    "",
                )
                if input_peak_hypothesis_id:
                    peak_hypothesis_id = input_peak_hypothesis_id
                    row_identity_basis = (
                        family_row.get(_INPUT_ROW_IDENTITY_BASIS, "")
                        or "input_matrix_identity"
                    )
                    legacy_rt_row_context_id = family_row.get(
                        _INPUT_LEGACY_RT_ROW_CONTEXT_ID,
                        "",
                    )
                else:
                    projection_id = _legacy_projection_hypothesis_id(family_id)
                    if not include_family_projections:
                        excluded_family_projection_ids.add(projection_id)
                        excluded_family_projection_cells += 1
                        continue
                    legacy_reference = _match_legacy_rt_row_reference(
                        review_row,
                        legacy_rt_row_oracle,
                        mz_ppm=legacy_rt_row_oracle_mz_ppm,
                        rt_tolerance_min=legacy_rt_row_oracle_rt_tolerance_min,
                    )
                    peak_hypothesis_id = projection_id
                    row_identity_basis = "family_projection_no_split_evidence"
                    family_projection_ids.add(peak_hypothesis_id)
                    legacy_rt_row_context_id = ""
                    if legacy_reference is not None:
                        legacy_rt_row_context_id = legacy_reference.peak_hypothesis_id
                        legacy_rt_row_context_row_ids.add(peak_hypothesis_id)
                candidate_container_id = family_id
            else:
                legacy_rt_row_context_id = ""
            metadata_row = _identity_metadata_row(family_row, review_row)
            _reject_multi_family_hypothesis_collapse(
                rows_by_hypothesis.get(peak_hypothesis_id),
                peak_hypothesis_id=peak_hypothesis_id,
                family_id=family_id,
            )
            row = rows_by_hypothesis.setdefault(
                peak_hypothesis_id,
                _new_peak_hypothesis_matrix_row(
                    peak_hypothesis_id,
                    family_id=family_id,
                    candidate_container_id=candidate_container_id,
                    row_identity_basis=row_identity_basis,
                    legacy_rt_row_context_id=legacy_rt_row_context_id,
                    review_row=metadata_row,
                    sample_columns=sample_columns,
                ),
            )
            _append_unique_cell(row, "feature_family_id", family_id)
            _append_unique_cell(row, "candidate_container_id", candidate_container_id)
            _append_unique_cell(
                row,
                "legacy_rt_row_context_id",
                legacy_rt_row_context_id,
            )
            previous = row.get(sample_id, "")
            if previous and previous != value:
                value = _select_conflicting_matrix_value(previous, value)
                matrix_value_conflict_cells += 1
            row[sample_id] = value
            _add_rt_mode_center(
                rt_centers_by_hypothesis,
                family_id=family_id,
                sample_id=sample_id,
                peak_hypothesis_id=peak_hypothesis_id,
                value=value,
                rt_mode_by_key=rt_mode_by_key,
            )
    _apply_rt_mode_centers(rows_by_hypothesis, rt_centers_by_hypothesis)
    return (
        [rows_by_hypothesis[key] for key in sorted(rows_by_hypothesis)],
        FormalMatrixStats(
            family_projection_rows=len(family_projection_ids),
            legacy_rt_row_context_rows=len(legacy_rt_row_context_row_ids),
            matrix_value_conflict_cells=matrix_value_conflict_cells,
            family_projection_rows_excluded=len(excluded_family_projection_ids),
            family_projection_cells_excluded=excluded_family_projection_cells,
        ),
    )


def _decision_peak_hypothesis_id(decision: Mapping[str, str]) -> str:
    if decision.get("activation_unit_scope") != "peak_hypothesis":
        return ""
    if decision.get("activation_status") != "auto_activate":
        return ""
    return decision.get("peak_hypothesis_id", "")


def _rt_mode_by_key(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    return {
        (row.get("feature_family_id", ""), row.get("sample_stem", "")): row
        for row in rows
        if row.get("feature_family_id") and row.get("sample_stem")
    }


def _add_rt_mode_center(
    rt_centers_by_hypothesis: dict[str, _RtCenterAccumulator],
    *,
    family_id: str,
    sample_id: str,
    peak_hypothesis_id: str,
    value: str,
    rt_mode_by_key: Mapping[tuple[str, str], Mapping[str, str]],
) -> None:
    row = rt_mode_by_key.get((family_id, sample_id))
    if row is None or not _rt_mode_matches_hypothesis(row, peak_hypothesis_id):
        return
    raw_rt = _to_float(row.get("raw_selected_rt"))
    if raw_rt is None:
        return
    weight = _to_float(value)
    rt_centers_by_hypothesis.setdefault(
        peak_hypothesis_id,
        _RtCenterAccumulator(),
    ).add(raw_rt, weight)


def _rt_mode_matches_hypothesis(
    rt_mode_row: Mapping[str, str],
    peak_hypothesis_id: str,
) -> bool:
    if not _rt_mode_is_product_coordinate_authority(rt_mode_row):
        return False
    mode_id = rt_mode_row.get("selected_mode_id", "")
    if not mode_id or "::" not in peak_hypothesis_id:
        return False
    return peak_hypothesis_id.rsplit("::", 1)[1] == mode_id


def _rt_mode_is_product_coordinate_authority(rt_mode_row: Mapping[str, str]) -> bool:
    if rt_mode_row.get("rt_mode_status", "") != "mode_supported":
        return False
    return rt_mode_row.get("rt_mode_evidence_level", "") in {
        "irt_selected_apex_modes",
        "mode_assignment_summary",
    }


def _apply_rt_mode_centers(
    rows_by_hypothesis: Mapping[str, dict[str, str]],
    rt_centers_by_hypothesis: Mapping[str, _RtCenterAccumulator],
) -> None:
    for peak_hypothesis_id, accumulator in rt_centers_by_hypothesis.items():
        row = rows_by_hypothesis.get(peak_hypothesis_id)
        center = accumulator.center
        if row is None or center is None:
            continue
        row[_HYPOTHESIS_CENTER_RT] = _format_float(center)
        row[_CENTER_RT_BASIS] = accumulator.basis


def _decision_row_identity_basis(family_id: str, peak_hypothesis_id: str) -> str:
    if not peak_hypothesis_id:
        return ""
    if peak_hypothesis_id == family_id:
        return "no_split_peak_hypothesis"
    return "split_peak_hypothesis"


def _reject_multi_family_hypothesis_collapse(
    existing_row: Mapping[str, str] | None,
    *,
    peak_hypothesis_id: str,
    family_id: str,
) -> None:
    if existing_row is None:
        return
    source_ids = tuple(
        part for part in existing_row.get("feature_family_id", "").split(";") if part
    )
    if not source_ids or family_id in source_ids:
        return
    collapsed_ids = ";".join((*source_ids, family_id))
    raise ValueError(
        f"{peak_hypothesis_id}: product matrix row requires exactly one "
        f"source_feature_family_id, got {collapsed_ids}"
    )


def _identity_metadata_row(
    family_row: Mapping[str, str],
    review_row: Mapping[str, str],
) -> dict[str, str]:
    if family_row.get(_INPUT_PEAK_HYPOTHESIS_ID):
        family_center_mz = (
            family_row.get("family_center_mz")
            or review_row.get("family_center_mz", "")
        )
    else:
        family_center_mz = (
            review_row.get("family_center_mz")
            or family_row.get("family_center_mz", "")
        )
    return {
        "neutral_loss_tag": (
            family_row.get("neutral_loss_tag")
            or review_row.get("neutral_loss_tag", "")
        ),
        "family_center_mz": canonical_mz_text(family_center_mz),
        "family_center_rt": (
            family_row.get("family_center_rt")
            or review_row.get("family_center_rt", "")
        ),
    }


def _legacy_projection_hypothesis_id(family_id: str) -> str:
    return f"{family_id}::family_projection"


def _match_legacy_rt_row_reference(
    review_row: Mapping[str, str],
    oracle_rows: Sequence[LegacyRtRowReference],
    *,
    mz_ppm: float,
    rt_tolerance_min: float,
) -> LegacyRtRowReference | None:
    if not oracle_rows:
        return None
    mz = _to_float(review_row.get("family_center_mz"))
    rt = _to_float(review_row.get("family_center_rt"))
    if mz is None or rt is None:
        return None
    matches: list[tuple[float, float, LegacyRtRowReference]] = []
    for oracle_row in oracle_rows:
        ppm = abs(oracle_row.mz - mz) / mz * 1_000_000
        rt_delta = abs(oracle_row.rt - rt)
        if ppm <= mz_ppm and rt_delta <= rt_tolerance_min:
            matches.append((rt_delta, ppm, oracle_row))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], item[2].row_number))
    if len(matches) > 1 and _legacy_oracle_match_is_ambiguous(matches):
        return None
    return matches[0][2]


def _legacy_oracle_match_is_ambiguous(
    matches: Sequence[tuple[float, float, LegacyRtRowReference]],
) -> bool:
    best = matches[0]
    second = matches[1]
    return abs(best[0] - second[0]) <= 0.05 and abs(best[1] - second[1]) <= 5.0


def _to_float(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _format_float(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _append_unique_cell(row: dict[str, str], column: str, value: str) -> None:
    if not value:
        return
    existing = tuple(part for part in row.get(column, "").split(";") if part)
    if value in existing:
        return
    row[column] = ";".join((*existing, value))


def _select_conflicting_matrix_value(existing: str, incoming: str) -> str:
    existing_value = _to_float(existing)
    incoming_value = _to_float(incoming)
    if existing_value is None or incoming_value is None:
        return existing
    return incoming if incoming_value > existing_value else existing


def _new_peak_hypothesis_matrix_row(
    peak_hypothesis_id: str,
    *,
    family_id: str,
    candidate_container_id: str,
    row_identity_basis: str,
    legacy_rt_row_context_id: str,
    review_row: Mapping[str, str],
    sample_columns: Sequence[str],
) -> dict[str, str]:
    row = {
        "peak_hypothesis_id": peak_hypothesis_id,
        "feature_family_id": family_id,
        "candidate_container_id": candidate_container_id or family_id,
        "row_identity_basis": row_identity_basis,
        "legacy_rt_row_context_id": legacy_rt_row_context_id,
        "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
        "family_center_mz": canonical_mz_text(
            review_row.get("family_center_mz", "")
        ),
        "family_center_rt": review_row.get("family_center_rt", ""),
    }
    row.update({sample: "" for sample in sample_columns})
    return row


def _new_matrix_row(
    family_id: str,
    *,
    sample_columns: Sequence[str],
    review_row: Mapping[str, str],
) -> dict[str, str]:
    row = {
        "feature_family_id": family_id,
        "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
        "family_center_mz": canonical_mz_text(
            review_row.get("family_center_mz", "")
        ),
        "family_center_rt": review_row.get("family_center_rt", ""),
    }
    row.update({sample: "" for sample in sample_columns})
    return row


def _accepted_samples(
    matrix_row: Mapping[str, str] | None,
    sample_columns: Sequence[str],
) -> tuple[str, ...]:
    if matrix_row is None:
        return ()
    return tuple(sample for sample in sample_columns if matrix_row.get(sample))


def _accepted_samples_for_source_family(
    matrix_by_family: Mapping[str, Mapping[str, str]],
    family_id: str,
    sample_columns: Sequence[str],
) -> tuple[str, ...]:
    accepted: list[str] = []
    for row_key in _matrix_row_keys_for_source_family(matrix_by_family, family_id):
        for sample in _accepted_samples(matrix_by_family[row_key], sample_columns):
            if sample not in accepted:
                accepted.append(sample)
    return tuple(accepted)


def _matrix_contains_source_family(
    matrix_by_family: Mapping[str, Mapping[str, str]],
    family_id: str,
) -> bool:
    return bool(_matrix_row_keys_for_source_family(matrix_by_family, family_id))


def _has_any_sample_value(
    row: Mapping[str, str],
    sample_columns: Sequence[str],
) -> bool:
    return any(row.get(sample) for sample in sample_columns)


def _require_columns(header: Sequence[str], columns: Sequence[str]) -> None:
    missing = [column for column in columns if column not in header]
    if missing:
        raise ValueError(f"missing required columns: {','.join(missing)}")


__all__ = [
    "FormalMatrixStats",
    "LegacyRtRowReference",
    "_RtCenterAccumulator",
    "_accepted_samples",
    "_accepted_samples_for_source_family",
    "_alignment_matrix_identity_rows",
    "_append_unique_cell",
    "_apply_rt_mode_centers",
    "_decision_peak_hypothesis_id",
    "_decision_row_identity_basis",
    "_evidence_status",
    "_format_float",
    "_formal_matrix_header",
    "_has_any_sample_value",
    "_identity_metadata_row",
    "_legacy_oracle_match_is_ambiguous",
    "_legacy_projection_hypothesis_id",
    "_match_legacy_rt_row_reference",
    "_matrix_center_rt",
    "_matrix_center_rt_basis",
    "_matrix_contains_source_family",
    "_matrix_input_by_family",
    "_matrix_row_key_for_decision",
    "_matrix_row_keys_for_source_family",
    "_matrix_source_family_id",
    "_matrix_source_family_ids",
    "_new_matrix_row",
    "_new_peak_hypothesis_matrix_row",
    "_parent_peak_hypothesis_id",
    "_peak_hypothesis_matrix_rows",
    "_pop_matrix_rows_for_source_family",
    "_positive_row_index",
    "_product_matrix_header",
    "_product_matrix_rows_from_hypotheses",
    "_public_matrix_row_key",
    "_public_matrix_rows_by_family",
    "_reject_multi_family_hypothesis_collapse",
    "_rt_mode_by_key",
    "_rt_mode_is_product_coordinate_authority",
    "_rt_mode_matches_hypothesis",
    "_select_conflicting_matrix_value",
    "_single_source_family_id",
    "_split_evaluation_status",
    "_to_float",
    "_validate_identity_coordinate",
    "_validate_public_identity_basis",
]
