"""Closed artifact planning and delivery receipts for finalized CID-NL."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.output_levels import AlignmentOutputLevel
from xic_extractor.alignment.pipeline_outputs import AlignmentRunOutputs, output_paths

_AUTHORITY_SCOPE = "cid_nl_finalized_decisions_v1"


@dataclass(frozen=True, slots=True)
class UntargetedCidNlArtifactPlan:
    outputs: AlignmentRunOutputs
    authority_manifest_sha256: str
    authority_scope: str
    capture_artifact_fingerprints: bool


@dataclass(frozen=True, slots=True)
class UntargetedCidNlDeliveryReceipt:
    authority_scope: str
    authority_manifest_sha256: str
    artifact_paths: tuple[Path, ...]


def compile_untargeted_cid_nl_artifact_plan(
    *,
    output_dir: Path,
    output_level: AlignmentOutputLevel,
    emit_provisional_matrix: bool,
    emit_alignment_cells: bool,
    emit_alignment_status_matrix: bool,
    emit_alignment_integration_audit: bool,
    emit_alignment_backfill_seed_audit: bool,
    emit_alignment_backfill_candidate_audit: bool,
    capture_artifact_fingerprints: bool,
) -> UntargetedCidNlArtifactPlan:
    """Compile delivery intent only after validating the registered authority."""

    if output_level in {"debug", "validation"}:
        raise ValueError(
            "finalized CID-NL does not support legacy ownership debug ledgers"
        )
    unsupported = {
        "alignment status matrix": emit_alignment_status_matrix,
        "integration audit": emit_alignment_integration_audit,
        "backfill candidate audit": emit_alignment_backfill_candidate_audit,
    }
    requested = tuple(name for name, enabled in unsupported.items() if enabled)
    if requested:
        raise ValueError(
            "finalized CID-NL cannot render requested legacy artifacts: "
            + ", ".join(requested)
        )
    manifest_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "superpowers"
        / "schemas"
        / "productization_authority_manifest.v1.json"
    )
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    allowed = manifest.get("authority_policy", {}).get(
        "product_writer_allowed_scopes", []
    )
    current = manifest.get("current_authority", {}).get("cid_nl", {})
    if (
        manifest.get("schema_version") != "productization_authority_manifest_v1"
        or manifest.get("status") != "active"
        or _AUTHORITY_SCOPE not in allowed
        or current.get("authority_scope") != _AUTHORITY_SCOPE
        or current.get("authority_source") != "completed_untarget_batch_workflow_result"
    ):
        raise ValueError("finalized CID-NL writer authority is not active")
    outputs = output_paths(
        output_dir,
        output_level=output_level,
        emit_alignment_cells=emit_alignment_cells,
        emit_alignment_status_matrix=False,
        emit_alignment_integration_audit=False,
        emit_alignment_backfill_seed_audit=emit_alignment_backfill_seed_audit,
        emit_alignment_backfill_candidate_audit=False,
        emit_provisional_matrix=emit_provisional_matrix,
    )
    return UntargetedCidNlArtifactPlan(
        outputs=outputs,
        authority_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        authority_scope=_AUTHORITY_SCOPE,
        capture_artifact_fingerprints=capture_artifact_fingerprints,
    )


__all__ = [
    "UntargetedCidNlArtifactPlan",
    "UntargetedCidNlDeliveryReceipt",
    "compile_untargeted_cid_nl_artifact_plan",
]
