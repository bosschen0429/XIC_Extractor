"""End-to-end Pt pattern Discovery pipeline.

Wires the Task 2 matcher (``xic_extractor.discovery.delta_mass_patterns``),
the Task 3 CSV/JSON IO (``xic_extractor.discovery.delta_mass_csv``), and the
Task 4 preset stack (``xic_extractor.presets``) into a single
``run_pt_pattern_discovery()`` entry point: Detected Features CSV (+ optional
CID-NL(dR) candidates CSV for the composite strategy) in, Pt Candidate CSV,
Pt Review CSV, and Pt Sidecar JSON out.

See ``docs/superpowers/plans/2026-07-01-pt-pattern-discovery-implementation-plan.md``
Task 5 (pipeline steps + CID-NL base join predicate) for the binding contract
this module implements.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from xic_extractor.discovery.delta_mass_csv import (
    DeltaMassDiscoveryResult,
    load_detected_features_csv,
    write_pt_pattern_candidates_csv,
    write_pt_pattern_review_csv,
    write_pt_pattern_sidecar_json,
)
from xic_extractor.discovery.delta_mass_patterns import (
    PtPatternCandidate,
    match_delta_mass_pattern,
)
from xic_extractor.presets import apply_to_delta_mass_discovery, load_preset

if TYPE_CHECKING:
    from xic_extractor.diagnostics.timing import TimingRecorder

_ALLOWED_CID_NL_TAG = "DNA_dR"
_ALLOWED_CID_NL_STATES = frozenset(
    {"ms1_feature_nl_supported", "ms1_feature_nl_rescued"}
)
_CID_NL_REQUIRED_COLUMNS = (
    "ms1_feature_row_id",
    "neutral_loss_tag",
    "discovery_candidate_state",
)
_CID_NL_MZ_TOLERANCE_PPM = 20.0
_CID_NL_RT_TOLERANCE_SEC = 30.0


@dataclass(frozen=True)
class DeltaMassDiscoveryOutputs:
    """Output artifact paths from a completed ``run_pt_pattern_discovery()`` run."""

    candidates_csv: Path
    review_csv: Path
    sidecar_json: Path


@dataclass(frozen=True)
class _CidNlCandidateRow:
    """A typed, validated row parsed from an existing ``discovery_candidates.csv``.

    Only the fields needed for the CID-NL base join predicate are kept.
    ``precursor_mz``, ``ms1_apex_rt``, and ``evidence_score`` are optional
    because they are only required by the m/z/RT fallback join path, not by
    the same-feature-identity join path (plan lines 809-813).

    ``sample_stem`` is not itself a required input column: the real
    ``discovery_candidates.csv`` contract (``xic_extractor.discovery.models``)
    has no ``sample_stem`` column of its own -- ``ms1_feature_row_id`` is
    built as ``sample_stem|neutral_loss_tag|precursor_mz|rt_identity``
    (``build_ms1_feature_row_id``), so ``sample_stem`` is derived from its
    leading ``|``-delimited segment when a ``sample_stem`` column is absent.
    """

    sample_stem: str
    ms1_feature_row_id: str
    neutral_loss_tag: str
    discovery_candidate_state: str
    precursor_mz: float | None
    ms1_apex_rt: float | None
    evidence_score: float | None


def run_pt_pattern_discovery(
    *,
    detected_features_csv: Path,
    output_dir: Path,
    preset_name: str = "pt_pattern",
    cid_nl_candidates_csv: Path | None = None,
    timing_recorder: "TimingRecorder | None" = None,
) -> DeltaMassDiscoveryOutputs:
    """Run the Pt pattern Discovery pipeline end-to-end.

    Loads ``preset_name``, rejects it unless it carries a ``delta_mass`` tag,
    loads and matches the Detected Features CSV, optionally joins CID-NL(dR)
    base evidence for the composite (``pt_pattern_cid_nl_dr``) strategy, and
    writes ``pt_pattern_candidates.csv``, ``pt_pattern_review.csv``, and
    ``pt_pattern_sidecar.json`` into ``output_dir``.
    """

    preset = load_preset(preset_name)
    if not any(tag.strategy == "delta_mass" for tag in preset.tags):
        raise ValueError(
            f"preset {preset_name!r} is not a 'pt_pattern' preset (no "
            "'delta_mass' tag); use a Pt pattern preset such as 'pt_pattern' "
            "or 'pt_pattern_cid_nl_dr'"
        )
    is_composite = any(tag.strategy == "neutral_loss" for tag in preset.tags)
    if is_composite and cid_nl_candidates_csv is None:
        raise ValueError(
            f"preset {preset_name!r} requires cid_nl_candidates_csv (composite "
            "Pt pattern + CID-NL(dR) strategy); pass --cid-nl-candidates-csv"
        )

    settings = apply_to_delta_mass_discovery(preset)

    features = load_detected_features_csv(detected_features_csv)
    candidates = match_delta_mass_pattern(features, settings=settings)

    if is_composite:
        cid_nl_rows = _load_cid_nl_candidate_rows(cid_nl_candidates_csv)  # type: ignore[arg-type]
        source_feature_row_id_by_feature_id = {
            feature.feature_id: feature.source_feature_row_id
            for feature in features
        }
        candidates = _apply_cid_nl_composite_join(
            candidates,
            cid_nl_rows=cid_nl_rows,
            source_feature_row_id_by_feature_id=source_feature_row_id_by_feature_id,
        )

    if timing_recorder is not None:
        timing_recorder.record(
            "pt_pattern_discovery",
            elapsed_sec=0.0,
            metrics={"candidate_count": len(candidates)},
        )

    candidates_path = output_dir / "pt_pattern_candidates.csv"
    review_path = output_dir / "pt_pattern_review.csv"
    sidecar_path = output_dir / "pt_pattern_sidecar.json"

    write_pt_pattern_candidates_csv(candidates_path, candidates)
    write_pt_pattern_review_csv(review_path, candidates)
    write_pt_pattern_sidecar_json(
        sidecar_path,
        result=DeltaMassDiscoveryResult(
            method_label=settings.method_label,
            pattern=settings.pattern,
            detected_features_csv=str(detected_features_csv),
            cid_nl_candidates_csv=(
                str(cid_nl_candidates_csv)
                if cid_nl_candidates_csv is not None
                else None
            ),
            candidates=candidates,
        ),
    )

    return DeltaMassDiscoveryOutputs(
        candidates_csv=candidates_path,
        review_csv=review_path,
        sidecar_json=sidecar_path,
    )


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

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or ()
        missing_columns = [
            column for column in _CID_NL_REQUIRED_COLUMNS if column not in fieldnames
        ]
        if missing_columns:
            raise ValueError(
                f"{path}: CID-NL candidates CSV is missing required column(s): "
                f"{', '.join(missing_columns)}"
            )
        rows = list(reader)

    parsed: list[_CidNlCandidateRow] = []
    for row_index, row in enumerate(rows):
        state = _parse_cid_nl_row_state(row, path=path, row_index=row_index)
        if state not in _ALLOWED_CID_NL_STATES:
            continue
        parsed.append(_parse_cid_nl_row(row, path=path, row_index=row_index))
    return tuple(parsed)


def _parse_cid_nl_row_state(
    row: dict[str, str | None], *, path: Path, row_index: int
) -> str:
    """Parse the fields required on EVERY row regardless of join eligibility.

    ``neutral_loss_tag`` and ``discovery_candidate_state`` make a row
    structurally identifiable/filterable; a row genuinely missing either is
    malformed regardless of its state, so this always validates them.
    Returns the row's ``discovery_candidate_state`` so the caller can decide
    whether the row is eligible for the stricter join-field parse.
    """

    def _row_label() -> str:
        return f"{path} row {row_index}"

    _required_text(row.get("neutral_loss_tag"), "neutral_loss_tag", _row_label())
    return _required_text(
        row.get("discovery_candidate_state"),
        "discovery_candidate_state",
        _row_label(),
    )


def _parse_cid_nl_row(
    row: dict[str, str | None], *, path: Path, row_index: int
) -> _CidNlCandidateRow:
    """Parse a row already known to be eligible for the CID-NL base join.

    Only called for rows whose ``discovery_candidate_state`` is in
    ``_ALLOWED_CID_NL_STATES``; validates the join-only fields
    (``ms1_feature_row_id``, and the optional-but-finite-when-present
    ``precursor_mz``/``ms1_apex_rt``/``evidence_score``) that ineligible-state
    rows are not required to carry.
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
    sample_stem = _optional_text(row.get("sample_stem")) or _sample_stem_from_row_id(
        ms1_feature_row_id
    )

    return _CidNlCandidateRow(
        sample_stem=sample_stem,
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


def _apply_cid_nl_composite_join(
    candidates: tuple[PtPatternCandidate, ...],
    *,
    cid_nl_rows: tuple[_CidNlCandidateRow, ...],
    source_feature_row_id_by_feature_id: dict[str, str | None],
) -> tuple[PtPatternCandidate, ...]:
    """Join each Pt base candidate against CID-NL(dR) base evidence.

    Implements the plan's CID-NL base join predicate (plan lines 814-838):
    same-feature identity join is tried first and always wins over the m/z/RT
    fallback join when both could apply; fallback is only used when Pt
    feature identity cannot be compared (no ``source_feature_row_id`` on the
    base feature). Candidates with no CID-NL match are excluded from the
    composite output entirely (plan line 839).
    """

    eligible_rows = [
        row
        for row in cid_nl_rows
        if row.neutral_loss_tag == _ALLOWED_CID_NL_TAG
        and row.discovery_candidate_state in _ALLOWED_CID_NL_STATES
    ]

    joined: list[PtPatternCandidate] = []
    for candidate in candidates:
        sample_rows = [
            row for row in eligible_rows if row.sample_stem == candidate.sample_stem
        ]
        base_source_feature_row_id = source_feature_row_id_by_feature_id.get(
            candidate.base_feature_id
        )

        match: _CidNlCandidateRow | None = None
        join_mode = ""
        if base_source_feature_row_id:
            identity_matches = [
                row
                for row in sample_rows
                if row.ms1_feature_row_id
                in {base_source_feature_row_id, candidate.base_feature_id}
            ]
            if identity_matches:
                match = _best_identity_match(candidate, identity_matches)
                join_mode = "same_feature_id"

        if match is None:
            fallback_matches = [
                row
                for row in sample_rows
                if _fallback_matches(candidate, row)
            ]
            if fallback_matches:
                match = _best_fallback_match(candidate, fallback_matches)
                join_mode = "mz_rt_fallback"

        if match is None:
            continue

        joined.append(_mark_composite_supported(candidate, join_mode=join_mode))

    return tuple(joined)


def _best_identity_match(
    candidate: PtPatternCandidate, matches: list[_CidNlCandidateRow]
) -> _CidNlCandidateRow:
    """Tie-break for multiple same-feature-identity matches (plan line 833):
    highest ``evidence_score``, then smallest absolute RT delta."""

    def _key(row: _CidNlCandidateRow) -> tuple[float, float]:
        rt_delta = (
            abs(row.ms1_apex_rt - candidate.base_rt_apex_min)
            if row.ms1_apex_rt is not None
            else 0.0
        )
        evidence_score = row.evidence_score if row.evidence_score is not None else 0.0
        return (-evidence_score, rt_delta)

    return min(matches, key=_key)


def _fallback_matches(candidate: PtPatternCandidate, row: _CidNlCandidateRow) -> bool:
    if row.precursor_mz is None or row.ms1_apex_rt is None:
        return False
    base_mz = candidate.base_mz
    ppm_error = abs((row.precursor_mz - base_mz) / base_mz * 1e6)
    if ppm_error > _CID_NL_MZ_TOLERANCE_PPM:
        return False
    rt_delta_sec = abs(row.ms1_apex_rt - candidate.base_rt_apex_min) * 60.0
    return rt_delta_sec <= _CID_NL_RT_TOLERANCE_SEC


def _best_fallback_match(
    candidate: PtPatternCandidate, matches: list[_CidNlCandidateRow]
) -> _CidNlCandidateRow:
    """Tie-break for multiple m/z/RT fallback matches (plan lines 836-838):
    smallest absolute RT delta, then smallest absolute m/z ppm, then highest
    ``evidence_score``."""

    def _key(row: _CidNlCandidateRow) -> tuple[float, float, float]:
        assert row.ms1_apex_rt is not None
        assert row.precursor_mz is not None
        rt_delta_sec = abs(row.ms1_apex_rt - candidate.base_rt_apex_min) * 60.0
        ppm_error = abs(
            (row.precursor_mz - candidate.base_mz) / candidate.base_mz * 1e6
        )
        evidence_score = row.evidence_score if row.evidence_score is not None else 0.0
        return (rt_delta_sec, ppm_error, -evidence_score)

    return min(matches, key=_key)


def _mark_composite_supported(
    candidate: PtPatternCandidate, *, join_mode: str
) -> PtPatternCandidate:
    reason = candidate.reason
    if join_mode == "mz_rt_fallback":
        reason = f"{reason}; CID-NL(dR) base matched via m/z/RT fallback"
    else:
        reason = f"{reason}; CID-NL(dR) base matched via same-feature identity"

    return replace(
        candidate,
        candidate_state="pt_pattern_cid_nl_supported",
        cid_nl_base_status="matched",
        cid_nl_base_join_mode=join_mode,
        reason=reason,
    )
