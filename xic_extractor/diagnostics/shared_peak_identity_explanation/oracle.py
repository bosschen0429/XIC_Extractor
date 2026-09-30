from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.tabular_io import read_tsv_required

from .schema import ORACLE_COLUMNS, ORACLE_SCHEMA_VERSION, validate_row_tokens

SENTINEL_SAMPLE_IDS = frozenset({"__scope_rule__", "__family_context__"})


@dataclass(frozen=True)
class ManualOracleRow:
    data: Mapping[str, str]

    @property
    def oracle_row_id(self) -> str:
        return self.data["oracle_row_id"]

    @property
    def feature_family_id(self) -> str:
        return self.data["feature_family_id"]

    @property
    def sample_id(self) -> str:
        return self.data["sample_id"]

    @property
    def manual_label(self) -> str:
        return self.data["manual_label"]

    @property
    def manual_scope(self) -> str:
        return self.data["manual_scope"]

    @property
    def manual_reason_tags(self) -> tuple[str, ...]:
        return tuple(
            token
            for token in self.data.get("manual_reason_tags", "").split(";")
            if token
        )

    @property
    def is_sentinel(self) -> bool:
        return self.sample_id in SENTINEL_SAMPLE_IDS


def load_manual_oracle(path: Path) -> tuple[ManualOracleRow, ...]:
    raw_rows = read_tsv_required(path, ORACLE_COLUMNS)
    rows: list[ManualOracleRow] = []
    seen: set[str] = set()
    for raw in raw_rows:
        row = {column: raw.get(column, "") for column in ORACLE_COLUMNS}
        if row["oracle_schema_version"] != ORACLE_SCHEMA_VERSION:
            raise ValueError(
                f"{path}: unsupported oracle_schema_version "
                f"{row['oracle_schema_version']!r}"
            )
        expected_id = f"{row['feature_family_id']}|{row['sample_id']}"
        if row["oracle_row_id"] != expected_id:
            raise ValueError(
                f"{path}: oracle_row_id {row['oracle_row_id']!r} "
                f"does not match {expected_id!r}"
            )
        if row["oracle_row_id"] in seen:
            raise ValueError(f"{path}: duplicate oracle_row_id {row['oracle_row_id']}")
        validate_row_tokens(row)
        seen.add(row["oracle_row_id"])
        rows.append(ManualOracleRow(row))
    return tuple(
        sorted(rows, key=lambda item: (item.feature_family_id, item.sample_id))
    )


def oracle_rows_as_dicts(
    rows: tuple[ManualOracleRow, ...],
) -> tuple[Mapping[str, str], ...]:
    return tuple(row.data for row in rows)
