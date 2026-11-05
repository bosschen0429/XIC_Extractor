from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from xic_extractor.alignment.config import (
    AlignmentConfig,
)
from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
)
from xic_extractor.applications.untargeted.publication import (
    UntargetedPublicationPolicy,
)
from xic_extractor.configuration.execution_profile import (
    UntargetedExecutionPolicy,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
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
    signal_processing_profile: SignalProcessingProfile | None = None
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
        profile = resolve_signal_processing_profile(
            signal_processing_method=self.peak_config.signal_processing_method,
            signal_processing_window_points=self.peak_config.signal_processing_window_points,
        )
        override = self.signal_processing_profile
        if override is not None:
            if not isinstance(override, SignalProcessingProfile):
                raise TypeError(
                    "signal_processing_profile must be a SignalProcessingProfile"
                )
            if override != profile:
                raise ValueError(
                    "signal_processing_profile conflicts with peak_config "
                    "canonical fields"
                )
        validate_retained_processing_profile(profile)
        return profile


def validate_retained_processing_profile(profile: SignalProcessingProfile) -> None:
    """Retained-route capability check, also used by CLI preflight before I/O.

    Request/config conflicts are resolved by the application entry before this
    capability check; the CLI's canonical profile is already bound to its flags.
    """
    if profile != DEFAULT_SIGNAL_PROCESSING_PROFILE:
        raise ValueError(
            "pure CID-NL retained route does not support legacy Alignment "
            "options: signal_processing_profile"
        )


@dataclass(frozen=True, slots=True)
class UntargetedExecutionContext:
    """Run-scoped instrumentation and cancellation, not persisted settings."""

    timing_recorder: TimingRecorder | None = None
    should_cancel: Callable[[], bool] | None = None
