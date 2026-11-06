"""Source freshness and run-spec reuse at planning/runtime boundaries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.discovery.processing_run_spec_adapter as processing_spec_adapter
import xic_extractor.processing_identity as identity_owner
import xic_extractor.raw_source_binding as raw_source_binding_owner
import xic_extractor.untargeted_execution.partition.request as input_owner
from tests.cid_nl_raw_fixture import (
    TEST_PROFILE,
    _peak_config,
    _settings,
)
from tests.cid_nl_runtime_fixtures import (
    build_discovery_receipts,
    install_runtime_raws,
)
from tests.untargeted_cid_nl_batch_runtime_fixtures import _nearby_subject_candidates
from tests.untargeted_cid_nl_production_batch_fixtures import (
    _source_verification_inputs,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_with_expectations,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.execution import (
    execute_untargeted_plan,
)
from xic_extractor.untargeted_execution.input_planning import (
    prepare_eager_untargeted_plan,
)


def test_production_reuses_source_verification_across_runtime_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, raw, arguments = _source_verification_inputs(tmp_path)
    fingerprints = []
    fingerprint = identity_owner.fingerprint_file_content

    def count_fingerprint(source, **kwargs):
        fingerprints.append(source)
        return fingerprint(source, **kwargs)

    monkeypatch.setattr(identity_owner, "fingerprint_file_content", count_fingerprint)
    monkeypatch.setattr(
        raw_source_binding_owner, "fingerprint_file_content", count_fingerprint
    )
    plan = prepare_eager_untargeted_plan(**arguments)
    opened = install_runtime_raws(
        monkeypatch, sources_by_path={path: raw}, tmp_path=tmp_path
    )
    execute_untargeted_plan(
        plan,
        output_dir=tmp_path / "out",
        alignment_config=AlignmentConfig(),
        max_partition_jobs=1,
    )
    assert len(opened[path]) == len(plan.subject_batch.subjects) > 1
    assert fingerprints == []


def test_production_rejects_source_change_during_expectation_mint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _raw, arguments = _source_verification_inputs(tmp_path)
    build_source = input_owner.build_local_acquisition_source_identity

    def change_after_fingerprint(source_path, **kwargs):
        source = build_source(source_path, **kwargs)
        path.write_bytes(b"changed-after-fingerprint")
        return source

    monkeypatch.setattr(
        input_owner, "build_local_acquisition_source_identity", change_after_fingerprint
    )
    with pytest.raises(identity_owner.IdentityContractError, match="changed during"):
        prepare_eager_untargeted_plan(**arguments)


def test_reused_run_spec_source_rejects_change_before_plan(
    tmp_path: Path,
) -> None:
    path, _raw, arguments = _source_verification_inputs(tmp_path)
    _spec, expectations = compile_discovery_processing_run_spec_with_expectations(
        (path,),
        discovery_settings=arguments["settings"],
    )
    path.write_bytes(b"changed-between-preparation-owners")

    with pytest.raises(identity_owner.IdentityContractError, match="changed after"):
        prepare_eager_untargeted_plan(
            **arguments,
            acquisition_source_expectations=expectations,
        )


def test_run_spec_expectation_rejects_change_after_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _raw, arguments = _source_verification_inputs(tmp_path)
    build_source = processing_spec_adapter.build_local_acquisition_source_identity

    def change_after_fingerprint(source_path, **kwargs):
        source = build_source(source_path, **kwargs)
        path.write_bytes(b"changed-during-run-spec-preparation")
        return source

    monkeypatch.setattr(
        processing_spec_adapter,
        "build_local_acquisition_source_identity",
        change_after_fingerprint,
    )
    with pytest.raises(identity_owner.IdentityContractError, match="changed during"):
        compile_discovery_processing_run_spec_with_expectations(
            (path,),
            discovery_settings=arguments["settings"],
        )


@pytest.mark.parametrize("stems", [("B", "A"), ("a_sample", "B_sample"), ("A", "B")])
def test_reused_run_spec_source_preserves_plan_request_and_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stems: tuple[str, str],
) -> None:
    physical_records = {}
    settings = replace(
        _settings(), neutral_loss_profile=None, precursor_mz_tolerance_ppm=1.0
    )
    peak_config = _peak_config(tmp_path)
    paths = tuple(tmp_path / f"{stem}.synthetic" for stem in stems)
    candidates = {}
    for path in paths:
        path.write_bytes(f"source-verification:{path.stem}".encode())
        _, candidates[path.stem] = _nearby_subject_candidates(
            path,
            settings=settings,
            peak_config=peak_config,
            physical_records=physical_records,
        )
    arguments = dict(
        signal_processing_profile=TEST_PROFILE,
        physical_records_by_candidate=physical_records,
        raw_paths=paths,
        sample_metadata=tuple(
            SampleMetadata(
                sample_name=stem,
                raw_stem=stem,
                injection_order=index,
                sample_role="study_sample",
            )
            for index, stem in enumerate(stems, 1)
        ),
        settings=settings,
        peak_config=peak_config,
        candidates_by_sample=candidates,
        discovery_receipts_by_sample=build_discovery_receipts(
            tmp_path, paths, candidates
        ),
        dll_dir=tmp_path,
        sample_identity_namespace="study:source-verification",
    )
    baseline = prepare_eager_untargeted_plan(**arguments)
    spec, expectations = compile_discovery_processing_run_spec_with_expectations(
        paths,
        discovery_settings=settings,
    )
    fingerprints = []
    fingerprint = identity_owner.fingerprint_file_content

    def count_fingerprint(source, **kwargs):
        fingerprints.append(source)
        return fingerprint(source, **kwargs)

    monkeypatch.setattr(identity_owner, "fingerprint_file_content", count_fingerprint)
    monkeypatch.setattr(
        raw_source_binding_owner, "fingerprint_file_content", count_fingerprint
    )
    reused = prepare_eager_untargeted_plan(
        **arguments,
        acquisition_source_expectations=expectations,
    )
    assert fingerprints == []
    for path, expectation, acquisition in zip(
        paths, expectations, spec.acquisition_inputs, strict=True
    ):
        assert acquisition.acquisition_source == expectation.source
        assert reused.context.acquisition_sources_by_sample[path.stem] is expectation
        assert reused.context.raw_paths_by_sample[path.stem] == path
    assert reused.subject_batch == baseline.subject_batch
    assert reused.context.processing_owners.request == (
        baseline.context.processing_owners.request
    )
    assert reused.context.candidates_by_sample == baseline.context.candidates_by_sample
    assert reused.excluded_candidates == baseline.excluded_candidates


@pytest.mark.parametrize("change", ["edit", "replace"])
def test_production_rejects_source_change_after_input_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    path, raw, arguments = _source_verification_inputs(tmp_path)
    plan = prepare_eager_untargeted_plan(**arguments)
    if change == "edit":
        path.write_bytes(b"changed-before-runtime")
    else:
        replacement = tmp_path / "replacement.synthetic"
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
    install_runtime_raws(monkeypatch, sources_by_path={path: raw}, tmp_path=tmp_path)
    with pytest.raises(RuntimeError, match="failed before batch publication"):
        execute_untargeted_plan(
            plan,
            output_dir=tmp_path / "failed-output",
            alignment_config=AlignmentConfig(),
        )
