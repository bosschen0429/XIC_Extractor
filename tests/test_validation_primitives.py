from __future__ import annotations

import pytest

from xic_extractor.validation_primitives import (
    canonical_float_hex,
    canonical_json_bytes,
    json_unique_mapping_hook,
    make_unique_mapping_hook,
    parse_canonical_float_hex,
    parse_json_mapping_payload,
    require_boolean,
    require_finite_real,
    require_integer,
    require_keys,
    require_list,
    require_lower_sha256,
    require_mapping,
    require_optional_integer,
    require_optional_lower_sha256,
    require_optional_owner_normalized_text,
    require_optional_text,
    require_owner_normalized_text,
    require_text,
    require_tuple_of,
    sha256_hexdigest,
)


def test_canonical_json_bytes() -> None:
    data = {"b": 1, "a": "中文", "c": True, "d": None}
    raw = canonical_json_bytes(data)
    assert raw.decode("utf-8") == '{"b":1,"a":"中文","c":true,"d":null}'

    with pytest.raises(ValueError):
        canonical_json_bytes({"nan": float("nan")})
    with pytest.raises(ValueError):
        canonical_json_bytes({"inf": float("inf")})


def test_unique_mapping_hook() -> None:
    pairs = [("a", 1), ("b", 2)]
    assert json_unique_mapping_hook(pairs) == {"a": 1, "b": 2}

    with pytest.raises(ValueError, match="JSON payload has duplicate field: a"):
        json_unique_mapping_hook([("a", 1), ("a", 2)])

    custom_hook = make_unique_mapping_hook(
        label="Custom payload", error_factory=KeyError
    )
    with pytest.raises(KeyError, match="Custom payload has duplicate field: x"):
        custom_hook([("x", 1), ("x", 2)])


def test_parse_json_mapping_payload() -> None:
    raw, mapping = parse_json_mapping_payload('{"a":1,"b":"val"}', label="test")
    assert raw == b'{"a":1,"b":"val"}'
    assert mapping == {"a": 1, "b": "val"}

    # Accepts bytes directly
    raw2, mapping2 = parse_json_mapping_payload(b'{"k":true}', label="test")
    assert raw2 == b'{"k":true}'
    assert mapping2 == {"k": True}

    # Type error on non-bytes/str
    with pytest.raises(TypeError, match="test payload must be bytes or str"):
        parse_json_mapping_payload(123, label="test")  # type: ignore[arg-type]

    # Non-UTF8
    with pytest.raises(ValueError, match="test payload must be UTF-8"):
        parse_json_mapping_payload(b"\xff\xfe", label="test")

    # Invalid JSON
    with pytest.raises(ValueError, match="test payload must be valid JSON"):
        parse_json_mapping_payload("{invalid}", label="test")

    # Non-dict JSON
    with pytest.raises(ValueError, match="test must be one JSON object"):
        parse_json_mapping_payload("[1, 2]", label="test")

    # Duplicate keys
    with pytest.raises(ValueError, match="duplicate field"):
        parse_json_mapping_payload('{"x": 1, "x": 2}', label="test")

    # Enforce canonical
    with pytest.raises(ValueError, match="test is not canonical"):
        parse_json_mapping_payload(
            '{"a": 1, "b": 2}', label="test", enforce_canonical=True
        )

    # Valid canonical
    c_raw, c_val = parse_json_mapping_payload(
        '{"a":1,"b":2}', label="test", enforce_canonical=True
    )
    assert c_val == {"a": 1, "b": 2}


def test_canonical_float_hex() -> None:
    assert canonical_float_hex(0.0) == (0.0).hex()
    assert canonical_float_hex(-0.0) == (0.0).hex()
    assert canonical_float_hex(1.5) == (1.5).hex()
    assert canonical_float_hex(10) == (10.0).hex()

    with pytest.raises(TypeError, match="canonical numeric fields must be numbers"):
        canonical_float_hex(True)
    with pytest.raises(TypeError, match="canonical numeric fields must be numbers"):
        canonical_float_hex("1.5")
    with pytest.raises(ValueError, match="canonical numeric fields must be finite"):
        canonical_float_hex(float("nan"))
    with pytest.raises(ValueError, match="canonical numeric fields must be finite"):
        canonical_float_hex(float("inf"))


def test_parse_canonical_float_hex() -> None:
    hex_str = (1.5).hex()
    assert parse_canonical_float_hex(hex_str, field="rt") == 1.5
    assert parse_canonical_float_hex((0.0).hex(), field="val") == 0.0

    with pytest.raises(TypeError, match="rt must be canonical hexadecimal text"):
        parse_canonical_float_hex(1.5, field="rt")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="rt must be canonical hexadecimal text"):
        parse_canonical_float_hex("not-a-hex", field="rt")
    with pytest.raises(
        ValueError, match="rt must be canonical finite hexadecimal text"
    ):
        parse_canonical_float_hex("nan", field="rt")
    with pytest.raises(
        ValueError, match="rt must be canonical finite hexadecimal text"
    ):
        # Not canonical (should be 0x1.8000000000000p+0)
        parse_canonical_float_hex("0x1.8p0", field="rt")


def test_require_finite_real() -> None:
    assert require_finite_real(3.14, name="val") == 3.14
    assert require_finite_real(42, name="val") == 42.0

    with pytest.raises(ValueError, match="val must be a real number"):
        require_finite_real(True, name="val")
    with pytest.raises(ValueError, match="val must be a real number"):
        require_finite_real("3.14", name="val")
    with pytest.raises(ValueError, match="val must be finite"):
        require_finite_real(float("nan"), name="val")

    # non-negative & positive
    assert require_finite_real(0.0, name="v", non_negative=True) == 0.0
    with pytest.raises(ValueError, match="v must be non-negative"):
        require_finite_real(-0.1, name="v", non_negative=True)

    assert require_finite_real(0.1, name="v", positive=True) == 0.1
    with pytest.raises(ValueError, match="v must be positive"):
        require_finite_real(0.0, name="v", positive=True)


def test_require_integer() -> None:
    assert require_integer(42, name="cnt") == 42

    with pytest.raises(ValueError, match="cnt must be an integer"):
        require_integer(True, name="cnt")
    with pytest.raises(ValueError, match="cnt must be an integer"):
        require_integer(3.14, name="cnt")
    with pytest.raises(ValueError, match="cnt must be an integer"):
        require_integer("42", name="cnt")

    # non-negative & positive
    assert require_integer(0, name="cnt", non_negative=True) == 0
    with pytest.raises(ValueError, match="cnt must be a non-negative integer"):
        require_integer(-1, name="cnt", non_negative=True)

    assert require_integer(1, name="cnt", positive=True) == 1
    with pytest.raises(ValueError, match="cnt must be a positive integer"):
        require_integer(0, name="cnt", positive=True)

    # optional integer
    assert require_optional_integer(None, name="cnt") is None
    assert require_optional_integer(5, name="cnt") == 5


def test_require_boolean() -> None:
    assert require_boolean(True, field="flag") is True
    assert require_boolean(False, field="flag") is False

    with pytest.raises(TypeError, match="flag must be a boolean"):
        require_boolean(1, field="flag")
    with pytest.raises(TypeError, match="flag must be a boolean"):
        require_boolean("true", field="flag")


def test_require_text_and_optional() -> None:
    assert require_text("hello", field="f") == "hello"
    assert require_text("", field="f", allow_blank=True) == ""

    with pytest.raises(TypeError, match="f must be text"):
        require_text(123, field="f")
    with pytest.raises(ValueError, match="f must be non-empty text"):
        require_text("", field="f")

    assert require_optional_text(None, field="f") is None
    assert require_optional_text("test", field="f") == "test"
    with pytest.raises(TypeError, match="f must be text"):
        require_optional_text(123, field="f")


def test_require_owner_normalized_text() -> None:
    assert require_owner_normalized_text("valid_name", field="f") == "valid_name"

    with pytest.raises(ValueError, match="f must be non-empty owner-normalized text"):
        require_owner_normalized_text("", field="f")
    with pytest.raises(ValueError, match="f must be non-empty owner-normalized text"):
        require_owner_normalized_text("  padded  ", field="f")
    with pytest.raises(ValueError, match="f must be non-empty owner-normalized text"):
        require_owner_normalized_text(None, field="f")

    assert require_optional_owner_normalized_text(None, field="f") is None
    assert require_optional_owner_normalized_text("valid", field="f") == "valid"


def test_require_mapping_and_list() -> None:
    assert require_mapping({"a": 1}, label="obj") == {"a": 1}
    with pytest.raises(ValueError, match="obj must be one JSON object"):
        require_mapping([1, 2], label="obj")
    with pytest.raises(ValueError, match="obj must be one JSON object"):
        require_mapping("text", label="obj")

    assert require_list([1, 2], label="arr") == [1, 2]
    with pytest.raises(ValueError, match="arr must be one JSON array"):
        require_list({"a": 1}, label="arr")


def test_require_keys() -> None:
    require_keys({"a": 1, "b": 2}, ("a", "b"), label="test")

    expected_err1 = "test fields must be exactly \\('a', 'b'\\); got \\('a',\\)"
    with pytest.raises(ValueError, match=expected_err1):
        require_keys({"a": 1}, ("a", "b"), label="test")

    expected_err2 = "test fields must be exactly \\('a', 'b'\\); got \\('b', 'a'\\)"
    with pytest.raises(ValueError, match=expected_err2):
        require_keys({"b": 2, "a": 1}, ("a", "b"), label="test")


def test_require_lower_sha256() -> None:
    valid = "a" * 64
    assert require_lower_sha256(valid, field="hash") == valid

    with pytest.raises(ValueError, match="hash must be a lowercase SHA-256"):
        require_lower_sha256("A" * 64, field="hash")
    with pytest.raises(ValueError, match="hash must be a lowercase SHA-256"):
        require_lower_sha256("a" * 63, field="hash")
    with pytest.raises(ValueError, match="hash must be a lowercase SHA-256"):
        require_lower_sha256("g" * 64, field="hash")
    with pytest.raises(ValueError, match="hash must be a lowercase SHA-256"):
        require_lower_sha256(12345, field="hash")
    for invalid in (valid + "\n", "a" * 63 + "\n", "ａ" * 64, "a" * 65):
        with pytest.raises(ValueError, match="hash must be a lowercase SHA-256"):
            require_lower_sha256(invalid, field="hash")

    assert require_optional_lower_sha256(None, field="hash") is None
    assert require_optional_lower_sha256(valid, field="hash") == valid


def test_require_tuple_of() -> None:
    assert require_tuple_of((1, 2, 3), int, field="numbers") == (1, 2, 3)
    assert require_tuple_of((), str, field="empty") == ()

    with pytest.raises(ValueError, match="numbers must be a tuple of int values"):
        require_tuple_of([1, 2, 3], int, field="numbers")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="numbers must be a tuple of int values"):
        require_tuple_of((1, "two", 3), int, field="numbers")


def test_sha256_hexdigest() -> None:
    import hashlib

    data_bytes = b"test payload"
    data_str = "test payload"
    expected = hashlib.sha256(data_bytes).hexdigest()

    assert sha256_hexdigest(data_bytes) == expected
    assert sha256_hexdigest(data_str) == expected
