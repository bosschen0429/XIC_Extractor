from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.issue175_no_raw_fixture import build_issue175_no_raw_fixture
from tests.test_alignment_xlsx_writer import sample_alignment_matrix
from tests.test_backfill_evidence_contract import (
    _backfill_bindings,
    _backfill_row,
    _write_rows,
)
from tests.test_untargeted_cid_nl_golden_path import (
    SAMPLE_ROLES,
    SOURCE_RELPATH,
    _golden_inputs,
)
from xic_extractor.alignment import artifact_write_receipts
from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    BackfillEvidenceOwnerBindings,
    EvidenceClaim,
    EvidenceClaimState,
    EvidenceQueryResult,
)
from xic_extractor.alignment.backfill_evidence_tsv import (
    lookup_backfill_evidence_tsv,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.owner_matrix import build_owner_alignment_matrix
from xic_extractor.alignment.production_decisions import build_production_decisions
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    build_untargeted_cid_nl_golden_path,
)
from xic_extractor.alignment.untargeted_explanation_index import (
    EXPLANATION_INFORMATION_REQUIREMENTS,
    AlignmentExplanationPayload,
    BackfillExplanationInput,
    BackfillExplanationPayload,
    DiscoveryExplanationPayload,
    ExplanationLocator,
    ExplanationLocatorKind,
    ExplanationLookupRequest,
    ExplanationTopic,
    MatrixExplanationPayload,
    UntargetedExplanationIndex,
    build_untargeted_explanation_index,
)
from xic_extractor.peak_detection.ms1_morphology import (
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    build_acquisition_source_identity,
    build_processing_request_identity,
    fingerprint_file_content,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)


def _attempt():
    source = build_acquisition_source_identity(
        Path(__file__),
        acquisition_format="no_raw_test_fixture",
        observation_namespace="test-explanation",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="explanation-fixture",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="test_explanation_inputs_v1",
        processing_algorithm_version="test-explanation-v1",
        processing_contract_version="test-explanation-contract-v1",
        scientific_inputs={"mode": "no_raw_contract_test"},
    )
    return issue_processing_attempt(request)


def _samples() -> tuple[ResolvedCanonicalSampleIdentity, ...]:
    snapshot = mint_sample_metadata_snapshot(
        (
            SampleMetadata(sample_name="sample-1", raw_stem="s1"),
            SampleMetadata(sample_name="sample-2", raw_stem="s2"),
            SampleMetadata(sample_name="sample-3", raw_stem="s3"),
        ),
        identity_namespace="test-explanation",
    )
    return tuple(
        resolve_canonical_sample_identity(snapshot, token)
        for token in ("s1", "s2", "s3")
    )


def _request(
    sample: ResolvedCanonicalSampleIdentity,
    *,
    locators: tuple[ExplanationLocator, ...] = (),
) -> ExplanationLookupRequest:
    return ExplanationLookupRequest(
        peak_hypothesis_id="FAM000001",
        sample=sample,
        locators=locators,
    )


def _build_index(**kwargs):
    matrix = kwargs.pop("matrix", sample_alignment_matrix())
    return build_untargeted_explanation_index(
        matrix=matrix,
        production_decisions=build_production_decisions(
            matrix,
            AlignmentConfig(),
        ),
        **kwargs,
    )


def _build_complete_no_raw_index(
    tmp_path: Path,
    *,
    include_backfill_query: bool = True,
) -> UntargetedExplanationIndex:
    fixture = build_issue175_no_raw_fixture(tmp_path, sample_roles=SAMPLE_ROLES)
    sample_inputs, recovery = _golden_inputs(fixture)
    projection = build_untargeted_cid_nl_golden_path(
        processing_owners=fixture.owners,
        method_subject=fixture.method_subject,
        sample_inputs=sample_inputs,
        recovery_inputs=(recovery,),
        source_artifact_relpath=SOURCE_RELPATH,
    )
    group = projection.spine.group
    assert group is not None
    samples = fixture.owners.sample_resolutions
    sample_order = tuple(sample.lookup_token for sample in samples)
    matrix = build_owner_alignment_matrix(
        (group,),
        sample_order=sample_order,
        ambiguous_by_sample={},
        rescued_cells=(),
    )
    matrix = replace(
        matrix,
        cells=tuple(
            replace(
                cell,
                selected_integration=replace(
                    cell.selected_integration,
                    area_ms1_morphology=(
                        cell.selected_integration.area_raw_counts_seconds
                    ),
                    ms1_morphology_area_source=MS1_MORPHOLOGY_AREA_SOURCE,
                    ms1_morphology_trace_method=MS1_MORPHOLOGY_TRACE_METHOD,
                    ms1_morphology_trace_window_points=15,
                    ms1_morphology_trace_effective_points=3,
                ),
            )
            if cell.status == "detected" and cell.selected_integration is not None
            else cell
            for cell in matrix.cells
        ),
    )
    sample = next(item for item in samples if item.lookup_token == "Missing")
    decision = next(
        item
        for item in projection.backfill_decisions
        if item.target_cell.content.canonical_sample_identity_id
        == sample.identity.sample_identity_id
    )
    assert decision.matched_candidate is not None
    source_candidate_id = (
        decision.matched_candidate.peak_observation.content
    ).peak_observation_content_id
    backfill_inputs: tuple[BackfillExplanationInput, ...] = ()
    if include_backfill_query:
        path = _write_rows(
            tmp_path / "backfill.tsv",
            ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
            [
                _backfill_row(
                    feature_family_id=group.group_hypothesis_id,
                    group_hypothesis_id=group.group_hypothesis_id,
                    public_family_id=group.public_family_id,
                    sample_stem=sample.lookup_token,
                    source_candidate_id=source_candidate_id,
                )
            ],
        )
        bindings = BackfillEvidenceOwnerBindings(
            known_group_ids=frozenset({group.group_hypothesis_id}),
            known_sample_stems=frozenset({sample.lookup_token}),
            candidate_samples={source_candidate_id: sample.lookup_token},
            group_feature_family_ids={
                group.group_hypothesis_id: group.group_hypothesis_id
            },
            group_public_family_ids={
                group.group_hypothesis_id: group.public_family_id
            },
        )
        query_result = lookup_backfill_evidence_tsv(
            path,
            feature_family_id=group.group_hypothesis_id,
            group_hypothesis_id=group.group_hypothesis_id,
            sample_stem=sample.lookup_token,
            owner_bindings=bindings,
        )
        backfill_inputs = (
            BackfillExplanationInput(
                route_key=(
                    group.group_hypothesis_id,
                    sample.identity.sample_identity_id,
                ),
                query_result=query_result,
            ),
        )
    return _build_index(
        processing_attempt=fixture.owners.attempt,
        matrix=matrix,
        sample_population=samples,
        requests=(
            ExplanationLookupRequest(
                peak_hypothesis_id=group.group_hypothesis_id,
                sample=sample,
            ),
        ),
        cid_nl_spines=(projection.spine,),
        backfill_decisions=projection.backfill_decisions,
        backfill_inputs=backfill_inputs,
    )


def test_builds_only_requested_routes_in_deterministic_owner_order() -> None:
    samples = _samples()
    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[2]), _request(samples[0])),
    )

    assert [entry.subject.sample_stem for entry in index.entries] == ["s1", "s3"]
    assert len(index.entries) == 2
    assert index.run_binding.attempt_claim.state is (
        EvidenceClaimState.OWNER_REHYDRATED
    )
    assert index.run_binding.matrix_artifact_claim.state is (
        EvidenceClaimState.UNAVAILABLE
    )
    assert index.run_binding.matrix_run_binding_claim.state is (
        EvidenceClaimState.UNAVAILABLE
    )
    assert index.run_binding.runtime_finalization_claim.state is (
        EvidenceClaimState.UNAVAILABLE
    )

    first = index.entries[0]
    assert tuple(section.topic for section in first.sections) == tuple(ExplanationTopic)
    matrix = first.section(ExplanationTopic.MATRIX)
    assert matrix.claim.state is EvidenceClaimState.OWNER_REHYDRATED
    assert matrix.payload is not None
    assert matrix.payload.written is True
    assert matrix.payload.matrix_value == "1.00000e+02"
    assert first.subject.matrix_row_binding_sha256
    assert first.subject.source_feature_family_id == "FAM000001"

    for topic in (
        ExplanationTopic.DISCOVERY,
        ExplanationTopic.ALIGNMENT,
        ExplanationTopic.BACKFILL,
    ):
        section = first.section(topic)
        assert section.claim.state is EvidenceClaimState.UNAVAILABLE
        assert section.payload is None
        assert (
            section.missing_requirements
            == (EXPLANATION_INFORMATION_REQUIREMENTS[topic])
        )


def test_no_raw_owner_route_closes_all_four_explanation_sections(
    tmp_path: Path,
) -> None:
    index = _build_complete_no_raw_index(tmp_path)
    entry = index.entries[0]

    assert all(
        section.claim.state is EvidenceClaimState.OWNER_REHYDRATED
        for section in entry.sections
    )
    discovery = entry.section(ExplanationTopic.DISCOVERY).payload
    alignment = entry.section(ExplanationTopic.ALIGNMENT).payload
    backfill = entry.section(ExplanationTopic.BACKFILL).payload
    matrix = entry.section(ExplanationTopic.MATRIX).payload
    assert isinstance(discovery, DiscoveryExplanationPayload)
    assert discovery.selection_outcome == "HOLD"
    assert discovery.selected_peak_hypothesis_id is None
    assert isinstance(alignment, AlignmentExplanationPayload)
    assert alignment.sample_member_peak_hypothesis_id is None
    assert alignment.group_hypothesis_id == entry.subject.peak_hypothesis_id
    assert isinstance(backfill, BackfillExplanationPayload)
    assert backfill.evaluated is True
    assert backfill.disposition == "unresolved"
    assert backfill.matched_candidate_peak_content_id is not None
    assert isinstance(matrix, MatrixExplanationPayload)
    assert matrix.written is False


def test_missing_backfill_sidecar_stays_unavailable_without_hiding_live_owners(
    tmp_path: Path,
) -> None:
    entry = _build_complete_no_raw_index(
        tmp_path,
        include_backfill_query=False,
    ).entries[0]

    assert entry.section(ExplanationTopic.DISCOVERY).claim.state is (
        EvidenceClaimState.OWNER_REHYDRATED
    )
    assert entry.section(ExplanationTopic.ALIGNMENT).claim.state is (
        EvidenceClaimState.OWNER_REHYDRATED
    )
    backfill = entry.section(ExplanationTopic.BACKFILL)
    assert backfill.claim.state is EvidenceClaimState.UNAVAILABLE
    assert backfill.payload is None
    assert backfill.claim.reason == "no owner-reader Backfill query result was supplied"
    assert entry.section(ExplanationTopic.MATRIX).claim.state is (
        EvidenceClaimState.OWNER_REHYDRATED
    )


def test_backfill_owner_query_preserves_claim_ceiling_and_quarantines_row_text(
    tmp_path: Path,
) -> None:
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [
            _backfill_row(
                sample_stem="s1",
                source_candidate_id="s1#candidate-1",
                reason="<script>alert('not HTML')</script>",
            )
        ],
    )
    bindings = replace(
        _backfill_bindings(),
        known_sample_stems=frozenset({"s1"}),
        candidate_samples={"s1#candidate-1": "s1"},
    )
    result = lookup_backfill_evidence_tsv(
        path,
        feature_family_id="FAM000001",
        group_hypothesis_id="FAM000001",
        sample_stem="s1",
        owner_bindings=bindings,
    )
    sample = _samples()[0]

    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=_samples(),
        requests=(_request(sample),),
        backfill_inputs=(
            BackfillExplanationInput(
                route_key=("FAM000001", sample.identity.sample_identity_id),
                query_result=result,
            ),
        ),
    )

    section = index.entries[0].section(ExplanationTopic.BACKFILL)
    assert section.claim.state is EvidenceClaimState.UNAVAILABLE
    assert section.payload is None
    assert "complete ProductionDecisionSet" in section.claim.reason
    assert section.supporting_claims == result.claims
    assert "<script>" not in section.claim.reason
    assert result.records[0].reason == "<script>alert('not HTML')</script>"


def test_backfill_cross_matrix_identity_is_rejected(tmp_path: Path) -> None:
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [
            _backfill_row(
                feature_family_id="FAM000002",
                group_hypothesis_id="FAM000002",
                public_family_id="FAM000002",
                sample_stem="s1",
                source_candidate_id="s1#candidate-1",
            )
        ],
    )
    bindings = replace(
        _backfill_bindings(),
        known_sample_stems=frozenset({"s1"}),
        candidate_samples={"s1#candidate-1": "s1"},
    )
    result = lookup_backfill_evidence_tsv(
        path,
        feature_family_id="FAM000002",
        group_hypothesis_id="FAM000002",
        sample_stem="s1",
        owner_bindings=bindings,
    )
    samples = _samples()

    with pytest.raises(ValueError, match="crosses the Matrix source feature"):
        _build_index(
            processing_attempt=_attempt(),
            matrix=sample_alignment_matrix(),
            sample_population=samples,
            requests=(_request(samples[0]),),
            backfill_inputs=(
                BackfillExplanationInput(
                    route_key=(
                        "FAM000001",
                        samples[0].identity.sample_identity_id,
                    ),
                    query_result=result,
                ),
            ),
        )


def test_caller_cannot_promote_backfill_production_claim(tmp_path: Path) -> None:
    path = _write_rows(
        tmp_path / "backfill.tsv",
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        [
            _backfill_row(
                sample_stem="s1",
                source_candidate_id="s1#candidate-1",
            )
        ],
    )
    bindings = replace(
        _backfill_bindings(),
        known_sample_stems=frozenset({"s1"}),
        candidate_samples={"s1#candidate-1": "s1"},
    )
    result = lookup_backfill_evidence_tsv(
        path,
        feature_family_id="FAM000001",
        group_hypothesis_id="FAM000001",
        sample_stem="s1",
        owner_bindings=bindings,
    )
    forged_claims = tuple(
        EvidenceClaim(
            subject=claim.subject,
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if claim.subject == "production_decision"
                else claim.state
            ),
            reason=claim.reason,
        )
        for claim in result.claims
    )
    forged = EvidenceQueryResult(records=result.records, claims=forged_claims)
    samples = _samples()

    with pytest.raises(ValueError, match="cannot promote production_decision"):
        _build_index(
            processing_attempt=_attempt(),
            matrix=sample_alignment_matrix(),
            sample_population=samples,
            requests=(_request(samples[0]),),
            backfill_inputs=(
                BackfillExplanationInput(
                    route_key=(
                        "FAM000001",
                        samples[0].identity.sample_identity_id,
                    ),
                    query_result=forged,
                ),
            ),
        )


@pytest.mark.parametrize(
    "path",
    (
        "../escape.png",
        "/absolute/overlay.png",
        "C:/absolute/overlay.png",
        "folder\\overlay.png",
        "folder/name#fragment.png",
        "folder/%2e%2e/overlay.png",
        "javascript:alert(1)",
        "https://example.invalid/overlay.png",
    ),
)
def test_locator_rejects_nonportable_or_unsafe_navigation(path: str) -> None:
    with pytest.raises(ValueError, match="locator"):
        ExplanationLocator(
            kind=ExplanationLocatorKind.OVERLAY,
            relative_path=path,
        )


def test_locator_is_navigation_only_and_relative() -> None:
    locator = ExplanationLocator(
        kind=ExplanationLocatorKind.DETAIL,
        relative_path="details/FAM000001/s1.json",
    )
    samples = _samples()

    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0], locators=(locator,)),),
    )

    assert index.entries[0].section(ExplanationTopic.MATRIX).locators == (locator,)


def test_writer_receipts_bind_matrix_bytes_but_not_runtime_finalization(
    tmp_path: Path,
) -> None:
    matrix_path = tmp_path / "alignment_matrix.tsv"
    identity_path = tmp_path / "alignment_matrix_identity.tsv"
    matrix_path.write_text("Mz\tRT\ts1\n", encoding="utf-8")
    identity_path.write_text("identity_schema_version\n", encoding="utf-8")
    receipts = tuple(
        artifact_write_receipts._mint_alignment_artifact_fingerprint_receipt(
            artifact_path=path,
            fingerprint=fingerprint_file_content(path),
        )
        for path in (matrix_path, identity_path)
    )
    batch = artifact_write_receipts._mint_alignment_artifact_fingerprint_batch(
        artifact_paths=(matrix_path, identity_path),
        receipts=receipts,
    )
    samples = _samples()

    index = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0]),),
        artifact_fingerprints=batch,
    )

    assert index.run_binding.matrix_artifact_claim.state is (
        EvidenceClaimState.BYTE_BOUND
    )
    assert index.run_binding.matrix_fingerprint == receipts[0].fingerprint
    assert index.run_binding.matrix_identity_fingerprint == receipts[1].fingerprint
    assert index.run_binding.matrix_run_binding_claim.state is (
        EvidenceClaimState.UNAVAILABLE
    )
    assert index.run_binding.runtime_finalization_claim.state is (
        EvidenceClaimState.UNAVAILABLE
    )

    partial_batch = artifact_write_receipts._mint_alignment_artifact_fingerprint_batch(
        artifact_paths=(matrix_path,),
        receipts=(receipts[0],),
    )
    partial = _build_index(
        processing_attempt=_attempt(),
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0]),),
        artifact_fingerprints=partial_batch,
    )
    assert partial.run_binding.matrix_artifact_claim.state is (
        EvidenceClaimState.UNAVAILABLE
    )
    assert partial.run_binding.matrix_fingerprint is None
    assert partial.run_binding.matrix_identity_fingerprint is None


def test_duplicate_route_and_partial_sample_owner_inputs_fail_closed() -> None:
    samples = _samples()
    duplicate = _request(samples[0])

    with pytest.raises(ValueError, match="requests must be unique"):
        _build_index(
            processing_attempt=_attempt(),
            matrix=sample_alignment_matrix(),
            sample_population=samples,
            requests=(duplicate, duplicate),
        )

    with pytest.raises(ValueError, match="exactly follow"):
        _build_index(
            processing_attempt=_attempt(),
            matrix=sample_alignment_matrix(),
            sample_population=samples[:2],
            requests=(_request(samples[0]),),
        )
