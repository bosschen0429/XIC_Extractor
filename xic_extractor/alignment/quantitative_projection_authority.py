"""Package-owned authority for the canonical quantitative projection effect."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import InitVar, dataclass, field
from importlib.resources import files
from typing import Literal, cast, final

from xic_extractor.alignment.stage_policy_matrix import _canonical_semantic_bytes

QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1 = (
    "schema_version",
    "authority_scope",
    "policy_id",
    "effect",
    "consumer_id",
    "signal_processing_profile_id",
    "decision",
    "requires_existing_registered_write_scope",
    "allowed_registered_writer_scope_ids",
    "may_add_write_rows",
    "may_change_write_decision",
    "may_touch_matrix",
    "may_change_quant_output",
    "may_change_workbook",
    "may_change_selected_peak",
    "may_change_selected_area",
    "may_change_counted_detection",
    "activation_summary_sha256",
    "authority_id",
)

QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1 = (
    "schema_version",
    "effect_id",
    "status",
    "package_authority_artifact",
    "package_authority_sha256",
    "allowed_registered_writer_scope_ids",
    "activation_summary_artifact",
    "activation_summary_sha256",
    "current_effect_cell_count",
    "manifest_content_id",
)

_AUTHORITY_ID_PREFIX = "quantitative-projection-authority.v1:"
_AUTHORITY_RESOURCE_PACKAGE = "xic_extractor.alignment"
_AUTHORITY_RESOURCE_DIRECTORY = "data"
_AUTHORITY_RESOURCE_NAME = "issue256_quantitative_projection_authority_v1.json"
_AUTHORITY_MINT_TOKEN = object()
_MANIFEST_MINT_TOKEN = object()

_MANIFEST_SCHEMA_VERSION = "quantitative_projection_authority_manifest_v1"
_MANIFEST_EFFECT_ID = "untargeted_owner_backfill_canonical_quantitative_projection_v1"
_PACKAGE_AUTHORITY_ARTIFACT = (
    "xic_extractor/alignment/data/issue256_quantitative_projection_authority_v1.json"
)
_ACTIVATION_SUMMARY_ARTIFACT = (
    "docs/superpowers/validation/"
    "issue256_untargeted_owner_backfill_canonical_quantitative_activation_v1/"
    "activation_summary.json"
)
_MANIFEST_CONTENT_ID_PREFIX = "quantitative-projection-authority-manifest.v1:"

_EXACT_TEXT_FIELDS = {
    "schema_version": "quantitative_projection_authority_v1",
    "authority_scope": (
        "untargeted_owner_backfill_canonical_quantitative_projection_v1"
    ),
    "policy_id": "activation-base-owner-backfill-policy.v1",
    "effect": "reproject_existing_matrix_value_on_existing_authorized_cells",
    "consumer_id": (
        "xic_extractor.alignment.pipeline.run_alignment:production-equivalent"
    ),
    "signal_processing_profile_id": (
        "signal-profile.v1:"
        "bcefd76d05bc26a0ec9f333fdb76427ccb2133c1946f5ec711916e85efc06de2"
    ),
}
_ALLOWED_REGISTERED_WRITER_SCOPE_IDS = (
    "backfill_policy_write_ready_rows",
)
_EXACT_BOOLEAN_FIELDS = {
    "requires_existing_registered_write_scope": True,
    "may_add_write_rows": False,
    "may_change_write_decision": False,
    "may_touch_matrix": True,
    "may_change_quant_output": True,
    "may_change_workbook": True,
    "may_change_selected_peak": False,
    "may_change_selected_area": True,
    "may_change_counted_detection": False,
}
_DECISIONS = frozenset({"HOLD_PENDING_VALIDATION", "ACTIVE"})
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

_HOLD_MESSAGE = "HOLD: quantitative_projection_authority_pending_validation"
_ACTIVE_MEMBERSHIP_ERROR = "active_membership_owner_unavailable"


class QuantitativeProjectionAuthorityError(ValueError):
    """The packaged quantitative projection authority is invalid or unusable."""


class QuantitativeProjectionAuthorityHoldError(QuantitativeProjectionAuthorityError):
    """The valid package authority deliberately holds the product route."""


@final
@dataclass(frozen=True, slots=True)
class QuantitativeProjectionAuthorityV1:
    """Immutable authority content minted only by this package-data owner."""

    schema_version: str
    authority_scope: str
    policy_id: str
    effect: str
    consumer_id: str
    signal_processing_profile_id: str
    decision: Literal["HOLD_PENDING_VALIDATION", "ACTIVE"]
    requires_existing_registered_write_scope: bool
    allowed_registered_writer_scope_ids: tuple[str, ...]
    may_add_write_rows: bool
    may_change_write_decision: bool
    may_touch_matrix: bool
    may_change_quant_output: bool
    may_change_workbook: bool
    may_change_selected_peak: bool
    may_change_selected_area: bool
    may_change_counted_detection: bool
    activation_summary_sha256: str
    authority_id: str
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("QuantitativeProjectionAuthorityV1 is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _AUTHORITY_MINT_TOKEN:
            raise QuantitativeProjectionAuthorityError(
                "quantitative projection authority must be minted by its owner"
            )


@final
@dataclass(frozen=True, slots=True)
class QuantitativeProjectionAuthorityManifestV1:
    """Immutable dependent-effect manifest minted only by this package owner."""

    schema_version: str
    effect_id: str
    status: Literal["HOLD_PENDING_VALIDATION", "ACTIVE"]
    package_authority_artifact: str
    package_authority_sha256: str
    allowed_registered_writer_scope_ids: tuple[str, ...]
    activation_summary_artifact: str
    activation_summary_sha256: str
    current_effect_cell_count: int
    manifest_content_id: str
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("QuantitativeProjectionAuthorityManifestV1 is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MANIFEST_MINT_TOKEN:
            raise QuantitativeProjectionAuthorityError(
                "quantitative projection authority manifest must be minted by its owner"
            )


def load_quantitative_projection_authority_for_diagnostic() -> (
    QuantitativeProjectionAuthorityV1
):
    """Strictly load HOLD or ACTIVE package content without product authority."""

    return _rehydrate_quantitative_projection_authority(_read_package_authority_bytes())


def rehydrate_quantitative_projection_authority_manifest(
    payload: object,
) -> QuantitativeProjectionAuthorityManifestV1:
    """Rehydrate one exact dependent-effect HOLD manifest from untrusted content."""

    if type(payload) is not dict:
        raise QuantitativeProjectionAuthorityError(
            "quantitative projection authority manifest must be one ordered mapping"
        )
    content = cast(dict[str, object], payload)
    if tuple(content) != QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1:
        raise QuantitativeProjectionAuthorityError(
            "quantitative projection authority manifest fields are missing, "
            "extra, or reordered"
        )

    _validate_manifest_exact_content(content)
    expected_content_id = _manifest_content_id_from_content(content)
    if content["manifest_content_id"] != expected_content_id:
        raise QuantitativeProjectionAuthorityError(
            "manifest_content_id does not match canonical manifest content"
        )

    package_raw = _read_package_authority_bytes()
    package_authority = _rehydrate_quantitative_projection_authority(package_raw)
    expected_package_sha256 = hashlib.sha256(package_raw).hexdigest()
    if content["package_authority_sha256"] != expected_package_sha256:
        raise QuantitativeProjectionAuthorityError(
            "package_authority_sha256 does not match package authority bytes"
        )
    if content["status"] != package_authority.decision:
        raise QuantitativeProjectionAuthorityError(
            "manifest status does not match package authority decision"
        )
    if tuple(cast(list[str], content["allowed_registered_writer_scope_ids"])) != (
        package_authority.allowed_registered_writer_scope_ids
    ):
        raise QuantitativeProjectionAuthorityError(
            "manifest writer scopes do not match package authority"
        )
    if content["activation_summary_sha256"] != (
        package_authority.activation_summary_sha256
    ):
        raise QuantitativeProjectionAuthorityError(
            "manifest activation summary SHA-256 does not match package authority"
        )

    if content["status"] == "ACTIVE":
        raise QuantitativeProjectionAuthorityError(_ACTIVE_MEMBERSHIP_ERROR)
    return _mint_quantitative_projection_authority_manifest(content)


def render_quantitative_projection_authority_manifest_bytes(
    manifest: QuantitativeProjectionAuthorityManifestV1,
) -> bytes:
    """Render one owner-minted manifest as exact canonical UTF-8/LF bytes."""

    if type(manifest) is not QuantitativeProjectionAuthorityManifestV1:
        raise QuantitativeProjectionAuthorityError(
            "manifest renderer requires an owner-minted exact manifest"
        )
    try:
        content = _manifest_content_from_value(manifest)
    except AttributeError as exc:
        raise QuantitativeProjectionAuthorityError(
            "manifest renderer requires an owner-minted exact manifest"
        ) from exc
    _validate_manifest_exact_content(content)
    if content["manifest_content_id"] != _manifest_content_id_from_content(content):
        raise QuantitativeProjectionAuthorityError(
            "manifest_content_id does not match canonical manifest content"
        )
    if content["status"] == "ACTIVE":
        raise QuantitativeProjectionAuthorityError(_ACTIVE_MEMBERSHIP_ERROR)
    return (
        _canonical_projection_bytes(
            content,
            QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1,
        )
        + b"\n"
    )


def require_product_quantitative_projection_authority(
    *,
    consumer_id: str,
    signal_processing_profile_id: str,
) -> QuantitativeProjectionAuthorityV1:
    """Fail closed unless the package can authorize the exact product ingress."""

    if type(consumer_id) is not str:
        raise QuantitativeProjectionAuthorityError("consumer_id must be a string")
    if type(signal_processing_profile_id) is not str:
        raise QuantitativeProjectionAuthorityError(
            "signal_processing_profile_id must be a string"
        )

    authority = load_quantitative_projection_authority_for_diagnostic()
    if authority.consumer_id != consumer_id:
        raise QuantitativeProjectionAuthorityError("consumer_id_mismatch")
    if authority.signal_processing_profile_id != signal_processing_profile_id:
        raise QuantitativeProjectionAuthorityError(
            "signal_processing_profile_id_mismatch"
        )
    if authority.decision == "HOLD_PENDING_VALIDATION":
        raise QuantitativeProjectionAuthorityHoldError(_HOLD_MESSAGE)

    # The separately owned registered-writer membership contract does not exist.
    # An ACTIVE package therefore remains unusable on the product route.
    raise QuantitativeProjectionAuthorityError(_ACTIVE_MEMBERSHIP_ERROR)


def _read_package_authority_bytes() -> bytes:
    try:
        resource_directory = files(_AUTHORITY_RESOURCE_PACKAGE).joinpath(
            _AUTHORITY_RESOURCE_DIRECTORY
        )
        casefold_name = _AUTHORITY_RESOURCE_NAME.casefold()
        matches = tuple(
            entry
            for entry in resource_directory.iterdir()
            if entry.name.casefold() == casefold_name
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError) as exc:
        raise QuantitativeProjectionAuthorityError(
            "package_authority_resource_unavailable"
        ) from exc

    if not matches:
        raise QuantitativeProjectionAuthorityError(
            "package_authority_resource_unavailable"
        )
    if len(matches) != 1 or matches[0].name != _AUTHORITY_RESOURCE_NAME:
        raise QuantitativeProjectionAuthorityError(
            "package_authority_resource_ambiguous"
        )
    resource = matches[0]
    try:
        if not resource.is_file():
            raise QuantitativeProjectionAuthorityError(
                "package_authority_resource_unavailable"
            )
        return resource.read_bytes()
    except OSError as exc:
        raise QuantitativeProjectionAuthorityError(
            "package_authority_resource_unavailable"
        ) from exc


def _rehydrate_quantitative_projection_authority(
    raw: bytes,
) -> QuantitativeProjectionAuthorityV1:
    if type(raw) is not bytes:
        raise QuantitativeProjectionAuthorityError(
            "package authority content must be bytes"
        )
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QuantitativeProjectionAuthorityError(
            "package authority content must be UTF-8"
        ) from exc
    try:
        payload = json.loads(
            decoded,
            object_pairs_hook=_mapping_without_duplicate_fields,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except QuantitativeProjectionAuthorityError:
        raise
    except (ValueError, RecursionError) as exc:
        raise QuantitativeProjectionAuthorityError(
            "package authority content must be valid JSON"
        ) from exc
    if type(payload) is not dict:
        raise QuantitativeProjectionAuthorityError(
            "package authority content must be one JSON object"
        )
    content = cast(dict[str, object], payload)

    actual_fields = tuple(content)
    if actual_fields != QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1:
        raise QuantitativeProjectionAuthorityError(
            "package authority fields are missing, extra, or reordered"
        )
    if _canonical_package_bytes(content) != raw:
        raise QuantitativeProjectionAuthorityError(
            "package authority content is not canonical UTF-8/LF JSON"
        )

    _validate_exact_content(content)
    expected_authority_id = _authority_id_from_content(content)
    if content["authority_id"] != expected_authority_id:
        raise QuantitativeProjectionAuthorityError(
            "authority_id does not match canonical authority content"
        )
    return _mint_quantitative_projection_authority(content)


def _validate_exact_content(content: dict[str, object]) -> None:
    for field_name, expected_text in _EXACT_TEXT_FIELDS.items():
        value = content[field_name]
        if type(value) is not str or value != expected_text:
            raise QuantitativeProjectionAuthorityError(
                f"invalid package authority field: {field_name}"
            )

    decision = content["decision"]
    if type(decision) is not str or decision not in _DECISIONS:
        raise QuantitativeProjectionAuthorityError(
            "invalid package authority field: decision"
        )

    scope_ids = content["allowed_registered_writer_scope_ids"]
    if type(scope_ids) is not list or tuple(scope_ids) != (
        _ALLOWED_REGISTERED_WRITER_SCOPE_IDS
    ):
        raise QuantitativeProjectionAuthorityError(
            "invalid package authority field: allowed_registered_writer_scope_ids"
        )
    if any(type(scope_id) is not str for scope_id in scope_ids):
        raise QuantitativeProjectionAuthorityError(
            "invalid package authority scope ID type"
        )

    for field_name, expected_bool in _EXACT_BOOLEAN_FIELDS.items():
        value = content[field_name]
        if type(value) is not bool or value is not expected_bool:
            raise QuantitativeProjectionAuthorityError(
                f"invalid package authority field: {field_name}"
            )

    summary_sha256 = content["activation_summary_sha256"]
    if type(summary_sha256) is not str:
        raise QuantitativeProjectionAuthorityError(
            "activation_summary_sha256 must be a string"
        )
    if decision == "HOLD_PENDING_VALIDATION" and summary_sha256 != "":
        raise QuantitativeProjectionAuthorityError(
            "HOLD authority requires an empty activation summary SHA-256"
        )
    if decision == "ACTIVE" and _LOWERCASE_SHA256.fullmatch(summary_sha256) is None:
        raise QuantitativeProjectionAuthorityError(
            "ACTIVE authority requires a lowercase activation summary SHA-256"
        )

    authority_id = content["authority_id"]
    if type(authority_id) is not str:
        raise QuantitativeProjectionAuthorityError("authority_id must be a string")


def _validate_manifest_exact_content(content: dict[str, object]) -> None:
    exact_text_fields = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "effect_id": _MANIFEST_EFFECT_ID,
        "package_authority_artifact": _PACKAGE_AUTHORITY_ARTIFACT,
    }
    for field_name, expected_text in exact_text_fields.items():
        value = content[field_name]
        if type(value) is not str or value != expected_text:
            raise QuantitativeProjectionAuthorityError(
                "invalid quantitative projection authority manifest field: "
                f"{field_name}"
            )

    status = content["status"]
    if type(status) is not str or status not in _DECISIONS:
        raise QuantitativeProjectionAuthorityError(
            "invalid quantitative projection authority manifest field: status"
        )

    package_sha256 = content["package_authority_sha256"]
    if (
        type(package_sha256) is not str
        or _LOWERCASE_SHA256.fullmatch(package_sha256) is None
    ):
        raise QuantitativeProjectionAuthorityError(
            "package_authority_sha256 must be lowercase SHA-256"
        )

    scope_ids = content["allowed_registered_writer_scope_ids"]
    if type(scope_ids) is not list or tuple(scope_ids) != (
        _ALLOWED_REGISTERED_WRITER_SCOPE_IDS
    ):
        raise QuantitativeProjectionAuthorityError(
            "invalid quantitative projection authority manifest writer scopes"
        )
    if any(type(scope_id) is not str for scope_id in scope_ids):
        raise QuantitativeProjectionAuthorityError(
            "invalid quantitative projection authority manifest writer scope type"
        )

    activation_artifact = content["activation_summary_artifact"]
    activation_sha256 = content["activation_summary_sha256"]
    effect_cell_count = content["current_effect_cell_count"]
    if type(activation_artifact) is not str:
        raise QuantitativeProjectionAuthorityError(
            "activation_summary_artifact must be a string"
        )
    if type(activation_sha256) is not str:
        raise QuantitativeProjectionAuthorityError(
            "activation_summary_sha256 must be a string"
        )
    if type(effect_cell_count) is not int:
        raise QuantitativeProjectionAuthorityError(
            "current_effect_cell_count must be an integer"
        )

    if status == "HOLD_PENDING_VALIDATION":
        if activation_artifact != "" or activation_sha256 != "":
            raise QuantitativeProjectionAuthorityError(
                "HOLD manifest requires empty activation summary bindings"
            )
        if effect_cell_count != 0:
            raise QuantitativeProjectionAuthorityError(
                "HOLD manifest requires current_effect_cell_count zero"
            )
    else:
        if activation_artifact != _ACTIVATION_SUMMARY_ARTIFACT:
            raise QuantitativeProjectionAuthorityError(
                "ACTIVE manifest requires the exact activation summary artifact"
            )
        if _LOWERCASE_SHA256.fullmatch(activation_sha256) is None:
            raise QuantitativeProjectionAuthorityError(
                "ACTIVE manifest requires a lowercase activation summary SHA-256"
            )
        if effect_cell_count <= 0:
            raise QuantitativeProjectionAuthorityError(
                "ACTIVE manifest requires a positive current_effect_cell_count"
            )

    manifest_content_id = content["manifest_content_id"]
    if type(manifest_content_id) is not str:
        raise QuantitativeProjectionAuthorityError(
            "manifest_content_id must be a string"
        )


def _mint_quantitative_projection_authority(
    content: dict[str, object],
) -> QuantitativeProjectionAuthorityV1:
    return QuantitativeProjectionAuthorityV1(
        schema_version=cast(str, content["schema_version"]),
        authority_scope=cast(str, content["authority_scope"]),
        policy_id=cast(str, content["policy_id"]),
        effect=cast(str, content["effect"]),
        consumer_id=cast(str, content["consumer_id"]),
        signal_processing_profile_id=cast(str, content["signal_processing_profile_id"]),
        decision=cast(
            Literal["HOLD_PENDING_VALIDATION", "ACTIVE"], content["decision"]
        ),
        requires_existing_registered_write_scope=cast(
            bool, content["requires_existing_registered_write_scope"]
        ),
        allowed_registered_writer_scope_ids=tuple(
            cast(list[str], content["allowed_registered_writer_scope_ids"])
        ),
        may_add_write_rows=cast(bool, content["may_add_write_rows"]),
        may_change_write_decision=cast(bool, content["may_change_write_decision"]),
        may_touch_matrix=cast(bool, content["may_touch_matrix"]),
        may_change_quant_output=cast(bool, content["may_change_quant_output"]),
        may_change_workbook=cast(bool, content["may_change_workbook"]),
        may_change_selected_peak=cast(bool, content["may_change_selected_peak"]),
        may_change_selected_area=cast(bool, content["may_change_selected_area"]),
        may_change_counted_detection=cast(
            bool, content["may_change_counted_detection"]
        ),
        activation_summary_sha256=cast(str, content["activation_summary_sha256"]),
        authority_id=cast(str, content["authority_id"]),
        _mint_token=_AUTHORITY_MINT_TOKEN,
    )


def _mint_quantitative_projection_authority_manifest(
    content: dict[str, object],
) -> QuantitativeProjectionAuthorityManifestV1:
    return QuantitativeProjectionAuthorityManifestV1(
        schema_version=cast(str, content["schema_version"]),
        effect_id=cast(str, content["effect_id"]),
        status=cast(Literal["HOLD_PENDING_VALIDATION", "ACTIVE"], content["status"]),
        package_authority_artifact=cast(str, content["package_authority_artifact"]),
        package_authority_sha256=cast(str, content["package_authority_sha256"]),
        allowed_registered_writer_scope_ids=tuple(
            cast(list[str], content["allowed_registered_writer_scope_ids"])
        ),
        activation_summary_artifact=cast(str, content["activation_summary_artifact"]),
        activation_summary_sha256=cast(str, content["activation_summary_sha256"]),
        current_effect_cell_count=cast(int, content["current_effect_cell_count"]),
        manifest_content_id=cast(str, content["manifest_content_id"]),
        _mint_token=_MANIFEST_MINT_TOKEN,
    )


def _manifest_content_from_value(
    manifest: QuantitativeProjectionAuthorityManifestV1,
) -> dict[str, object]:
    scope_ids = manifest.allowed_registered_writer_scope_ids
    if type(scope_ids) is not tuple:
        raise QuantitativeProjectionAuthorityError(
            "manifest renderer requires exact tuple writer scopes"
        )
    return {
        "schema_version": manifest.schema_version,
        "effect_id": manifest.effect_id,
        "status": manifest.status,
        "package_authority_artifact": manifest.package_authority_artifact,
        "package_authority_sha256": manifest.package_authority_sha256,
        "allowed_registered_writer_scope_ids": list(scope_ids),
        "activation_summary_artifact": manifest.activation_summary_artifact,
        "activation_summary_sha256": manifest.activation_summary_sha256,
        "current_effect_cell_count": manifest.current_effect_cell_count,
        "manifest_content_id": manifest.manifest_content_id,
    }


def _authority_id_from_content(content: dict[str, object]) -> str:
    digest = hashlib.sha256(
        _canonical_projection_bytes(
            content,
            QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1[:-1],
        )
    ).hexdigest()
    return f"{_AUTHORITY_ID_PREFIX}{digest}"


def _canonical_package_bytes(content: dict[str, object]) -> bytes:
    return (
        _canonical_projection_bytes(
            content,
            QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1,
        )
        + b"\n"
    )


def _manifest_content_id_from_content(content: dict[str, object]) -> str:
    digest = hashlib.sha256(
        _canonical_projection_bytes(
            content,
            QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1[:-1],
        )
    ).hexdigest()
    return f"{_MANIFEST_CONTENT_ID_PREFIX}{digest}"


def _canonical_projection_bytes(
    content: dict[str, object],
    field_names: tuple[str, ...],
) -> bytes:
    projected = {
        field_name: (
            tuple(cast(list[object], content[field_name]))
            if field_name == "allowed_registered_writer_scope_ids"
            and type(content[field_name]) is list
            else content[field_name]
        )
        for field_name in field_names
    }
    try:
        return _canonical_semantic_bytes(projected, field_names=field_names)
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise QuantitativeProjectionAuthorityError(
            "package authority content uses an unsupported JSON value"
        ) from exc


def _mapping_without_duplicate_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise QuantitativeProjectionAuthorityError(
                f"package authority content has duplicate field: {key}"
            )
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> object:
    raise QuantitativeProjectionAuthorityError(
        f"package authority content has nonstandard JSON constant: {value}"
    )


__all__ = (
    "QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1",
    "QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1",
    "QuantitativeProjectionAuthorityError",
    "QuantitativeProjectionAuthorityHoldError",
    "QuantitativeProjectionAuthorityManifestV1",
    "QuantitativeProjectionAuthorityV1",
    "load_quantitative_projection_authority_for_diagnostic",
    "rehydrate_quantitative_projection_authority_manifest",
    "render_quantitative_projection_authority_manifest_bytes",
    "require_product_quantitative_projection_authority",
)
