"""Source freshness and run-spec reuse at planning/runtime boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

import xic_extractor.discovery.processing_run_spec_adapter as processing_spec_adapter
import xic_extractor.processing_identity as identity_owner
import xic_extractor.raw_source_binding as raw_source_binding_owner
from tests.cid_nl_runtime_fixtures import (
    install_runtime_raws,
    prepare_persisted_discovery_plan,
)
from tests.untargeted_cid_nl_production_batch_fixtures import (
    _source_verification_inputs,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_with_expectations,
)
from xic_extractor.untargeted_execution.execution import (
    execute_untargeted_plan,
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

    plan = prepare_persisted_discovery_plan(**arguments, root=tmp_path / "persisted")
    monkeypatch.setattr(identity_owner, "fingerprint_file_content", count_fingerprint)
    monkeypatch.setattr(
        raw_source_binding_owner, "fingerprint_file_content", count_fingerprint
    )
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


@pytest.mark.parametrize("change", ["edit", "replace"])
def test_production_rejects_source_change_after_input_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    path, raw, arguments = _source_verification_inputs(tmp_path)
    plan = prepare_persisted_discovery_plan(**arguments, root=tmp_path / "persisted")
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
