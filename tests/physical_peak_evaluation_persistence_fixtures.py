"""Helpers shared by test_physical_peak_evaluation_persistence and others."""

from __future__ import annotations

import numpy as np


def _context_sensitive_trace() -> tuple[np.ndarray, np.ndarray]:
    # Scaled observed XIC, with RT in minutes at 1.5 seconds per scan.
    # Removing one exterior scan leaves the retained Gaussian peak exact.
    raw = np.asarray(
        [
            236.132,
            164.179,
            104.242,
            84.744,
            131.893,
            59.936,
            55.567,
            29.262,
            33.487,
            27.028,
            20.902,
            16.914,
            23.171,
            19.101,
            11.874,
            14.134,
            30.926,
            117.955,
            378.598,
            1206.0,
            3363.568,
            7394.956,
            10749.463,
            12201.848,
            11247.332,
            7386.286,
            4304.834,
            2474.821,
            1514.42,
            1122.02,
            913.297,
            694.172,
            528.272,
            422.653,
            387.785,
            328.499,
            291.29,
            249.678,
            200.976,
            190.326,
            144.765,
            163.245,
            164.729,
            227.957,
            399.121,
            895.663,
            2136.916,
            4325.4,
        ]
    )
    return np.arange(len(raw), dtype=float) * 0.025, raw


def _short_peak_with_separate_noise_hits() -> tuple[np.ndarray, np.ndarray]:
    rt = np.arange(387, dtype=float) * 0.04
    raw = np.zeros_like(rt)
    # A ten-scan observed peak rises and falls. Earlier, smaller hits and
    # scattered background observations do not establish its absence.
    raw[187:206] = 10000.0 * np.asarray(
        [
            0.103516,
            0.173885,
            0.172859,
            0.126381,
            0.229408,
            0.0,
            0.072839,
            0.0,
            0.0,
            0.065716,
            0.194376,
            0.324126,
            0.648833,
            1.0,
            0.789331,
            0.598496,
            0.262206,
            0.122555,
            0.023368,
        ]
    )
    raw[
        [
            219,
            220,
            241,
            244,
            256,
            260,
            268,
            270,
            272,
            274,
            281,
            283,
            284,
            290,
            296,
            299,
            301,
            303,
            309,
            310,
            323,
            326,
            354,
            371,
        ]
    ] = 120.0
    return rt, raw


def _overlapping_triplet_trace(
    point_count: int = 1001,
    width: float = 0.65,
) -> tuple[np.ndarray, np.ndarray]:
    rt = np.linspace(0.0, 10.0, point_count)
    raw = 100.0 + sum(
        height * np.exp(-0.5 * ((rt - center) / width) ** 2)
        for height, center in ((1000.0, 3.0), (1500.0, 5.0), (2000.0, 7.0))
    )
    return rt, raw
