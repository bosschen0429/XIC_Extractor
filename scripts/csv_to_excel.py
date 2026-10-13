"""
Command entry for converting XIC CSV outputs into a formatted workbook.

Workbook implementation lives under :mod:`xic_extractor.output`.
"""

from __future__ import annotations

from pathlib import Path
from typing import overload

from xic_extractor.configuration.loader import prepare_targeted_config
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output.workbook_builder import run_from_config

__all__ = [
    "main",
    "run",
]


@overload
def run(base_or_config: Path) -> Path: ...


@overload
def run(base_or_config: ExtractionConfig, targets: list[Target]) -> Path: ...


def run(
    base_or_config: Path | ExtractionConfig,
    targets: list[Target] | None = None,
) -> Path:
    if isinstance(base_or_config, Path):
        prepared = prepare_targeted_config(base_or_config / "config")
        try:
            return run_from_config(prepared.config, list(prepared.targets))
        finally:
            prepared.cleanup()
    if targets is None:
        raise TypeError("targets are required when run() receives ExtractionConfig")
    return run_from_config(base_or_config, targets)


def main() -> None:
    base_dir = Path(__file__).parent.parent
    run(base_dir)


if __name__ == "__main__":
    main()
