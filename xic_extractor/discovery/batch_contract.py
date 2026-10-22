"""Owner-backed Discovery candidate-universe receipts.

The receipt binds one sample's exact candidate CSV bytes and complete scientific
candidate payload to the acquisition content that Discovery inspected.  Paths
remain locators only and may change when the same content is replayed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.mass_recalibration import (
    VENDOR_MASS_RECALIBRATION_RESULT,
    MassRecalibration,
    MassRecalibrationResult,
    deserialize_mass_recalibration_result,
    serialize_mass_recalibration_result,
)
from xic_extractor.discovery.csv_writer import format_discovery_csv_value
from xic_extractor.discovery.models import (
    DISCOVERY_CANDIDATE_COLUMNS,
    DiscoveryCandidate,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    IdentityContractError,
    ProcessingRequestIdentity,
    fingerprint_file_content,
)
from xic_extractor.validation_primitives import (
    is_lower_sha256,
    require_integer,
    require_lower_sha256,
    require_owner_normalized_text,
)

DISCOVERY_BATCH_INDEX_SCHEMA_VERSION = "discovery_batch_index"
DISCOVERY_CANDIDATE_ARTIFACT_SCHEMA_VERSION = "discovery_candidates_csv"
DISCOVERY_CANDIDATE_PAYLOAD_SCHEMA_VERSION = "discovery_candidate_payload"
CANDIDATE_UNIVERSE_INPUT_KEY = "current_discovery_candidate_universe"
CANDIDATE_UNIVERSE_SCHEMA_VERSION = "current_discovery_candidate_universe"
ALIGNMENT_FEATURE_INPUT_KEY = "alignment_feature_candidate_ids"

DISCOVERY_BATCH_RECEIPT_COLUMNS = (
    "discovery_receipt_id",
    "candidate_artifact_schema_version",
    "candidate_artifact_sha256",
    "candidate_artifact_byte_size",
    "candidate_payload_sha256",
    "acquisition_source_id",
    "acquisition_content_sha256",
    "acquisition_byte_size",
    "acquisition_format",
    "observation_namespace",
)

_RECEIPT_PREFIX = "xic.discovery-candidate-universe:"


@dataclass(frozen=True, slots=True)
class DiscoveryCandidateUniverseReceipt:
    """One Discovery-owned, content-bound candidate-universe receipt."""

    discovery_receipt_id: str
    sample_stem: str
    acquisition_source: AcquisitionSourceIdentity
    candidate_count: int
    candidate_artifact_schema_version: str
    candidate_artifact_sha256: str
    candidate_artifact_byte_size: int
    candidate_payload_sha256: str
    mass_recalibration_result: MassRecalibrationResult = MassRecalibrationResult("none")

    def __post_init__(self) -> None:
        _required_text(self.sample_stem, field="sample_stem")
        if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
            raise IdentityContractError(
                "acquisition_source must be an AcquisitionSourceIdentity"
            )
        if (
            isinstance(self.candidate_count, bool)
            or not isinstance(self.candidate_count, int)
            or self.candidate_count < 0
        ):
            raise IdentityContractError(
                "candidate_count must be a non-negative integer"
            )
        if (
            self.candidate_artifact_schema_version
            != DISCOVERY_CANDIDATE_ARTIFACT_SCHEMA_VERSION
        ):
            raise IdentityContractError(
                "candidate artifact schema version is unsupported"
            )
        _validate_digest(
            self.candidate_artifact_sha256,
            field="candidate_artifact_sha256",
        )
        if (
            isinstance(self.candidate_artifact_byte_size, bool)
            or not isinstance(self.candidate_artifact_byte_size, int)
            or self.candidate_artifact_byte_size < 0
        ):
            raise IdentityContractError(
                "candidate_artifact_byte_size must be a non-negative integer"
            )
        _validate_digest(
            self.candidate_payload_sha256,
            field="candidate_payload_sha256",
        )
        expected = _receipt_id(
            candidate_artifact_schema_version=self.candidate_artifact_schema_version,
            sample_stem=self.sample_stem,
            acquisition_source=self.acquisition_source,
            candidate_count=self.candidate_count,
            candidate_artifact_sha256=self.candidate_artifact_sha256,
            candidate_artifact_byte_size=self.candidate_artifact_byte_size,
            candidate_payload_sha256=self.candidate_payload_sha256,
        )
        if self.discovery_receipt_id != expected:
            raise IdentityContractError(
                "discovery_receipt_id does not match its immutable owner facts"
            )


def mint_discovery_candidate_universe_receipt(
    *,
    sample_stem: str,
    acquisition_source: AcquisitionSourceIdentity,
    candidate_csv: Path,
    candidates: Sequence[DiscoveryCandidate],
    mass_recalibration_result: MassRecalibrationResult = (
        VENDOR_MASS_RECALIBRATION_RESULT
    ),
) -> DiscoveryCandidateUniverseReceipt:
    """Mint a receipt after the Discovery candidate writer has committed bytes."""

    stem = _required_text(sample_stem, field="sample_stem")
    if not isinstance(acquisition_source, AcquisitionSourceIdentity):
        raise IdentityContractError(
            "acquisition_source must be an AcquisitionSourceIdentity"
        )
    ordered = _canonical_candidates(candidates, sample_stem=stem)
    artifact = fingerprint_file_content(candidate_csv)
    payload_sha256 = discovery_candidate_payload_sha256(ordered)
    receipt_id = _receipt_id(
        sample_stem=stem,
        acquisition_source=acquisition_source,
        candidate_count=len(ordered),
        candidate_artifact_sha256=artifact.content_sha256,
        candidate_artifact_byte_size=artifact.byte_size,
        candidate_payload_sha256=payload_sha256,
    )
    return DiscoveryCandidateUniverseReceipt(
        discovery_receipt_id=receipt_id,
        sample_stem=stem,
        acquisition_source=acquisition_source,
        candidate_count=len(ordered),
        candidate_artifact_schema_version=(DISCOVERY_CANDIDATE_ARTIFACT_SCHEMA_VERSION),
        candidate_artifact_sha256=artifact.content_sha256,
        candidate_artifact_byte_size=artifact.byte_size,
        candidate_payload_sha256=payload_sha256,
        mass_recalibration_result=mass_recalibration_result,
    )


def validate_discovery_candidate_artifact(
    receipt: DiscoveryCandidateUniverseReceipt,
    candidate_csv: Path,
) -> None:
    """Reject candidate bytes that do not match the Discovery-owned artifact."""

    _require_receipt(receipt)
    fingerprint = fingerprint_file_content(candidate_csv)
    if (
        fingerprint.content_sha256 != receipt.candidate_artifact_sha256
        or fingerprint.byte_size != receipt.candidate_artifact_byte_size
    ):
        raise IdentityContractError(
            "candidate CSV bytes do not match the Discovery receipt"
        )


def validate_discovery_candidate_universe(
    receipt: DiscoveryCandidateUniverseReceipt,
    *,
    acquisition_source: AcquisitionSourceIdentity,
    candidates: Sequence[DiscoveryCandidate],
) -> tuple[DiscoveryCandidate, ...]:
    """Close parsed candidates and replay RAW content against one receipt."""

    _require_receipt(receipt)
    if acquisition_source != receipt.acquisition_source:
        raise IdentityContractError(
            "current acquisition source does not match the Discovery receipt"
        )
    ordered = _canonical_candidates(candidates, sample_stem=receipt.sample_stem)
    if len(ordered) != receipt.candidate_count:
        raise IdentityContractError(
            "candidate count does not match the Discovery receipt"
        )
    if discovery_candidate_payload_sha256(ordered) != receipt.candidate_payload_sha256:
        raise IdentityContractError(
            "candidate payload does not match the Discovery receipt"
        )
    return ordered


def discovery_candidate_payload_sha256(
    candidates: Sequence[DiscoveryCandidate],
) -> str:
    """Return the path-independent digest of complete candidate facts."""

    canonical: list[dict[str, str]] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        canonical.append(
            {
                column: format_discovery_csv_value(
                    column,
                    getattr(candidate, column),
                )
                for column in DISCOVERY_CANDIDATE_COLUMNS
                if column != "raw_file"
            }
        )
    encoded = json.dumps(
        {
            "schema_version": DISCOVERY_CANDIDATE_PAYLOAD_SCHEMA_VERSION,
            "candidates": canonical,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def discovery_receipt_index_fields(
    receipt: DiscoveryCandidateUniverseReceipt,
) -> dict[str, str]:
    """Project one typed receipt into the batch-index CSV vocabulary."""

    _require_receipt(receipt)
    source = receipt.acquisition_source
    return {
        "discovery_receipt_id": receipt.discovery_receipt_id,
        "candidate_artifact_schema_version": (
            receipt.candidate_artifact_schema_version
        ),
        "candidate_artifact_sha256": receipt.candidate_artifact_sha256,
        "candidate_artifact_byte_size": str(receipt.candidate_artifact_byte_size),
        "candidate_payload_sha256": receipt.candidate_payload_sha256,
        "acquisition_source_id": source.acquisition_source_id,
        "acquisition_content_sha256": source.content_sha256,
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
    candidate_count: int,
) -> DiscoveryCandidateUniverseReceipt:
    """Reconstruct and self-validate one batch-index receipt."""

    try:
        source = AcquisitionSourceIdentity(
            acquisition_source_id=row["acquisition_source_id"],
            content_sha256=row["acquisition_content_sha256"],
            byte_size=_nonnegative_int(
                row["acquisition_byte_size"],
                field="acquisition_byte_size",
            ),
            acquisition_format=row["acquisition_format"],
            observation_namespace=row["observation_namespace"],
        )
        return DiscoveryCandidateUniverseReceipt(
            discovery_receipt_id=row["discovery_receipt_id"],
            sample_stem=sample_stem,
            acquisition_source=source,
            candidate_count=candidate_count,
            candidate_artifact_schema_version=(
                row["candidate_artifact_schema_version"]
            ),
            candidate_artifact_sha256=row["candidate_artifact_sha256"],
            candidate_artifact_byte_size=_nonnegative_int(
                row["candidate_artifact_byte_size"],
                field="candidate_artifact_byte_size",
            ),
            candidate_payload_sha256=row["candidate_payload_sha256"],
            mass_recalibration_result=deserialize_mass_recalibration_result(
                row.get("mass_recalibration", "")
            ),
        )
    except KeyError as exc:
        raise IdentityContractError(
            f"Discovery receipt is missing {exc.args[0]}"
        ) from exc


def _canonical_candidates(
    candidates: Sequence[DiscoveryCandidate],
    *,
    sample_stem: str,
) -> tuple[DiscoveryCandidate, ...]:
    if isinstance(candidates, (str, bytes)):
        raise IdentityContractError(
            "candidate universe must contain DiscoveryCandidate values"
        )
    supplied = tuple(candidates)
    if any(not isinstance(item, DiscoveryCandidate) for item in supplied):
        raise IdentityContractError(
            "candidate universe must contain DiscoveryCandidate values"
        )
    ordered = tuple(sorted(supplied, key=lambda item: item.candidate_id))
    candidate_ids = tuple(item.candidate_id for item in ordered)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise IdentityContractError("candidate universe IDs must be unique")
    if any(
        item.sample_stem != sample_stem or item.raw_file.stem != sample_stem
        for item in ordered
    ):
        raise IdentityContractError("candidate universe contains a different sample")
    return ordered


def _receipt_id(
    *,
    candidate_artifact_schema_version: str = (
        DISCOVERY_CANDIDATE_ARTIFACT_SCHEMA_VERSION
    ),
    sample_stem: str,
    acquisition_source: AcquisitionSourceIdentity,
    candidate_count: int,
    candidate_artifact_sha256: str,
    candidate_artifact_byte_size: int,
    candidate_payload_sha256: str,
) -> str:
    basis = {
        "schema_version": "discovery_candidate_universe_receipt",
        "sample_stem": sample_stem,
        "acquisition_source_id": acquisition_source.acquisition_source_id,
        "candidate_count": candidate_count,
        "candidate_artifact_schema_version": candidate_artifact_schema_version,
        "candidate_artifact_sha256": candidate_artifact_sha256,
        "candidate_artifact_byte_size": candidate_artifact_byte_size,
        "candidate_payload_sha256": candidate_payload_sha256,
    }
    digest = hashlib.sha256(
        json.dumps(
            basis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return f"{_RECEIPT_PREFIX}{digest}"


def _require_receipt(value: object) -> DiscoveryCandidateUniverseReceipt:
    if not isinstance(value, DiscoveryCandidateUniverseReceipt):
        raise IdentityContractError(
            "receipt must be a DiscoveryCandidateUniverseReceipt"
        )
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


def validate_candidate_universe_payload(
    payload: object,
    *,
    expected_sample_stems: Sequence[str],
) -> tuple[dict[str, object], ...]:
    expected = tuple(sorted(canonical_sample_stems(expected_sample_stems)))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "samples",
    }:
        raise IdentityContractError("candidate universe must use the exact envelope")
    if payload.get("schema_version") != CANDIDATE_UNIVERSE_SCHEMA_VERSION:
        raise IdentityContractError("candidate universe schema is unsupported")
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise IdentityContractError("candidate universe samples must be a list")
    typed: list[dict[str, object]] = []
    for entry in samples:
        if not isinstance(entry, dict) or set(entry) != {
            "sample_stem",
            "candidate_count",
            "candidate_ids",
            "candidate_payload_sha256",
            "discovery_receipt_id",
            "candidate_artifact_schema_version",
            "candidate_artifact_sha256",
            "candidate_artifact_byte_size",
            "acquisition_source_id",
        }:
            raise IdentityContractError(
                "candidate universe entry must use the exact current shape"
            )
        stem = entry.get("sample_stem")
        count = entry.get("candidate_count")
        ids = entry.get("candidate_ids")
        digest = entry.get("candidate_payload_sha256")
        if not isinstance(stem, str) or not stem.strip() or stem != stem.strip():
            raise IdentityContractError("candidate universe sample stem is invalid")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
            or not isinstance(ids, list)
            or ids != sorted(set(ids))
            or count != len(ids)
            or any(not isinstance(item, str) or not item.strip() for item in ids)
        ):
            raise IdentityContractError("candidate universe IDs are invalid")
        if not is_lower_sha256(digest):
            raise IdentityContractError("candidate universe digest is invalid")
        if (
            not isinstance(entry.get("discovery_receipt_id"), str)
            or not entry["discovery_receipt_id"].startswith(
                "xic.discovery-candidate-universe:"
            )
            or not isinstance(entry.get("candidate_artifact_schema_version"), str)
            or entry["candidate_artifact_schema_version"]
            != DISCOVERY_CANDIDATE_ARTIFACT_SCHEMA_VERSION
            or not is_lower_sha256(entry.get("candidate_artifact_sha256"))
            or isinstance(entry.get("candidate_artifact_byte_size"), bool)
            or not isinstance(entry.get("candidate_artifact_byte_size"), int)
            or entry["candidate_artifact_byte_size"] < 0
            or not isinstance(entry.get("acquisition_source_id"), str)
            or not entry["acquisition_source_id"].startswith("xic.acquisition-source:")
        ):
            raise IdentityContractError(
                "candidate universe owner receipt facts are invalid"
            )
        typed.append(entry)
    if tuple(entry["sample_stem"] for entry in typed) != expected:
        raise IdentityContractError(
            "candidate universe must bind every acquisition sample exactly once"
        )
    return tuple(typed)


def validate_alignment_feature_selection(
    payload: object,
    *,
    candidate_ids: Iterable[str],
) -> tuple[str, ...] | None:
    """Read an optional canonical feature subset without choosing its members."""
    if payload is None:
        return None
    if (
        not isinstance(payload, list)
        or any(not isinstance(item, str) or not item for item in payload)
        or payload != sorted(set(payload))
    ):
        raise IdentityContractError("alignment feature candidate IDs are malformed")
    if not set(payload).issubset(candidate_ids):
        raise IdentityContractError(
            "alignment feature candidates are outside the Discovery universe"
        )
    return tuple(payload)


def candidate_sample_stems_from_request(
    request: ProcessingRequestIdentity,
    *,
    expected_sample_stems: Sequence[str],
) -> tuple[dict[str, str], tuple[str, ...] | None]:
    """Decode selected candidate/sample facts and the optional feature selection.

    Sample identity and method-subject binding remain with the consumer. A
    missing selection retains the full universe; an empty selection retains none.
    """
    values = json.loads(request.normalized_scientific_inputs_json)
    entries = validate_candidate_universe_payload(
        values.get(CANDIDATE_UNIVERSE_INPUT_KEY),
        expected_sample_stems=expected_sample_stems,
    )
    sample_stems: dict[str, str] = {}
    for entry in entries:
        stem = entry["sample_stem"]
        ids = entry["candidate_ids"]
        assert isinstance(stem, str)
        assert isinstance(ids, list)
        for candidate_id in ids:
            if candidate_id in sample_stems:
                raise IdentityContractError("candidate/sample universe is not unique")
            sample_stems[candidate_id] = stem
    feature_ids = validate_alignment_feature_selection(
        values.get(ALIGNMENT_FEATURE_INPUT_KEY), candidate_ids=sample_stems
    )
    if feature_ids is not None:
        sample_stems = {cid: sample_stems[cid] for cid in feature_ids}
    return sample_stems, feature_ids


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
    "ALIGNMENT_FEATURE_INPUT_KEY",
    "CANDIDATE_UNIVERSE_INPUT_KEY",
    "CANDIDATE_UNIVERSE_SCHEMA_VERSION",
    "DISCOVERY_BATCH_INDEX_SCHEMA_VERSION",
    "DISCOVERY_BATCH_RECEIPT_COLUMNS",
    "DISCOVERY_CANDIDATE_ARTIFACT_SCHEMA_VERSION",
    "DiscoveryCandidateUniverseReceipt",
    "discovery_candidate_payload_sha256",
    "discovery_receipt_from_index_fields",
    "discovery_receipt_index_fields",
    "mint_discovery_candidate_universe_receipt",
    "validate_discovery_candidate_artifact",
    "canonical_sample_stems",
    "candidate_sample_stems_from_request",
    "validate_alignment_feature_selection",
    "validate_candidate_universe_payload",
    "validate_discovery_candidate_universe",
)
