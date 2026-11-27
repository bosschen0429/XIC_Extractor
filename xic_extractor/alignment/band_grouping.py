"""Use injection-ordered bands for bootstrap, shift voting, and final rows."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal

from xic_extractor.alignment.cid_nl_grouping import (
    cid_nl_pair_failures,
    has_cid_nl_activation_support,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    BandInference,
    CrossSampleContractError,
    CrossSamplePeakGroupHypothesis,
    _hypothesis_from_owner_group,
)
from xic_extractor.alignment.membership_risk import (
    AcceptedMembershipRisk,
    OwnerGroup,
    measured_members,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_band_linking import (
    MAX_BAND_PRECURSOR_SPAN_PPM,
    MAX_BOOTSTRAP_RT_STEP_MIN,
    LinkedPeakBand,
    band_extension_distance,
    competing_band_members,
    link_peak_bands,
    link_with_band_drift,
)
from xic_extractor.alignment.rt_drift import ROW_MIN_SAMPLES
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import within_ppm


def band_rt(owner: SampleLocalMS1Owner, rt_frame: RtFrame) -> float:
    """Use the calibrated coordinate when supported, otherwise the measured RT.

    An under-equilibrated injection's early peak is first placed where it would
    elute without that injection's early-segment shift.
    """
    raw = owner.peak.rt_apex_min
    early = rt_frame.early_segment_rt_min(owner.sample_stem, raw)
    if early is not None:
        return early
    calibrated = rt_frame.calibrated_rt_min(owner.sample_stem, raw)
    if calibrated is not None:
        return calibrated
    return raw if rt_frame.drift_model is not None else rt_frame.corrected_rt_min(owner)


def group_peak_bands(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    stage: Literal["bootstrap", "final", "vote", "targeted"] = "final",
    known_identity_bindings: Sequence[tuple[str, str]] = (),
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Bind independently known identities and link the remaining measured peaks."""
    labels = dict((*rt_frame.known_identity_bindings, *known_identity_bindings))
    precursor_ppm = min(config.precursor_mz_tolerance_ppm, MAX_BAND_PRECURSOR_SPAN_PPM)
    known: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    ordinary = []
    for owner in owners:
        if owner.peak.integration is None:
            continue
        label = labels.get(owner.owner_id)
        if label is None:
            ordinary.append(owner)
        else:
            known[label].append(owner)
    points = tuple(
        (
            owner,
            owner.peak.rt_apex_min
            if stage == "bootstrap"
            else band_rt(owner, rt_frame),
        )
        for owner in ordinary
    )
    injections = rt_frame.injections
    drift = rt_frame.drift_model
    lookup = drift.batch_state_lookup() if drift is not None else None
    if stage == "bootstrap":
        bands = tuple(
            LinkedPeakBand(members)
            for members in link_peak_bands(
                points,
                injections=injections,
                precursor_ppm=precursor_ppm,
                max_rt_step_min=MAX_BOOTSTRAP_RT_STEP_MIN,
            )
        )
    else:
        bands = link_with_band_drift(
            points,
            injections=injections,
            drift=drift,
            has_direct_support=has_cid_nl_activation_support,
            precursor_ppm=precursor_ppm,
            batch_state_lookup=lookup,
            unite_seeds=stage == "final",
        )
    groups = []
    for label, members in sorted(known.items()):
        mzs = [owner.peak.mz for owner in members]
        if len({owner.sample_stem for owner in members}) != len(members):
            raise CrossSampleContractError(
                "known identity has conflicting sample observations"
            )
        if not within_ppm(max(mzs), min(mzs), precursor_ppm) or any(
            cid_nl_pair_failures(left, right, config)[0] is not None
            for left, right in zip(members, members[1:], strict=False)
        ):
            bands += tuple(LinkedPeakBand((owner,)) for owner in members)
        else:
            groups.append(_group(LinkedPeakBand(tuple(members)), label=label))

    competing_extensions: dict[str, tuple[float, OwnerGroup]] = {}
    established_bands = tuple(
        band
        for band in bands
        if stage == "final"
        and sum(has_cid_nl_activation_support(o) for o in band.members) >= 2
    )
    planes = {}
    for band in established_bands:
        plane = points
        if (
            band.drift_reference is not None
            and drift is not None
            and lookup is not None
        ):
            offsets = drift.sample_offsets(
                band.coefficients, lookup(*band.drift_reference)
            )
            plane = tuple(
                (owner, rt - offsets[owner.sample_stem])
                for owner, rt in points
                if owner.sample_stem in offsets
            )
        planes[id(band)] = plane
        member_ids = {member.owner_id for member in band.members}
        band_points = tuple(point for point in plane if point[0].owner_id in member_ids)
        member_samples = {owner.sample_stem for owner in band.members}
        for candidate, rt in plane:
            if candidate.sample_stem in member_samples:
                continue
            distance = band_extension_distance(
                band_points,
                (candidate, rt),
                injections=injections,
                precursor_ppm=precursor_ppm,
            )
            if distance is not None:
                key = (distance, measured_members((*band.members, candidate)))
                old = competing_extensions.get(candidate.owner_id)
                if old is None or key < old:
                    competing_extensions[candidate.owner_id] = key

    for band in bands:
        risks: tuple[tuple[str, AcceptedMembershipRisk], ...] = ()
        if (competition_points := planes.get(id(band))) is not None:
            risks = _competition_risks(
                band,
                competing_band_members(
                    band.members,
                    competition_points,
                    injections=injections,
                    has_direct_support=has_cid_nl_activation_support,
                    precursor_ppm=precursor_ppm,
                ),
                competing_extensions,
            )
        groups.append(_group(band, risks=risks))
    groups.extend(
        _hypothesis_from_owner_group(
            (owner,), disposition="provisional", evidence="unresolved_peak"
        )
        for owner in owners
        if owner.peak.integration is None
    )
    return tuple(groups)


def _group(
    band: LinkedPeakBand,
    *,
    label: str | None = None,
    risks: tuple[tuple[str, AcceptedMembershipRisk], ...] = (),
) -> CrossSamplePeakGroupHypothesis:
    members = tuple(sorted(band.members, key=lambda owner: owner.sample_stem))
    established = sum(has_cid_nl_activation_support(owner) for owner in members) >= 2
    return _hypothesis_from_owner_group(
        members,
        disposition="established" if established else "provisional",
        evidence="known_identity" if label is not None else "peak_band",
        inference_basis=BandInference(label, band.coefficients),
        drift_reference=band.drift_reference,
        member_risks=risks,
    )


def _competition_risks(
    band: LinkedPeakBand,
    competitors: Sequence[tuple[SampleLocalMS1Owner, SampleLocalMS1Owner]],
    extensions: Mapping[str, tuple[float, OwnerGroup]],
) -> tuple[tuple[str, AcceptedMembershipRisk], ...]:
    selected = measured_members(band.members)
    by_member: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for member, rival in competitors:
        by_member[member.owner_id].append(rival)
    result = []
    for member in band.members:
        if (extension := extensions.get(member.owner_id)) is not None:
            alternative = (extension[1],)
            evidence = "competing_band"
        elif member.owner_id in by_member:
            peers = tuple(owner for owner in band.members if owner is not member)
            alternative = (measured_members((*peers, by_member[member.owner_id][0])),)
            evidence = "same_sample_band_replacement"
        else:
            continue
        result.append(
            (
                member.owner_id,
                AcceptedMembershipRisk(
                    status="accepted",
                    phase="initial",
                    selected_owners=selected,
                    alternative_partition=alternative,
                    equivalent_alternatives=(),
                    rt_basis="band_drift"
                    if band.drift_reference
                    else "calibrated_band",
                    evidence_basis=evidence,
                    soft_contradictions=(),
                    risk_flags=("accepted_competition",),
                ),
            )
        )
    return tuple(result)


def band_shift_rows(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    rt_frame: RtFrame,
) -> tuple[dict[str, float], ...]:
    """Only a band of at least twenty direct peaks can vote on M.

    A peak placed by the early-segment shift is a prediction, not a measured
    coordinate, so it does not vote.
    """
    direct = tuple(
        owner
        for owner in owners
        if has_cid_nl_activation_support(owner)
        and rt_frame.early_segment_rt_min(owner.sample_stem, owner.peak.rt_apex_min)
        is None
    )
    if len(direct) < ROW_MIN_SAMPLES:
        return ()
    return tuple(
        {owner.sample_stem: band_rt(owner, rt_frame) for owner in group.owners}
        for group in group_peak_bands(
            direct, config=config, rt_frame=rt_frame, stage="vote"
        )
        if len(group.owners) >= ROW_MIN_SAMPLES
    )
