"""Byte- and structure-bound envelope for one provisional Alignment run."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import InitVar, asdict, dataclass, field, fields
from pathlib import Path
from typing import cast, final

from xic_extractor.alignment.artifact_write_receipts import (
    AlignmentArtifactFingerprintBatch,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.csv_io import (
    DiscoveryBatchInput,
    read_discovery_batch_index,
)
from xic_extractor.alignment.ms1_index_source import (
    OwnerBackfillXicBackend,
    OwnerBuildXicBackend,
)
from xic_extractor.alignment.owner_backfill import OwnerBackfillWindowStrategy
from xic_extractor.alignment.provisional_matrix import (
    PROVISIONAL_IDENTITY_SCHEMA_VERSION,
    PROVISIONAL_MATRIX_SCHEMA_VERSION,
    PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
    PROVISIONAL_SUMMARY_SCHEMA_VERSION,
)
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.presets import bind_cid_nl_recipe, load_preset
from xic_extractor.processing_identity import (
    FileContentFingerprint,
    fingerprint_file_content,
)

PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION = (
    "untargeted_provisional_matrix_publication_envelope"
)

_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ARTIFACT_SCHEMAS = {
    "provisional_matrix": PROVISIONAL_MATRIX_SCHEMA_VERSION,
    "provisional_identity": PROVISIONAL_IDENTITY_SCHEMA_VERSION,
    "provisional_cell_provenance": PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
    "provisional_summary": PROVISIONAL_SUMMARY_SCHEMA_VERSION,
}
_ALIGNMENT_ARTIFACT_FILENAMES = {
    "provisional_matrix": "provisional_alignment_matrix.tsv",
    "provisional_identity": "provisional_row_identity.tsv",
    "provisional_cell_provenance": "provisional_cell_provenance.tsv",
    "provisional_summary": "provisional_matrix_summary.json",
}
_CLAIM_LEVELS = {
    "artifact_bytes": "byte_bound",
    "configuration": "structure_bound",
    "embedded_semantics": "unavailable",
}
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "validation_status",
        "row_identity_status",
        "identity_settlement_status",
        "product_authority",
        "authenticity_authority",
        "semantic_replay_authority",
        "claim_levels",
        "code_revision",
        "preset_name",
        "effective_configuration",
        "runtime",
        "inputs",
        "artifacts",
    }
)
_INPUT_FIELDS = frozenset({"sample_metadata", "discovery_batch_index"})
_ARTIFACT_BINDING_FIELDS = frozenset(
    {"path", "schema_version", "sha256", "byte_size"}
)
_DISCOVERY_CENSUS_FIELDS = frozenset(
    {"sample_count", "receipt_count", "candidate_count", "receipt_census_sha256"}
)
_MANIFEST_BINDING_ROLES = frozenset(
    {
        "input:sample_metadata",
        "input:discovery_batch_index",
        *(
            f"artifact:{artifact_name}"
            for artifact_name in _ARTIFACT_SCHEMAS
        ),
    }
)
_PUBLICATION_FILE_ROLES = _MANIFEST_BINDING_ROLES
_TRANSACTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_FINGERPRINT_MINT_CAPABILITY = object()
_RETAINED_METADATA_FINGERPRINT_MINT_CAPABILITY = object()

_OWNER_BUILD_BACKENDS = frozenset({"raw", "raw_superwindow", "ms1_index"})
_OWNER_BACKFILL_BACKENDS = frozenset(
    {"raw", "ms1_index", "ms1_index_hybrid"}
)
_OWNER_BACKFILL_WINDOW_STRATEGIES = frozenset({"exact", "super-window"})


@dataclass(frozen=True, slots=True)
class _ImmutableManifestFingerprint:
    """Owner-minted byte facts retained only for one publication transaction."""

    transaction_id: str
    binding_role: str
    root: Path
    resolved_path: Path
    relative_path: str
    schema_version: str
    fingerprint: FileContentFingerprint
    _mint_capability: InitVar[object]

    def __post_init__(self, _mint_capability: object) -> None:
        if _mint_capability is not _FINGERPRINT_MINT_CAPABILITY:
            raise TypeError("manifest fingerprints must be minted by their owner")
        if _TRANSACTION_ID_PATTERN.fullmatch(self.transaction_id) is None:
            raise ValueError("manifest fingerprint transaction identity is invalid")
        if self.binding_role not in _PUBLICATION_FILE_ROLES:
            raise ValueError("manifest fingerprint role is invalid")
        if not self.root.is_absolute() or not self.resolved_path.is_absolute():
            raise ValueError("manifest fingerprint paths must be canonical")
        relative_path = Path(self.relative_path)
        if (
            not self.relative_path
            or relative_path.is_absolute()
            or ".." in relative_path.parts
        ):
            raise ValueError("manifest fingerprint relative path is invalid")
        if not self.schema_version:
            raise ValueError("manifest fingerprint schema is invalid")
        if not isinstance(self.fingerprint, FileContentFingerprint):
            raise TypeError("manifest fingerprint content facts are invalid")


@final
@dataclass(frozen=True, slots=True)
class RetainedSampleMetadataFingerprintReceipt:
    """Owner-minted bytes for one retained publication metadata copy."""

    root: Path
    resolved_path: Path
    relative_path: str
    fingerprint: FileContentFingerprint
    _mint_capability: InitVar[object]

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RetainedSampleMetadataFingerprintReceipt is final")

    def __post_init__(self, _mint_capability: object) -> None:
        if _mint_capability is not _RETAINED_METADATA_FINGERPRINT_MINT_CAPABILITY:
            raise TypeError(
                "retained sample metadata fingerprints must be minted by "
                "their owner"
            )
        if not self.root.is_absolute() or not self.resolved_path.is_absolute():
            raise ValueError("retained sample metadata paths must be canonical")
        relative_path = Path(self.relative_path)
        if (
            not self.relative_path
            or relative_path.is_absolute()
            or ".." in relative_path.parts
        ):
            raise ValueError(
                "retained sample metadata relative path is invalid"
            )
        if not isinstance(self.fingerprint, FileContentFingerprint):
            raise TypeError(
                "retained sample metadata fingerprint content facts are invalid"
            )


@dataclass(frozen=True, slots=True)
class _ManifestBuild:
    payload: dict[str, object]
    fingerprints: tuple[_ImmutableManifestFingerprint, ...]


@dataclass(frozen=True, slots=True)
class ProvisionalRunRuntimeConfiguration:
    """Complete typed launch configuration recorded by this diagnostic runner."""

    sample_count: int
    raw_workers: int
    raw_xic_batch_size: int
    owner_build_xic_backend: OwnerBuildXicBackend
    owner_backfill_xic_backend: OwnerBackfillXicBackend
    owner_backfill_window_strategy: OwnerBackfillWindowStrategy
    owner_backfill_superwindow_span_factor: int
    runner: str = field(
        init=False,
        default="scripts.run_untargeted_provisional_matrix",
    )
    precursor_filter: None = field(init=False, default=None)
    backfill_scope: str = field(init=False, default="full-audit")
    selected_family_count: int = field(init=False, default=0)
    selected_family_source: str = field(init=False, default="")
    require_discovery_owner_closure: bool = field(init=False, default=True)
    emit_provisional_matrix: bool = field(init=False, default=True)
    output_level: str = field(init=False, default="validation-minimal")
    audit_evidence_mode: str = field(init=False, default="none")
    sample_column_order_source: str = field(
        init=False,
        default="retained_sample_metadata",
    )

    def __post_init__(self) -> None:
        for name in (
            "sample_count",
            "raw_workers",
            "raw_xic_batch_size",
            "owner_backfill_superwindow_span_factor",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.owner_build_xic_backend not in _OWNER_BUILD_BACKENDS:
            raise ValueError("owner_build_xic_backend is unsupported")
        if self.owner_backfill_xic_backend not in _OWNER_BACKFILL_BACKENDS:
            raise ValueError("owner_backfill_xic_backend is unsupported")
        if (
            self.owner_backfill_window_strategy
            not in _OWNER_BACKFILL_WINDOW_STRATEGIES
        ):
            raise ValueError("owner_backfill_window_strategy is unsupported")


def mint_retained_sample_metadata_fingerprint_receipt(
    path: Path,
    *,
    root: Path,
    observation: Callable[[Mapping[str, object]], None] | None = None,
) -> RetainedSampleMetadataFingerprintReceipt:
    """Fingerprint one retained metadata copy through its publication owner."""

    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    try:
        relative_path = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            "retained sample metadata must stay under the publication root"
        ) from exc
    return RetainedSampleMetadataFingerprintReceipt(
        root=resolved_root,
        resolved_path=resolved_path,
        relative_path=relative_path.as_posix(),
        fingerprint=fingerprint_file_content(
            resolved_path,
            observation=observation,
        ),
        _mint_capability=_RETAINED_METADATA_FINGERPRINT_MINT_CAPABILITY,
    )


def write_provisional_run_manifest(
    path: Path,
    *,
    root: Path,
    code_revision: str,
    preset_name: str,
    discovery_settings: DiscoverySettings,
    alignment_config: AlignmentConfig,
    runtime: ProvisionalRunRuntimeConfiguration,
    retained_sample_metadata: Path,
    discovery_batch_index: Path,
    artifacts: Mapping[str, Path],
) -> Path:
    """Write and immediately verify one portable, byte-bound run envelope."""

    build = _build_manifest(
        root=root,
        code_revision=code_revision,
        preset_name=preset_name,
        discovery_settings=discovery_settings,
        alignment_config=alignment_config,
        runtime=runtime,
        retained_sample_metadata=retained_sample_metadata,
        discovery_batch_index=discovery_batch_index,
        artifacts=artifacts,
        transaction_id=None,
        retained_sample_metadata_fingerprint=None,
        alignment_artifact_fingerprints=None,
    )
    _write_json_atomic(path, build.payload)
    validate_provisional_run_manifest(path, root=root)
    return path


def publish_provisional_run_bundle_atomic(
    staging_root: Path,
    output_dir: Path,
    *,
    code_revision: str,
    preset_name: str,
    discovery_settings: DiscoverySettings,
    alignment_config: AlignmentConfig,
    runtime: ProvisionalRunRuntimeConfiguration,
    retained_sample_metadata: Path,
    discovery_batch_index: Path,
    artifacts: Mapping[str, Path],
    retained_sample_metadata_fingerprint: (
        RetainedSampleMetadataFingerprintReceipt
    ),
    alignment_artifact_fingerprints: AlignmentArtifactFingerprintBatch,
) -> Path:
    """Bind and publish one directory under a closed single-writer protocol.

    Atomicity applies to final-directory visibility.  The caller and all
    in-process collaborators must stop writing ``staging_root`` before entry;
    this function does not claim an OS-level seal against another
    same-permission process mutating staging during the final check-to-rename
    interval.  Consumers independently validate the visible manifest bytes.
    """

    resolved_staging = staging_root.resolve(strict=True)
    resolved_output_parent = output_dir.parent.resolve(strict=True)
    resolved_output = resolved_output_parent / output_dir.name
    if resolved_staging.parent != resolved_output_parent:
        raise ValueError("staging and final output directories must share a parent")
    if resolved_staging == resolved_output or resolved_output.exists():
        raise ValueError("final publication directory must not already exist")
    manifest_path = resolved_staging / "run_manifest.json"
    if manifest_path.exists():
        raise ValueError("staging publication already contains a run manifest")

    transaction_id = secrets.token_hex(16)
    build = _build_manifest(
        root=resolved_staging,
        code_revision=code_revision,
        preset_name=preset_name,
        discovery_settings=discovery_settings,
        alignment_config=alignment_config,
        runtime=runtime,
        retained_sample_metadata=retained_sample_metadata,
        discovery_batch_index=discovery_batch_index,
        artifacts=artifacts,
        transaction_id=transaction_id,
        retained_sample_metadata_fingerprint=(
            retained_sample_metadata_fingerprint
        ),
        alignment_artifact_fingerprints=alignment_artifact_fingerprints,
    )
    expected_manifest_bytes = _manifest_json_bytes(build.payload)
    _write_json_atomic(manifest_path, build.payload)
    payload = _read_owner_manifest_for_publication(
        manifest_path,
        expected_bytes=expected_manifest_bytes,
    )
    registry = _OwnerFingerprintRegistry(
        build.fingerprints,
        transaction_id=transaction_id,
    )
    _validate_manifest_payload(
        payload,
        root=resolved_staging,
        fingerprint_registry=registry,
    )

    def confirm_immutable_bundle() -> None:
        _confirm_owner_fingerprints(build.fingerprints)
        _confirm_owner_manifest_bytes(
            manifest_path,
            expected_bytes=expected_manifest_bytes,
        )

    _publish_directory_atomic(
        resolved_staging,
        resolved_output,
        before_attempt=confirm_immutable_bundle,
    )
    return resolved_output / "run_manifest.json"


def _build_manifest(
    *,
    root: Path,
    code_revision: str,
    preset_name: str,
    discovery_settings: DiscoverySettings,
    alignment_config: AlignmentConfig,
    runtime: ProvisionalRunRuntimeConfiguration,
    retained_sample_metadata: Path,
    discovery_batch_index: Path,
    artifacts: Mapping[str, Path],
    transaction_id: str | None,
    retained_sample_metadata_fingerprint: (
        RetainedSampleMetadataFingerprintReceipt | None
    ),
    alignment_artifact_fingerprints: AlignmentArtifactFingerprintBatch | None,
) -> _ManifestBuild:
    code_revision = _validated_revision(code_revision)
    if not isinstance(runtime, ProvisionalRunRuntimeConfiguration):
        raise TypeError(
            "provisional run manifest requires typed runtime configuration"
        )
    preset_name = preset_name.strip()
    if not preset_name:
        raise ValueError("provisional run manifest requires preset_name")
    if set(artifacts) != set(_ARTIFACT_SCHEMAS):
        raise ValueError("provisional run manifest artifact set is incomplete")
    reused_fingerprints = _publication_fingerprint_reuse(
        root=root,
        retained_sample_metadata=retained_sample_metadata,
        artifacts=artifacts,
        retained_sample_metadata_fingerprint=(
            retained_sample_metadata_fingerprint
        ),
        alignment_artifact_fingerprints=alignment_artifact_fingerprints,
        required=transaction_id is not None,
    )

    batch = read_discovery_batch_index(
        discovery_batch_index,
        require_owner_closure=True,
    )
    receipt_census = _discovery_receipt_census(batch)
    effective_configuration = _effective_configuration_from_owner(
        preset_name=preset_name,
        discovery_settings=discovery_settings,
        alignment_config=alignment_config,
    )
    fingerprints: list[_ImmutableManifestFingerprint] = []
    payload: dict[str, object] = {
        "schema_version": PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION,
        "validation_status": "diagnostic_only",
        "row_identity_status": "provisional",
        "identity_settlement_status": "not_run",
        "product_authority": False,
        "authenticity_authority": False,
        "semantic_replay_authority": False,
        "claim_levels": dict(_CLAIM_LEVELS),
        "code_revision": code_revision,
        "preset_name": preset_name,
        "effective_configuration": effective_configuration,
        "runtime": _json_value(asdict(runtime)),
        "inputs": {
            "sample_metadata": _artifact_binding(
                retained_sample_metadata,
                root=root,
                schema_version="sample_metadata",
                binding_role="input:sample_metadata",
                transaction_id=transaction_id,
                fingerprint_sink=fingerprints,
                reused_fingerprint=reused_fingerprints.get(
                    "input:sample_metadata"
                ),
            ),
            "discovery_batch_index": {
                **_artifact_binding(
                    discovery_batch_index,
                    root=root,
                    schema_version=DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
                    binding_role="input:discovery_batch_index",
                    transaction_id=transaction_id,
                    fingerprint_sink=fingerprints,
                    reused_fingerprint=None,
                ),
                "sample_count": len(batch.sample_order),
                "receipt_count": len(receipt_census),
                "candidate_count": sum(row[3] for row in receipt_census),
                "receipt_census_sha256": _canonical_sha256(receipt_census),
            },
        },
        "artifacts": {
            name: _artifact_binding(
                artifacts[name],
                root=root,
                schema_version=schema_version,
                binding_role=f"artifact:{name}",
                transaction_id=transaction_id,
                fingerprint_sink=fingerprints,
                reused_fingerprint=reused_fingerprints.get(
                    f"artifact:{name}"
                ),
            )
            for name, schema_version in _ARTIFACT_SCHEMAS.items()
        },
    }
    return _ManifestBuild(payload=payload, fingerprints=tuple(fingerprints))


def validate_provisional_run_manifest(
    path: Path,
    *,
    root: Path,
) -> dict[str, object]:
    """Validate envelope structure and exact bytes without semantic replay."""

    payload = _read_manifest_payload(path)
    _validate_manifest_payload(payload, root=root, fingerprint_registry=None)
    return payload


def _read_manifest_payload(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_mapping_without_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("provisional run manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("provisional run manifest must be an object")
    return payload


def _read_owner_manifest_for_publication(
    path: Path,
    *,
    expected_bytes: bytes,
) -> dict[str, object]:
    try:
        encoded = path.read_bytes()
        if encoded != expected_bytes:
            raise ValueError(
                "run manifest bytes do not match owner serialization"
            )
        payload = json.loads(
            encoded,
            object_pairs_hook=_mapping_without_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("provisional run manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("provisional run manifest must be an object")
    return payload


def _validate_manifest_payload(
    payload: Mapping[str, object],
    *,
    root: Path,
    fingerprint_registry: _OwnerFingerprintRegistry | None,
) -> None:
    if set(payload) != _MANIFEST_FIELDS:
        raise ValueError("provisional run manifest field set is invalid")
    if payload.get("schema_version") != PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported provisional run manifest schema")
    _validated_revision(payload.get("code_revision"))
    if payload.get("validation_status") != "diagnostic_only":
        raise ValueError("provisional run manifest must remain diagnostic_only")
    if payload.get("row_identity_status") != "provisional":
        raise ValueError("provisional run manifest row identity is invalid")
    if payload.get("identity_settlement_status") != "not_run":
        raise ValueError("provisional run manifest identity settlement is invalid")
    if payload.get("product_authority") is not False:
        raise ValueError("provisional run manifest cannot claim product authority")
    if payload.get("authenticity_authority") is not False:
        raise ValueError("provisional run manifest cannot claim authenticity")
    if payload.get("semantic_replay_authority") is not False:
        raise ValueError("provisional run manifest cannot claim semantic replay")
    if payload.get("claim_levels") != _CLAIM_LEVELS:
        raise ValueError("provisional run manifest claim levels are invalid")

    inputs = _required_mapping(payload, "inputs")
    if set(inputs) != _INPUT_FIELDS:
        raise ValueError("provisional run manifest input field set is invalid")
    _validate_binding(
        _required_mapping(inputs, "sample_metadata"),
        root=root,
        expected_schema="sample_metadata",
        binding_role="input:sample_metadata",
        fingerprint_registry=fingerprint_registry,
    )
    discovery_binding = _required_mapping(inputs, "discovery_batch_index")
    _validate_binding(
        discovery_binding,
        root=root,
        expected_schema=DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        extra_fields=_DISCOVERY_CENSUS_FIELDS,
        binding_role="input:discovery_batch_index",
        fingerprint_registry=fingerprint_registry,
    )
    sample_count = _positive_int(discovery_binding, "sample_count")
    receipt_count = _positive_int(discovery_binding, "receipt_count")
    if sample_count != receipt_count:
        raise ValueError("Discovery receipt census does not cover every sample")
    candidate_count = _nonnegative_int(discovery_binding, "candidate_count")
    expected_census_sha = _validated_sha256(
        discovery_binding.get("receipt_census_sha256")
    )
    del candidate_count, expected_census_sha

    artifacts = _required_mapping(payload, "artifacts")
    if set(artifacts) != set(_ARTIFACT_SCHEMAS):
        raise ValueError("provisional run manifest artifact set is incomplete")
    for name, expected_schema in _ARTIFACT_SCHEMAS.items():
        _validate_binding(
            _required_mapping(artifacts, name),
            root=root,
            expected_schema=expected_schema,
            binding_role=f"artifact:{name}",
            fingerprint_registry=fingerprint_registry,
        )
    _validate_effective_configuration(payload)
    _validate_runtime_configuration(payload, sample_count=sample_count)
    if fingerprint_registry is not None:
        fingerprint_registry.assert_consumed()


def _effective_configuration_from_owner(
    *,
    preset_name: str,
    discovery_settings: DiscoverySettings,
    alignment_config: AlignmentConfig,
) -> dict[str, object]:
    """Derive and verify effective settings while live owners are available."""

    preset = load_preset(preset_name)
    recipe = bind_cid_nl_recipe(preset, "full")
    expected = {
        "discovery": _json_value(asdict(recipe.discovery_settings)),
        "alignment": _json_value(asdict(recipe.alignment_config)),
    }
    supplied = {
        "discovery": _json_value(asdict(discovery_settings)),
        "alignment": _json_value(asdict(alignment_config)),
    }
    if supplied != expected:
        raise ValueError(
            "provisional run effective configuration does not match "
            "the preset owner"
        )
    return expected


def _validate_effective_configuration(payload: Mapping[str, object]) -> None:
    preset_name = payload.get("preset_name")
    if not isinstance(preset_name, str) or not preset_name.strip():
        raise ValueError("provisional run manifest preset_name is missing")
    effective = _required_mapping(payload, "effective_configuration")
    if set(effective) != {"discovery", "alignment"}:
        raise ValueError("provisional run manifest effective configuration is invalid")
    for name, owner_type in (
        ("discovery", DiscoverySettings),
        ("alignment", AlignmentConfig),
    ):
        values = _required_mapping(effective, name)
        expected_fields = {item.name for item in fields(owner_type)}
        if set(values) != expected_fields:
            raise ValueError(
                "provisional run effective configuration field set is invalid"
            )
        _json_value(dict(values))


def _validate_runtime_configuration(
    payload: Mapping[str, object],
    *,
    sample_count: int,
) -> None:
    runtime = _required_mapping(payload, "runtime")
    expected_fields = {
        item.name for item in fields(ProvisionalRunRuntimeConfiguration)
    }
    if set(runtime) != expected_fields:
        raise ValueError("provisional run runtime field set is invalid")
    configured = ProvisionalRunRuntimeConfiguration(
        sample_count=_positive_int(runtime, "sample_count"),
        raw_workers=_positive_int(runtime, "raw_workers"),
        raw_xic_batch_size=_positive_int(runtime, "raw_xic_batch_size"),
        owner_build_xic_backend=cast(
            OwnerBuildXicBackend,
            _required_text(runtime, "owner_build_xic_backend"),
        ),
        owner_backfill_xic_backend=cast(
            OwnerBackfillXicBackend,
            _required_text(runtime, "owner_backfill_xic_backend"),
        ),
        owner_backfill_window_strategy=cast(
            OwnerBackfillWindowStrategy,
            _required_text(runtime, "owner_backfill_window_strategy"),
        ),
        owner_backfill_superwindow_span_factor=_positive_int(
            runtime,
            "owner_backfill_superwindow_span_factor",
        ),
    )
    if configured.sample_count != sample_count:
        raise ValueError("runtime sample count does not match Discovery receipts")
    if runtime != _json_value(asdict(configured)):
        raise ValueError("provisional run runtime is not owner-normalized")


def _publication_fingerprint_reuse(
    *,
    root: Path,
    retained_sample_metadata: Path,
    artifacts: Mapping[str, Path],
    retained_sample_metadata_fingerprint: (
        RetainedSampleMetadataFingerprintReceipt | None
    ),
    alignment_artifact_fingerprints: AlignmentArtifactFingerprintBatch | None,
    required: bool,
) -> dict[str, FileContentFingerprint]:
    if not required:
        if (
            retained_sample_metadata_fingerprint is not None
            or alignment_artifact_fingerprints is not None
        ):
            raise ValueError(
                "publication fingerprint reuse requires a publication transaction"
            )
        return {}
    if not isinstance(
        retained_sample_metadata_fingerprint,
        RetainedSampleMetadataFingerprintReceipt,
    ):
        raise TypeError(
            "publication requires an owner-minted retained metadata fingerprint"
        )
    if not isinstance(
        alignment_artifact_fingerprints,
        AlignmentArtifactFingerprintBatch,
    ):
        raise TypeError(
            "publication requires an owner-minted Alignment fingerprint batch"
        )

    resolved_root = root.resolve(strict=True)
    resolved_metadata = retained_sample_metadata.resolve(strict=True)
    try:
        metadata_relative_path = resolved_metadata.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            "retained sample metadata must stay under the publication root"
        ) from exc
    if (
        retained_sample_metadata_fingerprint.root != resolved_root
        or retained_sample_metadata_fingerprint.resolved_path != resolved_metadata
        or retained_sample_metadata_fingerprint.relative_path
        != metadata_relative_path.as_posix()
    ):
        raise ValueError("retained sample metadata fingerprint was substituted")

    resolved_artifacts = {
        artifact_role: artifact_path.resolve(strict=True)
        for artifact_role, artifact_path in artifacts.items()
    }
    resolved_artifact_paths = set(resolved_artifacts.values())
    if len(resolved_artifact_paths) != len(resolved_artifacts):
        raise ValueError(
            "manifest artifact path census contains duplicate paths"
        )
    for artifact_role, resolved_path in resolved_artifacts.items():
        if resolved_path.name != _ALIGNMENT_ARTIFACT_FILENAMES[artifact_role]:
            raise ValueError(
                "Alignment artifact role mapping does not match its owner"
            )

    receipts_by_path = {
        receipt.artifact_path: receipt.fingerprint
        for receipt in alignment_artifact_fingerprints.receipts
    }
    if not resolved_artifact_paths.issubset(receipts_by_path):
        raise ValueError(
            "Alignment fingerprint batch does not cover the manifest artifact "
            "path census"
        )
    reused = {
        "input:sample_metadata": (
            retained_sample_metadata_fingerprint.fingerprint
        )
    }
    for artifact_name, resolved_path in resolved_artifacts.items():
        fingerprint = receipts_by_path.get(resolved_path)
        if fingerprint is None:
            raise ValueError(
                f"Alignment fingerprint batch is missing {artifact_name}"
            )
        reused[f"artifact:{artifact_name}"] = fingerprint
    return reused


def _artifact_binding(
    path: Path,
    *,
    root: Path,
    schema_version: str,
    binding_role: str,
    transaction_id: str | None,
    fingerprint_sink: list[_ImmutableManifestFingerprint],
    reused_fingerprint: FileContentFingerprint | None,
) -> dict[str, object]:
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    try:
        relative_path = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("manifest artifact must stay under the run root") from exc
    fingerprint = (
        reused_fingerprint
        if reused_fingerprint is not None
        else fingerprint_file_content(resolved_path)
    )
    if transaction_id is not None:
        fingerprint_sink.append(
            _ImmutableManifestFingerprint(
                transaction_id=transaction_id,
                binding_role=binding_role,
                root=resolved_root,
                resolved_path=resolved_path,
                relative_path=relative_path.as_posix(),
                schema_version=schema_version,
                fingerprint=fingerprint,
                _mint_capability=_FINGERPRINT_MINT_CAPABILITY,
            )
        )
    return {
        "path": relative_path.as_posix(),
        "schema_version": schema_version,
        "sha256": fingerprint.content_sha256,
        "byte_size": fingerprint.byte_size,
    }


def _validate_binding(
    binding: Mapping[str, object],
    *,
    root: Path,
    expected_schema: str,
    extra_fields: frozenset[str] = frozenset(),
    binding_role: str,
    fingerprint_registry: _OwnerFingerprintRegistry | None,
) -> Path:
    if set(binding) != _ARTIFACT_BINDING_FIELDS | extra_fields:
        raise ValueError(
            "provisional run manifest artifact binding field set is invalid"
        )
    if binding.get("schema_version") != expected_schema:
        raise ValueError("manifest artifact schema does not match its owner")
    relative_text = binding.get("path")
    if not isinstance(relative_text, str) or not relative_text:
        raise ValueError("manifest artifact path is missing")
    relative_path = Path(relative_text)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("manifest artifact path escapes the run root")
    expected_sha = _validated_sha256(binding.get("sha256"))
    expected_size = _nonnegative_int(binding, "byte_size")
    artifact_path = (root / relative_path).resolve(strict=True)
    try:
        artifact_path.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("manifest artifact resolves outside the run root") from exc
    if fingerprint_registry is None:
        actual = fingerprint_file_content(artifact_path)
        if actual.content_sha256 != expected_sha or actual.byte_size != expected_size:
            raise ValueError("manifest artifact bytes do not match their binding")
    else:
        fingerprint_registry.consume(
            binding_role=binding_role,
            root=root,
            artifact_path=artifact_path,
            relative_path=relative_path.as_posix(),
            schema_version=expected_schema,
            content_sha256=expected_sha,
            byte_size=expected_size,
        )
    return artifact_path


class _OwnerFingerprintRegistry:
    """Consume every owner-minted binding exactly once inside one transaction."""

    def __init__(
        self,
        fingerprints: tuple[_ImmutableManifestFingerprint, ...],
        *,
        transaction_id: str,
    ) -> None:
        if _TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None:
            raise ValueError("manifest fingerprint transaction identity is invalid")
        by_role: dict[str, _ImmutableManifestFingerprint] = {}
        for fingerprint in fingerprints:
            if fingerprint.transaction_id != transaction_id:
                raise ValueError(
                    "manifest fingerprint belongs to a foreign transaction"
                )
            if fingerprint.binding_role in by_role:
                raise ValueError("manifest fingerprint role is duplicated")
            by_role[fingerprint.binding_role] = fingerprint
        if set(by_role) != _MANIFEST_BINDING_ROLES:
            raise ValueError("manifest fingerprint set is incomplete")
        self._remaining = by_role

    def consume(
        self,
        *,
        binding_role: str,
        root: Path,
        artifact_path: Path,
        relative_path: str,
        schema_version: str,
        content_sha256: str,
        byte_size: int,
    ) -> None:
        fingerprint = self._remaining.pop(binding_role, None)
        if fingerprint is None:
            raise ValueError("manifest fingerprint binding is missing or duplicated")
        if fingerprint.root != root.resolve(strict=True):
            raise ValueError("manifest fingerprint root was substituted")
        if fingerprint.resolved_path != artifact_path:
            raise ValueError("manifest fingerprint path was substituted")
        if fingerprint.relative_path != relative_path:
            raise ValueError("manifest fingerprint relative path was substituted")
        if fingerprint.schema_version != schema_version:
            raise ValueError("manifest fingerprint schema was substituted")
        if (
            fingerprint.fingerprint.content_sha256 != content_sha256
            or fingerprint.fingerprint.byte_size != byte_size
        ):
            raise ValueError("manifest fingerprint bytes were substituted")

    def assert_consumed(self) -> None:
        if self._remaining:
            raise ValueError("manifest fingerprint set contains unconsumed bindings")


def _confirm_owner_fingerprints(
    fingerprints: tuple[_ImmutableManifestFingerprint, ...],
) -> None:
    for fingerprint in fingerprints:
        current_path = (fingerprint.root / fingerprint.relative_path).resolve(
            strict=True
        )
        if current_path != fingerprint.resolved_path:
            raise ValueError(
                "manifest artifact path changed after fingerprint minting"
            )
        actual = fingerprint_file_content(current_path)
        if actual != fingerprint.fingerprint:
            raise ValueError("manifest artifact bytes changed before publication")


def _confirm_owner_manifest_bytes(path: Path, *, expected_bytes: bytes) -> None:
    try:
        actual_bytes = path.read_bytes()
    except OSError as exc:
        raise ValueError("run manifest is unreadable before publication") from exc
    if actual_bytes != expected_bytes:
        raise ValueError("run manifest bytes changed before publication")


def _publish_directory_atomic(
    staging: Path,
    output_dir: Path,
    *,
    before_attempt: Callable[[], None],
) -> None:
    for attempt in range(5):
        before_attempt()
        try:
            staging.replace(output_dir)
            return
        except PermissionError:
            if output_dir.exists() or attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _mapping_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    mapping: dict[str, object] = {}
    for key, value in pairs:
        if key in mapping:
            raise ValueError(
                f"provisional run manifest JSON contains duplicate key: {key}"
            )
        mapping[key] = value
    return mapping


def _discovery_receipt_census(
    batch: DiscoveryBatchInput,
) -> list[tuple[str, str, str, int]]:
    census: list[tuple[str, str, str, int]] = []
    for sample_stem in batch.sample_order:
        receipt = batch.candidate_receipts.get(sample_stem)
        if receipt is None:
            raise ValueError(
                f"provisional run manifest requires receipt for {sample_stem}"
            )
        census.append(
            (
                sample_stem,
                receipt.discovery_receipt_id,
                receipt.acquisition_source.acquisition_source_id,
                receipt.candidate_count,
            )
        )
    return census


def _required_mapping(
    value: Mapping[str, object],
    key: str,
) -> Mapping[str, object]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise ValueError(f"provisional run manifest {key} is missing")
    return nested


def _required_text(value: Mapping[str, object], key: str) -> str:
    parsed = value.get(key)
    if not isinstance(parsed, str) or not parsed or parsed != parsed.strip():
        raise ValueError(f"provisional run manifest {key} is invalid")
    return parsed


def _validated_revision(value: object) -> str:
    if not isinstance(value, str) or _REVISION_PATTERN.fullmatch(value) is None:
        raise ValueError("code_revision must be a lowercase 40-character Git SHA")
    return value


def _validated_sha256(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("manifest SHA-256 is invalid")
    return value


def _positive_int(value: Mapping[str, object], key: str) -> int:
    parsed = _nonnegative_int(value, key)
    if parsed < 1:
        raise ValueError(f"provisional run manifest {key} must be positive")
    return parsed


def _nonnegative_int(value: Mapping[str, object], key: str) -> int:
    parsed = value.get(key)
    if isinstance(parsed, bool) or not isinstance(parsed, int) or parsed < 0:
        raise ValueError(
            f"provisional run manifest {key} must be a non-negative integer"
        )
    return parsed


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_value(value: object) -> object:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("manifest configuration floats must be finite")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return [_json_value(item) for item in sorted(value, key=repr)]
    raise TypeError(f"unsupported manifest value: {type(value).__name__}")


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(_manifest_json_bytes(payload))
    temporary.replace(path)


def _manifest_json_bytes(payload: Mapping[str, object]) -> bytes:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return text.replace("\n", os.linesep).encode("utf-8")


__all__ = (
    "PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION",
    "ProvisionalRunRuntimeConfiguration",
    "RetainedSampleMetadataFingerprintReceipt",
    "mint_retained_sample_metadata_fingerprint_receipt",
    "publish_provisional_run_bundle_atomic",
    "validate_provisional_run_manifest",
    "write_provisional_run_manifest",
)
