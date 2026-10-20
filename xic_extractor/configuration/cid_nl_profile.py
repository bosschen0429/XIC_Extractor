"""Shared CID-NL mass policy, independent of workflow orchestration."""

from dataclasses import dataclass
from typing import Literal

PrecursorReferenceRequirement = Literal["independent_mass", "recorded_precursor"]


@dataclass(frozen=True)
class NeutralLossProfile:
    tag: str
    neutral_loss_da: float


@dataclass(frozen=True)
class CidNlMassPolicy:
    nl_tolerance_ppm: float = 20.0
    precursor_mz_tolerance_ppm: float = 20.0
    product_mz_tolerance_ppm: float = 20.0
    product_search_ppm: float = 50.0
    ms2_precursor_tol_da: float = 0.8
    nl_min_intensity_ratio: float = 0.01


DEFAULT_CID_NL_MASS_POLICY = CidNlMassPolicy()
