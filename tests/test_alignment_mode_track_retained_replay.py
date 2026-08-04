from __future__ import annotations

import ast
import json
from dataclasses import fields
from pathlib import Path

import pytest

from xic_extractor.alignment.identity_coherence.mode_track_retained_replay import (
    ModeTrackRetainedReplayError,
    ModeTrackRetainedReplaySummary,
    canonical_retained_mode_track_replay_summary_bytes,
    replay_retained_mode_track_fixture,
)

FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "docs"
    / "superpowers"
    / "fixtures"
    / "mode_track_assignment_85raw_inconclusive_v1.json"
)


def test_retained_85raw_replay_is_deterministic_inconclusive_and_no_raw() -> None:
    fixture_bytes = FIXTURE_PATH.read_bytes()

    first = replay_retained_mode_track_fixture(fixture_bytes)
    repeated = replay_retained_mode_track_fixture(fixture_bytes)

    assert first == repeated
    assert first.fixture_sha256 == (
        "342af70ef07d59ace1f2b9daabe06f4624ae6a78c8d503c2a0334dd59f7f9dbb"
    )
    assert canonical_retained_mode_track_replay_summary_bytes(first) == (
        canonical_retained_mode_track_replay_summary_bytes(repeated)
    )
    assert first.validation_status == "inconclusive"
    assert first.verdict == "inconclusive_no_write"
    assert first.product_authority == "none"
    assert first.seed_oracle_reproduced is False
    assert first.raw_open_count == 0
    assert first.chromatogram_call_count == 0
    assert first.sample_count == 85
    assert first.family_count == 1
    assert first.peak_hypothesis_count == 0
    assert first.track_count == 0
    assert first.anchor_count == 0
    assert first.landmark_count == 0
    assert dict(first.assignment_state_counts) == {
        "ambiguous_multiple_tracks": 0,
        "not_observed": 0,
        "out_of_model": 0,
        "technical_unassessable": 0,
        "uniquely_assigned": 0,
    }
    assert first.ambiguous_assignment_count == 0

    summary = json.loads(canonical_retained_mode_track_replay_summary_bytes(first))
    assert summary["counts"]["samples"] == 85
    assert summary["counts"]["assignment_states"] == dict(
        first.assignment_state_counts
    )


def test_retained_fixture_preserves_source_mismatch_and_85_sample_census() -> None:
    payload = json.loads(FIXTURE_PATH.read_bytes())

    qualification = payload["source_qualification"]
    required = qualification["required_seed_oracle"]
    historical = qualification["retained_historical_packet"]
    assert required["discovery_index_sha256"] == (
        "a5272eaf34f02d734f88c28ad055b244549fa98b14c9a0ea2d6d0e4380bc9e90"
    )
    assert required["sample_metadata_sha256"] == (
        "bc8b656877ebe827b804ba9b62bf349866f5d62e1482ad958d4ce832eb83fb05"
    )
    assert required["trace_packet_sha256"] == (
        "4b83dbcd0251e08589047b8c89b284c734094c128853e484fa2478f0f49d8b70"
    )
    assert historical["discovery_index_sha256"] != required[
        "discovery_index_sha256"
    ]
    assert historical["trace_packet_sha256"] != required["trace_packet_sha256"]
    assert qualification["required_seed_bytes_retained"] is False
    assert payload["sample_census"] == {
        "total": 85,
        "study": 78,
        "pooled_qc": 7,
        "historical_type_counts": {
            "Benign": 20,
            "Normal": 28,
            "QC": 7,
            "Tumor": 30,
        },
    }
    assert payload["reconstruction"] == {
        "status": "not_launched_missing_source",
        "reason_code": "required_owner_bound_discovery_index_unavailable",
        "raw_open_count": 0,
        "chromatogram_call_count": 0,
        "authorized_attempt_consumed": False,
    }


@pytest.mark.parametrize(
    ("path", "value", "message"),
    (
        (("authority", "product_authority"), "matrix_writer", "no assignment"),
        (
            ("owner_evidence", "direct_support_peak_hypothesis_count"),
            1,
            "cannot seed",
        ),
        (
            ("reconstruction", "raw_open_count"),
            85,
            "no-launch",
        ),
        (
            ("sensitivity", "shoulder_exclusion_status"),
            "passed",
            "technical_unassessable",
        ),
    ),
)
def test_retained_replay_rejects_authority_and_evidence_overclaims(
    path: tuple[str, str],
    value: object,
    message: str,
) -> None:
    payload = json.loads(FIXTURE_PATH.read_bytes())
    payload[path[0]][path[1]] = value

    with pytest.raises(ModeTrackRetainedReplayError, match=message):
        replay_retained_mode_track_fixture(json.dumps(payload).encode())


def test_retained_replay_summary_cannot_be_forged_or_gain_value_fields() -> None:
    with pytest.raises(TypeError, match="_mint_token"):
        ModeTrackRetainedReplaySummary(  # type: ignore[call-arg]
            fixture_sha256="0" * 64,
            family_count=1,
            sample_count=85,
            peak_hypothesis_count=0,
            track_count=0,
            anchor_count=0,
            landmark_count=0,
            assignment_state_counts=(),
            ambiguous_assignment_count=0,
            raw_open_count=0,
            chromatogram_call_count=0,
            seed_oracle_reproduced=False,
            reason_codes=("forged",),
        )

    forbidden = {"selected_area", "numeric_value", "writer", "matrix"}
    assert not {
        item.name
        for item in fields(ModeTrackRetainedReplaySummary)
        if any(token in item.name for token in forbidden)
    }


def test_retained_replay_owner_has_no_raw_or_product_consumer_dependencies() -> None:
    path = (
        Path(__file__).parents[1]
        / "xic_extractor"
        / "alignment"
        / "identity_coherence"
        / "mode_track_retained_replay.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    assert not {
        module
        for module in imported
        if any(
            token in module.lower()
            for token in ("raw", "thermo", "matrix", "backfill", "writer")
        )
    }
