from __future__ import annotations

from collections.abc import Callable, Mapping
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
from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)
from xic_extractor.discovery.csv_reader import DiscoveryBatchIndex
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.input_planning import (
    is_supported_alignment_discovery,
)


@dataclass(frozen=True, slots=True)
class AlignmentReplayOptions:
    """Inputs to retain from this run for local stage replay."""

    grouping: bool = False
    backfill: bool = False
    pairing: bool = False
    matrix: bool = False


@dataclass(frozen=True, slots=True)
class UntargetedRequest:
    """Alignment intent from completed Discovery; not a full-analysis request.

    The application entry resolves config dependencies once before index/RAW
    I/O. Policies own their accepted options; canonical configs are not copied.
    """

    discovery_batch_index: Path | DiscoveryBatchIndex
    raw_dir: Path
    dll_dir: Path
    output_dir: Path
    alignment_config: AlignmentConfig
    discovery_settings: DiscoverySettings
    signal_processing_profile: SignalProcessingProfile = (
        DEFAULT_SIGNAL_PROCESSING_PROFILE
    )
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
    replay: AlignmentReplayOptions = field(default_factory=AlignmentReplayOptions)

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
        profile = self.signal_processing_profile
        if not isinstance(profile, SignalProcessingProfile):
            raise TypeError(
                "signal_processing_profile must be a SignalProcessingProfile"
            )
        validate_retained_processing_profile(profile)
        return profile


def validate_retained_processing_profile(profile: SignalProcessingProfile) -> None:
    """Retained-route capability check, also used by CLI preflight before I/O."""
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
    progress_callback: Callable[[Mapping[str, object]], None] | None = None
