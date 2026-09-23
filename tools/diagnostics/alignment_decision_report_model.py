"""Report model assembly for the alignment decision diagnostic report."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.diagnostics.alignment_decision_report_io import TsvTable, read_json, read_tsv

_REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "identity_decision",
    "include_in_primary_matrix",
    "present_rate",
    "detected_count",
    "accepted_backfill_count",
    "row_flags",
)
_MATRIX_REQUIRED_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
)
_MATRIX_METADATA_COLUMNS = frozenset(_MATRIX_REQUIRED_COLUMNS)
_CLEANLINESS_WARNING_FLAGS = (
    "duplicate_claim_pressure",
    "high_backfill_dependency",
    "rescue_heavy",
    "weak_seed_tolerated",
)
_CLEANLINESS_WARNING_FLAG_SET = frozenset(_CLEANLINESS_WARNING_FLAGS)


def build_report(
    *,
    alignment_dir: Path,
    targeted_istd_benchmark_json: Path | None = None,
    owner_backfill_economics_json: Path | None = None,
    timing_json: Path | None = None,
    rt_normalization_json: Path | None = None,
    known_istd_exceptions: tuple[str, ...] = (),
) -> dict[str, Any]:
    review = read_tsv(
        alignment_dir / "alignment_review.tsv",
        required_columns=_REVIEW_REQUIRED_COLUMNS,
    )
    matrix = read_tsv(
        alignment_dir / "alignment_matrix.tsv",
        required_columns=_MATRIX_REQUIRED_COLUMNS,
    )
    known = _parse_known_istd_exceptions(known_istd_exceptions)
    cleanliness = _matrix_cleanliness(review, matrix)
    istd = _istd_benchmark(targeted_istd_benchmark_json, known)
    economics = _backfill_economics(owner_backfill_economics_json)
    timing = _timing_summary(timing_json)
    rt_normalization = _rt_normalization(rt_normalization_json)
    verdict = _verdict(
        istd=istd,
        cleanliness=cleanliness,
        economics=economics,
        timing=timing,
    )
    return {
        "alignment_dir": str(alignment_dir),
        "verdict": verdict,
        "run": {
            "matrix_row_count": len(matrix.rows),
            "sample_count": _sample_count(matrix.fieldnames),
            "identity_counts": cleanliness["identity_counts"],
            "istd_pass_count": istd["pass_count"],
            "istd_warning_count": istd["warning_count"],
            "istd_fail_count": istd["fail_count"],
            "istd_known_count": istd["known_count"],
            "runtime": timing["summary"],
        },
        "istd": istd,
        "cleanliness": cleanliness,
        "economics": economics,
        "timing": timing,
        "rt_normalization": rt_normalization,
    }

def _verdict(
    *,
    istd: Mapping[str, Any],
    cleanliness: Mapping[str, Any],
    economics: Mapping[str, Any],
    timing: Mapping[str, Any],
) -> str:
    if istd["unhandled_failures"]:
        return "FAIL"
    if (
        not istd["provided"]
        or not economics["provided"]
        or not timing["provided"]
        or istd["known_count"]
        or istd["warning_count"]
        or cleanliness["warning_count"]
    ):
        return "WARN"
    return "PASS"


def _matrix_cleanliness(
    review: TsvTable,
    matrix: TsvTable,
) -> dict[str, Any]:
    identity_counts = Counter(
        row.get("identity_decision", "") or "unknown" for row in review.rows
    )
    primary_rows = [
        row for row in review.rows if _is_true(row["include_in_primary_matrix"])
    ]
    zero_present_count = 0
    flag_counts: Counter[str] = Counter()
    warning_row_items: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    for row in primary_rows:
        flags = _split_list(row.get("row_flags", ""))
        warning_flags = tuple(
            flag for flag in flags if flag in _CLEANLINESS_WARNING_FLAG_SET
        )
        flag_counts.update(warning_flags)
        zero_present = _is_zero_present(row)
        if zero_present:
            zero_present_count += 1
        if zero_present or warning_flags:
            warning_row_items.append(
                (
                    flags,
                    {
                        "feature_family_id": row["feature_family_id"],
                        "identity_decision": row.get("identity_decision", ""),
                        "present_rate": row.get("present_rate", ""),
                        "detected_count": row.get("detected_count", ""),
                        "accepted_backfill_count": row.get(
                            "accepted_backfill_count", ""
                        ),
                        "row_flags": row.get("row_flags", ""),
                        "warning": row.get("warning", ""),
                    },
                )
            )
    warning_row_items.sort(
        key=lambda item: (
            -len(item[0]),
            _float_value(str(item[1]["present_rate"]), default=math.inf),
            str(item[1]["feature_family_id"]),
        )
    )
    warning_rows = [row for _, row in warning_row_items[:20]]
    warning_count = zero_present_count + sum(flag_counts.values())
    return {
        "primary_row_count": len(matrix.rows),
        "review_primary_row_count": len(primary_rows),
        "identity_counts": dict(sorted(identity_counts.items())),
        "zero_present_row_count": zero_present_count,
        "flag_counts": {
            flag: flag_counts.get(flag, 0)
            for flag in _CLEANLINESS_WARNING_FLAGS
        },
        "warning_count": warning_count,
        "top_warning_rows": warning_rows,
    }


def _istd_benchmark(
    path: Path | None,
    known_exceptions: Mapping[str, set[str]],
) -> dict[str, Any]:
    if path is None:
        return _not_provided("ISTD benchmark JSON was not provided.")
    payload = read_json(path)
    summaries = payload.get("summaries", ())
    if not isinstance(summaries, Sequence) or isinstance(summaries, (str, bytes)):
        raise ValueError(f"{path}: summaries must be a list")
    rows: list[dict[str, Any]] = []
    pass_count = 0
    warning_count = 0
    known_count = 0
    unhandled: list[dict[str, Any]] = []
    for item in summaries:
        if not isinstance(item, Mapping):
            continue
        target = str(item.get("target_label", ""))
        status = str(item.get("status", "")).upper()
        active = _is_active(item)
        modes = _issue_modes(item)
        known = active and status != "PASS" and _is_known_exception(
            target,
            modes,
            known_exceptions,
        )
        if active and status == "PASS":
            pass_count += 1
        elif known:
            known_count += 1
        elif active and status == "WARN":
            warning_count += 1
        elif active and status != "PASS":
            unhandled.append(
                {
                    "target_label": target,
                    "status": status,
                    "failure_modes": modes,
                }
            )
        rows.append(
            {
                "target_label": target,
                "status": status or "UNKNOWN",
                "active": active,
                "known": known,
                "selected_family": item.get("selected_feature_id", ""),
                "primary_hit_count": item.get("primary_match_count", ""),
                "rt_mean_delta": item.get("family_mean_rt_delta_min", ""),
                "rt_p95": item.get("sample_rt_p95_abs_delta_min", ""),
                "spearman": item.get("log_area_spearman", ""),
                "pearson": item.get("log_area_pearson", ""),
                "coverage": _coverage_text(item),
                "failure_modes": ";".join(modes),
            }
        )
    return {
        "provided": True,
        "source": str(path),
        "pass_count": pass_count,
        "warning_count": warning_count,
        "known_count": known_count,
        "fail_count": len(unhandled),
        "unhandled_failures": unhandled,
        "rows": rows,
    }


def _backfill_economics(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _not_provided("Owner-backfill economics JSON was not provided.")
    payload = read_json(path)
    totals = payload.get("totals", {})
    summary = payload.get("summary", ())
    features = payload.get("features", ())
    if not isinstance(totals, Mapping):
        raise ValueError(f"{path}: totals must be an object")
    if not isinstance(summary, Sequence) or isinstance(summary, (str, bytes)):
        raise ValueError(f"{path}: summary must be a list")
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)):
        raise ValueError(f"{path}: features must be a list")
    return {
        "provided": True,
        "source": str(path),
        "totals": dict(totals),
        "summary": [dict(row) for row in summary if isinstance(row, Mapping)],
        "top_expensive_families": [
            dict(row) for row in features if isinstance(row, Mapping)
        ][:20],
    }


def _timing_summary(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _not_provided("Timing JSON was not provided.")
    payload = read_json(path)
    records = payload.get("records", ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError(f"{path}: records must be a list")
    by_stage: dict[str, float] = {}
    for item in records:
        if not isinstance(item, Mapping):
            continue
        stage = str(item.get("stage", "unknown"))
        by_stage[stage] = by_stage.get(stage, 0.0) + _float_value(
            item.get("elapsed_sec", ""),
            default=0.0,
        )
    top_stages = [
        {"stage": stage, "elapsed_sec": elapsed}
        for stage, elapsed in sorted(
            by_stage.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:10]
    ]
    total = sum(by_stage.values())
    return {
        "provided": True,
        "source": str(path),
        "summary": {
            "pipeline": payload.get("pipeline", ""),
            "run_id": payload.get("run_id", ""),
            "total_elapsed_sec": total,
            "top_stages": top_stages,
        },
    }


def _rt_normalization(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"provided": False}
    payload = read_json(path)
    leave_one = payload.get("leave_one_anchor_out", ())
    rt_bands = payload.get("rt_band_summary", {})
    if not isinstance(leave_one, Sequence) or isinstance(leave_one, (str, bytes)):
        raise ValueError(f"{path}: leave_one_anchor_out must be a list")
    if not isinstance(rt_bands, Mapping):
        raise ValueError(f"{path}: rt_band_summary must be an object")
    return {
        "provided": True,
        "source": str(path),
        "overall_status": payload.get("overall_status", ""),
        "reference_source": payload.get("reference_source", ""),
        "model_type": payload.get("model_type", ""),
        "anchor_label_count": payload.get("anchor_label_count", ""),
        "sample_count": payload.get("sample_count", ""),
        "modelled_sample_count": payload.get("modelled_sample_count", ""),
        "unmodelled_sample_count": payload.get("unmodelled_sample_count", ""),
        "excluded_anchor_count": payload.get("excluded_anchor_count", ""),
        "families_improved_count": payload.get("families_improved_count", ""),
        "families_worsened_count": payload.get("families_worsened_count", ""),
        "median_rt_range_improvement_min": payload.get(
            "median_rt_range_improvement_min",
            "",
        ),
        "rt_band_summary": {
            str(band): dict(value)
            for band, value in rt_bands.items()
            if isinstance(value, Mapping)
        },
        "leave_one_anchor_out": [
            dict(row) for row in leave_one if isinstance(row, Mapping)
        ],
    }



def _not_provided(reason: str) -> dict[str, Any]:
    return {
        "provided": False,
        "reason": reason,
        "pass_count": 0,
        "warning_count": 0,
        "known_count": 0,
        "fail_count": 0,
        "unhandled_failures": [],
        "rows": [],
        "summary": {},
        "totals": {},
        "top_expensive_families": [],
    }



def _coverage_text(row: Mapping[str, Any]) -> str:
    observed = row.get("untargeted_positive_count", "")
    minimum = row.get("coverage_minimum", "")
    targeted = row.get("targeted_positive_count", "")
    return f"{observed}/{targeted} (min {minimum})"


def _sample_count(fieldnames: Sequence[str]) -> int:
    return sum(1 for field in fieldnames if field not in _MATRIX_METADATA_COLUMNS)


def _is_zero_present(row: Mapping[str, str]) -> bool:
    return (
        _float_value(row.get("present_rate", ""), default=0.0) == 0.0
        and _int_value(row.get("detected_count", "")) == 0
        and _int_value(row.get("accepted_backfill_count", "")) == 0
    )


def _is_active(row: Mapping[str, Any]) -> bool:
    value = row.get("active_tag")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return _is_true(str(value))


def _is_known_exception(
    target: str,
    modes: tuple[str, ...],
    known_exceptions: Mapping[str, set[str]],
) -> bool:
    if not modes:
        return False
    known_modes = known_exceptions.get(target)
    if known_modes is None:
        return False
    return all(mode in known_modes for mode in modes)


def _parse_known_istd_exceptions(values: tuple[str, ...]) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {}
    for value in values:
        if ":" not in value:
            raise ValueError(
                "--known-istd-exception must use TARGET:FAILURE_MODE format"
            )
        target, mode = value.split(":", 1)
        target = target.strip()
        mode = mode.strip()
        if not target or not mode:
            raise ValueError(
                "--known-istd-exception must use TARGET:FAILURE_MODE format"
            )
        known.setdefault(target, set()).add(mode)
    return known


def _failure_modes(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part for part in _split_list(value) if part)
    if isinstance(value, Sequence):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _issue_modes(row: Mapping[str, Any]) -> tuple[str, ...]:
    modes = _failure_modes(row.get("failure_modes", ()))
    if modes:
        return modes
    return _failure_modes(row.get("targeted_reliability_warning_modes", ()))


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in value.replace(",", ";").split(";")
        if part.strip()
    )


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "t", "yes", "y"}


def _int_value(value: Any) -> int:
    try:
        return int(float(str(value)))
    except ValueError:
        return 0


def _float_value(value: Any, *, default: float) -> float:
    try:
        number = float(str(value))
    except ValueError:
        return default
    return number if math.isfinite(number) else default
