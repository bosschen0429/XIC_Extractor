from __future__ import annotations

import re
from collections.abc import Iterable


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
    candidates = [diagnostic.get("Issue", "") for diagnostic in diagnostics]
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
    if nl == "NL_FAIL":
        return "NL_FAIL"
    if nl == "NO_MS2":
        return "NO_MS2"
    if nl.startswith("WARN_"):
        return "NL_WARN"
    if _has_product_projection(row):
        if row.get("Projection Conflict Reasons", ""):
            return "PROJECTION_CONFLICT"
        if row.get("Projection Exclusion Reasons", ""):
            return "PRODUCT_EXCLUDED"
        product_state = row.get("Product State", "")
        if product_state == "ambiguous":
            return "PRODUCT_AMBIGUOUS"
        if row.get("Projection Not Counted Reasons", ""):
            return "PRODUCT_NOT_COUNTED"
        if row.get("Review State", "") in {"flagged", "review_required"}:
            return "PROJECTION_FLAGGED"
        return ""
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
    """Explain saved facts; event labels never create a counting decision."""
    counted = row.get("Counted Detection", "").upper()
    state = row.get("Product State", "")
    if state == "excluded":
        return "此筆已被分析規則排除，未計入偵測。"
    if state == "ambiguous" or row.get("Projection Conflict Reasons"):
        return "此筆仍有未解決的證據衝突，尚未確認峰的身份。"
    if counted == "TRUE" and "cross_sample_backfill_identity" in row.get(
        "Projection Support Reasons", ""
    ):
        return "未取得符合條件的直接 NL 證據；跨樣本回填已確認此峰，已計入偵測。"
    if "BACKFILL_HOLD" in evidence and counted == "FALSE":
        return "跨樣本回填未找到可唯一確認的峰；保留原量測，但仍未計入偵測。"
    if "BACKFILL_ACCEPTED" in evidence and counted == "FALSE":
        return "已取得回填量測，但尚未滿足身份確認條件，仍未計入偵測。"
    if issue == "NL_FAIL":
        return "未取得符合條件的 NL 證據。量測是否計入，以已保存的判定為準。"
    if issue == "NO_MS2":
        return "此峰未取得可用的 MS2 掃描，無法由直接 NL 證據確認。"
    if issue == "NL_WARN":
        return "已觀測到 NL，但質量誤差達到提醒範圍，需檢查 MS2 證據。"
    if issue in {"PEAK_NOT_FOUND", "NO_SIGNAL"}:
        return "搜尋範圍內未取得可報告的峰量測。"
    if issue == "FILE_ERROR":
        return "此檔案處理失敗，結果可能缺漏；請先檢查 RAW 與原始錯誤紀錄。"
    if issue == "MULTI_PEAK":
        return "搜尋範圍內有多個候選峰；本表保留正式流程已選定的峰。"
    if issue == "ANCHOR_RT_MISMATCH":
        return "所選峰與 RT 定位提示不同；這是檢閱註記，不會單獨推翻已保存的判定。"
    if issue == "NL_ANCHOR_FALLBACK":
        return "未取得可用的 NL 定位點，因此使用備用 RT 搜尋範圍。"
    if issue in {"ISTD_CONFIDENCE", "CONFIDENCE_LOW", "CONFIDENCE_VERY_LOW"}:
        return "信心分級偏低，需檢查峰形與證據；信心分級不等於是否計入偵測。"
    if issue == "PRODUCT_NOT_COUNTED":
        return "此筆未滿足計入條件；保留可用量測供檢閱，不視為已確認偵測。"
    if issue == "PROJECTION_FLAGGED":
        reasons = row.get("Projection Review Reasons", "")
        if "trace_morphology_review" in reasons:
            return "峰形或訊號品質有檢閱註記；量測與計數仍沿用正式結果。"
        if "targeted_rt_review" in reasons:
            return "所選峰的 RT 與定位提示不同，請檢查峰的位置與同批樣本。"
        return "此筆結果帶有檢閱註記，請連同峰形及原始證據查看。"
    return "此筆有補充診斷紀錄；完整內容保留在下方原始紀錄區。"


def _has_product_projection(row: dict[str, str]) -> bool:
    return row.get("Counted Detection", "").upper() in {"TRUE", "FALSE"} and bool(
        row.get("Product State", "")
    )


def _suggested_action(issue: str, row: dict[str, str], evidence: str) -> str:
    if "BACKFILL" in evidence:
        return "在 Inspector 比較此峰與同批支持樣本，查看回填定位及峰形。"
    if issue in {"NL_FAIL", "NL_WARN"}:
        return "在 Inspector 檢查所選 RT 附近的 MS2／NL 證據。"
    if issue == "NO_MS2":
        return "檢查 DDA 掃描涵蓋範圍；不要將缺少 MS2 視為已確認。"
    if issue in {"PEAK_NOT_FOUND", "NO_SIGNAL"}:
        return "檢查 XIC 訊號及 RT 搜尋範圍。"
    if issue == "FILE_ERROR":
        return "查看原始錯誤，確認 RAW 可讀及來源路徑正確。"
    if issue in {
        "PROJECTION_CONFLICT",
        "PRODUCT_AMBIGUOUS",
        "PRODUCT_NOT_COUNTED",
        "PROJECTION_FLAGGED",
    }:
        return "在 Inspector 檢查此峰的證據與已保存的判定原因。"
    if issue == "PRODUCT_EXCLUDED":
        return "核對目標適用範圍及排除原因。"
    if row.get("Confidence") in {"LOW", "VERY_LOW"}:
        return "檢查峰形、RT 提示與 ISTD 配對。"
    if issue == "MULTI_PEAK":
        return "如需追查選峰，在 Inspector 比較候選峰與直接方法證據。"
    return "如需追查細節，查看此筆原始紀錄。"
