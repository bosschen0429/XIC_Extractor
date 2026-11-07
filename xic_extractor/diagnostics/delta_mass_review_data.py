"""Saved Delta Mass candidates and newly acquired, read-only review evidence.

This artifact preserves candidate answers. Its RAW series and MS2 spectra are
new diagnostic reads, not a replay of the original detector or its decisions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, TypeVar, cast

from xic_extractor.configuration.cid_nl_profile import CidNlMassPolicy
from xic_extractor.evidence.pt_envelope import PtEnvelopeComparison
from xic_extractor.ms2_scan_models import Ms2PrecursorMetadata
from xic_extractor.tabular_io import write_delimited_rows

REVIEW_SCHEMA = "delta_mass_candidate_review"


@dataclass(frozen=True)
class ReviewPosition:
    label: str
    delta_da: float
    expected_mz: float
    observed_mz: float | None
    position_status: str
    raw_rt: tuple[float, ...] = ()
    raw_intensity: tuple[float, ...] = ()
    feature_id: str = ""
    peak_rt_window: tuple[float, float] | None = None
    feature_mz: float | None = None

    @property
    def query_mz(self) -> float:
        return self.observed_mz if self.observed_mz is not None else self.expected_mz


@dataclass(frozen=True)
class ReviewSpectrum:
    scan_number: int
    rt_min: float
    precursor_mz: float
    precursor_metadata: Ms2PrecursorMetadata | None
    mz: tuple[float, ...]
    intensity: tuple[float, ...]


@dataclass(frozen=True)
class ReviewReadError:
    scan_number: int
    requested_rt_window: tuple[float, float]
    message: str


@dataclass(frozen=True)
class ReviewNlMatch:
    scan_number: int
    precursor_mz: float
    product_mz: float
    product_base_ratio: float
    observed_loss_da: float | None
    loss_error_ppm: float | None
    reference_source: str
    status: str


@dataclass(frozen=True)
class ReviewNlPosition:
    position_label: str
    tag: str
    neutral_loss_da: float
    status: str
    related_scan_numbers: tuple[int, ...]
    matches: tuple[ReviewNlMatch, ...]


@dataclass(frozen=True)
class ReviewNlMethod:
    preset: str
    mass_policy: CidNlMassPolicy


@dataclass(frozen=True)
class ReviewCandidate:
    candidate_id: str
    sample_stem: str
    method_label: str
    pattern_id: str
    status: str
    reject_reason: str
    base_mz: float
    base_rt_apex_min: float
    positions: tuple[ReviewPosition, ...]
    raw_file: str
    xic_ppm: float
    rt_window: tuple[float, float]
    spectra: tuple[ReviewSpectrum, ...] = ()
    ms2_read_errors: tuple[ReviewReadError, ...] = ()
    neutral_losses: tuple[ReviewNlPosition, ...] = ()
    envelope: PtEnvelopeComparison | None = None


@dataclass(frozen=True)
class DeltaMassReview:
    discovery_directory: str
    candidates: tuple[ReviewCandidate, ...]
    nl_method: ReviewNlMethod | None = None


def write_delta_mass_review(review: DeltaMassReview, path: Path) -> None:
    payload = {"schema_version": REVIEW_SCHEMA, **asdict(review)}
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_delta_mass_nl_summary(review: DeltaMassReview, path: Path) -> None:
    """One candidate/tag row; expose the position vector, not a new admission rule.

    An intersection here means a saved Delta Mass candidate has NL support at
    at least one explicitly named position. It never implies both positions,
    a conjunction of all NL tags, chemical identity or Matrix eligibility.
    """
    rows = []
    for candidate in review.candidates:
        for tag in dict.fromkeys(n.tag for n in candidate.neutral_losses):
            evidence = [n for n in candidate.neutral_losses if n.tag == tag]
            supported = [n.position_label for n in evidence if n.status == "supported"]
            scans = sorted(
                {
                    m.scan_number
                    for n in evidence
                    for m in n.matches
                    if m.status == "supported"
                }
            )
            rows.append(
                {
                    "sample_stem": candidate.sample_stem,
                    "candidate_id": candidate.candidate_id,
                    "base_mz": candidate.base_mz,
                    "base_rt_apex_min": candidate.base_rt_apex_min,
                    "delta_mass_status": candidate.status,
                    "nl_tag": tag,
                    "neutral_loss_da": evidence[0].neutral_loss_da,
                    "intersection_status": "delta_mass_rejected"
                    if candidate.status != "candidate"
                    else "supported"
                    if supported
                    else "not_established",
                    "supported_positions": ";".join(supported),
                    "position_results": ";".join(
                        f"{n.position_label}:{n.status}" for n in evidence
                    ),
                    "related_scan_count": len(
                        {scan for n in evidence for scan in n.related_scan_numbers}
                    ),
                    "support_scan_count": len(scans),
                    "support_scan_ids": ";".join(map(str, scans)),
                }
            )
    write_delimited_rows(
        path,
        rows,
        (
            "sample_stem",
            "candidate_id",
            "base_mz",
            "base_rt_apex_min",
            "delta_mass_status",
            "nl_tag",
            "neutral_loss_da",
            "intersection_status",
            "supported_positions",
            "position_results",
            "related_scan_count",
            "support_scan_count",
            "support_scan_ids",
        ),
        formatter=str,
    )


def decode_delta_mass_review(payload: object) -> DeltaMassReview:
    """Decode the review schema, checking paired observation-array lengths."""
    root = cast(dict[str, Any], payload)
    if root.get("schema_version") != REVIEW_SCHEMA:
        raise ValueError("Unsupported Delta Mass review schema")
    candidates = []
    for row in root["candidates"]:
        positions = []
        for position in row["positions"]:
            rt, intensity = tuple(position["raw_rt"]), tuple(position["raw_intensity"])
            if len(rt) != len(intensity):
                raise ValueError("Position RAW arrays must pair")
            positions.append(
                _record(
                    ReviewPosition,
                    position,
                    raw_rt=rt,
                    raw_intensity=intensity,
                    peak_rt_window=_tuple_or_none(position.get("peak_rt_window")),
                )
            )
        spectra = []
        for spectrum in row["spectra"]:
            mz, intensity = tuple(spectrum["mz"]), tuple(spectrum["intensity"])
            if len(mz) != len(intensity):
                raise ValueError("MS2 arrays must pair")
            meta = spectrum["precursor_metadata"]
            spectra.append(
                _record(
                    ReviewSpectrum,
                    spectrum,
                    mz=mz,
                    intensity=intensity,
                    precursor_metadata=Ms2PrecursorMetadata(**meta)
                    if meta is not None
                    else None,
                )
            )
        losses = tuple(
            _record(
                ReviewNlPosition,
                n,
                related_scan_numbers=tuple(n["related_scan_numbers"]),
                matches=tuple(ReviewNlMatch(**m) for m in n["matches"]),
            )
            for n in row.get("neutral_losses", ())
        )
        errors = tuple(
            _record(
                ReviewReadError, e, requested_rt_window=tuple(e["requested_rt_window"])
            )
            for e in row["ms2_read_errors"]
        )
        candidates.append(
            _record(
                ReviewCandidate,
                row,
                positions=tuple(positions),
                spectra=tuple(spectra),
                neutral_losses=losses,
                ms2_read_errors=errors,
                rt_window=tuple(row["rt_window"]),
                envelope=(
                    _record(
                        PtEnvelopeComparison,
                        row["envelope"],
                        core_rt_window=_tuple_or_none(
                            row["envelope"].get("core_rt_window")
                        ),
                        position_labels=tuple(row["envelope"]["position_labels"]),
                        observed_relative=tuple(row["envelope"]["observed_relative"]),
                        expected_relative=tuple(row["envelope"]["expected_relative"]),
                    )
                    if row.get("envelope") is not None
                    else None
                ),
            )
        )
    method = root.get("nl_method")
    return DeltaMassReview(
        root["discovery_directory"],
        tuple(candidates),
        ReviewNlMethod(method["preset"], CidNlMassPolicy(**method["mass_policy"]))
        if method is not None
        else None,
    )


Record = TypeVar("Record")


def _record(cls: type[Record], row: dict[str, Any], **nested: Any) -> Record:
    """Construct current fields; retired review fields are not consumed."""
    return cls(
        **{
            f.name: row[f.name]
            for f in fields(cast(Any, cls))
            if f.name in row and f.name not in nested
        },
        **nested,
    )


def _tuple_or_none(value: Any) -> Any:
    return None if value is None else tuple(value)
