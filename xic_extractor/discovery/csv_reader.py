from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    DiscoveryCandidateUniverseReceipt,
    discovery_receipt_from_index_fields,
    validate_discovery_candidate_artifact,
    validate_discovery_candidate_universe,
)
from xic_extractor.discovery.models import (
    DISCOVERY_CANDIDATE_COLUMNS,
    DISCOVERY_CANDIDATE_STATE_VALUES,
    MS1_FEATURE_BACKED_STATES,
    DiscoveryCandidate,
    DiscoveryCandidateState,
    GroupPrecursorMzBasis,
    NeutralLossErrorBasis,
)
from xic_extractor.processing_identity import IdentityContractError

_BATCH_REQUIRED_COLUMNS = ("sample_stem", "raw_file", "candidate_csv")
_BATCH_OWNER_REQUIRED_COLUMNS = (
    "schema_version",
    *_BATCH_REQUIRED_COLUMNS,
    "candidate_count",
    *DISCOVERY_BATCH_RECEIPT_COLUMNS,
)
_BATCH_UNESCAPE_FIELDS = {"sample_stem", "raw_file", "candidate_csv", "review_csv"}
_CANDIDATE_UNESCAPE_FIELDS = {
    "sample_stem",
    "raw_file",
    "candidate_id",
    "feature_family_id",
    "ms1_feature_row_id",
}

_INT_FIELDS = {
    "evidence_score",
    "feature_family_size",
    "seed_event_count",
    "best_ms2_scan_id",
    "selected_tag_count",
    "matched_tag_count",
}
_FLOAT_FIELDS = {
    "precursor_mz",
    "product_mz",
    "observed_neutral_loss_da",
    "best_seed_rt",
    "ms1_apex_rt",
    "ms1_area",
    "ms2_product_max_intensity",
    "configured_neutral_loss_da",
    "neutral_loss_mass_error_ppm",
    "scan_precursor_mz",
    "scan_precursor_delta_da",
    "max_scan_precursor_abs_delta_da",
    "rt_seed_min",
    "rt_seed_max",
    "ms1_search_rt_min",
    "ms1_search_rt_max",
    "ms1_seed_delta_min",
    "ms1_peak_rt_start",
    "ms1_peak_rt_end",
    "ms1_height",
    "ms1_scan_support_score",
}

_ALLOWED_PRECURSOR_MZ_BASIS = {
    "scan_precursor",
    "scan_monoisotopic",
    "product_plus_neutral_loss",
    "mixed",
}
_ALLOWED_NEUTRAL_LOSS_ERROR_BASIS = {
    "measured_scan_precursor_product",
    "recorded_monoisotopic_precursor_product",
    "configured_loss_inferred_precursor",
    "mixed",
}
_ALLOWED_DISCOVERY_CANDIDATE_STATES = set(DISCOVERY_CANDIDATE_STATE_VALUES)
_MS1_FEATURE_BACKED_STATES = set(MS1_FEATURE_BACKED_STATES)
_ROW_ID_MZ_TOLERANCE_DA = 0.001
_MS1_FEATURE_ROW_ID_RT_TOLERANCE_MIN = 0.001
_ROW_ID_PATTERN = re.compile(
    r"^(?P<sample>.+)#(?P<scan>\d+)@mz(?P<precursor>\d+(?:\.\d+)?)"
    r"_p(?P<product>\d+(?:\.\d+)?)$",
)
_MS1_FEATURE_ROW_ID_PATTERN = re.compile(
    r"^(?P<sample>[^|]+)\|(?P<tag>[^|]+)\|"
    r"(?P<precursor>\d+(?:\.\d+)?)\|(?P<rt>\d+(?:\.\d+)?)$"
)


@dataclass(frozen=True)
class DiscoveryBatchInput:
    sample_order: tuple[str, ...]
    candidate_csvs: dict[str, Path]
    raw_files: dict[str, Path | None]
    review_csvs: dict[str, Path | None]
    candidate_receipts: dict[
        str,
        DiscoveryCandidateUniverseReceipt,
    ]


@dataclass(frozen=True)
class _CandidateRowIdentity:
    sample_stem: str
    scan_id: int
    precursor_mz: float
    product_mz: float


@dataclass(frozen=True)
class _Ms1FeatureRowIdentity:
    sample_stem: str
    neutral_loss_tag: str
    precursor_mz: float
    rt: float


def read_discovery_batch_index(
    path: Path,
) -> DiscoveryBatchInput:
    rows, fieldnames = _read_csv_rows(path)
    _require_columns(path, fieldnames, _BATCH_REQUIRED_COLUMNS)
    if "schema_version" not in fieldnames and not (
        set(DISCOVERY_BATCH_RECEIPT_COLUMNS) & set(fieldnames)
    ):
        raise ValueError(
            f"{path}: {DISCOVERY_BATCH_INDEX_SCHEMA_VERSION} owner closure is required"
        )
    _require_columns(path, fieldnames, _BATCH_OWNER_REQUIRED_COLUMNS)

    sample_order: list[str] = []
    candidate_csvs: dict[str, Path] = {}
    raw_files: dict[str, Path | None] = {}
    review_csvs: dict[str, Path | None] = {}
    candidate_receipts: dict[
        str,
        DiscoveryCandidateUniverseReceipt,
    ] = {}
    for row_number, row in rows:
        sample_stem = _machine_text(row.get("sample_stem", ""))
        candidate_csv = _machine_text(row.get("candidate_csv", ""))
        raw_file = _machine_text(row.get("raw_file", ""))
        review_csv = _machine_text(row.get("review_csv", ""))
        if not sample_stem:
            raise ValueError(f"{path}: row {row_number}: sample_stem is required")
        if not candidate_csv:
            raise ValueError(f"{path}: row {row_number}: candidate_csv is required")
        if sample_stem in candidate_csvs:
            raise ValueError(
                f"{path}: row {row_number}: duplicate sample_stem values are not "
                f"supported ({sample_stem!r})"
            )
        schema_version = _machine_text(row.get("schema_version", ""))
        if schema_version != DISCOVERY_BATCH_INDEX_SCHEMA_VERSION:
            raise ValueError(
                f"{path}: row {row_number}: unsupported schema_version "
                f"{schema_version!r}"
            )
        candidate_count = _batch_nonnegative_int(
            path,
            row_number,
            row.get("candidate_count", ""),
            field="candidate_count",
        )
        try:
            receipt = discovery_receipt_from_index_fields(
                row,
                sample_stem=sample_stem,
                candidate_count=candidate_count,
            )
        except (IdentityContractError, ValueError) as exc:
            raise ValueError(
                f"{path}: row {row_number}: invalid Discovery receipt: {exc}"
            ) from exc
        sample_order.append(sample_stem)
        candidate_csvs[sample_stem] = _resolve_artifact_path(path.parent, candidate_csv)
        raw_files[sample_stem] = Path(raw_file) if raw_file else None
        review_csvs[sample_stem] = (
            _resolve_artifact_path(path.parent, review_csv) if review_csv else None
        )
        candidate_receipts[sample_stem] = receipt
    return DiscoveryBatchInput(
        sample_order=tuple(sample_order),
        candidate_csvs=candidate_csvs,
        raw_files=raw_files,
        review_csvs=review_csvs,
        candidate_receipts=candidate_receipts,
    )


def read_discovery_candidates_csv(
    path: Path,
) -> tuple[DiscoveryCandidate, ...]:
    rows, fieldnames = _read_csv_rows(path)
    _require_columns(path, fieldnames, DISCOVERY_CANDIDATE_COLUMNS)

    candidates: list[DiscoveryCandidate] = []
    seen_candidate_ids: dict[str, int] = {}
    seen_ms1_feature_row_ids: dict[tuple[str, str, str], int] = {}
    for row_number, row in rows:
        candidate = _parse_candidate_row(path, row_number, row)
        previous_row = seen_candidate_ids.get(candidate.candidate_id)
        if previous_row is not None:
            raise ValueError(
                f"{path}: row {row_number}: duplicate candidate_id "
                f"{candidate.candidate_id!r}; first seen at row {previous_row}"
            )
        seen_candidate_ids[candidate.candidate_id] = row_number
        _require_unique_ms1_feature_row_id(
            path,
            row_number,
            candidate,
            seen_ms1_feature_row_ids,
        )
        candidates.append(candidate)
    return tuple(candidates)


def read_owner_bound_discovery_candidates_csv(
    path: Path,
    *,
    receipt: DiscoveryCandidateUniverseReceipt,
) -> tuple[DiscoveryCandidate, ...]:
    """Read one exact Discovery artifact and close it against its receipt."""

    try:
        validate_discovery_candidate_artifact(receipt, path)
        candidates = read_discovery_candidates_csv(path)
        validate_discovery_candidate_artifact(receipt, path)
        return validate_discovery_candidate_universe(
            receipt,
            acquisition_source=receipt.acquisition_source,
            candidates=candidates,
        )
    except IdentityContractError as exc:
        raise ValueError(
            f"{path}: invalid owner-bound candidate artifact: {exc}"
        ) from exc


def _read_csv_rows(
    path: Path,
) -> tuple[list[tuple[int, dict[str, str]]], tuple[str, ...]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            return (
                [(index, row) for index, row in enumerate(reader, start=2)],
                fieldnames,
            )
    except OSError as exc:
        raise ValueError(f"{path}: CSV file could not be read: {exc}") from exc


def _require_columns(
    path: Path,
    fieldnames: tuple[str, ...],
    required_columns: tuple[str, ...],
) -> None:
    missing = [column for column in required_columns if column not in fieldnames]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"{path}: missing required columns: {joined}")


def _parse_candidate_row(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> DiscoveryCandidate:
    candidate_id = _machine_field(row, "candidate_id")
    row_identity = _parse_row_identity_candidate_id(path, row_number, candidate_id)
    precursor_mz_text = row.get("precursor_mz", "")
    product_mz_text = row.get("product_mz", "")
    precursor_mz = _parse_float(path, row_number, row, "precursor_mz")
    product_mz = _parse_float(path, row_number, row, "product_mz")
    sample_stem = _machine_field(row, "sample_stem")
    best_ms2_scan_id = _parse_int(path, row_number, row, "best_ms2_scan_id")
    ms1_peak_found = _parse_bool(path, row_number, row, "ms1_peak_found")
    neutral_loss_tag = _required_text(path, row_number, row, "neutral_loss_tag")
    precursor_mz_basis = _parse_precursor_mz_basis(path, row_number, row)
    best_seed_rt = _parse_float(path, row_number, row, "best_seed_rt")
    ms1_apex_rt = _parse_optional_float(path, row_number, row, "ms1_apex_rt")
    ms1_peak_rt_start = _parse_optional_float(
        path, row_number, row, "ms1_peak_rt_start"
    )
    ms1_peak_rt_end = _parse_optional_float(path, row_number, row, "ms1_peak_rt_end")
    discovery_candidate_state = _parse_discovery_candidate_state(
        path,
        row_number,
        row,
    )
    ms1_feature_row_id = _machine_field(row, "ms1_feature_row_id")
    _validate_discovery_candidate_state_identity(
        path,
        row_number,
        discovery_candidate_state=discovery_candidate_state,
        ms1_feature_row_id=ms1_feature_row_id,
        ms1_peak_found=ms1_peak_found,
    )
    _require_candidate_id_matches_row(
        path,
        row_number,
        candidate_id,
        row_identity,
        sample_stem=sample_stem,
        best_ms2_scan_id=best_ms2_scan_id,
        precursor_mz=precursor_mz,
        precursor_mz_text=precursor_mz_text,
        product_mz=product_mz,
        product_mz_text=product_mz_text,
    )
    _validate_ms1_feature_row_identity(
        path,
        row_number,
        ms1_feature_row_id=ms1_feature_row_id,
        sample_stem=sample_stem,
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=row_identity.precursor_mz,
        best_seed_rt=best_seed_rt,
        ms1_peak_found=ms1_peak_found,
        ms1_apex_rt=ms1_apex_rt,
        ms1_peak_rt_start=ms1_peak_rt_start,
        ms1_peak_rt_end=ms1_peak_rt_end,
    )
    return DiscoveryCandidate(
        review_priority=_required_text(path, row_number, row, "review_priority"),  # type: ignore[arg-type]
        evidence_tier=_required_text(path, row_number, row, "evidence_tier"),
        evidence_score=_parse_int(path, row_number, row, "evidence_score"),
        ms2_support=_required_text(path, row_number, row, "ms2_support"),
        ms1_support=_required_text(path, row_number, row, "ms1_support"),
        rt_alignment=_required_text(path, row_number, row, "rt_alignment"),
        discovery_candidate_state=discovery_candidate_state,
        ms1_feature_row_id=ms1_feature_row_id,
        candidate_id=candidate_id,
        feature_family_id=_machine_field(row, "feature_family_id"),
        feature_family_size=_parse_int(path, row_number, row, "feature_family_size"),
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=_parse_float(
            path, row_number, row, "observed_neutral_loss_da"
        ),
        best_seed_rt=best_seed_rt,
        seed_event_count=_parse_int(path, row_number, row, "seed_event_count"),
        ms1_peak_found=ms1_peak_found,
        ms1_apex_rt=ms1_apex_rt,
        ms1_area=_parse_optional_float(path, row_number, row, "ms1_area"),
        ms2_product_max_intensity=_parse_float(
            path, row_number, row, "ms2_product_max_intensity"
        ),
        reason=_required_text(path, row_number, row, "reason"),
        raw_file=Path(_machine_field(row, "raw_file")),
        sample_stem=sample_stem,
        best_ms2_scan_id=best_ms2_scan_id,
        seed_scan_ids=_parse_int_tuple(path, row_number, row, "seed_scan_ids"),
        neutral_loss_tag=neutral_loss_tag,
        configured_neutral_loss_da=_parse_float(
            path, row_number, row, "configured_neutral_loss_da"
        ),
        neutral_loss_mass_error_ppm=_parse_optional_float(
            path, row_number, row, "neutral_loss_mass_error_ppm"
        ),
        neutral_loss_error_basis=_parse_neutral_loss_error_basis(path, row_number, row),
        precursor_mz_basis=precursor_mz_basis,
        scan_precursor_mz=_parse_optional_float(
            path, row_number, row, "scan_precursor_mz"
        ),
        scan_precursor_delta_da=_parse_optional_float(
            path, row_number, row, "scan_precursor_delta_da"
        ),
        max_scan_precursor_abs_delta_da=_parse_optional_float(
            path, row_number, row, "max_scan_precursor_abs_delta_da"
        ),
        rt_seed_min=_parse_float(path, row_number, row, "rt_seed_min"),
        rt_seed_max=_parse_float(path, row_number, row, "rt_seed_max"),
        ms1_search_rt_min=_parse_float(path, row_number, row, "ms1_search_rt_min"),
        ms1_search_rt_max=_parse_float(path, row_number, row, "ms1_search_rt_max"),
        ms1_seed_delta_min=_parse_optional_float(
            path, row_number, row, "ms1_seed_delta_min"
        ),
        ms1_peak_rt_start=ms1_peak_rt_start,
        ms1_peak_rt_end=ms1_peak_rt_end,
        ms1_height=_parse_optional_float(path, row_number, row, "ms1_height"),
        ms1_apex_mz=_parse_optional_float(path, row_number, row, "ms1_apex_mz"),
        ms1_trace_quality=_required_text(path, row_number, row, "ms1_trace_quality"),
        is_provisional=_parse_provisional_decision(path, row_number, row),
        ms1_scan_support_score=_parse_optional_float(
            path, row_number, row, "ms1_scan_support_score"
        ),
        selected_tag_count=_parse_int(path, row_number, row, "selected_tag_count"),
        matched_tag_count=_parse_int(path, row_number, row, "matched_tag_count"),
        matched_tag_names=_parse_text_tuple(row, "matched_tag_names"),
        primary_tag_name=_required_text(path, row_number, row, "primary_tag_name"),
        tag_combine_mode=_required_text(path, row_number, row, "tag_combine_mode"),  # type: ignore[arg-type]
        tag_intersection_status=_required_text(  # type: ignore[arg-type]
            path, row_number, row, "tag_intersection_status"
        ),
        tag_evidence_json=_required_text(path, row_number, row, "tag_evidence_json"),
    )


def _parse_provisional_decision(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> bool:
    return _parse_bool(path, row_number, row, "is_provisional")


def _resolve_artifact_path(parent: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else parent / path


def _machine_field(row: dict[str, str], column: str) -> str:
    value = row.get(column, "")
    if column in _CANDIDATE_UNESCAPE_FIELDS or column in _BATCH_UNESCAPE_FIELDS:
        return _machine_text(value)
    return value


def _machine_text(value: str) -> str:
    if len(value) >= 2 and value[0] == "'" and value[1] in ("=", "+", "-", "@"):
        return value[1:]
    return value


def _parse_row_identity_candidate_id(
    path: Path,
    row_number: int,
    candidate_id: str,
) -> _CandidateRowIdentity:
    match = _ROW_ID_PATTERN.match(candidate_id)
    if match is None:
        raise ValueError(
            f"{path}: row {row_number}: candidate_id must include row identity "
            "suffix '<sample>#<scan>@mz<precursor>_p<product>'; stale "
            "'<sample>#<scan>' discovery artifacts must be regenerated"
        )
    return _CandidateRowIdentity(
        sample_stem=match.group("sample"),
        scan_id=int(match.group("scan")),
        precursor_mz=float(match.group("precursor")),
        product_mz=float(match.group("product")),
    )


def _require_candidate_id_matches_row(
    path: Path,
    row_number: int,
    candidate_id: str,
    row_identity: _CandidateRowIdentity,
    *,
    sample_stem: str,
    best_ms2_scan_id: int,
    precursor_mz: float,
    precursor_mz_text: str,
    product_mz: float,
    product_mz_text: str,
) -> None:
    if row_identity.sample_stem != sample_stem:
        raise ValueError(
            f"{path}: row {row_number}: candidate_id sample stem does not match "
            f"sample_stem for {candidate_id!r}"
        )
    if row_identity.scan_id != best_ms2_scan_id:
        raise ValueError(
            f"{path}: row {row_number}: candidate_id scan id does not match "
            f"best_ms2_scan_id for {candidate_id!r}"
        )
    _require_candidate_id_mz_matches_row(
        path,
        row_number,
        candidate_id,
        column="precursor_mz",
        id_value=row_identity.precursor_mz,
        row_value=precursor_mz,
        row_text=precursor_mz_text,
    )
    _require_candidate_id_mz_matches_row(
        path,
        row_number,
        candidate_id,
        column="product_mz",
        id_value=row_identity.product_mz,
        row_value=product_mz,
        row_text=product_mz_text,
    )


def _require_candidate_id_mz_matches_row(
    path: Path,
    row_number: int,
    candidate_id: str,
    *,
    column: str,
    id_value: float,
    row_value: float,
    row_text: str,
) -> None:
    tolerance_da = _candidate_id_mz_tolerance_da(row_text)
    if abs(id_value - row_value) <= tolerance_da:
        return
    raise ValueError(
        f"{path}: row {row_number}: candidate_id {column} does not match "
        f"{column} for {candidate_id!r}: id={id_value:.8g}, "
        f"row={row_value:.8g}, tolerance_da={tolerance_da:g}"
    )


def _candidate_id_mz_tolerance_da(row_text: str) -> float:
    text = row_text.strip()
    mantissa = re.split("[eE]", text, maxsplit=1)[0]
    if "." not in mantissa:
        return _ROW_ID_MZ_TOLERANCE_DA
    fractional = mantissa.rsplit(".", maxsplit=1)[1]
    if not fractional.isdigit():
        return _ROW_ID_MZ_TOLERANCE_DA
    display_half_unit_da = 0.5 * (10 ** -len(fractional))
    return max(_ROW_ID_MZ_TOLERANCE_DA, display_half_unit_da)


def _parse_neutral_loss_error_basis(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> NeutralLossErrorBasis:
    value = _required_text(path, row_number, row, "neutral_loss_error_basis")
    if value not in _ALLOWED_NEUTRAL_LOSS_ERROR_BASIS:
        allowed = ", ".join(sorted(_ALLOWED_NEUTRAL_LOSS_ERROR_BASIS))
        raise ValueError(
            f"{path}: row {row_number}: neutral_loss_error_basis must be one "
            f"of {allowed}: {value!r}"
        )
    return cast(NeutralLossErrorBasis, value)


def _parse_precursor_mz_basis(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> GroupPrecursorMzBasis:
    value = _required_text(path, row_number, row, "precursor_mz_basis")
    if value not in _ALLOWED_PRECURSOR_MZ_BASIS:
        allowed = ", ".join(sorted(_ALLOWED_PRECURSOR_MZ_BASIS))
        raise ValueError(
            f"{path}: row {row_number}: precursor_mz_basis must be one of "
            f"{allowed}: {value!r}"
        )
    return cast(GroupPrecursorMzBasis, value)


def _parse_discovery_candidate_state(
    path: Path,
    row_number: int,
    row: dict[str, str],
) -> DiscoveryCandidateState:
    value = _required_text(path, row_number, row, "discovery_candidate_state")
    if value not in _ALLOWED_DISCOVERY_CANDIDATE_STATES:
        allowed = ", ".join(sorted(_ALLOWED_DISCOVERY_CANDIDATE_STATES))
        raise ValueError(
            f"{path}: row {row_number}: discovery_candidate_state must be one "
            f"of {allowed}: {value!r}"
        )
    return cast(DiscoveryCandidateState, value)


def _validate_discovery_candidate_state_identity(
    path: Path,
    row_number: int,
    *,
    discovery_candidate_state: DiscoveryCandidateState,
    ms1_feature_row_id: str,
    ms1_peak_found: bool,
) -> None:
    if discovery_candidate_state in _MS1_FEATURE_BACKED_STATES:
        if not ms1_peak_found:
            raise ValueError(
                f"{path}: row {row_number}: ms1_peak_found must be TRUE for "
                f"{discovery_candidate_state}"
            )
        if not ms1_feature_row_id:
            raise ValueError(
                f"{path}: row {row_number}: ms1_feature_row_id is required for "
                f"{discovery_candidate_state}"
            )
    if discovery_candidate_state == "review_only_orphan_nl":
        if ms1_peak_found:
            raise ValueError(
                f"{path}: row {row_number}: review_only_orphan_nl requires "
                "ms1_peak_found FALSE"
            )
        if ms1_feature_row_id:
            raise ValueError(
                f"{path}: row {row_number}: ms1_feature_row_id must be blank "
                "for review_only_orphan_nl"
            )


def _validate_ms1_feature_row_identity(
    path: Path,
    row_number: int,
    *,
    ms1_feature_row_id: str,
    sample_stem: str,
    neutral_loss_tag: str,
    precursor_mz: float,
    best_seed_rt: float,
    ms1_peak_found: bool,
    ms1_apex_rt: float | None,
    ms1_peak_rt_start: float | None,
    ms1_peak_rt_end: float | None,
) -> None:
    if not ms1_feature_row_id:
        return
    row_identity = _parse_ms1_feature_row_identity(
        path,
        row_number,
        ms1_feature_row_id,
    )
    expected_rt = _ms1_feature_row_identity_rt(
        best_seed_rt=best_seed_rt,
        ms1_peak_found=ms1_peak_found,
        ms1_apex_rt=ms1_apex_rt,
        ms1_peak_rt_start=ms1_peak_rt_start,
        ms1_peak_rt_end=ms1_peak_rt_end,
    )
    identity_matches = (
        row_identity.sample_stem == sample_stem
        and row_identity.neutral_loss_tag == neutral_loss_tag
        and abs(row_identity.precursor_mz - precursor_mz) <= _ROW_ID_MZ_TOLERANCE_DA
        and abs(row_identity.rt - expected_rt) <= _MS1_FEATURE_ROW_ID_RT_TOLERANCE_MIN
    )
    if not identity_matches:
        raise ValueError(
            f"{path}: row {row_number}: ms1_feature_row_id does not match "
            f"candidate row identity: {ms1_feature_row_id!r}"
        )


def _parse_ms1_feature_row_identity(
    path: Path,
    row_number: int,
    ms1_feature_row_id: str,
) -> _Ms1FeatureRowIdentity:
    match = _MS1_FEATURE_ROW_ID_PATTERN.match(ms1_feature_row_id)
    if match is None:
        raise ValueError(
            f"{path}: row {row_number}: ms1_feature_row_id must match "
            "'<sample>|<tag>|<precursor_mz>|<rt>'"
        )
    return _Ms1FeatureRowIdentity(
        sample_stem=match.group("sample"),
        neutral_loss_tag=match.group("tag"),
        precursor_mz=float(match.group("precursor")),
        rt=float(match.group("rt")),
    )


def _ms1_feature_row_identity_rt(
    *,
    best_seed_rt: float,
    ms1_peak_found: bool,
    ms1_apex_rt: float | None,
    ms1_peak_rt_start: float | None,
    ms1_peak_rt_end: float | None,
) -> float:
    if not ms1_peak_found:
        return best_seed_rt
    if ms1_apex_rt is not None:
        return ms1_apex_rt
    if ms1_peak_rt_start is not None and ms1_peak_rt_end is not None:
        return (ms1_peak_rt_start + ms1_peak_rt_end) / 2.0
    return best_seed_rt


def _require_unique_ms1_feature_row_id(
    path: Path,
    row_number: int,
    candidate: DiscoveryCandidate,
    seen_ms1_feature_row_ids: dict[tuple[str, str, str], int],
) -> None:
    if candidate.discovery_candidate_state not in _MS1_FEATURE_BACKED_STATES:
        return
    tag_scope = candidate.neutral_loss_tag
    key = (candidate.sample_stem, tag_scope, candidate.ms1_feature_row_id)
    previous_row = seen_ms1_feature_row_ids.get(key)
    if previous_row is not None:
        raise ValueError(
            f"{path}: row {row_number}: duplicate ms1_feature_row_id "
            f"{candidate.ms1_feature_row_id!r} in sample/tag scope "
            f"{candidate.sample_stem!r}/{tag_scope!r}; first seen at row "
            f"{previous_row}"
        )
    seen_ms1_feature_row_ids[key] = row_number


def _required_text(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> str:
    value = row.get(column, "")
    if value == "":
        raise ValueError(f"{path}: row {row_number}: {column} is required")
    return value


def _parse_int(path: Path, row_number: int, row: dict[str, str], column: str) -> int:
    value = row.get(column, "")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {row_number}: {column} must be an integer: {value!r}"
        ) from exc


def _batch_nonnegative_int(
    path: Path,
    row_number: int,
    value: object,
    *,
    field: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(
            f"{path}: row {row_number}: {field} must be a non-negative integer"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{path}: row {row_number}: {field} must be a non-negative integer"
        ) from exc
    if parsed < 0:
        raise ValueError(
            f"{path}: row {row_number}: {field} must be a non-negative integer"
        )
    return parsed


def _parse_float(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> float:
    value = row.get(column, "")
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {row_number}: {column} must be a float: {value!r}"
        ) from exc


def _parse_optional_float(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> float | None:
    value = row.get(column, "")
    if value == "":
        return None
    return _parse_float(path, row_number, row, column)


def _parse_bool(path: Path, row_number: int, row: dict[str, str], column: str) -> bool:
    value = row.get(column, "")
    if value == "TRUE":
        return True
    if value == "FALSE":
        return False
    raise ValueError(
        f"{path}: row {row_number}: {column} must be TRUE or FALSE: {value!r}"
    )


def _parse_int_tuple(
    path: Path,
    row_number: int,
    row: dict[str, str],
    column: str,
) -> tuple[int, ...]:
    value = row.get(column, "")
    if value == "":
        return ()
    try:
        return tuple(int(item) for item in value.split(";") if item)
    except ValueError as exc:
        raise ValueError(
            f"{path}: row {row_number}: {column} must be semicolon-separated integers"
        ) from exc


def _parse_text_tuple(row: dict[str, str], column: str) -> tuple[str, ...]:
    value = row.get(column, "")
    if value == "":
        return ()
    return tuple(item for item in value.split(";") if item)
