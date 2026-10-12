"""Lightweight claim vocabulary shared by semantic owners and renderers."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import Generic, TypeVar, final

_T = TypeVar("_T")
_QUERY_RESULT_MINT_TOKEN = object()


class EvidenceClaimState(str, Enum):
    BYTE_BOUND = "byte_bound"
    STRUCTURE_BOUND = "structure_bound"
    OWNER_REHYDRATED = "owner_rehydrated"
    PRODUCT_AUTHORIZED = "product_authorized"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    subject: str
    state: EvidenceClaimState
    reason: str


@final
@dataclass(frozen=True, slots=True)
class EvidenceQueryResult(Generic[_T]):
    records: tuple[_T, ...]
    claims: tuple[EvidenceClaim, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("EvidenceQueryResult is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _QUERY_RESULT_MINT_TOKEN:
            raise ValueError("EvidenceQueryResult must be minted by an owner reader")
        if not isinstance(self.records, tuple):
            raise TypeError("records must be a tuple")
        if not isinstance(self.claims, tuple) or any(
            not isinstance(claim, EvidenceClaim) for claim in self.claims
        ):
            raise TypeError("claims must contain EvidenceClaim values")
        subjects = tuple(claim.subject for claim in self.claims)
        if len(subjects) != len(set(subjects)):
            raise ValueError("query result claim subjects must be unique")

    def claim(self, subject: str) -> EvidenceClaim:
        for claim in self.claims:
            if claim.subject == subject:
                return claim
        raise KeyError(subject)


def mint_evidence_query_result(
    *,
    records: tuple[_T, ...],
    claims: tuple[EvidenceClaim, ...],
) -> EvidenceQueryResult[_T]:
    """Mint one query result from a strict semantic owner reader."""

    return EvidenceQueryResult(
        records=records,
        claims=claims,
        _mint_token=_QUERY_RESULT_MINT_TOKEN,
    )


__all__ = (
    "EvidenceClaim",
    "EvidenceClaimState",
    "EvidenceQueryResult",
    "mint_evidence_query_result",
)
