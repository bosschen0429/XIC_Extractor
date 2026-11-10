"""Owner-backed Discovery peak-universe receipts.

The receipt binds a sample's peak CSV and physical-facts files to the
local acquisition revision that Discovery inspected.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.mass_recalibration import (
    VENDOR_MASS_RECALIBRATION_RESULT,
    MassRecalibration,
    MassRecalibrationResult,
    deserialize_mass_recalibration_result,
    serialize_mass_recalibration_result,
)
from xic_extractor.discovery.models import (
    DiscoveryPeak,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    FileContentFingerprint,
    IdentityContractError,
    fingerprint_file_content,
)
from xic_extractor.validation_primitives import (
    require_integer,
    require_lower_sha256,
    require_owner_normalized_text,
)

DISCOVERY_BATCH_INDEX_SCHEMA_VERSION = "discovery_batch_index_peaks"
DISCOVERY_PEAK_CSV_SCHEMA_VERSION = "discovery_peaks_csv"

DISCOVERY_BATCH_RECEIPT_COLUMNS = (
    "peak_csv_schema_version",
    "peak_csv_sha256",
    "peak_csv_byte_size",
    "physical_facts_sha256",
    "physical_facts_byte_size",
    "acquisition_local_file_revision",
    "acquisition_byte_size",
    "acquisition_format",
    "observation_namespace",
)
DISCOVERY_BATCH_INDEX_COLUMNS = (
    "schema_version",
    "sample_stem",
    "raw_file",
    "peak_csv",
    "event_csv",
    "peak_count",
    "admitted_peak_count",
    "event_count",
    *DISCOVERY_BATCH_RECEIPT_COLUMNS,
    "mass_recalibration",
)


@dataclass(frozen=True, slots=True)
class DiscoveryPeakUniverseReceipt:
    """One Discovery-owned, content-bound peak-universe receipt."""

    sample_stem: str
    acquisition_source: AcquisitionSourceIdentity
    peak_count: int
    peak_csv_schema_version: str
    peak_csv_sha256: str
    peak_csv_byte_size: int
    physical_facts_sha256: str
    physical_facts_byte_size: int
    mass_recalibration_result: MassRecalibrationResult = MassRecalibrationResult("none")

    def __post_init__(self) -> None:
        _required_text(self.sample_stem, field="sample_stem")
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise IdentityContractError(
                "acquisition_source must be an AcquisitionSourceIdentity"
            )
        if (
            isinstance(self.peak_count, bool)
            or not isinstance(self.peak_count, int)
            or self.peak_count < 0
        ):
            raise IdentityContractError("peak_count must be a non-negative integer")
        if self.peak_csv_schema_version != DISCOVERY_PEAK_CSV_SCHEMA_VERSION:
            raise IdentityContractError("peak artifact schema version is unsupported")
        _validate_digest(
            self.peak_csv_sha256,
            field="peak_csv_sha256",
        )
        if (
            isinstance(self.peak_csv_byte_size, bool)
            or not isinstance(self.peak_csv_byte_size, int)
            or self.peak_csv_byte_size < 0
        ):
            raise IdentityContractError(
                "peak_csv_byte_size must be a non-negative integer"
            )
        _validate_digest(
            self.physical_facts_sha256,
            field="physical_facts_sha256",
        )
        if (
            isinstance(self.physical_facts_byte_size, bool)
            or not isinstance(self.physical_facts_byte_size, int)
            or self.physical_facts_byte_size < 0
        ):
            raise IdentityContractError(
                "physical_facts_byte_size must be a non-negative integer"
            )


def mint_discovery_peak_universe_receipt(
    *,
    sample_stem: str,
    acquisition_source: AcquisitionSourceIdentity,
    peak_csv: Path,
    peaks: Sequence[DiscoveryPeak],
    physical_facts: FileContentFingerprint | None,
    mass_recalibration_result: MassRecalibrationResult = (
        VENDOR_MASS_RECALIBRATION_RESULT
    ),
) -> DiscoveryPeakUniverseReceipt:
    """Mint a receipt after the Discovery writers have committed bytes."""

    if not isinstance(physical_facts, FileContentFingerprint):
        raise IdentityContractError(
            "Discovery receipt requires the published physical-facts fingerprint"
        )

    stem = _required_text(sample_stem, field="sample_stem")
    if not isinstance(acquisition_source, AcquisitionSourceIdentity):
        raise IdentityContractError(
            "acquisition_source must be an AcquisitionSourceIdentity"
        )
    ordered = _canonical_peaks(peaks, sample_stem=stem)
    artifact = fingerprint_file_content(peak_csv)
    return DiscoveryPeakUniverseReceipt(
        sample_stem=stem,
        acquisition_source=acquisition_source,
        peak_count=len(ordered),
        peak_csv_schema_version=(DISCOVERY_PEAK_CSV_SCHEMA_VERSION),
        peak_csv_sha256=artifact.content_sha256,
        peak_csv_byte_size=artifact.byte_size,
        physical_facts_sha256=physical_facts.content_sha256,
        physical_facts_byte_size=physical_facts.byte_size,
        mass_recalibration_result=mass_recalibration_result,
    )


def validate_discovery_peak_artifact(
    receipt: DiscoveryPeakUniverseReceipt,
    peak_csv: Path,
) -> None:
    """Reject peak bytes that do not match the Discovery-owned artifact."""

    _require_receipt(receipt)
    fingerprint = fingerprint_file_content(peak_csv)
    if (
        fingerprint.content_sha256 != receipt.peak_csv_sha256
        or fingerprint.byte_size != receipt.peak_csv_byte_size
    ):
        raise IdentityContractError("peak CSV bytes do not match the Discovery receipt")


def validate_discovery_physical_facts_artifact(
    receipt: DiscoveryPeakUniverseReceipt,
    physical_facts_json: Path,
) -> None:
    """Reject physical-facts bytes that do not match the Discovery receipt."""

    _require_receipt(receipt)
    fingerprint = fingerprint_file_content(physical_facts_json)
    if (
        fingerprint.content_sha256 != receipt.physical_facts_sha256
        or fingerprint.byte_size != receipt.physical_facts_byte_size
    ):
        raise IdentityContractError(
            "physical-facts bytes do not match the Discovery receipt"
        )


def validate_discovery_peak_universe(
    receipt: DiscoveryPeakUniverseReceipt,
    *,
    acquisition_source: AcquisitionSourceIdentity,
    peaks: Sequence[DiscoveryPeak],
) -> tuple[DiscoveryPeak, ...]:
    """Check the parsed population and acquisition against its file receipt."""

    _require_receipt(receipt)
    if acquisition_source != receipt.acquisition_source:
        raise IdentityContractError(
            "current acquisition source does not match the Discovery receipt"
        )
    ordered = _canonical_peaks(peaks, sample_stem=receipt.sample_stem)
    if len(ordered) != receipt.peak_count:
        raise IdentityContractError("peak count does not match the Discovery receipt")
    return ordered


def discovery_receipt_index_fields(
    receipt: DiscoveryPeakUniverseReceipt,
) -> dict[str, str]:
    """Project one typed receipt into the batch-index CSV vocabulary."""

    _require_receipt(receipt)
    source = receipt.acquisition_source
    if source.local_file_revision is None:
        raise ValueError(
            "Discovery batch indexes require a local source revision; "
            "regenerate Discovery"
        )
    return {
        "peak_csv_schema_version": (receipt.peak_csv_schema_version),
        "peak_csv_sha256": receipt.peak_csv_sha256,
        "peak_csv_byte_size": str(receipt.peak_csv_byte_size),
        "physical_facts_sha256": receipt.physical_facts_sha256,
        "physical_facts_byte_size": str(receipt.physical_facts_byte_size),
        "acquisition_local_file_revision": json.dumps(
            source.local_file_revision, separators=(",", ":")
        ),
        "acquisition_byte_size": str(source.byte_size),
        "acquisition_format": source.acquisition_format,
        "observation_namespace": source.observation_namespace,
        **(
            {
                "mass_recalibration": serialize_mass_recalibration_result(
                    receipt.mass_recalibration_result
                )
            }
            if receipt.mass_recalibration_result.policy is not MassRecalibration.NONE
            else {}
        ),
    }


def discovery_receipt_from_index_fields(
    row: Mapping[str, str],
    *,
    sample_stem: str,
    peak_count: int,
) -> DiscoveryPeakUniverseReceipt:
    """Reconstruct and self-validate one batch-index receipt."""

    try:
        revision = json.loads(row["acquisition_local_file_revision"])
        if not isinstance(revision, list):
            raise IdentityContractError(
                "acquisition_local_file_revision must be an array"
            )
        source = AcquisitionSourceIdentity(
            content_sha256=None,
            local_file_revision=tuple(revision),
            byte_size=_nonnegative_int(
                row["acquisition_byte_size"],
                field="acquisition_byte_size",
            ),
            acquisition_format=row["acquisition_format"],
            observation_namespace=row["observation_namespace"],
        )
        return DiscoveryPeakUniverseReceipt(
            sample_stem=sample_stem,
            acquisition_source=source,
            peak_count=peak_count,
            peak_csv_schema_version=(row["peak_csv_schema_version"]),
            peak_csv_sha256=row["peak_csv_sha256"],
            peak_csv_byte_size=_nonnegative_int(
                row["peak_csv_byte_size"],
                field="peak_csv_byte_size",
            ),
            physical_facts_sha256=row["physical_facts_sha256"],
            physical_facts_byte_size=_nonnegative_int(
                row["physical_facts_byte_size"],
                field="physical_facts_byte_size",
            ),
            mass_recalibration_result=deserialize_mass_recalibration_result(
                row.get("mass_recalibration", "")
            ),
        )
    except KeyError as exc:
        raise IdentityContractError(
            f"Discovery receipt is missing {exc.args[0]}"
        ) from exc


def _canonical_peaks(
    peaks: Sequence[DiscoveryPeak],
    *,
    sample_stem: str,
) -> tuple[DiscoveryPeak, ...]:
    if isinstance(peaks, (str, bytes)):
        raise IdentityContractError("peak universe must contain DiscoveryPeak values")
    supplied = tuple(peaks)
    if any(not isinstance(item, DiscoveryPeak) for item in supplied):
        raise IdentityContractError("peak universe must contain DiscoveryPeak values")
    ordered = tuple(sorted(supplied, key=lambda item: item.peak_id))
    peak_ids = tuple(item.peak_id for item in ordered)
    if len(peak_ids) != len(set(peak_ids)):
        raise IdentityContractError("peak universe IDs must be unique")
    if any(item.sample_stem != sample_stem for item in ordered):
        raise IdentityContractError("peak universe contains a different sample")
    return ordered


def _require_receipt(value: object) -> DiscoveryPeakUniverseReceipt:
    if not isinstance(value, DiscoveryPeakUniverseReceipt):
        raise IdentityContractError("receipt must be a DiscoveryPeakUniverseReceipt")
    return value


def _required_text(value: object, *, field: str) -> str:
    return require_owner_normalized_text(
        value,
        field=field,
        error_factory=lambda msg: IdentityContractError(
            f"{field} must be non-empty normalized text"
        ),
    )


def _validate_digest(value: object, *, field: str) -> str:
    return require_lower_sha256(
        value,
        field=field,
        error_factory=lambda msg: IdentityContractError(
            f"{field} must be a lowercase SHA-256 digest"
        ),
    )


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as exc:
            raise IdentityContractError(
                f"{field} must be a non-negative integer"
            ) from exc
    return require_integer(
        value,
        field=field,
        non_negative=True,
        error_factory=IdentityContractError,
        type_error_factory=IdentityContractError,
    )


def canonical_sample_stems(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise IdentityContractError("sample stems must be a sequence")
    stems = tuple(values)
    if (
        not stems
        or len(stems) != len(set(stems))
        or any(not isinstance(item, str) or not item.strip() for item in stems)
    ):
        raise IdentityContractError("sample stems must be unique non-empty text")
    return stems


__all__ = (
    "DISCOVERY_BATCH_INDEX_COLUMNS",
    "DISCOVERY_BATCH_INDEX_SCHEMA_VERSION",
    "DISCOVERY_BATCH_RECEIPT_COLUMNS",
    "DISCOVERY_PEAK_CSV_SCHEMA_VERSION",
    "DiscoveryPeakUniverseReceipt",
    "discovery_receipt_from_index_fields",
    "discovery_receipt_index_fields",
    "mint_discovery_peak_universe_receipt",
    "validate_discovery_peak_artifact",
    "validate_discovery_physical_facts_artifact",
    "canonical_sample_stems",
    "validate_discovery_peak_universe",
)
