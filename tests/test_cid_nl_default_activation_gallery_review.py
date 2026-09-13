from __future__ import annotations

from pathlib import Path

from tools.diagnostics import cid_nl_default_activation_gallery_review as review
from xic_extractor.tabular_io import read_tsv_required, write_tsv


def test_writes_source_successor_transition_evidence(tmp_path: Path) -> None:
    decisions_tsv = tmp_path / "decisions.tsv"
    alignment_tsv = tmp_path / "alignment.tsv"
    output_path = tmp_path / "differential.tsv"
    write_tsv(
        decisions_tsv,
        [
            _decision("FAM000001", "FAM000002", "SampleA", "write_authorized"),
            _decision(
                "FAM000001",
                "FAM000002",
                "SampleB",
                "no_write_detected_baseline_preserved",
            ),
            _decision("FAM000003", "", "SampleC", "no_write_omitted"),
        ],
        review.DECISION_COLUMNS,
    )
    write_tsv(
        alignment_tsv,
        [
            _alignment(
                "FAM000001",
                mz="243.099",
                rt="23.66",
                product="127.052",
                accepted="0",
                identity="audit_family",
            ),
            _alignment("FAM000002", mz="300.1605", rt="23.35"),
            _alignment("FAM000003", mz="301.165", rt="23.34"),
        ],
        review.ALIGNMENT_REVIEW_COLUMNS,
    )

    written = review.write_transition_review_evidence(
        decisions_tsv=decisions_tsv,
        alignment_review_tsv=alignment_tsv,
        output_path=output_path,
    )

    assert written == output_path
    rows = read_tsv_required(output_path, review.DIFFERENTIAL_REVIEW_COLUMNS)
    assert [row["transition_key"] for row in rows] == [
        "FAM000001->FAM000002",
        "FAM000003-><none>",
    ]
    successor = rows[0]
    assert successor["sample_count"] == "2"
    assert successor["write_authorized_count"] == "1"
    assert successor["no_write_detected_baseline_preserved_count"] == "1"
    assert successor["feature_inclusion_gate"] == (
        "candidate_ms1_feature_inclusion_supported"
    )
    assert successor["identity_authority_gate"] == (
        "replacement_merge_dedupe_requires_expected_diff"
    )
    assert successor["source_successor_relationship"] == (
        "source_and_successor_not_mutually_exclusive"
    )
    assert successor["differential_overlay_readiness"] == "ready_for_paired_overlay"
    assert successor["mz_delta"] == "57.0615"
    assert successor["rt_delta"] == "-0.31"

    omitted = rows[1]
    assert omitted["sample_count"] == "1"
    assert omitted["no_write_omitted_count"] == "1"
    assert omitted["feature_inclusion_gate"] == "not_assessable_no_successor_feature"
    assert omitted["identity_authority_gate"] == "no_replacement_target"
    assert omitted["source_successor_relationship"] == "old_identity_has_no_successor"
    assert omitted["differential_overlay_readiness"] == "no_successor_target"


def _decision(
    old_peak: str,
    successor_peak: str,
    sample: str,
    decision: str,
) -> dict[str, str]:
    return {
        "old_peak_hypothesis_id": old_peak,
        "sample_stem": sample,
        "successor_peak_hypothesis_id": successor_peak,
        "successor_decision": decision,
        "write_authority": "TRUE" if decision == "write_authorized" else "FALSE",
        "matrix_write_allowed": (
            "TRUE" if decision == "write_authorized" else "FALSE"
        ),
        "matrix_effect": (
            "write_accepted_backfill"
            if decision == "write_authorized"
            else "no_write_scope_removed"
        ),
        "human_explanation": f"{decision} explanation",
        "input_resolution_status": "write_ready_blank",
        "candidate_new_peak_hypothesis_ids": successor_peak,
        "candidate_baseline_values": "",
        "accepted_quant_value": "123",
    }


def _alignment(
    family: str,
    *,
    mz: str,
    rt: str,
    product: str = "184.113",
    accepted: str = "85",
    identity: str = "production_family",
) -> dict[str, str]:
    return {
        "feature_family_id": family,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": mz,
        "family_center_rt": rt,
        "family_product_mz": product,
        "detected_count": "85",
        "accepted_cell_count": accepted,
        "include_in_primary_matrix": "TRUE",
        "identity_decision": identity,
        "identity_confidence": "high",
        "row_flags": "",
        "reason": "fixture",
    }
