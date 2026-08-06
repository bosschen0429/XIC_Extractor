from __future__ import annotations

import pytest

from xic_extractor.persisted_mapping_contract import ClosedMappingContract


def _contract() -> ClosedMappingContract:
    return ClosedMappingContract(
        name="test handoff",
        declared_fields=frozenset(("required", "optional")),
        required_fields=frozenset(("required",)),
    )


def test_closed_mapping_contract_accepts_declared_optional_fields() -> None:
    _contract().validate({"required": 1, "optional": 2})


def test_closed_mapping_contract_rejects_undeclared_fields() -> None:
    with pytest.raises(ValueError, match="undeclared fields: surprise"):
        _contract().validate({"required": 1, "surprise": 2})


def test_closed_mapping_contract_rejects_missing_required_fields() -> None:
    with pytest.raises(ValueError, match="missing required fields: required"):
        _contract().validate({"optional": 2})


def test_closed_mapping_contract_projects_a_new_declared_mapping() -> None:
    source = {"required": 1, "optional": 2}

    projected = _contract().project(source, overrides={"required": 3})

    assert projected == {"required": 3, "optional": 2}
    assert projected is not source


def test_closed_mapping_contract_rejects_undeclared_overrides() -> None:
    with pytest.raises(ValueError, match="undeclared override fields: surprise"):
        _contract().project({"required": 1}, overrides={"surprise": 2})
