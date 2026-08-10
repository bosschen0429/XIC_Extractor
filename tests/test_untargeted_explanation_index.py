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
from xic_extractor.alignment import untargeted_explanation_contract as contract_module
from xic_extractor.alignment import untargeted_explanation_index as index_module
from xic_extractor.alignment import (
    untargeted_explanation_navigation as navigation_module,
)
from xic_extractor.alignment import (
    untargeted_explanation_run_binding as run_binding_module,
)
from xic_extractor.alignment import untargeted_explanation_sections as sections_module
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
from xic_extractor.alignment.matrix import AlignmentMatrix
from xic_extractor.alignment.owner_matrix import build_owner_alignment_matrix
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    build_untargeted_cid_nl_golden_path,
)
from xic_extractor.alignment.untargeted_explanation_contract import (
    _ENTRY_MINT_TOKEN,
    _LOCATOR_MINT_TOKEN,
    _SECTION_MINT_TOKEN,
    EXPLANATION_INFORMATION_REQUIREMENTS,
    AlignmentExplanationPayload,
    BackfillExplanationPayload,
    DiscoveryExplanationPayload,
    ExplanationLocator,
    ExplanationLocatorKind,
    ExplanationLocatorRequest,
    ExplanationLookupRequest,
    ExplanationTopic,
    MatrixExplanationPayload,
    UntargetedExplanationIndex,
)
from xic_extractor.alignment.untargeted_explanation_index import (
    BackfillExplanationInput,
    _build_untargeted_explanation_index,
)
from xic_extractor.alignment.untargeted_explanation_navigation import (
    explanation_locator_relative_path,
    mint_explanation_locator_request,
)
from xic_extractor.output import untargeted_evidence_report as report_module
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
    locators: tuple[ExplanationLocatorRequest, ...] = (),
) -> ExplanationLookupRequest:
    return ExplanationLookupRequest(
        peak_hypothesis_id="FAM000001",
        sample=sample,
        locators=locators,
    )


def _locator_request(
    root: Path,
    *,
    attempt,
    sample: ResolvedCanonicalSampleIdentity,
    topic: ExplanationTopic = ExplanationTopic.ALIGNMENT,
    kind: ExplanationLocatorKind = ExplanationLocatorKind.DETAIL,
    artifact_name: str = "evidence.json",
) -> ExplanationLocatorRequest:
    relative_path = explanation_locator_relative_path(
        processing_attempt=attempt,
        peak_hypothesis_id="FAM000001",
        sample=sample,
        topic=topic,
        artifact_name=artifact_name,
    )
    artifact_path = root.joinpath(*relative_path.split("/"))
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(b"navigation-only fixture")
    return mint_explanation_locator_request(
        navigation_root=root,
        artifact_path=artifact_path,
        processing_attempt=attempt,
        peak_hypothesis_id="FAM000001",
        sample=sample,
        topic=topic,
        kind=kind,
    )


def _build_index(**kwargs):
    matrix = kwargs.pop("matrix", sample_alignment_matrix())
    return _build_untargeted_explanation_index(
        matrix=matrix,
        alignment_config=AlignmentConfig(),
        **kwargs,
    )


def _complete_no_raw_kwargs(
    tmp_path: Path,
    *,
    include_backfill_query: bool = True,
    sample_stem: str = "Missing",
) -> dict[str, object]:
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
    sample = next(item for item in samples if item.lookup_token == sample_stem)
    decision = next(
        (
            item
            for item in projection.backfill_decisions
            if item.target_cell.content.canonical_sample_identity_id
            == sample.identity.sample_identity_id
        ),
        None,
    )
    if decision is not None and decision.matched_candidate is not None:
        source_candidate_id = (
            decision.matched_candidate.peak_observation.content
        ).peak_observation_content_id
    else:
        source_candidate_id = next(
            cell.source_candidate_id
            for cell in matrix.cells
            if cell.cluster_id == group.feature_family_id
            and cell.sample_stem == sample.lookup_token
        )
        assert source_candidate_id is not None
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
            group_public_family_ids={group.group_hypothesis_id: group.public_family_id},
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
    return {
        "processing_attempt": fixture.owners.attempt,
        "matrix": matrix,
        "sample_population": samples,
        "requests": (
            ExplanationLookupRequest(
                peak_hypothesis_id=group.group_hypothesis_id,
                sample=sample,
            ),
        ),
        "cid_nl_spines": (projection.spine,),
        "backfill_group_facts": (projection.backfill_facts,),
        "backfill_decisions": projection.backfill_decisions,
        "backfill_inputs": backfill_inputs,
    }


def _build_complete_no_raw_index(
    tmp_path: Path,
    *,
    include_backfill_query: bool = True,
    sample_stem: str = "Missing",
) -> UntargetedExplanationIndex:
    return _build_index(
        **_complete_no_raw_kwargs(
            tmp_path,
            include_backfill_query=include_backfill_query,
            sample_stem=sample_stem,
        )
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
    assert matrix.payload.matrix_row_mz == "242.1140"
    assert matrix.payload.matrix_row_rt == "12.593"
    assert matrix.payload.sample_stem == "s1"
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


def test_written_backfill_requires_exact_live_matrix_source_candidate(
    tmp_path: Path,
) -> None:
    kwargs = _complete_no_raw_kwargs(tmp_path)
    matrix = kwargs["matrix"]
    assert isinstance(matrix, AlignmentMatrix)
    owner_decision = next(
        item
        for item in kwargs["backfill_decisions"]
        if item.matched_candidate is not None
    )
    request = kwargs["requests"][0]
    key = (
        owner_decision.group_facts.group_context.group.feature_family_id,
        request.sample.lookup_token,
    )
    different_candidate_cell = next(
        cell for cell in matrix.cells if cell.status == "detected"
    )
    matrix = replace(
        matrix,
        cells=tuple(
            replace(
                different_candidate_cell,
                cluster_id=key[0],
                sample_stem=key[1],
            )
            if (cell.cluster_id, cell.sample_stem) == key
            else cell
            for cell in matrix.cells
        ),
    )
    kwargs["matrix"] = matrix

    section = _build_untargeted_explanation_index(
        alignment_config=AlignmentConfig(),
        **kwargs,
    ).entries[0].section(ExplanationTopic.BACKFILL)

    assert section.claim.state is EvidenceClaimState.UNAVAILABLE
    assert section.payload is None
    assert "does not bind the Backfill matched candidate" in section.claim.reason


def test_caller_cannot_supply_a_detached_production_decision_snapshot(
    tmp_path: Path,
) -> None:
    kwargs = _complete_no_raw_kwargs(tmp_path)

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        _build_untargeted_explanation_index(
            alignment_config=AlignmentConfig(),
            production_decisions=object(),
            **kwargs,
        )


def test_no_raw_selected_direct_support_route_closes_all_four_sections(
    tmp_path: Path,
) -> None:
    entry = _build_complete_no_raw_index(
        tmp_path,
        sample_stem="StudySupport",
    ).entries[0]

    assert all(
        section.claim.state is EvidenceClaimState.OWNER_REHYDRATED
        for section in entry.sections
    )
    discovery = entry.section(ExplanationTopic.DISCOVERY).payload
    alignment = entry.section(ExplanationTopic.ALIGNMENT).payload
    backfill = entry.section(ExplanationTopic.BACKFILL).payload
    matrix = entry.section(ExplanationTopic.MATRIX).payload
    assert isinstance(discovery, DiscoveryExplanationPayload)
    assert discovery.selection_outcome == "SUPPORT"
    assert discovery.selected_peak_hypothesis_id is not None
    assert isinstance(alignment, AlignmentExplanationPayload)
    assert alignment.sample_member_peak_hypothesis_id == (
        discovery.selected_peak_hypothesis_id
    )
    assert isinstance(backfill, BackfillExplanationPayload)
    assert backfill.evaluated is False
    assert backfill.disposition == "not_applicable"
    assert backfill.matched_candidate_peak_content_id is None
    assert "direct_support_no_recovery_required" in backfill.decision_reasons
    assert isinstance(matrix, MatrixExplanationPayload)
    assert matrix.written is True
    assert matrix.matrix_value is not None


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
    assert "owner group facts" in section.claim.reason
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


def test_caller_cannot_forge_positive_backfill_reader_result(tmp_path: Path) -> None:
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
    with pytest.raises(ValueError, match="minted by an owner reader"):
        EvidenceQueryResult(
            records=result.records,
            claims=forged_claims,
            _mint_token=object(),
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
    sample = _samples()[0]
    with pytest.raises(ValueError, match="locator"):
        explanation_locator_relative_path(
            processing_attempt=_attempt(),
            peak_hypothesis_id="FAM000001",
            sample=sample,
            topic=ExplanationTopic.MATRIX,
            artifact_name=path,
        )


def test_caller_cannot_forge_owner_bound_locator(tmp_path: Path) -> None:
    sample = _samples()[0]
    attempt = _attempt()
    receipt = _locator_request(tmp_path, attempt=attempt, sample=sample)
    with pytest.raises(ValueError, match="bound by the index owner"):
        ExplanationLocator(
            kind=ExplanationLocatorKind.DETAIL,
            topic=ExplanationTopic.MATRIX,
            relative_path=receipt.relative_path,
            processing_attempt_id="forged-attempt",
            peak_hypothesis_id="FAM000001",
            canonical_sample_identity_id="forged-sample",
            artifact_fingerprint=receipt.artifact_fingerprint,
            _mint_token=object(),
        )


def test_locator_is_navigation_only_and_exactly_route_attempt_topic_bound(
    tmp_path: Path,
) -> None:
    samples = _samples()
    attempt = _attempt()
    request_locator = _locator_request(
        tmp_path,
        attempt=attempt,
        sample=samples[0],
    )

    index = _build_index(
        processing_attempt=attempt,
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0], locators=(request_locator,)),),
    )

    entry = index.entries[0]
    assert entry.section(ExplanationTopic.MATRIX).locators == ()
    (locator,) = entry.section(ExplanationTopic.ALIGNMENT).locators
    assert locator.relative_path == request_locator.relative_path
    assert locator.topic is ExplanationTopic.ALIGNMENT
    assert locator.processing_attempt_id == attempt.processing_attempt_id
    assert locator.peak_hypothesis_id == entry.subject.peak_hypothesis_id
    assert locator.canonical_sample_identity_id == (
        samples[0].identity.sample_identity_id
    )


def test_safe_but_cross_route_navigation_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    samples = _samples()
    attempt = _attempt()
    other_route_receipt = _locator_request(
        tmp_path,
        attempt=attempt,
        sample=samples[1],
    )

    with pytest.raises(ValueError, match="crosses the requested route or attempt"):
        _build_index(
            processing_attempt=attempt,
            matrix=sample_alignment_matrix(),
            sample_population=samples,
            requests=(_request(samples[0], locators=(other_route_receipt,)),),
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "processing_attempt_id",
        "peak_hypothesis_id",
        "canonical_sample_identity_id",
    ),
)
def test_stale_or_cross_route_bound_locator_is_rejected(
    field_name: str,
    tmp_path: Path,
) -> None:
    samples = _samples()
    attempt = _attempt()
    request_locator = _locator_request(
        tmp_path,
        attempt=attempt,
        sample=samples[0],
    )
    entry = _build_index(
        processing_attempt=attempt,
        matrix=sample_alignment_matrix(),
        sample_population=samples,
        requests=(_request(samples[0], locators=(request_locator,)),),
    ).entries[0]
    section = entry.section(ExplanationTopic.ALIGNMENT)
    forged = replace(
        section.locators[0],
        **{field_name: "forged-owner-binding"},
        _mint_token=_LOCATOR_MINT_TOKEN,
    )
    forged_section = replace(
        section,
        locators=(forged,),
        _mint_token=_SECTION_MINT_TOKEN,
    )
    sections = tuple(
        forged_section if item.topic is ExplanationTopic.ALIGNMENT else item
        for item in entry.sections
    )

    with pytest.raises(ValueError, match="exact entry route and attempt"):
        replace(entry, sections=sections, _mint_token=_ENTRY_MINT_TOKEN)


def test_duplicate_spines_and_backfill_decisions_fail_closed(
    tmp_path: Path,
) -> None:
    kwargs = _complete_no_raw_kwargs(tmp_path)
    (spine,) = kwargs["cid_nl_spines"]
    kwargs["cid_nl_spines"] = (spine, spine)
    with pytest.raises(ValueError, match="spines must use unique groups"):
        _build_index(**kwargs)

    kwargs = _complete_no_raw_kwargs(tmp_path)
    decisions = kwargs["backfill_decisions"]
    kwargs["backfill_decisions"] = (*decisions, decisions[0])
    with pytest.raises(ValueError, match="decisions must use unique routes"):
        _build_index(**kwargs)


def test_stale_attempt_and_equal_but_substituted_matrix_group_fail_closed(
    tmp_path: Path,
) -> None:
    kwargs = _complete_no_raw_kwargs(tmp_path)
    kwargs["processing_attempt"] = _attempt()
    with pytest.raises(ValueError, match="different attempt"):
        _build_index(**kwargs)

    kwargs = _complete_no_raw_kwargs(tmp_path)
    matrix = kwargs["matrix"]
    (group,) = matrix.clusters
    kwargs["matrix"] = replace(matrix, clusters=(replace(group),))
    with pytest.raises(ValueError, match="exact live Matrix group"):
        _build_index(**kwargs)


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


@pytest.mark.parametrize(
    "module",
    (
        contract_module,
        index_module,
        navigation_module,
        run_binding_module,
        sections_module,
        report_module,
    ),
)
def test_explanation_prototype_modules_publish_no_public_exports(
    module: object,
) -> None:
    assert getattr(module, "__all__") == ()


def test_provisional_route_guard_fails_before_projection() -> None:
    samples = _samples()
    limit = index_module._PROTOTYPE_ROUTE_LIMIT
    requests = tuple(
        replace(
            _request(samples[0]),
            peak_hypothesis_id=f"bounded-route-{index:04d}",
        )
        for index in range(limit + 1)
    )

    with pytest.raises(ValueError, match=rf"at most {limit} requested routes"):
        _build_index(
            processing_attempt=_attempt(),
            matrix=sample_alignment_matrix(),
            sample_population=samples,
            requests=requests,
        )
