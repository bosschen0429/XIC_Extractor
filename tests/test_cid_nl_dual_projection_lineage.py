from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from xic_extractor import raw_reader
from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
    mint_target_list_snapshot,
    resolve_target_definition,
    target_list_snapshot_payload,
)
from xic_extractor.discovery import cid_nl_evidence_projection as untargeted_module
from xic_extractor.discovery.cid_nl_evidence_projection import (
    UntargetedCidNlProjectionError,
    UntargetedCidNlProjectionRef,
    canonical_untargeted_cid_nl_projection_bytes,
    mint_untargeted_cid_nl_projection_ref,
)
from xic_extractor.discovery.models import DiscoverySeed
from xic_extractor.evidence_artifacts import verify_evidence_artifact_payload
from xic_extractor.extraction import cid_nl_evidence_projection as targeted_module
from xic_extractor.extraction.cid_nl_evidence_projection import (
    TargetedCidNlProjectionError,
    TargetedCidNlProjectionRef,
    canonical_targeted_cid_nl_projection_bytes,
    mint_targeted_cid_nl_projection_ref,
)
from xic_extractor.neutral_loss import MS2ProductEvidence
from xic_extractor.peak_detection import cid_nl_evidence as canonical_module
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceContractError,
    CidNlEvidenceRef,
    canonical_cid_nl_evidence_bytes,
    mint_cid_nl_evidence_ref,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    IdentityContractError,
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.review_actions_models import ReviewAction
from xic_extractor.review_records import (
    ReviewRecordReferenceError,
    mint_review_record_reference,
    mint_review_record_registry_snapshot,
    resolve_current_review_record,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    SampleMetadataSnapshot,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

_PRECURSOR_MZ = 258.1085
_NEUTRAL_LOSS_DA = 116.0474
_PRODUCT_MZ = _PRECURSOR_MZ - _NEUTRAL_LOSS_DA
_OBSERVED_LOSS_DA = _PRECURSOR_MZ - _PRODUCT_MZ
_OBSERVED_LOSS_ERROR_PPM = (
    abs(_OBSERVED_LOSS_DA - _NEUTRAL_LOSS_DA) / _NEUTRAL_LOSS_DA * 1_000_000.0
)


@dataclass(frozen=True)
class _TracerFixture:
    request: ProcessingRequestIdentity
    attempt: ProcessingAttemptIdentity
    sample_snapshot: SampleMetadataSnapshot
    sample: ResolvedCanonicalSampleIdentity
    target_snapshot: TargetListSnapshot
    target: ResolvedTargetDefinition
    targeted_evidence: MS2ProductEvidence
    discovery_seed: DiscoverySeed


@dataclass(frozen=True)
class _TracerRefs:
    evidence: CidNlEvidenceRef
    targeted: TargetedCidNlProjectionRef
    untargeted: UntargetedCidNlProjectionRef


def test_dual_projection_modules_have_no_raw_gui_or_writer_dependency() -> None:
    imported_modules: set[str] = set()
    for module in (canonical_module, targeted_module, untargeted_module):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
            elif isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)

    forbidden = (
        "xic_extractor.raw_reader",
        "xic_extractor.gui",
        "xic_extractor.output",
    )
    assert not any(
        module_name.startswith(forbidden) for module_name in imported_modules
    )


def test_same_canonical_evidence_feeds_both_projections_without_source_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    initial_paths = tuple(sorted(tmp_path.iterdir()))
    calls = {"source_open": 0, "raw_open": 0, "xic_call": 0}

    def fail_source_open(*_args: object, **_kwargs: object) -> object:
        calls["source_open"] += 1
        raise AssertionError("retained evidence must not reopen the source")

    class CountingRaw:
        def __enter__(self) -> "CountingRaw":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_xic(self, *_args: object, **_kwargs: object) -> None:
            calls["xic_call"] += 1

        def extract_xic_many(self, *_args: object, **_kwargs: object) -> tuple[()]:
            calls["xic_call"] += 1
            return ()

    def counting_raw_open(*_args: object, **_kwargs: object) -> CountingRaw:
        calls["raw_open"] += 1
        return CountingRaw()

    monkeypatch.setattr(Path, "open", fail_source_open)
    monkeypatch.setattr(raw_reader, "open_raw", counting_raw_open)

    refs = _mint_refs(fixture)
    lineage = compose_processing_owner_references(
        request=fixture.request,
        attempt=fixture.attempt,
        sample_snapshot=fixture.sample_snapshot,
        sample_resolutions=(fixture.sample,),
        target_snapshot=fixture.target_snapshot,
        evidence_artifacts=(
            refs.untargeted.artifact,
            refs.targeted.artifact,
            refs.evidence.artifact,
        ),
    )

    evidence_id = refs.evidence.artifact.evidence_artifact_id
    assert refs.targeted.artifact.upstream_artifact_ids == (evidence_id,)
    assert refs.untargeted.artifact.upstream_artifact_ids == (evidence_id,)
    assert refs.targeted.artifact.semantic_contract.semantic_role != (
        refs.untargeted.artifact.semantic_contract.semantic_role
    )
    assert set(lineage.evidence_artifacts) == {
        refs.evidence.artifact,
        refs.targeted.artifact,
        refs.untargeted.artifact,
    }
    assert calls == {"source_open": 0, "raw_open": 0, "xic_call": 0}
    assert tuple(sorted(tmp_path.iterdir())) == initial_paths

    verify_evidence_artifact_payload(
        refs.evidence.artifact,
        canonical_cid_nl_evidence_bytes(refs.evidence.observation),
    )
    verify_evidence_artifact_payload(
        refs.targeted.artifact,
        canonical_targeted_cid_nl_projection_bytes(refs.targeted),
    )
    verify_evidence_artifact_payload(
        refs.untargeted.artifact,
        canonical_untargeted_cid_nl_projection_bytes(refs.untargeted),
    )


def test_signed_zero_cid_nl_evidence_has_one_canonical_identity(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    processing_owners = _processing_owners(fixture)
    inputs = {
        "processing_owners": processing_owners,
        "sample": fixture.sample,
        "scan_number": 42,
        "precursor_mz": 500.0,
        "product_mz": 384.0,
        "configured_neutral_loss_da": 116.0,
        "observed_neutral_loss_da": 116.0,
        "scan_rt_min": 9.0,
        "product_intensity": 400.0,
    }

    positive = mint_cid_nl_evidence_ref(
        **inputs,
        observed_loss_error_ppm=0.0,
    )
    negative = mint_cid_nl_evidence_ref(
        **inputs,
        observed_loss_error_ppm=-0.0,
    )

    assert negative.observation == positive.observation
    assert canonical_cid_nl_evidence_bytes(negative.observation) == (
        canonical_cid_nl_evidence_bytes(positive.observation)
    )
    assert negative.artifact.content_identity == positive.artifact.content_identity


def test_observation_mutation_and_workflow_evidence_substitution_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    refs = _mint_refs(fixture)

    with pytest.raises(CidNlEvidenceContractError, match="mismatches"):
        replace(
            refs.evidence,
            observation=replace(
                refs.evidence.observation,
                product_intensity=refs.evidence.observation.product_intensity + 1.0,
            ),
        )
    with pytest.raises(TargetedCidNlProjectionError, match="product_mz"):
        mint_targeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            target=fixture.target,
            canonical_evidence=refs.evidence,
            targeted_evidence=replace(
                fixture.targeted_evidence,
                product_mz=fixture.targeted_evidence.product_mz + 0.1,
            ),
        )
    with pytest.raises(UntargetedCidNlProjectionError, match="product_mz"):
        mint_untargeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            canonical_evidence=refs.evidence,
            discovery_seed=replace(
                fixture.discovery_seed,
                product_mz=fixture.discovery_seed.product_mz + 0.1,
            ),
        )
    with pytest.raises(UntargetedCidNlProjectionError, match="scan_precursor"):
        mint_untargeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            canonical_evidence=refs.evidence,
            discovery_seed=replace(
                fixture.discovery_seed,
                precursor_mz_basis="product_plus_neutral_loss",
            ),
        )
    with pytest.raises(UntargetedCidNlProjectionError, match="scan_number"):
        mint_untargeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            canonical_evidence=refs.evidence,
            discovery_seed=replace(
                fixture.discovery_seed,
                scan_number=fixture.discovery_seed.scan_number + 1,
            ),
        )
    with pytest.raises(TargetedCidNlProjectionError, match="scan_number"):
        mint_targeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            target=fixture.target,
            canonical_evidence=refs.evidence,
            targeted_evidence=replace(
                fixture.targeted_evidence,
                scan_number=fixture.discovery_seed.scan_number + 1,
            ),
        )


def test_projection_sources_must_match_the_request_acquisition_binding(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    foreign = _fixture(tmp_path, source_name="qc2")
    refs = _mint_refs(fixture)
    foreign_source_id = foreign.request.acquisition_sources[0].acquisition_source_id

    with pytest.raises(TargetedCidNlProjectionError, match="acquisition source"):
        mint_targeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            target=fixture.target,
            canonical_evidence=refs.evidence,
            targeted_evidence=replace(
                fixture.targeted_evidence,
                acquisition_source_id=foreign_source_id,
            ),
        )
    with pytest.raises(UntargetedCidNlProjectionError, match="acquisition source"):
        mint_untargeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            canonical_evidence=refs.evidence,
            discovery_seed=replace(
                fixture.discovery_seed,
                acquisition_source_id=foreign_source_id,
            ),
        )


@pytest.mark.parametrize("scan_precursor_mz", [None, _PRECURSOR_MZ + 50.0])
def test_direct_discovery_projection_binds_the_measured_scan_precursor(
    tmp_path: Path,
    scan_precursor_mz: float | None,
) -> None:
    fixture = _fixture(tmp_path)
    refs = _mint_refs(fixture)

    with pytest.raises(UntargetedCidNlProjectionError, match="scan_precursor_mz"):
        mint_untargeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            canonical_evidence=refs.evidence,
            discovery_seed=replace(
                fixture.discovery_seed,
                scan_precursor_mz=scan_precursor_mz,
            ),
        )


def test_retry_reuses_content_identity_but_mints_distinct_artifact_instances(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    first = _mint_refs(fixture)
    retry = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
    retry_fixture = replace(fixture, attempt=retry)
    retried = _mint_refs(retry_fixture)

    assert first.evidence.artifact.content_identity == (
        retried.evidence.artifact.content_identity
    )
    assert first.evidence.artifact.evidence_artifact_id != (
        retried.evidence.artifact.evidence_artifact_id
    )
    assert first.targeted.artifact.evidence_artifact_id != (
        retried.targeted.artifact.evidence_artifact_id
    )
    assert first.untargeted.artifact.evidence_artifact_id != (
        retried.untargeted.artifact.evidence_artifact_id
    )


def test_sample_target_request_and_attempt_substitution_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    refs = _mint_refs(fixture)
    other_sample_snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="QC2", raw_stem="qc2"),),
        identity_namespace="study:cid-nl-tracer",
    )
    other_sample = resolve_canonical_sample_identity(other_sample_snapshot, "qc2")

    with pytest.raises(TargetedCidNlProjectionError, match="different sample"):
        mint_targeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=other_sample,
            target=fixture.target,
            canonical_evidence=refs.evidence,
            targeted_evidence=fixture.targeted_evidence,
        )
    with pytest.raises(UntargetedCidNlProjectionError, match="different sample"):
        mint_untargeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=other_sample,
            canonical_evidence=refs.evidence,
            discovery_seed=fixture.discovery_seed,
        )

    changed_target_snapshot = mint_target_list_snapshot(
        (replace(fixture.target.definition.target, mz=_PRECURSOR_MZ + 1.0),),
        identity_namespace=fixture.target_snapshot.identity_namespace,
    )
    changed_target = resolve_target_definition(
        changed_target_snapshot,
        fixture.target.definition.target.label,
    )
    with pytest.raises(TargetedCidNlProjectionError, match="target-list snapshot"):
        mint_targeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            target=changed_target,
            canonical_evidence=refs.evidence,
            targeted_evidence=fixture.targeted_evidence,
        )

    chemically_identical_snapshot = mint_target_list_snapshot(
        (fixture.target.definition.target,),
        identity_namespace="method:foreign-cid-nl-tracer",
    )
    chemically_identical_foreign_target = resolve_target_definition(
        chemically_identical_snapshot,
        fixture.target.definition.target.label,
    )
    with pytest.raises(TargetedCidNlProjectionError, match="target-list snapshot"):
        mint_targeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture),
            sample=fixture.sample,
            target=chemically_identical_foreign_target,
            canonical_evidence=refs.evidence,
            targeted_evidence=fixture.targeted_evidence,
        )

    retry = issue_processing_attempt(fixture.request, retry_of=fixture.attempt)
    with pytest.raises(
        TargetedCidNlProjectionError,
        match="different processing attempt",
    ):
        mint_targeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(fixture, attempt=retry),
            sample=fixture.sample,
            target=fixture.target,
            canonical_evidence=refs.evidence,
            targeted_evidence=fixture.targeted_evidence,
        )

    other_fixture = _fixture(tmp_path, source_name="other-source")
    with pytest.raises(
        UntargetedCidNlProjectionError,
        match="different processing request",
    ):
        mint_untargeted_cid_nl_projection_ref(
            processing_owners=_processing_owners(other_fixture),
            sample=other_fixture.sample,
            canonical_evidence=refs.evidence,
            discovery_seed=fixture.discovery_seed,
        )


def test_targeted_projection_binds_scan_precursor_to_the_exact_target(
    tmp_path: Path,
) -> None:
    nearby_target = Target(
        label="Nearby NL116 ISTD",
        mz=_PRECURSOR_MZ + 0.0200005,
        rt_min=8.5,
        rt_max=9.5,
        ppm_tol=10.0,
        neutral_loss_da=_NEUTRAL_LOSS_DA,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    fixture = _fixture(tmp_path, additional_targets=(nearby_target,))
    processing_owners = _processing_owners(fixture)
    exact_target = fixture.target.definition.target
    exact_product_mz = exact_target.mz - _NEUTRAL_LOSS_DA
    nearby_product_mz = nearby_target.mz - _NEUTRAL_LOSS_DA
    observed_product_mz = (
        2.0
        * exact_product_mz
        * nearby_product_mz
        / (exact_product_mz + nearby_product_mz)
    )
    target_product_ppm = (
        abs(observed_product_mz - exact_product_mz)
        / exact_product_mz
        * 1_000_000.0
    )
    observed_loss_da = _PRECURSOR_MZ - observed_product_mz
    observed_loss_error_ppm = (
        abs(observed_loss_da - _NEUTRAL_LOSS_DA)
        / _NEUTRAL_LOSS_DA
        * 1_000_000.0
    )
    source_id = fixture.request.acquisition_sources[0].acquisition_source_id
    evidence = mint_cid_nl_evidence_ref(
        processing_owners=processing_owners,
        sample=fixture.sample,
        scan_number=42,
        precursor_mz=_PRECURSOR_MZ,
        product_mz=observed_product_mz,
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=observed_loss_da,
        observed_loss_error_ppm=observed_loss_error_ppm,
        scan_rt_min=9.0,
        product_intensity=400.0,
    )
    targeted_evidence = MS2ProductEvidence(
        scan_rt=9.0,
        precursor_mz=_PRECURSOR_MZ,
        product_mz=observed_product_mz,
        product_intensity=400.0,
        product_base_ratio=0.4,
        target_product_ppm=target_product_ppm,
        observed_loss_da=observed_loss_da,
        observed_loss_error_ppm=observed_loss_error_ppm,
        scan_number=42,
        acquisition_source_id=source_id,
    )

    mint_targeted_cid_nl_projection_ref(
        processing_owners=processing_owners,
        sample=fixture.sample,
        target=fixture.target,
        canonical_evidence=evidence,
        targeted_evidence=targeted_evidence,
    )
    wrong_target = resolve_target_definition(
        fixture.target_snapshot,
        nearby_target.label,
    )
    with pytest.raises(TargetedCidNlProjectionError, match="precursor"):
        mint_targeted_cid_nl_projection_ref(
            processing_owners=processing_owners,
            sample=fixture.sample,
            target=wrong_target,
            canonical_evidence=evidence,
            targeted_evidence=targeted_evidence,
        )


def test_projection_reorder_is_neutral_but_missing_shared_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    refs = _mint_refs(fixture)
    forward = compose_processing_owner_references(
        request=fixture.request,
        attempt=fixture.attempt,
        sample_snapshot=fixture.sample_snapshot,
        sample_resolutions=(fixture.sample,),
        target_snapshot=fixture.target_snapshot,
        evidence_artifacts=(
            refs.evidence.artifact,
            refs.targeted.artifact,
            refs.untargeted.artifact,
        ),
    )
    reverse = compose_processing_owner_references(
        request=fixture.request,
        attempt=fixture.attempt,
        sample_snapshot=fixture.sample_snapshot,
        sample_resolutions=(fixture.sample,),
        target_snapshot=fixture.target_snapshot,
        evidence_artifacts=(
            refs.untargeted.artifact,
            refs.targeted.artifact,
            refs.evidence.artifact,
        ),
    )

    assert forward == reverse
    with pytest.raises(IdentityContractError, match="missing upstream"):
        compose_processing_owner_references(
            request=fixture.request,
            attempt=fixture.attempt,
            sample_snapshot=fixture.sample_snapshot,
            sample_resolutions=(fixture.sample,),
            target_snapshot=fixture.target_snapshot,
            evidence_artifacts=(
                refs.targeted.artifact,
                refs.untargeted.artifact,
            ),
        )


def test_review_owner_rejects_superseded_dual_projection_conclusion(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    refs = _mint_refs(fixture)
    artifacts = (
        refs.evidence.artifact,
        refs.targeted.artifact,
        refs.untargeted.artifact,
    )
    initial_action = _review_action(comment="initial dual-projection review")
    initial = mint_review_record_reference(
        record=initial_action,
        sample=fixture.sample,
        target=fixture.target,
        processing_request_id=fixture.request.processing_request_id,
        evidence_artifacts=artifacts,
    )
    successor_action = _review_action(comment="superseding dual-projection review")
    successor = mint_review_record_reference(
        record=successor_action,
        sample=fixture.sample,
        target=fixture.target,
        processing_request_id=fixture.request.processing_request_id,
        evidence_artifacts=artifacts,
        supersedes=initial,
        supersession_reason="review conclusion corrected",
    )
    registry = mint_review_record_registry_snapshot((successor, initial))

    with pytest.raises(ReviewRecordReferenceError, match="superseded"):
        resolve_current_review_record(
            registry,
            review_record_id=initial.review_record_id,
            expected_record=initial_action,
            expected_sample=fixture.sample,
            expected_target=fixture.target,
            expected_processing_request_id=fixture.request.processing_request_id,
            expected_evidence_artifacts=artifacts,
        )
    current = resolve_current_review_record(
        registry,
        review_record_id=successor.review_record_id,
        expected_record=successor_action,
        expected_sample=fixture.sample,
        expected_target=fixture.target,
        expected_processing_request_id=fixture.request.processing_request_id,
        expected_evidence_artifacts=artifacts,
    )
    lineage = compose_processing_owner_references(
        request=fixture.request,
        attempt=fixture.attempt,
        sample_snapshot=fixture.sample_snapshot,
        sample_resolutions=(fixture.sample,),
        target_snapshot=fixture.target_snapshot,
        evidence_artifacts=artifacts,
        review_target=fixture.target,
        review_snapshot=registry,
        review_record=current,
    )

    assert lineage.review_record == current


def _fixture(
    tmp_path: Path,
    *,
    source_name: str = "qc1",
    additional_targets: tuple[Target, ...] = (),
) -> _TracerFixture:
    source_path = tmp_path / f"{source_name}.synthetic"
    source_path.write_bytes(f"synthetic:{source_name}".encode("ascii"))
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="synthetic_no_raw",
        observation_namespace=f"synthetic:{source_name}",
    )
    primary_target = Target(
        label="NL116 ISTD",
        mz=_PRECURSOR_MZ,
        rt_min=8.5,
        rt_max=9.5,
        ppm_tol=10.0,
        neutral_loss_da=_NEUTRAL_LOSS_DA,
        nl_ppm_warn=20.0,
        nl_ppm_max=50.0,
        is_istd=True,
        istd_pair="",
    )
    target_snapshot = mint_target_list_snapshot(
        (primary_target, *additional_targets),
        identity_namespace="method:cid-nl-tracer",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=source_name,
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="cid-nl-dual-projection-input",
        processing_algorithm_version="cid-nl-dual-projection-algorithm",
        processing_contract_version="cid-nl-dual-projection-contract",
        scientific_inputs={
            "fixture": "no-raw-cid-nl-dual-projection",
            "target_list_snapshot": target_list_snapshot_payload(target_snapshot),
        },
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="QC1", raw_stem=source_name),),
        identity_namespace="study:cid-nl-tracer",
    )
    sample = resolve_canonical_sample_identity(sample_snapshot, source_name)
    target = resolve_target_definition(target_snapshot, "NL116 ISTD")
    targeted_evidence = MS2ProductEvidence(
        scan_rt=9.0,
        precursor_mz=_PRECURSOR_MZ,
        product_mz=_PRODUCT_MZ,
        product_intensity=400.0,
        product_base_ratio=0.4,
        target_product_ppm=0.0,
        observed_loss_da=_OBSERVED_LOSS_DA,
        observed_loss_error_ppm=_OBSERVED_LOSS_ERROR_PPM,
        scan_number=42,
        acquisition_source_id=source.acquisition_source_id,
    )
    discovery_seed = DiscoverySeed(
        raw_file=Path(f"{source_name}.raw"),
        sample_stem=source_name,
        scan_number=42,
        rt=9.0,
        precursor_mz=_PRECURSOR_MZ,
        product_mz=_PRODUCT_MZ,
        product_intensity=400.0,
        neutral_loss_tag="NL116",
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=_OBSERVED_LOSS_DA,
        observed_loss_error_ppm=_OBSERVED_LOSS_ERROR_PPM,
        matched_tag_names=("NL116",),
        scan_precursor_mz=_PRECURSOR_MZ,
        precursor_mz_basis="scan_precursor",
        acquisition_source_id=source.acquisition_source_id,
    )
    return _TracerFixture(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=sample_snapshot,
        sample=sample,
        target_snapshot=target_snapshot,
        target=target,
        targeted_evidence=targeted_evidence,
        discovery_seed=discovery_seed,
    )


def _mint_refs(fixture: _TracerFixture) -> _TracerRefs:
    processing_owners = compose_processing_owner_references(
        request=fixture.request,
        attempt=fixture.attempt,
        sample_snapshot=fixture.sample_snapshot,
        sample_resolutions=(fixture.sample,),
        target_snapshot=fixture.target_snapshot,
    )
    evidence = mint_cid_nl_evidence_ref(
        processing_owners=processing_owners,
        sample=fixture.sample,
        scan_number=fixture.discovery_seed.scan_number,
        precursor_mz=fixture.targeted_evidence.precursor_mz,
        product_mz=fixture.targeted_evidence.product_mz,
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=fixture.targeted_evidence.observed_loss_da,
        observed_loss_error_ppm=fixture.targeted_evidence.observed_loss_error_ppm,
        scan_rt_min=fixture.targeted_evidence.scan_rt,
        product_intensity=fixture.targeted_evidence.product_intensity,
    )
    targeted = mint_targeted_cid_nl_projection_ref(
        processing_owners=processing_owners,
        sample=fixture.sample,
        target=fixture.target,
        canonical_evidence=evidence,
        targeted_evidence=fixture.targeted_evidence,
    )
    untargeted = mint_untargeted_cid_nl_projection_ref(
        processing_owners=processing_owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        discovery_seed=fixture.discovery_seed,
    )
    return _TracerRefs(
        evidence=evidence,
        targeted=targeted,
        untargeted=untargeted,
    )


def _processing_owners(
    fixture: _TracerFixture,
    *,
    attempt: ProcessingAttemptIdentity | None = None,
) -> ProcessingOwnerReferences:
    return compose_processing_owner_references(
        request=fixture.request,
        attempt=fixture.attempt if attempt is None else attempt,
        sample_snapshot=fixture.sample_snapshot,
        sample_resolutions=(fixture.sample,),
        target_snapshot=fixture.target_snapshot,
    )


def _review_action(*, comment: str) -> ReviewAction:
    return ReviewAction(
        sample_name="QC1",
        target_label="NL116 ISTD",
        action_type="accept_current",
        comment=comment,
        reviewer="analyst",
        reviewed_at="2026-07-26T12:00:00Z",
    )
