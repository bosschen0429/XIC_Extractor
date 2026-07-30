"""Executable Package 0 parity and frozen successor-comparison receipt checks."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from xic_extractor.alignment.runtime_adapter import AlignmentRuntimeApplicationResult
from xic_extractor.execution_runtime import AttemptTerminalOutcome
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    canonical_coordinator_terminal_record_bytes,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedRuntimeLifecycleApplicationResult,
)
from xic_extractor.peak_detection.observation_batch import (
    canonical_peak_observation_batch_bytes,
)
from xic_extractor.processing_identity import ProcessingRequestIdentity


class Package0ComparatorError(ValueError):
    """Raised when an adapter or successor receipt differs from the frozen gate."""


_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_PACKAGE1_DIFFERENCE_SCHEMA = "package1_runtime_observation_difference_v1"
_FROZEN_PACKAGE1_SURFACE_SHA256 = (
    "70099C0412D9301736CA479F05BEBF1D7F928C248407ED062D1EFBBA0D045B6B"
)
_PACKAGE1_DIFFERENCE_FIELDS = (
    "schema_version",
    "stage",
    "stable_subject_or_cell",
    "old_value",
    "new_value",
    "reason",
    "preserved_invariant",
    "product_impact",
    "classification",
    "disposition",
)


@dataclass(frozen=True, slots=True)
class _TargetedAdapterParityReceipt:
    adapter_name: str
    processing_request_id: str
    attempt_semantics_sha256: str
    lineage_sha256: str
    scientific_results_sha256: str
    delivery_projection_sha256: str
    baseline_stage_xic_results_sha256: str
    final_stage_xic_results_sha256: str

    def __post_init__(self) -> None:
        if self.adapter_name not in {"cli", "gui"}:
            raise Package0ComparatorError("adapter_name must be cli or gui")
        if not self.processing_request_id.startswith("xic.processing-request.v1:"):
            raise Package0ComparatorError(
                "processing_request_id must be owner-minted"
            )
        for field in (
            "attempt_semantics_sha256",
            "lineage_sha256",
            "scientific_results_sha256",
            "delivery_projection_sha256",
            "baseline_stage_xic_results_sha256",
            "final_stage_xic_results_sha256",
        ):
            _sha256(getattr(self, field), field)


@dataclass(frozen=True, slots=True)
class _Package0SuccessorObservation:
    baseline_id: str
    canonical_output_inventory_sha256: str
    canonical_artifact_path_sha256: str
    ordered_sample_sha256: str
    ordered_final_header_sha256: str
    baseline_stage_xic_results_sha256: str
    final_stage_xic_results_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.baseline_id, str) or not self.baseline_id.strip():
            raise Package0ComparatorError("baseline_id must be non-empty")
        for field in (
            "canonical_output_inventory_sha256",
            "canonical_artifact_path_sha256",
            "ordered_sample_sha256",
            "ordered_final_header_sha256",
            "baseline_stage_xic_results_sha256",
            "final_stage_xic_results_sha256",
        ):
            _sha256(getattr(self, field), field)


@dataclass(frozen=True, slots=True)
class Package1DifferenceObservation:
    """Observed no-write receipt for one Package 1 runtime consumer run."""

    frozen_surface_sha256: str
    processing_request_id: str
    processing_attempt_id: str
    targeted_coordinator_sha256: str
    alignment_coordinator_sha256: str
    targeted_observation_batches_sha256: str
    alignment_observation_batches_sha256: str
    observation_batch_count: int
    targeted_terminal_dispositions_sha256: str
    alignment_terminal_dispositions_sha256: str
    targeted_offered_artifact_count: int
    alignment_offered_artifact_count: int
    terminal_non_offered_count: int
    targeted_legacy_runner_call_count: int
    successor_product_output_paths: tuple[str, ...]
    scientific_projection_status: str
    matrix_projection_status: str
    writer_status: str
    validation_status: str

    def __post_init__(self) -> None:
        for field in (
            "frozen_surface_sha256",
            "targeted_coordinator_sha256",
            "alignment_coordinator_sha256",
            "targeted_observation_batches_sha256",
            "alignment_observation_batches_sha256",
            "targeted_terminal_dispositions_sha256",
            "alignment_terminal_dispositions_sha256",
        ):
            _sha256(getattr(self, field), field)
        if not self.processing_request_id.startswith("xic.processing-request.v1:"):
            raise Package0ComparatorError(
                "Package 1 observation must bind an owner request"
            )
        if not self.processing_attempt_id.startswith("xic.processing-attempt.v1:"):
            raise Package0ComparatorError(
                "Package 1 observation must bind an owner attempt"
            )
        for field in (
            "observation_batch_count",
            "targeted_offered_artifact_count",
            "alignment_offered_artifact_count",
            "terminal_non_offered_count",
            "targeted_legacy_runner_call_count",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise Package0ComparatorError(f"{field} must be non-negative")
        if not isinstance(self.successor_product_output_paths, tuple) or any(
            not isinstance(path, str) or not path
            for path in self.successor_product_output_paths
        ):
            raise Package0ComparatorError(
                "successor_product_output_paths must be a text tuple"
            )


def mint_package1_difference_observation(
    *,
    targeted: TargetedRuntimeLifecycleApplicationResult,
    alignment: AlignmentRuntimeApplicationResult,
    targeted_legacy_runner_call_count: int,
    successor_product_output_paths: Sequence[str],
) -> Package1DifferenceObservation:
    """Derive the no-write receipt from actual dual-consumer runtime results."""

    if not isinstance(targeted, TargetedRuntimeLifecycleApplicationResult):
        raise Package0ComparatorError(
            "targeted must be a validated Targeted runtime lifecycle result"
        )
    if not isinstance(alignment, AlignmentRuntimeApplicationResult):
        raise Package0ComparatorError(
            "alignment must be a validated Alignment runtime application result"
        )
    targeted_attempt = targeted.settlement.runtime.manifest.attempt
    alignment_attempt = alignment.runtime.manifest.attempt
    if targeted_attempt != alignment_attempt:
        raise Package0ComparatorError(
            "Targeted and Alignment results must bind one request and attempt"
        )
    if (
        targeted.coordinator_record.proposed_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
        or alignment.coordinator_record.proposed_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
        or targeted.finalization_record.terminal_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
        or alignment.finalization_record.terminal_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
    ):
        raise Package0ComparatorError(
            "Package 1 difference receipt requires successful terminal results"
        )
    targeted_batches = tuple(
        (key, canonical_peak_observation_batch_bytes(batch))
        for key, batch in targeted.settlement.successful_observation_batches
    )
    alignment_batches = tuple(
        (key, canonical_peak_observation_batch_bytes(batch))
        for key, batch in alignment.successful_observation_batches
    )
    if not targeted_batches or targeted_batches != alignment_batches:
        raise Package0ComparatorError(
            "Targeted and Alignment observation batch receipts must preserve work keys "
            "and be canonical-equal"
        )
    targeted_dispositions = targeted.finalization_record.artifact_dispositions
    alignment_dispositions = alignment.finalization_record.artifact_dispositions
    expected_observation_ids = {
        artifact.evidence_artifact_id
        for _key, batch in targeted.settlement.successful_observation_batches
        for artifact in batch.observation_artifacts
    }
    if not expected_observation_ids.issubset(
        {artifact_id for artifact_id, _disposition in targeted_dispositions}
    ) or not expected_observation_ids.issubset(
        {artifact_id for artifact_id, _disposition in alignment_dispositions}
    ):
        raise Package0ComparatorError(
            "terminal results must disposition every observation artifact"
        )
    non_offered = sum(
        disposition is not RuntimeArtifactDisposition.OFFERED_TO_OWNER
        for dispositions in (targeted_dispositions, alignment_dispositions)
        for _artifact_id, disposition in dispositions
    )
    targeted_coordinator_bytes = canonical_coordinator_terminal_record_bytes(
        targeted.coordinator_record
    )
    alignment_coordinator_bytes = canonical_coordinator_terminal_record_bytes(
        alignment.coordinator_record
    )
    return Package1DifferenceObservation(
        frozen_surface_sha256=_FROZEN_PACKAGE1_SURFACE_SHA256,
        processing_request_id=targeted_attempt.processing_request_id,
        processing_attempt_id=targeted_attempt.processing_attempt_id,
        targeted_coordinator_sha256=hashlib.sha256(
            targeted_coordinator_bytes
        ).hexdigest().upper(),
        alignment_coordinator_sha256=hashlib.sha256(
            alignment_coordinator_bytes
        ).hexdigest().upper(),
        targeted_observation_batches_sha256=_canonical_sha256(
            {
                "items": [
                    {
                        "work_unit_key": key,
                        "sha256": hashlib.sha256(item).hexdigest(),
                    }
                    for key, item in targeted_batches
                ]
            }
        ),
        alignment_observation_batches_sha256=_canonical_sha256(
            {
                "items": [
                    {
                        "work_unit_key": key,
                        "sha256": hashlib.sha256(item).hexdigest(),
                    }
                    for key, item in alignment_batches
                ]
            }
        ),
        observation_batch_count=len(targeted_batches),
        targeted_terminal_dispositions_sha256=_canonical_sha256(
            {
                "items": [
                    (artifact_id, disposition.value)
                    for artifact_id, disposition in targeted_dispositions
                ]
            }
        ),
        alignment_terminal_dispositions_sha256=_canonical_sha256(
            {
                "items": [
                    (artifact_id, disposition.value)
                    for artifact_id, disposition in alignment_dispositions
                ]
            }
        ),
        targeted_offered_artifact_count=sum(
            disposition is RuntimeArtifactDisposition.OFFERED_TO_OWNER
            for _artifact_id, disposition in targeted_dispositions
        ),
        alignment_offered_artifact_count=sum(
            disposition is RuntimeArtifactDisposition.OFFERED_TO_OWNER
            for _artifact_id, disposition in alignment_dispositions
        ),
        terminal_non_offered_count=non_offered,
        targeted_legacy_runner_call_count=targeted_legacy_runner_call_count,
        successor_product_output_paths=tuple(successor_product_output_paths),
        scientific_projection_status="not_emitted",
        matrix_projection_status="not_emitted",
        writer_status="not_invoked",
        validation_status="diagnostic_only",
    )


def mint_targeted_adapter_parity_receipt(
    *,
    adapter_name: str,
    processing_request: ProcessingRequestIdentity,
    attempt_semantics: bytes,
    lineage: bytes,
    scientific_results: bytes,
    delivery_projection: bytes,
    baseline_stage_xic_results: Path,
    final_stage_xic_results: Path,
) -> _TargetedAdapterParityReceipt:
    """Mint one parity receipt only from owner identity and actual bytes/files."""

    if not isinstance(processing_request, ProcessingRequestIdentity):
        raise Package0ComparatorError(
            "processing_request must be an owner-minted ProcessingRequestIdentity"
        )
    return _TargetedAdapterParityReceipt(
        adapter_name=adapter_name,
        processing_request_id=processing_request.processing_request_id,
        attempt_semantics_sha256=_bytes_sha256(
            attempt_semantics, field="attempt_semantics"
        ),
        lineage_sha256=_bytes_sha256(lineage, field="lineage"),
        scientific_results_sha256=_bytes_sha256(
            scientific_results, field="scientific_results"
        ),
        delivery_projection_sha256=_bytes_sha256(
            delivery_projection, field="delivery_projection"
        ),
        baseline_stage_xic_results_sha256=_file_sha256(
            baseline_stage_xic_results,
            field="baseline_stage_xic_results",
        ),
        final_stage_xic_results_sha256=_file_sha256(
            final_stage_xic_results,
            field="final_stage_xic_results",
        ),
    )


def assert_targeted_adapter_parity(
    cli: _TargetedAdapterParityReceipt,
    gui: _TargetedAdapterParityReceipt,
) -> str:
    if not isinstance(cli, _TargetedAdapterParityReceipt) or cli.adapter_name != "cli":
        raise Package0ComparatorError("cli must be a CLI parity receipt")
    if not isinstance(gui, _TargetedAdapterParityReceipt) or gui.adapter_name != "gui":
        raise Package0ComparatorError("gui must be a GUI parity receipt")
    cli_basis = _adapter_basis(cli)
    gui_basis = _adapter_basis(gui)
    if cli_basis != gui_basis:
        differences = sorted(
            key for key in cli_basis if cli_basis[key] != gui_basis[key]
        )
        raise Package0ComparatorError(
            f"CLI/GUI adapter parity differs: {', '.join(differences)}"
        )
    return _canonical_sha256(cli_basis)


def validate_package0_successor_output(
    contract: Mapping[str, object],
    membership_receipt: Mapping[str, object],
    *,
    output_root: Path,
    cli: _TargetedAdapterParityReceipt,
    gui: _TargetedAdapterParityReceipt,
) -> str:
    """Derive and validate the frozen comparator from actual successor files."""

    observation = _observe_package0_successor_output(
        contract,
        membership_receipt,
        output_root=output_root,
    )
    return _validate_package0_successor_observation(
        contract,
        observation,
        cli=cli,
        gui=gui,
    )


def _validate_package0_successor_observation(
    contract: Mapping[str, object],
    observation: _Package0SuccessorObservation,
    *,
    cli: _TargetedAdapterParityReceipt,
    gui: _TargetedAdapterParityReceipt,
) -> str:
    if contract.get("authority") != {
        "product_authority_granted": False,
        "validation_status": "diagnostic_only",
    }:
        raise Package0ComparatorError(
            "Package 0 comparator must remain diagnostic_only"
        )
    delivery_scope = contract.get("delivery_scope")
    if not isinstance(delivery_scope, Mapping) or (
        delivery_scope.get("issue_149_completion") != "complete"
        or delivery_scope.get("deferred") != []
    ):
        raise Package0ComparatorError(
            "Package 0 comparator contract is not formally complete"
        )
    comparator = contract.get("legacy_comparator")
    if not isinstance(comparator, Mapping):
        raise Package0ComparatorError("legacy_comparator contract is missing")
    expected = {
        "baseline_id": contract.get("baseline_id"),
        "canonical_output_inventory_sha256": contract.get(
            "canonical_output_inventory_sha256"
        ),
        "canonical_artifact_path_sha256": comparator.get(
            "canonical_artifact_path_sha256"
        ),
        "ordered_sample_sha256": comparator.get("ordered_sample_sha256"),
        "ordered_final_header_sha256": comparator.get(
            "ordered_final_header_sha256"
        ),
        "baseline_stage_xic_results_sha256": comparator.get(
            "baseline_stage_xic_results_sha256"
        ),
        "final_stage_xic_results_sha256": comparator.get(
            "final_stage_xic_results_sha256"
        ),
    }
    observed = {
        field: getattr(observation, field)
        for field in expected
    }
    differences = sorted(
        field for field, value in expected.items() if observed[field] != value
    )
    if differences:
        raise Package0ComparatorError(
            f"successor differs from frozen comparator: {', '.join(differences)}"
        )
    adapter_digest = assert_targeted_adapter_parity(cli, gui)
    if (
        cli.baseline_stage_xic_results_sha256
        != observation.baseline_stage_xic_results_sha256
        or cli.final_stage_xic_results_sha256
        != observation.final_stage_xic_results_sha256
    ):
        raise Package0ComparatorError(
            "adapter receipts do not bind the frozen Targeted CSV digests"
        )
    return _canonical_sha256(
        {"observation": observed, "adapter_parity_sha256": adapter_digest}
    )


def validate_package1_difference_manifest(
    contract: Mapping[str, object],
    rows: Sequence[Mapping[str, str]],
    *,
    observation: Package1DifferenceObservation,
) -> str:
    """Validate Package 1's internal diff and frozen exact-zero comparator row."""

    if contract.get("authority") != {
        "product_authority_granted": False,
        "validation_status": "diagnostic_only",
    }:
        raise Package0ComparatorError(
            "Package 1 difference gate must reuse the diagnostic-only comparator"
        )
    frozen_digest = _package1_frozen_surface_digest(contract)
    if frozen_digest != _FROZEN_PACKAGE1_SURFACE_SHA256:
        raise Package0ComparatorError(
            "Package 1 comparator contract differs from the immutable baseline anchor"
        )
    if not isinstance(observation, Package1DifferenceObservation) or (
        observation.frozen_surface_sha256 != _FROZEN_PACKAGE1_SURFACE_SHA256
        or observation.targeted_observation_batches_sha256
        != observation.alignment_observation_batches_sha256
        or observation.observation_batch_count < 1
        or observation.targeted_offered_artifact_count < 1
        or observation.targeted_offered_artifact_count
        != observation.alignment_offered_artifact_count
        or observation.terminal_non_offered_count != 0
        or observation.targeted_legacy_runner_call_count != 0
        or observation.successor_product_output_paths
        or observation.scientific_projection_status != "not_emitted"
        or observation.matrix_projection_status != "not_emitted"
        or observation.writer_status != "not_invoked"
        or observation.validation_status != "diagnostic_only"
    ):
        raise Package0ComparatorError(
            "Package 1 run emitted or invoked a frozen product surface"
        )
    expected = (
        {
            "schema_version": _PACKAGE1_DIFFERENCE_SCHEMA,
            "stage": "package1_runtime_observation",
            "stable_subject_or_cell": (
                "targeted_alignment_process_result_carrier"
            ),
            "old_value": "owner_result_ref_without_observation_batch",
            "new_value": "owner_result_ref_with_immutable_observation_batch",
            "reason": "compose_existing_acquisition_and_peak_observation_owners",
            "preserved_invariant": (
                "legacy_product_science_matrix_writer_authority_and_bytes_unchanged"
            ),
            "product_impact": "none",
            "classification": "explained_internal_runtime_difference",
            "disposition": "accept_diagnostic_only",
        },
        {
            "schema_version": _PACKAGE1_DIFFERENCE_SCHEMA,
            "stage": "package1_runtime_observation",
            "stable_subject_or_cell": (
                "frozen_legacy_product_scientific_matrix_writer"
            ),
            "old_value": f"sha256:{_FROZEN_PACKAGE1_SURFACE_SHA256}",
            "new_value": f"sha256:{_FROZEN_PACKAGE1_SURFACE_SHA256}",
            "reason": "package1_path_does_not_invoke_legacy_runner_or_writer",
            "preserved_invariant": (
                "frozen_package0_inventory_membership_and_csv_bytes_exact"
            ),
            "product_impact": "none",
            "classification": (
                "exact_zero_product_scientific_matrix_writer"
            ),
            "disposition": "pass",
        },
    )
    normalized = tuple(
        {field: row.get(field, "") for field in _PACKAGE1_DIFFERENCE_FIELDS}
        for row in rows
    )
    if any(set(row) != set(_PACKAGE1_DIFFERENCE_FIELDS) for row in rows):
        raise Package0ComparatorError(
            "Package 1 DifferenceManifest fields do not match the reviewed schema"
        )
    if normalized != expected:
        raise Package0ComparatorError(
            "Package 1 DifferenceManifest differs from the frozen exact-zero gate"
        )
    return _canonical_sha256(
        {
            "schema_version": _PACKAGE1_DIFFERENCE_SCHEMA,
            "observation": {
                "frozen_surface_sha256": observation.frozen_surface_sha256,
                "processing_request_id": observation.processing_request_id,
                "processing_attempt_id": observation.processing_attempt_id,
                "targeted_coordinator_sha256": (
                    observation.targeted_coordinator_sha256
                ),
                "alignment_coordinator_sha256": (
                    observation.alignment_coordinator_sha256
                ),
                "targeted_observation_batches_sha256": (
                    observation.targeted_observation_batches_sha256
                ),
                "alignment_observation_batches_sha256": (
                    observation.alignment_observation_batches_sha256
                ),
                "observation_batch_count": observation.observation_batch_count,
                "targeted_terminal_dispositions_sha256": (
                    observation.targeted_terminal_dispositions_sha256
                ),
                "alignment_terminal_dispositions_sha256": (
                    observation.alignment_terminal_dispositions_sha256
                ),
                "targeted_offered_artifact_count": (
                    observation.targeted_offered_artifact_count
                ),
                "alignment_offered_artifact_count": (
                    observation.alignment_offered_artifact_count
                ),
                "terminal_non_offered_count": observation.terminal_non_offered_count,
                "targeted_legacy_runner_call_count": (
                    observation.targeted_legacy_runner_call_count
                ),
                "successor_product_output_paths": (
                    observation.successor_product_output_paths
                ),
                "scientific_projection_status": (
                    observation.scientific_projection_status
                ),
                "matrix_projection_status": observation.matrix_projection_status,
                "writer_status": observation.writer_status,
                "validation_status": observation.validation_status,
            },
            "rows": normalized,
        }
    )


def _package1_frozen_surface_digest(contract: Mapping[str, object]) -> str:
    comparator = contract.get("legacy_comparator")
    if not isinstance(comparator, Mapping):
        raise Package0ComparatorError("legacy_comparator contract is missing")
    fields = (
        "baseline_stage_xic_results_sha256",
        "final_stage_xic_results_sha256",
        "ordered_sample_sha256",
        "ordered_final_header_sha256",
        "sample_row_count_per_stage",
        "column_count_per_stage",
        "sample_identity_cell_count_per_stage",
        "analytical_cell_count_per_stage",
        "target_count",
        "fields_per_target",
        "required_exact_globs",
    )
    basis = {
        "canonical_output_inventory_sha256": contract.get(
            "canonical_output_inventory_sha256"
        ),
        **{field: comparator.get(field) for field in fields},
    }
    if any(value is None for value in basis.values()):
        raise Package0ComparatorError(
            "frozen comparator lacks Package 1 exact-zero surface membership"
        )
    return _canonical_sha256(basis)


def _observe_package0_successor_output(
    contract: Mapping[str, object],
    membership_receipt: Mapping[str, object],
    *,
    output_root: Path,
) -> _Package0SuccessorObservation:
    if not isinstance(membership_receipt, Mapping) or (
        membership_receipt.get("schema_version")
        != "package0_legacy_membership_receipt_v1"
    ):
        raise Package0ComparatorError("membership receipt schema is invalid")
    paths = _membership_text_sequence(
        membership_receipt,
        "canonical_artifact_paths",
    )
    samples = _membership_text_sequence(
        membership_receipt,
        "ordered_sample_identities",
    )
    header = _membership_text_sequence(
        membership_receipt,
        "ordered_final_header",
    )
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise Package0ComparatorError(
            "canonical_artifact_paths must be unique and ordered"
        )
    normalized_paths = tuple(_normalized_relative_path(path) for path in paths)
    if not isinstance(output_root, Path) or not output_root.is_dir():
        raise Package0ComparatorError("output_root must be an existing directory")
    resolved_root = output_root.resolve()
    actual_paths = tuple(
        sorted(
            path.relative_to(output_root).as_posix()
            for path in output_root.rglob("*")
            if path.is_file()
        )
    )
    if actual_paths != paths:
        raise Package0ComparatorError(
            "successor differs from frozen comparator: canonical_artifact_paths"
        )
    output_artifacts: list[dict[str, object]] = []
    bytes_by_path: dict[str, bytes] = {}
    for relative_path in normalized_paths:
        path = output_root / relative_path
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root) or not path.is_file():
            raise Package0ComparatorError(
                f"successor artifact escapes or is missing: {relative_path}"
            )
        exact_bytes = path.read_bytes()
        bytes_by_path[relative_path] = exact_bytes
        output_artifacts.append(
            {
                "path": relative_path,
                "sha256": hashlib.sha256(exact_bytes).hexdigest().upper(),
                "size_bytes": len(exact_bytes),
            }
        )
    baseline_path = "baseline/output/xic_results.csv"
    final_path = "final/output/xic_results.csv"
    try:
        baseline_bytes = bytes_by_path[baseline_path]
        final_bytes = bytes_by_path[final_path]
    except KeyError as exc:
        raise Package0ComparatorError(
            "membership receipt must include both Targeted xic_results.csv files"
        ) from exc
    baseline_header, baseline_samples = _csv_membership(
        baseline_bytes,
        field=baseline_path,
    )
    final_header, final_samples = _csv_membership(
        final_bytes,
        field=final_path,
    )
    differences: list[str] = []
    if baseline_header != header or final_header != header:
        differences.append("ordered_final_header")
    if baseline_samples != samples or final_samples != samples:
        differences.append("ordered_sample")
    if differences:
        raise Package0ComparatorError(
            "successor differs from frozen comparator: " + ", ".join(differences)
        )
    baseline_id = contract.get("baseline_id")
    if not isinstance(baseline_id, str) or not baseline_id:
        raise Package0ComparatorError("baseline_id must be non-empty")
    return _Package0SuccessorObservation(
        baseline_id=baseline_id,
        canonical_output_inventory_sha256=_canonical_value_sha256(
            output_artifacts
        ),
        canonical_artifact_path_sha256=_ordered_sha256(paths),
        ordered_sample_sha256=_ordered_sha256(baseline_samples),
        ordered_final_header_sha256=_ordered_sha256(baseline_header),
        baseline_stage_xic_results_sha256=(
            hashlib.sha256(baseline_bytes).hexdigest().upper()
        ),
        final_stage_xic_results_sha256=(
            hashlib.sha256(final_bytes).hexdigest().upper()
        ),
    )


def _adapter_basis(receipt: _TargetedAdapterParityReceipt) -> dict[str, str]:
    return {
        "processing_request_id": receipt.processing_request_id,
        "attempt_semantics_sha256": receipt.attempt_semantics_sha256,
        "lineage_sha256": receipt.lineage_sha256,
        "scientific_results_sha256": receipt.scientific_results_sha256,
        "delivery_projection_sha256": receipt.delivery_projection_sha256,
        "baseline_stage_xic_results_sha256": (
            receipt.baseline_stage_xic_results_sha256
        ),
        "final_stage_xic_results_sha256": receipt.final_stage_xic_results_sha256,
    }


def _canonical_sha256(value: Mapping[str, object]) -> str:
    return _canonical_value_sha256(value)


def _canonical_value_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest().upper()


def _ordered_sha256(values: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest().upper()


def _bytes_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, bytes):
        raise Package0ComparatorError(f"{field} must be exact bytes")
    return hashlib.sha256(value).hexdigest().upper()


def _file_sha256(path: object, *, field: str) -> str:
    if not isinstance(path, Path) or not path.is_file():
        raise Package0ComparatorError(f"{field} must be an existing file")
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _membership_text_sequence(
    membership_receipt: Mapping[str, object],
    field: str,
) -> tuple[str, ...]:
    value = membership_receipt.get(field)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise Package0ComparatorError(f"{field} must be a non-empty text list")
    return tuple(value)


def _normalized_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise Package0ComparatorError(
            "canonical artifact paths must be normalized relative POSIX paths"
        )
    return value


def _csv_membership(
    exact_bytes: bytes,
    *,
    field: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        rows = tuple(
            tuple(row)
            for row in csv.reader(
                io.StringIO(exact_bytes.decode("utf-8-sig"), newline="")
            )
        )
    except (UnicodeDecodeError, csv.Error) as exc:
        raise Package0ComparatorError(f"{field} must be a UTF-8 CSV") from exc
    if len(rows) < 2 or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise Package0ComparatorError(f"{field} must be a rectangular non-empty CSV")
    return rows[0], tuple(row[0] for row in rows[1:])


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise Package0ComparatorError(
            f"{field} must be an upper-case SHA-256 digest"
        )
    return value


__all__ = [
    "Package0ComparatorError",
    "Package1DifferenceObservation",
    "assert_targeted_adapter_parity",
    "mint_targeted_adapter_parity_receipt",
    "mint_package1_difference_observation",
    "validate_package1_difference_manifest",
    "validate_package0_successor_output",
]
