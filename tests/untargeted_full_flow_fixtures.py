"""Synthetic vendor boundaries for complete Untargeted application routes."""

from types import SimpleNamespace

import numpy as np

from tests.cid_nl_raw_fixture import _FakeRaw
from tests.cid_nl_runtime_fixtures import install_runtime_raws
from tests.discovery_pipeline_fixtures import _OwnerBoundFakeRawHandle, _scan_event
from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.ms1_scan_index import build_ms1_scan_index
from xic_extractor.presets.delta_mass import resolve_delta_mass_discovery
from xic_extractor.processing_identity import build_local_acquisition_source_identity
from xic_extractor.raw_source_binding import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)
from xic_extractor.xic_models import XICTrace


def install_full_flow_raws(monkeypatch, directory, *, delta_mass=False):
    directory.mkdir(exist_ok=True)
    paths = tuple(directory / f"{stem}.raw" for stem in ("first", "second"))
    for path in paths:
        path.write_bytes(path.stem.encode())
    monkeypatch.setattr(
        "xic_extractor.configuration.execution_profile.os.cpu_count", lambda: 2
    )
    monkeypatch.setattr(
        "xic_extractor.applications.untargeted.full_analysis.DEFAULT_ALIGNMENT_WORKER_BACKEND",
        "thread",
    )
    if not delta_mass:
        sources = {}
        for index, path in enumerate(paths):
            raw = _FakeRaw((7.8,), neutral_loss_das=(116.0474,))
            raw.intensity *= index + 1
            sources[path.resolve()] = raw
        return paths, install_runtime_raws(
            monkeypatch, sources_by_path=sources, tmp_path=directory
        )

    install_fake_raw_headers(monkeypatch)
    positions = resolve_delta_mass_discovery("pt_pattern").settings.positions
    opened = {path.resolve(): [] for path in paths}

    class DiagnosticRaw(_OwnerBoundFakeRawHandle):
        RunHeaderEx = SimpleNamespace(FirstSpectrum=1, LastSpectrum=121)

        def __init__(self, path, source, mass_recalibration):
            self.base = 300.0 if path.stem == "first" else 600.0
            self.height = 1e6 if path.stem == "first" else 2e6
            self.times = np.linspace(0, 2, 121)
            self.signal = np.exp(-0.5 * ((self.times - 1) / 0.035) ** 2)
            super().__init__(
                source,
                [
                    _scan_event(
                        scan_number=61,
                        rt=1.0,
                        precursor_mz=self.base,
                        masses=[self.base - 116.0474],
                        intensities=[10_000.0],
                    )
                ],
                rt=self.times,
                intensity=self.signal * self.height,
            )
            self.mass_recalibration_result = MassRecalibrationResult(
                "unavailable",
                reason="missing_lock_mass_trailers",
                policy=mass_recalibration,
            )

        def GetFilterForScanNumber(self, _scan):
            return SimpleNamespace(MSOrder="Ms")

        def RetentionTimeFromScanNumber(self, scan):
            return self.times[scan - 1]

        def GetCentroidStream(self, scan, _refresh):
            return SimpleNamespace(
                Masses=[self.base + position.delta_da for position in positions],
                Intensities=self.signal[scan - 1]
                * self.height
                * np.array([1, 1, 0.75, 0.25]),
            )

        def read_ms1_scan_index(self, *, centroid):
            return build_ms1_scan_index(self, centroid=centroid)

        def extract_xic_many(self, requests):
            def signal_for(request):
                position = min(
                    positions, key=lambda p: abs(self.base + p.delta_da - request.mz)
                )
                if absolute_ppm_error(request.mz, self.base + position.delta_da) > 20:
                    return np.zeros_like(self.signal)
                ratio = [1, 1, 0.75, 0.25][positions.index(position)]
                return self.signal * self.height * ratio

            return tuple(
                XICTrace(self.times, signal_for(request)) for request in requests
            )

    def open_raw(
        path,
        dll_dir,
        *,
        mass_recalibration,
        expected_acquisition_source=None,
        **_kwargs,
    ):
        source = expected_acquisition_source or build_local_acquisition_source_identity(
            path,
            acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
            observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
        )
        raw = DiagnosticRaw(path, source, mass_recalibration)
        opened[path.resolve()].append(raw)
        return raw

    monkeypatch.setattr("xic_extractor.raw_reader.open_raw", open_raw)
    monkeypatch.setattr(
        "xic_extractor.discovery.delta_mass_pipeline.open_raw", open_raw
    )
    return paths, opened
