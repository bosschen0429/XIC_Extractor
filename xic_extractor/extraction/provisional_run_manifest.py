"""Byte- and structure-bound envelope for one provisional Alignment run."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import cast

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
from xic_extractor.presets import apply_to_alignment, apply_to_discovery, load_preset
from xic_extractor.processing_identity import fingerprint_file_content

PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION = (
    "untargeted_provisional_matrix_publication_envelope_v2"
)

_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ARTIFACT_SCHEMAS = {
    "provisional_matrix": PROVISIONAL_MATRIX_SCHEMA_VERSION,
    "provisional_identity": PROVISIONAL_IDENTITY_SCHEMA_VERSION,
    "provisional_cell_provenance": PROVISIONAL_PROVENANCE_SCHEMA_VERSION,
    "provisional_summary": PROVISIONAL_SUMMARY_SCHEMA_VERSION,
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

_OWNER_BUILD_BACKENDS = frozenset({"raw", "raw_superwindow", "ms1_index"})
_OWNER_BACKFILL_BACKENDS = frozenset(
    {"raw", "ms1_index", "ms1_index_hybrid"}
)
_OWNER_BACKFILL_WINDOW_STRATEGIES = frozenset({"exact", "super-window"})


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
                schema_version="sample_metadata_v1",
            ),
            "discovery_batch_index": {
                **_artifact_binding(
                    discovery_batch_index,
                    root=root,
                    schema_version=DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
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
            )
            for name, schema_version in _ARTIFACT_SCHEMAS.items()
        },
    }
    _write_json_atomic(path, payload)
    validate_provisional_run_manifest(path, root=root)
    return path


def validate_provisional_run_manifest(
    path: Path,
    *,
    root: Path,
) -> dict[str, object]:
    """Validate envelope structure and exact bytes without semantic replay."""

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_mapping_without_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("provisional run manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("provisional run manifest must be an object")
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
        expected_schema="sample_metadata_v1",
    )
    discovery_binding = _required_mapping(inputs, "discovery_batch_index")
    _validate_binding(
        discovery_binding,
        root=root,
        expected_schema=DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        extra_fields=_DISCOVERY_CENSUS_FIELDS,
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
        )
    _validate_effective_configuration(payload)
    _validate_runtime_configuration(payload, sample_count=sample_count)
    return payload


def _effective_configuration_from_owner(
    *,
    preset_name: str,
    discovery_settings: DiscoverySettings,
    alignment_config: AlignmentConfig,
) -> dict[str, object]:
    """Derive and verify effective settings while live owners are available."""

    preset = load_preset(preset_name)
    owned_discovery = apply_to_discovery(preset)
    owned_alignment, _runtime_options = apply_to_alignment(preset)
    expected = {
        "discovery": _json_value(asdict(owned_discovery)),
        "alignment": _json_value(asdict(owned_alignment)),
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


def _artifact_binding(
    path: Path,
    *,
    root: Path,
    schema_version: str,
) -> dict[str, object]:
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    try:
        relative_path = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("manifest artifact must stay under the run root") from exc
    fingerprint = fingerprint_file_content(resolved_path)
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
    actual = fingerprint_file_content(artifact_path)
    if actual.content_sha256 != expected_sha or actual.byte_size != expected_size:
        raise ValueError("manifest artifact bytes do not match their binding")
    return artifact_path


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
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = (
    "PROVISIONAL_RUN_MANIFEST_SCHEMA_VERSION",
    "ProvisionalRunRuntimeConfiguration",
    "validate_provisional_run_manifest",
    "write_provisional_run_manifest",
)
