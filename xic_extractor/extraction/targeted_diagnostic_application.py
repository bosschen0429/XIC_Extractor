"""Canonical Targeted production service composition for CLI and GUI."""

from __future__ import annotations

from xic_extractor.extraction.targeted_service import (
    TargetedApplicationService,
    TargetedRunner,
)


def build_canonical_targeted_application_service(
    runner: TargetedRunner,
) -> TargetedApplicationService:
    """Build the single production CLI/GUI service composition."""

    return TargetedApplicationService(runner)


__all__ = ["build_canonical_targeted_application_service"]
