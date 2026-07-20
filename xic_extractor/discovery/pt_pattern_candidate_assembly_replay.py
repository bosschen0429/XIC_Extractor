"""Replay Pt candidate assembly against detected features without opening RAW."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from xic_extractor.discovery.delta_mass_patterns import (
    PT_PATTERN,
    DeltaMassPatternPosition,
    match_delta_mass_pattern,
)
from xic_extractor.discovery.detected_features import (
    LOCAL_MS1_FEATURE_TRACE_QUALITIES,
    LOCAL_MS1_FEATURE_TRACE_QUALITY_OK,
    DetectedFeature,
    is_local_ms1_feature_source,
    load_detected_features_csv,
)
from xic_extractor.discovery.pt_pattern_candidate_assembly_report import (
    render_candidate_assembly_replay_html,
)
from xic_extractor.mass_accuracy import mz_window, signed_ppm_error
from xic_extractor.tabular_io import (
    bool_value,
    file_sha256,
    optional_float,
    read_delimited_rows,
    text_value,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_candidate_assembly_replay.v1"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_candidate_assembly_replay"
AUTHORITY_SCOPE = "candidate_assembly_diagnosis_only"
RT_THRESHOLDS_SEC = (30, 45, 60, 90, 180, 300)
_NON_BASE_POSITIONS = tuple(
    position for position in PT_PATTERN.positions if position.label != "base"
)
_PT_PLUS1_DELTA_DA = next(
    position.delta_da
    for position in PT_PATTERN.positions
    if position.label == "pt_plus_1"
)
_TOP102_REQUIRED_COLUMNS = (
    "query_set",
    "compound",
    "list_plus1_mz",
    "expected_base_mz",
    "pipeline_status",
    "coelution_root_layer",
    "feature_base_count_20ppm",
    "feature_plus1_count_20ppm",
    "feature_plus2_count_20ppm",
    "feature_plus4_count_20ppm",
    "coherent_full_pattern_exists",
)
_KNOWN_REQUIRED_COLUMNS = (
    "compound",
    "list_plus1_mz",
    "expected_base_mz",
    "chosen_candidate_id",
    "feature_base_nearest_abs_ppm",
    "feature_plus1_nearest_abs_ppm",
    "feature_plus2_nearest_abs_ppm",
    "feature_plus4_nearest_abs_ppm",
)
REPLAY_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "report_id",
    "source_set",
    "compound",
    "list_plus1_mz",
    "expected_base_mz",
    "sample_stem",
    "target_window_group_id",
    "target_window_group_size",
    "replay_signature_id",
    "base_pool_count",
    "eligible_base_pool_count",
    "complete_mass_assembly_count",
    "classification",
    "first_break_explanation",
    "missing_mass_positions",
    "limiting_position",
    "best_max_abs_rt_delta_sec",
    "best_rt_span_sec",
    "best_base_feature_id",
    "best_base_mz",
    "best_base_rt_min",
    "best_pt_plus_1_feature_id",
    "best_pt_plus_1_mz",
    "best_pt_plus_1_rt_min",
    "best_pt_plus_1_abs_ppm",
    "best_pt_plus_1_rt_delta_sec",
    "best_pt_plus_2_feature_id",
    "best_pt_plus_2_mz",
    "best_pt_plus_2_rt_min",
    "best_pt_plus_2_abs_ppm",
    "best_pt_plus_2_rt_delta_sec",
    "best_pt_plus_4_feature_id",
    "best_pt_plus_4_mz",
    "best_pt_plus_4_rt_min",
    "best_pt_plus_4_abs_ppm",
    "best_pt_plus_4_rt_delta_sec",
    "recovered_at_30_sec",
    "recovered_at_45_sec",
    "recovered_at_60_sec",
    "recovered_at_90_sec",
    "recovered_at_180_sec",
    "recovered_at_300_sec",
)


@dataclass(frozen=True)
class _MassMatch:
    feature: DetectedFeature
    abs_ppm: float
    rt_delta_sec: float


@dataclass(frozen=True)
class _Assembly:
    base: DetectedFeature
    matches: Mapping[str, _MassMatch]

    @property
    def max_abs_rt_delta_sec(self) -> float:
        return max(match.rt_delta_sec for match in self.matches.values())

    @property
    def rt_span_sec(self) -> float:
        retention_times = [
            self.base.rt_apex_min,
            *(match.feature.rt_apex_min for match in self.matches.values()),
        ]
        return (max(retention_times) - min(retention_times)) * 60.0

    @property
    def limiting_position(self) -> str:
        return max(
            self.matches.items(),
            key=lambda item: (item[1].rt_delta_sec, item[0]),
        )[0]


def run_candidate_assembly_replay(
    *,
    detected_features_csv: Path,
    top102_diagnosis_csv: Path,
    known_diagnosis_csv: Path,
    output_dir: Path,
    report_id: str,
) -> dict[str, Path]:
    """Classify the first assembly break for rows with four list-relative pools."""

    if not report_id.strip():
        raise ValueError("report_id must be non-empty")
    features = load_detected_features_csv(detected_features_csv)
    if not features:
        raise ValueError("detected_features_csv has no feature rows")
    samples = {feature.sample_stem for feature in features}
    if len(samples) != 1:
        raise ValueError("candidate assembly replay requires exactly one sample")
    if any(not is_local_ms1_feature_source(feature.source) for feature in features):
        raise ValueError(
            "candidate assembly replay currently requires local MS1 features"
        )

    targets = _load_targets(top102_diagnosis_csv, known_diagnosis_csv)
    _assign_target_window_groups(targets)
    sorted_features = sorted(features, key=lambda feature: feature.mz)
    sorted_mzs = [feature.mz for feature in sorted_features]
    emitted_base_ids = {
        candidate.base_feature_id
        for candidate in match_delta_mass_pattern(sorted_features)
    }
    sample_stem = next(iter(samples))
    rows = [
        _replay_target(
            target,
            sample_stem=sample_stem,
            sorted_features=sorted_features,
            sorted_mzs=sorted_mzs,
            emitted_base_ids=emitted_base_ids,
            report_id=report_id,
        )
        for target in targets
    ]

    unique_rows_by_signature: dict[str, dict[str, object]] = {}
    for row in rows:
        unique_rows_by_signature.setdefault(text_value(row["replay_signature_id"]), row)
    unique_rows = list(unique_rows_by_signature.values())
    classification_counts = Counter(text_value(row["classification"]) for row in rows)
    unique_classification_counts = Counter(
        text_value(row["classification"]) for row in unique_rows
    )
    limiting_counts = Counter(
        text_value(row["limiting_position"])
        for row in rows
        if text_value(row["limiting_position"])
    )
    unique_limiting_counts = Counter(
        text_value(row["limiting_position"])
        for row in unique_rows
        if text_value(row["limiting_position"])
    )
    threshold_counts = {
        str(threshold): sum(bool(row[f"recovered_at_{threshold}_sec"]) for row in rows)
        for threshold in RT_THRESHOLDS_SEC
    }
    unique_threshold_counts = {
        str(threshold): sum(
            bool(row[f"recovered_at_{threshold}_sec"]) for row in unique_rows
        )
        for threshold in RT_THRESHOLDS_SEC
    }
    target_groups = {
        text_value(row["target_window_group_id"])
        for row in rows
        if text_value(row["target_window_group_id"])
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "report_id": report_id,
        "opens_raw": False,
        "changes_detector_rule": False,
        "changes_counted_detection": False,
        "changes_matrix": False,
        "changes_export": False,
        "feature_row_count": len(features),
        "sample_stem": sample_stem,
        "row_count": len(rows),
        "unique_replay_signature_count": len(unique_rows),
        "unique_target_window_count": len(target_groups),
        "duplicate_target_row_count": sum(
            1 for row in rows if _required_float(row["target_window_group_size"]) > 1
        ),
        "mass_tolerance_ppm": PT_PATTERN.mz_tolerance_ppm,
        "production_rt_tolerance_sec": PT_PATTERN.rt_tolerance_sec,
        "classification_counts": dict(sorted(classification_counts.items())),
        "unique_classification_counts": dict(
            sorted(unique_classification_counts.items())
        ),
        "threshold_recovery_counts": threshold_counts,
        "unique_threshold_recovery_counts": unique_threshold_counts,
        "limiting_position_counts": dict(sorted(limiting_counts.items())),
        "unique_limiting_position_counts": dict(sorted(unique_limiting_counts.items())),
        "inputs": {
            "detected_features_csv": _provenance(detected_features_csv),
            "top102_diagnosis_csv": _provenance(top102_diagnosis_csv),
            "known_diagnosis_csv": _provenance(known_diagnosis_csv),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    replay_csv = output_dir / "pt_pattern_candidate_assembly_replay.csv"
    summary_json = output_dir / "pt_pattern_candidate_assembly_replay_summary.json"
    html = output_dir / "pt_pattern_candidate_assembly_replay.html"
    write_delimited_rows(replay_csv, rows, REPLAY_COLUMNS)
    summary_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    html.write_text(
        render_candidate_assembly_replay_html(summary=summary, rows=rows),
        encoding="utf-8",
    )
    return {"replay_csv": replay_csv, "summary_json": summary_json, "html": html}


def _load_targets(top102_path: Path, known_path: Path) -> list[dict[str, object]]:
    top102_rows = read_delimited_rows(
        top102_path,
        required_columns=_TOP102_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    known_rows = read_delimited_rows(
        known_path,
        required_columns=_KNOWN_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    targets: list[dict[str, object]] = []
    for row_index, row in enumerate(top102_rows, start=2):
        row_label = f"{top102_path} row {row_index}"
        _required_text(row, "query_set", label=row_label)
        _validate_target_identity(row, label=row_label)
        position_counts = tuple(
            _required_nonnegative_int(
                row,
                f"feature_{position}_count_20ppm",
                label=row_label,
            )
            for position in ("base", "plus1", "plus2", "plus4")
        )
        all_four_exist = all(count > 0 for count in position_counts)
        coherent = _required_boolean(
            row,
            "coherent_full_pattern_exists",
            label=row_label,
        )
        if all_four_exist and not coherent:
            targets.append(_target_row(row, source_set=text_value(row["query_set"])))
    for row_index, row in enumerate(known_rows, start=2):
        row_label = f"{known_path} row {row_index}"
        _validate_target_identity(row, label=row_label)
        nearest_ppm = [
            _optional_nonnegative_number(
                row,
                f"feature_{position}_nearest_abs_ppm",
                label=row_label,
            )
            for position in ("base", "plus1", "plus2", "plus4")
        ]
        all_four_exist = all(
            value is not None and value <= PT_PATTERN.mz_tolerance_ppm
            for value in nearest_ppm
        )
        if all_four_exist and not text_value(row["chosen_candidate_id"]):
            targets.append(_target_row(row, source_set="manual_known"))
    return targets


def _target_row(row: Mapping[str, object], *, source_set: str) -> dict[str, object]:
    return {
        "source_set": source_set,
        "compound": _required_text(row, "compound", label="target row"),
        "list_plus1_mz": _required_positive_number(
            row,
            "list_plus1_mz",
            label="target row",
        ),
        "expected_base_mz": _required_positive_number(
            row,
            "expected_base_mz",
            label="target row",
        ),
    }


def _assign_target_window_groups(targets: list[dict[str, object]]) -> None:
    unvisited = set(range(len(targets)))
    components: list[list[int]] = []
    while unvisited:
        root = min(unvisited)
        unvisited.remove(root)
        component = [root]
        frontier = [root]
        while frontier:
            current = frontier.pop()
            current_mz = _required_float(targets[current]["list_plus1_mz"])
            neighbours = [
                index
                for index in sorted(unvisited)
                if _pair_within_ppm(
                    current_mz,
                    _required_float(targets[index]["list_plus1_mz"]),
                    PT_PATTERN.mz_tolerance_ppm,
                )
            ]
            for index in neighbours:
                unvisited.remove(index)
                component.append(index)
                frontier.append(index)
        components.append(component)
    components.sort(
        key=lambda component: min(
            _required_float(targets[index]["list_plus1_mz"]) for index in component
        )
    )
    for group_number, component in enumerate(components, start=1):
        for index in component:
            targets[index]["target_window_group_id"] = (
                f"target_window_{group_number:03d}"
            )
            targets[index]["target_window_group_size"] = len(component)


def _pair_within_ppm(left_mz: float, right_mz: float, tolerance_ppm: float) -> bool:
    denominator = (left_mz + right_mz) / 2.0
    return abs(left_mz - right_mz) / denominator * 1e6 <= tolerance_ppm


def _replay_target(
    target: Mapping[str, object],
    *,
    sample_stem: str,
    sorted_features: Sequence[DetectedFeature],
    sorted_mzs: Sequence[float],
    emitted_base_ids: set[str],
    report_id: str,
) -> dict[str, object]:
    expected_base_mz = _required_float(target["expected_base_mz"])
    base_pool = _features_in_window(
        sorted_features,
        sorted_mzs,
        expected_base_mz,
        PT_PATTERN.mz_tolerance_ppm,
    )
    eligible_bases = [
        feature
        for feature in base_pool
        if feature.trace_quality == LOCAL_MS1_FEATURE_TRACE_QUALITY_OK
    ]
    assemblies = [
        assembly
        for base in eligible_bases
        if (
            assembly := _mass_complete_assembly(
                base,
                sorted_features=sorted_features,
                sorted_mzs=sorted_mzs,
            )
        )
        is not None
    ]
    raw_base_assemblies = [
        assembly
        for base in base_pool
        if (
            assembly := _mass_complete_assembly(
                base,
                sorted_features=sorted_features,
                sorted_mzs=sorted_mzs,
            )
        )
        is not None
    ]
    best = min(assemblies, key=_assembly_sort_key) if assemblies else None
    has_emitted_base = any(
        base.feature_id in emitted_base_ids for base in eligible_bases
    )
    if has_emitted_base:
        classification = "unexpected_emittable"
        explanation = (
            "Canonical matcher emits a candidate from this base window; "
            "reconcile the upstream no-candidate label."
        )
    elif not assemblies and raw_base_assemblies:
        classification = "feature_quality_blocked"
        explanation = "A complete mass assembly exists only with a non-OK base."
        best = min(raw_base_assemblies, key=_assembly_sort_key)
    elif not assemblies:
        classification = "mass_incoherent"
        explanation = (
            "Four list-relative pools exist, but no observed base has all three "
            "base-relative mass edges within 20 ppm."
        )
    else:
        classification = "rt_incoherent"
        explanation = (
            "A complete base-relative mass assembly exists, but its positions "
            "do not fit the production RT gate."
        )

    row: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "report_id": report_id,
        "source_set": target["source_set"],
        "compound": target["compound"],
        "list_plus1_mz": target["list_plus1_mz"],
        "expected_base_mz": expected_base_mz,
        "sample_stem": sample_stem,
        "target_window_group_id": target["target_window_group_id"],
        "target_window_group_size": target["target_window_group_size"],
        "replay_signature_id": _replay_signature_id(
            target=target, classification=classification, assembly=best
        ),
        "base_pool_count": len(base_pool),
        "eligible_base_pool_count": len(eligible_bases),
        "complete_mass_assembly_count": len(assemblies),
        "classification": classification,
        "first_break_explanation": explanation,
        "missing_mass_positions": (
            _missing_mass_positions(
                eligible_bases,
                sorted_features=sorted_features,
                sorted_mzs=sorted_mzs,
            )
            if classification == "mass_incoherent"
            else ""
        ),
        "limiting_position": best.limiting_position if best is not None else "",
        "best_max_abs_rt_delta_sec": (
            best.max_abs_rt_delta_sec if best is not None else None
        ),
        "best_rt_span_sec": best.rt_span_sec if best is not None else None,
    }
    row.update(_assembly_fields(best))
    rt_recoverable = classification in {"rt_incoherent", "unexpected_emittable"}
    for threshold in RT_THRESHOLDS_SEC:
        row[f"recovered_at_{threshold}_sec"] = bool(
            rt_recoverable
            and best is not None
            and best.max_abs_rt_delta_sec <= threshold
            and best.rt_span_sec <= threshold
        )
    return row


def _mass_complete_assembly(
    base: DetectedFeature,
    *,
    sorted_features: Sequence[DetectedFeature],
    sorted_mzs: Sequence[float],
) -> _Assembly | None:
    matches: dict[str, _MassMatch] = {}
    for position in _NON_BASE_POSITIONS:
        match = _best_mass_match(
            base,
            position,
            sorted_features=sorted_features,
            sorted_mzs=sorted_mzs,
        )
        if match is None:
            return None
        matches[position.label] = match
    return _Assembly(base=base, matches=matches)


def _best_mass_match(
    base: DetectedFeature,
    position: DeltaMassPatternPosition,
    *,
    sorted_features: Sequence[DetectedFeature],
    sorted_mzs: Sequence[float],
) -> _MassMatch | None:
    expected_mz = base.mz + position.delta_da
    eligible = [
        feature
        for feature in _features_in_window(
            sorted_features,
            sorted_mzs,
            expected_mz,
            PT_PATTERN.mz_tolerance_ppm,
        )
        if feature is not base
        and feature.trace_quality in LOCAL_MS1_FEATURE_TRACE_QUALITIES
    ]
    if not eligible:
        return None

    def key(feature: DetectedFeature) -> tuple[float, float, float]:
        return (
            abs(feature.rt_apex_min - base.rt_apex_min) * 60.0,
            abs(signed_ppm_error(feature.mz, expected_mz)),
            -feature.height,
        )

    feature = min(eligible, key=key)
    return _MassMatch(
        feature=feature,
        abs_ppm=abs(signed_ppm_error(feature.mz, expected_mz)),
        rt_delta_sec=abs(feature.rt_apex_min - base.rt_apex_min) * 60.0,
    )


def _features_in_window(
    sorted_features: Sequence[DetectedFeature],
    sorted_mzs: Sequence[float],
    expected_mz: float,
    tolerance_ppm: float,
) -> list[DetectedFeature]:
    lower_mz, upper_mz = mz_window(expected_mz, tolerance_ppm)
    start = bisect.bisect_left(sorted_mzs, lower_mz)
    end = bisect.bisect_right(sorted_mzs, upper_mz)
    return list(sorted_features[start:end])


def _assembly_sort_key(assembly: _Assembly) -> tuple[float, float, float, float]:
    return (
        assembly.max_abs_rt_delta_sec,
        assembly.rt_span_sec,
        sum(match.abs_ppm for match in assembly.matches.values()),
        -assembly.base.height,
    )


def _missing_mass_positions(
    bases: Sequence[DetectedFeature],
    *,
    sorted_features: Sequence[DetectedFeature],
    sorted_mzs: Sequence[float],
) -> str:
    best_key: tuple[int, float, float] | None = None
    best_matches: Mapping[str, _MassMatch] = {}
    for base in bases:
        matches = {
            position.label: match
            for position in _NON_BASE_POSITIONS
            if (
                match := _best_mass_match(
                    base,
                    position,
                    sorted_features=sorted_features,
                    sorted_mzs=sorted_mzs,
                )
            )
            is not None
        }
        key = (
            len(matches),
            -sum(match.abs_ppm for match in matches.values()),
            base.height,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_matches = matches
    return ";".join(
        position.label
        for position in _NON_BASE_POSITIONS
        if position.label not in best_matches
    )


def _replay_signature_id(
    *,
    target: Mapping[str, object],
    classification: str,
    assembly: _Assembly | None,
) -> str:
    parts: tuple[str, ...]
    if assembly is None:
        parts = (
            classification,
            text_value(target["target_window_group_id"]),
            f"{_required_float(target['expected_base_mz']):.6f}",
        )
    else:
        parts = (
            classification,
            assembly.base.feature_id,
            *(
                assembly.matches[label].feature.feature_id
                for label in ("pt_plus_1", "pt_plus_2", "pt_plus_4")
            ),
        )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"replay_{digest}"


def _assembly_fields(assembly: _Assembly | None) -> dict[str, object]:
    fields: dict[str, object] = {
        "best_base_feature_id": "",
        "best_base_mz": None,
        "best_base_rt_min": None,
    }
    for label in ("pt_plus_1", "pt_plus_2", "pt_plus_4"):
        fields.update(
            {
                f"best_{label}_feature_id": "",
                f"best_{label}_mz": None,
                f"best_{label}_rt_min": None,
                f"best_{label}_abs_ppm": None,
                f"best_{label}_rt_delta_sec": None,
            }
        )
    if assembly is None:
        return fields
    fields.update(
        {
            "best_base_feature_id": assembly.base.feature_id,
            "best_base_mz": assembly.base.mz,
            "best_base_rt_min": assembly.base.rt_apex_min,
        }
    )
    for label, match in assembly.matches.items():
        fields.update(
            {
                f"best_{label}_feature_id": match.feature.feature_id,
                f"best_{label}_mz": match.feature.mz,
                f"best_{label}_rt_min": match.feature.rt_apex_min,
                f"best_{label}_abs_ppm": match.abs_ppm,
                f"best_{label}_rt_delta_sec": match.rt_delta_sec,
            }
        )
    return fields


def _required_float(value: object) -> float:
    parsed = optional_float(value)
    if parsed is None:
        raise ValueError(f"expected finite numeric value, got {value!r}")
    return parsed


def _validate_target_identity(
    row: Mapping[str, object],
    *,
    label: str,
) -> None:
    _required_text(row, "compound", label=label)
    list_plus1_mz = _required_positive_number(
        row,
        "list_plus1_mz",
        label=label,
    )
    expected_base_mz = _required_positive_number(
        row,
        "expected_base_mz",
        label=label,
    )
    observed_delta = list_plus1_mz - expected_base_mz
    serialization_tolerance_da = _target_identity_serialization_tolerance_da(
        row.get("list_plus1_mz"),
        row.get("expected_base_mz"),
        magnitude=max(list_plus1_mz, expected_base_mz),
    )
    if not math.isclose(
        observed_delta,
        _PT_PLUS1_DELTA_DA,
        rel_tol=0.0,
        abs_tol=serialization_tolerance_da,
    ):
        raise ValueError(
            f"{label} list_plus1_mz must differ from expected_base_mz by the "
            f"Pt +1 delta ({_PT_PLUS1_DELTA_DA:g} Da); observed "
            f"{observed_delta:g} Da"
        )


def _target_identity_serialization_tolerance_da(
    *raw_values: object,
    magnitude: float,
) -> float:
    """Bound identity tolerance to the precision present in current artifacts."""

    represented_half_ulps = sum(
        _serialized_decimal_half_ulp(value) for value in raw_values
    )
    # Existing diagnosis CSVs use ``.6g``. Never infer more tolerance than the
    # combined worst-case rounding error of two six-significant-digit values.
    six_sig_combined_ulp = 10.0 ** (math.floor(math.log10(magnitude)) - 5)
    return min(represented_half_ulps, six_sig_combined_ulp) + 1e-12


def _serialized_decimal_half_ulp(value: object) -> float:
    try:
        normalized = Decimal(str(value).strip()).normalize()
    except InvalidOperation:
        return 0.0
    exponent = normalized.as_tuple().exponent
    if not isinstance(exponent, int):
        return 0.0
    if exponent >= 0:
        return 0.0
    return 0.5 * (10.0**exponent)


def _required_text(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> str:
    value = text_value(row.get(field))
    if not value:
        raise ValueError(f"{label} {field} must be non-empty")
    return value


def _required_positive_number(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> float:
    value = optional_float(row.get(field))
    if value is None or value <= 0:
        raise ValueError(f"{label} {field} must be a finite positive number")
    return value


def _optional_nonnegative_number(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> float | None:
    raw_value = row.get(field)
    if raw_value is None or not text_value(raw_value):
        return None
    value = optional_float(raw_value)
    if value is None or value < 0:
        raise ValueError(f"{label} {field} must be a finite non-negative number")
    return value


def _required_nonnegative_int(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> int:
    value = optional_float(row.get(field))
    if value is None or not value.is_integer() or value < 0:
        raise ValueError(f"{label} {field} must be a non-negative integer")
    return int(value)


def _required_boolean(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
) -> bool:
    value = bool_value(row.get(field))
    if value is None:
        raise ValueError(f"{label} {field} must be a boolean")
    return value


def _provenance(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": file_sha256(path)}
