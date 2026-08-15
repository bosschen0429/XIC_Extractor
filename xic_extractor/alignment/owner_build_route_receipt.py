"""Owner-minted truth about the XIC route used to build Alignment owners."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, final

from xic_extractor.alignment.ms1_index_source import OwnerBuildXicBackend

EffectiveOwnerBuildRoute = Literal[
    "configured_backend",
    "discovery_physical_bindings",
]

_OWNER_BUILD_ROUTE_RECEIPT_MINT_TOKEN = object()


@final
@dataclass(frozen=True, slots=True)
class OwnerBuildRouteReceipt:
    """Validated in-process route truth returned by the Alignment owner."""

    configured_owner_build_xic_backend: OwnerBuildXicBackend
    effective_owner_build_route: EffectiveOwnerBuildRoute
    effective_owner_build_xic_backend: OwnerBuildXicBackend | None
    processing_run_spec_id: str | None
    processing_run_spec_hash: str | None
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("OwnerBuildRouteReceipt is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _OWNER_BUILD_ROUTE_RECEIPT_MINT_TOKEN:
            raise ValueError(
                "OwnerBuildRouteReceipt must be minted by the Alignment owner"
            )
        if self.effective_owner_build_route == "configured_backend":
            if (
                self.effective_owner_build_xic_backend
                != self.configured_owner_build_xic_backend
            ):
                raise ValueError("configured route must report its configured backend")
            if self.processing_run_spec_id is not None:
                raise ValueError("configured route cannot claim a ProcessingRunSpec")
            if self.processing_run_spec_hash is not None:
                raise ValueError(
                    "configured route cannot claim a ProcessingRunSpec hash"
                )
        else:
            if self.effective_owner_build_xic_backend is not None:
                raise ValueError("Discovery physical bindings do not execute a backend")
            if not self.processing_run_spec_id or not self.processing_run_spec_hash:
                raise ValueError(
                    "Discovery physical bindings require ProcessingRunSpec identity"
                )

    def as_dict(self) -> dict[str, object]:
        return {
            "configured_owner_build_xic_backend": (
                self.configured_owner_build_xic_backend
            ),
            "effective_owner_build_route": self.effective_owner_build_route,
            "effective_owner_build_xic_backend": (
                self.effective_owner_build_xic_backend
            ),
            "effective_route_validation": "owner_validated",
            "processing_run_spec_id": self.processing_run_spec_id,
            "processing_run_spec_hash": self.processing_run_spec_hash,
        }


def _mint_owner_build_route_receipt(
    *,
    configured_backend: OwnerBuildXicBackend,
    processing_run_spec_id: str | None = None,
    processing_run_spec_hash: str | None = None,
) -> OwnerBuildRouteReceipt:
    uses_discovery_bindings = processing_run_spec_id is not None
    if uses_discovery_bindings != (processing_run_spec_hash is not None):
        raise ValueError("ProcessingRunSpec id and hash must be provided together")
    return OwnerBuildRouteReceipt(
        configured_owner_build_xic_backend=configured_backend,
        effective_owner_build_route=(
            "discovery_physical_bindings"
            if uses_discovery_bindings
            else "configured_backend"
        ),
        effective_owner_build_xic_backend=(
            None if uses_discovery_bindings else configured_backend
        ),
        processing_run_spec_id=processing_run_spec_id,
        processing_run_spec_hash=processing_run_spec_hash,
        _mint_token=_OWNER_BUILD_ROUTE_RECEIPT_MINT_TOKEN,
    )


__all__ = ["OwnerBuildRouteReceipt"]
