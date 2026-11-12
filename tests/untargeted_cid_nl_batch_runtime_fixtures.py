"""Helpers shared by test_untargeted_cid_nl_batch_runtime and other test modules."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tests.cid_nl_raw_fixture import (
    _current_candidates,
    _FakeRaw,
)
from xic_extractor.discovery.models import DiscoveryPeak


def _nearby_subject_candidates(
    raw_path: Path,
    *,
    settings,
    peak_config,
) -> tuple[_FakeRaw, tuple[DiscoveryPeak, ...]]:
    first_raw = _FakeRaw((7.98,))
    second_raw = _FakeRaw(
        (7.98,),
        scan_precursor_mz=500.004,
        neutral_loss_das=(116.0474,),
    )
    second_raw.scans = (
        replace(
            second_raw.scans[0],
            scan_number=first_raw.scans[0].scan_number + 1,
        ),
    )
    candidates = tuple(
        sorted(
            (
                _current_candidates(
                    raw,
                    raw_file=raw_path,
                    settings=settings,
                )[0]
                for raw in (first_raw, second_raw)
            ),
            key=lambda item: item.peak_id,
        )
    )
    source_raw = _FakeRaw((7.98,))
    source_raw.scans = (first_raw.scans[0], second_raw.scans[0])
    return source_raw, candidates
