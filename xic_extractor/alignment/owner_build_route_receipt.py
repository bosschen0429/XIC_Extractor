"""Owner-minted truth about the XIC route used to build Alignment owners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast, final, get_args

from xic_extractor.alignment.config import OwnerBuildXicBackend

EffectiveOwnerBuildRoute = Literal["retained_untargeted_cid_nl_batch"]

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

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("OwnerBuildRouteReceipt is final")

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
    )


__all__ = ["OwnerBuildRouteReceipt"]
