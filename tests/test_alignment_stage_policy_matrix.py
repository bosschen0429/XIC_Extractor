from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import pytest

from xic_extractor.alignment import stage_policy_matrix as codec_module


def test_literal_field_projection_is_compact_utf8_and_ordered() -> None:
    fields = ("label", "enabled", "count", "values", "missing")
    content = {
        "missing": None,
        "values": (1.0, -2.5, "0x1.0000000000000p+0"),
        "count": 3,
        "enabled": True,
        "label": "訊號",
    }

    encoded = codec_module._canonical_semantic_bytes(
        content,
        field_names=fields,
    )

    assert encoded == (
        '{"label":"訊號","enabled":true,"count":3,'
        '"values":[{"$float":"0x1.0000000000000p+0"},'
        '{"$float":"-0x1.4000000000000p+1"},'
        '"0x1.0000000000000p+0"],"missing":null}'
    ).encode("utf-8")


def test_source_mapping_insertion_order_does_not_define_semantic_order() -> None:
    fields = ("first", "second")
    forward = {"first": "a", "second": "b"}
    reverse = {"second": "b", "first": "a"}

    assert codec_module._canonical_semantic_bytes(
        forward, field_names=fields
    ) == codec_module._canonical_semantic_bytes(reverse, field_names=fields)
    assert codec_module._canonical_semantic_bytes(
        forward, field_names=fields
    ) != codec_module._canonical_semantic_bytes(
        forward, field_names=tuple(reversed(fields))
    )


def test_signed_zero_normalizes_and_float_does_not_collide_with_hex_string() -> None:
    positive_zero = codec_module._canonical_semantic_bytes(0.0)
    negative_zero = codec_module._canonical_semantic_bytes(-0.0)
    tagged_one = codec_module._canonical_semantic_bytes(1.0)
    text_one = codec_module._canonical_semantic_bytes("0x1.0000000000000p+0")

    assert positive_zero == negative_zero == b'{"$float":"0x0.0p+0"}'
    assert tagged_one == b'{"$float":"0x1.0000000000000p+0"}'
    assert text_one == b'"0x1.0000000000000p+0"'
    assert tagged_one != text_one


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        ["list"],
        {"mapping": "without projection"},
        OrderedDict((("mapping", "subtype"),)),
        {"set"},
        Path("local/path"),
    ],
)
def test_generic_codec_rejects_nonsemantic_or_unknown_values(value: object) -> None:
    with pytest.raises(ValueError, match="unsupported canonical semantic"):
        codec_module._canonical_semantic_bytes(value)


def test_codec_requires_exact_builtin_scalar_types() -> None:
    class DerivedInt(int):
        pass

    class DerivedString(str):
        pass

    class DerivedFloat(float):
        pass

    for value in (DerivedInt(1), DerivedString("x"), DerivedFloat(1.0)):
        with pytest.raises(ValueError, match="unsupported canonical semantic"):
            codec_module._canonical_semantic_bytes(value)


@pytest.mark.parametrize(
    ("content", "fields"),
    [
        ({"a": 1}, ("a", "b")),
        ({"a": 1, "b": 2}, ("a",)),
        ({"a": 1}, ("a", "a")),
        ({"a": 1}, ["a"]),
        ({"a": 1}, ("",)),
        ({"a": 1}, (1,)),
    ],
)
def test_literal_projection_rejects_missing_extra_or_invalid_field_tuples(
    content: dict[str, object],
    fields: object,
) -> None:
    with pytest.raises(ValueError, match="unsupported canonical semantic"):
        codec_module._canonical_semantic_bytes(
            content,
            field_names=fields,  # type: ignore[arg-type]
        )


def test_literal_projection_rejects_generic_mapping_implementations() -> None:
    with pytest.raises(ValueError, match="unsupported canonical semantic mapping"):
        codec_module._canonical_semantic_bytes(
            OrderedDict((("field", "value"),)),
            field_names=("field",),
        )


def test_stage_policy_matrix_has_no_public_codec_export() -> None:
    assert codec_module.__all__ == ()
