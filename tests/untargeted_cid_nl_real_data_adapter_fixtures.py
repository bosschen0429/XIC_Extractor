"""Helpers shared by test_untargeted_cid_nl_real_data_adapter and other test modules."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.discovery.models import DiscoveryPeak


def build_sample_adapter_fixture(tmp_path: Path, *, raw_stem: str = "S1"):
    """One study sample with a Thermo-format synthetic source, not vendor I/O."""
    return build_untargeted_cid_nl_no_raw_fixture(
        tmp_path,
        sample_roles={raw_stem: "sample"},
        thermo_raw_sources=True,
    )


def _without_ms1_peak(candidate: DiscoveryPeak) -> DiscoveryPeak:
    """A peak held at peak level: its MS2 events remain, its MS1 peak is not used."""
    return replace(candidate, status="doubly_charged")
