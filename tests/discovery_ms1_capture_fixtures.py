"""Helpers shared by test_discovery_ms1_capture and other test modules."""

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from tests.cid_nl_raw_fixture import fake_acquisition_source
from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile
from xic_extractor.discovery.ion_channels import build_discovery_channels
from xic_extractor.discovery.models import DiscoverySeed
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
)
from xic_extractor.xic_models import XICRequest, XICTrace

NEUTRAL_LOSS_DA = 116.0474


RAW_FILE = Path("C:/data/TumorBC2312_DNA.raw")


def _settings(**overrides: float) -> DiscoverySettings:
    values = {
        "neutral_loss_profile": NeutralLossProfile("DNA_dR", NEUTRAL_LOSS_DA),
        **overrides,
    }
    return DiscoverySettings(**values)


def _multi_tag_settings() -> DiscoverySettings:
    return DiscoverySettings(
        selected_tag_names=("dR", "R", "MeR"),
        tag_combine_mode="union",
        neutral_loss_profiles=(
            NeutralLossProfile("dR", 116.047344),
            NeutralLossProfile("R", 132.0423),
            NeutralLossProfile("MeR", 146.0579),
        ),
    )


def _gaussian(
    rt: np.ndarray,
    *,
    center: float,
    sigma: float,
    height: float,
) -> np.ndarray:
    return height * np.exp(-0.5 * ((rt - center) / sigma) ** 2)


def _channel(
    seeds: tuple[DiscoverySeed, ...],
    **settings_overrides: float,
):
    return build_discovery_channels(seeds, settings=_settings(**settings_overrides))[0]


def _seed(
    *,
    scan_number: int,
    rt: float,
    precursor_mz: float = 500.0000,
    product_mz: float = 383.9526,
    product_intensity: float = 10000.0,
    observed_loss_error_ppm: float = 0.0,
) -> DiscoverySeed:
    return DiscoverySeed(
        raw_file=RAW_FILE,
        sample_stem="TumorBC2312_DNA",
        scan_number=scan_number,
        rt=rt,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        product_intensity=product_intensity,
        neutral_loss_tag="DNA_dR",
        configured_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=NEUTRAL_LOSS_DA,
        observed_loss_error_ppm=observed_loss_error_ppm,
    )


class _OwnerBoundBatchRaw:
    def __init__(
        self,
        source: AcquisitionSourceIdentity,
        *,
        drop_results: bool = False,
    ) -> None:
        self.acquisition_source = source
        self.drop_results = drop_results
        self.raw_chromatogram_call_count = 0
        self.request_batches: list[tuple[XICRequest, ...]] = []
        self.rt = np.linspace(7.7, 7.9, 21)
        self.intensity = np.zeros(21)

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        self.request_batches.append(tuple(requests))
        self.raw_chromatogram_call_count += len(
            {self.scan_window_for_request(request) for request in requests}
        )
        if self.drop_results:
            return ()
        return tuple(
            XICTrace.from_arrays(self.rt, self.intensity) for _request in requests
        )

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        if request.rt_min < 8.5:
            return 100, 200
        return 300, 400

    def batch_xic_source(self):
        return self

    def read_ms1_mass_windows(self, requests):
        """This fake records no parent-MS1 centroids."""
        return (None,) * len(requests)


class _FakeRaw:
    """Owner-bound RAW returning one fixed trace for every request."""

    def __init__(self, *, rt: np.ndarray, intensity: np.ndarray) -> None:
        self.acquisition_source = fake_acquisition_source()
        self.rt = rt
        self.intensity = intensity
        self.raw_chromatogram_call_count = 0
        self.requests: list[tuple[float, float, float, float]] = []

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        self.raw_chromatogram_call_count += len(
            {self.scan_window_for_request(request) for request in requests}
        )
        for request in requests:
            self.requests.append(
                (request.mz, request.rt_min, request.rt_max, request.ppm_tol)
            )
        return tuple(XICTrace.from_arrays(self.rt, self.intensity) for _ in requests)

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        return 0, max(len(self.rt) - 1, 0)

    def batch_xic_source(self):
        return self

    def read_ms1_mass_windows(self, requests):
        """This fake records no parent-MS1 centroids."""
        return (None,) * len(requests)


class _BoundedRaw(_FakeRaw):
    """Fixed acquired samples, with the actual requested scan window."""

    def scan_window_for_request(self, request):
        return (
            int(np.searchsorted(self.rt, request.rt_min, side="left")),
            int(np.searchsorted(self.rt, request.rt_max, side="right")) - 1,
        )

    def retention_time_for_scan(self, scan_number):
        return float(self.rt[scan_number])

    def extract_xic_many(self, requests):
        super().extract_xic_many(requests)
        return tuple(
            XICTrace.from_arrays(
                self.rt[left : right + 1], self.intensity[left : right + 1]
            )
            for request in requests
            for left, right in (self.scan_window_for_request(request),)
        )
