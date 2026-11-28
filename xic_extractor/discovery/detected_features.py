"""Sample-local detected MS1 features, shared by Delta Mass and its sources."""

from __future__ import annotations

from dataclasses import dataclass

LOCAL_MS1_FEATURE_SOURCE = "local_ms1_feature"
LOCAL_MS1_FEATURE_TRACE_QUALITY_OK = "OK"
LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT = "WEAK_SUPPORT"


@dataclass(frozen=True)
class DetectedFeature:
    """A single sample-local detected MS1 feature.

    ``source_feature_row_id`` is an optional pointer back to the originating
    row (e.g. an existing CID-NL ``peak_id``) used by
    later composite/intersection strategies; it is not required for delta-mass
    matching.
    """

    sample_stem: str
    feature_id: str
    source_feature_row_id: str | None
    mz: float
    rt_apex_min: float
    rt_start_min: float | None
    rt_end_min: float | None
    height: float
    area: float | None
    trace_quality: str
    shape_score: float | None
    source: str
