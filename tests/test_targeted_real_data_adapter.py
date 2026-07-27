from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.discovery.ms1_backfill import backfill_ms1_candidates
from xic_extractor.discovery.ms2_seeds import collect_strict_nl_seeds
from xic_extractor.extraction.targeted_real_data_adapter import (
    TargetedRealDataAdapterError,
    adapt_current_discovery_sample_target,
)
from xic_extractor.raw_reader import Ms2Scan, Ms2ScanEvent
from xic_extractor.xic_models import XICRequest, XICTrace

NEUTRAL_LOSS_DA = 116.0474
PRECURSOR_MZ = 500.0
PRODUCT_MZ = PRECURSOR_MZ - NEUTRAL_LOSS_DA


def test_adapter_reacquires_one_current_candidate_into_targeted_lifecycle(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assert result.validation_status == "diagnostic_only"
    assert result.source_candidate_ids == (candidates[0].candidate_id,)
    assert result.acquisition is not None
    assert result.acquisition.logical_xic_request_count == 1
    assert result.acquisition.xic_batch_count == 1
    assert result.acquisition.adapter_chromatogram_call_count == 1
    assert raw.batch_calls == 1
    assert result.alignment_input.selection.status == "selected"
    assert len(result.alignment_input.candidate_set.candidates) == 1
    assert result.alignment_input.candidate_set.lineage_artifacts


def test_adapter_preserves_multiple_owner_backed_candidates_as_ambiguous(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    raw = _FakeRaw((7.8, 8.3))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 2

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        raw=raw,
    )

    assert result.source_candidate_ids == tuple(
        sorted(item.candidate_id for item in candidates)
    )
    assert result.acquisition is not None
    assert result.acquisition.logical_xic_request_count == 2
    assert raw.batch_calls == 1
    assert len(result.alignment_input.candidate_set.candidates) == 2
    assert result.alignment_input.selection.status == "ambiguous"
    assert result.alignment_input.selection.selected_hypothesis_id is None


def test_adapter_rejects_candidate_receipt_drift(tmp_path: Path) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )[0]
    assert candidate.ms1_area is not None
    drifted = replace(candidate, ms1_area=candidate.ms1_area * 1.1)

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="area_raw_counts_seconds mismatches current receipt",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=fixture.target,
            candidates=(drifted,),
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )


def test_adapter_rejects_inferred_discovery_evidence(tmp_path: Path) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    raw = _FakeRaw((8.0,), scan_precursor_mz=500.5)
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    assert len(candidates) == 1
    assert candidates[0].precursor_mz_basis == "product_plus_neutral_loss"

    with pytest.raises(
        TargetedRealDataAdapterError,
        match="exact canonical Discovery seed is not unique",
    ):
        adapt_current_discovery_sample_target(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            target=fixture.target,
            candidates=candidates,
            settings=settings,
            peak_config=peak_config,
            raw=raw,
        )


def test_adapter_seals_empty_target_universe_without_raw_acquisition(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("S1",))
    raw = _FakeRaw((8.0,))

    result = adapt_current_discovery_sample_target(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=(),
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        raw=raw,
    )

    assert result.acquisition is None
    assert result.source_candidate_ids == ()
    assert result.alignment_input.selection.status == "no_candidate"
    assert raw.batch_calls == 0


def _current_candidates(
    raw: _FakeRaw,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
):
    seeds = collect_strict_nl_seeds(raw, raw_file=raw_file, settings=settings)
    groups = group_discovery_seeds(seeds, settings=settings)
    return backfill_ms1_candidates(
        raw,
        groups,
        settings=settings,
        peak_config=peak_config,
    )


def _settings() -> DiscoverySettings:
    return DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        rt_min=7.5,
        rt_max=8.6,
        seed_rt_gap_min=0.20,
        ms1_search_padding_min=0.20,
        resolver_mode="local_minimum",
    )


def _peak_config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "unused.csv",
        diagnostics_csv=tmp_path / "unused-diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.10,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
        resolver_mode="local_minimum",
    )


class _FakeRaw:
    def __init__(
        self,
        scan_rts: tuple[float, ...],
        *,
        scan_precursor_mz: float = PRECURSOR_MZ,
    ) -> None:
        self.rt = np.linspace(7.4, 8.7, 521)
        self.intensity = sum(
            (
                1000.0 * np.exp(-0.5 * ((self.rt - center) / 0.035) ** 2)
                for center in scan_rts
            ),
            np.zeros_like(self.rt),
        )
        self.scans = tuple(
            Ms2Scan(
                scan_number=100 + index,
                rt=rt,
                precursor_mz=scan_precursor_mz,
                masses=np.asarray((PRODUCT_MZ, PRODUCT_MZ + 10.0)),
                intensities=np.asarray((800.0, 200.0)),
                base_peak=1000.0,
            )
            for index, rt in enumerate(scan_rts)
        )
        self.batch_calls = 0

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        request = XICRequest(mz=mz, rt_min=rt_min, rt_max=rt_max, ppm_tol=ppm_tol)
        trace = self._trace(request)
        return trace.rt, trace.intensity

    def extract_xic_many(
        self,
        requests: tuple[XICRequest, ...],
    ) -> tuple[XICTrace, ...]:
        self.batch_calls += 1
        return tuple(self._trace(request) for request in requests)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        mask = (self.rt >= request.rt_min) & (self.rt <= request.rt_max)
        indices = np.flatnonzero(mask)
        return int(indices[0]), int(indices[-1])

    def iter_ms2_scans(
        self,
        rt_min: float,
        rt_max: float,
    ):
        for scan in self.scans:
            if rt_min <= scan.rt <= rt_max:
                yield Ms2ScanEvent(
                    scan=scan,
                    parse_error=None,
                    scan_number=scan.scan_number,
                )

    def _trace(self, request: XICRequest) -> XICTrace:
        mask = (self.rt >= request.rt_min) & (self.rt <= request.rt_max)
        return XICTrace(rt=self.rt[mask], intensity=self.intensity[mask])
