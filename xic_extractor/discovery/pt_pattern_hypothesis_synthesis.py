"""No-RAW synthesis for Pt pattern ratio and assembly diagnostics."""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_ratio_profile import (
    AUTHORITY_SCOPE as RATIO_AUTHORITY_SCOPE,
)
from xic_extractor.discovery.pt_pattern_ratio_profile import (
    EVIDENCE_OWNER as RATIO_EVIDENCE_OWNER,
)
from xic_extractor.discovery.pt_pattern_ratio_profile import (
    RATIO_NAMES,
)
from xic_extractor.discovery.pt_pattern_ratio_profile import (
    READINESS_TIER as RATIO_READINESS_TIER,
)
from xic_extractor.discovery.pt_pattern_ratio_profile import (
    SCHEMA_VERSION as RATIO_SCHEMA_VERSION,
)
from xic_extractor.discovery.pt_pattern_synthesis_report import (
    render_hypothesis_synthesis_html,
)
from xic_extractor.tabular_io import (
    bool_value,
    file_sha256,
    format_diagnostic_value,
    optional_float,
    read_delimited_rows,
    split_semicolon_labels,
    text_value,
    validate_delimited_header,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_hypothesis_synthesis"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_hypothesis_synthesis"
AUTHORITY_SCOPE = "hypothesis_synthesis_only"

RATIO_SUMMARY_REQUIRED_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "profile_id",
    "population_label",
    "ratio_name",
    "row_count",
    "missing_count",
    "median",
    "q25",
    "q75",
    "q95",
    "outlier_count",
)

INCLUSION_SUMMARY_REQUIRED_COLUMNS = ("set", "metric", "value")

PLUS1_SUMMARY_REQUIRED_COLUMNS = (
    "threshold_plus1_height",
    "diagnostic_full_pattern_candidates",
    "unique_base_count",
    "current_386_candidate_recovered",
    "current_146_accept_recovered",
    "current_146_accept_recall_fraction",
    "top102_rows_hit_by_plus1_mz",
    "top102_recall_fraction",
    "manual_known_rows_hit_by_plus1_mz",
    "manual_known_recall_fraction",
    "edge_to_candidate_ratio",
)

ASSEMBLY_DIAGNOSIS_REQUIRED_COLUMNS = (
    "query_set",
    "compound",
    "list_plus1_mz",
    "pipeline_status",
    "coelution_root_layer",
    "candidate_reject_reason_if_any",
    "feature_base_count_20ppm",
    "feature_plus1_count_20ppm",
    "feature_plus2_count_20ppm",
    "feature_plus4_count_20ppm",
    "coherent_full_pattern_exists",
)

KNOWN_DIAGNOSIS_REQUIRED_COLUMNS = (
    "compound",
    "list_plus1_mz",
    "diagnostic_status",
    "trace_rule_reason",
)

ASSEMBLY_LAYER_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "report_id",
    "query_set",
    "coelution_root_layer",
    "pipeline_status",
    "row_count",
    "feature_all4_exist_count",
    "coherent_full_pattern_count",
    "candidate_rejected_count",
    "accepted_count",
)

KNOWN_EXAMPLE_COLUMNS = (
    "schema_version",
    "readiness_tier",
    "evidence_owner",
    "authority_scope",
    "report_id",
    "source_label",
    "compound",
    "list_plus1_mz",
    "pipeline_status",
    "coelution_root_layer",
    "candidate_reject_reason_if_any",
    "feature_base_count_20ppm",
    "feature_plus1_count_20ppm",
    "feature_plus2_count_20ppm",
    "feature_plus4_count_20ppm",
    "coherent_full_pattern_exists",
    "diagnostic_note",
)

KEY_RATIO_NAMES = (
    "plus1_over_base_height_ratio",
    "base_over_plus1_height_ratio",
    "plus1_over_plus2_height_ratio",
    "plus1_over_plus4_height_ratio",
    "plus1_over_base_area_ratio",
    "base_over_plus1_area_ratio",
)

_RATIO_VALUE_FIELDS = ("median", "q25", "q75", "q95")
_INTERFERENCE_COUNT_FIELDS = (
    "input_accept_count",
    "rt_12p5_13p5_accept_count",
    "rt_12p5_13p5_overlap_candidate_count",
    "base_delta_overlap_candidate_count",
    "base_delta_overlap_cluster_count",
    "ratio_above_expected_with_overlap_count",
    "ratio_above_expected_without_overlap_count",
)


def run_hypothesis_synthesis(
    *,
    ratio_summary_csv: Path,
    output_dir: Path,
    report_id: str,
    background_ratio_summary_csv: Path | None = None,
    interference_summary_json: Path | None = None,
    plus1_seed_summary_csv: Path | None = None,
    inclusion_summary_csv: Path | None = None,
    top102_diagnosis_csv: Path | None = None,
    known_diagnosis_csv: Path | None = None,
) -> dict[str, Path]:
    """Write a diagnostic-only synthesis from already-produced Pt artifacts."""

    if not report_id.strip():
        raise ValueError("report_id must be non-empty")
    ratio_summary = read_delimited_rows(
        ratio_summary_csv,
        required_columns=RATIO_SUMMARY_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    background_ratio_summary = (
        read_delimited_rows(
            background_ratio_summary_csv,
            required_columns=RATIO_SUMMARY_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
        if background_ratio_summary_csv is not None
        else []
    )
    interference: Mapping[str, object]
    if interference_summary_json is not None:
        interference_value = json.loads(
            interference_summary_json.read_text(encoding="utf-8")
        )
        if not isinstance(interference_value, Mapping):
            raise ValueError("interference summary must contain a JSON object")
        interference = interference_value
        _validate_interference_summary(interference)
    else:
        interference = {}
    plus1_summary = (
        read_delimited_rows(
            plus1_seed_summary_csv,
            required_columns=PLUS1_SUMMARY_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
        if plus1_seed_summary_csv is not None
        else []
    )
    inclusion_summary = (
        read_delimited_rows(
            inclusion_summary_csv,
            required_columns=INCLUSION_SUMMARY_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
        if inclusion_summary_csv is not None
        else []
    )
    top102_rows = _read_optional_assembly(
        top102_diagnosis_csv,
        expected_query_set="top102",
    )
    known_rows = _read_optional_known(known_diagnosis_csv)
    assembly_rows = [*top102_rows, *known_rows]

    _require_unique_text_key(
        ratio_summary,
        column="ratio_name",
        label="primary ratio_name",
    )
    _require_unique_text_key(
        background_ratio_summary,
        column="ratio_name",
        label="background ratio_name",
    )
    primary_profile_id = _validate_ratio_summary_rows(
        ratio_summary,
        label="primary ratio summary",
        expected_population_label="primary_population",
    )
    if background_ratio_summary_csv is not None:
        background_profile_id = _validate_ratio_summary_rows(
            background_ratio_summary,
            label="background ratio summary",
            expected_population_label="background_population",
        )
        if background_profile_id != primary_profile_id:
            raise ValueError(
                "background ratio summary profile_id must match primary ratio "
                f"summary: {background_profile_id!r} != {primary_profile_id!r}"
            )
    _require_unique_plus1_thresholds(plus1_summary)
    _validate_plus1_summary_rows(plus1_summary)
    _require_unique_inclusion_keys(inclusion_summary)

    ratio_payload = _ratio_payload(ratio_summary, background_ratio_summary)
    plus1_payload = _plus1_payload(plus1_summary)
    inclusion_payload = _inclusion_payload(inclusion_summary)
    interference_payload = _interference_payload(interference)
    layer_rows = _assembly_layer_rows(
        assembly_rows,
        report_id=report_id,
    )
    known_examples = _known_example_rows(
        known_rows,
        report_id=report_id,
        source_label="known_named",
    )
    observations = _observations(
        ratio_payload=ratio_payload,
        interference_payload=interference_payload,
        plus1_payload=plus1_payload,
        inclusion_payload=inclusion_payload,
        layer_rows=layer_rows,
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "authority_scope": AUTHORITY_SCOPE,
        "report_id": report_id,
        "opens_raw": False,
        "changes_matrix": False,
        "changes_counted_detection": False,
        "changes_export": False,
        "changes_detector_rule": False,
        "ratio": ratio_payload,
        "interference": interference_payload,
        "plus1_seed": plus1_payload,
        "inclusion_list": inclusion_payload,
        "assembly_layer_count": len(layer_rows),
        "known_example_count": len(known_examples),
        "observations": observations,
        "inputs": _input_provenance(
            ratio_summary_csv=ratio_summary_csv,
            background_ratio_summary_csv=background_ratio_summary_csv,
            interference_summary_json=interference_summary_json,
            plus1_seed_summary_csv=plus1_seed_summary_csv,
            inclusion_summary_csv=inclusion_summary_csv,
            top102_diagnosis_csv=top102_diagnosis_csv,
            known_diagnosis_csv=known_diagnosis_csv,
        ),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    layer_csv = output_dir / "pt_pattern_assembly_layer_summary.csv"
    known_csv = output_dir / "pt_pattern_known_miss_examples.csv"
    summary_json = output_dir / "pt_pattern_hypothesis_synthesis_summary.json"
    markdown = output_dir / "pt_pattern_hypothesis_synthesis.md"
    html = output_dir / "pt_pattern_hypothesis_synthesis.html"

    write_delimited_rows(layer_csv, layer_rows, ASSEMBLY_LAYER_COLUMNS)
    write_delimited_rows(known_csv, known_examples, KNOWN_EXAMPLE_COLUMNS)
    summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown.write_text(_render_markdown(summary), encoding="utf-8")
    html.write_text(
        render_hypothesis_synthesis_html(
            summary=summary,
            primary_ratio_summary=ratio_summary,
            background_ratio_summary=background_ratio_summary,
        ),
        encoding="utf-8",
    )
    return {
        "assembly_layer_summary_csv": layer_csv,
        "known_miss_examples_csv": known_csv,
        "summary_json": summary_json,
        "markdown": markdown,
        "html": html,
    }


def _read_optional_assembly(
    path: Path | None,
    *,
    expected_query_set: str,
) -> list[dict[str, str]]:
    if path is None:
        return []
    rows = read_delimited_rows(
        path,
        required_columns=ASSEMBLY_DIAGNOSIS_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    _validate_assembly_rows(
        rows,
        label=str(path),
        expected_query_set=expected_query_set,
    )
    return rows


def _read_optional_known(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=",")
        header = validate_delimited_header(reader.fieldnames, path=path)
    assembly_only_columns = set(ASSEMBLY_DIAGNOSIS_REQUIRED_COLUMNS).difference(
        KNOWN_DIAGNOSIS_REQUIRED_COLUMNS
    )
    if assembly_only_columns.intersection(header):
        rows = read_delimited_rows(
            path,
            required_columns=ASSEMBLY_DIAGNOSIS_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
    else:
        raw_rows = read_delimited_rows(
            path,
            required_columns=KNOWN_DIAGNOSIS_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
        rows = [_normalize_known_row(row) for row in raw_rows]
    _validate_assembly_rows(
        rows,
        label=str(path),
        expected_query_set="manual_known",
    )
    return rows


def _validate_assembly_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    label: str,
    expected_query_set: str,
) -> None:
    for row_index, row in enumerate(rows, start=2):
        row_label = f"{label} row {row_index}"
        query_set = _required_text(row, "query_set", label=row_label)
        if query_set != expected_query_set:
            raise ValueError(f"{row_label} query_set must be {expected_query_set!r}")
        _required_text(row, "compound", label=row_label)
        _required_positive_number(row, "list_plus1_mz", label=row_label)
        for position in ("base", "plus1", "plus2", "plus4"):
            _required_nonnegative_int(
                row,
                f"feature_{position}_count_20ppm",
                label=row_label,
            )
        _required_boolean(
            row,
            "coherent_full_pattern_exists",
            label=row_label,
        )


def _normalize_known_row(row: Mapping[str, str]) -> dict[str, str]:
    status_tokens = set(split_semicolon_labels(row.get("diagnostic_status")))
    pipeline_status = _known_pipeline_status(status_tokens)
    return {
        "query_set": "manual_known",
        "compound": text_value(row.get("compound")),
        "list_plus1_mz": text_value(row.get("list_plus1_mz")),
        "pipeline_status": pipeline_status,
        "coelution_root_layer": _known_root_layer(status_tokens, pipeline_status),
        "candidate_reject_reason_if_any": text_value(row.get("trace_rule_reason")),
        "feature_base_count_20ppm": "1"
        if "feature_base_exists" in status_tokens
        else "0",
        "feature_plus1_count_20ppm": "1"
        if "feature_plus1_exists" in status_tokens
        else "0",
        "feature_plus2_count_20ppm": "1"
        if "feature_plus2_exists" in status_tokens
        else "0",
        "feature_plus4_count_20ppm": "1"
        if "feature_plus4_exists" in status_tokens
        else "0",
        "coherent_full_pattern_exists": "FALSE",
    }


def _known_pipeline_status(status_tokens: set[str]) -> str:
    if "accepted_candidate" in status_tokens:
        return "accepted_146"
    if "candidate_rejected" in status_tokens:
        return "candidate_rejected"
    if "no_candidate_within20" in status_tokens:
        return "no_candidate_within20"
    return "unknown"


def _known_root_layer(status_tokens: set[str], pipeline_status: str) -> str:
    if pipeline_status == "accepted_146":
        return "accepted"
    if pipeline_status == "candidate_rejected":
        return "candidate_rejected_by_trace_gate"
    feature_tokens = {
        "feature_base_exists",
        "feature_plus1_exists",
        "feature_plus2_exists",
        "feature_plus4_exists",
    }
    if feature_tokens.issubset(status_tokens):
        return "features_all4_exist_but_no_candidate"
    return "feature_missing_before_candidate"


def _ratio_payload(
    primary_rows: Sequence[Mapping[str, str]],
    background_rows: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    primary_by_name = {text_value(row.get("ratio_name")): row for row in primary_rows}
    background_by_name = {
        text_value(row.get("ratio_name")): row for row in background_rows
    }
    primary_counts = _row_counts_by_ratio(primary_rows)
    background_counts = _row_counts_by_ratio(background_rows)
    key_ratios: dict[str, dict[str, object]] = {}
    for name in KEY_RATIO_NAMES:
        primary = primary_by_name.get(name)
        background = background_by_name.get(name)
        key_ratios[name] = {
            "primary_median": optional_float(primary.get("median"))
            if primary
            else None,
            "primary_q25": optional_float(primary.get("q25")) if primary else None,
            "primary_q75": optional_float(primary.get("q75")) if primary else None,
            "primary_q95": optional_float(primary.get("q95")) if primary else None,
            "primary_outlier_count": _required_nonnegative_int(
                primary,
                "outlier_count",
                label="primary ratio summary",
            )
            if primary
            else None,
            "background_median": optional_float(background.get("median"))
            if background
            else None,
        }
    return {
        "profile_id": _ratio_profile_id(primary_rows),
        "background_available": bool(background_rows),
        "primary_population_count": _ratio_population_count(primary_rows),
        "background_population_count": _ratio_population_count(background_rows),
        "primary_row_count": _common_row_count(primary_counts),
        "background_row_count": _common_row_count(background_counts),
        "primary_row_count_by_ratio": primary_counts,
        "background_row_count_by_ratio": background_counts,
        "key_ratios": key_ratios,
        "interpretation": (
            "Multiple formula families are compared side-by-side; no ratio "
            "family is detector authority in this slice."
        ),
    }


def _row_counts_by_ratio(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, int]:
    return {
        ratio_name: _required_nonnegative_int(
            row,
            "row_count",
            label=f"ratio summary {ratio_name}",
        )
        for ratio_name, row in sorted(
            (text_value(row.get("ratio_name")), row) for row in rows
        )
    }


def _ratio_profile_id(rows: Sequence[Mapping[str, str]]) -> str:
    if not rows:
        return ""
    return text_value(rows[0].get("profile_id"))


def _ratio_population_count(
    rows: Sequence[Mapping[str, str]],
) -> int | None:
    if not rows:
        return None
    row = rows[0]
    return _required_nonnegative_int(
        row,
        "row_count",
        label="ratio summary",
    ) + _required_nonnegative_int(
        row,
        "missing_count",
        label="ratio summary",
    )


def _common_row_count(counts: Mapping[str, int]) -> int | None:
    if not counts:
        return 0
    unique_counts = set(counts.values())
    if len(unique_counts) == 1:
        return next(iter(unique_counts))
    return None


def _interference_payload(interference: Mapping[str, object]) -> dict[str, object]:
    largest_value = interference.get("largest_clusters")
    largest = (
        list(largest_value)
        if isinstance(largest_value, Sequence)
        and not isinstance(largest_value, (str, bytes))
        else []
    )
    largest_cluster = largest[0] if largest and isinstance(largest[0], Mapping) else {}
    available = bool(interference)
    if not available:
        counts: dict[str, int | None] = {
            field: None for field in _INTERFERENCE_COUNT_FIELDS
        }
    else:
        counts = {
            field: _required_nonnegative_int(
                interference,
                field,
                label="interference summary",
            )
            for field in _INTERFERENCE_COUNT_FIELDS
        }
    return {
        "available": available,
        **counts,
        "largest_cluster_candidate_count": (
            _required_nonnegative_int(
                largest_cluster,
                "candidate_count",
                label="interference largest cluster",
            )
            if largest_cluster
            else (0 if available else None)
        ),
        "largest_cluster_edge_count": (
            _required_nonnegative_int(
                largest_cluster,
                "edge_count",
                label="interference largest cluster",
            )
            if largest_cluster
            else (0 if available else None)
        ),
        "largest_cluster_rt_span_sec": (
            _optional_finite_number(
                largest_cluster,
                "rt_span_sec",
                label="interference largest cluster",
                minimum=0,
            )
            if largest_cluster
            else None
        ),
        "interpretation": (
            "Dense overlap supports an interference hypothesis, but does not "
            "prove the physical mechanism."
        ),
    }


def _plus1_payload(rows: Sequence[Mapping[str, str]]) -> dict[str, object]:
    by_threshold = {_plus1_threshold_key(row): row for row in rows}
    return {
        "available": bool(rows),
        "thresholds": {
            str(threshold): _plus1_threshold_payload(row)
            for threshold, row in sorted(by_threshold.items())
        },
        "threshold_25000": _plus1_threshold_payload(by_threshold.get(25000)),
        "threshold_100000": _plus1_threshold_payload(by_threshold.get(100000)),
        "interpretation": (
            "+1 intensity is a seed/prioritization knob here, not a hard detector gate."
        ),
    }


def _plus1_threshold_payload(row: Mapping[str, str] | None) -> dict[str, object]:
    if row is None:
        return {}
    return {
        "diagnostic_full_pattern_candidates": _plus1_count(
            row,
            "diagnostic_full_pattern_candidates",
        ),
        "unique_base_count": _plus1_count(row, "unique_base_count"),
        "current_386_candidate_recovered": _plus1_count(
            row,
            "current_386_candidate_recovered",
        ),
        "current_146_accept_recovered": _plus1_count(
            row,
            "current_146_accept_recovered",
        ),
        "current_146_accept_recall_fraction": _optional_finite_number(
            row,
            "current_146_accept_recall_fraction",
            label="plus1 summary",
            minimum=0,
            maximum=1,
        ),
        "top102_rows_hit_by_plus1_mz": _plus1_count(
            row,
            "top102_rows_hit_by_plus1_mz",
        ),
        "top102_recall_fraction": _optional_finite_number(
            row,
            "top102_recall_fraction",
            label="plus1 summary",
            minimum=0,
            maximum=1,
        ),
        "manual_known_rows_hit_by_plus1_mz": _plus1_count(
            row,
            "manual_known_rows_hit_by_plus1_mz",
        ),
        "manual_known_recall_fraction": _optional_finite_number(
            row,
            "manual_known_recall_fraction",
            label="plus1 summary",
            minimum=0,
            maximum=1,
        ),
        "edge_to_candidate_ratio": _optional_finite_number(
            row,
            "edge_to_candidate_ratio",
            label="plus1 summary",
            minimum=0,
        ),
    }


def _inclusion_payload(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        set_name = text_value(row.get("set"))
        metric = text_value(row.get("metric"))
        grouped.setdefault(set_name, {})[metric] = _required_nonnegative_int(
            row,
            "value",
            label=f"inclusion {set_name}/{metric}",
        )
    for set_name, metrics in grouped.items():
        row_count = metrics.get("row_count")
        if not isinstance(row_count, int):
            raise ValueError(f"inclusion {set_name} must define row_count")
        for metric, value in metrics.items():
            if metric != "row_count" and isinstance(value, int) and value > row_count:
                raise ValueError(f"inclusion {set_name}/{metric} must be <= row_count")
    return {"available": bool(rows), **grouped}


def _require_unique_text_key(
    rows: Sequence[Mapping[str, str]],
    *,
    column: str,
    label: str,
) -> None:
    seen: set[str] = set()
    for row_index, row in enumerate(rows, start=2):
        value = text_value(row.get(column))
        if not value:
            raise ValueError(f"{label} row {row_index} must be non-empty")
        if value in seen:
            raise ValueError(f"duplicate {label}: {value}")
        seen.add(value)


def _validate_ratio_summary_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    label: str,
    expected_population_label: str,
) -> str:
    if not rows:
        raise ValueError(f"{label} must contain the complete canonical ratio_name set")
    observed_ratio_names = {text_value(row.get("ratio_name")) for row in rows}
    expected_ratio_names = set(RATIO_NAMES)
    if observed_ratio_names != expected_ratio_names:
        missing = sorted(expected_ratio_names - observed_ratio_names)
        extra = sorted(observed_ratio_names - expected_ratio_names)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise ValueError(
            f"{label} must contain the canonical ratio_name set: " + "; ".join(details)
        )
    profile_ids: set[str] = set()
    total_population_counts: set[int] = set()
    for row_index, row in enumerate(rows, start=2):
        row_label = f"{label} row {row_index}"
        expected_envelope = {
            "schema_version": RATIO_SCHEMA_VERSION,
            "readiness_tier": RATIO_READINESS_TIER,
            "evidence_owner": RATIO_EVIDENCE_OWNER,
            "authority_scope": RATIO_AUTHORITY_SCOPE,
        }
        for field, expected_value in expected_envelope.items():
            observed_value = _required_text(row, field, label=row_label)
            if observed_value != expected_value:
                raise ValueError(f"{row_label} {field} must be {expected_value!r}")
        profile_ids.add(_required_text(row, "profile_id", label=row_label))
        population_label = _required_text(
            row,
            "population_label",
            label=row_label,
        )
        if population_label != expected_population_label:
            raise ValueError(
                f"{row_label} population_label must be {expected_population_label!r}"
            )
        row_count = _required_nonnegative_int(
            row,
            "row_count",
            label=row_label,
        )
        missing_count = _required_nonnegative_int(
            row,
            "missing_count",
            label=row_label,
        )
        total_population_counts.add(row_count + missing_count)
        outlier_count = _required_nonnegative_int(
            row,
            "outlier_count",
            label=row_label,
        )
        if outlier_count > row_count:
            raise ValueError(f"{row_label} outlier_count must be <= row_count")
        values = {
            field: _optional_finite_number(
                row,
                field,
                label=row_label,
                minimum=0,
            )
            for field in _RATIO_VALUE_FIELDS
        }
        if row_count > 0:
            for field, value in values.items():
                if value is None:
                    raise ValueError(
                        f"{row_label} {field} must be present when row_count > 0"
                    )
        else:
            for field, value in values.items():
                if value is not None:
                    raise ValueError(
                        f"{row_label} {field} must be empty when row_count is 0"
                    )
        q25 = values["q25"]
        median = values["median"]
        q75 = values["q75"]
        q95 = values["q95"]
        if (
            q25 is not None
            and median is not None
            and q75 is not None
            and not q25 <= median <= q75
        ):
            raise ValueError(f"{row_label} must satisfy q25 <= median <= q75")
        if q75 is not None and q95 is not None and q95 < q75:
            raise ValueError(f"{row_label} must satisfy q75 <= q95")
    if len(profile_ids) != 1:
        raise ValueError(
            f"{label} must contain a single profile_id; found {sorted(profile_ids)!r}"
        )
    if len(total_population_counts) != 1:
        raise ValueError(
            f"{label} row_count + missing_count must identify one stable "
            f"population; found {sorted(total_population_counts)!r}"
        )
    return next(iter(profile_ids))


def _validate_interference_summary(interference: Mapping[str, object]) -> None:
    counts = {
        field: _required_nonnegative_int(
            interference,
            field,
            label="interference summary",
        )
        for field in _INTERFERENCE_COUNT_FIELDS
    }
    _require_subset_count(
        counts,
        "rt_12p5_13p5_accept_count",
        "input_accept_count",
        label="interference summary",
    )
    _require_subset_count(
        counts,
        "rt_12p5_13p5_overlap_candidate_count",
        "rt_12p5_13p5_accept_count",
        label="interference summary",
    )
    _require_subset_count(
        counts,
        "base_delta_overlap_candidate_count",
        "input_accept_count",
        label="interference summary",
    )
    _require_subset_count(
        counts,
        "base_delta_overlap_cluster_count",
        "base_delta_overlap_candidate_count",
        label="interference summary",
    )
    for field in (
        "ratio_above_expected_with_overlap_count",
        "ratio_above_expected_without_overlap_count",
    ):
        _require_subset_count(
            counts,
            field,
            "input_accept_count",
            label="interference summary",
        )
    ratio_above_total = (
        counts["ratio_above_expected_with_overlap_count"]
        + counts["ratio_above_expected_without_overlap_count"]
    )
    if ratio_above_total > counts["input_accept_count"]:
        raise ValueError(
            "interference summary ratio_above_expected counts must sum to "
            "<= input_accept_count"
        )
    largest_clusters = interference.get("largest_clusters")
    if not isinstance(largest_clusters, Sequence) or isinstance(
        largest_clusters,
        (str, bytes),
    ):
        raise ValueError("interference summary largest_clusters must be a list")
    for index, cluster in enumerate(largest_clusters):
        if not isinstance(cluster, Mapping):
            raise ValueError(
                f"interference summary largest_clusters[{index}] must be an object"
            )
        cluster_label = f"interference largest_clusters[{index}]"
        candidate_count = _required_nonnegative_int(
            cluster,
            "candidate_count",
            label=cluster_label,
        )
        _required_nonnegative_int(
            cluster,
            "edge_count",
            label=cluster_label,
        )
        _optional_finite_number(
            cluster,
            "rt_span_sec",
            label=cluster_label,
            minimum=0,
        )
        if candidate_count > counts["base_delta_overlap_candidate_count"]:
            raise ValueError(
                f"{cluster_label} candidate_count must be <= "
                "base_delta_overlap_candidate_count"
            )
    if len(largest_clusters) > counts["base_delta_overlap_cluster_count"]:
        raise ValueError(
            "interference summary largest_clusters length must be <= "
            "base_delta_overlap_cluster_count"
        )


def _validate_plus1_summary_rows(
    rows: Sequence[Mapping[str, object]],
) -> None:
    for row_index, row in enumerate(rows, start=2):
        label = f"plus1 summary row {row_index}"
        counts = {
            field: _required_nonnegative_int(row, field, label=label)
            for field in (
                "diagnostic_full_pattern_candidates",
                "unique_base_count",
                "current_386_candidate_recovered",
                "current_146_accept_recovered",
            )
        }
        _require_subset_count(
            counts,
            "unique_base_count",
            "diagnostic_full_pattern_candidates",
            label=label,
        )
        _require_subset_count(
            counts,
            "current_386_candidate_recovered",
            "diagnostic_full_pattern_candidates",
            label=label,
        )
        _require_subset_count(
            counts,
            "current_146_accept_recovered",
            "current_386_candidate_recovered",
            label=label,
        )


def _require_subset_count(
    counts: Mapping[str, int],
    child: str,
    parent: str,
    *,
    label: str,
) -> None:
    if counts[child] > counts[parent]:
        raise ValueError(f"{label} {child} must be <= {parent}")


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


def _plus1_count(row: Mapping[str, object], field: str) -> int:
    return _required_nonnegative_int(row, field, label="plus1 summary")


def _optional_finite_number(
    row: Mapping[str, object],
    field: str,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    raw_value = row.get(field)
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        return None
    value = optional_float(raw_value)
    if value is None:
        raise ValueError(f"{label} {field} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} {field} must be >= {minimum:g}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} {field} must be <= {maximum:g}")
    return value


def _plus1_threshold_key(row: Mapping[str, str]) -> int:
    try:
        return _required_nonnegative_int(
            row,
            "threshold_plus1_height",
            label="plus1 summary",
        )
    except ValueError as exc:
        raise ValueError(
            "threshold_plus1_height must be a finite integer identity key"
        ) from exc


def _require_unique_plus1_thresholds(
    rows: Sequence[Mapping[str, str]],
) -> None:
    seen: set[int] = set()
    for row in rows:
        threshold = _plus1_threshold_key(row)
        if threshold in seen:
            raise ValueError(f"duplicate plus1 threshold_plus1_height: {threshold}")
        seen.add(threshold)


def _require_unique_inclusion_keys(
    rows: Sequence[Mapping[str, str]],
) -> None:
    seen: set[tuple[str, str]] = set()
    for row_index, row in enumerate(rows, start=2):
        key = (text_value(row.get("set")), text_value(row.get("metric")))
        if not all(key):
            raise ValueError(f"inclusion set/metric row {row_index} must be non-empty")
        if key in seen:
            raise ValueError(f"duplicate inclusion set/metric: {key!r}")
        seen.add(key)


def _assembly_layer_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    report_id: str,
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, str]]] = {}
    for row in rows:
        key = (
            text_value(row.get("query_set")),
            text_value(row.get("coelution_root_layer")) or "unknown",
            text_value(row.get("pipeline_status")) or "unknown",
        )
        grouped.setdefault(key, []).append(row)
    output_rows: list[dict[str, object]] = []
    for (query_set, layer, status), items in sorted(grouped.items()):
        output_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "readiness_tier": READINESS_TIER,
                "evidence_owner": EVIDENCE_OWNER,
                "authority_scope": AUTHORITY_SCOPE,
                "report_id": report_id,
                "query_set": query_set,
                "coelution_root_layer": layer,
                "pipeline_status": status,
                "row_count": len(items),
                "feature_all4_exist_count": sum(
                    1 for item in items if _all_four_features_exist(item)
                ),
                "coherent_full_pattern_count": sum(
                    1
                    for item in items
                    if _required_boolean(
                        item,
                        "coherent_full_pattern_exists",
                        label="assembly row",
                    )
                ),
                "candidate_rejected_count": sum(
                    1
                    for item in items
                    if text_value(item.get("pipeline_status")) == "candidate_rejected"
                ),
                "accepted_count": sum(
                    1
                    for item in items
                    if text_value(item.get("pipeline_status")) == "accepted_146"
                ),
            }
        )
    return output_rows


def _known_example_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    report_id: str,
    source_label: str,
) -> list[dict[str, object]]:
    examples = [
        row for row in rows if text_value(row.get("pipeline_status")) != "accepted_146"
    ][:40]
    return [
        {
            "schema_version": SCHEMA_VERSION,
            "readiness_tier": READINESS_TIER,
            "evidence_owner": EVIDENCE_OWNER,
            "authority_scope": AUTHORITY_SCOPE,
            "report_id": report_id,
            "source_label": source_label,
            "compound": text_value(row.get("compound")),
            "list_plus1_mz": _required_positive_number(
                row,
                "list_plus1_mz",
                label="known assembly row",
            ),
            "pipeline_status": text_value(row.get("pipeline_status")),
            "coelution_root_layer": text_value(row.get("coelution_root_layer")),
            "candidate_reject_reason_if_any": text_value(
                row.get("candidate_reject_reason_if_any")
            ),
            "feature_base_count_20ppm": _int(row, "feature_base_count_20ppm"),
            "feature_plus1_count_20ppm": _int(row, "feature_plus1_count_20ppm"),
            "feature_plus2_count_20ppm": _int(row, "feature_plus2_count_20ppm"),
            "feature_plus4_count_20ppm": _int(row, "feature_plus4_count_20ppm"),
            "coherent_full_pattern_exists": _required_boolean(
                row,
                "coherent_full_pattern_exists",
                label="known assembly row",
            ),
            "diagnostic_note": _diagnostic_note(row),
        }
        for row in examples
    ]


def _observations(
    *,
    ratio_payload: Mapping[str, object],
    interference_payload: Mapping[str, object],
    plus1_payload: Mapping[str, object],
    inclusion_payload: Mapping[str, object],
    layer_rows: Sequence[Mapping[str, object]],
) -> list[str]:
    observations = [
        "Ratio formula choice remains open; compare base-over-position, "
        "position-over-base, and +1-anchor families side-by-side.",
    ]
    if interference_payload.get("rt_12p5_13p5_accept_count"):
        observations.append(
            "The 12.5-13.5 min region is a dense overlap hotspot, supporting "
            "but not proving the isotope-envelope interference hypothesis."
        )
    threshold_100k = plus1_payload.get("threshold_100000")
    if isinstance(threshold_100k, Mapping) and threshold_100k:
        observations.append(
            "+1 height thresholding changes the search universe; it is a "
            "seed/prioritization experiment rather than a hard rule."
        )
    top102 = inclusion_payload.get("top102")
    if isinstance(top102, Mapping) and top102:
        observations.append(
            "Many list rows have the four feature positions nearby but do not "
            "become accepted candidates, pointing at feature grouping and "
            "assignment before ratio gating."
        )
    layer_counter: Counter[str] = Counter()
    for row in layer_rows:
        layer_counter[text_value(row.get("coelution_root_layer"))] += _int(
            row,
            "row_count",
        )
    if layer_counter:
        top_layer, top_count = layer_counter.most_common(1)[0]
        observations.append(
            f"Largest assembly diagnosis layer is {top_layer} ({top_count} rows)."
        )
    primary_counts = ratio_payload.get("primary_row_count_by_ratio")
    has_primary_rows = isinstance(primary_counts, Mapping) and any(
        isinstance(value, int) and value > 0 for value in primary_counts.values()
    )
    if not has_primary_rows:
        observations.append(
            "Primary ratio population is empty in this synthesis input."
        )
    return observations


def _render_markdown(summary: Mapping[str, object]) -> str:
    ratio = _mapping(summary.get("ratio"))
    interference = _mapping(summary.get("interference"))
    plus1 = _mapping(summary.get("plus1_seed"))
    inclusion = _mapping(summary.get("inclusion_list"))
    top102 = _mapping(inclusion.get("top102"))
    manual_known = _mapping(inclusion.get("manual_known"))
    plus1_25k = _mapping(plus1.get("threshold_25000"))
    plus1_100k = _mapping(plus1.get("threshold_100000"))
    key_ratios = _mapping(ratio.get("key_ratios"))
    interference_available = interference.get("available") is True
    plus1_available = plus1.get("available") is True
    inclusion_available = inclusion.get("available") is True
    top102_misses = _int_value(
        top102,
        "features_all4_exist_but_no_candidate",
    )
    manual_known_misses = _int_value(
        manual_known,
        "features_all4_exist_but_no_candidate",
    )
    if inclusion_available and top102_misses + manual_known_misses > 0:
        conclusion = (
            "目前輸入顯示 candidate assembly 有可重現漏失；Ratio 只能當人工 "
            "review 與假說解釋訊號，不能直接升級成 hard gate。"
        )
    elif interference_available:
        conclusion = (
            "目前有 RT/interference 訊號，但缺少足夠 assembly evidence 可宣告 "
            "root cause；Ratio 仍只能當 diagnostic signal。"
        )
    else:
        conclusion = "目前只有 ratio artifact；可以比較分布，但不能判定主要漏失來源。"
    evidence_lines: list[str] = []
    if interference_available:
        evidence_lines.append(
            "- 13 分鐘附近的輸入 evidence："
            f"{_int_value(interference, 'rt_12p5_13p5_accept_count')} / "
            f"{_int_value(interference, 'input_accept_count')} 個 accepted "
            "落在 12.5-13.5 min，"
            f"其中 {_int_value(interference, 'rt_12p5_13p5_overlap_candidate_count')} "
            "個有 overlap candidate。"
        )
    else:
        evidence_lines.append("- 未提供 interference artifact；RT hotspot 未知。")
    if plus1_available:
        evidence_lines.append(
            "- +1 intensity threshold artifact：25k recovered "
            f"{_int_value(plus1_25k, 'current_146_accept_recovered')}；"
            "100k recovered "
            f"{_int_value(plus1_100k, 'current_146_accept_recovered')}。"
        )
    else:
        evidence_lines.append("- 未提供 +1 threshold artifact；sensitivity 未知。")
    if inclusion_available:
        evidence_lines.append(
            f"- assembly artifact：Top102 有 {top102_misses} 筆、manual known "
            f"有 {manual_known_misses} 筆四個位置都在但沒有組成 candidate。"
        )
    else:
        evidence_lines.append("- 未提供 inclusion artifact；assembly 漏失數未知。")
    lines = [
        "# Pt Pattern 現況判讀",
        "",
        "## 一句話結論",
        "",
        conclusion,
        "",
        "## 目前能相信什麼",
        "",
        *evidence_lines,
        "",
        "## 目前不能相信什麼",
        "",
        "- 不能說 FeatureHunter ratio 公式就是正確公式；它只能當舊方法 comparator。",
        "- 不能說 accepted ratio 就等於理論 isotope envelope 正確，"
        "因為 13 分鐘 overlap 很可能污染比例。",
        "- 不能用目前 feature table 結果直接反推化學結論，"
        "因為 assembly 層可能已經漏掉東西。",
        "",
        "## Ratio 公式目前怎麼用",
        "",
        "| 問題 | 先看的 ratio | accepted median | background median | 解讀 |",
        "|---|---:|---:|---:|---|",
        _ratio_brief_row(
            key_ratios,
            "plus1_over_base_height_ratio",
            "+1 是否比 base 更像主峰",
            "+1/base height",
            "可讀，但 primary/background 接近時不能當 gate。",
        ),
        _ratio_brief_row(
            key_ratios,
            "plus1_over_plus2_height_ratio",
            "+2 是否支持同一 envelope",
            "+1/+2 height",
            "偏離時要先查 overlap 或 grouping。",
        ),
        _ratio_brief_row(
            key_ratios,
            "plus1_over_plus4_height_ratio",
            "+4 是否掉鏈子或受污染",
            "+1/+4 height",
            "+4 最敏感，適合提示 review。",
        ),
        _ratio_brief_row(
            key_ratios,
            "base_over_plus1_height_ratio",
            "base 放分子的 reciprocal view",
            "base/+1 height",
            "保留討論，不當權威公式。",
        ),
        "",
        "## 建議下一步",
        "",
        (
            "先查 candidate assembly/search 的具體斷點。"
            if inclusion_available and top102_misses + manual_known_misses > 0
            else (
                "先補一個能區分 assembly、interference 與 ratio 假說的 "
                "bounded artifact。"
            )
        ),
        "",
        "## Scope",
        "",
        f"- readiness: `{summary.get('readiness_tier')}`",
        "- no RAW opens",
        "- no Matrix / counted detection / export / detector rule change",
        "- detailed tables stay in CSV/JSON sidecars",
    ]
    return "\n".join(lines)


def _ratio_brief_row(
    key_ratios: Mapping[str, object],
    ratio_name: str,
    question: str,
    display_ratio: str,
    interpretation: str,
) -> str:
    payload = _mapping(key_ratios.get(ratio_name))
    return (
        f"| {question} | `{display_ratio}` | "
        f"{_format_ratio_value(payload.get('primary_median'))} | "
        f"{_format_ratio_value(payload.get('background_median'))} | "
        f"{interpretation} |"
    )


def _format_ratio_value(value: object) -> str:
    number = optional_float(value)
    if number is None:
        return "n/a"
    return f"{number:.4g}"


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _int_value(row: Mapping[str, object], field: str) -> int:
    value = optional_float(row.get(field))
    return int(value) if value is not None else 0


def _input_provenance(**paths: Path | None) -> dict[str, dict[str, str]]:
    provenance: dict[str, dict[str, str]] = {}
    for label, path in paths.items():
        if path is None:
            continue
        provenance[label] = {
            "path": str(path),
            "sha256": file_sha256(path),
        }
    return provenance


def _all_four_features_exist(row: Mapping[str, str]) -> bool:
    return all(
        _int(row, column) > 0
        for column in (
            "feature_base_count_20ppm",
            "feature_plus1_count_20ppm",
            "feature_plus2_count_20ppm",
            "feature_plus4_count_20ppm",
        )
    )


def _diagnostic_note(row: Mapping[str, str]) -> str:
    status = text_value(row.get("pipeline_status"))
    layer = text_value(row.get("coelution_root_layer"))
    if status == "candidate_rejected":
        reason = text_value(row.get("candidate_reject_reason_if_any"))
        return f"candidate exists but trace rule rejected: {reason or 'unspecified'}"
    if _all_four_features_exist(row):
        return "four feature positions exist nearby but assembly did not accept"
    if layer:
        return layer
    return "not accepted under current diagnostic inputs"


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


def _int(row: Mapping[str, object], field: str) -> int:
    return _required_nonnegative_int(row, field, label="assembly row")


def format_value(value: object) -> str:
    """Public value formatter for tests and wrappers."""

    return format_diagnostic_value(value)
