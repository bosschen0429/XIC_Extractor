from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_TABLE = (
    ROOT / "docs/superpowers/validation/"
    "cid_nl_oracle_istd_calibration_2raw/calibration_gate_table.tsv"
)


def test_cid_nl_calibration_gate_table_freezes_only_supported_tolerances() -> None:
    with GATE_TABLE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    assert {row["gate_id"] for row in rows} == {
        "boundary_containment",
        "area_retention",
    }
    assert {row["gate_status"] for row in rows} == {"frozen"}
    assert {row["runtime_behavior_changed"] for row in rows} == {"FALSE"}
    assert {row["unknown_handling"] for row in rows} == {"inconclusive"}

    area = next(row for row in rows if row["gate_id"] == "area_retention")
    assert area["comparator"] == "greater_than_or_equal"
    assert area["threshold"] == "0.98"

    boundary = next(row for row in rows if row["gate_id"] == "boundary_containment")
    assert boundary["comparator"] == "equal"
    assert boundary["threshold"] == "TRUE"
