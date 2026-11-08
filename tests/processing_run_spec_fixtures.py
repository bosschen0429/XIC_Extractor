"""Helpers shared by test_processing_run_spec and other test modules."""

from __future__ import annotations

from pathlib import Path

from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec,
)
from xic_extractor.discovery.settings import DiscoverySettings


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile(
            tag="DNA_dR",
            neutral_loss_da=116.0474,
        )
    )


def _raws(tmp_path: Path) -> tuple[Path, Path]:
    first = tmp_path / "A.raw"
    second = tmp_path / "B.raw"
    first.write_bytes(b"first acquisition")
    second.write_bytes(b"second acquisition")
    return first, second


def _compile_paths(
    raw_paths: tuple[Path, ...],
    *,
    settings: DiscoverySettings | None = None,
):
    return compile_discovery_processing_run_spec(
        raw_paths,
        discovery_settings=settings or _settings(),
    )
