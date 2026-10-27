from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.configuration.models import Target
from xic_extractor.configuration.target_identity import (
    TargetListSnapshot,
    mint_target_list_snapshot,
    target_list_snapshot_payload,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    build_acquisition_source_identity,
    build_processing_request_identity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    SampleMetadataError,
    SampleMetadataSnapshot,
    mint_canonical_sample_identity,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)


def test_sample_owner_mints_canonical_identity_from_primary_sample_key() -> None:
    row = SampleMetadata(
        sample_name="Sample A",
        raw_stem="sample_a_acquisition",
        injection_order=3,
        sample_role="study_sample",
        batch_id="batch-1",
    )

    snapshot = _sample_snapshot(row)
    identity = mint_canonical_sample_identity(
        row,
        identity_namespace=snapshot.identity_namespace,
    )

    assert identity.sample_key == "Sample A"
    assert identity.sample_metadata_schema_version == "sample_metadata"
    by_key = resolve_canonical_sample_identity(snapshot, "Sample A")
    by_alias = resolve_canonical_sample_identity(snapshot, "sample_a_acquisition")

    assert by_key.identity == identity
    assert by_alias.identity == identity
    assert by_key.lookup_token == "Sample A"
    assert by_alias.lookup_token == "sample_a_acquisition"


def test_sample_identity_ignores_projection_metadata_but_not_sample_rename() -> None:
    original = SampleMetadata(sample_name="Sample A", raw_stem="raw_a")
    projection_changed = replace(
        original,
        injection_order=9,
        sample_role="qc",
        batch_id="batch-2",
        excluded=True,
        exclusion_reason="operator exclusion",
    )
    renamed = replace(original, sample_name="Sample A corrected")

    assert mint_canonical_sample_identity(
        projection_changed,
        identity_namespace="study:package-0-test",
    ) == (
        mint_canonical_sample_identity(
            original,
            identity_namespace="study:package-0-test",
        )
    )
    assert mint_canonical_sample_identity(
        renamed,
        identity_namespace="study:package-0-test",
    ) != (
        mint_canonical_sample_identity(
            original,
            identity_namespace="study:package-0-test",
        )
    )
    assert _sample_snapshot(projection_changed) != (_sample_snapshot(original))


def test_sample_resolution_fails_closed_for_missing_ambiguous_or_stale_alias() -> None:
    original_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample A", raw_stem="shared")
    )
    original = resolve_canonical_sample_identity(original_snapshot, "shared")

    with pytest.raises(SampleMetadataError, match="not owned"):
        resolve_canonical_sample_identity(original_snapshot, "shared.raw")
    with pytest.raises(SampleMetadataError, match="not owned"):
        resolve_canonical_sample_identity(original_snapshot, "1")
    with pytest.raises(SampleMetadataError, match="alias collision|duplicate"):
        _sample_snapshot(
            SampleMetadata(sample_name="Sample A", raw_stem="shared"),
            SampleMetadata(sample_name="Sample B", raw_stem="shared"),
        )
    changed_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample B", raw_stem="shared")
    )
    with pytest.raises(SampleMetadataError, match="stale"):
        resolve_canonical_sample_identity(
            changed_snapshot,
            "shared",
            expected_identity=original.identity,
        )
    with pytest.raises(SampleMetadataError, match="owner-normalized"):
        resolve_canonical_sample_identity(original_snapshot, " shared")


def test_sample_identity_namespace_prevents_cross_owner_collisions() -> None:
    row = SampleMetadata(sample_name="Sample A", raw_stem="raw_a")

    assert mint_canonical_sample_identity(
        row,
        identity_namespace="study:first",
    ) != mint_canonical_sample_identity(
        row,
        identity_namespace="study:second",
    )


def test_sample_snapshot_is_order_neutral_and_content_bound() -> None:
    first = SampleMetadata(sample_name="Sample A", raw_stem="raw_a")
    second = SampleMetadata(sample_name="Sample B", raw_stem="raw_b")

    forward = _sample_snapshot(first, second)
    reverse = _sample_snapshot(second, first)
    mutated = _sample_snapshot(first, replace(second, raw_stem="raw_b_changed"))

    assert forward == reverse
    assert forward != mutated


def test_lineage_only_composes_owner_minted_references(tmp_path: Path) -> None:
    target_snapshot = _target_snapshot()
    request = _request(
        tmp_path,
        payload=b"request-a",
        target_snapshot=target_snapshot,
    )
    attempt = issue_processing_attempt()
    sample_snapshot = _sample_snapshot(
        SampleMetadata(sample_name="Sample A", raw_stem="request-a"),
        SampleMetadata(sample_name="Sample B", raw_stem="request-b"),
    )
    sample = resolve_canonical_sample_identity(
        sample_snapshot,
        "request-a",
    )
    references = compose_processing_owner_references(
        request=request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=(sample,),
        target_snapshot=target_snapshot,
    )

    assert references.request is request
    assert references.attempt is attempt
    assert references.sample_snapshot is sample_snapshot
    assert references.sample_resolutions == (sample,)
    assert references.target_snapshot is target_snapshot


def _request(
    tmp_path: Path,
    *,
    payload: bytes,
    target_snapshot: TargetListSnapshot | None = None,
):
    return _request_many(
        tmp_path,
        payloads=(payload,),
        target_snapshot=target_snapshot,
    )


def _request_many(
    tmp_path: Path,
    *,
    payloads: tuple[bytes, ...],
    target_snapshot: TargetListSnapshot | None = None,
):
    sources = []
    bindings = []
    for injection_order, payload in enumerate(payloads, start=1):
        source_path = tmp_path / f"{payload.decode('ascii')}.raw"
        source_path.write_bytes(payload)
        source = build_acquisition_source_identity(
            source_path,
            acquisition_format="thermo_raw",
            observation_namespace="thermo_raw_file_reader:Device.MS:1",
        )
        sources.append(source)
        bindings.append(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=source_path.stem,
                injection_order=injection_order,
            )
        )
    scientific_inputs: dict[str, object] = {
        "fixture": [payload.decode("ascii") for payload in payloads],
    }
    if target_snapshot is not None:
        scientific_inputs["target_list_snapshot"] = target_list_snapshot_payload(
            target_snapshot
        )
    return build_processing_request_identity(
        acquisition_sources=tuple(sources),
        acquisition_bindings=tuple(bindings),
        scientific_input_schema_version="owner-reference-fixture",
        processing_algorithm_version="owner-reference-fixture",
        processing_contract_version="owner-reference-fixture",
        scientific_inputs=scientific_inputs,
    )


def _sample_snapshot(
    *rows: SampleMetadata,
    identity_namespace: str = "study:package-0-test",
) -> SampleMetadataSnapshot:
    return mint_sample_metadata_snapshot(
        rows,
        identity_namespace=identity_namespace,
    )


def _resolved_sample(sample_name: str = "Sample A") -> ResolvedCanonicalSampleIdentity:
    snapshot = _sample_snapshot(
        SampleMetadata(sample_name=sample_name, raw_stem=sample_name)
    )
    return resolve_canonical_sample_identity(snapshot, sample_name)


def _target_snapshot(
    *,
    target_label: str = "5-mdC",
    mz: float = 250.0,
) -> TargetListSnapshot:
    return mint_target_list_snapshot(
        (
            Target(
                label=target_label,
                mz=mz,
                rt_min=1.0,
                rt_max=3.0,
                ppm_tol=10.0,
                neutral_loss_da=116.0,
                nl_ppm_warn=20.0,
                nl_ppm_max=40.0,
                is_istd=False,
                istd_pair="",
            ),
        ),
        identity_namespace="target-list:package-0-test",
    )
