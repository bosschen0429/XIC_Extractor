from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.artifact_lineage import EvidenceArtifactClosureError
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import (
    mint_target_list_snapshot,
    resolve_target_definition,
)
from xic_extractor.delivery_lineage import (
    DeliveryLineageContractError,
    mint_targeted_delivery_cell_key,
    mint_targeted_delivery_revision_candidate,
    mint_targeted_delivery_seal_candidate,
    stage_targeted_delivery_candidate,
)
from xic_extractor.evidence_artifacts import (
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    build_acquisition_source_identity,
    build_processing_request_identity,
    canonical_evidence_artifact_closure,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)


def test_targeted_delivery_cell_revision_stage_and_seal_candidate_are_bound(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    artifact = _artifact(attempt, b"selected-targeted-result")
    cell = mint_targeted_delivery_cell_key(_sample(), _target())
    revision = mint_targeted_delivery_revision_candidate(
        cell=cell,
        producer_attempt=attempt,
        projection_contract_version="targeted_projection",
        evidence_artifacts=(artifact,),
        selected_payload=b"9.1000,1000.0,5000.0",
    )
    stage = stage_targeted_delivery_candidate(
        producer_attempt=attempt,
        revisions=(revision,),
        output_artifacts=(artifact,),
    )
    seal = mint_targeted_delivery_seal_candidate(stage)

    assert cell.canonical_sample_identity_id == _sample().identity.sample_identity_id
    assert revision.producer_attempt == attempt
    assert stage.lineage_manifest_sha256
    assert seal.stage == stage
    assert "candidate" in seal.delivery_seal_candidate_id


def test_targeted_delivery_revision_rejects_failure_as_scientific_null(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    artifact = _artifact(attempt, b"failed-attempt-diagnostic")

    with pytest.raises(DeliveryLineageContractError, match="exactly one"):
        mint_targeted_delivery_revision_candidate(
            cell=mint_targeted_delivery_cell_key(_sample(), _target()),
            producer_attempt=attempt,
            projection_contract_version="targeted_projection",
            evidence_artifacts=(artifact,),
        )


def test_delivery_revision_changes_when_selected_payload_changes(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    artifact = _artifact(attempt, b"selected-targeted-result")
    kwargs = {
        "cell": mint_targeted_delivery_cell_key(_sample(), _target()),
        "producer_attempt": attempt,
        "projection_contract_version": "targeted_projection",
        "evidence_artifacts": (artifact,),
    }

    first = mint_targeted_delivery_revision_candidate(
        **kwargs, selected_payload=b"first"
    )
    second = mint_targeted_delivery_revision_candidate(
        **kwargs, selected_payload=b"second"
    )

    assert first.delivery_revision_id != second.delivery_revision_id


def test_stage_rejects_duplicate_delivery_cell(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    artifact = _artifact(attempt, b"selected-targeted-result")
    revision = mint_targeted_delivery_revision_candidate(
        cell=mint_targeted_delivery_cell_key(_sample(), _target()),
        producer_attempt=attempt,
        projection_contract_version="targeted_projection",
        evidence_artifacts=(artifact,),
        selected_payload=b"first",
    )

    with pytest.raises(DeliveryLineageContractError, match="one revision"):
        stage_targeted_delivery_candidate(
            producer_attempt=attempt,
            revisions=(revision, replace(revision)),
            output_artifacts=(artifact,),
        )


def test_delivery_revision_derives_complete_retry_predecessor_chain(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    first = issue_processing_attempt(request)
    second = issue_processing_attempt(request, retry_of=first)
    third = issue_processing_attempt(request, retry_of=second)
    first_artifact = _artifact(first, b"first-attempt-evidence")

    revision = mint_targeted_delivery_revision_candidate(
        cell=mint_targeted_delivery_cell_key(_sample(), _target()),
        producer_attempt=third,
        projection_contract_version="targeted_projection",
        evidence_artifacts=(first_artifact,),
        selected_payload=b"selected-from-retry-chain",
    )

    assert revision.dependency_processing_attempt_ids == (
        second.processing_attempt_id,
        first.processing_attempt_id,
    )


def test_delivery_revision_rejects_caller_authorized_sibling_attempt(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    sibling = issue_processing_attempt(request)
    producer = issue_processing_attempt(request)

    with pytest.raises(DeliveryLineageContractError, match="retry predecessor"):
        mint_targeted_delivery_revision_candidate(
            cell=mint_targeted_delivery_cell_key(_sample(), _target()),
            producer_attempt=producer,
            projection_contract_version="targeted_projection",
            evidence_artifacts=(_artifact(sibling, b"sibling-evidence"),),
            dependency_processing_attempt_ids=(sibling.processing_attempt_id,),
            selected_payload=b"invalid-cross-attempt-selection",
        )


def test_artifact_closure_derives_retry_scope_from_producer_attempt(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    predecessor = issue_processing_attempt(request)
    retry = issue_processing_attempt(request, retry_of=predecessor)
    sibling = issue_processing_attempt(request)

    assert canonical_evidence_artifact_closure(
        producer_attempt=retry,
        artifacts=(_artifact(predecessor, b"retry-evidence"),),
    )
    with pytest.raises(
        EvidenceArtifactClosureError,
        match="different processing attempt",
    ):
        canonical_evidence_artifact_closure(
            producer_attempt=retry,
            artifacts=(_artifact(sibling, b"sibling-evidence"),),
        )


def test_delivery_stage_outputs_must_come_from_current_attempt(tmp_path: Path) -> None:
    request = _request(tmp_path)
    predecessor = issue_processing_attempt(request)
    retry = issue_processing_attempt(request, retry_of=predecessor)
    predecessor_artifact = _artifact(predecessor, b"predecessor-evidence")
    revision = mint_targeted_delivery_revision_candidate(
        cell=mint_targeted_delivery_cell_key(_sample(), _target()),
        producer_attempt=retry,
        projection_contract_version="targeted_projection",
        evidence_artifacts=(predecessor_artifact,),
        selected_payload=b"selected-from-retry-chain",
    )

    with pytest.raises(
        EvidenceArtifactClosureError,
        match="different processing attempt",
    ):
        stage_targeted_delivery_candidate(
            producer_attempt=retry,
            revisions=(revision,),
            output_artifacts=(predecessor_artifact,),
        )


def _sample():
    snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="SampleA", raw_stem="SampleA"),),
        identity_namespace="package0-targeted",
    )
    return resolve_canonical_sample_identity(snapshot, "SampleA")


def _target():
    snapshot = mint_target_list_snapshot(
        (
            Target(
                label="Analyte",
                mz=258.1085,
                rt_min=8.0,
                rt_max=10.0,
                ppm_tol=20.0,
                neutral_loss_da=116.0474,
                nl_ppm_warn=20.0,
                nl_ppm_max=50.0,
                is_istd=False,
                istd_pair="",
            ),
        ),
        identity_namespace="package0-targeted",
    )
    return resolve_target_definition(snapshot, "Analyte")


def _request(tmp_path: Path):
    path = tmp_path / "source.fixture"
    path.write_bytes(b"no-raw-source")
    source = build_acquisition_source_identity(
        path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )
    return build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="SampleA",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="package0_fixture",
        processing_algorithm_version="package0_fixture",
        processing_contract_version="package0_fixture",
        scientific_inputs={"neutral_loss_da": 116.0474},
    )


def _attempt(tmp_path: Path):
    return issue_processing_attempt(_request(tmp_path))


def _artifact(attempt, payload: bytes):
    contract = _mint_artifact_semantic_contract_ref(
        owner_namespace="tests.package0_delivery_lineage",
        semantic_role="targeted_selected_result_fixture",
        schema_version="targeted_selected_result_fixture",
        validation_status="diagnostic_only",
        media_type="application/json",
        serialization_format="canonical-json",
    )
    return _mint_evidence_artifact_ref(
        producer_processing_request_id=attempt.processing_request_id,
        producer_processing_attempt_id=attempt.processing_attempt_id,
        semantic_contract=contract,
        payload_bytes=payload,
    )
