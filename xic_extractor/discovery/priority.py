from xic_extractor.discovery.models import ReviewPriority
from xic_extractor.discovery.settings import DiscoverySettings

_RT_DELTA_EPSILON_MIN = 1e-9


def assign_review_priority(
    *,
    attached_event_count: int,
    ms1_event_delta_min: float,
    settings: DiscoverySettings,
) -> ReviewPriority:
    if attached_event_count >= 2 and _within_padding(
        ms1_event_delta_min, settings=settings
    ):
        return "HIGH"
    return "MEDIUM"


def build_peak_reason(
    *,
    attached_event_count: int,
    ms1_event_delta_min: float,
    settings: DiscoverySettings,
) -> str:
    if attached_event_count < 1:
        return "MS1 peak found; no NL event attaches"
    if attached_event_count >= 2:
        if _within_padding(ms1_event_delta_min, settings=settings):
            return "strong MS2 NL event set; MS1 peak found near event RT"
        return "MS1 peak offset from event RT"
    return "single MS2 NL event; MS1 peak found"


def _within_padding(delta_min: float, *, settings: DiscoverySettings) -> bool:
    return abs(delta_min) <= settings.ms1_search_padding_min + _RT_DELTA_EPSILON_MIN
