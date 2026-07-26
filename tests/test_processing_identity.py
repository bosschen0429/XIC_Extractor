import ast
import os
from dataclasses import fields, replace
from pathlib import Path
from uuid import UUID

import pytest

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.configuration.target_identity import mint_target_list_snapshot
from xic_extractor.extraction.targeted_inputs import (
    TARGETED_PROCESSING_ALGORITHM_VERSION,
    TARGETED_PROCESSING_CONTRACT_VERSION,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    ResolvedTargetedInputs,
    build_targeted_processing_request_identity,
)
from xic_extractor.peak_detection.model_selection import (
    ExpectedDiffApprovalRecord,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    AcquisitionSourceIdentity,
    IdentityContractError,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)
from xic_extractor.rt_prior_library import LibraryEntry


def test_canonical_typed_identity_golden_vectors(monkeypatch) -> None:
    source = AcquisitionSourceIdentity(
        acquisition_source_id=(
            "xic.acquisition-source.v1:"
            "34f07412e8ab8ecba62561ae55f884bf21b3b3ed65b70e9e74d5bfb828411bad"
        ),
        content_sha256=(
            "551ebcc397ca84390e53746bbf4c3b1a30c31f3ed35065dc724c40a8bbb89fc4"
        ),
        byte_size=13,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="sample_a",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="golden_inputs_v1",
        processing_algorithm_version="golden_algorithm_v1",
        processing_contract_version="golden_contract_v1",
        scientific_inputs={"z": [2, 1], "a": "é"},
    )
    assert request.processing_request_id == (
        "xic.processing-request.v1:"
        "2ad720c5b9c84c9aef69e5563f6c33f63c521264cbe6c1fe2ccc2d0189e1e061"
    )

    changed_algorithm = build_processing_request_identity(
        acquisition_sources=request.acquisition_sources,
        acquisition_bindings=request.acquisition_bindings,
        scientific_input_schema_version=request.scientific_input_schema_version,
        processing_algorithm_version="golden_algorithm_v2",
        processing_contract_version=request.processing_contract_version,
        scientific_inputs={"z": [2, 1], "a": "é"},
    )
    assert changed_algorithm.processing_request_id != request.processing_request_id

    monkeypatch.setattr(
        "xic_extractor.processing_identity.uuid.uuid4",
        lambda: UUID("00112233-4455-6677-8899-aabbccddeeff"),
    )
    attempt = issue_processing_attempt(request)
    assert attempt.processing_attempt_id == (
        "xic.processing-attempt.v1:"
        "0c4117db90c1d2eb67f7b194aa95eb294b12dcf17f638ace9f96dc12cb6c0bbe"
    )


def test_acquisition_source_identity_is_content_bound_not_path_bound(
    tmp_path: Path,
) -> None:
    first = _raw(tmp_path / "first.raw", b"same-acquisition")
    copied = _raw(tmp_path / "copied.raw", b"same-acquisition")

    first_identity = _source_identity(first)
    copied_identity = _source_identity(copied)

    assert first_identity == copied_identity
    assert first_identity.byte_size == len(b"same-acquisition")
    assert len(first_identity.content_sha256) == 64
    assert str(first) not in first_identity.acquisition_source_id


def test_acquisition_source_identity_changes_on_substitution_or_namespace(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"first-content")
    first = _source_identity(path)

    path.write_bytes(b"mutated-content")
    mutated = _source_identity(path)
    other_namespace = build_acquisition_source_identity(
        path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:2",
    )

    assert mutated.acquisition_source_id != first.acquisition_source_id
    assert mutated.content_sha256 != first.content_sha256
    assert other_namespace.acquisition_source_id != mutated.acquisition_source_id


def test_source_path_replacement_during_hash_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"original-content")
    displaced = tmp_path / "displaced.raw"
    original_open = Path.open

    class ReplacingHandle:
        def __init__(self, handle) -> None:
            self._handle = handle
            self._replaced = False

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def fileno(self):
            return self._handle.fileno()

        def read(self, size=-1):
            chunk = self._handle.read(size)
            if not self._replaced:
                self._replaced = True
                os.replace(path, displaced)
                with original_open(path, "wb") as replacement:
                    replacement.write(b"replacement-content")
            return chunk

    def replacing_open(self, *args, **kwargs):
        handle = original_open(self, *args, **kwargs)
        if self == path and args and args[0] == "rb":
            return ReplacingHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", replacing_open)

    with pytest.raises(IdentityContractError, match="mutated|replaced"):
        _source_identity(path)


def test_source_inventory_enumeration_reorder_does_not_change_request(
    tmp_path: Path,
) -> None:
    first = _raw(tmp_path / "sample_a.raw", b"source-a")
    second = _raw(tmp_path / "sample_b.raw", b"source-b")
    config = _config(tmp_path)
    resolved = _resolved_inputs()

    forward = _request(config, [first, second], resolved=resolved)
    reverse = _request(config, [second, first], resolved=resolved)

    assert reverse == forward


def test_semantic_injection_order_reorder_changes_request(tmp_path: Path) -> None:
    paths = [
        _raw(tmp_path / "sample_a.raw", b"source-a"),
        _raw(tmp_path / "sample_b.raw", b"source-b"),
    ]
    config = _config(tmp_path)
    forward = _request(config, paths, resolved=_resolved_inputs())
    reversed_order = _request(
        config,
        paths,
        resolved=replace(
            _resolved_inputs(),
            injection_order={"sample_a": 2, "sample_b": 1},
        ),
    )

    assert reversed_order.processing_request_id != forward.processing_request_id


def test_adapter_input_key_change_invalidates_request_not_source_content(
    tmp_path: Path,
) -> None:
    original_path = _raw(tmp_path / "sample_a.raw", b"source-a")
    renamed_path = _raw(tmp_path / "renamed_sample.raw", b"source-a")
    config = _config(tmp_path)
    original = _request(
        config,
        [original_path],
        resolved=ResolvedTargetedInputs(
            injection_order={"sample_a": 1},
            rt_prior_library={},
            model_selection_expected_diff_approvals=None,
        ),
    )
    renamed = _request(
        config,
        [renamed_path],
        resolved=ResolvedTargetedInputs(
            injection_order={"renamed_sample": 1},
            rt_prior_library={},
            model_selection_expected_diff_approvals=None,
        ),
    )

    assert original.acquisition_sources == renamed.acquisition_sources
    assert original.processing_request_id != renamed.processing_request_id


def test_request_invalidates_changed_source_target_rt_prior_or_approval(
    tmp_path: Path,
) -> None:
    first = _raw(tmp_path / "sample_a.raw", b"source-a")
    second = _raw(tmp_path / "sample_b.raw", b"source-b")
    paths = [first, second]
    config = _config(tmp_path)
    inputs = _resolved_inputs()
    baseline = _request(config, paths, resolved=inputs)

    first.write_bytes(b"source-a-substituted")
    substituted = _request(config, paths, resolved=inputs)
    target_changed = _request(
        config,
        paths,
        resolved=inputs,
        targets=(replace(_target(), mz=251.0),),
    )
    prior = inputs.rt_prior_library[("Analyte", "analyte")]
    rt_changed = _request(
        config,
        paths,
        resolved=replace(
            inputs,
            rt_prior_library={
                ("Analyte", "analyte"): replace(prior, median_abs_rt=2.5)
            },
        ),
    )
    approval = inputs.model_selection_expected_diff_approvals["approval-1"]
    approval_changed = _request(
        config,
        paths,
        resolved=replace(
            inputs,
            model_selection_expected_diff_approvals={
                "approval-1": replace(approval, evidence_summary="new evidence")
            },
        ),
    )

    changed_ids = {
        substituted.processing_request_id,
        target_changed.processing_request_id,
        rt_changed.processing_request_id,
        approval_changed.processing_request_id,
    }
    assert baseline.processing_request_id not in changed_ids
    assert len(changed_ids) == 4


def test_request_pins_owner_target_snapshot_order(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first_target = _target()
    second_target = replace(_target(), label="Other", mz=300.0)
    raw_path = _raw(tmp_path / "sample_a.raw", b"source-a")
    resolved = ResolvedTargetedInputs(
        injection_order={"sample_a": 1},
        rt_prior_library={},
        model_selection_expected_diff_approvals=None,
    )

    forward = _request(
        config,
        [raw_path],
        resolved=resolved,
        targets=(first_target, second_target),
    )
    reordered = _request(
        config,
        [raw_path],
        resolved=resolved,
        targets=(second_target, first_target),
    )

    assert reordered.processing_request_id != forward.processing_request_id
    assert "target_list_snapshot" in forward.normalized_scientific_inputs_json


def test_request_invalidates_ms1_shape_support_content_or_policy(
    tmp_path: Path,
) -> None:
    raw_path = _raw(tmp_path / "sample_a.raw", b"source-a")
    support_path = tmp_path / "shape-support.tsv"
    support_path.write_bytes(b"support-v1")
    config = replace(
        _config(tmp_path),
        targeted_ms1_shape_identity_support_tsv=support_path,
    )
    resolved = ResolvedTargetedInputs(
        injection_order={"sample_a": 1},
        rt_prior_library={},
        model_selection_expected_diff_approvals=None,
    )
    baseline = _request(config, [raw_path], resolved=resolved)

    support_path.write_bytes(b"support-v2")
    content_changed = _request(config, [raw_path], resolved=resolved)
    policy_changed = _request(
        replace(
            config,
            targeted_ms1_shape_identity_activation_policy="limited_5hmdc_5medc_v1",
        ),
        [raw_path],
        resolved=resolved,
    )

    assert content_changed.processing_request_id != baseline.processing_request_id
    assert policy_changed.processing_request_id != content_changed.processing_request_id


def test_request_excludes_output_callback_and_execution_policy(
    tmp_path: Path,
) -> None:
    paths = [
        _raw(tmp_path / "sample_a.raw", b"source-a"),
        _raw(tmp_path / "sample_b.raw", b"source-b"),
    ]
    config = _config(tmp_path)
    baseline = _request(config, paths)
    execution_changed = _request(
        replace(
            config,
            output_csv=tmp_path / "elsewhere" / "results.csv",
            diagnostics_csv=tmp_path / "elsewhere" / "diagnostics.csv",
            parallel_mode="process",
            parallel_workers=8,
            emit_review_report=True,
            emit_peak_candidates=True,
        ),
        paths,
    )

    assert execution_changed == baseline

    inactive_policy_changed = _request(
        replace(
            config,
            targeted_ms1_shape_identity_activation_policy="limited_5hmdc_5medc_v1",
        ),
        paths,
    )
    assert inactive_policy_changed == baseline


def test_none_and_empty_approval_snapshots_are_same_effective_input(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample_a.raw", b"source-a")
    config = _config(tmp_path)
    base = ResolvedTargetedInputs(
        injection_order={"sample_a": 1},
        rt_prior_library={},
        model_selection_expected_diff_approvals=None,
    )

    absent = _request(config, [path], resolved=base)
    empty = _request(
        config,
        [path],
        resolved=replace(
            base,
            model_selection_expected_diff_approvals={},
        ),
    )

    assert absent == empty


def test_request_snapshot_is_immutable_after_caller_mapping_mutation(
    tmp_path: Path,
) -> None:
    paths = [
        _raw(tmp_path / "sample_a.raw", b"source-a"),
        _raw(tmp_path / "sample_b.raw", b"source-b"),
    ]
    config = _config(tmp_path)
    inputs = _resolved_inputs()
    request = _request(config, paths, resolved=inputs)
    original_id = request.processing_request_id

    inputs.rt_prior_library[("Analyte", "analyte")] = replace(
        inputs.rt_prior_library[("Analyte", "analyte")],
        median_abs_rt=9.0,
    )
    inputs.model_selection_expected_diff_approvals.clear()

    assert request.processing_request_id == original_id
    assert _request(config, paths, resolved=inputs).processing_request_id != original_id


def test_missing_ambiguous_or_rebound_inputs_fail_closed(tmp_path: Path) -> None:
    first = _raw(tmp_path / "sample_a.raw", b"duplicate-content")
    second = _raw(tmp_path / "sample_b.raw", b"duplicate-content")
    config = _config(tmp_path)

    with pytest.raises(IdentityContractError, match="injection order"):
        _request(
            config,
            [first, second],
            resolved=replace(
                _resolved_inputs(),
                injection_order={"sample_a": 1},
            ),
        )

    with pytest.raises(IdentityContractError, match="duplicate"):
        _request(config, [first, second])

    with pytest.raises(IdentityContractError, match="injection_order values"):
        _request(
            config,
            [
                _raw(first, b"first-content"),
                _raw(second, b"second-content"),
            ],
            resolved=replace(
                _resolved_inputs(),
                injection_order={"sample_a": 1, "sample_b": 1},
            ),
        )

    with pytest.raises(IdentityContractError, match="unable to hash"):
        _source_identity(tmp_path / "missing.raw")

    approval = _approval()
    with pytest.raises(IdentityContractError, match="approval mapping key"):
        _request(
            config,
            [first],
            resolved=ResolvedTargetedInputs(
                injection_order={"sample_a": 1},
                rt_prior_library={},
                model_selection_expected_diff_approvals={"wrong-key": approval},
            ),
        )

    source = _source_identity(first)
    with pytest.raises(IdentityContractError, match="rebind"):
        replace(source, byte_size=source.byte_size + 1)

    request = _request(
        config,
        [first],
        resolved=ResolvedTargetedInputs(
            injection_order={"sample_a": 1},
            rt_prior_library={},
            model_selection_expected_diff_approvals=None,
        ),
    )
    with pytest.raises(IdentityContractError, match="rebind"):
        replace(request, normalized_scientific_inputs_json='{"changed":true}')


def test_retry_issues_new_attempt_without_mutating_request_or_predecessor(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample_a.raw", b"source-a")
    request = _request(
        _config(tmp_path),
        [path],
        resolved=ResolvedTargetedInputs(
            injection_order={"sample_a": 1},
            rt_prior_library={},
            model_selection_expected_diff_approvals=None,
        ),
    )
    first_attempt = issue_processing_attempt(request)
    retry = issue_processing_attempt(request, retry_of=first_attempt)

    assert retry.processing_attempt_id != first_attempt.processing_attempt_id
    assert retry.processing_request_id == first_attempt.processing_request_id
    assert retry.retry_of_attempt_id == first_attempt.processing_attempt_id
    assert retry.retry_of is first_attempt
    assert first_attempt.retry_of_attempt_id is None
    assert {field.name for field in fields(first_attempt)} == {
        "processing_attempt_id",
        "processing_request_id",
        "attempt_token",
        "retry_of",
    }

    changed_request = _request(
        replace(_config(tmp_path), peak_rel_height=0.03),
        [path],
        resolved=ResolvedTargetedInputs(
            injection_order={"sample_a": 1},
            rt_prior_library={},
            model_selection_expected_diff_approvals=None,
        ),
    )
    with pytest.raises(IdentityContractError, match="different processing request"):
        issue_processing_attempt(changed_request, retry_of=first_attempt)

    foreign_attempt = issue_processing_attempt(changed_request)
    with pytest.raises(IdentityContractError, match="different processing request"):
        replace(retry, retry_of=foreign_attempt)

    assert request.processing_algorithm_version == (
        TARGETED_PROCESSING_ALGORITHM_VERSION
    )
    assert request.processing_contract_version == TARGETED_PROCESSING_CONTRACT_VERSION


def test_processing_identity_owner_has_no_runtime_writer_or_raw_dependency() -> None:
    path = Path("xic_extractor/processing_identity.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = _imports(tree)
    forbidden = (
        "gui",
        "scripts",
        "xic_extractor.extraction",
        "xic_extractor.output",
        "xic_extractor.raw_reader",
    )

    assert not any(name.startswith(forbidden) for name in imports), imports


def test_targeted_observation_namespace_tracks_raw_adapter_controller() -> None:
    raw_reader_source = Path("xic_extractor/raw_reader.py").read_text(encoding="utf-8")

    assert "SelectInstrument(thermo.device_ms, 1)" in raw_reader_source
    assert THERMO_RAW_OBSERVATION_NAMESPACE == ("thermo_raw_file_reader:Device.MS:1")


def _request(
    config: ExtractionConfig,
    paths: list[Path],
    *,
    resolved: ResolvedTargetedInputs | None = None,
    targets: tuple[Target, ...] | None = None,
):
    return build_targeted_processing_request_identity(
        config,
        target_snapshot=mint_target_list_snapshot(
            targets or (_target(),),
            identity_namespace="target-list:package-0-test",
        ),
        raw_paths=paths,
        resolved_inputs=resolved or _resolved_inputs(),
    )


def _source_identity(path: Path):
    return build_acquisition_source_identity(
        path,
        acquisition_format="thermo_raw",
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )


def _raw(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def _resolved_inputs() -> ResolvedTargetedInputs:
    return ResolvedTargetedInputs(
        injection_order={"sample_a": 1, "sample_b": 2},
        rt_prior_library={
            ("Analyte", "analyte"): LibraryEntry(
                config_hash="legacy-fragment",
                target_label="Analyte",
                role="analyte",
                istd_pair="",
                median_delta_rt=None,
                sigma_delta_rt=None,
                median_abs_rt=1.5,
                sigma_abs_rt=0.2,
                n_samples=8,
                updated_at="2026-07-25T00:00:00Z",
            )
        },
        model_selection_expected_diff_approvals={"approval-1": _approval()},
    )


def _approval() -> ExpectedDiffApprovalRecord:
    return ExpectedDiffApprovalRecord(
        stable_row_id="approval-1",
        sample_name="sample_a",
        target_label="Analyte",
        legacy_selected_candidate_id="legacy",
        successor_selected_candidate_id="successor",
        public_outputs_touched=("selected marker",),
        matrix_value_impact="none",
        evidence_sources=("fixture",),
        evidence_summary="reviewed evidence",
        validation_tier="synthetic_fixture",
        reviewer_role="reviewer",
        reviewer_verdict="approved",
        final_label="expected_diff",
    )


def _config(tmp_path: Path) -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path / "dll",
        output_csv=tmp_path / "output" / "xic_results.csv",
        diagnostics_csv=tmp_path / "output" / "xic_diagnostics.csv",
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )


def _imports(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)
