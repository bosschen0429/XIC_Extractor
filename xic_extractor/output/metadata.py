from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib import metadata

from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.targeted_strategy import strategy_receipt
from xic_extractor.output.schema import TARGETED_OUTPUT_SCHEMA_VERSION


def app_version() -> str:
    try:
        return metadata.version("xic-extractor")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_metadata_rows(config: ExtractionConfig) -> list[tuple[str, object]]:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        ("app_version", app_version()),
        ("generated_at", generated_at),
        ("target_config_hash", config.target_config_hash),
        ("targeted_output_schema_version", TARGETED_OUTPUT_SCHEMA_VERSION),
        *execution_metadata_rows(config.execution),
        ("mass_recalibration", config.mass_recalibration.value),
        ("targeted_strategy", json.dumps(strategy_receipt(), sort_keys=True)),
        ("signal_processing_method", config.signal_processing_method),
        ("signal_processing_window_points", config.signal_processing_window_points),
        ("ms2_precursor_tol_da", config.ms2_precursor_tol_da),
        ("nl_min_intensity_ratio", config.nl_min_intensity_ratio),
        ("count_no_ms2_as_detected", config.count_no_ms2_as_detected),
    ]


def execution_metadata_rows(execution: ExecutionProfile) -> list[tuple[str, object]]:
    return [
        ("parallel_mode", "process" if execution.backend == "process" else "serial"),
        ("parallel_workers", execution.parallel_workers),
    ]
