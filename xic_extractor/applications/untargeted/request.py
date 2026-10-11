from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.alignment.backfill_peak_fact import BackfillWindowStrategy
from xic_extractor.alignment.backfill_scope import BackfillScope
from xic_extractor.alignment.config import (
    AlignmentConfig,
    BackfillXicBackend,
    OwnerBuildXicBackend,
)
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
)
from xic_extractor.alignment.owner_build_route_receipt import (
    _validate_owner_build_xic_backend,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.configuration.execution_profile import (
    UntargetedExecutionPolicy,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE_ID,
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.input_planning import (
    is_supported_alignment_discovery,
)


@dataclass(frozen=True, slots=True)
class RetainedAlignmentOptions:
    """Accepted advanced overrides for the retained single-provider route."""

    signal_processing_profile: SignalProcessingProfile | None = None
    raw_xic_batch_size: int = 1
    owner_build_xic_backend: OwnerBuildXicBackend = "raw"
    owner_backfill_xic_backend: BackfillXicBackend = "raw"
    owner_backfill_window_strategy: BackfillWindowStrategy = "exact"
    owner_backfill_superwindow_span_factor: int = 2
    sample_column_injection_order: Path | None = None
    backfill_scope: BackfillScope = "full-audit"
    audit_evidence_mode: str = "auto"
    selected_family_ids: frozenset[str] = frozenset()
    selected_family_source: str = ""
    require_discovery_owner_closure: bool = False

    def __post_init__(self) -> None:
        if self.signal_processing_profile is not None and not isinstance(
            self.signal_processing_profile, SignalProcessingProfile
        ):
            raise TypeError(
                "signal_processing_profile must be a SignalProcessingProfile"
            )
        unsupported: list[str] = []
        if self.raw_xic_batch_size != 1:
            unsupported.append("raw_xic_batch_size")
        if self.owner_backfill_xic_backend != "raw":
            unsupported.append("owner_backfill_xic_backend")
        if self.owner_backfill_window_strategy != "exact":
            unsupported.append("owner_backfill_window_strategy")
        if self.owner_backfill_superwindow_span_factor != 2:
            unsupported.append("owner_backfill_superwindow_span_factor")
        if self.sample_column_injection_order is not None:
            unsupported.append("sample_column_injection_order")
        if self.backfill_scope != "full-audit":
            unsupported.append("backfill_scope")
        if self.audit_evidence_mode not in {"auto", "none"}:
            unsupported.append("audit_evidence_mode")
        if self.selected_family_ids:
            unsupported.append("selected_family_ids")
        if self.selected_family_source.strip():
            unsupported.append("selected_family_source")
        if (
            self.signal_processing_profile is not None
            and self.signal_processing_profile.profile_id
            != DEFAULT_SIGNAL_PROCESSING_PROFILE_ID
        ):
            unsupported.append("signal_processing_profile")
        if unsupported:
            raise ValueError(
                "pure CID-NL retained route does not support legacy Alignment options: "
                + ", ".join(unsupported)
            )


@dataclass(frozen=True, slots=True)
class UntargetedRequest:
    """Complete Alignment intent; canonical configs retain their existing owners.

    The application entry resolves config dependencies once before discovery/RAW
    I/O. Policies own their accepted options; canonical configs are not copied.
    """

    discovery_batch_index: Path
    raw_dir: Path
    dll_dir: Path
    output_dir: Path
    alignment_config: AlignmentConfig
    peak_config: ExtractionConfig
    discovery_settings: DiscoverySettings
    sample_metadata: tuple[SampleMetadata, ...] | None = None
    known_istd_reference_workbook: Path | None = None
    targets_csv: Path | None = None
    known_istd_reference: KnownIstdReference | None = None
    execution: UntargetedExecutionPolicy = field(
        default_factory=UntargetedExecutionPolicy
    )
    publication: UntargetedPublicationPolicy = field(
        default_factory=UntargetedPublicationPolicy
    )
    legacy: RetainedAlignmentOptions = field(default_factory=RetainedAlignmentOptions)

    def resolve_processing_profile(self) -> SignalProcessingProfile:
        """Resolve request dependencies at the formal application entry."""
        settings = self.discovery_settings
        if settings is not None and type(settings) is not DiscoverySettings:
            raise TypeError("discovery_settings must be an exact DiscoverySettings")
        if settings is None or not is_supported_alignment_discovery(settings):
            raise ValueError(
                "run_untargeted_application supports only supported CID-NL "
                "discovery_settings; "
                "pure single-provider CID-NL or compatible composite settings; "
                "use scripts.run_alignment --preset dna_dr with an "
                "owner-closed discovery batch index"
            )
        _validate_owner_build_xic_backend(self.legacy.owner_build_xic_backend)
        profile = resolve_signal_processing_profile(
            signal_processing_method=self.peak_config.signal_processing_method,
            signal_processing_window_points=self.peak_config.signal_processing_window_points,
        )
        override = self.legacy.signal_processing_profile
        if override is not None:
            if override != profile:
                raise ValueError(
                    "signal_processing_profile conflicts with peak_config "
                    "canonical fields"
                )
        elif profile.profile_id != DEFAULT_SIGNAL_PROCESSING_PROFILE_ID:
            raise ValueError(
                "pure CID-NL retained route does not support legacy Alignment "
                "options: signal_processing_profile"
            )
        if (
            self.publication.emit_provisional_matrix
            and not self.legacy.require_discovery_owner_closure
        ):
            raise ValueError(
                "provisional matrix emission requires Discovery owner closure"
            )
        return profile


@dataclass(frozen=True, slots=True)
class UntargetedExecutionContext:
    """Run-scoped instrumentation and cancellation, not persisted settings."""

    timing_recorder: TimingRecorder | None = None
    should_cancel: Callable[[], bool] | None = None
