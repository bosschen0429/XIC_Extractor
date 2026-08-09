"""Owner-minted content receipts for committed Alignment artifacts."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from pathlib import Path
from typing import final

from xic_extractor.processing_identity import FileContentFingerprint

_ALIGNMENT_ARTIFACT_RECEIPT_MINT_TOKEN = object()


@final
@dataclass(frozen=True, slots=True)
class AlignmentArtifactFingerprintReceipt:
    """Immutable content facts minted by the Alignment writer after commit."""

    artifact_name: str
    artifact_path: Path
    fingerprint: FileContentFingerprint
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("AlignmentArtifactFingerprintReceipt is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _ALIGNMENT_ARTIFACT_RECEIPT_MINT_TOKEN:
            raise ValueError(
                "AlignmentArtifactFingerprintReceipt must be minted by the "
                "Alignment writer"
            )
        if (
            not self.artifact_name
            or Path(self.artifact_name).name != self.artifact_name
            or self.artifact_name in {".", ".."}
        ):
            raise ValueError("artifact_name must be one file name")
        if not isinstance(self.artifact_path, Path):
            raise TypeError("artifact_path must be a Path")
        if not self.artifact_path.is_absolute():
            raise ValueError("artifact_path must be absolute")
        if self.artifact_path.name != self.artifact_name:
            raise ValueError("artifact_name must match artifact_path")
        if self.artifact_path.resolve(strict=True) != self.artifact_path:
            raise ValueError("artifact_path must be canonical")
        if not isinstance(self.fingerprint, FileContentFingerprint):
            raise TypeError("fingerprint must be a FileContentFingerprint")


def _mint_alignment_artifact_fingerprint_receipt(
    *,
    artifact_path: Path,
    fingerprint: FileContentFingerprint,
) -> AlignmentArtifactFingerprintReceipt:
    resolved_path = artifact_path.resolve(strict=True)
    return AlignmentArtifactFingerprintReceipt(
        artifact_name=resolved_path.name,
        artifact_path=resolved_path,
        fingerprint=fingerprint,
        _mint_token=_ALIGNMENT_ARTIFACT_RECEIPT_MINT_TOKEN,
    )


__all__ = [
    "AlignmentArtifactFingerprintReceipt",
]
