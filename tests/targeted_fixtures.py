"""Synthetic Targeted inputs shared by method and Workbench tests."""

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.hypotheses import (
    AuditTrail,
    EvidenceVector,
    IntegrationResult,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import PeakDetectionResult
from xic_extractor.peak_detection.peak_selection import select_peak_and_area
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    physical_candidates_result,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    enumerate_physical_segment_universe,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_ENVELOPE_POLICY,
)


def selected_targeted_hypothesis(
    integration: IntegrationResult,
    *,
    sample_name: str = "SampleA",
    target_label: str = "WithNL",
    role: str = "Analyte",
    istd_pair: str = "",
) -> PeakHypothesis:
    """A selected owner measurement for presentation and anchor fixtures."""
    return PeakHypothesis(
        hypothesis_id=f"{sample_name}|{target_label}|selected",
        trace_group_id=f"{sample_name}|{target_label}|targeted",
        target_label=target_label,
        role=role,
        istd_pair=istd_pair,
        analysis_mode="targeted",
        integration=integration,
        evidence=EvidenceVector(reason="selected spine"),
        audit=AuditTrail(selected=True),
    )


def targeted_rows():
    """A synthetic deuterated ISTD and its paired analyte."""
    common = {
        "mz": "250",
        "expected_rt_min": "2",
        "cid_nl_loss_da": "116.0474",
        "role": "analyte",
        "istd_pair": "",
    }
    return [
        {
            **common,
            "label": "standard",
            "role": "istd",
            "isotope_label_type": "deuterated",
            "note": "標準品",
        },
        {
            **common,
            "label": "analyte",
            "istd_pair": "standard",
            "paired_rt_relation": "istd_not_later_than_pair",
            "note": "方法 A",
        },
    ]


class ScopedMs2WindowReader:
    """Fake-reader ``read_ms2_window``: scope admits events; errors are kept."""

    def read_ms2_window(self, rt_min, rt_max, *, precursor_scope):
        return tuple(
            event
            for event in self.iter_ms2_scans(rt_min, rt_max)
            if event.scan is None
            or precursor_scope(event.scan.precursor_mz, event.scan.precursor_metadata)
        )


def physical_peak_result(
    rt, intensity, config: ExtractionConfig, *, preferred_rt: float
) -> PeakDetectionResult:
    """Select from the Targeted physical-segment population of one trace."""
    prepared = prepare_physical_signal(rt, intensity)
    universe = enumerate_physical_segment_universe(
        prepared, selection_policy_version=PHYSICAL_PEAK_ENVELOPE_POLICY
    )
    return select_peak_and_area(
        physical_candidates_result(
            prepared, universe, selection_policy_version=PHYSICAL_PEAK_ENVELOPE_POLICY
        ),
        preferred_rt=preferred_rt,
    )
