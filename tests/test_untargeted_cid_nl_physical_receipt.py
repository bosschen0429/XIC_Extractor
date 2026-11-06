"""Persisted candidate morphology must agree with retained physical facts."""

from dataclasses import replace

import pytest

from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _current_candidates,
    _FakeRaw,
    _open_bound_raw,
    _peak_config,
    _settings,
    bind_physical_capture,
)
from tests.cid_nl_runtime_fixtures import build_discovery_receipts, install_runtime_raw
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.cid_nl_inputs import CID_NL_ALIGNMENT_CONFIG
from xic_extractor.untargeted_execution.partition.cid_nl_raw_source import (
    CidNlAcquisitionError,
)
from xic_extractor.untargeted_execution.partition.cid_nl_sample_acquisition import (
    adapt_current_discovery_sample_subject,
)
from xic_extractor.untargeted_execution.partition.execution import execute_partition
from xic_extractor.untargeted_execution.partition.models import UntargetedPartitionJob
from xic_extractor.untargeted_execution.partition.request import build_partition_context


def _physical_fixture(tmp_path):
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"S1": "study_sample"}, thermo_raw_sources=True
    )
    source = _FakeRaw((8.0,))
    source.intensity += 250.0
    settings = _settings()
    config = _peak_config(tmp_path)
    records = {}
    (candidate,) = _current_candidates(
        source,
        raw_file=fixture.source_paths["S1"],
        settings=settings,
        physical_records=records,
    )
    fixture = replace(fixture, owners=bind_physical_capture(fixture.owners, records))
    return fixture, source, settings, config, candidate, records


@pytest.mark.parametrize(
    ("candidate_field", "receipt_field"),
    (("ms1_area", "area_counts_seconds"), ("ms1_height", "height")),
)
@pytest.mark.parametrize("mutation", ("missing", "drift"))
def test_adapter_rejects_candidate_morphology_different_from_physical_receipt(
    tmp_path, candidate_field, receipt_field, mutation
):
    fixture, source, settings, config, candidate, records = _physical_fixture(tmp_path)
    value = getattr(candidate, candidate_field)
    assert value is not None
    candidate = replace(
        candidate, **{candidate_field: None if mutation == "missing" else value * 1.1}
    )
    raw, _vendor = _open_bound_raw(fixture, "S1", source, tmp_path=tmp_path)
    with pytest.raises(
        CidNlAcquisitionError,
        match=f"physical {receipt_field} mismatches current receipt",
    ):
        adapt_current_discovery_sample_subject(
            processing_owners=fixture.owners,
            sample=fixture.sample("S1"),
            method_subject=fixture.method_subject,
            candidates=(candidate,),
            settings=settings,
            peak_config=config,
            raw=raw,
            signal_processing_profile=TEST_PROFILE,
            physical_records=tuple(records.values()),
        )


def test_runtime_retains_physical_receipt_failure(tmp_path, monkeypatch):
    fixture, source, settings, config, candidate, records = _physical_fixture(tmp_path)
    candidate = replace(candidate, ms1_area=candidate.ms1_area * 1.1)
    paths = (fixture.source_paths["S1"],)
    candidates = {"S1": (candidate,)}
    context = build_partition_context(
        raw_paths=paths,
        sample_metadata=(fixture.sample("S1").metadata,),
        method_subject=fixture.method_subject,
        settings=settings,
        peak_config=config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        sample_identity_namespace="study:physical-receipt",
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate=records,
    )
    install_runtime_raw(
        monkeypatch, source_raw=source, raw_path=paths[0], tmp_path=tmp_path
    )
    job = UntargetedPartitionJob(
        processing_owners=context.processing_owners,
        sample=context.samples[0],
        method_subject=context.method_subject,
        candidates=(candidate,),
        discovery_receipt=context.discovery_receipts_by_sample["S1"],
        settings=settings,
        peak_config=config,
        raw_path=paths[0],
        dll_dir=tmp_path,
        work_unit_key="untargeted-cid-nl:S1",
        signal_processing_profile=TEST_PROFILE,
        physical_records=tuple(records.values()),
    )
    result = execute_partition(
        (job,),
        partition_key="runtime-fixture",
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
    )
    assert not result.succeeded
    assert result.sample_results == ()
    assert isinstance(result.failure, CidNlAcquisitionError)
    assert "physical area_counts_seconds mismatches current receipt" in str(
        result.failure
    )
