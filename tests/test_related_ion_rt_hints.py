from __future__ import annotations

from dataclasses import replace

import pytest

from tests.test_feature_membership_inference import _owner
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.related_ion_rt_hints import RelatedIonRtHintAccumulator


def _group(name, mz, samples, *, offset=0.0):
    owners = tuple(
        _owner(sample, name, precursor_mz=mz, apex_rt=rt + offset)
        for sample, rt in samples.items()
    )
    return CrossSamplePeakGroupHypothesis(
        group_hypothesis_id=name,
        public_family_id=name,
        owner_ids=tuple(o.owner_id for o in owners),
        event_ids=(),
        event_member_count=len(owners),
        group_center_mz=mz,
        group_center_rt=10.0,
        owners=owners,
    )


def _evidence():
    training = {f"S{i:02}": 10.0 + i * 0.01 for i in range(20)}
    target = _group("target", 349.2157, training, offset=0.01)
    partner = _group("partner", 349.2157 - 1.00335483507, training | {"missing": 13.0})
    cohorts = dict.fromkeys((*training, "missing"), "batch-a")
    return target, partner, cohorts


def _fit(groups, cohorts):
    model = RelatedIonRtHintAccumulator(sample_cohorts=cohorts, mass_tolerance_ppm=5.0)
    for group in groups:
        model.add_groups((group,))
    return model.freeze()


def test_related_ion_supplies_only_missing_sample_search_coordinate():
    target, partner, cohorts = _evidence()
    before = target, partner
    hints = _fit((target, partner), cohorts)
    assert hints == _fit((partner, target), cohorts)
    assert len(hints) == 1
    hint = hints["target", "missing"]
    assert hint.query_rt == pytest.approx(13.01)
    assert hint.basis == "related_ion_coelution:partner"
    assert (target, partner) == before
    assert target.group_center_rt == 10.0
    assert target.group_center_mz == 349.2157
    assert "missing" not in {o.sample_stem for o in target.owners}


@pytest.mark.parametrize(
    "failure",
    [
        "absent_partner",
        "too_few_pairs",
        "different_cohort",
        "wrong_mass",
        "poor_coelution",
        "unstable_ratio",
        "no_query_signal",
        "rejected_group",
    ],
)
def test_unsupported_related_ions_produce_no_hint(failure):
    target, partner, cohorts = _evidence()
    if failure == "absent_partner":
        partner = replace(partner, owners=partner.owners[:-1])
    elif failure == "too_few_pairs":
        target = replace(target, owners=target.owners[:-1])
    elif failure == "different_cohort":
        cohorts["missing"] = "batch-b"
    elif failure == "wrong_mass":
        partner = replace(partner, group_center_mz=345.0)
    elif failure == "poor_coelution":
        partner = replace(
            partner,
            owners=tuple(
                replace(o, owner_apex_rt=o.owner_apex_rt + 0.2) for o in partner.owners
            ),
        )
    elif failure == "unstable_ratio":
        partner = replace(
            partner,
            owners=tuple(
                replace(o, owner_area=100.0 if i % 2 else 1000.0)
                for i, o in enumerate(partner.owners)
            ),
        )
    elif failure == "no_query_signal":
        partner = replace(
            partner,
            owners=tuple(
                replace(o, owner_area=0.0) if o.sample_stem == "missing" else o
                for o in partner.owners
            ),
        )
    else:
        partner = replace(partner, disposition="rejected")
    assert ("target", "missing") not in _fit((target, partner), cohorts)


def test_disagreeing_partner_observations_abstain():
    target, partner, cohorts = _evidence()
    other = _group(
        "other",
        target.family_center_mz + 1.00335483507,
        {o.sample_stem: o.owner_apex_rt for o in partner.owners},
    )
    other = replace(
        other,
        owners=tuple(
            replace(o, owner_apex_rt=14.0) if o.sample_stem == "missing" else o
            for o in other.owners
        ),
    )
    assert ("target", "missing") not in _fit((target, partner, other), cohorts)
