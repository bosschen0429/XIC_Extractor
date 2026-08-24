from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

_T0_PACKET_HASHES = {
    "README.md": "51153CC647E101E9D3DA54E152DF498AE954143F710F12B77EC393DDBE656734",
    "call_cost_baseline_v1.tsv": (
        "0CCC6A67815048B2F472D9A1E51850B41A225164F28F83481561AE55680C954F"
    ),
    "historical_failure_matrix_v1.tsv": (
        "AB78FB65992A18F0AB38FAEEDE2D01D775DE5462243B201C9A82EBC45C5D6FD2"
    ),
    "rule_disposition_v1.tsv": (
        "9003E0C5AA1DA5F7B4B3050A29BB77B3672E07121BE39A107F6AD6972A391BE7"
    ),
}


def test_retired_84_cell_scope_cannot_regain_writer_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    authority_manifest = json.loads(
        (
            root
            / "docs/superpowers/schemas/productization_authority_manifest.v1.json"
        ).read_text(encoding="utf-8")
    )
    retired_scope = "backfill_expansion_clean_target_selective_activation_84_cells"
    assert retired_scope not in authority_manifest["authority_policy"][
        "product_writer_allowed_scopes"
    ]
    assert retired_scope in {
        row["scope_id"]
        for row in authority_manifest["blocked_or_negative_evidence_scopes"]
    }


def test_retained_t0_packet_is_an_immutable_historical_oracle() -> None:
    packet = (
        Path(__file__).resolve().parents[1]
        / "docs/superpowers/validation/standard_peak_shared_evidence_t0_v1"
    )
    actual_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest().upper()
        for path in packet.iterdir()
        if path.is_file()
    }

    assert actual_hashes == _T0_PACKET_HASHES
    readme = (packet / "README.md").read_text(encoding="utf-8")
    assert "- Evidence label: `diagnostic_only`." in readme
    assert "- Write authority: `none`." in readme


def test_sp001_through_sp055_have_one_final_t5d_disposition() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = (
        root
        / "docs/superpowers/validation/standard_peak_t5d_contraction_v1"
        / "capability_disposition_v1.tsv"
    )
    with manifest.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle, delimiter="\t"))

    expected_ids = {f"SP-{index:03d}" for index in range(1, 56)}
    assert {row["rule_id"] for row in rows} == expected_ids
    assert len(rows) == len(expected_ids)
    assert {row["final_disposition"] for row in rows} <= {
        "owner_input",
        "diagnostic_oracle",
        "diagnostic_renderer",
        "delete",
    }
    for row in rows:
        if row["final_disposition"] == "delete":
            assert row["retained_locator"] == "deleted_by_t5d"
        else:
            assert (root / row["retained_locator"]).exists(), row["rule_id"]

    by_id = {row["rule_id"]: row for row in rows}
    assert by_id["SP-040"] == {
        "rule_id": "SP-040",
        "final_disposition": "delete",
        "retained_locator": "deleted_by_t5d",
        "rationale": "ownerless clean-target selective writer retired",
    }
    for rule_id in (
        "SP-025",
        "SP-029",
        "SP-031",
        "SP-037",
        "SP-039",
        "SP-047",
        "SP-050",
        "SP-052",
        "SP-054",
        "SP-055",
    ):
        assert by_id[rule_id]["final_disposition"] == "delete"
