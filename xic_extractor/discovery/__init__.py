"""Untargeted discovery models and contracts.

The package surface remains import-compatible, but each public value is loaded
on first access.  Leaf domain owners such as ``processing_run_spec`` can
therefore import Discovery value types without importing the RAW pipeline.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "write_discovery_candidates_csv": "xic_extractor.discovery.csv_writer",
    "write_discovery_review_csv": "xic_extractor.discovery.csv_writer",
    "group_discovery_seeds": "xic_extractor.discovery.grouping",
    "DiscoveryBatchOutputs": "xic_extractor.discovery.models",
    "DiscoveryRunOutputs": "xic_extractor.discovery.models",
    "MS1XicSource": "xic_extractor.discovery.ms1_backfill",
    "backfill_ms1_candidates": "xic_extractor.discovery.ms1_backfill",
    "MS2ScanSource": "xic_extractor.discovery.ms2_seeds",
    "collect_strict_nl_seeds": "xic_extractor.discovery.ms2_seeds",
    "DiscoveryRawHandle": "xic_extractor.discovery.pipeline",
    "RawOpener": "xic_extractor.discovery.pipeline",
    "run_discovery": "xic_extractor.discovery.pipeline",
    "run_discovery_batch": "xic_extractor.discovery.pipeline",
    "assign_review_priority": "xic_extractor.discovery.priority",
    "build_candidate_reason": "xic_extractor.discovery.priority",
    "BlankBackgroundLibrary": "xic_extractor.discovery.blank_qualification",
    "get_default_background_library": ("xic_extractor.discovery.blank_qualification"),
    "resolve_background_library": "xic_extractor.discovery.blank_qualification",
    "apply_blank_qualification_to_candidates": (
        "xic_extractor.discovery.blank_qualification"
    ),
}

__all__ = (
    "BlankBackgroundLibrary",
    "DiscoveryBatchOutputs",
    "DiscoveryRawHandle",
    "DiscoveryRunOutputs",
    "MS1XicSource",
    "MS2ScanSource",
    "RawOpener",
    "apply_blank_qualification_to_candidates",
    "assign_review_priority",
    "backfill_ms1_candidates",
    "build_candidate_reason",
    "collect_strict_nl_seeds",
    "get_default_background_library",
    "group_discovery_seeds",
    "resolve_background_library",
    "run_discovery",
    "run_discovery_batch",
    "write_discovery_candidates_csv",
    "write_discovery_review_csv",
)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
