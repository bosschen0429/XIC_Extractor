"""Helpers shared by test_targeted_delta_mass and other test modules."""

import numpy as np


def pattern():
    return {
        "combine_mode": "all",
        "mz_tolerance_ppm": 12,
        "conditions": [
            {
                "kind": "delta_mass",
                "positions": [
                    {
                        "label": "light",
                        "offset_mz": -2.0,
                        "ratio_min": 0.1,
                        "ratio_max": 10,
                        "compare_shape": True,
                    },
                    {
                        "label": "heavy",
                        "offset_mz": 0.0,
                        "ratio_min": 0.1,
                        "ratio_max": 10,
                    },
                ],
                "apex_floor_sec": 2,
                "min_shape_r": 0.9,
            }
        ],
    }


class PatternRaw:
    """Independent analytic chromatograms; mass windows select actual channels."""

    def __init__(
        self, *, scale=1.0, partner_rt=2.0, partner_mz=300.0, partner_width=0.08
    ):
        self.rt = np.linspace(0, 4, 481)
        self.scale = scale
        self.partner_rt = partner_rt
        self.partner_mz = partner_mz
        self.partner_width = partner_width
        self.requests = []

    def acquisition_rt_bounds(self):
        return 0.0, 4.0

    def extract_xic(self, mz, start, end, ppm):
        values = np.full(self.rt.shape, 100.0)
        for mass, apex, height, width in [
            (302.0, 2.0, 100_000.0, 0.08),
            (self.partner_mz, self.partner_rt, 200_000.0, self.partner_width),
        ]:
            if abs(mass - mz) <= mz * ppm * 1e-6:
                values += (
                    self.scale * height * np.exp(-0.5 * ((self.rt - apex) / width) ** 2)
                )
        keep = (self.rt >= start) & (self.rt <= end)
        return self.rt[keep], values[keep]

    def extract_xic_many(self, requests):
        from xic_extractor.xic_models import XICTrace

        self.requests.extend(requests)
        return tuple(
            XICTrace.from_arrays(*self.extract_xic(r.mz, r.rt_min, r.rt_max, r.ppm_tol))
            for r in requests
        )
