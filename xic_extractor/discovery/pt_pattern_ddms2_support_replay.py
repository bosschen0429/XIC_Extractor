"""Fullscan-seeded DDMS2 support replay for Pt pattern Discovery.

This module compares Fullscan-accepted Pt pattern seeds against a DDMS2 local
MS1 feature table. DDMS2 evidence is support-only here: without the historical
target/inclusion list, missing DDMS2 support is not negative evidence against a
Fullscan Discovery seed.
"""

from __future__ import annotations

import bisect
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN
from xic_extractor.discovery.detected_features import (
    DetectedFeature,
    load_detected_features_csv,
)
from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS,
    validate_pt_candidate_evidence_envelope,
)
from xic_extractor.tabular_io import (
    file_sha256,
    read_delimited_rows,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_ddms2_support_replay.v1"
READINESS_TIER = "diagnostic_only"
AUTHORITY_SCOPE = "fullscan_seeded_ddms2_support_only"
DEFAULT_MZ_TOLERANCE_PPM = 20.0
DEFAULT_RT_WINDOW_SEC = 120.0
DEFAULT_SEED_TRACE_RULE_CALL = "accept"

_BASE_LABEL = "base"
_POSITION_LABELS = (_BASE_LABEL, "pt_plus_1", "pt_plus_2", "pt_plus_4")
_DELTA_BY_LABEL = {
    position.label: position.delta_da for position in PT_PATTERN.positions
}
_REQUIRED_FULL_PATTERN_POSITION_COUNT = len(_POSITION_LABELS)
_REQUIRED_FULL_PATTERN_NON_BASE_POSITION_COUNT = (
    _REQUIRED_FULL_PATTERN_POSITION_COUNT - 1
)

FULLSCAN_CANDIDATE_REQUIRED_COLUMNS = (
    *PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    "candidate_state",
)
FULLSCAN_EVIDENCE_REQUIRED_COLUMNS = PT_PATTERN_EVIDENCE_CONTRACT_COLUMNS

REPLAY_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "authority_scope",
    "fullscan_sample_stem",
    "fullscan_candidate_id",
    "fullscan_base_feature_id",
    "fullscan_base_mz",
    "fullscan_base_rt_apex_min",
    "fullscan_trace_rule_call",
    "fullscan_trace_detection_status",
    "ddms2_sample_stem",
    "mz_tolerance_ppm",
    "rt_window_sec",
    "ddms2_ms1_support_status",
    "ddms2_position_count_supported",
    "ddms2_non_base_positions_supported",
    "ddms2_required_position_count",
    "ddms2_targeting_status",
    "ddms2_ms2_support_status",
    "changes_matrix_authority",
    "changes_counted_detection_authority",
    "changes_export_authority",
    "reason",
    "base_ddms2_feature_status",
    "base_ddms2_feature_id",
    "base_ddms2_mz",
    "base_ddms2_expected_mz",
    "base_ddms2_mz_error_ppm",
    "base_ddms2_rt_apex_min",
    "base_ddms2_rt_delta_sec_to_fullscan_base",
    "base_ddms2_height",
    "base_ddms2_area",
    "base_ddms2_trace_quality",
    "base_ddms2_source",
    "pt_plus_1_ddms2_feature_status",
    "pt_plus_1_ddms2_feature_id",
    "pt_plus_1_ddms2_mz",
    "pt_plus_1_ddms2_expected_mz",
    "pt_plus_1_ddms2_mz_error_ppm",
    "pt_plus_1_ddms2_rt_apex_min",
    "pt_plus_1_ddms2_rt_delta_sec_to_fullscan_base",
    "pt_plus_1_ddms2_height",
    "pt_plus_1_ddms2_area",
    "pt_plus_1_ddms2_trace_quality",
    "pt_plus_1_ddms2_source",
    "pt_plus_2_ddms2_feature_status",
    "pt_plus_2_ddms2_feature_id",
    "pt_plus_2_ddms2_mz",
    "pt_plus_2_ddms2_expected_mz",
    "pt_plus_2_ddms2_mz_error_ppm",
    "pt_plus_2_ddms2_rt_apex_min",
    "pt_plus_2_ddms2_rt_delta_sec_to_fullscan_base",
    "pt_plus_2_ddms2_height",
    "pt_plus_2_ddms2_area",
    "pt_plus_2_ddms2_trace_quality",
    "pt_plus_2_ddms2_source",
    "pt_plus_4_ddms2_feature_status",
    "pt_plus_4_ddms2_feature_id",
    "pt_plus_4_ddms2_mz",
    "pt_plus_4_ddms2_expected_mz",
    "pt_plus_4_ddms2_mz_error_ppm",
    "pt_plus_4_ddms2_rt_apex_min",
    "pt_plus_4_ddms2_rt_delta_sec_to_fullscan_base",
    "pt_plus_4_ddms2_height",
    "pt_plus_4_ddms2_area",
    "pt_plus_4_ddms2_trace_quality",
    "pt_plus_4_ddms2_source",
)


@dataclass(frozen=True)
class FullscanPtSeed:
    sample_stem: str
    candidate_id: str
    base_feature_id: str
    base_mz: float
    base_rt_apex_min: float
    trace_rule_call: str
    trace_detection_status: str


@dataclass(frozen=True)
class PositionSupportMatch:
    position_label: str
    expected_mz: float
    feature: DetectedFeature
    mz_error_ppm: float
    rt_delta_sec_to_fullscan_base: float


@dataclass(frozen=True)
class Ddms2SupportReplayResult:
    rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def run_ddms2_support_replay(
    *,
    fullscan_candidates_csv: Path,
    fullscan_evidence_csv: Path,
    ddms2_detected_features_csv: Path,
    ddms2_sample_stem: str | None = None,
    mz_tolerance_ppm: float = DEFAULT_MZ_TOLERANCE_PPM,
    rt_window_sec: float = DEFAULT_RT_WINDOW_SEC,
    seed_trace_rule_call: str = DEFAULT_SEED_TRACE_RULE_CALL,
) -> Ddms2SupportReplayResult:
    """Replay DDMS2 local MS1 support for Fullscan-accepted Pt seeds."""

    if not math.isfinite(mz_tolerance_ppm) or mz_tolerance_ppm <= 0:
        raise ValueError("mz_tolerance_ppm must be > 0 and finite")
    if not math.isfinite(rt_window_sec) or rt_window_sec <= 0:
        raise ValueError("rt_window_sec must be > 0 and finite")
    normalized_seed_trace_rule_call = _normalize_seed_trace_rule_call(
        seed_trace_rule_call
    )

    seeds = load_fullscan_pt_seeds(
        candidates_csv=fullscan_candidates_csv,
        evidence_csv=fullscan_evidence_csv,
        seed_trace_rule_call=normalized_seed_trace_rule_call,
    )
    ddms2_features = load_detected_features_csv(ddms2_detected_features_csv)
    selected_sample_stem = _resolve_ddms2_sample_stem(
        ddms2_features,
        requested_sample_stem=ddms2_sample_stem,
    )
    index = _FeatureIndex(
        feature
        for feature in ddms2_features
        if feature.sample_stem == selected_sample_stem
    )

    rows = tuple(
        _replay_seed(
            seed,
            ddms2_sample_stem=selected_sample_stem,
            feature_index=index,
            mz_tolerance_ppm=mz_tolerance_ppm,
            rt_window_sec=rt_window_sec,
        )
        for seed in seeds
    )
    summary = build_ddms2_support_summary(
        rows=rows,
        seed_count=len(seeds),
        ddms2_feature_count=len(index.features),
        fullscan_candidates_csv=fullscan_candidates_csv,
        fullscan_evidence_csv=fullscan_evidence_csv,
        ddms2_detected_features_csv=ddms2_detected_features_csv,
        ddms2_sample_stem=selected_sample_stem,
        mz_tolerance_ppm=mz_tolerance_ppm,
        rt_window_sec=rt_window_sec,
        seed_trace_rule_call=normalized_seed_trace_rule_call,
    )
    return Ddms2SupportReplayResult(rows=rows, summary=summary)


def write_ddms2_support_replay_outputs(
    result: Ddms2SupportReplayResult,
    *,
    output_dir: Path,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_csv = output_dir / "fullscan_seeded_ddms2_support_replay.csv"
    summary_json = output_dir / "fullscan_seeded_ddms2_support_replay_summary.json"
    write_delimited_rows(rows_csv, result.rows, REPLAY_COLUMNS)
    summary_json.write_text(
        json.dumps(result.summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"rows_csv": rows_csv, "summary_json": summary_json}


def load_fullscan_pt_seeds(
    *,
    candidates_csv: Path,
    evidence_csv: Path,
    seed_trace_rule_call: str = DEFAULT_SEED_TRACE_RULE_CALL,
) -> tuple[FullscanPtSeed, ...]:
    normalized_seed_trace_rule_call = _normalize_seed_trace_rule_call(
        seed_trace_rule_call
    )
    candidate_rows = _read_csv_required(
        candidates_csv,
        required_columns=FULLSCAN_CANDIDATE_REQUIRED_COLUMNS,
    )
    evidence_rows = _read_csv_required(
        evidence_csv,
        required_columns=FULLSCAN_EVIDENCE_REQUIRED_COLUMNS,
    )
    candidate_by_id = _unique_rows_by_candidate_id(
        candidate_rows,
        path=candidates_csv,
        label="candidate",
    )
    evidence_by_candidate_id = _unique_rows_by_candidate_id(
        evidence_rows,
        path=evidence_csv,
        label="evidence",
    )
    validate_pt_candidate_evidence_envelope(
        candidate_by_id,
        evidence_by_candidate_id,
        evidence_rows=evidence_rows,
    )

    seeds: list[FullscanPtSeed] = []
    for row_index, candidate_row in enumerate(candidate_rows, start=2):
        candidate_id = candidate_row["candidate_id"].strip()
        evidence_row = evidence_by_candidate_id.get(candidate_id)
        if evidence_row is None:
            raise ValueError(
                f"{candidates_csv}:{row_index}: missing evidence row for "
                f"candidate_id={candidate_id!r}"
            )
        trace_rule_call = evidence_row["trace_rule_call"].strip()
        if trace_rule_call != normalized_seed_trace_rule_call:
            continue
        seeds.append(
            FullscanPtSeed(
                sample_stem=candidate_row["sample_stem"].strip(),
                candidate_id=candidate_id,
                base_feature_id=candidate_row["base_feature_id"].strip(),
                base_mz=_parse_float(
                    candidate_row["base_mz"],
                    path=candidates_csv,
                    row_index=row_index,
                    column="base_mz",
                ),
                base_rt_apex_min=_parse_float(
                    candidate_row["base_rt_apex_min"],
                    path=candidates_csv,
                    row_index=row_index,
                    column="base_rt_apex_min",
                ),
                trace_rule_call=trace_rule_call,
                trace_detection_status=evidence_row[
                    "trace_detection_status"
                ].strip(),
            )
        )
    return tuple(seeds)


def build_ddms2_support_summary(
    *,
    rows: Sequence[Mapping[str, Any]],
    seed_count: int,
    ddms2_feature_count: int,
    fullscan_candidates_csv: Path,
    fullscan_evidence_csv: Path,
    ddms2_detected_features_csv: Path,
    ddms2_sample_stem: str,
    mz_tolerance_ppm: float,
    rt_window_sec: float,
    seed_trace_rule_call: str,
) -> dict[str, Any]:
    support_counter = Counter(
        str(row["ddms2_ms1_support_status"]) for row in rows
    )
    reason_counter = Counter(str(row["reason"]) for row in rows)
    position_support_counts = {
        label: sum(
            1
            for row in rows
            if row[f"{label}_ddms2_feature_status"] == "feature_supported"
        )
        for label in _POSITION_LABELS
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "authority_scope": AUTHORITY_SCOPE,
        "fullscan_seed_filter": {
            "trace_rule_call": seed_trace_rule_call,
        },
        "seed_count": seed_count,
        "output_row_count": len(rows),
        "ddms2_sample_stem": ddms2_sample_stem,
        "ddms2_detected_feature_count": ddms2_feature_count,
        "mz_tolerance_ppm": mz_tolerance_ppm,
        "rt_window_sec": rt_window_sec,
        "ddms2_ms1_support_status_counts": dict(sorted(support_counter.items())),
        "reason_counts": dict(sorted(reason_counter.items())),
        "position_support_counts": position_support_counts,
        "targeting_status": "unknown_target_list_missing",
        "ddms2_ms2_support_status": "not_evaluable_without_target_list",
        "changes_matrix_authority": False,
        "changes_counted_detection_authority": False,
        "changes_export_authority": False,
        "interpretation": (
            "DDMS2 feature-table support is diagnostic support for Fullscan "
            "accepted seeds. Missing DDMS2/MS2 support is not rejection while "
            "the DDMS2 target list is unavailable."
        ),
        "inputs": {
            "fullscan_candidates_csv": str(fullscan_candidates_csv),
            "fullscan_candidates_sha256": file_sha256(
                fullscan_candidates_csv,
                uppercase=False,
            ),
            "fullscan_evidence_csv": str(fullscan_evidence_csv),
            "fullscan_evidence_sha256": file_sha256(
                fullscan_evidence_csv,
                uppercase=False,
            ),
            "ddms2_detected_features_csv": str(ddms2_detected_features_csv),
            "ddms2_detected_features_sha256": file_sha256(
                ddms2_detected_features_csv,
                uppercase=False,
            ),
        },
    }


class _FeatureIndex:
    def __init__(self, features: Iterable[DetectedFeature]) -> None:
        self.features = tuple(sorted(features, key=lambda feature: feature.mz))
        self.mzs = tuple(feature.mz for feature in self.features)

    def best_match(
        self,
        *,
        expected_mz: float,
        base_rt_apex_min: float,
        position_label: str,
        mz_tolerance_ppm: float,
        rt_window_sec: float,
    ) -> PositionSupportMatch | None:
        tolerance_da = expected_mz * mz_tolerance_ppm * 1e-6
        left = bisect.bisect_left(self.mzs, expected_mz - tolerance_da)
        right = bisect.bisect_right(self.mzs, expected_mz + tolerance_da)
        best: tuple[tuple[float, float, float, str], PositionSupportMatch] | None = None
        for feature in self.features[left:right]:
            rt_delta_sec = (feature.rt_apex_min - base_rt_apex_min) * 60.0
            if abs(rt_delta_sec) > rt_window_sec:
                continue
            mz_error_ppm = (feature.mz - expected_mz) / expected_mz * 1e6
            key = (
                abs(mz_error_ppm),
                abs(rt_delta_sec),
                -feature.height,
                feature.feature_id,
            )
            match = PositionSupportMatch(
                position_label=position_label,
                expected_mz=expected_mz,
                feature=feature,
                mz_error_ppm=mz_error_ppm,
                rt_delta_sec_to_fullscan_base=rt_delta_sec,
            )
            if best is None or key < best[0]:
                best = (key, match)
        return best[1] if best is not None else None


def _replay_seed(
    seed: FullscanPtSeed,
    *,
    ddms2_sample_stem: str,
    feature_index: _FeatureIndex,
    mz_tolerance_ppm: float,
    rt_window_sec: float,
) -> dict[str, Any]:
    matches = {
        label: feature_index.best_match(
            expected_mz=seed.base_mz + _DELTA_BY_LABEL[label],
            base_rt_apex_min=seed.base_rt_apex_min,
            position_label=label,
            mz_tolerance_ppm=mz_tolerance_ppm,
            rt_window_sec=rt_window_sec,
        )
        for label in _POSITION_LABELS
    }
    position_count = sum(match is not None for match in matches.values())
    non_base_count = sum(
        match is not None
        for label, match in matches.items()
        if label != _BASE_LABEL
    )
    status, reason = _classify_support(
        has_base=matches[_BASE_LABEL] is not None,
        non_base_count=non_base_count,
    )
    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "authority_scope": AUTHORITY_SCOPE,
        "fullscan_sample_stem": seed.sample_stem,
        "fullscan_candidate_id": seed.candidate_id,
        "fullscan_base_feature_id": seed.base_feature_id,
        "fullscan_base_mz": seed.base_mz,
        "fullscan_base_rt_apex_min": seed.base_rt_apex_min,
        "fullscan_trace_rule_call": seed.trace_rule_call,
        "fullscan_trace_detection_status": seed.trace_detection_status,
        "ddms2_sample_stem": ddms2_sample_stem,
        "mz_tolerance_ppm": mz_tolerance_ppm,
        "rt_window_sec": rt_window_sec,
        "ddms2_ms1_support_status": status,
        "ddms2_position_count_supported": position_count,
        "ddms2_non_base_positions_supported": non_base_count,
        "ddms2_required_position_count": _REQUIRED_FULL_PATTERN_POSITION_COUNT,
        "ddms2_targeting_status": "unknown_target_list_missing",
        "ddms2_ms2_support_status": "not_evaluable_without_target_list",
        "changes_matrix_authority": False,
        "changes_counted_detection_authority": False,
        "changes_export_authority": False,
        "reason": reason,
    }
    for label in _POSITION_LABELS:
        _add_position_columns(
            row,
            label=label,
            match=matches[label],
            expected_mz=seed.base_mz + _DELTA_BY_LABEL[label],
        )
    return row


def _classify_support(
    *,
    has_base: bool,
    non_base_count: int,
) -> tuple[str, str]:
    if has_base and non_base_count == _REQUIRED_FULL_PATTERN_NON_BASE_POSITION_COUNT:
        return (
            "full_pattern_feature_supported",
            "ddms2_ms1_feature_table_supports_all_fullscan_pt_positions",
        )
    if has_base and non_base_count >= 2:
        return (
            "partial_pattern_feature_supported",
            "ddms2_ms1_feature_table_supports_base_and_multiple_non_base_positions",
        )
    if has_base and non_base_count > 0:
        return (
            "weak_partial_feature_supported",
            "ddms2_ms1_feature_table_supports_base_but_not_enough_non_base_positions",
        )
    if has_base:
        return (
            "base_only_feature_supported",
            "ddms2_ms1_feature_table_supports_base_only",
        )
    return (
        "missing_base_feature_support",
        "ddms2_base_feature_missing_within_mz_rt_window",
    )


def _add_position_columns(
    row: dict[str, Any],
    *,
    label: str,
    match: PositionSupportMatch | None,
    expected_mz: float,
) -> None:
    prefix = f"{label}_ddms2"
    row[f"{prefix}_expected_mz"] = expected_mz
    if match is None:
        row[f"{prefix}_feature_status"] = "missing"
        row[f"{prefix}_feature_id"] = ""
        row[f"{prefix}_mz"] = ""
        row[f"{prefix}_mz_error_ppm"] = ""
        row[f"{prefix}_rt_apex_min"] = ""
        row[f"{prefix}_rt_delta_sec_to_fullscan_base"] = ""
        row[f"{prefix}_height"] = ""
        row[f"{prefix}_area"] = ""
        row[f"{prefix}_trace_quality"] = ""
        row[f"{prefix}_source"] = ""
        return

    feature = match.feature
    row[f"{prefix}_feature_status"] = "feature_supported"
    row[f"{prefix}_feature_id"] = feature.feature_id
    row[f"{prefix}_mz"] = feature.mz
    row[f"{prefix}_mz_error_ppm"] = match.mz_error_ppm
    row[f"{prefix}_rt_apex_min"] = feature.rt_apex_min
    row[f"{prefix}_rt_delta_sec_to_fullscan_base"] = (
        match.rt_delta_sec_to_fullscan_base
    )
    row[f"{prefix}_height"] = feature.height
    row[f"{prefix}_area"] = "" if feature.area is None else feature.area
    row[f"{prefix}_trace_quality"] = feature.trace_quality
    row[f"{prefix}_source"] = feature.source


def _resolve_ddms2_sample_stem(
    features: Sequence[DetectedFeature],
    *,
    requested_sample_stem: str | None,
) -> str:
    sample_stems = sorted({feature.sample_stem for feature in features})
    if not sample_stems:
        raise ValueError("ddms2_detected_features_csv has no feature rows")
    if requested_sample_stem is not None:
        if requested_sample_stem not in sample_stems:
            raise ValueError(
                f"requested ddms2_sample_stem={requested_sample_stem!r} "
                f"not found; available sample_stem values: {sample_stems}"
            )
        return requested_sample_stem
    if len(sample_stems) != 1:
        raise ValueError(
            "ddms2_sample_stem is required when the DDMS2 feature table "
            f"contains multiple samples: {sample_stems}"
        )
    return sample_stems[0]


def _read_csv_required(
    path: Path,
    *,
    required_columns: Sequence[str],
) -> list[dict[str, str]]:
    return read_delimited_rows(
        path,
        required_columns=required_columns,
        delimiter=",",
        encoding="utf-8-sig",
    )


def _unique_rows_by_candidate_id(
    rows: Sequence[Mapping[str, str]],
    *,
    path: Path,
    label: str,
) -> dict[str, Mapping[str, str]]:
    by_candidate_id: dict[str, Mapping[str, str]] = {}
    for row_index, row in enumerate(rows, start=2):
        candidate_id = str(row.get("candidate_id", "")).strip()
        if not candidate_id:
            raise ValueError(
                f"{path}:{row_index}: {label} candidate_id must be non-empty"
            )
        if candidate_id in by_candidate_id:
            raise ValueError(
                f"{path}:{row_index}: duplicate {label} candidate_id: "
                f"{candidate_id}"
            )
        by_candidate_id[candidate_id] = row
    return by_candidate_id


def _normalize_seed_trace_rule_call(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("seed_trace_rule_call must be non-empty")
    return value.strip()


def _parse_float(
    value: str,
    *,
    path: Path,
    row_index: int,
    column: str,
) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(
            f"{path}:{row_index}: column {column} must be numeric"
        ) from exc
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise ValueError(f"{path}:{row_index}: column {column} must be finite")
    return parsed
