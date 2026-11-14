"""Workbench JSON intake and presentation for the Untargeted application."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xic_extractor.applications.untargeted.full_analysis import FullAnalysisRequest
from xic_workbench.methods import preset_from_payload
from xic_workbench.samples import from_payload as samples_from_payload


def parse_request(values: dict[str, Any]) -> FullAnalysisRequest:
    """Parse JSON workbench state; the application binds its scientific inputs.

    ``preset_snapshot`` accepts
    ``xic_workbench.methods.method_details(...)["draft"]``; when present,
    execution binds those displayed method bytes instead of re-reading the
    preset locator.
    """

    if not isinstance(values, dict):
        raise TypeError("Untargeted request must be an object")
    allowed = {
        "preset",
        "overrides",
        "raw_dir",
        "dll_dir",
        "output_dir",
        "targets_csv",
        "sequence_source",
        "samples",
        "preset_snapshot",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"unsupported Untargeted request field: {unknown[0]}")
    preset = _text(values.get("preset", "dna_dr"), "preset")
    overrides = values.get("overrides", {})
    if not isinstance(overrides, dict):
        raise TypeError("overrides must be an object")
    snapshot_value = values.get("preset_snapshot")
    snapshot = (
        preset_from_payload(snapshot_value) if snapshot_value is not None else None
    )
    return FullAnalysisRequest(
        preset=preset,
        tuning_overrides=dict(overrides),
        raw_dir=_optional_path(values.get("raw_dir")),
        dll_dir=Path(_text(values.get("dll_dir"), "DLL directory")),
        output_dir=Path(_text(values.get("output_dir"), "output directory")),
        targets_csv=_optional_path(values.get("targets_csv")),
        sample_metadata=samples_from_payload(values.get("samples")),
        preset_snapshot=snapshot,
    )


def present_result(result: dict[str, Any]) -> dict[str, Any]:
    """Add the read-only Inspector overview to a completed application result."""
    evidence = result.get("evidence")
    if evidence is not None:
        from xic_inspector.api import read_evidence_summary
        from xic_inspector.sources.untargeted_overview import review_overview

        path = Path(evidence.report_path)
        result["overview"], _rows = review_overview(path, read_evidence_summary(path))
    return result


def _optional_path(value: object) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (str, Path)):
        raise TypeError("path values must be text")
    text = str(value).strip()
    return Path(text) if text else None


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"please provide {label}")
    return value.strip()
