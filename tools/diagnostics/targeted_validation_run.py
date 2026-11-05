"""Execute one validation case through the finalized Targeted application."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from xic_extractor import extractor
from xic_extractor.applications.targeted.application import (
    build_canonical_targeted_application_service,
    build_targeted_application_request,
)
from xic_extractor.configuration.loader import prepare_targeted_config


def run_targeted_case(
    *,
    base_dir: Path,
    data_dir: Path,
    mode: str,
    workers: int,
    output_dir: Path,
    settings_overrides: Mapping[str, str] | None = None,
) -> Path:
    prepared = prepare_targeted_config(
        base_dir / "config",
        settings_overrides={
            **dict(settings_overrides or {}),
            "data_dir": str(data_dir),
        },
        output_dir=output_dir,
    )
    try:
        config = replace(
            prepared.config,
            parallel_mode=mode,
            parallel_workers=workers,
        )
        workbook_path = output_dir / f"xic_results_{mode}_w{workers}.xlsx"
        build_canonical_targeted_application_service(
            extractor.acquire
        ).execute_application(
            build_targeted_application_request(
                config,
                list(prepared.targets),
                excel_output_path=workbook_path,
            )
        )
        return workbook_path
    finally:
        prepared.cleanup()
