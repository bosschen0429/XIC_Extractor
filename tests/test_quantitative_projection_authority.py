from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from importlib.resources import files

import pytest

from xic_extractor.alignment import (
    quantitative_projection_authority as authority_module,
)
from xic_extractor.alignment.quantitative_projection_authority import (
    QUANTITATIVE_PROJECTION_AUTHORITY_FIELDS_V1,
    QuantitativeProjectionAuthorityError,
    QuantitativeProjectionAuthorityHoldError,
    load_quantitative_projection_authority_for_diagnostic,
    require_product_quantitative_projection_authority,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE_ID,
)

_RESOURCE_NAME = "issue256_quantitative_projection_authority_v1.json"
_CONSUMER_ID = "xic_extractor.alignment.pipeline.run_alignment:production-equivalent"
_PROFILE_ID = (
    "signal-profile.v1:bcefd76d05bc26a0ec9f333fdb76427ccb2133c1946f5ec711916e85efc06de2"
)
_AUTHORITY_ID_PREFIX = "quantitative-projection-authority.v1:"


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
        "cid_nl_adopt_ready_feature_inclusion_95_cells",
        "backfill_expansion_clean_target_selective_activation_84_cells",
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
    assert authority.authority_id.startswith(_AUTHORITY_ID_PREFIX)


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
