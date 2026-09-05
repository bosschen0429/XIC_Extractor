from __future__ import annotations

import hashlib
import json
import pickle
from dataclasses import FrozenInstanceError, fields, replace
from importlib.resources import files
from pathlib import Path

import pytest

from xic_extractor.alignment import (
    quantitative_projection_authority as authority_module,
)
from xic_extractor.alignment.quantitative_projection_authority import (
    QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1,
    QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1,
    QuantitativeProjectionAuthorityError,
    QuantitativeProjectionAuthorityHoldError,
    QuantitativeProjectionAuthorityManifestV1,
    load_quantitative_projection_authority_for_diagnostic,
    rehydrate_quantitative_projection_authority_manifest,
    render_quantitative_projection_authority_manifest_bytes,
    require_product_quantitative_projection_authority,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE_ID,
)

_RESOURCE_NAME = "quantitative_projection_authority_v1.json"
_CONSUMER_ID = "xic_extractor.alignment.pipeline.run_alignment:production-equivalent"
_PROFILE_ID = (
    "signal-profile.v1:bcefd76d05bc26a0ec9f333fdb76427ccb2133c1946f5ec711916e85efc06de2"
)
_AUTHORITY_ID_PREFIX = "quantitative-projection-authority.v1:"
_MANIFEST_ID_PREFIX = "quantitative-projection-authority-manifest.v1:"
_PACKAGE_AUTHORITY_ARTIFACT = (
    "xic_extractor/alignment/data/quantitative_projection_authority_v1.json"
)
_ACTIVATION_SUMMARY_ARTIFACT = (
    "docs/superpowers/validation/"
    "untargeted_owner_backfill_canonical_quantitative_activation_v1/"
    "activation_summary.json"
)
_MANIFEST_PATH = Path(
    "docs/superpowers/schemas/quantitative_projection_authority_manifest.v1.json"
)


def _package_bytes() -> bytes:
    return (
        files("xic_extractor.alignment").joinpath("data", _RESOURCE_NAME).read_bytes()
    )


def _payload(raw: bytes | None = None) -> dict[str, object]:
    parsed = json.loads((raw or _package_bytes()).decode("utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _canonical_package_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _reseal(payload: dict[str, object]) -> bytes:
    basis = {
        field_name: payload[field_name]
        for field_name in QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1[:-1]
    }
    digest = hashlib.sha256(
        json.dumps(
            basis,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload["authority_id"] = f"{_AUTHORITY_ID_PREFIX}{digest}"
    return _canonical_package_bytes(payload)


def _manifest_payload(*, package_raw: bytes | None = None) -> dict[str, object]:
    raw = package_raw or _package_bytes()
    payload: dict[str, object] = {
        "schema_version": "quantitative_projection_authority_manifest_v1",
        "effect_id": ("untargeted_owner_backfill_canonical_quantitative_projection_v1"),
        "status": "HOLD_PENDING_VALIDATION",
        "package_authority_artifact": _PACKAGE_AUTHORITY_ARTIFACT,
        "package_authority_sha256": hashlib.sha256(raw).hexdigest(),
        "allowed_registered_writer_scope_ids": [
            "backfill_policy_write_ready_rows",
        ],
        "activation_summary_artifact": "",
        "activation_summary_sha256": "",
        "current_effect_cell_count": 0,
        "manifest_content_id": "",
    }
    _reseal_manifest(payload)
    return payload


def _reseal_manifest(payload: dict[str, object]) -> None:
    basis = {
        field_name: payload[field_name]
        for field_name in QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1[:-1]
    }
    digest = hashlib.sha256(
        json.dumps(
            basis,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload["manifest_content_id"] = f"{_MANIFEST_ID_PREFIX}{digest}"


def test_package_hold_authority_has_exact_contract() -> None:
    authority = load_quantitative_projection_authority_for_diagnostic()

    assert QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1 == (
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
    assert authority.schema_version == "quantitative_projection_authority_v1"
    assert authority.authority_scope == (
        "untargeted_owner_backfill_canonical_quantitative_projection_v1"
    )
    assert authority.policy_id == "activation-base-owner-backfill-policy.v1"
    assert authority.effect == (
        "reproject_existing_matrix_value_on_existing_authorized_cells"
    )
    assert authority.consumer_id == _CONSUMER_ID
    assert authority.signal_processing_profile_id == _PROFILE_ID
    assert (
        authority.signal_processing_profile_id == DEFAULT_SIGNAL_PROCESSING_PROFILE_ID
    )
    assert authority.decision == "HOLD_PENDING_VALIDATION"
    assert authority.requires_existing_registered_write_scope is True
    assert authority.allowed_registered_writer_scope_ids == (
        "backfill_policy_write_ready_rows",
    )
    assert authority.may_add_write_rows is False
    assert authority.may_change_write_decision is False
    assert authority.may_touch_matrix is True
    assert authority.may_change_quant_output is True
    assert authority.may_change_workbook is True
    assert authority.may_change_selected_peak is False
    assert authority.may_change_selected_area is True
    assert authority.may_change_counted_detection is False
    assert authority.activation_summary_sha256 == ""
    assert authority.authority_id == (
        "quantitative-projection-authority.v1:"
        "ca4ed4afd15bd5649c97ad91568e34b7b60e1437b2d87d7b9b75be60ce56965f"
    )
    assert hashlib.sha256(_package_bytes()).hexdigest() == (
        "15b74bc79dc9501210acd5f761c86b4a7128db83e74a62bf3e97b7beb1572ed5"
    )


def test_package_authority_bytes_are_canonical_utf8_lf_without_bom() -> None:
    raw = _package_bytes()

    assert raw == _canonical_package_bytes(_payload(raw))
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize(
    "invalid_bytes",
    [
        b"\xef\xbb\xbf" + _package_bytes(),
        _package_bytes().rstrip(b"\n"),
        _package_bytes().rstrip(b"\n") + b"\r\n",
        b"\xff",
    ],
)
def test_noncanonical_or_non_utf8_package_bytes_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    invalid_bytes: bytes,
) -> None:
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: invalid_bytes,
    )

    with pytest.raises(QuantitativeProjectionAuthorityError):
        load_quantitative_projection_authority_for_diagnostic()


def test_authority_is_immutable_final_and_owner_minted() -> None:
    authority = load_quantitative_projection_authority_for_diagnostic()

    with pytest.raises(FrozenInstanceError):
        authority.decision = "ACTIVE"  # type: ignore[misc]
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="must be minted by its owner",
    ):
        replace(authority, _mint_token=object())
    with pytest.raises(TypeError, match="is final"):
        type("ForgedQuantitativeAuthority", (type(authority),), {})


def test_valid_hold_is_diagnostic_but_holds_the_product_route() -> None:
    diagnostic = load_quantitative_projection_authority_for_diagnostic()
    assert diagnostic.decision == "HOLD_PENDING_VALIDATION"

    with pytest.raises(QuantitativeProjectionAuthorityHoldError) as exc_info:
        require_product_quantitative_projection_authority(
            consumer_id=_CONSUMER_ID,
            signal_processing_profile_id=_PROFILE_ID,
        )

    assert str(exc_info.value) == (
        "HOLD: quantitative_projection_authority_pending_validation"
    )
    assert issubclass(
        QuantitativeProjectionAuthorityHoldError,
        QuantitativeProjectionAuthorityError,
    )
    assert issubclass(QuantitativeProjectionAuthorityError, ValueError)


@pytest.mark.parametrize(
    ("consumer_id", "profile_id", "message"),
    [
        ("crossed-consumer", _PROFILE_ID, "consumer_id_mismatch"),
        (_CONSUMER_ID, "crossed-profile", "signal_processing_profile_id_mismatch"),
    ],
)
def test_product_route_rejects_consumer_or_profile_mismatch(
    consumer_id: str,
    profile_id: str,
    message: str,
) -> None:
    with pytest.raises(QuantitativeProjectionAuthorityError, match=message) as exc_info:
        require_product_quantitative_projection_authority(
            consumer_id=consumer_id,
            signal_processing_profile_id=profile_id,
        )

    assert not isinstance(exc_info.value, QuantitativeProjectionAuthorityHoldError)


def test_valid_active_is_diagnostic_only_until_membership_owner_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _payload()
    active["decision"] = "ACTIVE"
    active["activation_summary_sha256"] = "a" * 64
    active_bytes = _reseal(active)
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: active_bytes,
    )

    diagnostic = load_quantitative_projection_authority_for_diagnostic()
    assert diagnostic.decision == "ACTIVE"
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="^active_membership_owner_unavailable$",
    ) as exc_info:
        require_product_quantitative_projection_authority(
            consumer_id=_CONSUMER_ID,
            signal_processing_profile_id=_PROFILE_ID,
        )
    assert not isinstance(exc_info.value, QuantitativeProjectionAuthorityHoldError)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("schema_version", 1),
        ("decision", "UNKNOWN"),
        ("requires_existing_registered_write_scope", 1),
        ("may_change_selected_area", False),
        ("allowed_registered_writer_scope_ids", "not-an-array"),
    ],
)
def test_strict_rehydration_rejects_wrong_types_and_literals(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    invalid_value: object,
) -> None:
    invalid = _payload()
    invalid[field_name] = invalid_value
    invalid_bytes = _reseal(invalid)
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: invalid_bytes,
    )

    with pytest.raises(QuantitativeProjectionAuthorityError):
        load_quantitative_projection_authority_for_diagnostic()


@pytest.mark.parametrize("mutation", ["missing", "extra", "reordered", "duplicate"])
def test_strict_rehydration_rejects_field_shape_and_order(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    payload = _payload()
    if mutation == "missing":
        del payload["policy_id"]
        invalid_bytes = _canonical_package_bytes(payload)
    elif mutation == "extra":
        payload["unexpected"] = "field"
        invalid_bytes = _canonical_package_bytes(payload)
    elif mutation == "reordered":
        invalid_bytes = _canonical_package_bytes(dict(reversed(payload.items())))
    else:
        original = _package_bytes().decode("utf-8")
        invalid_bytes = original.replace(
            '{"schema_version":"quantitative_projection_authority_v1",',
            (
                '{"schema_version":"quantitative_projection_authority_v1",'
                '"schema_version":"quantitative_projection_authority_v1",'
            ),
            1,
        ).encode("utf-8")
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: invalid_bytes,
    )

    with pytest.raises(QuantitativeProjectionAuthorityError):
        load_quantitative_projection_authority_for_diagnostic()


def test_rehydration_rejects_unsealed_and_resealed_invalid_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsealed = _payload()
    unsealed["authority_id"] = f"{_AUTHORITY_ID_PREFIX}{'0' * 64}"
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: _canonical_package_bytes(unsealed),
    )
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="authority_id does not match",
    ):
        load_quantitative_projection_authority_for_diagnostic()

    resealed = _payload()
    resealed["consumer_id"] = "crossed-consumer"
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: _reseal(resealed),
    )
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="invalid package authority field: consumer_id",
    ):
        load_quantitative_projection_authority_for_diagnostic()


@pytest.mark.parametrize(
    ("decision", "summary_sha256"),
    [
        ("HOLD_PENDING_VALIDATION", "a" * 64),
        ("ACTIVE", ""),
        ("ACTIVE", "A" * 64),
    ],
)
def test_summary_sha_contract_is_decision_specific(
    monkeypatch: pytest.MonkeyPatch,
    decision: str,
    summary_sha256: str,
) -> None:
    invalid = _payload()
    invalid["decision"] = decision
    invalid["activation_summary_sha256"] = summary_sha256
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: _reseal(invalid),
    )

    with pytest.raises(QuantitativeProjectionAuthorityError):
        load_quantitative_projection_authority_for_diagnostic()


def test_invalid_package_is_rejection_not_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: b"{}\n",
    )

    with pytest.raises(QuantitativeProjectionAuthorityError) as exc_info:
        require_product_quantitative_projection_authority(
            consumer_id=_CONSUMER_ID,
            signal_processing_profile_id=_PROFILE_ID,
        )
    assert not isinstance(exc_info.value, QuantitativeProjectionAuthorityHoldError)


def test_hold_manifest_rehydrates_exact_package_bound_contract() -> None:
    manifest = rehydrate_quantitative_projection_authority_manifest(_manifest_payload())

    assert QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1 == (
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
    assert isinstance(manifest, QuantitativeProjectionAuthorityManifestV1)
    assert tuple(field.name for field in fields(manifest)) == (
        QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1
    )
    assert authority_module.__all__ == (
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
    assert manifest.schema_version == ("quantitative_projection_authority_manifest_v1")
    assert manifest.effect_id == (
        "untargeted_owner_backfill_canonical_quantitative_projection_v1"
    )
    assert manifest.status == "HOLD_PENDING_VALIDATION"
    assert manifest.package_authority_artifact == _PACKAGE_AUTHORITY_ARTIFACT
    assert (
        manifest.package_authority_sha256
        == hashlib.sha256(_package_bytes()).hexdigest()
    )
    assert manifest.allowed_registered_writer_scope_ids == (
        "backfill_policy_write_ready_rows",
    )
    assert manifest.activation_summary_artifact == ""
    assert manifest.activation_summary_sha256 == ""
    assert manifest.current_effect_cell_count == 0
    assert manifest.manifest_content_id.startswith(_MANIFEST_ID_PREFIX)


def test_manifest_renderer_exactly_matches_current_canonical_file() -> None:
    raw = _MANIFEST_PATH.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    manifest = rehydrate_quantitative_projection_authority_manifest(payload)

    rendered = render_quantitative_projection_authority_manifest_bytes(manifest)

    assert rendered == raw
    assert rendered.endswith(b"\n")
    assert not rendered.endswith(b"\n\n")
    assert b"\r" not in rendered
    assert not rendered.startswith(b"\xef\xbb\xbf")


def test_manifest_renderer_rejects_foreign_and_subclass_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="requires an owner-minted exact manifest",
    ):
        render_quantitative_projection_authority_manifest_bytes(
            _manifest_payload()  # type: ignore[arg-type]
        )

    empty_exact: QuantitativeProjectionAuthorityManifestV1 = object.__new__(
        QuantitativeProjectionAuthorityManifestV1
    )
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="requires an owner-minted exact manifest",
    ):
        render_quantitative_projection_authority_manifest_bytes(empty_exact)

    monkeypatch.setattr(
        QuantitativeProjectionAuthorityManifestV1,
        "__init_subclass__",
        classmethod(lambda cls, **kwargs: None),
    )
    forged_type = type(
        "ForgedQuantitativeProjectionAuthorityManifest",
        (QuantitativeProjectionAuthorityManifestV1,),
        {},
    )
    forged: QuantitativeProjectionAuthorityManifestV1 = object.__new__(forged_type)
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="requires an owner-minted exact manifest",
    ):
        render_quantitative_projection_authority_manifest_bytes(forged)


def test_manifest_renderer_never_renders_forged_active_content() -> None:
    payload = _manifest_payload()
    payload["status"] = "ACTIVE"
    payload["activation_summary_artifact"] = _ACTIVATION_SUMMARY_ARTIFACT
    payload["activation_summary_sha256"] = "a" * 64
    payload["current_effect_cell_count"] = 1
    _reseal_manifest(payload)
    forged: QuantitativeProjectionAuthorityManifestV1 = object.__new__(
        QuantitativeProjectionAuthorityManifestV1
    )
    for field_name in QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_FIELDS_V1:
        value = payload[field_name]
        if field_name == "allowed_registered_writer_scope_ids":
            value = tuple(value)  # type: ignore[arg-type]
        object.__setattr__(forged, field_name, value)

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="^active_membership_owner_unavailable$",
    ):
        render_quantitative_projection_authority_manifest_bytes(forged)


def test_manifest_renderer_revalidates_exact_typed_content() -> None:
    manifest = rehydrate_quantitative_projection_authority_manifest(_manifest_payload())
    object.__setattr__(
        manifest,
        "manifest_content_id",
        f"{_MANIFEST_ID_PREFIX}{'0' * 64}",
    )

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="manifest_content_id does not match",
    ):
        render_quantitative_projection_authority_manifest_bytes(manifest)


def test_manifest_is_immutable_final_owner_minted_and_pickleable() -> None:
    manifest = rehydrate_quantitative_projection_authority_manifest(_manifest_payload())

    with pytest.raises(FrozenInstanceError):
        manifest.status = "ACTIVE"  # type: ignore[misc]
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="manifest must be minted by its owner",
    ):
        replace(manifest, _mint_token=object())
    with pytest.raises(TypeError, match="is final"):
        type("ForgedQuantitativeAuthorityManifest", (type(manifest),), {})
    assert pickle.loads(pickle.dumps(manifest)) == manifest


@pytest.mark.parametrize("mutation", ["missing", "extra", "reordered"])
def test_manifest_rejects_missing_extra_or_reordered_fields(mutation: str) -> None:
    payload = _manifest_payload()
    if mutation == "missing":
        del payload["effect_id"]
    elif mutation == "extra":
        payload["unexpected"] = "field"
    else:
        payload = dict(reversed(payload.items()))

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="fields are missing, extra, or reordered",
    ):
        rehydrate_quantitative_projection_authority_manifest(payload)


def test_manifest_rejects_generic_mapping_and_dict_subclass() -> None:
    class PayloadDict(dict[str, object]):
        pass

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="must be one ordered mapping",
    ):
        rehydrate_quantitative_projection_authority_manifest(
            PayloadDict(_manifest_payload())
        )


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("schema_version", 1),
        ("effect_id", "crossed-effect"),
        ("status", "UNKNOWN"),
        ("package_authority_artifact", "crossed.json"),
        ("package_authority_sha256", "A" * 64),
        (
            "allowed_registered_writer_scope_ids",
            (
                "backfill_policy_write_ready_rows",
                "cid_nl_adopt_ready_feature_inclusion_95_cells",
                "backfill_expansion_clean_target_selective_activation_84_cells",
            ),
        ),
        (
            "allowed_registered_writer_scope_ids",
            [
                "cid_nl_adopt_ready_feature_inclusion_95_cells",
                "backfill_policy_write_ready_rows",
                "backfill_expansion_clean_target_selective_activation_84_cells",
            ],
        ),
        ("activation_summary_artifact", "unexpected.json"),
        ("activation_summary_sha256", "a" * 64),
        ("current_effect_cell_count", True),
        ("current_effect_cell_count", 1),
        ("manifest_content_id", 1),
    ],
)
def test_manifest_rejects_wrong_types_literals_scopes_and_hold_state(
    field_name: str,
    invalid_value: object,
) -> None:
    payload = _manifest_payload()
    payload[field_name] = invalid_value
    if field_name != "manifest_content_id":
        _reseal_manifest(payload)

    with pytest.raises(QuantitativeProjectionAuthorityError):
        rehydrate_quantitative_projection_authority_manifest(payload)


def test_manifest_rejects_unsealed_and_resealed_crossed_content() -> None:
    unsealed = _manifest_payload()
    unsealed["manifest_content_id"] = f"{_MANIFEST_ID_PREFIX}{'0' * 64}"
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="manifest_content_id does not match",
    ):
        rehydrate_quantitative_projection_authority_manifest(unsealed)

    resealed = _manifest_payload()
    resealed["effect_id"] = "crossed-effect"
    _reseal_manifest(resealed)
    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="invalid quantitative projection authority manifest field: effect_id",
    ):
        rehydrate_quantitative_projection_authority_manifest(resealed)


def test_manifest_reloads_and_verifies_package_raw_sha256() -> None:
    crossed = _manifest_payload()
    crossed["package_authority_sha256"] = "0" * 64
    _reseal_manifest(crossed)

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="package_authority_sha256 does not match package authority bytes",
    ):
        rehydrate_quantitative_projection_authority_manifest(crossed)


def test_manifest_rejects_crossed_package_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_package = _payload()
    active_package["decision"] = "ACTIVE"
    active_package["activation_summary_sha256"] = "a" * 64
    active_raw = _reseal(active_package)
    crossed = _manifest_payload(package_raw=active_raw)
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: active_raw,
    )

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="manifest status does not match package authority decision",
    ):
        rehydrate_quantitative_projection_authority_manifest(crossed)


def test_active_manifest_fails_closed_without_minting_or_reading_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_package = _payload()
    active_package["decision"] = "ACTIVE"
    active_package["activation_summary_sha256"] = "a" * 64
    active_raw = _reseal(active_package)
    active_manifest = _manifest_payload(package_raw=active_raw)
    active_manifest["status"] = "ACTIVE"
    active_manifest["activation_summary_artifact"] = _ACTIVATION_SUMMARY_ARTIFACT
    active_manifest["activation_summary_sha256"] = "a" * 64
    active_manifest["current_effect_cell_count"] = 1
    _reseal_manifest(active_manifest)
    monkeypatch.setattr(
        authority_module,
        "_read_package_authority_bytes",
        lambda: active_raw,
    )
    monkeypatch.setattr(
        authority_module,
        "_mint_quantitative_projection_authority_manifest",
        lambda _content: pytest.fail("ACTIVE manifest must never be minted"),
    )

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="^active_membership_owner_unavailable$",
    ):
        rehydrate_quantitative_projection_authority_manifest(active_manifest)


def test_missing_package_resource_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_files(_package: str) -> object:
        raise ModuleNotFoundError("missing package")

    monkeypatch.setattr(authority_module, "files", unavailable_files)

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="^package_authority_resource_unavailable$",
    ):
        load_quantitative_projection_authority_for_diagnostic()


def test_casefold_ambiguous_package_resource_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResource:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeDirectory:
        def iterdir(self) -> tuple[FakeResource, ...]:
            return (
                FakeResource(_RESOURCE_NAME),
                FakeResource(_RESOURCE_NAME.upper()),
            )

    class FakePackage:
        def joinpath(self, directory: str) -> FakeDirectory:
            assert directory == "data"
            return FakeDirectory()

    monkeypatch.setattr(authority_module, "files", lambda _package: FakePackage())

    with pytest.raises(
        QuantitativeProjectionAuthorityError,
        match="^package_authority_resource_ambiguous$",
    ):
        load_quantitative_projection_authority_for_diagnostic()
