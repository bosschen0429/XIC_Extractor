"""Helpers shared by test_discovery_physical_fact_integration and other test modules."""

from __future__ import annotations

import numpy as np

from tests.discovery_ms1_capture_fixtures import (
    _OwnerBoundBatchRaw as _BatchRaw,
)


class _OwnerBoundBatchRaw(_BatchRaw):
    def __init__(self, source, **kwargs) -> None:
        super().__init__(source, **kwargs)
        # Include baseline-return samples and context beyond both peak boundaries.
        self.rt = np.linspace(7.4, 8.2, 81)
        self.intensity = 100.0 * np.exp(-0.5 * ((self.rt - 7.8) / 0.06) ** 2)
