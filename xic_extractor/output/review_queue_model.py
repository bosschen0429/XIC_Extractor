from __future__ import annotations

import re
from collections.abc import Iterable

_INFORMATIONAL_EVENTS = {
    "MULTI_PEAK",
    "ANCHOR_RT_MISMATCH",
    "ANCHOR_MISMATCH",
    "NL_ANCHOR_FALLBACK",
    "ISTD_ANCHOR_MISSING",
    "ISTD_CONFIDENCE",
    "BACKFILL_ACCEPTED",
}


def _review_queue_rows(
    rows: list[dict[str, str]],
    diagnostics: list[dict[str, str]],
) -> list[dict[str, str]]:
    diagnostics_by_key = _diagnostics_grouped_by_key(diagnostics)
    rows_by_key = _results_grouped_by_key(rows)
    queue: list[dict[str, str]] = []
    for key, key_rows in rows_by_key.items():
        row_diagnostics = diagnostics_by_key.get(key, [])
        row, issue = _primary_review_row(key_rows, row_diagnostics)
        if not issue:
            continue
        evidence = _review_evidence(row, row_diagnostics)
        queue.append(
            {
                "Priority": str(_review_priority(row, issue)),
                "Sample": row.get("SampleName", ""),
                "Target": row.get("Target", ""),
                "Role": row.get("Role", ""),
                "Status": _review_status(issue, row.get("Confidence", "")),
                "Outcome": {
                    "TRUE": "已計入",
                    "FALSE": "未計入",
                }.get(row.get("Counted Detection", "").upper(), "未記錄判定"),
                "Why": _review_why(issue, row, evidence),
                "RT": row.get("RT", ""),
                "Area": row.get("Area", ""),
                "Action": _suggested_action(issue, row, evidence),
                "Issue Count": str(len(row_diagnostics) if row_diagnostics else 1),
                "Evidence": evidence,
            }
        )
    queue.sort(
        key=lambda item: (
            int(item["Priority"]),
            item["Sample"],
            item["Target"],
            item["Why"],
        )
    )
    return queue


def _results_grouped_by_key(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    by_key: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row.get("SampleName", ""), row.get("Target", ""))
        by_key.setdefault(key, []).append(row)
    return by_key


def _diagnostics_grouped_by_key(
    diagnostics: list[dict[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    by_key: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in diagnostics:
        key = (row.get("SampleName", ""), row.get("Target", ""))
        by_key.setdefault(key, []).append(row)
    return by_key


def _primary_review_row(
    rows: list[dict[str, str]],
    diagnostics: list[dict[str, str]],
) -> tuple[dict[str, str], str]:
    ranked: list[tuple[int, int, dict[str, str], str]] = []
    for index, row in enumerate(rows):
        issue = _primary_review_issue(row, diagnostics)
        if issue:
            ranked.append((_review_priority(row, issue), index, row, issue))
    if not ranked:
        return rows[0], ""
    _, _, row, issue = min(ranked, key=lambda item: (item[0], item[1]))
    return row, issue


def _primary_review_issue(
    row: dict[str, str], diagnostics: list[dict[str, str]]
) -> str:
    if _has_product_projection(row):
        # The completed projection owns current actions. Diagnostic events can
        # explain an action, but cannot reopen a resolved/confirmed result.
        return _row_review_issue(row)
    candidates = [
        diagnostic.get("Issue", "")
        for diagnostic in diagnostics
        if diagnostic.get("Issue", "") not in _INFORMATIONAL_EVENTS
    ]
    row_issue = _row_review_issue(row)
    if row_issue:
        candidates.append(row_issue)
    candidates = [issue for issue in candidates if issue]
    if not candidates:
        return ""
    return min(
        enumerate(candidates),
        key=lambda item: (_review_priority(row, item[1]), item[0]),
    )[1]


def _review_evidence(row: dict[str, str], diagnostics: list[dict[str, str]]) -> str:
    if diagnostics:
        parts = [_diagnostic_tag(diagnostic) for diagnostic in diagnostics]
    else:
        parts = [_row_review_issue(row)]
    return "; ".join(_dedupe_text(part for part in parts if part))


def _diagnostic_tag(diagnostic: dict[str, str]) -> str:
    issue = diagnostic.get("Issue", "")
    reason = diagnostic.get("Reason", "")
    if issue in {"NL_FAIL", "NO_MS2"}:
        return issue
    if issue == "NL_ANCHOR_FALLBACK":
        return "NL_anchor_fallback"
    if issue == "ANCHOR_RT_MISMATCH":
        delta = _first_regex_group(reason, r"deviates ([0-9.]+) min")
        return f"anchor dRT={delta} min" if delta else "anchor_mismatch"
    if issue == "MULTI_PEAK":
        count = _first_regex_group(reason, r"(\d+) qualified physical candidates")
        return f"multi_peak={count}" if count else "multi_peak"
    if issue in {"PEAK_NOT_FOUND", "NO_SIGNAL"}:
        return "peak_not_found"
    return issue


def _first_regex_group(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _dedupe_text(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _row_review_issue(row: dict[str, str]) -> str:
    nl = row.get("NL", "")
    if _has_product_projection(row):
        if row.get("Projection Conflict Reasons", ""):
            return "PROJECTION_CONFLICT"
        if row.get("Projection Exclusion Reasons", ""):
            return "PRODUCT_EXCLUDED"
        product_state = row.get("Product State", "")
        if product_state == "ambiguous":
            return "PRODUCT_AMBIGUOUS"
        if row.get("Counted Detection", "").upper() == "FALSE":
            if nl in {"NL_FAIL", "NO_MS2"}:
                return nl
            return "PRODUCT_NOT_COUNTED"
        if row.get("Review State", "") in {"flagged", "review_required"}:
            return "PROJECTION_FLAGGED"
        return ""
    if nl == "NL_FAIL":
        return "NL_FAIL"
    if nl == "NO_MS2":
        return "NO_MS2"
    if nl.startswith("WARN_"):
        return "NL_WARN"
    confidence = row.get("Confidence", "")
    if confidence in {"LOW", "VERY_LOW"}:
        return f"CONFIDENCE_{confidence}"
    return ""


def _review_priority(row: dict[str, str], issue: str) -> int:
    if _has_product_projection(row):
        if issue in {
            "PEAK_NOT_FOUND",
            "NO_SIGNAL",
            "FILE_ERROR",
            "NL_FAIL",
            "PROJECTION_CONFLICT",
            "PRODUCT_EXCLUDED",
            "PRODUCT_AMBIGUOUS",
        }:
            return 1
        if issue in {"NO_MS2", "NL_WARN", "PRODUCT_NOT_COUNTED", "PROJECTION_FLAGGED"}:
            return 2
        return 3
    confidence = row.get("Confidence", "")
    if issue in {"PEAK_NOT_FOUND", "NO_SIGNAL", "FILE_ERROR", "NL_FAIL"}:
        return 1
    if confidence == "VERY_LOW":
        return 1
    if issue in {"NO_MS2", "NL_WARN"} or confidence == "LOW":
        return 2
    return 3


def _review_status(issue: str, confidence: str) -> str:
    if issue in {
        "PROJECTION_CONFLICT",
        "PRODUCT_EXCLUDED",
        "PRODUCT_AMBIGUOUS",
        "PRODUCT_NOT_COUNTED",
    }:
        return "Review"
    if issue == "PROJECTION_FLAGGED":
        return "Check"
    if issue in {"NL_FAIL", "PEAK_NOT_FOUND", "NO_SIGNAL", "FILE_ERROR"}:
        return "Review"
    if issue in {"NO_MS2", "NL_WARN"} or confidence in {"LOW", "VERY_LOW"}:
        return "Check"
    return "Info"


def _review_why(issue: str, row: dict[str, str], evidence: str) -> str:
    """Name the saved fact briefly; counting is its own column, never restated."""
    counted = row.get("Counted Detection", "").upper()
    state = row.get("Product State", "")
    if state == "excluded":
        return "已被規則排除"
    if state == "ambiguous" or row.get("Projection Conflict Reasons"):
        return "證據衝突，身分未定"
    if "analyte_peak_off_paired_istd_position" in row.get("Projection Reason", ""):
        return "峰不在內標位置，報 ND"
    if "BACKFILL_HOLD" in evidence and counted == "FALSE":
        return "回填找不到唯一的峰"
    if "BACKFILL_ACCEPTED" in evidence and counted == "FALSE":
        return "已回填定位，身分未確認"
    if issue == "NL_FAIL":
        return "MS2 未見 NL"
    if issue == "NO_MS2":
        return "沒有 MS2"
    if issue == "NL_WARN":
        return "NL 質量誤差偏大"
    if issue in {"PEAK_NOT_FOUND", "NO_SIGNAL"}:
        return "找不到峰"
    if issue == "FILE_ERROR":
        return "RAW 處理失敗"
    if issue == "MULTI_PEAK":
        return "有多個候選峰"
    if issue == "ANCHOR_RT_MISMATCH":
        return "RT 偏離定位提示"
    if issue == "NL_ANCHOR_FALLBACK":
        return "無 NL 定位點，用備用範圍"
    if issue in {"ISTD_CONFIDENCE", "CONFIDENCE_LOW", "CONFIDENCE_VERY_LOW"}:
        return "信心分級偏低"
    if issue == "PRODUCT_NOT_COUNTED":
        return "未滿足計入條件"
    if issue == "PROJECTION_FLAGGED":
        reasons = row.get("Projection Review Reasons", "")
        if "physical_shoulder_overlap" in reasons:
            return "肩峰重疊需檢查"
        if "trace_morphology_review" in reasons:
            return "峰形需檢查"
        if "targeted_rt_review" in reasons:
            return "RT 位置需檢查"
        return "證據需檢查"
    return "有補充診斷紀錄"


def _has_product_projection(row: dict[str, str]) -> bool:
    return row.get("Counted Detection", "").upper() in {"TRUE", "FALSE"} and bool(
        row.get("Product State", "")
    )


def _suggested_action(issue: str, row: dict[str, str], evidence: str) -> str:
    if "BACKFILL" in evidence:
        return "比較同批樣本的回填位置與峰形"
    if issue in {"NL_FAIL", "NL_WARN"}:
        return "看所選 RT 附近的 MS2"
    if issue == "NO_MS2":
        return "看 DDA 掃描涵蓋範圍"
    if issue in {"PEAK_NOT_FOUND", "NO_SIGNAL"}:
        return "看 XIC 與 RT 範圍"
    if issue == "FILE_ERROR":
        return "看原始錯誤與 RAW 路徑"
    if issue in {
        "PROJECTION_CONFLICT",
        "PRODUCT_AMBIGUOUS",
        "PRODUCT_NOT_COUNTED",
        "PROJECTION_FLAGGED",
    }:
        return "看此峰的證據"
    if issue == "PRODUCT_EXCLUDED":
        return "核對適用範圍"
    if row.get("Confidence") in {"LOW", "VERY_LOW"}:
        return "看峰形、RT 與 ISTD"
    if issue == "MULTI_PEAK":
        return "比較候選峰"
    return "看原始紀錄"
