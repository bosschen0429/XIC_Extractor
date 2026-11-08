"""RAW-to-delta-mass orchestration and CID-NL(dR) composite admission."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from xic_extractor.configuration.mass_recalibration import (
    serialize_mass_recalibration_result,
)
from xic_extractor.discovery.delta_mass_csv import (
    DeltaMassDiscoveryOutputs,
    write_delta_mass_outputs,
)
from xic_extractor.discovery.delta_mass_patterns import (
    DeltaMassCandidate,
    complete_delta_mass_candidates,
    prepare_delta_mass_candidates,
)
from xic_extractor.discovery.delta_mass_strategy import DeltaMassDiscoveryStrategy
from xic_extractor.discovery.detected_features import write_detected_features_csv
from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureSettings,
    build_local_ms1_feature_table,
)
from xic_extractor.raw_reader import open_raw
from xic_extractor.tabular_io import read_delimited_rows

_ALLOWED_CID_NL_TAG = "DNA_dR"
_ALLOWED_CID_NL_STATES = frozenset(
    {"ms1_feature_nl_supported", "ms1_feature_nl_rescued"}
)
_CID_NL_REQUIRED_COLUMNS = (
    "candidate_id",
    "ms1_feature_row_id",
    "neutral_loss_tag",
    "discovery_candidate_state",
)
_CID_NL_MZ_TOLERANCE_PPM = 20.0
_CID_NL_RT_TOLERANCE_SEC = 30.0


def detector_settings_for_interval(
    strategy: DeltaMassDiscoveryStrategy, interval_sec: float
) -> LocalMs1FeatureSettings:
    """Convert preset seconds to the nearest positive odd smoothing window."""
    if not math.isfinite(interval_sec) or interval_sec <= 0:
        raise ValueError("at least two distinct MS1 scan times are required")
    points = max(
        1, 2 * round((strategy.smoothing_window_sec / interval_sec - 1) / 2) + 1
    )
    return LocalMs1FeatureSettings(
        gaussian_window_points=points,
        feature_link_ppm=strategy.settings.mz_tolerance_ppm,
    )


def run_delta_mass_discovery(
    *,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path,
    strategy: DeltaMassDiscoveryStrategy,
    cid_nl_candidates_csv: Path | None = None,
) -> DeltaMassDiscoveryOutputs:
    """Detect features per RAW, acquire one batch, then write diagnostic results."""
    raw_paths = sorted(
        p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() == ".raw"
    )
    if not raw_paths:
        raise ValueError(f"no RAW files in {raw_dir}")
    if strategy.cid_nl_composite and cid_nl_candidates_csv is None:
        raise ValueError("composite preset requires --cid-nl-candidates-csv")
    cid_rows = (
        _load_cid_nl_candidate_rows(cid_nl_candidates_csv)
        if cid_nl_candidates_csv is not None and strategy.cid_nl_composite
        else ()
    )
    candidates: list[DeltaMassCandidate] = []
    samples = []
    for raw_path in raw_paths:
        started = time.perf_counter()
        with open_raw(
            raw_path, dll_dir=dll_dir, mass_recalibration=strategy.mass_recalibration
        ) as reader:
            mass_result = reader.mass_recalibration_result
            index = reader.read_ms1_scan_index(centroid=True)
            intervals = np.diff([scan.rt for scan in index]) * 60
            interval = (
                float(np.median(intervals[intervals > 0]))
                if np.any(intervals > 0)
                else 0.0
            )
            detector = detector_settings_for_interval(strategy, interval)
            features, summary, _ = build_local_ms1_feature_table(
                index,
                sample_stem=raw_path.stem,
                raw_path=str(raw_path),
                settings=detector,
            )
            sample_dir = output_dir / raw_path.stem
            sample_dir.mkdir(parents=True, exist_ok=True)
            write_detected_features_csv(sample_dir / "detected_features.csv", features)
            prepared = prepare_delta_mass_candidates(
                features,
                strategy.settings,
                scan_interval_sec=interval,
            )
            acquisition_started = time.perf_counter()
            traces = (
                reader.extract_xic_many(prepared.requests) if prepared.requests else ()
            )
            acquisition_seconds = time.perf_counter() - acquisition_started
            completed = complete_delta_mass_candidates(
                prepared, traces, strategy.settings
            )
        if strategy.cid_nl_composite:
            completed = _join_cid_nl(completed, cid_rows)
        candidates.extend(completed)
        counts = dict(prepared.counts)
        for layer in (3, 4, 5):
            counts[f"layer_{layer}_rejected"] = sum(
                c.rejected_at_layer == layer for c in prepared.candidates
            )
        for layer in (3, 4, 5):
            counts[f"layer_{layer}_passed"] = sum(
                c.rejected_at_layer is None or c.rejected_at_layer > layer
                for c in prepared.candidates
            )
        samples.append(
            {
                "sample_stem": raw_path.stem,
                "feature_count": len(features),
                **counts,
                "candidate_count": sum(c.status == "candidate" for c in completed),
                "layer_4_xic_requests": len(prepared.requests),
                "layer_4_seconds": acquisition_seconds,
                "median_ms1_scan_interval_sec": interval,
                "mass_recalibration": serialize_mass_recalibration_result(mass_result),
                "gaussian_window_points": detector.gaussian_window_points,
                "detector_seconds": summary.wall_time_sec,
                "total_seconds": time.perf_counter() - started,
            }
        )
    sidecar = {
        "preset": strategy.label,
        "method": {
            **asdict(strategy.settings),
            "mass_recalibration": strategy.mass_recalibration.value,
            "height_ratio_policy": "evidence_only",
            "detector": {
                "smoothing_window_sec": strategy.smoothing_window_sec,
                "feature_link_ppm": strategy.settings.mz_tolerance_ppm,
                "spectrum_mode": "vendor_centroid",
            },
        },
        "inputs": {
            "raw_dir": str(raw_dir),
            "raw_files": [p.name for p in raw_paths],
            "cid_nl_candidates_csv": str(cid_nl_candidates_csv)
            if cid_nl_candidates_csv
            else None,
        },
        "samples": samples,
        "status": "diagnostic_only",
    }
    return write_delta_mass_outputs(
        output_dir, tuple(candidates), sidecar, strategy.settings
    )


def _join_cid_nl(
    candidates: tuple[DeltaMassCandidate, ...], rows: tuple[_CidNlCandidateRow, ...]
) -> tuple[DeltaMassCandidate, ...]:
    """Join detector-local bases by the existing bounded m/z/RT predicate."""
    eligible = _eligible_cid_nl_rows(rows)
    joined = []
    for candidate in candidates:
        candidate.cid_nl_base_status = "unmatched"
        matches = [
            row
            for row in eligible
            if row.sample_stem == candidate.sample_stem
            and _fallback_matches(candidate, row)
        ]
        if not matches:
            joined.append(candidate)
            continue
        match = _best_fallback_match(candidate, matches)
        candidate.cid_nl_base_status = "matched"
        candidate.cid_nl_base_candidate_id = match.candidate_id
        candidate.cid_nl_base_join_mode = "mz_rt_fallback"
        joined.append(candidate)
    return tuple(joined)


@dataclass(frozen=True)
class _CidNlCandidateRow:
    """A typed, validated row parsed from an existing ``discovery_candidates.csv``.

    Only the fields needed for the CID-NL base join predicate are kept.
    ``precursor_mz``, ``ms1_apex_rt``, and ``evidence_score`` are optional
    because they are only required by the m/z/RT fallback join path, not by
    the same-feature-identity join path (plan lines 809-813).

    ``candidate_id`` is required because composite output exposes the matched
    CID-NL candidate identity. A source-row locator is provenance, not a
    candidate ID, and must never be substituted into that public field.

    ``sample_stem`` may be supplied explicitly. ``ms1_feature_row_id`` is built
    as ``sample_stem|neutral_loss_tag|precursor_mz|rt_identity``
    (``build_ms1_feature_row_id``), so the sample is also derived from its
    leading ``|``-delimited segment. When both representations are present they
    must agree; the parser never guesses across conflicting sample identities.
    """

    sample_stem: str
    candidate_id: str
    ms1_feature_row_id: str
    neutral_loss_tag: str
    discovery_candidate_state: str
    precursor_mz: float | None
    ms1_apex_rt: float | None
    evidence_score: float | None


def _load_cid_nl_candidate_rows(path: Path) -> tuple[_CidNlCandidateRow, ...]:
    """Load and validate CID-NL(dR) candidate rows for the composite join.

    Every row must carry non-blank ``neutral_loss_tag`` and
    ``discovery_candidate_state`` values (missing/malformed values raise
    ``ValueError`` rather than being silently skipped) -- these fields are
    what make a row identifiable and filterable in the first place, so they
    are validated regardless of state.

    Only rows whose ``discovery_candidate_state`` is in
    ``_ALLOWED_CID_NL_STATES`` are eligible for the CID-NL base join (see the
    plan's join predicate); such rows must additionally carry a non-blank
    ``ms1_feature_row_id``, and finite ``precursor_mz``/``ms1_apex_rt``/
    ``evidence_score`` when present. Rows in any other state -- e.g. the
    legitimate ``review_only_orphan_nl`` state, which by design has no
    ``ms1_feature_row_id`` because it is not backed by an MS1 feature -- are
    excluded from the returned set without raising, since they were never
    join candidates to begin with.
    """

    rows = read_delimited_rows(
        path,
        required_columns=_CID_NL_REQUIRED_COLUMNS,
        delimiter=",",
        encoding="utf-8",
    )

    parsed: list[_CidNlCandidateRow] = []
    for row_index, row in enumerate(rows):
        state = _parse_cid_nl_row_state(row, path=path, row_index=row_index)
        if state not in _ALLOWED_CID_NL_STATES:
            continue
        parsed.append(_parse_cid_nl_row(row, path=path, row_index=row_index))
    seen_candidate_ids: set[str] = set()
    duplicate_candidate_ids: set[str] = set()
    for parsed_row in parsed:
        if parsed_row.candidate_id in seen_candidate_ids:
            duplicate_candidate_ids.add(parsed_row.candidate_id)
        seen_candidate_ids.add(parsed_row.candidate_id)
    if duplicate_candidate_ids:
        raise ValueError(
            f"{path}: duplicate eligible CID-NL candidate_id values: "
            + ", ".join(sorted(duplicate_candidate_ids))
        )
    return tuple(parsed)


def _eligible_cid_nl_rows(
    cid_nl_rows: tuple[_CidNlCandidateRow, ...],
) -> tuple[_CidNlCandidateRow, ...]:
    return tuple(
        row
        for row in cid_nl_rows
        if row.neutral_loss_tag == _ALLOWED_CID_NL_TAG
        and row.discovery_candidate_state in _ALLOWED_CID_NL_STATES
    )


def _parse_cid_nl_row_state(
    row: Mapping[str, str | None], *, path: Path, row_index: int
) -> str:
    """Parse the fields required on EVERY row regardless of join eligibility.

    ``candidate_id``, ``neutral_loss_tag``, and ``discovery_candidate_state``
    make a row structurally identifiable/filterable; a row genuinely missing
    any of them is malformed regardless of its state, so this always validates
    them.
    Returns the row's ``discovery_candidate_state`` so the caller can decide
    whether the row is eligible for the stricter join-field parse.
    """

    def _row_label() -> str:
        return f"{path} row {row_index}"

    candidate_id = _required_text(row.get("candidate_id"), "candidate_id", _row_label())
    _reject_ambiguous_formula_escaped_identity(
        candidate_id,
        field="candidate_id",
        row_label=_row_label(),
    )
    _required_text(row.get("neutral_loss_tag"), "neutral_loss_tag", _row_label())
    return _required_text(
        row.get("discovery_candidate_state"),
        "discovery_candidate_state",
        _row_label(),
    )


def _parse_cid_nl_row(
    row: Mapping[str, str | None], *, path: Path, row_index: int
) -> _CidNlCandidateRow:
    """Parse a row already known to be eligible for the CID-NL base join.

    Only called for rows whose ``discovery_candidate_state`` is in
    ``_ALLOWED_CID_NL_STATES``; validates the join-only fields
    (``ms1_feature_row_id``, coherent sample identity, and the
    optional-but-finite-when-present ``precursor_mz``/``ms1_apex_rt``/
    ``evidence_score``) that ineligible-state rows are not required to carry.
    """

    def _row_label() -> str:
        return f"{path} row {row_index}"

    ms1_feature_row_id = _required_text(
        row.get("ms1_feature_row_id"), "ms1_feature_row_id", _row_label()
    )
    neutral_loss_tag = _required_text(
        row.get("neutral_loss_tag"), "neutral_loss_tag", _row_label()
    )
    discovery_candidate_state = _required_text(
        row.get("discovery_candidate_state"),
        "discovery_candidate_state",
        _row_label(),
    )
    candidate_id = _required_text(row.get("candidate_id"), "candidate_id", _row_label())
    explicit_sample_stem = _optional_text(row.get("sample_stem"))
    derived_sample_stem = _sample_stem_from_row_id(ms1_feature_row_id)
    for field, identity in (
        ("candidate_id", candidate_id),
        ("ms1_feature_row_id", ms1_feature_row_id),
        ("sample_stem", explicit_sample_stem),
        ("derived sample_stem", derived_sample_stem),
    ):
        if identity is not None:
            _reject_ambiguous_formula_escaped_identity(
                identity,
                field=field,
                row_label=_row_label(),
            )
    if explicit_sample_stem is not None and explicit_sample_stem != derived_sample_stem:
        raise ValueError(
            f"{_row_label()} has sample_stem {explicit_sample_stem!r} that does not "
            "match the sample identity derived from ms1_feature_row_id "
            f"({derived_sample_stem!r})"
        )
    sample_stem = explicit_sample_stem or derived_sample_stem

    return _CidNlCandidateRow(
        sample_stem=sample_stem,
        candidate_id=candidate_id,
        ms1_feature_row_id=ms1_feature_row_id,
        neutral_loss_tag=neutral_loss_tag,
        discovery_candidate_state=discovery_candidate_state,
        precursor_mz=_optional_finite_float(
            row.get("precursor_mz"), "precursor_mz", _row_label()
        ),
        ms1_apex_rt=_optional_finite_float(
            row.get("ms1_apex_rt"), "ms1_apex_rt", _row_label()
        ),
        evidence_score=_optional_finite_float(
            row.get("evidence_score"), "evidence_score", _row_label()
        ),
    )


def _sample_stem_from_row_id(ms1_feature_row_id: str) -> str:
    """Derive ``sample_stem`` from ``ms1_feature_row_id``'s leading segment.

    ``build_ms1_feature_row_id`` (``xic_extractor.discovery.models``) joins
    ``sample_stem|neutral_loss_tag|precursor_mz|rt_identity`` with ``|``, so
    the first segment is the sample stem.
    """

    return ms1_feature_row_id.split("|", 1)[0]


def _reject_ambiguous_formula_escaped_identity(
    value: str,
    *,
    field: str,
    row_label: str,
) -> None:
    """Fail closed when an identity may have been Excel-formula escaped.

    The legacy Discovery CSV writer prepends an apostrophe to strings beginning
    with ``=``, ``+``, ``-``, or ``@``. That serialized form is indistinguishable
    from an original identifier that genuinely began with the apostrophe, so a
    composite identity join must not silently unescape or compare it literally.
    """

    if len(value) >= 2 and value[0] == "'" and value[1] in "=+-@":
        raise ValueError(
            f"{row_label} has ambiguous Excel formula-escaped machine identity "
            f"in {field!r}: {value!r}"
        )


def _required_text(value: str | None, field: str, row_label: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{row_label} is missing required field {field!r}")
    return text


def _optional_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _optional_finite_float(
    value: str | None, field: str, row_label: str
) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(
            f"{row_label} has non-numeric optional field {field!r}: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise ValueError(
            f"{row_label} has non-finite optional field {field!r}: {value!r}"
        )
    return parsed


def _fallback_matches(candidate: DeltaMassCandidate, row: _CidNlCandidateRow) -> bool:
    if row.precursor_mz is None or row.ms1_apex_rt is None:
        return False
    base_mz = candidate.base_mz
    ppm_error = abs((row.precursor_mz - base_mz) / base_mz * 1e6)
    if ppm_error > _CID_NL_MZ_TOLERANCE_PPM:
        return False
    rt_delta_sec = abs(row.ms1_apex_rt - candidate.base_rt_apex_min) * 60.0
    return rt_delta_sec <= _CID_NL_RT_TOLERANCE_SEC


def _best_fallback_match(
    candidate: DeltaMassCandidate, matches: list[_CidNlCandidateRow]
) -> _CidNlCandidateRow:
    """Tie-break for multiple m/z/RT fallback matches (plan lines 836-838):
    smallest absolute RT delta, then smallest absolute m/z ppm, then highest
    ``evidence_score``."""

    def _key(row: _CidNlCandidateRow) -> tuple[float, float, float, str]:
        assert row.ms1_apex_rt is not None
        assert row.precursor_mz is not None
        rt_delta_sec = abs(row.ms1_apex_rt - candidate.base_rt_apex_min) * 60.0
        ppm_error = abs(
            (row.precursor_mz - candidate.base_mz) / candidate.base_mz * 1e6
        )
        evidence_score = row.evidence_score if row.evidence_score is not None else 0.0
        return (
            rt_delta_sec,
            ppm_error,
            -evidence_score,
            row.candidate_id,
        )

    return min(matches, key=_key)
