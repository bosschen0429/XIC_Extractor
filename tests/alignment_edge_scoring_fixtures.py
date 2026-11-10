"""Helpers shared by test_alignment_edge_scoring and other test modules."""

from __future__ import annotations

from tests.owner_peak_fixtures import measured_peak
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)


def _owner(
    sample_stem: str,
    *,
    neutral_loss_tag: str = "DNA_dR",
    precursor_mz: float = 250.0,
    product_mz: float = 150.0,
    observed_neutral_loss_da: float = 100.0,
    owner_apex_rt: float = 10.00,
    owner_area: float = 1000.0,
    evidence_score: int = 80,
    seed_event_count: int = 2,
    assignment_reason: str = "same_apex_window",
    support_seed_rt: float | None = None,
) -> SampleLocalMS1Owner:
    primary = IdentityEvent(
        candidate_id=f"{sample_stem}#primary",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        neutral_loss_tag=neutral_loss_tag,
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=observed_neutral_loss_da,
        seed_rt=owner_apex_rt,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
    )
    support = ()
    if support_seed_rt is not None:
        support = (
            IdentityEvent(
                candidate_id=f"{sample_stem}#support",
                sample_stem=sample_stem,
                raw_file=f"{sample_stem}.raw",
                neutral_loss_tag=neutral_loss_tag,
                precursor_mz=precursor_mz,
                product_mz=product_mz,
                observed_neutral_loss_da=observed_neutral_loss_da,
                seed_rt=support_seed_rt,
                evidence_score=evidence_score,
                seed_event_count=1,
            ),
        )
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample_stem}",
        sample_stem=sample_stem,
        raw_file=f"{sample_stem}.raw",
        peak=measured_peak(
            mz=precursor_mz,
            apex_rt=owner_apex_rt,
            start_rt=owner_apex_rt - 0.05,
            end_rt=owner_apex_rt + 0.05,
            area=owner_area,
            height=100.0,
        ),
        primary_identity_event=primary,
        supporting_events=support,
        assignment_reason=assignment_reason,
    )
