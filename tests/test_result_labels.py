import re

import pytest

from scripts.check_architecture_census import (
    ROOT,
    targeted_no_write_variants,
    targeted_reason_code_literals,
)
from xic_extractor.extraction import targeted_product_projection as projection
from xic_extractor.output import result_labels as labels
from xic_extractor.output.result_labels import (
    BASIS_NEEDS_LOOK,
    DOUBT_LABELS,
    needs_look,
    result_labels,
)

# Code-shaped literals in the reason producers that are not reason codes. A new
# literal must be added to the label table or named here, so no new reason
# reaches a workbook without a label.
NOT_REASON_CODES = frozenset(
    {
        # Evidence inputs: concern, cap and support signals.
        "anchor_mismatch",
        "anchor_mismatch_cap",
        "hard_quality_flag",
        "hard_quality_flag_cap",
        "local_sn_strong",
        "low_scan_support",
        "low_trace_continuity",
        "nl_fail",
        "nl_match",
        "no_ms2",
        "no_ms2_cap",
        "noise_shape_borderline",
        "noise_shape_poor",
        "paired_area_ratio_plausible",
        "paired_istd_rt_close",
        "poor_edge_recovery",
        "rt_centrality_borderline",
        "rt_centrality_poor",
        "rt_prior_borderline",
        "rt_prior_close",
        "rt_prior_far",
        "rt_window_cap",
        "shape_borderline",
        "shape_clean",
        "shape_poor",
        "strict_nl_ok",
        "trace_clean",
        "trace_quality_cap",
        "unresolved_tie",
        # Internal consistency classes, mapped to codes before saving.
        "hard_nl_conflict",
        "missing_ms2",
        "plausible_nl_dropout",
        # Proposal and evidence-source names.
        "alignment_cell",
        "chrom_peak_segment",
        "discovery_candidate",
        "ms1_trace",
        "role_aware_rt",
        "targeted_peak",
        "trace_morphology",
        "typed_candidate_evidence_facts",
        "selected_hypothesis_decision",
        # Product states, attribute and function names.
        "detected_clean",
        "detected_flagged",
        "not_counted",
        "review_required",
        "exclusion_reasons",
        "support_reasons",
        "execute_targeted_production_workflow",
    }
)


def _labelled_codes() -> set[str]:
    return (
        set(labels.REVIEW_LABELS)
        | labels.CONFLICT_CODES
        | labels.NOT_COUNTED_CODES
        | labels.EXCLUSION_CODES
        | labels.BASIS_SUPPORT_CODES
        | labels.HIDDEN_SUPPORT_CODES
        | {labels.OFF_PAIRED_ISTD_POSITION, labels.NOT_QUANTIFIABLE}
        # Never saved: the projection drops or reroutes these.
        | projection.LOCATING_REVIEWS
        | projection.LOCATING_CONFLICTS
        | set(projection.CONFLICTS_AS_REVIEW)
    )


def test_every_produced_reason_code_has_a_label_or_is_declared_hidden():
    produced = targeted_reason_code_literals(ROOT)
    assert produced - _labelled_codes() - NOT_REASON_CODES == set()
    # The other direction finds table entries no module produces any more.
    assert _labelled_codes() - produced == set()


def test_every_finalized_no_write_variant_has_a_label():
    # The publication saves `finalized_<variant>` as the projection reason.
    assert {f"finalized_{v}" for v in targeted_no_write_variants(ROOT)} == {
        labels.SEARCHED_NO_OBSERVATION,
        labels.NOT_QUANTIFIABLE,
    }


def test_every_review_label_is_a_doubt_label():
    named = {label for label in labels.REVIEW_LABELS.values() if label}
    assert named - {"NL"} <= set(DOUBT_LABELS)
    # Each doubt has a source.
    assert set(DOUBT_LABELS) - named == {"未見 NL", "無 MS2", "NL 偏差大"}


def _row(**fields: str) -> dict[str, str]:
    row = {
        "SampleName": "S1",
        "Target": "A",
        "Role": "Analyte",
        "ISTD Pair": "",
        "RT": "9.1",
        "NL": "",
        "Counted Detection": "TRUE",
        "Projection Reason": "decision: detected_clean",
        "Projection Support Reasons": "ms1_peak_present; ms1_coherent; trace_coherent",
        "Projection Review Reasons": "",
        "Projection Conflict Reasons": "",
        "Projection Not Counted Reasons": "",
        "Projection Exclusion Reasons": "",
    }
    return row | fields


_NOT_COUNTED = {"Counted Detection": "FALSE"}
_COHERENT = "ms1_peak_present; ms1_coherent; trace_coherent"


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"NL": "OK"}, ("NL 確認", "")),
        ({"NL": "WARN_12.3ppm"}, ("NL 偏差大", "")),
        ({}, ("僅 MS1 峰", "")),
        (
            {"Projection Support Reasons": "identification_method_supported"},
            ("特異性條件", ""),
        ),
        # Identity from the paired ISTD; the missing MS2 stays a doubt.
        (
            {
                "NL": "NO_MS2",
                "Projection Support Reasons": f"{_COHERENT}; paired_istd_identity",
                "Projection Review Reasons": "missing_ms2_not_observed",
            },
            ("ISTD 位置", "無 MS2"),
        ),
        (
            {
                "Role": "ISTD",
                "NL": "NL_FAIL",
                "Projection Support Reasons": "cross_sample_backfill_identity",
                "Projection Review Reasons": "missing_nl_support_review",
            },
            ("回填", "未見 NL"),
        ),
        # A large NL mass error stays a doubt when identity came from elsewhere.
        (
            {
                "NL": "WARN_31.0ppm",
                "Projection Support Reasons": f"{_COHERENT}; paired_istd_identity",
            },
            ("ISTD 位置", "NL 偏差大"),
        ),
        (
            {
                "Role": "ISTD",
                "NL": "WARN_31.0ppm",
                "Projection Support Reasons": "cross_sample_backfill_identity",
            },
            ("回填", "NL 偏差大"),
        ),
        # Doubts read in table order, each once.
        (
            {
                "Projection Review Reasons": (
                    "targeted_reference_displacement;"
                    " targeted_acquired_signal_gaps;"
                    " targeted_retention_transfer_uncertain;"
                    " insufficient_typed_evidence"
                )
            },
            ("僅 MS1 峰", "訊號缺口、RT 偏移、證據單薄"),
        ),
        (
            {
                "Projection Review Reasons": (
                    "targeted_shared_ion_region:B%2F1,C; targeted_competing_regions"
                )
            },
            ("僅 MS1 峰", "多個候選峰、共用離子區段（B/1 / C）"),
        ),
    ],
)
def test_counted_results_name_their_identity_and_doubts(fields, expected):
    assert _labels_without_repeat(_row(**fields)) == expected


def _labels_without_repeat(row: dict[str, str]) -> tuple[str, str]:
    basis, doubts = result_labels(row)
    # An observation 依據 already names is not listed again as a doubt.
    assert basis not in doubts.split("、")
    # A reported peak whose NL was not seen cleanly always names it, so no NL
    # label means the neutral loss was seen within tolerance.
    if row["RT"] not in {"", "ND", "ERROR"} and row["NL"] not in {"", "OK"}:
        assert {basis, *doubts.split("、")} & {
            "未見 NL",
            "無 MS2",
            "NL 偏差大",
            "ISTD 未計入",
        }
    return basis, doubts


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {
                "RT": "ND",
                "Projection Reason": labels.SEARCHED_NO_OBSERVATION,
                "Projection Review Reasons": "targeted_no_plausible_measurable_region",
            },
            ("無可用 MS1 峰", ""),
        ),
        # A rejected peak is reported as ND, so its doubts are not listed.
        (
            {
                "RT": "ND",
                "Projection Reason": labels.OFF_PAIRED_ISTD_POSITION,
                "Projection Review Reasons": (
                    "plausible_nl_dropout_review; targeted_competing_regions"
                ),
                "Projection Not Counted Reasons": "cid_nl_method_not_supported",
            },
            ("偏離 ISTD 位置", ""),
        ),
        (
            {
                "RT": "",
                "Projection Reason": labels.NOT_QUANTIFIABLE,
            },
            ("不足以定量", ""),
        ),
        # The shape failed, so the paired ISTD could not lend identity.
        (
            {
                "NL": "NO_MS2",
                "ISTD Pair": "d3-A",
                "Projection Support Reasons": "ms1_peak_present; trace_coherent",
                "Projection Review Reasons": (
                    "missing_ms2_not_observed; physical_shoulder_overlap;"
                    " trace_morphology_review"
                ),
                "Projection Not Counted Reasons": "missing_ms2_policy_not_counted",
            },
            ("肩峰／重疊", "無 MS2、峰形不完整"),
        ),
        (
            {
                "NL": "NO_MS2",
                "ISTD Pair": "d3-A",
                "Projection Support Reasons": "ms1_peak_present",
                "Projection Review Reasons": "trace_morphology_review",
                "Projection Not Counted Reasons": "missing_ms2_policy_not_counted",
            },
            ("峰形不完整", "無 MS2"),
        ),
        # A qualified peak not identified by its counted ISTD: the ISTD was
        # not counted here, which already says the analyte had no NL.
        (
            {
                "NL": "NL_FAIL",
                "ISTD Pair": "d3-A",
                "Projection Review Reasons": "plausible_nl_dropout_review",
                "Projection Not Counted Reasons": "cid_nl_method_not_supported",
            },
            ("ISTD 未計入", ""),
        ),
        (
            {
                "NL": "NO_MS2",
                "Projection Review Reasons": "missing_ms2_not_observed",
                "Projection Not Counted Reasons": "missing_ms2_policy_not_counted",
            },
            ("無 MS2", ""),
        ),
        (
            {
                "Role": "ISTD",
                "NL": "NL_FAIL",
                "Projection Not Counted Reasons": "cid_nl_method_not_supported",
            },
            ("未見 NL", ""),
        ),
        (
            {"Projection Not Counted Reasons": "identification_method_not_supported"},
            ("不符特異性條件", ""),
        ),
        (
            {"Projection Conflict Reasons": "ambiguous_evidence"},
            ("證據衝突", ""),
        ),
        # Shared signal with another RT reference cannot be separated.
        (
            {
                "Projection Conflict Reasons": "targeted_shared_ion_region",
                "Projection Review Reasons": "targeted_shared_ion_region:B",
            },
            ("共用離子區段（B）", ""),
        ),
        (
            {"RT": "ERROR", "Projection Exclusion Reasons": "file_error"},
            ("RAW 失敗", ""),
        ),
    ],
)
def test_uncounted_results_name_the_fact_that_decided_them(fields, expected):
    assert _labels_without_repeat(_row(**_NOT_COUNTED, **fields)) == expected


def test_an_unknown_saved_code_fails_instead_of_disappearing():
    with pytest.raises(ValueError, match="new_review_code"):
        result_labels(_row(**{"Projection Review Reasons": "new_review_code"}))


def test_needs_look_follows_the_label_table():
    assert not needs_look("ISTD 位置", "", is_istd=False)
    assert needs_look("ISTD 位置", "無 MS2", is_istd=False)
    assert needs_look("肩峰／重疊", "", is_istd=False)
    assert needs_look("共用離子區段（B）", "", is_istd=False)
    assert not needs_look("偏離 ISTD 位置", "", is_istd=False)
    # A missing analyte is a finished ND; a missing spiked ISTD is not.
    assert not needs_look("無可用 MS1 峰", "", is_istd=False)
    assert needs_look("無可用 MS1 峰", "", is_istd=True)
    assert set(BASIS_NEEDS_LOOK) >= {"NL 確認", "RAW 失敗"}


def test_user_guide_explains_every_label_with_a_figure():
    guide = ROOT / "docs/user/targeted.md"
    rows = {
        cells[0].strip("`").partition("（")[0]: cells
        for line in guide.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
        and (cells := [cell.strip() for cell in line.strip("|").split("|")])
        and len(cells) == 5
    }
    labels_in_table = set(BASIS_NEEDS_LOOK) | set(DOUBT_LABELS)
    assert labels_in_table <= set(rows)
    look_word = {True: "是", False: "否", None: "只有 ISTD"}
    for label in labels_in_table:
        _, figure, _, _, look = rows[label]
        image = re.fullmatch(r"!\[[^\]]+\]\((assets/labels/[a-z0-9-]+\.svg)\)", figure)
        assert image and (guide.parent / image[1]).is_file(), label
        # The guide states the same look mark the table applies.
        assert look == look_word[BASIS_NEEDS_LOOK.get(label, True)], label
