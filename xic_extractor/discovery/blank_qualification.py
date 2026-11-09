from __future__ import annotations

import bisect
import gzip
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from xic_extractor.raw_reader import open_raw

if TYPE_CHECKING:
    from xic_extractor.discovery.models import DiscoveryPeak

BLANK_BACKGROUND_LIBRARY_SCHEMA = "blank_background_library"


@dataclass(frozen=True, slots=True)
class BlankBackgroundIon:
    """Represents a single recurring background ion detected in solvent blanks."""

    mz: float
    max_intensity: float
    scan_count: int
    source_blank: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "mz": round(self.mz, 6),
            "max_intensity": float(self.max_intensity),
            "scan_count": int(self.scan_count),
            "source_blank": self.source_blank,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlankBackgroundIon:
        return cls(
            mz=float(data["mz"]),
            max_intensity=float(data["max_intensity"]),
            scan_count=int(data["scan_count"]),
            source_blank=str(data.get("source_blank", "")),
        )


@dataclass(frozen=True)
class BlankBackgroundLibrary:
    """Sorted collection of background ions with fast logarithmic matching."""

    ions: tuple[BlankBackgroundIon, ...]
    min_intensity: float
    source_blanks: tuple[str, ...]
    version: str = BLANK_BACKGROUND_LIBRARY_SCHEMA

    @property
    def mzs(self) -> np.ndarray:
        return np.array([ion.mz for ion in self.ions], dtype=np.float64)

    def match(
        self,
        target_mz: float,
        *,
        ppm_tol: float = 10.0,
    ) -> list[tuple[BlankBackgroundIon, float]]:
        """Find matching background ions within mass tolerance.

        Returns a list of (ion, delta_ppm) tuples sorted by absolute mass error.
        """
        if not self.ions or target_mz <= 0.0:
            return []

        tol_da = target_mz * ppm_tol / 1e6
        min_mz = target_mz - tol_da
        max_mz = target_mz + tol_da

        # Binary search for mass window
        ion_mzs = [ion.mz for ion in self.ions]
        left = bisect.bisect_left(ion_mzs, min_mz)
        right = bisect.bisect_right(ion_mzs, max_mz)

        matches: list[tuple[BlankBackgroundIon, float]] = []
        for i in range(left, right):
            ion = self.ions[i]
            delta_ppm = (target_mz - ion.mz) / ion.mz * 1e6
            if abs(delta_ppm) <= ppm_tol:
                matches.append((ion, float(delta_ppm)))

        matches.sort(key=lambda x: abs(x[1]))
        return matches

    def save_json(self, path: Path) -> None:
        """Persist background library to JSON or JSON.GZ file."""
        payload = {
            "version": self.version,
            "min_intensity": self.min_intensity,
            "source_blanks": list(self.source_blanks),
            "ion_count": len(self.ions),
            "ions": [ion.to_dict() for ion in self.ions],
        }
        if str(path).endswith(".gz"):
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
        else:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

    @classmethod
    def load_json(cls, path: Path) -> BlankBackgroundLibrary:
        """Load background library from JSON or JSON.GZ file."""
        if str(path).endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        else:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

        ions = tuple(
            sorted(
                (BlankBackgroundIon.from_dict(item) for item in data.get("ions", [])),
                key=lambda ion: ion.mz,
            )
        )
        version = str(data["version"])
        if version != BLANK_BACKGROUND_LIBRARY_SCHEMA:
            raise ValueError("unsupported Blank background library version")
        return cls(
            ions=ions,
            min_intensity=float(data.get("min_intensity", 25000.0)),
            source_blanks=tuple(data.get("source_blanks", [])),
            version=version,
        )


def serialize_background_library(library: BlankBackgroundLibrary) -> str:
    """Freeze the selected library without the export writer's m/z rounding."""
    return json.dumps(
        {
            "version": library.version,
            "min_intensity": float(library.min_intensity),
            "source_blanks": list(library.source_blanks),
            "ions": [
                {
                    "mz": float(ion.mz),
                    "max_intensity": float(ion.max_intensity),
                    "scan_count": ion.scan_count,
                    "source_blank": ion.source_blank,
                }
                for ion in library.ions
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def deserialize_background_library(payload: str) -> BlankBackgroundLibrary:
    """Rehydrate only the exact canonical library snapshot bound to a run."""
    try:
        data = json.loads(payload)
        library = BlankBackgroundLibrary(
            ions=tuple(BlankBackgroundIon.from_dict(item) for item in data["ions"]),
            min_intensity=float(data["min_intensity"]),
            source_blanks=tuple(data["source_blanks"]),
            version=data["version"],
        )
        if library.version != BLANK_BACKGROUND_LIBRARY_SCHEMA:
            raise ValueError("unsupported Blank background library version")
        if serialize_background_library(library) != payload:
            raise ValueError("background library snapshot is not canonical")
        if any(
            a.mz > b.mz for a, b in zip(library.ions, library.ions[1:], strict=False)
        ):
            raise ValueError("background library snapshot ions are not ordered")
        return library
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("invalid background library snapshot") from exc


def extract_blank_background_library(
    blank_paths: Sequence[Path],
    dll_dir: Path,
    *,
    min_intensity: float = 25000.0,
    min_scans: int = 3,
    sample_step: int = 3,
    ppm_cluster: float = 5.0,
) -> BlankBackgroundLibrary:
    """Extract stable recurring background ions across specified Blank RAW files."""
    all_source_blanks: list[str] = []
    collected_ions: list[tuple[float, float, str]] = []  # (mz, intensity, source_stem)

    for blank_path in blank_paths:
        stem = blank_path.stem
        all_source_blanks.append(stem)

        with open_raw(blank_path, dll_dir) as handle:
            raw = handle._raw_file
            last_spec = int(raw.RunHeaderEx.LastSpectrum)

            for scan_num in range(1, last_spec + 1, sample_step):
                # Filter for Full MS
                scan_filter = raw.GetFilterForScanNumber(scan_num)
                filter_str = str(getattr(scan_filter, "ToString", lambda: "")())
                if "Full ms [" not in filter_str:
                    continue

                cs = raw.GetCentroidStream(scan_num, False)
                masses = getattr(cs, "Masses", None)
                intensities = getattr(cs, "Intensities", None)
                if masses is None or len(masses) == 0:
                    continue

                m_arr = np.asarray(masses, dtype=np.float64)
                i_arr = np.asarray(intensities, dtype=np.float64)
                mask = i_arr >= min_intensity

                for m, intensity in zip(m_arr[mask], i_arr[mask], strict=True):
                    collected_ions.append((m, intensity, stem))

    if not collected_ions:
        return BlankBackgroundLibrary(
            ions=(),
            min_intensity=min_intensity,
            source_blanks=tuple(all_source_blanks),
        )

    # Sort all centroids by m/z
    collected_ions.sort(key=lambda x: x[0])

    # Cluster within ppm_cluster tolerance
    clusters: list[list[tuple[float, float, str]]] = []
    curr_cluster: list[tuple[float, float, str]] = []

    for item in collected_ions:
        m, intensity, source = item
        if not curr_cluster:
            curr_cluster.append(item)
        else:
            cluster_median_mz = float(np.median([x[0] for x in curr_cluster]))
            ppm_diff = abs(m - cluster_median_mz) / cluster_median_mz * 1e6
            if ppm_diff <= ppm_cluster:
                curr_cluster.append(item)
            else:
                clusters.append(curr_cluster)
                curr_cluster = [item]

    if curr_cluster:
        clusters.append(curr_cluster)

    # Filter clusters by recurrence threshold
    background_ions: list[BlankBackgroundIon] = []
    for cluster in clusters:
        if len(cluster) >= min_scans:
            rep_mz = float(np.median([x[0] for x in cluster]))
            max_int = float(max(x[1] for x in cluster))
            sources = ",".join(sorted(set(x[2] for x in cluster)))
            background_ions.append(
                BlankBackgroundIon(
                    mz=rep_mz,
                    max_intensity=max_int,
                    scan_count=len(cluster),
                    source_blank=sources,
                )
            )

    # Sort final ions by m/z
    background_ions.sort(key=lambda ion: ion.mz)

    return BlankBackgroundLibrary(
        ions=tuple(background_ions),
        min_intensity=min_intensity,
        source_blanks=tuple(all_source_blanks),
    )


DEFAULT_BACKGROUND_LIBRARY_FILENAME = "default_background_library_15min.json.gz"
_CACHED_DEFAULT_LIBRARY: BlankBackgroundLibrary | None = None


def get_default_background_library_path() -> Path:
    """Return the filesystem path to the packaged default background library."""
    return Path(__file__).parent / "data" / DEFAULT_BACKGROUND_LIBRARY_FILENAME


def get_default_background_library() -> BlankBackgroundLibrary | None:
    """Load or return the cached default background library."""
    global _CACHED_DEFAULT_LIBRARY
    if _CACHED_DEFAULT_LIBRARY is not None:
        return _CACHED_DEFAULT_LIBRARY
    path = get_default_background_library_path()
    if path.is_file():
        _CACHED_DEFAULT_LIBRARY = BlankBackgroundLibrary.load_json(path)
        return _CACHED_DEFAULT_LIBRARY
    return None


def resolve_background_library(
    raw_dir: Path | None = None,
    library_path: Path | str | None = None,
) -> BlankBackgroundLibrary | None:
    """Resolve the appropriate background library for a run.

    1. If library_path is explicitly provided, load it.
    2. If raw_dir contains a cached blank library, load it.
    3. Otherwise return None, and blank qualification does nothing.

    There is no fallback to the packaged default. That default was measured on a
    15-minute gradient, and applying it to a run acquired on a different method
    marks peaks as system background on the strength of a background
    population the run never saw. A blank
    library measured for the method is an input the caller supplies, not
    something a run inherits. ``get_default_background_library`` stays available
    for a caller that explicitly wants it.
    """
    if library_path is not None:
        p = Path(library_path)
        if not p.is_file():
            raise FileNotFoundError(f"Blank background library not found: {p}")
        return BlankBackgroundLibrary.load_json(p)
    if raw_dir is not None:
        for candidate_name in ("blank_library.json.gz", "blank_library.json"):
            cached = raw_dir / candidate_name
            if cached.is_file():
                return BlankBackgroundLibrary.load_json(cached)
    return None


def apply_blank_qualification_to_peaks(
    peaks: Sequence[DiscoveryPeak],
    library: BlankBackgroundLibrary | None,
    *,
    ppm_tol: float = 10.0,
    fold_change_threshold: float = 3.0,
) -> tuple[DiscoveryPeak, ...]:
    """Mark peaks of pure background ions; a blank match alone does not.

    A peak matching a blank ion within ppm_tol whose height is below
    fold_change_threshold times the blank intensity is system background: it
    is not admitted, its review priority is LOW and its reason explains the
    match. A higher peak keeps its admission and notes the match.
    """
    if library is None or not library.ions:
        return tuple(peaks)

    updated: list[DiscoveryPeak] = []
    for candidate in peaks:
        matches = library.match(candidate.precursor_mz, ppm_tol=ppm_tol)
        if not matches:
            updated.append(candidate)
            continue

        best_ion, delta_ppm = matches[0]
        blank_int = best_ion.max_intensity
        tissue_height = float(candidate.ms1_height)
        fc = (tissue_height / blank_int) if blank_int > 0 else 0.0

        if fc < fold_change_threshold:
            reason_msg = (
                f"system background: blank m/z {best_ion.mz:.4f}, "
                f"delta {delta_ppm:+.1f} ppm, "
                f"FC {fc:.2f}x < {fold_change_threshold:.1f}x"
            )
            updated.append(
                replace(
                    candidate,
                    blank_background=True,
                    review_priority="LOW",
                    reason=reason_msg,
                )
            )
        else:
            override_msg = (
                f"{candidate.reason}; blank match m/z {best_ion.mz:.4f} "
                f"({delta_ppm:+.1f} ppm) "
                f"but high abundance tissue signal ({fc:.1f}x)"
            )
            updated.append(
                replace(
                    candidate,
                    reason=override_msg,
                )
            )

    return tuple(updated)
