"""Groups built directly from member subjects for membership-only tests."""

from __future__ import annotations

from collections.abc import Sequence

from xic_extractor.alignment.cross_sample.identity_models import (
    CrossSampleGroupDisposition,
    CrossSampleGroupMemberSubject,
    canonical_group_members,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner


def group_from_members(
    *,
    member_subjects: Sequence[CrossSampleGroupMemberSubject],
    disposition: CrossSampleGroupDisposition | None = None,
    owners: tuple[SampleLocalMS1Owner, ...] = (),
    center: tuple[float, float] = (0.0, 0.0),
) -> CrossSamplePeakGroupHypothesis:
    """A group whose tests read only its membership; centre is a placeholder."""

    members = canonical_group_members(member_subjects)
    return CrossSamplePeakGroupHypothesis(
        member_subjects=members,
        disposition=disposition
        or ("established" if len(members) >= 2 else "provisional"),
        owners=owners,
        group_center_mz=center[0],
        group_center_rt=center[1],
    )
