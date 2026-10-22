"""CSV writer for untargeted discovery review candidates."""

from collections.abc import Iterable
from pathlib import Path, PureWindowsPath

from xic_extractor.discovery.models import (
    DISCOVERY_BRIEF_COLUMNS,
    DISCOVERY_CANDIDATE_COLUMNS,
    DiscoveryCandidate,
    discovery_candidate_output_sort_key,
)
from xic_extractor.tabular_io import (
    escape_excel_formula,
    format_machine_float,
    write_delimited_rows,
)


def write_discovery_candidates_csv(
    path: Path, candidates: Iterable[DiscoveryCandidate]
) -> Path:
    """Write review-first discovery candidates to a stable CSV contract."""
    sorted_candidates = sorted(candidates, key=discovery_candidate_output_sort_key)
    return _write_csv(
        path,
        [_candidate_row(candidate) for candidate in sorted_candidates],
        DISCOVERY_CANDIDATE_COLUMNS,
    )


def write_discovery_review_csv(
    path: Path, candidates: Iterable[DiscoveryCandidate]
) -> Path:
    """Write the compact human review index for discovery candidates."""
    sorted_candidates = sorted(candidates, key=discovery_candidate_output_sort_key)
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
    ms2_support = candidate.ms2_support
    if ms2_support not in {"weak", "moderate", "strong"}:
        raise ValueError(
            "Discovery review MS2 support must be weak, moderate, or strong"
        )
    terminal_state = {
        "physical_unavailable": "unavailable",
        "physical_failed": "failed",
    }.get(candidate.ms1_trace_quality)
    if terminal_state is not None:
        prefix = f"physical MS1 evaluation {terminal_state}: "
        if not candidate.reason.startswith(prefix):
            raise ValueError("physical Discovery review reason has the wrong prefix")
        owner_reason = candidate.reason.removeprefix(prefix)
        if not owner_reason.strip():
            raise ValueError("physical Discovery review reason must be nonempty")
        return f"physical MS1 {terminal_state}: {owner_reason}; missing RT"
    evidence_summary = (
        f"{ms2_support} MS2; {candidate.ms1_support} MS1; {candidate.rt_alignment} RT"
    )
    if candidate.discovery_candidate_state == "review_only_ambiguous_coisolation":
        return f"review-only ambiguous inferred precursor; {evidence_summary}"
    if candidate.discovery_candidate_state == "review_only_unresolved_peak_attribution":
        return f"review-only unresolved peak/MS2 attribution; {evidence_summary}"
    return evidence_summary


_candidate_sort_key = discovery_candidate_output_sort_key


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
        # Alignment reads these values back; display rounding would move them.
        return format_machine_float(value)
    return escape_excel_formula(str(value))


def _stable_path_text(value: Path) -> str:
    text = str(value)
    windows_path = PureWindowsPath(text)
    if windows_path.drive:
        return str(windows_path)
    return text


_format_csv_value = format_discovery_csv_value
