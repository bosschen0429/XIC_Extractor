"""Closed-world structural contracts for persisted mapping handoffs.

This module validates mapping shape only. It does not restore domain authority;
nested semantic content must still be rehydrated by its original owner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClosedMappingContract:
    """Reject undeclared fields and rebuild one declared mapping projection."""

    name: str
    declared_fields: frozenset[str]
    required_fields: frozenset[str]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Closed mapping contract name is required")
        if not self.declared_fields:
            raise ValueError(f"{self.name} must declare at least one field")
        undeclared_required = self.required_fields.difference(self.declared_fields)
        if undeclared_required:
            fields = ", ".join(sorted(undeclared_required))
            raise ValueError(f"{self.name} has undeclared required fields: {fields}")

    def validate(self, payload: Mapping[str, object]) -> None:
        """Validate only the mapping's closed-world structural surface."""

        observed = set(payload)
        undeclared = sorted(observed.difference(self.declared_fields))
        if undeclared:
            fields = ", ".join(undeclared)
            raise ValueError(f"{self.name} has undeclared fields: {fields}")
        missing = sorted(self.required_fields.difference(observed))
        if missing:
            fields = ", ".join(missing)
            raise ValueError(f"{self.name} is missing required fields: {fields}")

    def project(
        self,
        payload: Mapping[str, object],
        *,
        overrides: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Validate input and return a new mapping containing declared fields only."""

        self.validate(payload)
        changes = overrides or {}
        undeclared_overrides = sorted(set(changes).difference(self.declared_fields))
        if undeclared_overrides:
            fields = ", ".join(undeclared_overrides)
            raise ValueError(f"{self.name} has undeclared override fields: {fields}")
        projected = {
            field: payload[field]
            for field in self.declared_fields
            if field in payload
        }
        projected.update(changes)
        self.validate(projected)
        return projected


__all__ = ("ClosedMappingContract",)
