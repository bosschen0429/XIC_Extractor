from __future__ import annotations

import pytest

from xic_extractor.validation_primitives import (
    require_boolean,
    require_finite_real,
    require_integer,
    require_lower_sha256,
    require_optional_integer,
    require_optional_text,
    require_owner_normalized_text,
    require_text,
)


def test_require_finite_real() -> None:
    assert require_finite_real(3.14, name="val") == 3.14
    assert require_finite_real(42, name="val") == 42.0

    with pytest.raises(ValueError, match="val must be a real number"):
        require_finite_real(True, name="val")
    with pytest.raises(ValueError, match="val must be a real number"):
        require_finite_real("3.14", name="val")
    with pytest.raises(ValueError, match="val must be finite"):
        require_finite_real(float("nan"), name="val")

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

    assert require_integer(0, name="cnt", non_negative=True) == 0
    with pytest.raises(ValueError, match="cnt must be a non-negative integer"):
        require_integer(-1, name="cnt", non_negative=True)

    assert require_integer(1, name="cnt", positive=True) == 1
    with pytest.raises(ValueError, match="cnt must be a positive integer"):
        require_integer(0, name="cnt", positive=True)

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
