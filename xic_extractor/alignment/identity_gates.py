from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from xic_extractor.peak_detection.evidence_semantics import CommonEvidence

EXTREME_BACKFILL_REASON = "extreme_backfill_dependency"
WEAK_SEED_BACKFILL_REASON = "weak_seed_backfill_dependency"
WEAK_SEED_TOLERATED_REASON = "weak_seed_tolerated"

SeedQualityStatus = Literal["unavailable", "missing_lookup", "adequate", "weak"]

_EXTREME_BACKFILL_MIN_BACKFILL_FRACTION = 0.70
_EXTREME_BACKFILL_MAX_DETECTED_SUPPORT = 2
_WEAK_SEED_BACKFILL_MIN_BACKFILL_FRACTION = 0.60
_WEAK_SEED_BACKFILL_MAX_DETECTED_SUPPORT = 3
_WEAK_SEED_MIN_EVIDENCE_SCORE = 60
_WEAK_SEED_MIN_EVENT_COUNT = 2
_WEAK_SEED_MAX_ABS_NL_PPM = 10.0
_WEAK_SEED_MIN_SCAN_SUPPORT_SCORE = 0.5


@dataclass(frozen=True)
class DetectedSeedRef:
    sample_stem: str
    source_candidate_id: str


@dataclass(frozen=True)
class SeedQualitySummary:
    available: bool
    min_evidence_score: float | None = None
    min_seed_event_count: float | None = None
    max_abs_nl_ppm: float | None = None
    min_scan_support_score: float | None = None
    trusted_detected_candidate_count: int = 0
    looked_up_candidate_count: int = 0
    missing_detected_candidate_count: int = 0

    @property
    def missing_lookup_only(self) -> bool:
        return (
            self.available
            and self.looked_up_candidate_count == 0
            and self.missing_detected_candidate_count > 0
        )

    @property
    def weak(self) -> bool:
        if not self.available:
            return False
        if self.missing_detected_candidate_count > 0:
            return True
        if self.trusted_detected_candidate_count >= 2:
            return False
        return (
            (
                self.min_evidence_score is not None
                and self.min_evidence_score < _WEAK_SEED_MIN_EVIDENCE_SCORE
            )
            or (
                self.min_seed_event_count is not None
                and self.min_seed_event_count < _WEAK_SEED_MIN_EVENT_COUNT
            )
            or (
                self.max_abs_nl_ppm is not None
                and self.max_abs_nl_ppm > _WEAK_SEED_MAX_ABS_NL_PPM
            )
            or (
                self.min_scan_support_score is not None
                and self.min_scan_support_score < _WEAK_SEED_MIN_SCAN_SUPPORT_SCORE
            )
        )

    @property
    def weak_seed_signal(self) -> bool:
        if not self.available:
            return False
        if self.missing_detected_candidate_count > 0:
            return True
        return (
            (
                self.min_evidence_score is not None
                and self.min_evidence_score < _WEAK_SEED_MIN_EVIDENCE_SCORE
            )
            or (
                self.min_seed_event_count is not None
                and self.min_seed_event_count < _WEAK_SEED_MIN_EVENT_COUNT
            )
            or (
                self.max_abs_nl_ppm is not None
                and self.max_abs_nl_ppm > _WEAK_SEED_MAX_ABS_NL_PPM
            )
            or (
                self.min_scan_support_score is not None
                and self.min_scan_support_score < _WEAK_SEED_MIN_SCAN_SUPPORT_SCORE
            )
        )

    @property
    def status(self) -> SeedQualityStatus:
        if not self.available:
            return "unavailable"
        if self.missing_lookup_only:
            return "missing_lookup"
        return "weak" if self.weak else "adequate"


SeedCandidateLookup = (
    Mapping[Any, Any] | Callable[[DetectedSeedRef], Any | None]
)


def classify_single_dr_backfill_dependency(
    *,
    neutral_loss_tag: str,
    q_detected: int,
    q_backfill: int,
    cell_count: int,
    seed_quality: SeedQualitySummary | None,
) -> str | None:
    if not is_dr_neutral_loss_tag(neutral_loss_tag):
        return None
    if q_detected <= 0 or cell_count <= 0:
        return None

    backfill_fraction = q_backfill / cell_count
    if (
        q_detected <= _EXTREME_BACKFILL_MAX_DETECTED_SUPPORT
        and backfill_fraction >= _EXTREME_BACKFILL_MIN_BACKFILL_FRACTION
    ):
        return EXTREME_BACKFILL_REASON
    if (
        q_detected <= _WEAK_SEED_BACKFILL_MAX_DETECTED_SUPPORT
        and backfill_fraction >= _WEAK_SEED_BACKFILL_MIN_BACKFILL_FRACTION
        and seed_quality is not None
    ):
        if seed_quality.weak:
            return WEAK_SEED_BACKFILL_REASON
        if seed_quality.weak_seed_signal:
            return WEAK_SEED_TOLERATED_REASON
    return None


def summarize_detected_seed_quality(
    detected_seeds: Sequence[DetectedSeedRef],
    candidate_lookup: SeedCandidateLookup | None,
    *,
    enrichment_available: bool,
) -> SeedQualitySummary:
    if not enrichment_available:
        return SeedQualitySummary(available=False)
    if candidate_lookup is None:
        return SeedQualitySummary(available=False)

    evidence_vectors: list[CommonEvidence] = []
    missing_count = 0
    for seed in detected_seeds:
        candidate = lookup_seed_candidate(seed, candidate_lookup)
        if candidate is None:
            missing_count += 1
            continue
        evidence_vectors.append(_seed_quality_evidence_from_candidate(candidate))

    return SeedQualitySummary(
        available=True,
        min_evidence_score=_min_common_metric(evidence_vectors, "evidence_score"),
        min_seed_event_count=_min_common_metric(evidence_vectors, "seed_event_count"),
        max_abs_nl_ppm=_max_abs_common_metric(
            evidence_vectors,
            "neutral_loss_error_ppm",
        ),
        min_scan_support_score=_min_common_metric(
            evidence_vectors,
            "scan_support_score",
        ),
        trusted_detected_candidate_count=sum(
            1 for evidence in evidence_vectors if _trusted_detected_seed(evidence)
        ),
        looked_up_candidate_count=len(evidence_vectors),
        missing_detected_candidate_count=missing_count,
    )


def _seed_quality_evidence_from_candidate(candidate: Any) -> CommonEvidence:
    """Read only the seed facts used by this Alignment gate."""

    return CommonEvidence(
        source="discovery_candidate",
        evidence_score=_optional_int(_candidate_field(candidate, "evidence_score")),
        seed_event_count=_optional_int(_candidate_field(candidate, "seed_event_count")),
        neutral_loss_error_ppm=_optional_float(
            _candidate_field(candidate, "neutral_loss_mass_error_ppm")
        ),
        scan_support_score=_optional_float(
            _candidate_field(candidate, "ms1_scan_support_score")
        ),
    )


def _candidate_field(candidate: Any, key: str) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(key)
    return getattr(candidate, key, None)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def lookup_seed_candidate(
    seed: DetectedSeedRef,
    candidate_lookup: SeedCandidateLookup,
) -> Any | None:
    if callable(candidate_lookup):
        return candidate_lookup(seed)

    keys = candidate_lookup_keys(seed.source_candidate_id)
    if not keys:
        return None
    source_key = keys[0]

    # Candidate ids are globally emitted as exact ids such as "sample#scan".
    # Avoid head/tail fallbacks here; bare suffix matches can collide across
    # samples and would attach the wrong seed evidence to a row gate.
    tuple_key = (seed.sample_stem, source_key)
    if tuple_key in candidate_lookup:
        return candidate_lookup[tuple_key]
    fallback_tuple_key = ("", source_key)
    if fallback_tuple_key in candidate_lookup:
        return candidate_lookup[fallback_tuple_key]
    if source_key in candidate_lookup:
        return candidate_lookup[source_key]
    return None


def candidate_lookup_keys(source_candidate_id: str) -> tuple[str, ...]:
    source = source_candidate_id.strip()
    if not source:
        return ()
    return (source,)


def is_dr_neutral_loss_tag(tag: str) -> bool:
    return tag == "dR" or tag.endswith("_dR")


def _min_common_metric(
    evidence_vectors: Sequence[CommonEvidence],
    key: str,
) -> float | None:
    values = tuple(
        value
        for evidence in evidence_vectors
        for value in (_number_field(evidence, key),)
        if value is not None
    )
    return min(values) if values else None


def _max_abs_common_metric(
    evidence_vectors: Sequence[CommonEvidence],
    key: str,
) -> float | None:
    values = tuple(
        abs(value)
        for evidence in evidence_vectors
        for value in (_number_field(evidence, key),)
        if value is not None
    )
    return max(values) if values else None


def _trusted_detected_seed(evidence: CommonEvidence) -> bool:
    return (
        evidence.evidence_score is not None
        and evidence.evidence_score >= _WEAK_SEED_MIN_EVIDENCE_SCORE
        and evidence.seed_event_count is not None
        and evidence.seed_event_count >= _WEAK_SEED_MIN_EVENT_COUNT
        and (
            evidence.neutral_loss_error_ppm is None
            or abs(evidence.neutral_loss_error_ppm) <= _WEAK_SEED_MAX_ABS_NL_PPM
        )
        and (
            evidence.scan_support_score is None
            or evidence.scan_support_score >= _WEAK_SEED_MIN_SCAN_SUPPORT_SCORE
        )
    )


def _number_field(item: Any, key: str) -> float | None:
    value = item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
