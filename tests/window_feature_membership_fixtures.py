"""Helpers shared by test_window_feature_membership and other test modules."""

from __future__ import annotations

from tests.owner_peak_fixtures import measured_peak
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner


def _synthetic_owner(
    sample: str,
    owner_id: str,
    rt_min: float,
    *,
    precursor_mz: float = 500.0,
    product_mz: float = 383.9526,
) -> SampleLocalMS1Owner:
    event = IdentityEvent(
        candidate_id=owner_id,
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        neutral_loss_tag="DNA_dR",
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=precursor_mz - product_mz,
        seed_rt=rt_min,
        evidence_score=90,
        seed_event_count=3,
    )
    return SampleLocalMS1Owner(
        owner_id=owner_id,
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        peak=measured_peak(
            mz=precursor_mz,
            apex_rt=rt_min,
            start_rt=rt_min - 0.05,
            end_rt=rt_min + 0.05,
            area=1000.0,
            height=100.0,
        ),
        primary_identity_event=event,
        supporting_events=(),
        assignment_reason="same_apex_window",
    )
