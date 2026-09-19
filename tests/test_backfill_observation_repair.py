"""Public contract tests for the bounded Backfill ObservationRepair owner."""

from __future__ import annotations

from copy import copy
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path

import numpy as np
import pytest

from tests.test_alignment_owner_backfill import _feature, _peak_config
from tests.test_alignment_owner_clustering import _owner
from tests.test_owner_backfill_peak_fact_provenance import (
    _CanonicalOwnerBoundSource,
)
from xic_extractor.alignment.backfill import (
    ObservationRepairContractError,
    close_observation_repair,
    mint_observation_repair_subject,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleSuccessorApplication,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
    build_owner_backfill_result,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    PreparedOwnerBackfillPeakFact,
    prepare_owner_backfill_peak_fact,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)
from xic_extractor.processing_identity import (
    build_acquisition_source_identity,
    canonical_identity_basis_bytes,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)
from xic_extractor.xic_models import XICTrace


class _ZeroTraceOwnerBoundSource(_CanonicalOwnerBoundSource):
    def __init__(self, acquisition: object) -> None:
        super().__init__(acquisition, empty=False)

    def extract_xic(
        self,
        _mz: float,
        rt_min: float,
        rt_max: float,
        _ppm_tol: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        rt = np.linspace(rt_min, rt_max, 61)
        self.last_trace = XICTrace.from_arrays(rt, np.zeros_like(rt))
        return self.last_trace.rt, self.last_trace.intensity


@dataclass(frozen=True, slots=True)
class _RepairFixture:
    group: CrossSampleGroupIdentity
    application: CrossSampleSuccessorApplication
    member: CrossSampleGroupMemberSubject
    sample_identity: ResolvedCanonicalSampleIdentity
    observation: OwnerBackfillPeakFactObservation
    prepared: PreparedOwnerBackfillPeakFact
    result: OwnerBackfillSampleResult
    other_member: CrossSampleGroupMemberSubject | None = None
    other_sample_identity: ResolvedCanonicalSampleIdentity | None = None


def test_empty_same_stem_carrier_cannot_prove_no_repair(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path)
    subject = _subject(fixture)
    result = OwnerBackfillSampleResult(1, "sample-b", ())

    with pytest.raises(ObservationRepairContractError, match="exact live"):
        close_observation_repair(subject, result)


def test_empty_owner_trace_closes_deterministic_no_repair(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path, empty=True)
    subject = _subject(fixture)
    evaluation = fixture.observation.physical_evaluation
    assert evaluation is not None
    assert fixture.observation.trace_evaluated is True
    assert evaluation.state == "unavailable"
    assert evaluation.prepared.trace_point_count == 0
    assert evaluation.facts is None
    assert evaluation.integration is None

    first = close_observation_repair(subject, fixture.result)
    second = close_observation_repair(subject, fixture.result)

    assert first == second
    assert first.outcome == "no_repair"
    assert first.evidence is None


def test_nonempty_no_signal_owner_trace_closes_no_repair(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path, all_zero=True)
    evaluation = fixture.observation.physical_evaluation
    assert evaluation is not None
    assert fixture.observation.trace_evaluated is True
    assert evaluation.state == "unavailable"
    assert evaluation.reason == "no_signal_above_baseline"
    assert evaluation.prepared.trace_point_count == 61

    receipt = close_observation_repair(_subject(fixture), fixture.result)

    assert receipt.outcome == "no_repair"
    assert receipt.evidence is None


def test_live_repair_closes_provenance_and_prepared_only_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path)
    subject = _subject(fixture)
    live_result = fixture.result
    prepared_result = replace(
        fixture.result,
        peak_fact_observations=(),
        prepared_peak_facts=(fixture.prepared,),
    )

    live = close_observation_repair(subject, live_result)
    assert live.outcome == "repaired"
    assert live.evidence is not None
    evaluation = fixture.observation.physical_evaluation
    assert evaluation is not None
    assert evaluation.facts is not None
    assert live.evidence.request_identity_id == (
        fixture.observation.request_identity.request_identity_id
    )
    assert live.evidence.group_hypothesis_id == fixture.group.group_hypothesis_id
    assert live.evidence.sample_stem == "sample-b"
    assert live.evidence.fact_content_id == (
        evaluation.facts.fact_content_id
    )
    assert live.evidence.rt_apex_min == pytest.approx(
        evaluation.facts.apex_rt_min
    )
    assert live.subject is subject
    assert live.membership_authority == "none"
    assert live.value_authority == "none"
    assert live.matrix_authority == "none"
    assert live.activation_authority == "none"
    assert live.writer_authority == "none"
    with pytest.raises(FrozenInstanceError):
        live.outcome = "no_repair"  # type: ignore[misc]

    with pytest.raises(ObservationRepairContractError, match="prepared"):
        close_observation_repair(subject, prepared_result)


@pytest.mark.parametrize("epoch", [0, 2, True])
def test_subject_rejects_invalid_or_second_epoch(
    tmp_path: Path,
    epoch: object,
) -> None:
    fixture = _repair_fixture(tmp_path)

    with pytest.raises(ObservationRepairContractError, match="epoch"):
        mint_observation_repair_subject(
            request_identity=fixture.observation.request_identity,
            logical_request=fixture.observation.logical_request,
            predecessor_group=fixture.group,
            member_subject=fixture.member,
            sample_identity=fixture.sample_identity,
            successor_application=fixture.application,
            repair_epoch=epoch,  # type: ignore[arg-type]
        )


def test_subject_rejects_cross_group_or_cross_member_binding(tmp_path: Path) -> None:
    fixture = _repair_fixture(tmp_path)
    outsider = _owner("sample-c", "outsider").cross_sample_member_subject
    assert outsider is not None
    expanded = mint_cross_sample_group_identity(
        processing_request_id=fixture.group.processing_request_id,
        member_subjects=(fixture.member, outsider),
    )

    with pytest.raises(ObservationRepairContractError, match="group"):
        mint_observation_repair_subject(
            request_identity=fixture.observation.request_identity,
            logical_request=fixture.observation.logical_request,
            predecessor_group=expanded,
            member_subject=fixture.member,
            sample_identity=fixture.sample_identity,
            successor_application=_successor_application(expanded),
        )
    with pytest.raises(ObservationRepairContractError, match="member"):
        mint_observation_repair_subject(
            request_identity=fixture.observation.request_identity,
            logical_request=fixture.observation.logical_request,
            predecessor_group=fixture.group,
            member_subject=outsider,
            sample_identity=fixture.sample_identity,
            successor_application=fixture.application,
        )


def test_subject_rejects_wrong_member_in_same_multi_member_group(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path, multi_member=True)
    assert fixture.other_member is not None

    with pytest.raises(
        ObservationRepairContractError,
        match="canonical sample identity",
    ):
        mint_observation_repair_subject(
            request_identity=fixture.observation.request_identity,
            logical_request=fixture.observation.logical_request,
            predecessor_group=fixture.group,
            member_subject=fixture.other_member,
            sample_identity=fixture.sample_identity,
            successor_application=fixture.application,
        )


def test_subject_rejects_group_from_another_successor_application(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path / "subject")
    crossed = _repair_fixture(
        tmp_path / "crossed",
        member_seed="crossed-application",
    )

    with pytest.raises(ObservationRepairContractError, match="successor application"):
        mint_observation_repair_subject(
            request_identity=fixture.observation.request_identity,
            logical_request=fixture.observation.logical_request,
            predecessor_group=fixture.group,
            member_subject=fixture.member,
            sample_identity=fixture.sample_identity,
            successor_application=crossed.application,
        )


def test_close_rejects_cross_request_and_cross_sample_results(tmp_path: Path) -> None:
    fixture = _repair_fixture(tmp_path / "first", source_content=b"first")
    crossed = _repair_fixture(tmp_path / "second", source_content=b"second")
    subject = _subject(fixture)

    with pytest.raises(ObservationRepairContractError, match="request"):
        close_observation_repair(subject, crossed.result)
    with pytest.raises(ObservationRepairContractError, match="sample"):
        close_observation_repair(
            subject,
            replace(fixture.result, sample_stem="sample-c"),
        )


def test_receipt_identity_and_surface_ignore_carrier_ordinal(tmp_path: Path) -> None:
    fixture = _repair_fixture(tmp_path)
    subject = _subject(fixture)

    first = close_observation_repair(subject, fixture.result)
    reordered = close_observation_repair(
        subject,
        replace(fixture.result, sample_index=7),
    )

    assert first == reordered
    assert not hasattr(first, "sample_index")


def test_complete_unselected_subject_ignores_unrelated_logical_requests(
    tmp_path: Path,
) -> None:
    exact = _repair_fixture(
        tmp_path / "exact",
        source_content=b"shared-acquisition",
    )
    exact_unselected = copy(exact.observation)
    object.__setattr__(exact_unselected, "selected_for_output", False)
    unrelated = _repair_fixture(
        tmp_path / "unrelated",
        source_content=b"shared-acquisition",
        member_seed="unrelated",
    )
    result = OwnerBackfillSampleResult(
        sample_index=1,
        sample_stem="sample-b",
        cells=(*unrelated.result.cells, *exact.result.cells),
        peak_fact_observations=(
            unrelated.observation,
            exact_unselected,
        ),
    )

    receipt = close_observation_repair(_subject(exact), result)

    assert exact_unselected.trace_evaluated is True
    assert exact_unselected.selected_for_output is False
    assert receipt.outcome == "no_repair"
    assert receipt.evidence is None


def test_close_rejects_duplicate_partial_or_terminal_receipt_input(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path)
    subject = _subject(fixture)
    duplicate = replace(
        fixture.result,
        peak_fact_observations=(fixture.observation, fixture.observation),
    )
    malformed_selected = copy(fixture.observation)
    object.__setattr__(malformed_selected, "facts", None)
    partial = replace(
        fixture.result,
        peak_fact_observations=(malformed_selected,),
    )
    malformed_container = replace(
        fixture.result,
        peak_fact_observations=[fixture.observation],  # type: ignore[arg-type]
    )

    with pytest.raises(ObservationRepairContractError, match="duplicate"):
        close_observation_repair(subject, duplicate)
    with pytest.raises(ObservationRepairContractError, match="selected"):
        close_observation_repair(subject, partial)
    with pytest.raises(ObservationRepairContractError, match="immutable tuples"):
        close_observation_repair(subject, malformed_container)

    receipt = close_observation_repair(subject, fixture.result)
    with pytest.raises(TypeError, match="OwnerBackfillSampleResult"):
        close_observation_repair(subject, receipt)  # type: ignore[arg-type]


def test_close_rejects_duplicate_exact_unselected_observation(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path)
    subject = _subject(fixture)
    exact_unselected = copy(fixture.observation)
    object.__setattr__(exact_unselected, "selected_for_output", False)
    duplicate = replace(
        fixture.result,
        peak_fact_observations=(exact_unselected, exact_unselected),
    )
    contradictory_duplicate = replace(
        fixture.result,
        peak_fact_observations=(fixture.observation, exact_unselected),
    )

    with pytest.raises(ObservationRepairContractError, match="duplicate"):
        close_observation_repair(subject, duplicate)
    with pytest.raises(ObservationRepairContractError, match="duplicate"):
        close_observation_repair(subject, contradictory_duplicate)


def _subject(fixture: _RepairFixture):
    return mint_observation_repair_subject(
        request_identity=fixture.observation.request_identity,
        logical_request=fixture.observation.logical_request,
        predecessor_group=fixture.group,
        member_subject=fixture.member,
        sample_identity=fixture.sample_identity,
        successor_application=fixture.application,
    )


def _repair_fixture(
    tmp_path: Path,
    *,
    source_content: bytes = b"observation-repair-fixture",
    empty: bool = False,
    all_zero: bool = False,
    multi_member: bool = False,
    member_seed: str = "repair",
) -> _RepairFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    snapshot = mint_sample_metadata_snapshot(
        (
            SampleMetadata(sample_name="Sample B", raw_stem="sample-b"),
            SampleMetadata(sample_name="Sample C", raw_stem="sample-c"),
        ),
        identity_namespace="study:observation-repair-test",
    )
    sample_identity = resolve_canonical_sample_identity(snapshot, "sample-b")
    other_sample_identity = resolve_canonical_sample_identity(snapshot, "sample-c")
    member = _member_subject("sample-b", member_seed, sample_identity)
    other_member = _member_subject(
        "sample-c",
        "repair-other",
        other_sample_identity,
    )
    group = mint_cross_sample_group_identity(
        processing_request_id=member.processing_request_id,
        member_subjects=(member, other_member) if multi_member else (member,),
    )
    application = _successor_application(group)
    feature = replace(
        _feature(),
        group_hypothesis_id=group.group_hypothesis_id,
        processing_request_id=group.processing_request_id,
        member_subjects=group.member_subjects,
        identity_basis_bytes=group.basis_bytes,
        disposition=group.disposition,
    )
    source_path = tmp_path / "sample-b.raw"
    source_path.write_bytes(source_content)
    acquisition = build_acquisition_source_identity(
        source_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    source = (
        _ZeroTraceOwnerBoundSource(acquisition)
        if all_zero
        else _CanonicalOwnerBoundSource(acquisition, empty=empty)
    )
    built = build_owner_backfill_result(
        (feature,),
        sample_order=("sample-a", "sample-b"),
        raw_sources={"sample-b": source},
        expected_acquisition_sources={"sample-b": acquisition},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=3),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    assert len(built.peak_fact_observations) == 1
    observation = built.peak_fact_observations[0]
    prepared = prepare_owner_backfill_peak_fact(
        observation,
        feature_order_index=0,
        sample_order_index=1,
        profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    result = OwnerBackfillSampleResult(
        sample_index=1,
        sample_stem="sample-b",
        cells=built.cells,
        peak_fact_observations=(observation,),
    )
    return _RepairFixture(
        group,
        application,
        member,
        sample_identity,
        observation,
        prepared,
        result,
        other_member if multi_member else None,
        other_sample_identity if multi_member else None,
    )


def _successor_application(
    group: CrossSampleGroupIdentity,
) -> CrossSampleSuccessorApplication:
    return CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=(group,),
        successors=(group,),
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )


def _member_subject(
    sample_stem: str,
    seed: str,
    sample_identity: ResolvedCanonicalSampleIdentity,
) -> CrossSampleGroupMemberSubject:
    base = _owner(sample_stem, seed).cross_sample_member_subject
    assert base is not None
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject",
            "processing_request_id": base.processing_request_id,
            "canonical_sample_identity_id": (
                sample_identity.identity.sample_identity_id
            ),
            "subject": {"kind": base.subject_kind, "id": base.subject_id},
            "physical_observation_ids": list(base.physical_observation_ids),
        }
    )
    return CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(
            "xic.group-member-subject:",
            basis,
        ),
        processing_request_id=base.processing_request_id,
        canonical_sample_identity_id=sample_identity.identity.sample_identity_id,
        subject_kind=base.subject_kind,
        subject_id=base.subject_id,
        physical_observation_ids=base.physical_observation_ids,
        basis_bytes=basis,
    )
