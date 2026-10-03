"""Pure precursor references and isolation bounds shared by CID-NL consumers."""

from typing import Literal

from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata, Ms2Scan

DirectPrecursorMzBasis = Literal["scan_precursor", "scan_monoisotopic"]
PrecursorMzBasis = Literal[
    "scan_precursor", "scan_monoisotopic", "product_plus_neutral_loss"
]
DIRECT_PRECURSOR_BASES = frozenset({"scan_precursor", "scan_monoisotopic"})


def direct_precursor_references(
    scan: Ms2Scan, tolerance_ppm: float
) -> tuple[tuple[float, DirectPrecursorMzBasis], ...]:
    """Keep distinguishable recorded ions; do not collapse isotope families."""
    metadata = scan.precursor_metadata
    if metadata is not None and metadata.charge not in (None, 1):
        return ()
    selected: tuple[float, DirectPrecursorMzBasis] = (
        scan.precursor_mz,
        "scan_precursor",
    )
    mono = metadata.monoisotopic_mz if metadata is not None else None
    if mono is None or abs(mono - scan.precursor_mz) <= mono * tolerance_ppm / 1e6:
        return (selected,)
    return (selected, (mono, "scan_monoisotopic"))


def precursor_inference_bounds(
    selected_mz: float, metadata: Ms2PrecursorMetadata | None, maximum_radius_da: float
) -> tuple[float, float]:
    """Use scan isolation geometry, capped by the configured inference radius."""
    width = metadata.isolation_width_da if metadata is not None else None
    offset = (metadata.isolation_offset_da or 0.0) if metadata is not None else 0.0
    radius = maximum_radius_da if width is None else min(width / 2, maximum_radius_da)
    center = selected_mz + offset
    return center - radius, center + radius


def scan_supports_precursor(
    scan: Ms2Scan, target_mz: float, maximum_radius_da: float, tolerance_ppm: float
) -> bool:
    references = direct_precursor_references(scan, tolerance_ppm)
    if not references:
        return False
    if any(
        abs(mz - target_mz) <= target_mz * tolerance_ppm / 1e6 for mz, _ in references
    ):
        return True
    lower, upper = precursor_inference_bounds(
        scan.precursor_mz, scan.precursor_metadata, maximum_radius_da
    )
    return lower <= target_mz <= upper
