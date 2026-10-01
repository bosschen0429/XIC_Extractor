from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.output.method_manifest import (
    METHOD_MANIFEST_SCHEMA_VERSION,
    artifact_sha256,
    method_manifest_path,
)
from xic_extractor.output.schema import TARGETED_OUTPUT_SCHEMA_VERSION


def app_version() -> str:
    try:
        return metadata.version("xic-extractor")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_metadata_rows(config: ExtractionConfig) -> list[tuple[str, object]]:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_path = method_manifest_path(config)
    return [
        ("config_hash", config.config_hash),
        ("app_version", app_version()),
        ("generated_at", generated_at),
        ("signal_processing_method", config.signal_processing_method),
        ("signal_processing_window_points", config.signal_processing_window_points),
        (
            "ms1_morphology_smoothing_window_points",
            config.ms1_morphology_smoothing_window_points,
        ),
        ("nl_min_intensity_ratio", config.nl_min_intensity_ratio),
        ("ms2_precursor_tol_da", config.ms2_precursor_tol_da),
        ("targeted_output_schema_version", TARGETED_OUTPUT_SCHEMA_VERSION),
        ("method_manifest_schema", METHOD_MANIFEST_SCHEMA_VERSION),
        ("method_manifest_path", manifest_path.name),
        ("method_manifest_sha256", artifact_sha256(manifest_path) or ""),
    ]
