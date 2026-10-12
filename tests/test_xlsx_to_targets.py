from __future__ import annotations

import csv
from pathlib import Path

from scripts.xlsx_to_targets import TargetRow, assign_istd_pairs, write_targets_csv


def testassign_istd_pairs_preserves_standard_claims_and_istd_rows() -> None:
    standard_a = _target("5-hmdC", rt=8.0)
    standard_b = _target("8-oxo-Guo", rt=12.0)
    istd_a = _target("d3-5-hmdC", rt=8.1, is_istd=True)
    istd_b = _target("d3-8-oxo-Guo", rt=12.2, is_istd=True)
    targets = [standard_a, istd_a, standard_b, istd_b]

    assign_istd_pairs(targets)

    assert standard_a["istd_pair"] == "d3-5-hmdC"
    assert standard_b["istd_pair"] == "d3-8-oxo-Guo"
    assert istd_a["istd_pair"] == ""
    assert istd_b["istd_pair"] == ""


def test_name_prefill_does_not_make_rt_a_pairing_veto() -> None:
    standard = _target("5-hmdC", rt=20.0)
    istd = _target("d3-5-hmdC", rt=8.0, is_istd=True)

    assign_istd_pairs([standard, istd])

    assert standard["istd_pair"] == "d3-5-hmdC"


def test_name_prefill_preserves_explicit_choices_and_ambiguous_names() -> None:
    analyte = _target("5-medC", rt=8.0)
    first = _target("d3-5-medC", rt=8.0, is_istd=True)
    second = _target("d4-5-medC", rt=8.0, is_istd=True)
    nearby_name = _target("6-medC", rt=8.0)
    assign_istd_pairs([analyte, first, second, nearby_name])
    assert analyte["istd_pair"] == nearby_name["istd_pair"] == ""
    analyte["istd_pair"] = "d4-5-medC"
    assign_istd_pairs([analyte, first, second])
    assert analyte["istd_pair"] == "d4-5-medC"


def test_name_prefill_fills_missing_roles_without_overriding_explicit_roles() -> None:
    analyte = _target("5-medC", rt=8.0)
    standard = _target("d3-5-medC", rt=8.0)
    del standard["role"]
    explicitly_analyte = _target("d4-5-medC", rt=8.0)
    assign_istd_pairs([analyte, standard, explicitly_analyte])
    assert standard["role"] == "istd"
    assert explicitly_analyte["role"] == "analyte"
    assert analyte["istd_pair"] == "d3-5-medC"


def test_write_targets_csv_preserves_public_csv_contract(
    tmp_path: Path,
    capsys,
) -> None:
    output_path = tmp_path / "targets.csv"
    target = _target("5-hmdC", rt=8.0)

    write_targets_csv([target], output_path)

    raw = output_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw
    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
            "label",
            "mz",
            "expected_rt_min",
            "cid_nl_tag",
            "role",
            "istd_pair",
        ]
        rows = list(reader)
    assert rows == [
        {
            "label": "5-hmdC",
            "mz": "100.0",
            "expected_rt_min": "8.0",
            "cid_nl_tag": "dR",
            "role": "analyte",
            "istd_pair": "",
        }
    ]
    assert f"Written 1 targets to {output_path}" in capsys.readouterr().out


def _target(label: str, *, rt: float, is_istd: bool = False) -> TargetRow:
    return {
        "label": label,
        "mz": 100.0,
        "expected_rt_min": rt,
        "cid_nl_tag": "dR",
        "role": "istd" if is_istd else "analyte",
        "istd_pair": "",
    }
