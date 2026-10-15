"""Plain-language presentation of owner-issued product decisions and evidence.

A saved decision is described by the evidence it rests on, so a reader can test
that evidence. Under CID, a missing neutral loss or MS2 scan is a given fact
rather than evidence to revisit; it is never offered as the reason.
"""

from __future__ import annotations

from collections.abc import Mapping

MISSING_DIRECT_NL_NOT_COUNTED = frozenset(
    {
        "cid_nl_method_not_supported",
        "missing_ms2_policy_not_counted",
        "istd_nl_fail_without_dropout_support",
    }
)
MISSING_DIRECT_NL_REVIEW = frozenset(
    {
        "plausible_nl_dropout_review",
        "plausible_dda_nl_dropout",
        "missing_ms2_not_observed",
        "missing_nl_support_review",
    }
)
_SHAPE_REVIEW = frozenset({"physical_shoulder_overlap", "trace_morphology_review"})
# Restates that no decisive evidence exists; the decision basis already says so.
_RESTATED_REVIEW = frozenset({"insufficient_typed_evidence"})

_REASON_TEXT = {
    "paired_istd_position_mismatch": "峰偏離配對 ISTD 位置",
    "targeted_rt_conflict": "峰位置與 RT 參考或範圍衝突",
    "targeted_rt_review": "RT 位置需檢查",
    "anchor_conflict": "峰 RT 與 ISTD 錨點不一致",
    "hard_nl_conflict": "預期的中性丟失與所選峰衝突",
    "hard_local_quality_conflict": "局部峰品質需檢查",
    "trace_morphology_conflict": "XIC 峰形或連續性需檢查",
    "hard_quality_flag_conflict": "XIC 有重大品質旗標",
    "trace_morphology_review": "XIC 峰形或連續性需檢查",
    "physical_shoulder_overlap": "有肩峰或重疊",
    "chrom_peak_segment_shoulder_review": "所選峰旁有肩峰",
    "legacy_confidence_review": "信心分級偏低",
    "selected_envelope_split_supported": "有多個峰段，邊界需檢查",
    "selected_envelope_boundary_externalize": "峰或積分邊界未定",
    "missing_positive_ms1_peak": "沒有 MS1 峰",
    "local_sn_minor": "局部訊噪比偏弱",
    "sample_review": "此樣本需檢查",
    "insufficient_typed_evidence": "沒有足夠的判定證據",
    "paired_istd_rt_review": "RT 需與配對 ISTD 比對",
    "role_aware_istd_expected_present": "此樣本應有 ISTD",
    "file_error": "RAW 檔無法處理",
}

_RT_CONCERN_TEXT = {
    "rt_prior_far": "峰 RT 離參考值太遠",
    "rt_prior_borderline": "峰 RT 接近參考值容許邊界",
    "rt_centrality_poor": "峰離 RT 範圍中心太遠",
    "rt_centrality_borderline": "峰偏離 RT 範圍中心（邊界）",
}


def reasons(row: Mapping[str, object], field: str) -> set[str]:
    return set(str(row.get(field) or "").replace(";", " ").split())


def unqualified_ms1_shape(row: Mapping[str, object]) -> bool:
    """Present saved peak qualification, without re-evaluating the trace."""
    support = reasons(row, "Projection Support Reasons")
    return (
        str(row.get("Counted Detection", "")).upper() == "FALSE"
        and "ms1_peak_present" in support
        and "ms1_coherent" not in support
        and "cross_sample_backfill_localization" not in support
        and bool(reasons(row, "Projection Review Reasons") & _SHAPE_REVIEW)
    )


def decision_basis(row: Mapping[str, object]) -> str:
    """Name the evidence the saved decision rests on, for a reader to test."""
    support = reasons(row, "Projection Support Reasons")
    not_counted = reasons(row, "Projection Not Counted Reasons")
    if str(row.get("Counted Detection", "")).upper() == "TRUE":
        if "cross_sample_backfill_identity" in support:
            return "由同批樣本回填確認的峰"
        if "paired_istd_identity" in support:
            return "MS1 峰形合格，位於配對 ISTD 位置"
        nl = str(row.get("NL", ""))
        if nl == "OK" or nl.startswith("WARN_"):
            return "MS2 見到預期的中性丟失"
        if "ms1_coherent" in support:
            return "MS1 峰形合格"
        if "role_aware_rt_support" in support:
            return "MS1 峰位於預期 RT"
        if not nl:
            return "有 MS1 峰；此目標不要求中性丟失"
        return "有 MS1 峰"
    if "missing_positive_ms1_peak" in not_counted:
        return "沒有 MS1 峰"
    if row.get("Projection Reason") == "analyte_peak_off_paired_istd_position":
        return "峰不在配對 ISTD 位置"
    if unqualified_ms1_shape(row):
        review = reasons(row, "Projection Review Reasons")
        shoulder = "physical_shoulder_overlap" in review
        failed = "肩峰／重疊" if shoulder else "形狀不連續或不完整"
        return f"MS1 峰形未通過：{failed}"
    if not_counted and not_counted <= MISSING_DIRECT_NL_NOT_COUNTED:
        if str(row.get("Role", "")).upper() == "ISTD":
            return "ISTD 峰沒有可確認身分的依據"
        if row.get("ISTD Pair"):
            return "配對 ISTD 在此樣本未計入，無法確認身分"
        return "未設定配對 ISTD，無法確認身分"
    other = sorted(not_counted - MISSING_DIRECT_NL_NOT_COUNTED)
    if other:
        return f"未計入：{reason_text(other[0])}"
    return "證據不足以計入"


def display_measurement_reason(
    row: Mapping[str, object],
    *,
    has_quantifiable_peak: bool,
    concerns: str = "",
) -> str:
    """Explain a finished result: its decisive evidence, then other concerns."""
    exclusions = sorted(reasons(row, "Projection Exclusion Reasons"))
    if exclusions:
        if any(r.startswith("target_sample_applicability:") for r in exclusions):
            return "已排除：此目標不適用這個樣本。"
        return f"已排除：{reason_text(exclusions[0])}。"
    conflicts = reasons(row, "Projection Conflict Reasons")
    if conflicts:
        first = str(row.get("Projection Conflict Reasons")).split(";")[0].strip()
        return f"證據互相矛盾：{_conflict_text(first, concerns)}。"
    if row.get("Projection Reason") == "analyte_peak_off_paired_istd_position":
        return "峰不在配對 ISTD 位置，報 ND。"
    if not has_quantifiable_peak:
        return "搜尋範圍內沒有可定量的 MS1 峰。"
    basis = decision_basis(row)
    covered = _SHAPE_REVIEW if "峰形" in basis else frozenset()
    saved = str(row.get("Projection Review Reasons") or "")
    review = [reason.strip() for reason in saved.split(";")]
    extra = [
        reason_text(reason)
        for reason in review
        if reason
        and reason not in MISSING_DIRECT_NL_REVIEW | _RESTATED_REVIEW
        and reason not in covered
    ]
    if extra:
        return f"{basis}。另需注意：{'；'.join(dict.fromkeys(extra))}。"
    return f"{basis}。"


def display_nl_evidence(token: str | None, *, configured: bool) -> str:
    """Render the configured observation; lack of support is not chemical absence."""
    if not configured:
        return "未設定"
    if token and token.startswith("WARN_"):
        return "已觀察到（質量偏差偏大）"
    return {
        "OK": "已觀察到",
        "WARN": "已觀察到（質量偏差偏大）",
        "NL_FAIL": "所選峰未見預期的中性丟失",
        "NO_MS2": "所選峰沒有對應的 MS2",
        "ERROR": "處理錯誤",
    }.get(token or "", "無資料")


def reason_text(reason: str) -> str:
    return _REASON_TEXT.get(reason, reason.replace("_", " "))


def _conflict_text(reason: str, concerns: str) -> str:
    if reason == "targeted_rt_conflict":
        tokens = frozenset(part.strip() for part in concerns.split(";"))
        for token, text in _RT_CONCERN_TEXT.items():
            if token in tokens:
                return text
    return reason_text(reason)
