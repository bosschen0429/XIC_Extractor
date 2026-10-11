from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.output.schema import TARGETED_OUTPUT_SCHEMA_VERSION
from xic_extractor.settings_schema import METHOD_KEYS


def app_version() -> str:
    try:
        return metadata.version("xic-extractor")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_metadata_rows(config: ExtractionConfig) -> list[tuple[str, object]]:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        ("config_hash", config.config_hash),
        ("app_version", app_version()),
        ("generated_at", generated_at),
        ("target_config_hash", config.target_config_hash),
        ("targeted_output_schema_version", TARGETED_OUTPUT_SCHEMA_VERSION),
        ("parallel_mode", config.parallel_mode),
        ("parallel_workers", config.parallel_workers),
        *[(key, getattr(config, key)) for key in METHOD_KEYS],
    ]
