"""Owner-minted truth about the XIC route used to build Alignment owners."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, cast, final, get_args

from xic_extractor.alignment.config import OwnerBuildXicBackend

EffectiveOwnerBuildRoute = Literal[
    "configured_backend",
    "discovery_physical_bindings",
    "retained_untargeted_cid_nl_batch",
]

_OWNER_BUILD_ROUTE_RECEIPT_MINT_TOKEN = object()
_OWNER_BUILD_XIC_BACKENDS = frozenset(get_args(OwnerBuildXicBackend))


def _validate_owner_build_xic_backend(value: object) -> OwnerBuildXicBackend:
    if type(value) is not str or value not in _OWNER_BUILD_XIC_BACKENDS:
        raise ValueError(f"unsupported owner build XIC backend: {value}")
    return cast(OwnerBuildXicBackend, value)


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
        _validate_owner_build_xic_backend(
            self.configured_owner_build_xic_backend,
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
        elif self.effective_owner_build_route == "discovery_physical_bindings":
            if self.effective_owner_build_xic_backend is not None:
                raise ValueError("Discovery physical bindings do not execute a backend")
            if not self.processing_run_spec_id or not self.processing_run_spec_hash:
                raise ValueError(
                    "Discovery physical bindings require ProcessingRunSpec identity"
                )
        elif self.effective_owner_build_route == "retained_untargeted_cid_nl_batch":
            if self.effective_owner_build_xic_backend is not None:
                raise ValueError(
                    "retained CID-NL batch does not execute a legacy backend"
                )
            if (
                self.processing_run_spec_id is not None
                or self.processing_run_spec_hash is not None
            ):
                raise ValueError(
                    "retained CID-NL batch does not claim a ProcessingRunSpec route"
                )
        else:
            raise ValueError("unsupported effective owner build route")

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
    configured_backend = _validate_owner_build_xic_backend(configured_backend)
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


def _mint_retained_untargeted_cid_nl_route_receipt(
    *,
    configured_backend: OwnerBuildXicBackend,
) -> OwnerBuildRouteReceipt:
    return OwnerBuildRouteReceipt(
        configured_owner_build_xic_backend=_validate_owner_build_xic_backend(
            configured_backend
        ),
        effective_owner_build_route="retained_untargeted_cid_nl_batch",
        effective_owner_build_xic_backend=None,
        processing_run_spec_id=None,
        processing_run_spec_hash=None,
        _mint_token=_OWNER_BUILD_ROUTE_RECEIPT_MINT_TOKEN,
    )


__all__ = ["OwnerBuildRouteReceipt"]
