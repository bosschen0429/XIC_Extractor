from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from xic_extractor.evidence_artifacts import (
    EvidenceArtifactContractError,
    mint_artifact_semantic_contract_ref,
    mint_evidence_artifact_ref,
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


def test_artifact_binds_exact_bytes_and_processing_attempt(
    tmp_path: Path,
) -> None:
    request, sample_snapshot, sample = _request_and_sample(tmp_path)
    first_attempt = issue_processing_attempt(request)
    second_attempt = issue_processing_attempt(request, retry_of=first_attempt)
    payload_bytes = b"Sample A"
    first = _artifact(
        processing_attempt=first_attempt,
        payload_bytes=payload_bytes,
    )
    retry = _artifact(
        processing_attempt=second_attempt,
        payload_bytes=payload_bytes,
    )

    assert first.semantic_contract.semantic_role == "synthetic_observation"
    assert first.semantic_contract.schema_version == "synthetic_observation"
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
    artifact = _artifact(
        processing_attempt=issue_processing_attempt(request),
        payload_bytes=_payload("Sample A"),
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
    for original, mutation, message in (
        (
            artifact,
            {"evidence_artifact_id": "xic.evidence-artifact:" + "0" * 64},
            "immutable artifact content",
        ),
        (
            artifact.content_identity,
            {"byte_size": artifact.content_identity.byte_size + 1},
            "exact payload content",
        ),
    ):
        with pytest.raises(EvidenceArtifactContractError, match=message):
            replace(original, **mutation)
        values = {item.name: getattr(original, item.name) for item in fields(original)}
        with pytest.raises(EvidenceArtifactContractError, match=message):
            type(original)(**(values | mutation))
        type(original)(**values).__post_init__()


def test_lineage_closure_is_order_neutral_and_rejects_missing_or_other_attempt(
    tmp_path: Path,
) -> None:
    request, sample_snapshot, sample = _request_and_sample(tmp_path)
    attempt = issue_processing_attempt(request)
    retry = issue_processing_attempt(request, retry_of=attempt)
    upstream_a = _artifact(
        processing_attempt=attempt,
        payload_bytes=_payload("Sample A"),
    )
    upstream_b = _artifact(
        processing_attempt=attempt,
        payload_bytes=_payload("Sample B"),
    )
    child_forward = _artifact(
        processing_attempt=attempt,
        payload_bytes=_payload("Sample C"),
        upstream_artifacts=(upstream_a, upstream_b),
    )
    child_reordered = _artifact(
        processing_attempt=attempt,
        payload_bytes=_payload("Sample C"),
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

    retry_artifact = _artifact(
        processing_attempt=retry,
        payload_bytes=_payload("Sample A"),
    )
    assert retry_artifact.content_identity == upstream_a.content_identity
    retry_child = _artifact(
        processing_attempt=retry,
        payload_bytes=_payload("Sample C"),
        upstream_artifacts=(upstream_a,),
    )
    retry_references = compose_processing_owner_references(
        request=request,
        attempt=retry,
        sample_snapshot=sample_snapshot,
        sample_resolutions=(sample,),
        evidence_artifacts=(retry_child, upstream_a),
    )
    assert retry_references.evidence_artifacts == tuple(
        sorted(
            (retry_child, upstream_a),
            key=lambda artifact: artifact.evidence_artifact_id,
        )
    )

    ancestor_with_future_dependency = _artifact(
        processing_attempt=attempt,
        payload_bytes=_payload("Sample D"),
        upstream_artifacts=(retry_artifact,),
    )
    with pytest.raises(IdentityContractError, match="later processing attempt"):
        compose_processing_owner_references(
            request=request,
            attempt=retry,
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            evidence_artifacts=(ancestor_with_future_dependency, retry_artifact),
        )

    with pytest.raises(IdentityContractError, match="different processing attempt"):
        compose_processing_owner_references(
            request=request,
            attempt=attempt,
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            evidence_artifacts=(retry_artifact,),
        )

    sibling_retry = issue_processing_attempt(request, retry_of=attempt)
    sibling_artifact = _artifact(
        processing_attempt=sibling_retry,
        payload_bytes=_payload("Sample A"),
    )
    with pytest.raises(IdentityContractError, match="different processing attempt"):
        compose_processing_owner_references(
            request=request,
            attempt=retry,
            sample_snapshot=sample_snapshot,
            sample_resolutions=(sample,),
            evidence_artifacts=(sibling_artifact,),
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
        scientific_input_schema_version="artifact-tracer-input",
        processing_algorithm_version="artifact-tracer-algorithm",
        processing_contract_version="artifact-tracer-contract",
        scientific_inputs={"fixture": "no-raw-artifact-tracer"},
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="Sample A", raw_stem="source-a"),),
        identity_namespace="study:artifact-tracer",
    )
    sample = resolve_canonical_sample_identity(sample_snapshot, "source-a")
    return request, sample_snapshot, sample


def _payload(sample):
    return sample.encode("utf-8")


def _artifact(*, processing_attempt, payload_bytes, upstream_artifacts=()):
    contract = mint_artifact_semantic_contract_ref(
        owner_namespace="tests.synthetic_observation",
        semantic_role="synthetic_observation",
        schema_version="synthetic_observation",
        validation_status="diagnostic_only",
        media_type="application/octet-stream",
        serialization_format="bytes",
    )
    return mint_evidence_artifact_ref(
        producer_processing_request_id=processing_attempt.processing_request_id,
        producer_processing_attempt_id=processing_attempt.processing_attempt_id,
        semantic_contract=contract,
        payload_bytes=payload_bytes,
        upstream_artifacts=upstream_artifacts,
    )
