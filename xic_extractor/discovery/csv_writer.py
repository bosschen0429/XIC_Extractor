"""CSV writer for untargeted discovery review candidates."""

from collections.abc import Iterable
from pathlib import Path, PureWindowsPath
from typing import Any

from xic_extractor.discovery.models import (
    DISCOVERY_BRIEF_COLUMNS,
    DISCOVERY_CANDIDATE_COLUMNS,
    DiscoveryCandidate,
)
from xic_extractor.tabular_io import escape_excel_formula, write_delimited_rows

_PRIORITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def write_discovery_candidates_csv(
    path: Path, candidates: Iterable[DiscoveryCandidate]
) -> Path:
    """Write review-first discovery candidates to a stable CSV contract."""
    sorted_candidates = sorted(candidates, key=_candidate_sort_key)
    return _write_csv(
        path,
        [_candidate_row(candidate) for candidate in sorted_candidates],
        DISCOVERY_CANDIDATE_COLUMNS,
    )


def write_discovery_review_csv(
    path: Path, candidates: Iterable[DiscoveryCandidate]
) -> Path:
    """Write the compact human review index for discovery candidates."""
    sorted_candidates = sorted(candidates, key=_candidate_sort_key)
    return _write_csv(
        path,
        [_brief_candidate_row(candidate) for candidate in sorted_candidates],
        DISCOVERY_BRIEF_COLUMNS,
    )


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    columns: tuple[str, ...],
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_delimited_rows(path, rows, columns)
    return path


def build_discovery_review_note(candidate: DiscoveryCandidate) -> str:
    return (
        f"{candidate.ms2_support} MS2; "
        f"{candidate.ms1_support} MS1; "
        f"{candidate.rt_alignment} RT"
    )


def _candidate_sort_key(candidate: DiscoveryCandidate) -> tuple[Any, ...]:
    area_is_missing = candidate.ms1_area is None
    area_desc = 0.0 if candidate.ms1_area is None else -candidate.ms1_area
    return (
        _PRIORITY_RANK.get(candidate.review_priority, len(_PRIORITY_RANK)),
        -candidate.evidence_score,
        -candidate.feature_family_size,
        candidate.feature_family_id,
        -candidate.seed_event_count,
        -candidate.ms2_product_max_intensity,
        area_is_missing,
        area_desc,
        candidate.best_seed_rt,
    )


def _candidate_row(candidate: DiscoveryCandidate) -> dict[str, str]:
    return {
        column: format_discovery_csv_value(column, getattr(candidate, column))
        for column in DISCOVERY_CANDIDATE_COLUMNS
    }


def _brief_candidate_row(candidate: DiscoveryCandidate) -> dict[str, str]:
    return {
        column: format_discovery_csv_value(
            column,
            build_discovery_review_note(candidate)
            if column == "review_note"
            else getattr(candidate, column),
        )
        for column in DISCOVERY_BRIEF_COLUMNS
    }


def format_discovery_csv_value(column: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if column in {"seed_scan_ids", "matched_tag_names"}:
        if isinstance(value, str):
            return value
        if isinstance(value, tuple):
            return ";".join(str(item) for item in value)
        return str(value)
    if isinstance(value, Path):
        return escape_excel_formula(_stable_path_text(value))
    if isinstance(value, float):
        return f"{value:.6g}"
    return escape_excel_formula(str(value))


def _stable_path_text(value: Path) -> str:
    text = str(value)
    windows_path = PureWindowsPath(text)
    if windows_path.drive:
        return str(windows_path)
    return text


_format_csv_value = format_discovery_csv_value
