from __future__ import annotations

import re
from collections.abc import Iterable

from xic_extractor.output.workbook_values import _FORMULA_PREFIXES


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
                "Why": _review_why(
                    issue,
                    row.get("Projection Reason", "") or row.get("Reason", ""),
                    evidence,
                ),
                "RT": row.get("RT", ""),
                "Area": row.get("Area", ""),
                "Action": _suggested_action(issue, row),
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
        count = _first_regex_group(reason, r"(\d+) prominent peaks")
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


def _review_why(issue: str, reason: str, evidence: str) -> str:
    if issue == "NL_FAIL":
        return "NL support failed"
    if issue == "NO_MS2":
        return "MS2 trigger missing"
    if issue == "NL_WARN":
        return "NL support is borderline"
    if issue == "PROJECTION_CONFLICT":
        return (
            _first_projection_reason(reason, "conflict") or "Product evidence conflicts"
        )
    if issue == "PRODUCT_EXCLUDED":
        return _first_projection_reason(reason, "excluded") or "Product row excluded"
    if issue == "PRODUCT_AMBIGUOUS":
        return "Product evidence is ambiguous"
    if issue == "PRODUCT_NOT_COUNTED":
        return (
            _first_projection_reason(reason, "not_counted") or "Product row not counted"
        )
    if issue == "PROJECTION_FLAGGED":
        return (
            _first_projection_reason(reason, "review") or "Product projection flagged"
        )
    if issue in {"PEAK_NOT_FOUND", "NO_SIGNAL"}:
        return "Peak not found"
    if issue.startswith("CONFIDENCE_"):
        parsed = _first_concern(reason)
        if parsed and parsed != "all checks passed":
            return parsed
        return issue.removeprefix("CONFIDENCE_") + " confidence"
    parsed = _first_concern(reason) or _first_concern(evidence)
    return parsed if parsed and parsed != "all checks passed" else issue


def _has_product_projection(row: dict[str, str]) -> bool:
    return row.get("Counted Detection", "").upper() in {"TRUE", "FALSE"} and bool(
        row.get("Product State", "")
    )


def _first_projection_reason(reason: str, section: str) -> str:
    marker = f"{section}:"
    for part in reason.split(";"):
        stripped = part.strip()
        if not stripped.startswith(marker):
            continue
        return stripped.removeprefix(marker).strip().split(",", 1)[0].strip()
    return ""


def _first_concern(text: str) -> str:
    if not text:
        return ""
    if text.startswith(_FORMULA_PREFIXES):
        return ""
    normalized = text.removeprefix("concerns:").strip()
    first = normalized.split(";", 1)[0].strip()
    if first.startswith("weak candidate:"):
        first = first.removeprefix("weak candidate:").strip()
    return first


def _suggested_action(issue: str, row: dict[str, str]) -> str:
    if issue in {"NL_FAIL", "NL_WARN"}:
        return "Check MS2 / NL evidence near selected RT"
    if issue == "NO_MS2":
        return "Check whether missing DDA trigger is acceptable"
    if issue in {"PEAK_NOT_FOUND", "NO_SIGNAL"}:
        return "Inspect XIC trace and target RT window"
    if issue == "FILE_ERROR":
        return "Check RAW file readability"
    if issue in {
        "PROJECTION_CONFLICT",
        "PRODUCT_AMBIGUOUS",
        "PRODUCT_NOT_COUNTED",
        "PROJECTION_FLAGGED",
    }:
        return "Review product projection evidence"
    if issue == "PRODUCT_EXCLUDED":
        return "Review exclusion reason"
    if row.get("Confidence") in {"LOW", "VERY_LOW"}:
        return "Review peak shape, RT prior, and ISTD pairing"
    return "Review diagnostic detail"
