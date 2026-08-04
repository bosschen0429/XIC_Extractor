from __future__ import annotations

import json
from pathlib import Path


def test_mode_track_85raw_stop_receipt_is_evidence_only() -> None:
    root = Path(__file__).parents[1]
    receipt_path = (
        root
        / "docs"
        / "superpowers"
        / "fixtures"
        / "mode_track_assignment_85raw_inconclusive_v1.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert receipt["schema_version"] == "mode_track_85raw_stop_receipt_v1"
    assert receipt["sample_census"] == {
        "total": 85,
        "study": 78,
        "pooled_qc": 7,
    }
    qualified = receipt["qualified_historical_run"]
    assert qualified == {
        "producing_code_state": "8ef6b4394e57e066f76b72a221e19953b6f055fb",
        "implementation_base": "9519f546012ddcc189c2ba8f1d7ef34ea02e3199",
        "discovery_index_sha256": (
            "a5272eaf34f02d734f88c28ad055b244549fa98b14c9a0ea2d6d0e4380bc9e90"
        ),
        "sample_metadata_sha256": (
            "bc8b656877ebe827b804ba9b62bf349866f5d62e1482ad958d4ce832eb83fb05"
        ),
        "trace_packet_sha256": (
            "4b83dbcd0251e08589047b8c89b284c734094c128853e484fa2478f0f49d8b70"
        ),
        "sample_count": 85,
        "raw_open_count": 85,
        "chromatogram_call_count": 85,
        "elapsed_seconds": 81.67721,
        "independent_verdict": "multiple_tracks_supported_diagnostic_only",
    }
    assert receipt["retained_historical_packet"] == {
        "discovery_index_schema": "legacy_v1_unowned",
        "discovery_index_sha256": (
            "93c37cd016bff16f8243523777738f951955f13b86eb1a6dd7e30cb072a07474"
        ),
        "trace_packet_sha256": (
            "f73e2c2d4f9bb53ec4568955442e9a40b1faa7b431079d6241e7e39ea283fff3"
        ),
        "trace_count": 85,
        "raw_open_count_evidence": None,
        "chromatogram_call_count_evidence": None,
        "source_packet_bytes_retained_in_repo": False,
    }
    assert receipt["stop"] == {
        "status": "not_launched_missing_source",
        "reason_code": "required_owner_bound_discovery_index_unavailable",
        "required_seed_bytes_retained": False,
        "raw_open_count": 0,
        "chromatogram_call_count": 0,
        "authorized_attempt_consumed": False,
    }
    assert receipt["authority"] == {
        "validation_status": "inconclusive",
        "verdict": "inconclusive_no_write",
        "product_authority": "none",
        "assignment_facts_emitted": False,
    }
    assert receipt["owner_facts"]["direct_support_peak_hypothesis_count"] == 0
    assert receipt["owner_facts"]["canonical_track_seed_count"] == 0
    assert receipt["owner_facts"]["owner_refs_complete"] is False
    assert {
        "family_context",
        "historical_diagnostic_counts",
        "sensitivity",
        "expected_replay_counts",
        "reconstruction",
    }.isdisjoint(receipt)
    assert not (
        root
        / "xic_extractor"
        / "alignment"
        / "identity_coherence"
        / "mode_track_retained_replay.py"
    ).exists()
