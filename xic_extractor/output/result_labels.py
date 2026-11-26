"""Fixed labels that explain a saved Targeted result.

A label names an observed fact; its column gives its role. `依據` holds the one
fact the saved decision rests on, `疑點` the facts left uncertain. The same
fact is the same word in either column and is never listed twice on one row.
Labels are never combined into sentences; exact values stay on `診斷事件`.

Writers derive both columns once from the saved projection. Every reader
(accent colour, `摘要`, Workbench) reads the two saved strings, so this module
stays free of workbook and peak-detection imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import unquote

BASIS_NEEDS_LOOK: dict[str, bool | None] = {
    "NL 確認": False,
    "NL 偏差大": True,
    "ISTD 位置": False,
    "特異性條件": False,
    "回填": False,
    "僅 MS1 峰": False,
    "無可用 MS1 峰": None,
    "偏離 ISTD 位置": False,
    "峰形不完整": True,
    "ISTD 未計入": True,
    "未見 NL": True,
    "無 MS2": True,
    "不符特異性條件": True,
    "不足以定量": True,
    "證據衝突": True,
    "共用離子區段": True,
    "RAW 失敗": True,
}
DOUBT_LABELS: tuple[str, ...] = (
    "未見 NL",
    "無 MS2",
    "NL 偏差大",
    "峰形不完整",
    "訊號缺口",
    "多個候選峰",
    "RT 偏移",
    "共用離子區段",
    "證據單薄",
)
DOUBT_SEPARATOR = "、"
_PARTNER_SEPARATOR = " / "

SHARED_ION_REGION = "targeted_shared_ion_region"
_NL_FAMILY = "NL"
REVIEW_LABELS: dict[str, str | None] = {
    "plausible_nl_dropout_review": _NL_FAMILY,
    "missing_nl_support_review": _NL_FAMILY,
    "missing_ms2_not_observed": _NL_FAMILY,
    "trace_morphology_review": "峰形不完整",
    "targeted_acquired_signal_gaps": "訊號缺口",
    "targeted_competing_regions": "多個候選峰",
    "targeted_reference_displacement": "RT 偏移",
    "targeted_retention_transfer_uncertain": "RT 偏移",
    "targeted_retention_context_unavailable": "RT 偏移",
    SHARED_ION_REGION: "共用離子區段",
    "insufficient_typed_evidence": "證據單薄",
    "chrom_peak_segment_requires_correlated_evidence": "證據單薄",
    "targeted_cross_sample_region_conflict": None,
    "targeted_no_plausible_measurable_region": None,
}
CONFLICT_CODES = frozenset(
    {
        "ambiguous_evidence",
        "targeted_cross_sample_region_conflict",
        SHARED_ION_REGION,
    }
)
MISSING_NL_NOT_COUNTED = frozenset(
    {"cid_nl_method_not_supported", "missing_ms2_policy_not_counted"}
)
NOT_COUNTED_CODES = MISSING_NL_NOT_COUNTED | {
    "missing_positive_ms1_peak",
    "identification_method_not_supported",
}
OFF_PAIRED_ISTD_POSITION = "analyte_peak_off_paired_istd_position"
SEARCHED_NO_OBSERVATION = "finalized_searched_no_observation"
NOT_QUANTIFIABLE = "finalized_not_quantifiable"
_COHERENT_MS1_PEAK = frozenset({"ms1_peak_present", "ms1_coherent", "trace_coherent"})
_BACKFILL_LOCALIZATION = "cross_sample_backfill_localization"
BASIS_SUPPORT_CODES = _COHERENT_MS1_PEAK | {
    _BACKFILL_LOCALIZATION,
    "identification_method_supported",
    "cross_sample_backfill_identity",
    "paired_istd_identity",
}
HIDDEN_SUPPORT_CODES = frozenset(
    {
        "role_aware_rt_support",
        "paired_istd_anchor_support",
        "candidate_aligned_ms2_nl",
        "paired_area_ratio_support",
        "chrom_peak_segment_context",
    }
)
_MISSING_MS2_CODES = frozenset(
    {"missing_ms2_not_observed", "missing_ms2_policy_not_counted"}
)
_NL_BASES = frozenset({"NL 確認", "NL 偏差大", "未見 NL", "無 MS2", "ISTD 未計入"})


def basis_label(row: Mapping[str, object]) -> str:
    """Name the one fact the saved decision rests on."""
    conflicts = _codes(row, "Projection Conflict Reasons")
    if SHARED_ION_REGION in conflicts:
        return _shared_ion_label(row)
    if conflicts:
        return "證據衝突"
    reason = str(row.get("Projection Reason") or "")
    if reason == NOT_QUANTIFIABLE:
        return "不足以定量"
    support = _codes(row, "Projection Support Reasons")
    not_counted = _codes(row, "Projection Not Counted Reasons")
    if "identification_method_not_supported" in not_counted:
        return "不符特異性條件"
    nl = str(row.get("NL") or "")
    if str(row.get("Counted Detection", "")).upper() == "TRUE":
        if "identification_method_supported" in support:
            return "特異性條件"
        if "cross_sample_backfill_identity" in support:
            return "回填"
        if "paired_istd_identity" in support:
            return "ISTD 位置"
        if nl == "OK":
            return "NL 確認"
        if nl.startswith("WARN"):
            return "NL 偏差大"
        return "僅 MS1 峰"
    if reason == SEARCHED_NO_OBSERVATION or "missing_positive_ms1_peak" in not_counted:
        return "無可用 MS1 峰"
    if reason == OFF_PAIRED_ISTD_POSITION:
        return "偏離 ISTD 位置"
    qualified = _COHERENT_MS1_PEAK <= support or _BACKFILL_LOCALIZATION in support
    review = _codes(row, "Projection Review Reasons")
    if (
        "ms1_peak_present" in support
        and not qualified
        and "trace_morphology_review" in review
    ):
        return "峰形不完整"
    if not_counted and not_counted <= MISSING_NL_NOT_COUNTED:
        if str(row.get("Role", "")).upper() == "ISTD" or not row.get("ISTD Pair"):
            return _missing_nl_label(nl, not_counted)
        return "ISTD 未計入" if qualified else "峰形不完整"
    raise ValueError(f"No 依據 label for saved decision: {reason or not_counted}")


def doubt_labels(row: Mapping[str, object], basis: str) -> tuple[str, ...]:
    """Name the saved facts left uncertain about the reported peak.

    A row without a reported peak has nothing left to doubt: its 依據 already
    says why no value was reported.
    """
    if not _has_reported_peak(row):
        return ()
    nl = str(row.get("NL") or "")
    found = {
        label
        for code in _codes(row, "Projection Not Counted Reasons")
        & MISSING_NL_NOT_COUNTED
        if (label := _nl_doubt_label(nl, code)) is not None
    }
    if nl.startswith("WARN"):
        found.add("NL 偏差大")
    shared = ""
    for code in _codes(row, "Projection Review Reasons", keep_parameters=True):
        name, _, partners = code.partition(":")
        if name not in REVIEW_LABELS:
            raise ValueError(f"No 疑點 label for saved review code: {name}")
        label = REVIEW_LABELS[name]
        if label == _NL_FAMILY:
            label = _nl_doubt_label(nl, name)
        elif label == "共用離子區段":
            shared = _shared_ion_text(partners)
        if label is not None:
            found.add(label)
    if basis in _NL_BASES:
        found -= {"未見 NL", "無 MS2", "NL 偏差大"}
    found.discard(_label_name(basis))
    return tuple(
        shared if label == "共用離子區段" and shared else label
        for label in DOUBT_LABELS
        if label in found
    )


def result_labels(row: Mapping[str, object]) -> tuple[str, str]:
    """Return the saved `依據` and `疑點` strings for one long row."""
    basis = basis_label(row)
    return basis, DOUBT_SEPARATOR.join(doubt_labels(row, basis))


def needs_look(basis: object, doubts: object, *, is_istd: bool) -> bool:
    """A row needs a look when its 依據 is marked so or any 疑點 is saved."""
    if str(doubts or "").strip():
        return True
    marked = BASIS_NEEDS_LOOK.get(_label_name(str(basis or "")), True)
    return is_istd if marked is None else marked


def basis_needs_look(basis: object, *, is_istd: bool) -> bool:
    """Whether the 依據 cell itself carries a label marked for a look."""
    return needs_look(basis, "", is_istd=is_istd)


def _label_name(label: str) -> str:
    """`共用離子區段（A / B）` is the parameterised `共用離子區段`."""
    return label.partition("（")[0]


def _shared_ion_label(row: Mapping[str, object]) -> str:
    for code in _codes(row, "Projection Review Reasons", keep_parameters=True):
        name, _, partners = code.partition(":")
        if name == SHARED_ION_REGION and partners:
            return _shared_ion_text(partners)
    return "共用離子區段"


def _shared_ion_text(partners: str) -> str:
    names = _PARTNER_SEPARATOR.join(unquote(name) for name in partners.split(","))
    return f"共用離子區段（{names}）" if names else "共用離子區段"


def _missing_nl_label(nl: str, not_counted: set[str]) -> str:
    if nl.startswith("WARN"):
        return "NL 偏差大"
    if nl == "NO_MS2" or (nl != "NL_FAIL" and not_counted & _MISSING_MS2_CODES):
        return "無 MS2"
    return "未見 NL"


def _nl_doubt_label(nl: str, code: str) -> str | None:
    """The selected peak's NL observation names the doubt its code raised."""
    if nl == "OK":
        return None
    if nl.startswith("WARN"):
        return "NL 偏差大"
    if nl == "NO_MS2" or (nl != "NL_FAIL" and code in _MISSING_MS2_CODES):
        return "無 MS2"
    return "未見 NL"


def _has_reported_peak(row: Mapping[str, object]) -> bool:
    try:
        float(str(row.get("RT") or ""))
    except ValueError:
        return False
    return True


def _codes(
    row: Mapping[str, object], field: str, *, keep_parameters: bool = False
) -> set[str]:
    codes = (part.strip() for part in str(row.get(field) or "").split(";"))
    return {
        code if keep_parameters else code.partition(":")[0] for code in codes if code
    }
