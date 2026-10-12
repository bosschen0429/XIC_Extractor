from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from xic_extractor.configuration.models import ConfigError, Target
from xic_extractor.configuration.targets import (
    _parse_target_row,
    _validate_target_relationships,
)

TARGET_LIST_SCHEMA_VERSION = "target_list_snapshot"
_TARGET_KEY_PREFIX = "xic.target-key:"
_TARGET_DEFINITION_PREFIX = "xic.target-definition:"
_TARGET_LIST_SNAPSHOT_PREFIX = "xic.target-list-snapshot:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TargetIdentityError(ValueError):
    """Raised when a target-list identity cannot be owner-validated."""


@dataclass(frozen=True, slots=True)
class TargetKey:
    """Owner-stable target key; definition fields are deliberately excluded."""

    target_key_id: str
    identity_namespace: str
    target_label: str

    def __post_init__(self) -> None:
        namespace = _owner_normalized_text(
            self.identity_namespace,
            field="identity_namespace",
        )
        label = _owner_normalized_text(self.target_label, field="target_label")
        expected_id = _typed_digest(
            _TARGET_KEY_PREFIX,
            {
                "schema": "target_key_identity",
                "identity_namespace": namespace,
                "target_label": label,
            },
        )
        if self.target_key_id != expected_id:
            raise TargetIdentityError(
                "target_key_id mismatches the owner namespace and target label"
            )


@dataclass(frozen=True, slots=True)
class TargetDefinitionIdentity:
    """Exact immutable Target definition under one owner-stable key."""

    target_definition_id: str
    target_key: TargetKey
    target: Target

    def __post_init__(self) -> None:
        if not isinstance(self.target_key, TargetKey):
            raise TargetIdentityError("target_key must be an owner-minted TargetKey")
        _validate_target(self.target)
        if self.target.label != self.target_key.target_label:
            raise TargetIdentityError(
                "target definition label mismatches its owner-minted target key"
            )
        expected_id = _typed_digest(
            _TARGET_DEFINITION_PREFIX,
            _target_definition_basis(self.target_key, self.target),
        )
        if self.target_definition_id != expected_id:
            raise TargetIdentityError(
                "target_definition_id mismatches the exact target definition"
            )


@dataclass(frozen=True, slots=True)
class TargetListSnapshot:
    """Owner-minted immutable ordered target-list snapshot."""

    snapshot_id: str
    content_sha256: str
    identity_namespace: str
    definitions: tuple[TargetDefinitionIdentity, ...]

    def __post_init__(self) -> None:
        namespace = _owner_normalized_text(
            self.identity_namespace,
            field="identity_namespace",
        )
        content_sha256 = _validate_sha256(
            self.content_sha256,
            field="content_sha256",
        )
        definitions = _validated_definitions(
            self.definitions,
            identity_namespace=namespace,
        )
        if definitions != self.definitions:
            raise TargetIdentityError(
                "target-list snapshot definitions must preserve owner order"
            )
        expected_content_sha256 = _content_sha256(
            _target_list_content(namespace, definitions)
        )
        if content_sha256 != expected_content_sha256:
            raise TargetIdentityError(
                "target-list snapshot content digest mismatches owner definitions"
            )
        if self.snapshot_id != f"{_TARGET_LIST_SNAPSHOT_PREFIX}{content_sha256}":
            raise TargetIdentityError(
                "target-list snapshot ID mismatches owner content"
            )


@dataclass(frozen=True, slots=True)
class ResolvedTargetDefinition:
    """Self-validating exact resolution from one owner target-list snapshot."""

    definition: TargetDefinitionIdentity
    snapshot: TargetListSnapshot
    lookup_label: str

    def __post_init__(self) -> None:
        if not isinstance(self.definition, TargetDefinitionIdentity):
            raise TargetIdentityError(
                "definition must be an owner-minted TargetDefinitionIdentity"
            )
        if not isinstance(self.snapshot, TargetListSnapshot):
            raise TargetIdentityError(
                "snapshot must be an owner-minted TargetListSnapshot"
            )
        label = _owner_normalized_text(self.lookup_label, field="lookup_label")
        resolved = _resolve_definition(self.snapshot, label)
        if resolved != self.definition:
            raise TargetIdentityError(
                "resolved target definition mismatches its owner snapshot"
            )


def mint_target_key(
    target: Target,
    *,
    identity_namespace: str,
) -> TargetKey:
    """Mint a stable target key from the owner namespace and normalized label."""

    _validate_target(target)
    namespace = _owner_normalized_text(
        identity_namespace,
        field="identity_namespace",
    )
    label = _owner_normalized_text(target.label, field="target_label")
    return TargetKey(
        target_key_id=_typed_digest(
            _TARGET_KEY_PREFIX,
            {
                "schema": "target_key_identity",
                "identity_namespace": namespace,
                "target_label": label,
            },
        ),
        identity_namespace=namespace,
        target_label=label,
    )


def mint_target_definition_identity(
    target: Target,
    *,
    identity_namespace: str,
) -> TargetDefinitionIdentity:
    """Mint one exact definition under its owner-stable target key."""

    key = mint_target_key(target, identity_namespace=identity_namespace)
    return TargetDefinitionIdentity(
        target_definition_id=_typed_digest(
            _TARGET_DEFINITION_PREFIX,
            _target_definition_basis(key, target),
        ),
        target_key=key,
        target=target,
    )


def mint_target_list_snapshot(
    targets: Sequence[Target],
    *,
    identity_namespace: str,
) -> TargetListSnapshot:
    """Freeze validated Target values and their semantic row order."""

    if isinstance(targets, (str, bytes)):
        raise TargetIdentityError("target-list snapshot requires Target values")
    try:
        target_tuple = tuple(targets)
    except TypeError as exc:
        raise TargetIdentityError(
            "target-list snapshot requires an iterable of Target values"
        ) from exc
    _validate_target_list(target_tuple)
    namespace = _owner_normalized_text(
        identity_namespace,
        field="identity_namespace",
    )
    definitions = tuple(
        mint_target_definition_identity(
            target,
            identity_namespace=namespace,
        )
        for target in target_tuple
    )
    content_sha256 = _content_sha256(_target_list_content(namespace, definitions))
    return TargetListSnapshot(
        snapshot_id=f"{_TARGET_LIST_SNAPSHOT_PREFIX}{content_sha256}",
        content_sha256=content_sha256,
        identity_namespace=namespace,
        definitions=definitions,
    )


def resolve_target_definition(
    snapshot: TargetListSnapshot,
    target_label: str,
    *,
    expected_key: TargetKey | None = None,
    expected_definition: TargetDefinitionIdentity | None = None,
) -> ResolvedTargetDefinition:
    """Resolve one exact label and reject missing, ambiguous, or stale bindings."""

    if not isinstance(snapshot, TargetListSnapshot):
        raise TargetIdentityError(
            "target resolution requires an owner-minted TargetListSnapshot"
        )
    label = _owner_normalized_text(target_label, field="target_label")
    definition = _resolve_definition(snapshot, label)
    if expected_key is not None:
        if not isinstance(expected_key, TargetKey):
            raise TargetIdentityError("expected_key must be an owner-minted TargetKey")
        if definition.target_key != expected_key:
            raise TargetIdentityError(
                f"target label {label!r} resolved to a stale target key"
            )
    if expected_definition is not None:
        if not isinstance(expected_definition, TargetDefinitionIdentity):
            raise TargetIdentityError(
                "expected_definition must be an owner-minted TargetDefinitionIdentity"
            )
        if definition != expected_definition:
            raise TargetIdentityError(
                f"target label {label!r} resolved to a stale target definition"
            )
    return ResolvedTargetDefinition(
        definition=definition,
        snapshot=snapshot,
        lookup_label=label,
    )


def target_list_snapshot_payload(snapshot: TargetListSnapshot) -> dict[str, object]:
    """Return the exact immutable owner snapshot for a processing request."""

    if not isinstance(snapshot, TargetListSnapshot):
        raise TargetIdentityError(
            "processing input requires an owner-minted TargetListSnapshot"
        )
    return {
        "snapshot_id": snapshot.snapshot_id,
        "content_sha256": snapshot.content_sha256,
        **_target_list_content(snapshot.identity_namespace, snapshot.definitions),
    }


def _resolve_definition(
    snapshot: TargetListSnapshot,
    target_label: str,
) -> TargetDefinitionIdentity:
    matches = [
        definition
        for definition in snapshot.definitions
        if definition.target_key.target_label == target_label
    ]
    if not matches:
        raise TargetIdentityError(
            f"target label {target_label!r} is not owned by the target-list snapshot"
        )
    if len(matches) != 1:
        raise TargetIdentityError(f"target label {target_label!r} is ambiguous")
    return matches[0]


def _validated_definitions(
    definitions: object,
    *,
    identity_namespace: str,
) -> tuple[TargetDefinitionIdentity, ...]:
    if not isinstance(definitions, tuple) or not definitions:
        raise TargetIdentityError(
            "target-list snapshot definitions must be a non-empty tuple"
        )
    if any(
        not isinstance(definition, TargetDefinitionIdentity)
        for definition in definitions
    ):
        raise TargetIdentityError(
            "target-list snapshot requires owner-minted target definitions"
        )
    if any(
        definition.target_key.identity_namespace != identity_namespace
        for definition in definitions
    ):
        raise TargetIdentityError(
            "target-list definitions must share the snapshot owner namespace"
        )
    labels = [definition.target_key.target_label for definition in definitions]
    if len(set(labels)) != len(labels):
        raise TargetIdentityError("target-list snapshot has a duplicate target label")
    _validate_target_list(tuple(definition.target for definition in definitions))
    return definitions


def _validate_target_list(targets: tuple[Target, ...]) -> None:
    if not targets:
        raise TargetIdentityError(
            "target-list snapshot must contain at least one target"
        )
    for target in targets:
        _validate_target(target)
    labels = [target.label for target in targets]
    if len(set(labels)) != len(labels):
        raise TargetIdentityError("target-list snapshot has a duplicate target label")
    try:
        _validate_target_relationships(
            Path("<immutable-target-list>"),
            list(targets),
        )
    except ConfigError as exc:
        raise TargetIdentityError(
            "target list must satisfy the normalized Target relationship contract"
        ) from exc


def _validate_target(target: Target) -> None:
    if not isinstance(target, Target):
        raise TargetIdentityError(
            "target-list identity must be minted from owner-typed Target values"
        )
    if target.expected_rt_min is None:
        # Shared domain callers construct geometric targets directly;
        # CSV intake is a separate admission boundary.
        import math

        if (
            not target.label.strip()
            or not all(
                math.isfinite(v)
                for v in (target.mz, target.rt_min, target.rt_max, target.ppm_tol)
            )
            or target.mz <= 0
            or target.ppm_tol <= 0
            or target.rt_min < 0
            or target.rt_min >= target.rt_max
        ):
            raise TargetIdentityError("invalid geometric target definition")
        return
    row = {
        "label": target.label,
        "mz": str(target.mz),
        "expected_rt_min": str(target.expected_rt_min),
        "cid_nl_tag": target.cid_nl_tag,
        "role": "istd" if target.is_istd else "analyte",
        "istd_pair": target.istd_pair,
        "sample_applicability": target.sample_applicability,
    }
    try:
        parsed = _parse_target_row(
            Path("<immutable-target-definition>"),
            2,
            row,
        )
    except ConfigError as exc:
        raise TargetIdentityError(
            "target must satisfy the normalized Target configuration contract"
        ) from exc
    if parsed != target:
        raise TargetIdentityError(
            "target must be owner-normalized Target configuration content"
        )


def _target_definition_basis(
    target_key: TargetKey,
    target: Target,
) -> dict[str, object]:
    return {
        "schema": "target_definition_identity",
        "target_key_id": target_key.target_key_id,
        "target": asdict(target),
    }


def _target_list_content(
    identity_namespace: str,
    definitions: tuple[TargetDefinitionIdentity, ...],
) -> dict[str, object]:
    return {
        "schema": TARGET_LIST_SCHEMA_VERSION,
        "identity_namespace": identity_namespace,
        "definitions": [
            {
                "target_key_id": definition.target_key.target_key_id,
                "target_definition_id": definition.target_definition_id,
                "target": asdict(definition.target),
            }
            for definition in definitions
        ],
    }


def _typed_digest(prefix: str, content: Mapping[str, object]) -> str:
    return f"{prefix}{_content_sha256(content)}"


def _content_sha256(content: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(
            dict(content),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TargetIdentityError(
            "target identity content must be canonical JSON"
        ) from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _owner_normalized_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetIdentityError(f"{field} must be non-empty owner-normalized text")
    return value


def _validate_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise TargetIdentityError(f"{field} must be a lower-case SHA-256 digest")
    return value


__all__ = [
    "ResolvedTargetDefinition",
    "TARGET_LIST_SCHEMA_VERSION",
    "TargetDefinitionIdentity",
    "TargetIdentityError",
    "TargetKey",
    "TargetListSnapshot",
    "mint_target_definition_identity",
    "mint_target_key",
    "mint_target_list_snapshot",
    "resolve_target_definition",
    "target_list_snapshot_payload",
]
