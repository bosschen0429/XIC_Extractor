import json
from dataclasses import fields, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

import xic_extractor.processing_identity as processing_identity_module
from tests.raw_header_fakes import install_fake_raw_headers
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.target_identity import mint_target_list_snapshot
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.extraction.targeted_inputs import (
    TARGETED_PROCESSING_ALGORITHM_VERSION,
    TARGETED_PROCESSING_CONTRACT_VERSION,
    TARGETED_REAL_DATA_DISCOVERY_SETTINGS_INPUT_KEY,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    ResolvedTargetedInputs,
    build_targeted_processing_request_identity,
    resolve_targeted_inputs,
    resolve_targeted_raw_paths,
)
from xic_extractor.injection_timeline import InjectionHeader
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    AcquisitionSourceIdentity,
    FingerprintCancelledError,
    IdentityContractError,
    build_acquisition_source_identity,
    build_processing_request_identity,
    fingerprint_file_content,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    infer_sample_role,
)


@pytest.fixture(autouse=True)
def _placeholder_raw_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    # RAW files here are placeholders; order them by their file times.
    install_fake_raw_headers(monkeypatch)


def test_request_differs_by_algorithm_and_attempt_uses_uuid4(monkeypatch) -> None:
    source = AcquisitionSourceIdentity(
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
        scientific_input_schema_version="golden_inputs",
        processing_algorithm_version="golden_algorithm",
        processing_contract_version="golden_contract",
        scientific_inputs={"z": [2, 1], "a": "é"},
    )

    changed_algorithm = build_processing_request_identity(
        acquisition_sources=request.acquisition_sources,
        acquisition_bindings=request.acquisition_bindings,
        scientific_input_schema_version=request.scientific_input_schema_version,
        processing_algorithm_version="alternative_algorithm",
        processing_contract_version=request.processing_contract_version,
        scientific_inputs={"z": [2, 1], "a": "é"},
    )
    assert changed_algorithm != request

    monkeypatch.setattr(
        "xic_extractor.processing_identity.uuid.uuid4",
        lambda: UUID("00112233-4455-6677-8899-aabbccddeeff"),
    )
    attempt = issue_processing_attempt()
    assert attempt.processing_attempt_id == "00112233-4455-6677-8899-aabbccddeeff"


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


def test_acquisition_source_identity_changes_on_substitution(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"first-content")
    first = _source_identity(path)

    path.write_bytes(b"mutated-content")
    mutated = _source_identity(path)

    assert mutated.acquisition_source_id != first.acquisition_source_id
    assert mutated.content_sha256 != first.content_sha256


def test_fingerprint_observer_reports_measured_calls_bytes_and_time(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"measured-content")
    observations: list[dict[str, object]] = []

    fingerprint = fingerprint_file_content(path, observation=observations.append)

    assert fingerprint.byte_size == len(b"measured-content")
    assert observations == [
        {
            "metric": "fingerprint",
            "scope": str(path),
            "calls": 1,
            "bytes": len(b"measured-content"),
            "elapsed_sec": observations[0]["elapsed_sec"],
            "availability": "measured",
            "reason": "",
        }
    ]
    assert isinstance(observations[0]["elapsed_sec"], float)


def test_fingerprint_without_observer_skips_diagnostic_timer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"content")
    monkeypatch.setattr(
        "xic_extractor.processing_identity.perf_counter",
        lambda: (_ for _ in ()).throw(AssertionError("timer must stay opt-in")),
    )

    fingerprint = fingerprint_file_content(path)

    assert fingerprint.byte_size == len(b"content")


def test_fingerprint_cancellation_is_checked_between_chunks(tmp_path: Path) -> None:
    path = _raw(tmp_path / "sample.raw", b"x" * (2 * 1024 * 1024))

    with pytest.raises(FingerprintCancelledError, match="was cancelled"):
        fingerprint_file_content(path, should_cancel=lambda: True)


@pytest.mark.parametrize("path_kind", ["invalid", "missing", "directory"])
def test_fingerprint_observer_reports_each_pre_read_failure_once(
    tmp_path: Path,
    path_kind: str,
) -> None:
    if path_kind == "invalid":
        path = None
    elif path_kind == "missing":
        path = tmp_path / "missing.raw"
    else:
        path = tmp_path / "directory.raw"
        path.mkdir()
    observations: list[dict[str, object]] = []

    with pytest.raises(IdentityContractError):
        fingerprint_file_content(  # type: ignore[arg-type]
            path,
            observation=observations.append,
        )

    assert len(observations) == 1
    assert observations[0]["availability"] == "failed"
    assert observations[0]["calls"] == 1
    assert observations[0]["bytes"] in {None, 0}


def test_fingerprint_observer_failure_does_not_change_identity_semantics(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"stable-content")

    fingerprint = fingerprint_file_content(
        path,
        observation=lambda _event: (_ for _ in ()).throw(RuntimeError("diagnostic")),
    )

    assert fingerprint.byte_size == len(b"stable-content")


def test_source_path_replacement_during_hash_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"original-content")
    original_stat = Path.stat

    class DifferentFileIdentity:
        def __init__(self, observed) -> None:
            self.st_dev = observed.st_dev
            self.st_ino = observed.st_ino + 1

    def replaced_path_stat(self, *args, **kwargs):
        observed = original_stat(self, *args, **kwargs)
        if self == path:
            return DifferentFileIdentity(observed)
        return observed

    monkeypatch.setattr(Path, "stat", replaced_path_stat)
    observations: list[dict[str, object]] = []

    with pytest.raises(IdentityContractError, match="replaced"):
        fingerprint_file_content(path, observation=observations.append)

    assert len(observations) == 1
    assert observations[0]["availability"] == "failed"
    assert observations[0]["bytes"] == len(b"original-content")
    assert "replaced" in str(observations[0]["reason"])


def test_source_handle_mutation_reports_failed_fingerprint_observation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"original-content")
    original_fstat = processing_identity_module.os.fstat
    calls = 0

    def changed_fstat(fd):
        nonlocal calls
        observed = original_fstat(fd)
        calls += 1
        if calls == 1:
            return observed
        values = {
            field: getattr(observed, field)
            for field in (
                "st_mode",
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        values["st_mtime_ns"] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(processing_identity_module.os, "fstat", changed_fstat)
    observations: list[dict[str, object]] = []

    with pytest.raises(IdentityContractError, match="mutated"):
        fingerprint_file_content(path, observation=observations.append)

    assert len(observations) == 1
    assert observations[0]["availability"] == "failed"
    assert observations[0]["bytes"] == len(b"original-content")
    assert "mutated" in str(observations[0]["reason"])


def test_source_size_drift_reports_failed_fingerprint_observation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"original-content")
    original_fstat = processing_identity_module.os.fstat

    def wrong_size_fstat(fd):
        observed = original_fstat(fd)
        return SimpleNamespace(
            st_mode=observed.st_mode,
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_size=observed.st_size + 1,
            st_mtime_ns=observed.st_mtime_ns,
            st_ctime_ns=observed.st_ctime_ns,
        )

    monkeypatch.setattr(processing_identity_module.os, "fstat", wrong_size_fstat)
    observations: list[dict[str, object]] = []

    with pytest.raises(IdentityContractError, match="byte size changed"):
        fingerprint_file_content(path, observation=observations.append)

    assert len(observations) == 1
    assert observations[0]["availability"] == "failed"
    assert observations[0]["bytes"] == len(b"original-content")
    assert "byte size changed" in str(observations[0]["reason"])


def test_source_identity_allows_path_stat_metadata_lag_for_same_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _raw(tmp_path / "sample.raw", b"stable-content")
    original_stat = Path.stat

    class SameFileWithLaggingMetadata:
        def __init__(self, observed) -> None:
            self.st_dev = observed.st_dev
            self.st_ino = observed.st_ino
            self.st_size = observed.st_size
            self.st_mtime_ns = observed.st_mtime_ns + 1
            self.st_ctime_ns = observed.st_ctime_ns + 1

    def lagging_stat(self, *args, **kwargs):
        observed = original_stat(self, *args, **kwargs)
        if self == path:
            return SameFileWithLaggingMetadata(observed)
        return observed

    monkeypatch.setattr(Path, "stat", lagging_stat)

    identity = _source_identity(path)

    assert identity.byte_size == len(b"stable-content")


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

    assert reversed_order != forward


def test_real_data_discovery_settings_change_processing_request_identity(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample_a.raw", b"source-a")
    resolved = ResolvedTargetedInputs(
        injection_order={"sample_a": 1},
    )
    settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474),
        product_search_ppm=50.0,
    )
    changed_settings = DiscoverySettings(
        neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474),
        product_search_ppm=55.0,
    )

    first = _request(
        _config(tmp_path),
        [path],
        resolved=resolved,
        current_discovery_settings=settings,
    )
    changed = _request(
        _config(tmp_path),
        [path],
        resolved=resolved,
        current_discovery_settings=changed_settings,
    )

    assert changed != first
    request_inputs = json.loads(first.normalized_scientific_inputs_json)
    snapshot = request_inputs[TARGETED_REAL_DATA_DISCOVERY_SETTINGS_INPUT_KEY]
    assert snapshot["settings"]["product_search_ppm"] == 50.0
    assert set(snapshot["settings"]) == {
        field.name for field in fields(DiscoverySettings)
    }


def test_adapter_input_key_change_invalidates_request_not_source_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_path = _raw(tmp_path / "sample_a.raw", b"source-a")
    source = _source_identity(original_path)
    # Isolate adapter binding from file revision: a POSIX rename changes ctime.
    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_inputs.build_local_acquisition_source_identity",
        lambda *_args, **_kwargs: source,
    )
    config = _config(tmp_path)
    original = _request(
        config,
        [original_path],
        resolved=ResolvedTargetedInputs(
            injection_order={"sample_a": 1},
        ),
    )
    renamed_path = original_path.rename(tmp_path / "renamed_sample.raw")
    renamed = _request(
        config,
        [renamed_path],
        resolved=ResolvedTargetedInputs(
            injection_order={"renamed_sample": 1},
        ),
    )

    assert original.acquisition_sources == renamed.acquisition_sources
    assert original != renamed


def test_targeted_request_uses_local_revision_without_reading_raw_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _raw(tmp_path / "sample_a.raw", b"source-a")

    original_open = Path.open

    def reject_content_read(candidate, *args, **kwargs):
        if candidate == path:
            pytest.fail("Normal Targeted preparation must not read full RAW content")
        return original_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", reject_content_read)
    request = _request(
        _config(tmp_path),
        [path],
        resolved=ResolvedTargetedInputs(injection_order={"sample_a": 1}),
    )
    source = request.acquisition_sources[0]
    state = path.stat()
    assert source.content_sha256 is None
    assert source.local_file_revision == (
        state.st_dev,
        state.st_ino,
        state.st_size,
        state.st_mtime_ns,
        state.st_ctime_ns,
    )


def test_request_invalidates_changed_source_or_target_window(
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
    rt_changed = _request(
        config,
        paths,
        resolved=inputs,
        targets=(replace(_target(), rt_max=2.5),),
    )

    changed_ids = {
        substituted,
        target_changed,
        rt_changed,
    }
    assert baseline not in changed_ids
    assert len(changed_ids) == 3


def test_request_pins_owner_target_snapshot_order(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first_target = _target()
    second_target = replace(_target(), label="Other", mz=300.0)
    raw_path = _raw(tmp_path / "sample_a.raw", b"source-a")
    resolved = ResolvedTargetedInputs(
        injection_order={"sample_a": 1},
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

    assert reordered != forward
    assert "target_list_snapshot" in forward.normalized_scientific_inputs_json


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
        ),
        paths,
    )

    assert execution_changed == baseline


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
    original_id = request

    inputs.injection_order["sample_a"] = 9

    assert request == original_id
    assert _request(config, paths, resolved=inputs) != original_id


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

    # Byte-identical copies remain different local files without a content audit.
    request = _request(config, [first, second])
    assert (
        len({source.acquisition_source_id for source in request.acquisition_sources})
        == 2
    )

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

    request = _request(
        config,
        [first],
        resolved=ResolvedTargetedInputs(
            injection_order={"sample_a": 1},
        ),
    )


def test_request_rejects_duplicate_adapter_input_keys(tmp_path: Path) -> None:
    first = _source_identity(_raw(tmp_path / "first.raw", b"first"))
    second = _source_identity(_raw(tmp_path / "second.raw", b"second"))

    with pytest.raises(IdentityContractError, match="adapter_input_key.*unique"):
        build_processing_request_identity(
            acquisition_sources=(first, second),
            acquisition_bindings=(
                AcquisitionInputBinding(
                    acquisition_source_id=first.acquisition_source_id,
                    adapter_input_key="shared",
                    injection_order=1,
                ),
                AcquisitionInputBinding(
                    acquisition_source_id=second.acquisition_source_id,
                    adapter_input_key="shared",
                    injection_order=2,
                ),
            ),
            scientific_input_schema_version="fixture",
            processing_algorithm_version="fixture",
            processing_contract_version="fixture",
            scientific_inputs={"fixture": True},
        )


@pytest.mark.parametrize(
    "scientific_inputs",
    [
        {"nested": {1: "integer-key"}},  # type: ignore[dict-item]
        {"nested": [{1: "integer-key"}]},  # type: ignore[dict-item]
        {"nested": ({1: "integer-key"},)},  # type: ignore[dict-item]
    ],
)
def test_request_rejects_nested_non_string_json_keys(
    tmp_path: Path,
    scientific_inputs: dict[str, object],
) -> None:
    source = _source_identity(_raw(tmp_path / "source.raw", b"source"))

    with pytest.raises(IdentityContractError, match="JSON object keys must be strings"):
        build_processing_request_identity(
            acquisition_sources=(source,),
            acquisition_bindings=(
                AcquisitionInputBinding(
                    acquisition_source_id=source.acquisition_source_id,
                    adapter_input_key="source",
                    injection_order=1,
                ),
            ),
            scientific_input_schema_version="fixture",
            processing_algorithm_version="fixture",
            processing_contract_version="fixture",
            scientific_inputs=scientific_inputs,
        )


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_request_rejects_non_finite_json_numbers(
    tmp_path: Path,
    non_finite: float,
) -> None:
    source = _source_identity(_raw(tmp_path / "source.raw", b"source"))

    with pytest.raises(IdentityContractError, match="not canonical JSON"):
        build_processing_request_identity(
            acquisition_sources=(source,),
            acquisition_bindings=(
                AcquisitionInputBinding(
                    acquisition_source_id=source.acquisition_source_id,
                    adapter_input_key="source",
                    injection_order=1,
                ),
            ),
            scientific_input_schema_version="fixture",
            processing_algorithm_version="fixture",
            processing_contract_version="fixture",
            scientific_inputs={"nested": {"value": non_finite}},
        )


def test_sample_metadata_alias_projection_builds_targeted_request(
    tmp_path: Path,
) -> None:
    raw_path = _raw(
        tmp_path / "Breast_Cancer_Tissue_pooled_QC1.raw",
        b"qc-source",
    )
    metadata_path = tmp_path / "sample_metadata.tsv"
    row = {column: "" for column in SAMPLE_METADATA_COLUMNS}
    row.update(
        {
            "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            "sample_name": "QC1",
            "raw_stem": raw_path.stem,
            "injection_order": "1",
            "sample_role": "pooled_qc",
        }
    )
    metadata_path.write_text(
        "\t".join(SAMPLE_METADATA_COLUMNS)
        + "\n"
        + "\t".join(row[column] for column in SAMPLE_METADATA_COLUMNS)
        + "\n",
        encoding="utf-8",
    )
    config = replace(_config(tmp_path), injection_order_source=metadata_path)
    resolved = resolve_targeted_inputs(
        config,
        raw_paths=[raw_path],
        injection_order=None,
    )

    request = _request(config, [raw_path], resolved=resolved)

    assert resolved.injection_order == {raw_path.stem: 1}
    assert request.acquisition_bindings[0].adapter_input_key == raw_path.stem


def test_targeted_and_untargeted_read_one_sequence_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_sample_metadata_sequence_parsing import _create_synthetic_docx
    from xic_extractor import raw_reader
    from xic_workbench.samples import load_samples

    # The Word sequence numbers Blank 1 ... Normal 4; the RAW clock disagrees.
    clock = {
        "NormalBC001_DNA": 0,
        "TumorBC001_DNA": 10,
        "QC_Pool_1": 20,
        "Blank_1": 30,
        "Older_batch_S1": -60,
    }
    for stem in clock:
        _raw(tmp_path / f"{stem}.raw", stem.encode())

    def analysis_header(path: Path, _dll: Path):
        header = InjectionHeader(
            raw_path=str(path.resolve()),
            acquisition_source=f"D:/acquired/{path.name}",
            creation_date=datetime(2026, 1, 1, 12)
            + timedelta(minutes=clock[path.stem]),
            start_time_min=0.0,
            end_time_min=5.0,
            instrument_method="",
        )
        return header, infer_sample_role(None, sample_name=path.stem), path.stem

    monkeypatch.setattr(raw_reader, "_analysis_header", analysis_header)
    sequence = tmp_path / "sequence.docx"
    _create_synthetic_docx(sequence)
    config = replace(_config(tmp_path), injection_order_source=sequence)

    raw_paths = resolve_targeted_raw_paths(config)
    targeted = resolve_targeted_inputs(
        config, raw_paths=raw_paths, injection_order=None
    )
    untargeted = {
        row["raw_stem"]: row
        for row in load_samples(tmp_path, dll_dir=tmp_path, sequence_source=sequence)[
            "samples"
        ]
    }

    assert targeted.injection_order == {
        "Older_batch_S1": 1,
        "NormalBC001_DNA": 2,
        "TumorBC001_DNA": 3,
        "QC_Pool_1": 4,
    }
    for row in targeted.sample_metadata:
        assert (row.injection_order, row.sample_role) == (
            untargeted[row.raw_stem]["injection_order"],
            untargeted[row.raw_stem]["sample_role"],
        )
    assert untargeted["Blank_1"]["sample_role"] == "blank"


def test_retry_issues_new_attempt_without_mutating_request_or_predecessor(
    tmp_path: Path,
) -> None:
    path = _raw(tmp_path / "sample_a.raw", b"source-a")
    request = _request(
        _config(tmp_path),
        [path],
        resolved=ResolvedTargetedInputs(
            injection_order={"sample_a": 1},
        ),
    )
    first_attempt = issue_processing_attempt()
    retry = issue_processing_attempt(retry_of=first_attempt)

    assert retry.processing_attempt_id != first_attempt.processing_attempt_id
    assert retry.retry_of is first_attempt
    assert {field.name for field in fields(first_attempt)} == {
        "processing_attempt_id",
        "retry_of",
    }

    assert request.processing_algorithm_version == (
        TARGETED_PROCESSING_ALGORITHM_VERSION
    )
    assert request.processing_contract_version == TARGETED_PROCESSING_CONTRACT_VERSION


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
    current_discovery_settings: DiscoverySettings | None = None,
):
    return build_targeted_processing_request_identity(
        config,
        target_snapshot=mint_target_list_snapshot(
            targets or (_target(),),
            identity_namespace="target-list:package-0-test",
        ),
        raw_paths=paths,
        resolved_inputs=resolved or _resolved_inputs(),
        current_discovery_settings=current_discovery_settings,
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


def test_targeted_formal_request_binds_strategy_and_shared_mass_policy(
    tmp_path, monkeypatch
):
    from xic_extractor.configuration import targeted_strategy

    paths = [
        _raw(tmp_path / "sample_a.raw", b"a"),
        _raw(tmp_path / "sample_b.raw", b"b"),
    ]
    config = _config(tmp_path)
    baseline = _request(config, paths)
    receipt = json.loads(baseline.normalized_scientific_inputs_json)["strategy"]
    assert receipt["candidate_coverage"] == "complete_acquisition_exact_mass"
    assert (
        receipt["uncalibrated_paired_istd_position"]
        == "qualified_identity_and_coelution_rejection"
    )
    assert set(receipt["cid_nl_recipes"]) == {"dR", "R", "MeR"}
    monkeypatch.setattr(
        targeted_strategy, "TARGETED_PRECURSOR_REFERENCE", "independent_mass"
    )
    assert _request(config, paths) != baseline
    monkeypatch.undo()
    original = targeted_strategy.targeted_tag_profile
    monkeypatch.setattr(
        targeted_strategy,
        "targeted_tag_profile",
        lambda tag: replace(original(tag), product_search_ppm=51.0),
    )
    assert _request(config, paths) != baseline
