from xic_extractor.discovery.models import ReviewPriority
from xic_extractor.discovery.settings import DiscoverySettings

_RT_DELTA_EPSILON_MIN = 1e-9


def assign_review_priority(
    *,
    seed_event_count: int,
    ms1_peak_found: bool,
    ms1_seed_delta_min: float | None,
    settings: DiscoverySettings,
) -> ReviewPriority:
    if (
        seed_event_count >= 2
        and ms1_peak_found
        and ms1_seed_delta_min is not None
        and _within_padding(ms1_seed_delta_min, settings=settings)
    ):
        return "HIGH"
    if ms1_peak_found:
        return "MEDIUM"
    return "LOW"


def build_candidate_reason(
    *,
    seed_event_count: int,
    ms1_peak_found: bool,
    ms1_seed_delta_min: float | None,
    settings: DiscoverySettings,
) -> str:
    if not ms1_peak_found:
        return "strict MS2 NL seed; MS1 peak missing"
    if (
        seed_event_count >= 2
        and ms1_seed_delta_min is not None
        and _within_padding(ms1_seed_delta_min, settings=settings)
    ):
        return "strong MS2 NL seed group; MS1 peak found near seed RT"
    if (
        seed_event_count >= 2
        and ms1_seed_delta_min is not None
        and not _within_padding(ms1_seed_delta_min, settings=settings)
    ):
        return "MS1 peak offset from seed RT"
    return "single MS2 NL seed; MS1 peak found"


def _within_padding(delta_min: float, *, settings: DiscoverySettings) -> bool:
    return abs(delta_min) <= settings.ms1_search_padding_min + _RT_DELTA_EPSILON_MIN
