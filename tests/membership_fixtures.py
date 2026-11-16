"""In-memory CID-NL peak owners for membership tests; no RAW or runtime setup."""

from tests.owner_peak_fixtures import measured_peak
from xic_extractor.alignment.ownership_models import (
    IdentityEvent,
    SampleLocalMS1Owner,
)


def cid_nl_owner(
    sample: str,
    name: str,
    *,
    precursor_mz: float = 500.0,
    apex_rt: float = 10.0,
    evidence_score: int = 80,
    seed_event_count: int = 2,
    supporting_events: tuple[IdentityEvent, ...] = (),
) -> SampleLocalMS1Owner:
    """One MS1 peak with a primary DNA_dR event; RT values are in minutes.

    Defaults provide two seed events at score 80, not measured chemical truth.
    Cases testing admission or weak support override those evidence inputs.
    """
    event = IdentityEvent(
        candidate_id=f"{sample}-{name}-primary",
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        neutral_loss_tag="DNA_dR",
        precursor_mz=precursor_mz,
        product_mz=precursor_mz - 116.0474,
        observed_neutral_loss_da=116.0474,
        seed_rt=apex_rt,
        evidence_score=evidence_score,
        seed_event_count=seed_event_count,
    )
    return SampleLocalMS1Owner(
        owner_id=f"OWN-{sample}-{name}",
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        peak=measured_peak(
            mz=precursor_mz,
            apex_rt=apex_rt,
            start_rt=apex_rt - 0.05,
            end_rt=apex_rt + 0.05,
            area=1_000.0,
            height=100.0,
        ),
        primary_identity_event=event,
        supporting_events=supporting_events,
        assignment_reason="same_apex_window",
    )


def channel_anchor_observations(groups):
    """One method channel per group: each owner is that channel's only peak."""
    from xic_extractor.alignment.rt_drift import RtAnchorObservation

    return tuple(
        RtAnchorObservation(
            f"channel:{index}",
            owner.sample_stem,
            owner.owner_id,
            owner.peak.integration.rt_apex_min,
        )
        for index, group in enumerate(groups)
        for owner in group
    )


def freeze_channel_anchors(
    groups, *, samples, cohorts=None, orders=None, measured_istds=()
):
    """Freeze the batch RT model from per-group channel observations."""
    from xic_extractor.alignment.rt_drift import BatchRtModelAccumulator

    accumulator = BatchRtModelAccumulator(
        sample_stems=tuple(samples),
        sample_cohorts=cohorts if cohorts is not None else dict.fromkeys(samples, ""),
        injection_order=orders or {},
    )
    accumulator.add_observations(channel_anchor_observations(groups))
    return accumulator.freeze(measured_istds=measured_istds)
