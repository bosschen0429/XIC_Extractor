"""No-RAW synthesis for Pt pattern ratio and assembly diagnostics."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.discovery.pt_pattern_synthesis_report import (
    render_hypothesis_synthesis_html,
)
from xic_extractor.tabular_io import (
    file_sha256,
    format_diagnostic_value,
    optional_float,
    read_delimited_rows,
    split_semicolon_labels,
    text_value,
    write_delimited_rows,
)

SCHEMA_VERSION = "pt_pattern_hypothesis_synthesis.v1"
READINESS_TIER = "diagnostic_only"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_hypothesis_synthesis"
AUTHORITY_SCOPE = "hypothesis_synthesis_only"

RATIO_SUMMARY_REQUIRED_COLUMNS = (
    "population_label",
    "ratio_name",
    "row_count",
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
    interference = (
        json.loads(interference_summary_json.read_text(encoding="utf-8"))
        if interference_summary_json is not None
        else {}
    )
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
    top102_rows = _read_optional_assembly(top102_diagnosis_csv)
    known_rows = _read_optional_known(known_diagnosis_csv)
    assembly_rows = [*top102_rows, *known_rows]

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


def _read_optional_assembly(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    return read_delimited_rows(
        path,
        required_columns=ASSEMBLY_DIAGNOSIS_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )


def _read_optional_known(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    try:
        return read_delimited_rows(
            path,
            required_columns=ASSEMBLY_DIAGNOSIS_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
    except ValueError:
        raw_rows = read_delimited_rows(
            path,
            required_columns=KNOWN_DIAGNOSIS_REQUIRED_COLUMNS,
            delimiter=",",
            encoding="utf-8-sig",
        )
        return [_normalize_known_row(row) for row in raw_rows]


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
    primary_n = _first_positive_row_count(primary_rows)
    background_n = _first_positive_row_count(background_rows)
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
            "primary_outlier_count": optional_float(primary.get("outlier_count"))
            if primary
            else None,
            "background_median": optional_float(background.get("median"))
            if background
            else None,
        }
    return {
        "primary_row_count": primary_n,
        "background_row_count": background_n,
        "key_ratios": key_ratios,
        "interpretation": (
            "Multiple formula families are compared side-by-side; no ratio "
            "family is detector authority in this slice."
        ),
    }


def _first_positive_row_count(rows: Sequence[Mapping[str, str]]) -> int:
    for row in rows:
        count = optional_float(row.get("row_count"))
        if count is not None and count > 0:
            return int(count)
    return 0


def _interference_payload(interference: Mapping[str, object]) -> dict[str, object]:
    largest_value = interference.get("largest_clusters")
    largest = (
        list(largest_value)
        if isinstance(largest_value, Sequence)
        and not isinstance(largest_value, (str, bytes))
        else []
    )
    largest_cluster = (
        largest[0]
        if largest and isinstance(largest[0], Mapping)
        else {}
    )
    input_accept_count = optional_float(interference.get("input_accept_count")) or 0
    rt_accept_count = optional_float(interference.get("rt_12p5_13p5_accept_count"))
    overlap_count = optional_float(
        interference.get("rt_12p5_13p5_overlap_candidate_count")
    )
    return {
        "input_accept_count": int(input_accept_count),
        "rt_12p5_13p5_accept_count": int(rt_accept_count or 0),
        "rt_12p5_13p5_overlap_candidate_count": int(overlap_count or 0),
        "base_delta_overlap_candidate_count": int(
            optional_float(interference.get("base_delta_overlap_candidate_count"))
            or 0
        ),
        "base_delta_overlap_cluster_count": int(
            optional_float(interference.get("base_delta_overlap_cluster_count")) or 0
        ),
        "ratio_above_expected_with_overlap_count": int(
            optional_float(interference.get("ratio_above_expected_with_overlap_count"))
            or 0
        ),
        "ratio_above_expected_without_overlap_count": int(
            optional_float(
                interference.get("ratio_above_expected_without_overlap_count")
            )
            or 0
        ),
        "largest_cluster_candidate_count": int(
            optional_float(largest_cluster.get("candidate_count")) or 0
        ),
        "largest_cluster_edge_count": int(
            optional_float(largest_cluster.get("edge_count")) or 0
        ),
        "largest_cluster_rt_span_sec": optional_float(
            largest_cluster.get("rt_span_sec")
        ),
        "interpretation": (
            "Dense overlap supports an interference hypothesis, but does not "
            "prove the physical mechanism."
        ),
    }


def _plus1_payload(rows: Sequence[Mapping[str, str]]) -> dict[str, object]:
    by_threshold = {
        int(optional_float(row.get("threshold_plus1_height")) or 0): row
        for row in rows
    }
    return {
        "thresholds": {
            str(threshold): _plus1_threshold_payload(row)
            for threshold, row in sorted(by_threshold.items())
        },
        "threshold_25000": _plus1_threshold_payload(by_threshold.get(25000)),
        "threshold_100000": _plus1_threshold_payload(by_threshold.get(100000)),
        "interpretation": (
            "+1 intensity is a seed/prioritization knob here, not a hard "
            "detector gate."
        ),
    }


def _plus1_threshold_payload(row: Mapping[str, str] | None) -> dict[str, object]:
    if row is None:
        return {}
    return {
        "diagnostic_full_pattern_candidates": _int(
            row,
            "diagnostic_full_pattern_candidates",
        ),
        "unique_base_count": _int(row, "unique_base_count"),
        "current_386_candidate_recovered": _int(row, "current_386_candidate_recovered"),
        "current_146_accept_recovered": _int(row, "current_146_accept_recovered"),
        "current_146_accept_recall_fraction": optional_float(
            row.get("current_146_accept_recall_fraction")
        ),
        "top102_rows_hit_by_plus1_mz": _int(row, "top102_rows_hit_by_plus1_mz"),
        "top102_recall_fraction": optional_float(row.get("top102_recall_fraction")),
        "manual_known_rows_hit_by_plus1_mz": _int(
            row,
            "manual_known_rows_hit_by_plus1_mz",
        ),
        "manual_known_recall_fraction": optional_float(
            row.get("manual_known_recall_fraction")
        ),
        "edge_to_candidate_ratio": optional_float(row.get("edge_to_candidate_ratio")),
    }


def _inclusion_payload(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        set_name = text_value(row.get("set"))
        metric = text_value(row.get("metric"))
        value = optional_float(row.get("value"))
        grouped.setdefault(set_name, {})[metric] = (
            int(value) if value is not None else 0
        )
    return grouped


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
                    if _truthy(item.get("coherent_full_pattern_exists"))
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
        row
        for row in rows
        if text_value(row.get("pipeline_status")) != "accepted_146"
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
            "list_plus1_mz": optional_float(row.get("list_plus1_mz")),
            "pipeline_status": text_value(row.get("pipeline_status")),
            "coelution_root_layer": text_value(row.get("coelution_root_layer")),
            "candidate_reject_reason_if_any": text_value(
                row.get("candidate_reject_reason_if_any")
            ),
            "feature_base_count_20ppm": _int(row, "feature_base_count_20ppm"),
            "feature_plus1_count_20ppm": _int(row, "feature_plus1_count_20ppm"),
            "feature_plus2_count_20ppm": _int(row, "feature_plus2_count_20ppm"),
            "feature_plus4_count_20ppm": _int(row, "feature_plus4_count_20ppm"),
            "coherent_full_pattern_exists": text_value(
                row.get("coherent_full_pattern_exists")
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
            f"Largest assembly diagnosis layer is {top_layer} "
            f"({top_count} rows)."
        )
    if not ratio_payload.get("primary_row_count"):
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
    lines = [
        "# Pt Pattern 現況判讀",
        "",
        "## 一句話結論",
        "",
        "目前最大嫌疑不是 ratio gate，而是 candidate assembly / feature table "
        "source。Ratio 有異常，但現在只能當人工 review 與假說解釋訊號，"
        "不能直接升級成 hard gate。",
        "",
        "## 目前能相信什麼",
        "",
        "- 13 分鐘附近不是偶發："
        f"{_int_value(interference, 'rt_12p5_13p5_accept_count')} / "
        f"{_int_value(interference, 'input_accept_count')} 個 accepted "
        "落在 12.5-13.5 min，"
        f"其中 {_int_value(interference, 'rt_12p5_13p5_overlap_candidate_count')} "
        "個有 overlap candidate。",
        f"- +1 intensity 比較像 seed knob：25k 保留 "
        f"{_int_value(plus1_25k, 'current_146_accept_recovered')} 個已接受候選；"
        f"100k 仍保留 {_int_value(plus1_100k, 'current_146_accept_recovered')} 個。",
        f"- list 對不上不全是 ratio 問題：Top102 有 "
        f"{_int_value(top102, 'features_all4_exist_but_no_candidate')} "
        "筆四個位置都在但沒有組成 candidate；"
        "manual known 有 "
        f"{_int_value(manual_known, 'features_all4_exist_but_no_candidate')} "
        "筆同類問題。",
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
        "先查 candidate assembly/search：特別是「四個 feature position "
        "都存在，但沒有組成 Pt candidate」的 rows。"
        "不要先調 ratio 門檻，否則可能把 feature table 或 assembly "
        "的錯誤包裝成化學規則。",
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
    return int(optional_float(row.get(field)) or 0)


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


def _truthy(value: object) -> bool:
    return text_value(value).lower() in {"true", "1", "yes", "y"}


def _int(row: Mapping[str, object], field: str) -> int:
    return int(optional_float(row.get(field)) or 0)


def _inline_json(value: object) -> str:
    text = json.dumps(value, sort_keys=True)
    if len(text) <= 360:
        return text
    return text[:357] + "..."


def format_value(value: object) -> str:
    """Public value formatter for tests and wrappers."""

    return format_diagnostic_value(value)
