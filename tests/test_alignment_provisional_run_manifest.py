from __future__ import annotations

import copy
import csv
import hashlib
import itertools
import json
import os
import shutil
from pathlib import Path

import pytest

import xic_extractor.alignment.artifact_write_receipts as alignment_receipt_owner
import xic_extractor.extraction.provisional_run_manifest as manifest_owner
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.csv_writer import write_discovery_candidates_csv
from xic_extractor.discovery.models import DiscoverySettings, NeutralLossProfile
from xic_extractor.extraction.provisional_run_manifest import (
    PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION,
    ProvisionalRunRuntimeConfiguration,
    publish_provisional_run_bundle_atomic,
    validate_provisional_run_manifest,
    write_provisional_run_manifest,
)
from xic_extractor.processing_identity import (
    build_acquisition_source_identity,
    fingerprint_file_content,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
)


def test_manifest_binds_effective_configuration_receipts_and_artifact_bytes(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")

    payload = validate_provisional_run_manifest(
        packet["manifest"],
        root=packet["root"],
    )

    assert payload["code_revision"] == "a" * 40
    assert payload["schema_version"] == PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION
    assert payload["preset_name"] == "dna_dr"
    assert payload["effective_configuration"]["discovery"][
        "neutral_loss_profiles"
    ][0]["tag"] == "DNA_dR"
    assert payload["runtime"]["owner_build_xic_backend"] == "raw_superwindow"
    assert payload["runtime"]["selected_family_source"] == ""
    assert payload["runtime"]["output_level"] == "validation-minimal"
    assert payload["runtime"]["audit_evidence_mode"] == "none"
    assert payload["runtime"]["sample_column_order_source"] == (
        "retained_sample_metadata"
    )
    assert payload["claim_levels"] == {
        "artifact_bytes": "byte_bound",
        "configuration": "structure_bound",
        "embedded_semantics": "unavailable",
    }
    assert payload["authenticity_authority"] is False
    assert payload["semantic_replay_authority"] is False
    assert payload["inputs"]["discovery_batch_index"]["receipt_count"] == 1
    assert payload["artifacts"]["provisional_matrix"]["byte_size"] == packet[
        "artifacts"
    ]["provisional_matrix"].stat().st_size


def test_manifest_rejects_artifact_mutation(tmp_path: Path) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    packet["artifacts"]["provisional_matrix"].write_text(
        "mutated\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bytes do not match"):
        validate_provisional_run_manifest(
            packet["manifest"],
            root=packet["root"],
        )


def test_manifest_rejects_unresealed_cross_run_artifact_substitution(
    tmp_path: Path,
) -> None:
    first = _packet(tmp_path / "first", matrix_bytes="matrix-a\n")
    second = _packet(tmp_path / "second", matrix_bytes="matrix-b\n")
    shutil.copyfile(
        second["artifacts"]["provisional_matrix"],
        first["artifacts"]["provisional_matrix"],
    )

    with pytest.raises(ValueError, match="bytes do not match"):
        validate_provisional_run_manifest(
            first["manifest"],
            root=first["root"],
        )


def test_manifest_does_not_overclaim_resealed_artifact_semantics(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    matrix = packet["artifacts"]["provisional_matrix"]
    matrix.write_text("internally-inconsistent-but-byte-bound\n", encoding="utf-8")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    binding = payload["artifacts"]["provisional_matrix"]
    binding["sha256"] = hashlib.sha256(matrix.read_bytes()).hexdigest()
    binding["byte_size"] = matrix.stat().st_size
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validated = validate_provisional_run_manifest(
        packet["manifest"],
        root=packet["root"],
    )

    assert validated["claim_levels"]["embedded_semantics"] == "unavailable"


def test_manifest_rejects_incomplete_runtime_structure(tmp_path: Path) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    del payload["runtime"]["raw_workers"]
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime field set is invalid"):
        validate_provisional_run_manifest(
            packet["manifest"],
            root=packet["root"],
        )


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    manifest_text = packet["manifest"].read_text(encoding="utf-8")
    packet["manifest"].write_text(
        manifest_text.replace(
            '  "product_authority": false,',
            '  "product_authority": true,\n  "product_authority": false,',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key: product_authority"):
        validate_provisional_run_manifest(
            packet["manifest"],
            root=packet["root"],
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload.__setitem__("unexpected", True),
            "manifest field set is invalid",
        ),
        (
            lambda payload: payload["artifacts"]["provisional_matrix"].__setitem__(
                "unexpected", True
            ),
            "artifact binding field set is invalid",
        ),
    ],
)
def test_manifest_rejects_unknown_fields(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    mutate(payload)
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        validate_provisional_run_manifest(
            packet["manifest"],
            root=packet["root"],
        )


def test_manifest_runtime_is_structure_bound_not_execution_replay(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    payload["runtime"]["raw_workers"] = 4
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validated = validate_provisional_run_manifest(
        packet["manifest"],
        root=packet["root"],
    )

    assert validated["runtime"]["raw_workers"] == 4
    assert validated["claim_levels"]["configuration"] == "structure_bound"


def test_manifest_configuration_values_are_structure_bound(tmp_path: Path) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    payload["effective_configuration"]["discovery"]["nl_tolerance_ppm"] = 25.0
    payload["effective_configuration"]["alignment"]["preferred_ppm"] = 25.0
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validated = validate_provisional_run_manifest(
        packet["manifest"],
        root=packet["root"],
    )

    assert validated["effective_configuration"]["discovery"][
        "nl_tolerance_ppm"
    ] == 25.0
    assert validated["effective_configuration"]["alignment"][
        "preferred_ppm"
    ] == 25.0


def test_manifest_rejects_incomplete_configuration_structure(
    tmp_path: Path,
) -> None:
    packet = _packet(tmp_path / "run", matrix_bytes="matrix-a\n")
    payload = json.loads(packet["manifest"].read_text(encoding="utf-8"))
    del payload["effective_configuration"]["discovery"]["nl_tolerance_ppm"]
    packet["manifest"].write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="field set is invalid"):
        validate_provisional_run_manifest(
            packet["manifest"],
            root=packet["root"],
        )


def test_manifest_creation_rejects_non_owner_effective_configuration(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="does not match the preset owner"):
        _packet(
            tmp_path / "run",
            matrix_bytes="matrix-a\n",
            discovery_settings=DiscoverySettings(
                nl_tolerance_ppm=25.0,
                neutral_loss_profiles=(
                    NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0474),
                ),
            ),
        )


def test_manifest_bound_atomic_publish_reuses_owner_fingerprints_without_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    original_fingerprint = manifest_owner.fingerprint_file_content

    def counted_fingerprint(path, **kwargs):
        fingerprint = original_fingerprint(path, **kwargs)
        calls.append((Path(path).name, fingerprint.byte_size))
        return fingerprint

    monkeypatch.setattr(
        manifest_owner,
        "fingerprint_file_content",
        counted_fingerprint,
    )
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    baseline_calls = tuple(calls)
    assert len(baseline_calls) == 12

    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    publication_arguments = _publication_arguments(staging)
    calls.clear()
    published_manifest = publish_provisional_run_bundle_atomic(
        staging,
        tmp_path / "candidate",
        **publication_arguments,
    )
    candidate_calls = tuple(calls)

    assert len(candidate_calls) == 7
    assert [name for name, _size in candidate_calls].count(
        "discovery_batch_index.csv"
    ) == 2
    discovery_size = next(
        size
        for name, size in candidate_calls
        if name == "discovery_batch_index.csv"
    )
    assert sum(size for _name, size in baseline_calls) == 2 * (
        sum(size for _name, size in candidate_calls) - discovery_size
    )
    assert not staging.exists()
    assert published_manifest == tmp_path / "candidate" / "run_manifest.json"
    assert published_manifest.read_bytes() == baseline["manifest"].read_bytes()
    for relative_path in _publication_relative_paths():
        assert (tmp_path / "candidate" / relative_path).read_bytes() == (
            baseline["root"] / relative_path
        ).read_bytes()

    calls.clear()
    validate_provisional_run_manifest(
        published_manifest,
        root=tmp_path / "candidate",
    )
    assert len(calls) == 6


def test_manifest_bound_atomic_publish_rejects_post_fingerprint_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    output_dir = tmp_path / "candidate"
    original_write = manifest_owner._write_json_atomic

    def mutate_after_manifest_write(path, payload):
        original_write(path, payload)
        (staging / _publication_relative_paths()[0]).write_text(
            "mutated-after-fingerprint\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        manifest_owner,
        "_write_json_atomic",
        mutate_after_manifest_write,
    )

    with pytest.raises(ValueError, match="bytes changed before publication"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **_publication_arguments(staging),
        )

    assert staging.is_dir()
    assert not output_dir.exists()


def test_manifest_bound_atomic_publish_rejects_valid_manifest_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    output_dir = tmp_path / "candidate"
    original_write = manifest_owner._write_json_atomic

    def substitute_valid_manifest(path, payload):
        substituted = dict(payload)
        substituted["code_revision"] = "f" * 40
        original_write(path, substituted)

    monkeypatch.setattr(
        manifest_owner,
        "_write_json_atomic",
        substitute_valid_manifest,
    )

    with pytest.raises(ValueError, match="owner serialization"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **_publication_arguments(staging),
        )

    assert staging.is_dir()
    assert not output_dir.exists()


def test_manifest_bound_atomic_publish_rejects_same_size_restored_mtime_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    output_dir = tmp_path / "candidate"
    target = staging / _publication_relative_paths()[0]
    original_stat = target.stat()
    original_write = manifest_owner._write_json_atomic
    mutation_applied = False

    def mutate_with_restored_mtime(path, payload):
        nonlocal mutation_applied
        original_write(path, payload)
        original_bytes = target.read_bytes()
        replacement = bytes([original_bytes[0] ^ 1]) + original_bytes[1:]
        target.write_bytes(replacement)
        os.utime(
            target,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        mutation_applied = True

    monkeypatch.setattr(
        manifest_owner,
        "_write_json_atomic",
        mutate_with_restored_mtime,
    )

    with pytest.raises(ValueError, match="bytes changed before publication"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **_publication_arguments(staging),
        )

    assert mutation_applied
    assert target.stat().st_size == original_stat.st_size
    assert target.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert staging.is_dir()
    assert not output_dir.exists()


def test_manifest_bound_atomic_publish_failure_never_exposes_final_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    output_dir = tmp_path / "candidate"

    monkeypatch.setattr(
        manifest_owner,
        "_write_json_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected manifest write failure")
        ),
    )
    with pytest.raises(OSError, match="manifest write failure"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **_publication_arguments(staging),
        )
    assert staging.is_dir()
    assert not output_dir.exists()

    monkeypatch.undo()
    original_replace = Path.replace

    def fail_directory_replace(path: Path, target: Path) -> Path:
        if path == staging:
            raise PermissionError("injected directory replace failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_directory_replace)
    monkeypatch.setattr(manifest_owner.time, "sleep", lambda _seconds: None)
    with pytest.raises(PermissionError, match="directory replace failure"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **_publication_arguments(staging),
        )
    assert staging.is_dir()
    assert not output_dir.exists()


def test_manifest_bound_atomic_publish_rejects_manifest_mutation_between_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    output_dir = tmp_path / "candidate"
    original_replace = Path.replace
    publication_attempts = 0

    def mutate_manifest_then_block(path: Path, target: Path) -> Path:
        nonlocal publication_attempts
        if path == staging:
            publication_attempts += 1
            if publication_attempts == 1:
                (staging / "run_manifest.json").write_text(
                    '{"tampered": true}\n',
                    encoding="utf-8",
                )
                raise PermissionError("injected transient directory lock")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", mutate_manifest_then_block)
    monkeypatch.setattr(manifest_owner.time, "sleep", lambda _seconds: None)

    with pytest.raises(ValueError, match="run manifest bytes changed"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **_publication_arguments(staging),
        )

    assert publication_attempts == 1
    assert staging.is_dir()
    assert not output_dir.exists()


def test_manifest_bound_atomic_publish_rehashes_artifacts_between_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    output_dir = tmp_path / "candidate"
    target = staging / _publication_relative_paths()[0]
    target_stat = target.stat()
    original_replace = Path.replace
    publication_attempts = 0

    def mutate_artifact_then_block(path: Path, target_path: Path) -> Path:
        nonlocal publication_attempts
        if path == staging:
            publication_attempts += 1
            if publication_attempts == 1:
                original_bytes = target.read_bytes()
                replacement = bytes([original_bytes[0] ^ 1]) + original_bytes[1:]
                target.write_bytes(replacement)
                os.utime(
                    target,
                    ns=(target_stat.st_atime_ns, target_stat.st_mtime_ns),
                )
                raise PermissionError("injected transient directory lock")
        return original_replace(path, target_path)

    monkeypatch.setattr(Path, "replace", mutate_artifact_then_block)
    monkeypatch.setattr(manifest_owner.time, "sleep", lambda _seconds: None)

    with pytest.raises(ValueError, match="bytes changed before publication"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **_publication_arguments(staging),
        )

    assert publication_attempts == 1
    assert target.stat().st_size == target_stat.st_size
    assert target.stat().st_mtime_ns == target_stat.st_mtime_ns
    assert staging.is_dir()
    assert not output_dir.exists()


def test_manifest_fingerprint_registry_rejects_foreign_duplicate_and_missing(
    tmp_path: Path,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    transaction_id = "a" * 32
    build = manifest_owner._build_manifest(
        root=staging,
        transaction_id=transaction_id,
        **_publication_arguments(staging),
    )

    foreign = (
        _remint_fingerprint(
            build.fingerprints[0],
            transaction_id="b" * 32,
        ),
        *build.fingerprints[1:],
    )
    with pytest.raises(ValueError, match="foreign transaction"):
        manifest_owner._OwnerFingerprintRegistry(
            foreign,
            transaction_id=transaction_id,
        )
    with pytest.raises(ValueError, match="role is duplicated"):
        manifest_owner._OwnerFingerprintRegistry(
            (*build.fingerprints, build.fingerprints[0]),
            transaction_id=transaction_id,
        )
    with pytest.raises(ValueError, match="set is incomplete"):
        manifest_owner._OwnerFingerprintRegistry(
            build.fingerprints[:-1],
            transaction_id=transaction_id,
        )


def test_publication_rejects_foreign_owner_fingerprint_reuse(
    tmp_path: Path,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    output_dir = tmp_path / "candidate"
    candidate_arguments = _publication_arguments(staging)
    baseline_arguments = _publication_arguments(baseline["root"])

    candidate_arguments["retained_sample_metadata_fingerprint"] = (
        baseline_arguments["retained_sample_metadata_fingerprint"]
    )
    with pytest.raises(ValueError, match="metadata fingerprint was substituted"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **candidate_arguments,
        )

    candidate_arguments = _publication_arguments(staging)
    candidate_arguments["alignment_artifact_fingerprints"] = (
        baseline_arguments["alignment_artifact_fingerprints"]
    )
    with pytest.raises(ValueError, match="artifact path census"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **candidate_arguments,
        )

    assert staging.is_dir()
    assert not output_dir.exists()


def test_publication_rejects_every_cross_role_artifact_mapping(
    tmp_path: Path,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    output_dir = tmp_path / "candidate"
    artifact_roles = tuple(_publication_arguments(staging)["artifacts"])

    for permutation in itertools.permutations(artifact_roles):
        if permutation == artifact_roles:
            continue
        arguments = _publication_arguments(staging)
        original_artifacts = dict(arguments["artifacts"])
        arguments["artifacts"] = {
            role: original_artifacts[source_role]
            for role, source_role in zip(
                artifact_roles,
                permutation,
                strict=True,
            )
        }
        with pytest.raises(ValueError, match="artifact role mapping"):
            publish_provisional_run_bundle_atomic(
                staging,
                output_dir,
                **arguments,
            )

    arguments = _publication_arguments(staging)
    original_artifacts = dict(arguments["artifacts"])
    first_role, second_role = artifact_roles[:2]
    original_artifacts[second_role] = original_artifacts[first_role]
    arguments["artifacts"] = original_artifacts
    with pytest.raises(ValueError, match="path census"):
        publish_provisional_run_bundle_atomic(
            staging,
            output_dir,
            **arguments,
        )

    assert staging.is_dir()
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        (
            "path",
            "workflow/alignment/provisional_row_identity.tsv",
            "path was substituted",
        ),
        ("schema_version", "foreign_schema_v1", "schema does not match"),
    ],
)
def test_manifest_fingerprint_registry_rejects_path_or_schema_substitution(
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    baseline = _packet(tmp_path / "baseline", matrix_bytes="matrix-a\n")
    staging = _copy_packet_for_publication(baseline, tmp_path / "candidate.attempt")
    transaction_id = "a" * 32
    build = manifest_owner._build_manifest(
        root=staging,
        transaction_id=transaction_id,
        **_publication_arguments(staging),
    )
    payload = copy.deepcopy(build.payload)
    payload["artifacts"]["provisional_matrix"][field] = replacement
    registry = manifest_owner._OwnerFingerprintRegistry(
        build.fingerprints,
        transaction_id=transaction_id,
    )

    with pytest.raises(ValueError, match=message):
        manifest_owner._validate_manifest_payload(
            payload,
            root=staging,
            fingerprint_registry=registry,
        )


def test_manifest_fingerprint_cannot_be_caller_minted(tmp_path: Path) -> None:
    path = tmp_path / "artifact.tsv"
    path.write_text("artifact\n", encoding="utf-8")
    fingerprint = manifest_owner.fingerprint_file_content(path)

    with pytest.raises(TypeError, match="must be minted by their owner"):
        manifest_owner._ImmutableManifestFingerprint(
            transaction_id="a" * 32,
            binding_role="artifact:provisional_matrix",
            root=tmp_path.resolve(),
            resolved_path=path.resolve(),
            relative_path=path.name,
            schema_version="schema_v1",
            fingerprint=fingerprint,
            _mint_capability=object(),
        )
    with pytest.raises(TypeError, match="must be minted by their owner"):
        manifest_owner.RetainedSampleMetadataFingerprintReceipt(
            root=tmp_path.resolve(),
            resolved_path=path.resolve(),
            relative_path=path.name,
            fingerprint=fingerprint,
            _mint_capability=object(),
        )


def _packet(
    root: Path,
    *,
    matrix_bytes: str,
    discovery_settings: DiscoverySettings | None = None,
) -> dict[str, object]:
    root.mkdir(parents=True)
    retained_metadata = root / "inputs" / "sample_metadata.tsv"
    retained_metadata.parent.mkdir()
    retained_metadata.write_text(
        "sample_stem\tinjection_order\nSample_A\t1\n",
        encoding="utf-8",
    )
    discovery_index = _write_discovery_packet(root)
    artifact_dir = root / "workflow" / "alignment"
    artifact_dir.mkdir(parents=True)
    artifacts = {
        "provisional_matrix": artifact_dir / "provisional_alignment_matrix.tsv",
        "provisional_identity": artifact_dir / "provisional_row_identity.tsv",
        "provisional_cell_provenance": (
            artifact_dir / "provisional_cell_provenance.tsv"
        ),
        "provisional_summary": artifact_dir / "provisional_matrix_summary.json",
    }
    artifacts["provisional_matrix"].write_text(matrix_bytes, encoding="utf-8")
    for name, path in artifacts.items():
        if name != "provisional_matrix":
            path.write_text(f"{name}\n", encoding="utf-8")
    manifest = write_provisional_run_manifest(
        root / "run_manifest.json",
        root=root,
        code_revision="a" * 40,
        preset_name="dna_dr",
        discovery_settings=discovery_settings
        or DiscoverySettings(
            neutral_loss_profiles=(
                NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0474),
            ),
        ),
        alignment_config=AlignmentConfig(),
        runtime=ProvisionalRunRuntimeConfiguration(
            sample_count=1,
            raw_workers=3,
            raw_xic_batch_size=16,
            owner_build_xic_backend="raw_superwindow",
            owner_backfill_xic_backend="raw",
            owner_backfill_window_strategy="super-window",
            owner_backfill_superwindow_span_factor=2,
        ),
        retained_sample_metadata=retained_metadata,
        discovery_batch_index=discovery_index,
        artifacts=artifacts,
    )
    return {
        "root": root,
        "manifest": manifest,
        "artifacts": artifacts,
    }


def _copy_packet_for_publication(packet: dict[str, object], staging: Path) -> Path:
    shutil.copytree(packet["root"], staging)
    (staging / "run_manifest.json").unlink()
    return staging


def _publication_arguments(root: Path) -> dict[str, object]:
    retained_sample_metadata = root / "inputs" / "sample_metadata.tsv"
    artifacts = {
        "provisional_matrix": root / _publication_relative_paths()[0],
        "provisional_identity": root / _publication_relative_paths()[1],
        "provisional_cell_provenance": root / _publication_relative_paths()[2],
        "provisional_summary": root / _publication_relative_paths()[3],
    }
    artifact_paths = tuple(artifacts.values())
    artifact_receipts = tuple(
        alignment_receipt_owner._mint_alignment_artifact_fingerprint_receipt(
            artifact_path=path,
            fingerprint=fingerprint_file_content(path),
        )
        for path in artifact_paths
    )
    alignment_fingerprints = (
        alignment_receipt_owner._mint_alignment_artifact_fingerprint_batch(
            artifact_paths=artifact_paths,
            receipts=artifact_receipts,
        )
    )
    return {
        "code_revision": "a" * 40,
        "preset_name": "dna_dr",
        "discovery_settings": DiscoverySettings(
            neutral_loss_profiles=(
                NeutralLossProfile(tag="DNA_dR", neutral_loss_da=116.0474),
            ),
        ),
        "alignment_config": AlignmentConfig(),
        "runtime": ProvisionalRunRuntimeConfiguration(
            sample_count=1,
            raw_workers=3,
            raw_xic_batch_size=16,
            owner_build_xic_backend="raw_superwindow",
            owner_backfill_xic_backend="raw",
            owner_backfill_window_strategy="super-window",
            owner_backfill_superwindow_span_factor=2,
        ),
        "retained_sample_metadata": retained_sample_metadata,
        "discovery_batch_index": (
            root / "workflow" / "discovery" / "discovery_batch_index.csv"
        ),
        "artifacts": artifacts,
        "retained_sample_metadata_fingerprint": (
            manifest_owner.mint_retained_sample_metadata_fingerprint_receipt(
                retained_sample_metadata,
                root=root,
            )
        ),
        "alignment_artifact_fingerprints": alignment_fingerprints,
    }


def _publication_relative_paths() -> tuple[Path, ...]:
    artifact_root = Path("workflow") / "alignment"
    return (
        artifact_root / "provisional_alignment_matrix.tsv",
        artifact_root / "provisional_row_identity.tsv",
        artifact_root / "provisional_cell_provenance.tsv",
        artifact_root / "provisional_matrix_summary.json",
        Path("inputs") / "sample_metadata.tsv",
        Path("workflow") / "discovery" / "discovery_batch_index.csv",
    )


def _remint_fingerprint(
    fingerprint: manifest_owner._ImmutableManifestFingerprint,
    **changes: object,
) -> manifest_owner._ImmutableManifestFingerprint:
    values = {
        "transaction_id": fingerprint.transaction_id,
        "binding_role": fingerprint.binding_role,
        "root": fingerprint.root,
        "resolved_path": fingerprint.resolved_path,
        "relative_path": fingerprint.relative_path,
        "schema_version": fingerprint.schema_version,
        "fingerprint": fingerprint.fingerprint,
        **changes,
    }
    return manifest_owner._ImmutableManifestFingerprint(
        **values,
        _mint_capability=manifest_owner._FINGERPRINT_MINT_CAPABILITY,
    )


def _write_discovery_packet(root: Path) -> Path:
    discovery_dir = root / "workflow" / "discovery"
    sample_dir = discovery_dir / "Sample_A"
    sample_dir.mkdir(parents=True)
    raw_path = root / "inputs" / "Sample_A.raw"
    raw_path.write_bytes(b"raw")
    candidate_csv = sample_dir / "discovery_candidates.csv"
    write_discovery_candidates_csv(candidate_csv, ())
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
    receipt = mint_discovery_candidate_universe_receipt(
        sample_stem="Sample_A",
        acquisition_source=source,
        candidate_csv=candidate_csv,
        candidates=(),
    )
    fieldnames = (
        "schema_version",
        "sample_stem",
        "raw_file",
        "candidate_csv",
        "review_csv",
        "candidate_count",
        "high_count",
        "medium_count",
        "low_count",
        *DISCOVERY_BATCH_RECEIPT_COLUMNS,
    )
    row = {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "sample_stem": "Sample_A",
        "raw_file": str(raw_path),
        "candidate_csv": "Sample_A/discovery_candidates.csv",
        "review_csv": "",
        "candidate_count": "0",
        "high_count": "0",
        "medium_count": "0",
        "low_count": "0",
        **discovery_receipt_index_fields(receipt),
    }
    index_path = discovery_dir / "discovery_batch_index.csv"
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)
    return index_path
