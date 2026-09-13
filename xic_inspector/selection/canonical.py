"""Canonical presentation refs and item modeling for XIC Inspector."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

ItemKind = Literal["row", "group", "candidate"]


@dataclass(frozen=True, slots=True)
class Witness:
    """A witness chain linking an item to an actual candidate/sample observation."""

    canonical_sample_identity_id: str
    candidate_id: str
    group_hypothesis_id: str | None = None
    row_key_id: str | None = None
    precursor_mz: float | None = None
    rt_apex_min: float | None = None
    observation_id: str | None = None
    sample_stem: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalItem:
    """Canonical presentation item representing a reviewable unit."""

    kind: ItemKind
    request_id: str
    attempt_id: str
    identifier: str
    serialized_ref: str
    sample_ids: tuple[str, ...]
    witnesses: tuple[Witness, ...]
    stratum: str  # 'attention' | 'excluded' | 'null' | 'zero' | 'rest'
    is_attention: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def sort_key(self) -> bytes:
        return self.serialized_ref.encode("utf-8")


def serialize_canonical_ref(ref: list[str]) -> str:
    """Serialize canonical presentation ref array to JSON without spaces."""
    return json.dumps(ref, ensure_ascii=False, separators=(",", ":"))


def build_canonical_ref(
    kind: ItemKind,
    request_id: str,
    attempt_id: str,
    identifier: str,
    sample_id: str | None = None,
) -> tuple[list[str], str]:
    """Construct and serialize a canonical presentation ref."""
    if kind == "row":
        ref = ["row", request_id, attempt_id, identifier]
    elif kind == "group":
        ref = ["group", request_id, attempt_id, identifier]
    elif kind == "candidate":
        if not sample_id:
            raise ValueError("Candidate item ref requires sample_id")
        ref = ["candidate", request_id, attempt_id, sample_id, identifier]
    else:
        raise ValueError(f"Unknown item kind: {kind}")
    return ref, serialize_canonical_ref(ref)
