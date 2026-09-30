from __future__ import annotations

import csv
from pathlib import Path

from xic_extractor.diagnostics.shared_peak_identity_explanation.oracle import (
    load_manual_oracle,
)
from xic_extractor.diagnostics.shared_peak_identity_explanation.schema import (
    ORACLE_COLUMNS,
)

ORACLE = Path("docs/superpowers/fixtures/shared_peak_identity_manual_oracle.tsv")
CURRENT_8RAW_SAMPLES = {
    "BenignfatBC1055_DNA",
    "BenignfatBC1151_DNA",
    "Breast_Cancer_Tissue_pooled_QC3",
    "Breast_Cancer_Tissue_pooled_QC5",
    "NormalBC2263_DNA",
    "NormalBC2312_DNA",
    "TumorBC2263_DNA",
    "TumorBC2312_DNA",
}


def test_manual_oracle_fixture_schema_and_seed_scope() -> None:
    with ORACLE.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)

    assert tuple(reader.fieldnames or ()) == ORACLE_COLUMNS
    assert {row["oracle_schema_version"] for row in rows} == {
        "shared_peak_identity_manual_oracle"
    }
    assert len({row["oracle_row_id"] for row in rows}) == len(rows)
    assert all(
        row["oracle_row_id"] == f"{row['feature_family_id']}|{row['sample_id']}"
        for row in rows
    )
    assert {
        "FAM000144",
        "FAM000610",
        "FAM001227",
        "FAM001589",
        "FAM001658",
        "FAM002175",
    } <= {row["feature_family_id"] for row in rows}
    assert {row["manual_label"] for row in rows} >= {
        "pass",
        "suspect",
        "fail",
        "human_unjudgeable",
        "not_applicable",
    }
    assert _samples(rows, "FAM000610") == CURRENT_8RAW_SAMPLES
    assert _samples(rows, "FAM002175") == CURRENT_8RAW_SAMPLES


def test_manual_oracle_context_row_is_not_binary_label() -> None:
    rows = {row.oracle_row_id: row for row in load_manual_oracle(ORACLE)}

    context = rows["FAM001227|__family_context__"]
    assert context.data["manual_label"] == "not_applicable"
    assert context.data["related_family_id"] == "FAM001239"
    assert context.sample_id == "__family_context__"


def _samples(rows: list[dict[str, str]], family_id: str) -> set[str]:
    return {
        row["sample_id"]
        for row in rows
        if row["feature_family_id"] == family_id
        and not row["sample_id"].startswith("__")
    }
