from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

TagKind = Literal["neutral_loss"]
_CsvRow = dict[str | None, object]


@dataclass(frozen=True)
class FeatureTagProfile:
    tag_kind: TagKind
    tag_label: str
    tag_name: str
    parameter_mz_or_da: float
    mass_tolerance_ppm: float
    intensity_cutoff: float


def load_feature_tag_profiles(path: Path) -> list[FeatureTagProfile]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [cast(_CsvRow, row) for row in csv.DictReader(handle)]

    profiles: list[FeatureTagProfile] = []
    for row_number, row in enumerate(rows, start=2):
        category = _required(row, "Tag Category", row_number)
        if category == "2":
            continue
        if category != "1":
            raise ValueError(
                f"{path}:{row_number}: unsupported Tag Category {category!r}"
            )

        label = _extract_label(row)
        _required(row, "Tag No.", row_number)
        profiles.append(
            FeatureTagProfile(
                tag_kind="neutral_loss",
                tag_label=label,
                tag_name=_normalize_tag_name(label),
                parameter_mz_or_da=_parse_float(
                    row, "Tag Parameters (Da or m/z)", row_number
                ),
                mass_tolerance_ppm=_parse_float(
                    row, "Mass Tolerance (ppm)", row_number
                ),
                intensity_cutoff=_parse_float(
                    row, "Intensity Cutoff (height)", row_number
                ),
            )
        )
    return profiles


def resolve_selected_tag_profiles(
    profiles: Iterable[FeatureTagProfile],
    selected_tags: Iterable[str],
) -> list[FeatureTagProfile]:
    profile_list = list(profiles)
    resolved: list[FeatureTagProfile] = []
    for raw_selector in selected_tags:
        selector = _normalize_selector(raw_selector)
        matches = [
            profile
            for profile in profile_list
            if _normalize_selector(profile.tag_name) == selector
            or _normalize_selector(profile.tag_label) == selector
        ]
        if not matches:
            raise ValueError(
                f"selected tag {raw_selector!r} was not a selectable neutral-loss tag"
            )
        resolved.extend(matches)
    return resolved


def _required(row: _CsvRow, column: str, row_number: int) -> str:
    value = str(row.get(column, "")).strip()
    if value == "":
        raise ValueError(f"row {row_number}: {column} is required")
    return value


def _parse_float(row: _CsvRow, column: str, row_number: int) -> float:
    value = _required(row, column, row_number)
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"row {row_number}: {column} must be a float: {value!r}"
        ) from exc


def _extract_label(row: _CsvRow) -> str:
    value = row.get("")
    if isinstance(value, str) and value.strip():
        return _normalize_label(value)
    overflow = row.get(None)
    if isinstance(overflow, list) and overflow:
        return _normalize_label(str(overflow[-1]))
    raise ValueError("FH feature-list row is missing trailing tag label")


def _normalize_label(value: str) -> str:
    return " ".join(value.strip().split())


def _normalize_tag_name(label: str) -> str:
    normalized = _normalize_label(label)
    if ":" not in normalized:
        return normalized
    prefix, tag_name = normalized.split(":", 1)
    if prefix.strip().upper() != "NL":
        return normalized
    return tag_name.strip()


def _normalize_selector(value: str) -> str:
    return _normalize_label(value).lower()
