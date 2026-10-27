from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import (
    mint_target_list_snapshot,
    target_list_snapshot_payload,
)
from xic_extractor.discovery.cid_nl_evidence_projection import (
    UntargetedCidNlProjectionError,
    mint_untargeted_cid_nl_projection_ref,
)
from xic_extractor.discovery.models import DiscoverySeed
from xic_extractor.peak_detection.cid_nl_evidence import (
    mint_cid_nl_evidence_ref,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingOwnerReferences,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

_PRECURSOR_MZ = 258.1085
_NEUTRAL_LOSS_DA = 116.0474
_PRODUCT_MZ = _PRECURSOR_MZ - _NEUTRAL_LOSS_DA


@dataclass(frozen=True)
class _Fixture:
    owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    seed: DiscoverySeed


def _fixture(tmp_path: Path, *, source_name: str = "qc1") -> _Fixture:
    source_path = tmp_path / f"{source_name}.synthetic"
    source_path.write_bytes(f"synthetic:{source_name}".encode("ascii"))
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="synthetic_no_raw",
        observation_namespace=f"synthetic:{source_name}",
    )
    target = Target(
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
        (target,), identity_namespace="method:cid-nl-tracer"
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
        scientific_input_schema_version="cid-nl-projection-input",
        processing_algorithm_version="cid-nl-projection-algorithm",
        processing_contract_version="cid-nl-projection-contract",
        scientific_inputs={
            "fixture": "no-raw-cid-nl-projection",
            "target_list_snapshot": target_list_snapshot_payload(target_snapshot),
        },
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        (SampleMetadata(sample_name="QC1", raw_stem=source_name),),
        identity_namespace="study:cid-nl-tracer",
    )
    sample = resolve_canonical_sample_identity(sample_snapshot, source_name)
    owners = compose_processing_owner_references(
        request=request,
        attempt=issue_processing_attempt(request),
        sample_snapshot=sample_snapshot,
        sample_resolutions=(sample,),
        target_snapshot=target_snapshot,
    )
    seed = DiscoverySeed(
        raw_file=Path(f"{source_name}.raw"),
        sample_stem=source_name,
        scan_number=42,
        rt=9.0,
        precursor_mz=_PRECURSOR_MZ,
        product_mz=_PRODUCT_MZ,
        product_intensity=400.0,
        neutral_loss_tag="NL116",
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=_PRECURSOR_MZ - _PRODUCT_MZ,
        observed_loss_error_ppm=0.0,
        matched_tag_names=("NL116",),
        scan_precursor_mz=_PRECURSOR_MZ,
        precursor_mz_basis="scan_precursor",
        acquisition_source_id=source.acquisition_source_id,
    )
    return _Fixture(owners=owners, sample=sample, seed=seed)


def _evidence(fixture: _Fixture):
    seed = fixture.seed
    return mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_number=seed.scan_number,
        precursor_mz=seed.precursor_mz,
        product_mz=seed.product_mz,
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=seed.observed_neutral_loss_da,
        observed_loss_error_ppm=seed.observed_loss_error_ppm,
        scan_rt_min=seed.rt,
        product_intensity=seed.product_intensity,
    )


def test_canonical_evidence_feeds_untargeted_projection_without_source_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    evidence = _evidence(fixture)
    initial_paths = tuple(sorted(tmp_path.iterdir()))

    def fail_source_open(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("retained evidence must not reopen the source")

    monkeypatch.setattr(Path, "open", fail_source_open)
    projection = mint_untargeted_cid_nl_projection_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        canonical_evidence=evidence,
        discovery_seed=fixture.seed,
    )
    assert projection.artifact.upstream_artifact_ids == (
        evidence.artifact.evidence_artifact_id,
    )
    assert tuple(sorted(tmp_path.iterdir())) == initial_paths


def test_signed_zero_cid_nl_evidence_has_one_canonical_identity(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    positive = _evidence(fixture)
    negative = mint_cid_nl_evidence_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample,
        scan_number=fixture.seed.scan_number,
        precursor_mz=fixture.seed.precursor_mz,
        product_mz=fixture.seed.product_mz,
        configured_neutral_loss_da=_NEUTRAL_LOSS_DA,
        observed_neutral_loss_da=fixture.seed.observed_neutral_loss_da,
        observed_loss_error_ppm=-0.0,
        scan_rt_min=fixture.seed.rt,
        product_intensity=fixture.seed.product_intensity,
    )
    assert negative.observation == positive.observation
    assert negative.artifact.content_identity == positive.artifact.content_identity


def test_discovery_seed_substitution_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    evidence = _evidence(fixture)
    for seed, reason in (
        (replace(fixture.seed, product_mz=fixture.seed.product_mz + 0.1), "product_mz"),
        (replace(fixture.seed, scan_number=43), "scan_number"),
        (replace(fixture.seed, scan_precursor_mz=None), "scan_precursor_mz"),
    ):
        with pytest.raises(UntargetedCidNlProjectionError, match=reason):
            mint_untargeted_cid_nl_projection_ref(
                processing_owners=fixture.owners,
                sample=fixture.sample,
                canonical_evidence=evidence,
                discovery_seed=seed,
            )


def test_projection_source_must_match_request_binding(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    foreign = _fixture(tmp_path, source_name="qc2")
    evidence = _evidence(fixture)
    foreign_id = foreign.owners.request.acquisition_sources[0].acquisition_source_id
    with pytest.raises(UntargetedCidNlProjectionError, match="acquisition source"):
        mint_untargeted_cid_nl_projection_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample,
            canonical_evidence=evidence,
            discovery_seed=replace(fixture.seed, acquisition_source_id=foreign_id),
        )
