from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.diagnostics.targeted_ms1_shape_identity import (
    ARTIFACT_SEMANTIC_ROLE,
    SCHEMA_VERSION,
    TargetedMs1ShapeCandidate,
    build_targeted_ms1_shape_identity_rows,
    canonical_targeted_ms1_shape_identity_bytes,
    mint_targeted_ms1_shape_identity_artifact_ref,
    write_targeted_ms1_shape_identity_tsv,
)
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactContractError,
    verify_evidence_artifact_payload,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    IdentityContractError,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)


def test_targeted_owner_mints_exact_bytes_and_attempt_scoped_artifact(
    tmp_path: Path,
) -> None:
    request, sample_snapshot, sample = _request_and_sample(tmp_path)
    first_attempt = issue_processing_attempt(request)
    second_attempt = issue_processing_attempt(request, retry_of=first_attempt)
    rows = _targeted_rows("Sample A")
    payload_bytes = canonical_targeted_ms1_shape_identity_bytes(rows)
    output_path = tmp_path / "targeted_ms1_shape_identity_v0.tsv"

    write_targeted_ms1_shape_identity_tsv(output_path, rows)
    first = mint_targeted_ms1_shape_identity_artifact_ref(
        processing_attempt=first_attempt,
        rows=rows,
    )
    retry = mint_targeted_ms1_shape_identity_artifact_ref(
        processing_attempt=second_attempt,
        rows=rows,
    )

    assert output_path.read_bytes() == payload_bytes
    assert first.semantic_contract.semantic_role == ARTIFACT_SEMANTIC_ROLE
    assert first.semantic_contract.schema_version == SCHEMA_VERSION
    assert first.semantic_contract.validation_status == "diagnostic_only"
    assert first.content_identity == retry.content_identity
    assert first.evidence_artifact_id != retry.evidence_artifact_id
    verify_evidence_artifact_payload(first, payload_bytes)
    with pytest.raises(EvidenceArtifactContractError, match="does not match"):
        verify_evidence_artifact_payload(first, payload_bytes + b"mutation")

    references = compose_processing_owner_references(
        request=request,
        attempt=first_attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=(sample,),
        evidence_artifacts=(first,),
    )
    assert references.evidence_artifacts == (first,)


def test_artifact_role_and_schema_substitution_fail_closed(tmp_path: Path) -> None:
    request, _, _ = _request_and_sample(tmp_path)
    artifact = mint_targeted_ms1_shape_identity_artifact_ref(
        processing_attempt=issue_processing_attempt(request),
        rows=_targeted_rows("Sample A"),
    )

    with pytest.raises(EvidenceArtifactContractError, match="role/schema"):
        replace(
            artifact.semantic_contract,
            semantic_role="caller_selected_role",
        )
    with pytest.raises(EvidenceArtifactContractError, match="role/schema"):
        replace(
            artifact.semantic_contract,
            schema_version="caller_selected_schema",
        )
    with pytest.raises(ValueError, match="owner-issued"):
        mint_targeted_ms1_shape_identity_artifact_ref(
            processing_attempt="attempt-id",
            rows=_targeted_rows("Sample A"),
        )


def test_lineage_closure_is_order_neutral_and_rejects_missing_or_other_attempt(
    tmp_path: Path,
) -> None:
    request, sample_snapshot, sample = _request_and_sample(tmp_path)
    attempt = issue_processing_attempt(request)
    retry = issue_processing_attempt(request, retry_of=attempt)
    upstream_a = mint_targeted_ms1_shape_identity_artifact_ref(
        processing_attempt=attempt,
        rows=_targeted_rows("Sample A"),
    )
    upstream_b = mint_targeted_ms1_shape_identity_artifact_ref(
        processing_attempt=attempt,
        rows=_targeted_rows("Sample B"),
    )
    child_forward = mint_targeted_ms1_shape_identity_artifact_ref(
        processing_attempt=attempt,
        rows=_targeted_rows("Sample C"),
        upstream_artifacts=(upstream_a, upstream_b),
    )
    child_reordered = mint_targeted_ms1_shape_identity_artifact_ref(
        processing_attempt=attempt,
        rows=_targeted_rows("Sample C"),
        upstream_artifacts=(upstream_b, upstream_a),
    )

    assert child_forward == child_reordered
    compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=(sample,),
        evidence_artifacts=(child_forward, upstream_b, upstream_a),
    )

    with pytest.raises(IdentityContractError, match="missing upstream"):
        compose_processing_owner_references(
            request=request,
            attempt=attempt,
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            evidence_artifacts=(child_forward, upstream_a),
        )

    retry_artifact = mint_targeted_ms1_shape_identity_artifact_ref(
        processing_attempt=retry,
        rows=_targeted_rows("Sample A"),
    )
    assert retry_artifact.content_identity == upstream_a.content_identity
    with pytest.raises(IdentityContractError, match="different processing attempt"):
        compose_processing_owner_references(
            request=request,
            attempt=attempt,
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            evidence_artifacts=(retry_artifact,),
        )


def _request_and_sample(tmp_path: Path):
    source_path = tmp_path / "source-a.bin"
    source_path.write_bytes(b"synthetic-acquisition-source")
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="synthetic_no_raw",
        observation_namespace="synthetic:source-a",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="source-a",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="artifact-tracer-input-v1",
        processing_algorithm_version="artifact-tracer-algorithm-v1",
        processing_contract_version="artifact-tracer-contract-v1",
        scientific_inputs={"fixture": "no-raw-artifact-tracer"},
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="Sample A", raw_stem="source-a"),),
        identity_namespace="study:artifact-tracer",
    )
    sample = resolve_canonical_sample_identity(sample_snapshot, "source-a")
    return request, sample_snapshot, sample


def _targeted_rows(sample_name: str):
    return build_targeted_ms1_shape_identity_rows(
        (
            TargetedMs1ShapeCandidate(
                sample_name=sample_name,
                target_name="5-mdC",
                candidate_rt_min=None,
                candidate_rt=(),
                candidate_intensity=(),
                reference_rt_min=None,
                reference_rt=(),
                reference_intensity=(),
            ),
        )
    )
