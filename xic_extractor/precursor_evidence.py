"""Pure precursor references and isolation bounds shared by CID-NL consumers."""

import math
from dataclasses import dataclass
from typing import Literal

from xic_extractor.configuration.cid_nl_profile import DEFAULT_CID_NL_MASS_POLICY
from xic_extractor.mass_accuracy import neutral_loss_match
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Scan

RecordedPrecursorMzBasis = Literal["scan_precursor", "scan_monoisotopic"]
PrecursorMzBasis = Literal[
    "scan_precursor", "scan_monoisotopic", "product_plus_neutral_loss"
]
RECORDED_PRECURSOR_BASES = frozenset({"scan_precursor", "scan_monoisotopic"})
IsolationMembership = Literal["inside_nominal", "outside_nominal", "unknown"]
PrecursorReferenceRequirement = Literal["independent_mass", "recorded_precursor"]
_CARBON_ISOTOPE_SPACING_DA = 1.0033548


@dataclass(frozen=True, slots=True)
class Ms1PrecursorObservation:
    """Positive centroids acquired in one precursor search window of a parent MS1."""

    scan_number: int
    rt_min: float
    centroid_mzs: tuple[float, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.scan_number, bool)
            or not isinstance(self.scan_number, int)
            or self.scan_number <= 0
        ):
            raise ValueError("parent MS1 scan_number must be a positive integer")
        if not math.isfinite(self.rt_min) or self.rt_min < 0:
            raise ValueError("parent MS1 RT must be finite and non-negative")
        if not isinstance(self.centroid_mzs, tuple) or any(
            not math.isfinite(mz) or mz <= 0 for mz in self.centroid_mzs
        ):
            raise ValueError("parent MS1 centroids must be positive finite masses")


@dataclass(frozen=True, slots=True)
class PrecursorLossAssessment:
    """Completed mass/isolation answer; peak relation owners still qualify RT."""

    status: str
    reference_source: str | None
    reference_mz: float | None
    observed_loss_da: float | None
    observed_loss_error_ppm: float | None
    isolation_membership: IsolationMembership

    @property
    def supports_nl(self) -> bool:
        return self.status == "compatible"

    def supports_reference(self, requirement: PrecursorReferenceRequirement) -> bool:
        """Mass compatibility does not identify a coisolated fragment's parent.

        Discovery can retain independently measured parent-MS1 compatibility.
        A named Target's own CID-NL identity requires a recorded precursor
        reference, rather than an ion found by searching product + loss.
        """
        return self.supports_nl and (
            requirement == "independent_mass"
            or self.reference_source in RECORDED_PRECURSOR_BASES
        )


def assess_precursor_loss(
    *,
    basis: PrecursorMzBasis,
    precursor_mz: float,
    product_mz: float,
    neutral_loss_da: float,
    observed_loss_da: float,
    observed_loss_error_ppm: float | None,
    tolerance_ppm: float,
    selected_mz: float | None,
    metadata: Ms2PrecursorMetadata | None,
    ms1_observation: Ms1PrecursorObservation | None = None,
) -> PrecursorLossAssessment:
    """Separate how a candidate was found from its independently observed mass."""
    reference_source = str(basis)
    if basis == "product_plus_neutral_loss":
        unavailable = (
            "parent_ms1_unavailable"
            if ms1_observation is None
            else "parent_ms1_ion_missing"
            if not ms1_observation.centroid_mzs
            else "parent_ms1_ion_ambiguous"
            if len(ms1_observation.centroid_mzs) != 1
            else None
        )
        if unavailable is not None:
            return PrecursorLossAssessment(
                unavailable, None, None, None, None, "unknown"
            )
        assert ms1_observation is not None
        precursor_mz = ms1_observation.centroid_mzs[0]
        reference_source = "parent_ms1"
        observed_loss_da = precursor_mz - product_mz
        observed_loss_error_ppm = (
            abs(observed_loss_da - neutral_loss_da) / neutral_loss_da * 1e6
        )
    membership = precursor_isolation_membership(precursor_mz, selected_mz, metadata)
    if membership == "outside_nominal" and not _isolated_vendor_isotope(
        basis, precursor_mz, selected_mz, metadata
    ):
        status = "outside_nominal_isolation"
    elif membership == "unknown" and basis != "scan_precursor":
        status = "isolation_unavailable"
    elif not neutral_loss_match(
        precursor_mz,
        product_mz,
        neutral_loss_da,
        tolerance_ppm,
        observed_loss_da=observed_loss_da,
    )[2]:
        status = "neutral_loss_mismatch"
    else:
        status = "compatible"
    return PrecursorLossAssessment(
        status,
        reference_source,
        precursor_mz,
        observed_loss_da,
        observed_loss_error_ppm,
        membership,
    )


def recorded_precursor_references(
    scan: Ms2Scan, tolerance_ppm: float
) -> tuple[tuple[float, RecordedPrecursorMzBasis], ...]:
    """Keep distinguishable recorded ions; do not collapse isotope families."""
    metadata = scan.precursor_metadata
    if metadata is not None and metadata.charge not in (None, 1):
        return ()
    selected: tuple[float, RecordedPrecursorMzBasis] = (
        scan.precursor_mz,
        "scan_precursor",
    )
    mono = metadata.monoisotopic_mz if metadata is not None else None
    if mono is None or abs(mono - scan.precursor_mz) <= mono * tolerance_ppm / 1e6:
        return (selected,)
    return (selected, (mono, "scan_monoisotopic"))


def precursor_isolation_membership(
    precursor_mz: float,
    selected_mz: float | None,
    metadata: Ms2PrecursorMetadata | None,
) -> IsolationMembership:
    """Nominal geometry is compatibility evidence, not a transmission model."""
    if (
        selected_mz is None
        or metadata is None
        or metadata.isolation_width_da is None
        or metadata.isolation_offset_da is None
    ):
        return "unknown"
    center = selected_mz + metadata.isolation_offset_da
    if abs(precursor_mz - center) <= metadata.isolation_width_da / 2:
        return "inside_nominal"
    return "outside_nominal"


def supports_direct_nl(
    basis: PrecursorMzBasis,
    precursor_mz: float,
    selected_mz: float | None,
    metadata: Ms2PrecursorMetadata | None,
) -> bool:
    """Require independent mass reference and compatible precursor selection.

    Selected scan precursors preserve the method's acquisition evidence when
    geometry is unavailable. A vendor monoisotopic assignment outside the
    window qualifies only through a compatible, isolated isotope selection.
    This eligibility still requires the caller's mass and RT checks; it does
    not establish a unique chemical parent in a coisolated population.
    """
    if basis not in RECORDED_PRECURSOR_BASES:
        return False
    membership = precursor_isolation_membership(precursor_mz, selected_mz, metadata)
    return (
        membership == "inside_nominal"
        or (basis == "scan_precursor" and membership == "unknown")
        or _isolated_vendor_isotope(basis, precursor_mz, selected_mz, metadata)
    )


def _isolated_vendor_isotope(
    basis: PrecursorMzBasis,
    precursor_mz: float,
    selected_mz: float | None,
    metadata: Ms2PrecursorMetadata | None,
) -> bool:
    """Qualify vendor M+n selection without relabelling M's window geometry.

    The recorded assignment supplies the independent mass reference, not a
    product-plus-loss search. Known charge 1 and integer carbon isotope spacing
    must agree within the shared precursor mass policy; the selected isotope
    itself must be in the nominal window. Product/NL and peak checks remain with
    their existing owners. This does not prove exclusive fragment parentage.
    """
    if (
        basis != "scan_monoisotopic"
        or metadata is None
        or metadata.charge != 1
        or metadata.monoisotopic_mz != precursor_mz
        or selected_mz is None
        or precursor_isolation_membership(selected_mz, selected_mz, metadata)
        != "inside_nominal"
    ):
        return False
    isotope_number = round((selected_mz - precursor_mz) / _CARBON_ISOTOPE_SPACING_DA)
    if isotope_number < 1:
        return False
    expected_mz = precursor_mz + isotope_number * _CARBON_ISOTOPE_SPACING_DA
    tolerance = DEFAULT_CID_NL_MASS_POLICY.precursor_mz_tolerance_ppm / 1e6
    return abs(selected_mz - expected_mz) <= expected_mz * tolerance


def precursor_inference_bounds(
    selected_mz: float, metadata: Ms2PrecursorMetadata | None, maximum_radius_da: float
) -> tuple[float, float]:
    """Use scan isolation geometry, capped by the configured inference radius."""
    width = metadata.isolation_width_da if metadata is not None else None
    offset = (metadata.isolation_offset_da or 0.0) if metadata is not None else 0.0
    radius = maximum_radius_da if width is None else min(width / 2, maximum_radius_da)
    center = selected_mz + offset
    return center - radius, center + radius
