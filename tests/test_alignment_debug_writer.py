from pathlib import Path

from xic_extractor.alignment import debug_writer
from xic_extractor.alignment.debug_writer import (
    write_ambiguous_ms1_owners_tsv,
    write_event_to_ms1_owner_tsv,
    write_owner_edge_evidence_tsv,
)
from xic_extractor.alignment.edge_scoring import (
    OwnerEdgeEvidence,
)
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    OwnerAssignment,
)


def test_event_to_owner_debug_tsv(tmp_path: Path):
    path = write_event_to_ms1_owner_tsv(
        tmp_path / "event_to_ms1_owner.tsv",
        (
            OwnerAssignment(
                "s1#6095",
                "OWN-s1-000001",
                "primary",
                "primary_identity_event",
            ),
            OwnerAssignment(
                "s1#6096",
                "OWN-s1-000001",
                "supporting",
                "owner_exact_apex_match",
            ),
        ),
    )

    assert path.read_text(encoding="utf-8").splitlines() == [
        "candidate_id\towner_id\tassignment_status\treason",
        "s1#6095\tOWN-s1-000001\tprimary\tprimary_identity_event",
        "s1#6096\tOWN-s1-000001\tsupporting\towner_exact_apex_match",
    ]


def test_ambiguous_owner_debug_tsv(tmp_path: Path):
    path = write_ambiguous_ms1_owners_tsv(
        tmp_path / "ambiguous_ms1_owners.tsv",
        (
            AmbiguousOwnerRecord(
                "AMB-s1-000001",
                "s1",
                ("s1#8001", "s1#8002"),
                "owner_multiplet_ambiguity",
            ),
        ),
    )

    assert path.read_text(encoding="utf-8").splitlines() == [
        "ambiguity_id\tsample_stem\tcandidate_ids\treason",
        "AMB-s1-000001\ts1\ts1#8001;s1#8002\towner_multiplet_ambiguity",
    ]


def test_owner_edge_evidence_tsv_uses_stable_columns_and_float_format(
    tmp_path: Path,
):
    path = write_owner_edge_evidence_tsv(
        tmp_path / "owner_edge_evidence.tsv",
        (
            OwnerEdgeEvidence(
                left_owner_id="OWN-s1-000001",
                right_owner_id="OWN-s2-000001",
                left_sample_stem="s1",
                right_sample_stem="s2",
                neutral_loss_tag="dC",
                left_precursor_mz=228.123456789,
                right_precursor_mz=228.123451,
                left_rt_min=4.123456,
                right_rt_min=4.223456,
                decision="strong_edge",
                failure_reason="",
                rt_raw_delta_sec=6.0,
                rt_drift_corrected_delta_sec=None,
                drift_prior_source="none",
                rt_basis="raw",
                injection_order_gap=None,
                owner_quality="clean",
                seed_support_level="strong",
                duplicate_context="none",
                score=100,
                reason="=strong",
            ),
        ),
    )

    assert path.read_text(encoding="utf-8").splitlines() == [
        (
            "left_owner_id\tright_owner_id\tleft_sample_stem\tright_sample_stem\t"
            "neutral_loss_tag\tleft_precursor_mz\tright_precursor_mz\tleft_rt_min\t"
            "right_rt_min\tdecision\tfailure_reason\trt_raw_delta_sec\t"
            "rt_drift_corrected_delta_sec\tdrift_prior_source\tinjection_order_gap\t"
            "owner_quality\tseed_support_level\tduplicate_context\tscore\treason\trt_basis"
        ),
        (
            "OWN-s1-000001\tOWN-s2-000001\ts1\ts2\tdC\t228.123\t228.123\t"
            "4.12346\t4.22346\tstrong_edge\t\t6\t\tnone\t\tclean\tstrong\t"
            "none\t100\t'=strong\traw"
        ),
    ]


def test_debug_tsv_writer_preserves_private_contract(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "debug.tsv"

    returned = debug_writer._write_tsv(
        path,
        ("id", "reason"),
        [{"id": "=candidate", "reason": "@review", "extra": "ignored"}],
    )

    assert returned == path
    assert path.read_text(encoding="utf-8") == (
        "id\treason\n'=candidate\t'@review\n"
    )

    empty_path = tmp_path / "empty" / "debug.tsv"
    debug_writer._write_tsv(empty_path, ("id", "reason"), [])
    assert empty_path.read_text(encoding="utf-8") == "id\treason\n"
