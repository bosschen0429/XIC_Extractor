"""Executable Package 0 parity and frozen successor-comparison receipt checks."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import InitVar, dataclass, replace
from dataclasses import field as dataclass_field
from multiprocessing import get_context
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NoReturn, final

from xic_extractor.acquisition_contract import (
    TraceCoordinateKind,
    mint_trace_acquisition_capability,
)
from xic_extractor.alignment.runtime_adapter import (
    AlignmentRuntimeApplicationResult,
    AlignmentRuntimeProcessResult,
    execute_alignment_process_runtime,
    mint_alignment_process_result_artifact,
)
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    canonical_coordinator_terminal_record_bytes,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeAttemptAggregate,
    RuntimeTelemetryProvider,
    mint_work_unit_settlement,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedRuntimeWorkerBatch,
    mint_targeted_process_result_ref,
)
from xic_extractor.extraction.targeted_service import (
    TargetedApplicationRequest,
    TargetedApplicationService,
)
from xic_extractor.peak_detection.observation_batch import (
    PeakObservationBatch,
    PeakObservationPlan,
    canonical_peak_observation_batch_bytes,
    produce_peak_observation_batch,
)
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    resolve_request_acquisition_source,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.xic_models import XICRequest, XICTrace


class Package0ComparatorError(ValueError):
    """Raised when an adapter or successor receipt differs from the frozen gate."""


_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_PACKAGE1_DIFFERENCE_SCHEMA = "package1_runtime_observation_difference_v1"
_FROZEN_PACKAGE1_SURFACE_SHA256 = (
    "70099C0412D9301736CA479F05BEBF1D7F928C248407ED062D1EFBBA0D045B6B"
)
_PACKAGE1_OBSERVATION_MINT_TOKEN = object()
_PACKAGE1_PROBE_MINT_TOKEN = object()
_M5_VERDICT_RECEIPT_MINT_TOKEN = object()
_M5_ROLLBACK_RECEIPT_MINT_TOKEN = object()
_PACKAGE1_EXPERIMENT_DIR = "package1-runtime-observation-experiment"
_PACKAGE1_PRODUCT_OUTPUT_DIR = "product-output"
_PACKAGE1_ROOT_IDENTITY_MARKER = ".package1-root-identity"
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
_M5_PACKET_SCHEMA = "m5_successor_verdict_packet_v1"
_M5_CANDIDATE_SCHEMA = "m5_frozen_candidate_manifest_v1"
_M5_LEGACY_SCHEMA = "m5_legacy_baseline_receipt_v1"
_M5_VERDICT_CONTRACT_SCHEMA = "m5_scoped_verdict_contract_v1"
_M5_ROLLBACK_SCHEMA = "m5_rollback_rehearsal_v1"
# The evaluated candidate predates the M5 packet. Evidence and locators therefore
# resolve against the merge that first made the complete reviewed packet durable,
# never against whichever checkout happens to replay the verdict later.
_M5_FROZEN_EVIDENCE_REVISION = "5f3ca8ace68b2205c7bf96dd62b08aef7809e79e"
_M5_PACKET_FILES = (
    "README.md",
    "architecture_authority_audit.tsv",
    "candidate_manifest.json",
    "comparison_dimensions.tsv",
    "evidence_manifest.tsv",
    "legacy_baseline_receipt.json",
    "public_expected_diff.tsv",
    "rollback_rehearsal.json",
    "selective_scientific_cases.tsv",
    "verdict.json",
    "verdict_contract.json",
)
_M5_DYNAMIC_RESULT_FILES = frozenset(
    {
        "selective_scientific_cases.tsv",
        "verdict.json",
    }
)
_M5_REQUIRED_PACKET_ROLES = (
    ("README.md", "packet_summary"),
    ("architecture_authority_audit.tsv", "architecture_authority_audit"),
    ("candidate_manifest.json", "frozen_candidate"),
    ("comparison_dimensions.tsv", "comparison_matrix"),
    ("evidence_manifest.tsv", "external_evidence_seal"),
    ("legacy_baseline_receipt.json", "legacy_pointer"),
    ("public_expected_diff.tsv", "public_surface_gate"),
    ("rollback_rehearsal.json", "rollback_receipt"),
    ("selective_scientific_cases.tsv", "selective_gate"),
    ("verdict.json", "derived_verdict"),
    ("verdict_contract.json", "verdict_rules"),
)
# Independent package-code trust root for every frozen packet payload. The two
# result-bearing files above remain variable so the comparator can derive
# NARROW/KILL outcomes from preregistered observations; their roles and schemas
# are still exact. This digest is over normalized artifact-index rows excluding
# those two result files, not over the mutable index file itself.
_M5_STATIC_INVENTORY_CONTRACT_SHA256 = (
    "EDDD6E13990E00A5F010860709F7117C7662BAECA8277D84D7E72A08B26A8F66"
)
_M5_INDEX_FIELDS = ("path", "sha256", "size_bytes", "role")
_M5_EVIDENCE_FIELDS = ("path", "sha256", "role")
_M5_AUTHORITY_AUDIT_FIELDS = (
    "schema_version",
    "audit_id",
    "audit_kind",
    "responsibility_or_boundary",
    "expected_count",
    "observed_count",
    "status",
    "evidence_maturity",
    "evidence_locator",
    "claim_limit",
)
_M5_DIMENSION_FIELDS = (
    "schema_version",
    "dimension",
    "baseline_status",
    "candidate_status",
    "outcome",
    "evidence_maturity",
    "evidence_locator",
    "claim_limit",
)
_M5_CASE_FIELDS = (
    "schema_version",
    "case_id",
    "question",
    "expected",
    "observed",
    "status",
    "evidence_maturity",
    "evidence_locator",
    "verdict_effect",
)
_M5_PUBLIC_DIFF_FIELDS = (
    "schema_version",
    "surface",
    "expected_diff",
    "observed_diff",
    "status",
    "evidence_locator",
    "authority",
)
_M5_CANDIDATE_FIELDS = frozenset(
    {
        "activation_base_sha",
        "candidate_merge_sha",
        "candidate_scope",
        "candidate_tree_sha",
        "canonical_package4_go_reachable",
        "design_base_sha",
        "excluded_milestones",
        "included_milestones",
        "known_preexisting_followups",
        "product_authority_granted",
        "schema_version",
        "source_files",
        "validation_status",
    }
)
_M5_LEGACY_FIELDS = frozenset(
    {
        "baseline_id",
        "baseline_replay_status",
        "canonical_output_inventory_sha256",
        "external_legacy_replay_performed",
        "legacy_is_scientific_truth",
        "schema_version",
        "source_commit_sha",
        "tracked_files",
        "tracked_pointer_verified",
        "validation_status",
    }
)
_M5_ROLLBACK_FIELDS = frozenset(
    {
        "active_authority_after",
        "active_authority_before",
        "candidate_evidence_retained",
        "evidence_locator",
        "mixed_authority_observed",
        "product_output_changed",
        "recorded_owner_receipt",
        "schema_version",
        "status",
        "status_transition",
        "successor_product_authority",
    }
)
_M5_VERDICT_FIELDS = frozenset(
    {
        "automatic_cutover_authorized",
        "candidate_defects",
        "candidate_merge_sha",
        "evaluated_scope",
        "excluded_scope",
        "gate_status",
        "known_preexisting_followups",
        "legacy_retirement_authorized",
        "missing_evidence",
        "product_authority_granted",
        "retained_scope",
        "schema_version",
        "validation_status",
        "verdict",
        "writer_expansion_authorized",
    }
)
_M5_CANDIDATE_MILESTONE_FIELDS = frozenset({"issue", "merge_sha", "milestone"})
_M5_CANDIDATE_EXCLUDED_MILESTONE_FIELDS = frozenset(
    {"issue", "milestone", "reason", "status"}
)
_M5_CANDIDATE_FOLLOWUP_FIELDS = frozenset({"issue", "scope"})
_M5_FILE_SEAL_FIELDS = frozenset({"path", "sha256"})
_M5_ROLLBACK_OWNER_RECEIPT_FIELDS = frozenset(
    {
        "delivery_revision_count",
        "delivery_revision_ids_sha256",
        "delivery_seal_candidate_id",
        "delivery_stage_id",
        "lineage_artifact_count",
        "lineage_artifact_ids_sha256",
        "owner_receipt_sha256",
        "prior_current_delivery_id",
        "processing_attempt_id",
    }
)
_M5_VERDICT_GATE_STATUS_FIELDS = frozenset(
    {
        "bounded_substrate_pass",
        "canonical_package4_complete",
        "public_hard_zero_pass",
        "rollback_pass",
    }
)
_M5_REQUIRED_DIMENSIONS = frozenset(
    {
        "dependency_crossings",
        "duplicate_authorities",
        "identity_replay_lineage",
        "targeted_untargeted_shared_kernel",
        "selective_scientific_risk",
        "public_surface_blast_radius",
        "raw_vendor_point_cost",
        "elapsed_memory_artifact_cost",
        "review_and_same_class_defect_surface",
        "migration_upgrade_rollback_deletion",
        "missing_prerequisites",
        "evidence_maturity",
    }
)
_M5_REQUIRED_PUBLIC_SURFACES = frozenset(
    {
        "activation",
        "cli_config",
        "counted_detection",
        "csv",
        "matrix",
        "selected_area",
        "workbook",
        "writer",
    }
)
_M5_REQUIRED_NARROW_EXCLUSION_ORDER = (
    "canonical Package 4 GO",
    "general Untargeted identity readiness",
    "M4 comparable scan-set MS2 profile evidence",
    "Matrix or writer activation",
    "legacy retirement",
)
_M5_REQUIRED_NARROW_EXCLUSIONS = frozenset(
    _M5_REQUIRED_NARROW_EXCLUSION_ORDER
)
_M5_REQUIRED_RETAINED_SCOPE = (
    "owner-backed evidence and observation spine",
    "conservative workflow-neutral Backfill eligibility",
    "shadow identity and split lineage",
    "Targeted CID-NL diagnostic adapter and rollback substrate",
)
_M5_REQUIRED_KNOWN_FOLLOWUPS = (
    "Issue #105 RT/time-window precision",
    "Issue #156 Gap 10 complete-group ownership",
)
_M5_REQUIRED_LEGACY_BASELINE_ID = "cid_nl_legacy_baseline_6d3b18ef_8raw_v2"
_M5_REQUIRED_LEGACY_SOURCE_COMMIT_SHA = (
    "6d3b18efd88971ffb6d96412a8d8ee47e8ed4742"
)
_M5_REQUIRED_LEGACY_OUTPUT_INVENTORY_SHA256 = (
    "CC25A6A731C822F9147FECAB5949EAABB79D0415FCA67436365CEC035B4293B1"
)
_M5_REQUIRED_MISSING_EVIDENCE = (
    "complete #126/#127 Package 4 scientific gate revalidation",
    "comparable legacy and candidate RAW/vendor/point instrumentation",
    "representative candidate elapsed and peak-memory sample",
    "method-specific real-sample and manual EIC/MS2 acceptance",
)
_M5_REQUIRED_GATE_STATUS = {
    "bounded_substrate_pass": True,
    "canonical_package4_complete": False,
    "public_hard_zero_pass": True,
    "rollback_pass": True,
}
_M5_REQUIRED_GO_REQUIREMENTS = (
    "complete #126/#127 candidate gates revalidated",
    "Targeted lifecycle and broadened Discovery use the same downstream core",
    "public expected diff explained and accepted",
    "selective scientific and operational gates pass",
    "whole-attempt rollback preserves one active authority",
)
_M5_REQUIRED_VERDICT_STATES = ("GO", "NARROW", "KILL", "INCONCLUSIVE")
_M5_REQUIRED_VERDICT_RULES = {
    "candidate_defect_is_not_repaired_in_m5": True,
    "m2_or_m3_only_cannot_go": True,
    "missing_evidence_never_promotes": True,
    "rollback_requires_legacy_only_active_authority": True,
    "selective_failure_uses_preregistered_effect": True,
}
_M5_REQUIRED_EXCLUDED_MILESTONES = (
    (
        "M4",
        164,
        "M3 did not produce a named full-profile cohort and controls",
        "conditional_inactive",
    ),
)
_M5_REQUIRED_CANDIDATE_FOLLOWUPS = (
    (105, "RT/time-window serialization and lookup precision"),
    (156, "Gap 10 complete same-target group ownership"),
)
_M5_REQUIRED_AUTHORITY_AUDIT_ROWS = (
    (
        _M5_PACKET_SCHEMA,
        "forbidden_dependency_crossings",
        "dependency_crossing",
        "frozen candidate dependency direction",
        "0",
        "0",
        "pass",
        "ci_static_census",
        "tests/test_docs_architecture_census.py::"
        "test_current_architecture_census_is_valid_and_static_only",
        "Current frozen tree only; no historical improvement claim",
    ),
    (
        _M5_PACKET_SCHEMA,
        "runtime_authority_bypasses",
        "dependency_crossing",
        "runtime partial-lifecycle authority bypass",
        "0",
        "0",
        "pass",
        "ci_static_census",
        "tests/test_docs_architecture_census.py::"
        "test_runtime_boundary_authority_rejects_projection_and_reducer_bypasses",
        "Named runtime boundary only",
    ),
    (
        _M5_PACKET_SCHEMA,
        "cell_observation_owner",
        "duplicate_authority",
        "cell observation minting",
        "1",
        "1",
        "pass",
        "static_owner_inventory",
        "xic_extractor/alignment/cell_observations.py::mint_cell_observation_ref",
        "One named owner in frozen CID-NL scope; no behavior-equivalence proof",
    ),
    (
        _M5_PACKET_SCHEMA,
        "candidate_set_relation_owner",
        "duplicate_authority",
        "CID-NL candidate set relation",
        "1",
        "1",
        "pass",
        "static_owner_inventory",
        "xic_extractor/peak_detection/cid_nl_candidate_set_relation.py::"
        "resolve_dda_cid_nl_candidate_set_relation_ref",
        "One named owner in frozen CID-NL scope; no behavior-equivalence proof",
    ),
    (
        _M5_PACKET_SCHEMA,
        "method_assessment_owner",
        "duplicate_authority",
        "CID-NL method assessment",
        "1",
        "1",
        "pass",
        "static_owner_inventory",
        "xic_extractor/peak_detection/cid_nl_method_assessment.py::"
        "mint_cid_nl_method_assessment_ref",
        "One named owner in frozen CID-NL scope; no behavior-equivalence proof",
    ),
    (
        _M5_PACKET_SCHEMA,
        "identity_settlement_owner",
        "duplicate_authority",
        "identity model settlement",
        "1",
        "1",
        "pass",
        "static_owner_inventory",
        "xic_extractor/alignment/identity_settlement.py::settle_identity_model",
        "One named owner in frozen CID-NL scope; no repo-wide identity authority claim",
    ),
    (
        _M5_PACKET_SCHEMA,
        "targeted_delivery_owner",
        "duplicate_authority",
        "Targeted delivery and whole-attempt rollback",
        "1",
        "1",
        "pass",
        "static_owner_inventory",
        "xic_extractor/extraction/targeted_diagnostic_delivery.py::"
        "rollback_targeted_diagnostic_delivery_candidate",
        "One named owner in frozen Targeted diagnostic scope; no product cutover",
    ),
)
_M5_SELECTIVE_CASE_CONTRACT_SHA256 = (
    "A87650AE84E941F9B882B4178B5212E42F6443272F6528F136BAE4745C10560A"
)
_M5_DIMENSION_CONTRACT_SHA256 = (
    "A807C100B9912C65880DC268E4DF50A111E08170506AC4C73BA86B4890FD88C1"
)
_M5_PUBLIC_DIFF_CONTRACT_SHA256 = (
    "50CD83049117B3231FFCE0645B8212834E910ACE0753587CF52C8000D007DE99"
)
_M5_ROLLBACK_RECEIPT_SUMMARY_SHA256 = (
    "4729692AC09F285B51D68A9332CDC0554E9A2E764E13CEF7D3E94089BD2DB3D9"
)
_M5_REQUIRED_CANDIDATE_SOURCE_PATHS = frozenset(
    {
        "xic_extractor/alignment/cell_observations.py",
        "xic_extractor/alignment/cid_nl_evidence_spine.py",
        "xic_extractor/alignment/conservative_backfill.py",
        "xic_extractor/alignment/drift_evidence.py",
        "xic_extractor/alignment/hypothesis_owner_adapter.py",
        "xic_extractor/alignment/identity_settlement.py",
        "xic_extractor/extraction/targeted_inputs.py",
        "xic_extractor/extraction/targeted_diagnostic_delivery.py",
        "xic_extractor/extraction/targeted_real_data_adapter.py",
        "xic_extractor/extraction/targeted_real_data_runtime.py",
        "xic_extractor/output/csv_writers.py",
        "xic_extractor/peak_detection/cid_nl_candidate_set_relation.py",
        "xic_extractor/peak_detection/cid_nl_method_assessment.py",
    }
)
_M5_REQUIRED_LEGACY_TRACKED_PATHS = frozenset(
    {
        "docs/superpowers/validation/cid_nl_legacy_baseline_v1/"
        "baseline_summary.json",
        "docs/superpowers/validation/cid_nl_legacy_baseline_v1/"
        "comparator_contract.json",
        "docs/superpowers/validation/package0_smallest_strangler_contract_v1/"
        "contract.json",
        "docs/superpowers/validation/package0_smallest_strangler_contract_v1/"
        "membership_receipt.json",
    }
)
_M5_REQUIRED_EVIDENCE_PATHS = frozenset(
    {
        "docs/product/alignment.md",
        "docs/superpowers/validation/m5_successor_verdict_v1/"
        "architecture_authority_audit.tsv",
        "docs/superpowers/validation/cid_nl_legacy_baseline_v1/"
        "baseline_summary.json",
        "docs/superpowers/validation/cid_nl_legacy_baseline_v1/"
        "comparator_contract.json",
        "docs/superpowers/validation/package2_targeted_method_v1/README.md",
        "docs/superpowers/validation/package2_targeted_method_v1/"
        "difference_manifest.tsv",
        "tests/test_docs_architecture_census.py",
        "tests/test_alignment_conservative_backfill.py",
        "tests/test_alignment_identity_settlement.py",
        "tests/test_alignment_split_lineage.py",
        "tests/test_cid_nl_evidence_spine.py",
        "tests/test_m5_successor_verdict.py",
        "tests/test_targeted_cli_gui_diagnostic_service.py",
        "tests/test_targeted_real_data_adapter.py",
        "tests/test_targeted_service_contract.py",
        "xic_extractor/alignment/cell_observations.py",
        "xic_extractor/alignment/identity_settlement.py",
        "xic_extractor/extraction/targeted_diagnostic_delivery.py",
        "xic_extractor/peak_detection/cid_nl_candidate_set_relation.py",
        "xic_extractor/peak_detection/cid_nl_method_assessment.py",
    }
)
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@final
@dataclass(frozen=True, slots=True)
class Package1ObservationTraceFixture:
    """Exact no-RAW trace input for one Package 1 experiment request."""

    request: XICRequest
    rt: tuple[float, ...]
    intensity: tuple[float, ...]
    first_scan: int
    last_scan: int

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("Package1ObservationTraceFixture is final")

    def __post_init__(self) -> None:
        if not isinstance(self.request, XICRequest):
            raise Package0ComparatorError("trace fixture request must be an XICRequest")
        if not isinstance(self.rt, tuple) or not isinstance(self.intensity, tuple):
            raise Package0ComparatorError(
                "trace fixture rt and intensity must be exact tuples"
            )
        try:
            XICTrace.from_arrays(self.rt, self.intensity)
        except (TypeError, ValueError) as exc:
            raise Package0ComparatorError(
                "trace fixture must contain one valid finite XIC trace"
            ) from exc
        if (
            isinstance(self.first_scan, bool)
            or not isinstance(self.first_scan, int)
            or isinstance(self.last_scan, bool)
            or not isinstance(self.last_scan, int)
            or self.first_scan < 1
            or self.last_scan < self.first_scan
        ):
            raise Package0ComparatorError(
                "trace fixture scan window must be ordered positive integers"
            )


@final
@dataclass(frozen=True, slots=True)
class Package1ObservationExperimentJob:
    """Primitive, pickleable input to the fixed Package 1 experiment workers."""

    owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    work_unit_key: str
    resource_key: str
    coordinate_kind: TraceCoordinateKind
    plans: tuple[PeakObservationPlan, ...]
    trace_fixtures: tuple[Package1ObservationTraceFixture, ...]
    delay_sec: float = 0.0

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("Package1ObservationExperimentJob is final")

    def __post_init__(self) -> None:
        if not isinstance(self.owners, ProcessingOwnerReferences):
            raise Package0ComparatorError(
                "experiment job owners must be ProcessingOwnerReferences"
            )
        if not isinstance(self.sample, ResolvedCanonicalSampleIdentity):
            raise Package0ComparatorError(
                "experiment job sample must be a resolved canonical identity"
            )
        if not isinstance(self.work_unit_key, str) or not self.work_unit_key:
            raise Package0ComparatorError(
                "experiment job work_unit_key must be non-empty"
            )
        if not isinstance(self.resource_key, str) or not self.resource_key:
            raise Package0ComparatorError(
                "experiment job resource_key must be non-empty"
            )
        if not isinstance(self.coordinate_kind, TraceCoordinateKind):
            raise Package0ComparatorError(
                "experiment job coordinate_kind must be a TraceCoordinateKind"
            )
        if not isinstance(self.plans, tuple) or not self.plans or any(
            not isinstance(plan, PeakObservationPlan) for plan in self.plans
        ):
            raise Package0ComparatorError(
                "experiment job plans must be a non-empty PeakObservationPlan tuple"
            )
        if not isinstance(self.trace_fixtures, tuple) or any(
            type(fixture) is not Package1ObservationTraceFixture
            for fixture in self.trace_fixtures
        ):
            raise Package0ComparatorError(
                "experiment job trace_fixtures must be an exact fixture tuple"
            )
        if tuple(plan.request for plan in self.plans) != tuple(
            fixture.request for fixture in self.trace_fixtures
        ):
            raise Package0ComparatorError(
                "experiment trace fixtures must match plan requests in order"
            )
        if (
            isinstance(self.delay_sec, bool)
            or not isinstance(self.delay_sec, (int, float))
            or not math.isfinite(float(self.delay_sec))
            or self.delay_sec < 0
        ):
            raise Package0ComparatorError(
                "experiment job delay_sec must be finite and non-negative"
            )


@dataclass(frozen=True, slots=True)
class _Package1NoRawBatchAdapter:
    fixtures: tuple[Package1ObservationTraceFixture, ...]

    @property
    def raw_chromatogram_call_count(self) -> int:
        return 0

    def extract_xic_many(
        self,
        requests: Sequence[XICRequest],
    ) -> tuple[XICTrace, ...]:
        if tuple(requests) != tuple(fixture.request for fixture in self.fixtures):
            raise Package0ComparatorError(
                "experiment adapter requests must match the fixed trace fixtures"
            )
        return tuple(
            XICTrace.from_arrays(fixture.rt, fixture.intensity)
            for fixture in self.fixtures
        )

    def scan_window_for_request(self, request: XICRequest) -> tuple[int, int]:
        for fixture in self.fixtures:
            if fixture.request == request:
                return fixture.first_scan, fixture.last_scan
        raise Package0ComparatorError(
            "experiment adapter request is absent from the fixed trace fixtures"
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


@final
@dataclass(frozen=True, slots=True)
class Package1DifferenceObservation:
    """Owner-derived final-state and runner receipt for one Package 1 run."""

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
    validation_status: str
    _mint_token: InitVar[object] = dataclass_field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("Package1DifferenceObservation is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _PACKAGE1_OBSERVATION_MINT_TOKEN:
            raise Package0ComparatorError(
                "Package1DifferenceObservation must be minted by the comparator"
            )
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


@final
@dataclass(frozen=True, slots=True)
class M5SuccessorVerdictReceipt:
    """Sealed diagnostic receipt for one exact frozen successor verdict packet."""

    candidate_merge_sha: str
    verdict: str
    evaluated_scope: str
    validation_status: str
    rollback_rehearsal_status: str
    product_authority_granted: bool
    external_legacy_replay_performed: bool
    packet_sha256: str
    _mint_token: InitVar[object] = dataclass_field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("M5SuccessorVerdictReceipt is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _M5_VERDICT_RECEIPT_MINT_TOKEN:
            raise Package0ComparatorError(
                "M5SuccessorVerdictReceipt must be minted by the comparator"
            )


@final
@dataclass(frozen=True, slots=True)
class M5RollbackRehearsalReceipt:
    """Owner-derived proof that rollback retains legacy current authority."""

    status_transition: str
    processing_attempt_id: str
    prior_current_delivery_id: str
    active_authority: str
    successor_product_authority: bool
    lineage_artifact_ids: tuple[str, ...]
    delivery_revision_ids: tuple[str, ...]
    delivery_stage_id: str
    delivery_seal_candidate_id: str
    receipt_sha256: str
    _mint_token: InitVar[object] = dataclass_field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("M5RollbackRehearsalReceipt is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _M5_ROLLBACK_RECEIPT_MINT_TOKEN:
            raise Package0ComparatorError(
                "M5RollbackRehearsalReceipt must be minted by the comparator"
            )


@final
class _Package1ProductOutputProbe:
    """Comparator-owned final-state parity window for product outputs."""

    __slots__ = (
        "_before_inventory",
        "_consumed",
        "_legacy_runner_call_count",
        "_output_root",
        "_root_identity",
    )

    def __init__(
        self,
        *,
        output_root: Path,
        before_inventory: tuple[tuple[str, str], ...],
        root_identity: tuple[int, int, int, int, int, str],
        _mint_token: object,
    ) -> None:
        if _mint_token is not _PACKAGE1_PROBE_MINT_TOKEN:
            raise Package0ComparatorError(
                "Package 1 product-output probe must be minted by the comparator"
            )
        self._output_root = output_root
        self._before_inventory = before_inventory
        self._root_identity = root_identity
        self._legacy_runner_call_count = 0
        self._consumed = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("Package 1 product-output probe is final")

    def _forbidden_legacy_runner(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> NoReturn:
        """Fail immediately if the Package 1 path enters the legacy runner."""

        self._legacy_runner_call_count += 1
        raise Package0ComparatorError(
            "Package 1 observation path invoked the forbidden legacy runner"
        )

    def _finish(self) -> tuple[int, tuple[str, ...]]:
        if self._consumed:
            raise Package0ComparatorError(
                "Package 1 product-output probe may finalize exactly one observation"
            )
        self._consumed = True
        if _package1_output_root_identity(self._output_root) != self._root_identity:
            raise Package0ComparatorError(
                "Package 1 comparator-owned output root identity changed"
            )
        after_inventory = _package1_product_output_inventory(self._output_root)
        before_by_path = dict(self._before_inventory)
        after_by_path = dict(after_inventory)
        changed_paths = tuple(
            sorted(
                path
                for path in before_by_path.keys() | after_by_path.keys()
                if before_by_path.get(path) != after_by_path.get(path)
            )
        )
        try:
            (self._output_root / _PACKAGE1_ROOT_IDENTITY_MARKER).unlink()
        except OSError as exc:
            raise Package0ComparatorError(
                "Package 1 comparator-owned output root identity marker changed"
            ) from exc
        return self._legacy_runner_call_count, changed_paths


def run_package1_observation_experiment(
    *,
    experiment_workspace: Path,
    targeted_request: TargetedApplicationRequest,
    runtime: AttemptRuntime,
    resource_reservations: Sequence[RuntimeResourceReservation],
    telemetry_provider: RuntimeTelemetryProvider,
    jobs: tuple[Package1ObservationExperimentJob, ...],
    max_workers: int,
) -> Package1DifferenceObservation:
    """Run fixed Package 1 consumers inside one comparator-owned parity window."""

    if not isinstance(experiment_workspace, Path) or not experiment_workspace.is_dir():
        raise Package0ComparatorError(
            "experiment_workspace must be an existing directory"
        )
    if not isinstance(targeted_request, TargetedApplicationRequest):
        raise Package0ComparatorError(
            "targeted_request must be a TargetedApplicationRequest"
        )
    _validate_package1_experiment_jobs(
        jobs,
        runtime=runtime,
        resource_reservations=resource_reservations,
        max_workers=max_workers,
    )
    experiment_root = experiment_workspace / _PACKAGE1_EXPERIMENT_DIR
    try:
        experiment_root.mkdir()
    except FileExistsError as exc:
        raise Package0ComparatorError(
            "Package 1 experiment root must not already exist"
        ) from exc
    product_output_root = experiment_root / _PACKAGE1_PRODUCT_OUTPUT_DIR
    product_output_root.mkdir()
    (product_output_root / _PACKAGE1_ROOT_IDENTITY_MARKER).write_bytes(
        secrets.token_bytes(32)
    )
    probe = _Package1ProductOutputProbe(
        output_root=product_output_root,
        before_inventory=_package1_product_output_inventory(product_output_root),
        root_identity=_package1_output_root_identity(product_output_root),
        _mint_token=_PACKAGE1_PROBE_MINT_TOKEN,
    )
    bound_targeted_request = _bind_package1_targeted_output_root(
        targeted_request,
        product_output_root=product_output_root,
    )

    def run_fixed_targeted_jobs(
        request: TargetedApplicationRequest,
        aggregate: RuntimeAttemptAggregate,
    ) -> TargetedRuntimeWorkerBatch:
        del request, aggregate
        return _execute_package1_targeted_jobs(jobs, max_workers=max_workers)

    targeted_service = TargetedApplicationService(
        probe._forbidden_legacy_runner,
        runtime_lifecycle_runner=run_fixed_targeted_jobs,
        runtime_recovery_marker=OwnerPathRuntimeMarker(
            experiment_root / "targeted.runtime.json",
            "package1-targeted-runtime-observation:v1",
        ),
    )
    targeted = targeted_service.execute_runtime_lifecycle(
        bound_targeted_request,
        runtime,
        resource_reservations=resource_reservations,
        telemetry_provider=telemetry_provider,
    )
    alignment = execute_alignment_process_runtime(
        runtime,
        jobs,
        worker=_run_package1_alignment_job,
        error_factory=_package1_alignment_error,
        max_workers=max_workers,
        resource_reservations=resource_reservations,
        telemetry_provider=telemetry_provider,
        recovery_marker=OwnerPathRuntimeMarker(
            experiment_root / "alignment.runtime.json",
            "package1-alignment-runtime-observation:v1",
        ),
    )
    return _mint_package1_difference_observation(
        probe=probe,
        targeted=targeted,
        alignment=alignment,
    )


def _validate_package1_experiment_jobs(
    jobs: object,
    *,
    runtime: AttemptRuntime,
    resource_reservations: Sequence[RuntimeResourceReservation],
    max_workers: int,
) -> None:
    if not isinstance(runtime, AttemptRuntime):
        raise Package0ComparatorError("runtime must be an AttemptRuntime")
    if not isinstance(jobs, tuple) or not jobs or any(
        type(job) is not Package1ObservationExperimentJob for job in jobs
    ):
        raise Package0ComparatorError(
            "jobs must be a non-empty Package1ObservationExperimentJob tuple"
        )
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        raise Package0ComparatorError("max_workers must be a positive integer")
    if tuple(job.work_unit_key for job in jobs) != runtime.manifest.work_unit_keys:
        raise Package0ComparatorError(
            "experiment jobs must match runtime manifest work in order"
        )
    if any(job.owners.attempt != runtime.manifest.attempt for job in jobs):
        raise Package0ComparatorError(
            "experiment jobs must bind the runtime processing attempt"
        )
    reservations = tuple(resource_reservations)
    if any(
        not isinstance(reservation, RuntimeResourceReservation)
        for reservation in reservations
    ):
        raise Package0ComparatorError(
            "resource_reservations must contain RuntimeResourceReservation values"
        )
    if tuple(reservation.resource_key for reservation in reservations) != tuple(
        job.resource_key for job in jobs
    ):
        raise Package0ComparatorError(
            "experiment resource reservations must match jobs in order"
        )


def _bind_package1_targeted_output_root(
    request: TargetedApplicationRequest,
    *,
    product_output_root: Path,
) -> TargetedApplicationRequest:
    output_csv = product_output_root / "xic_results.csv"
    diagnostics_csv = product_output_root / "xic_diagnostics.csv"
    bound = replace(
        request,
        config=replace(
            request.config,
            output_csv=output_csv,
            diagnostics_csv=diagnostics_csv,
        ),
    )
    if (
        bound.config.output_csv != output_csv
        or bound.config.diagnostics_csv != diagnostics_csv
    ):
        raise Package0ComparatorError(
            "Targeted product outputs must use the comparator-owned output root"
        )
    return bound


def _execute_package1_targeted_jobs(
    jobs: tuple[Package1ObservationExperimentJob, ...],
    *,
    max_workers: int,
) -> TargetedRuntimeWorkerBatch:
    worker_count = min(max_workers, len(jobs))
    with ProcessPoolExecutor(
        max_workers=worker_count,
        mp_context=get_context("spawn"),
    ) as executor:
        futures = tuple(
            executor.submit(_run_package1_targeted_job, job) for job in jobs
        )
        results = tuple(future.result() for future in futures)
    envelopes = tuple(envelope for envelope, _report in results)
    return TargetedRuntimeWorkerBatch(
        result_envelopes=envelopes,
        resource_reports=tuple(report for _envelope, report in results),
        runtime_artifact_refs=tuple(
            envelope.owner_result_ref
            for envelope in envelopes
            if envelope.owner_result_ref is not None
        ),
    )


def _run_package1_targeted_job(
    job: Package1ObservationExperimentJob,
) -> tuple[TargetedProcessWorkerResultEnvelope, TargetedProcessResourceReport]:
    batch = _produce_package1_observation_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    reference = mint_targeted_process_result_ref(
        processing_attempt=job.owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=batch.observation_artifacts,
    )
    return (
        TargetedProcessWorkerResultEnvelope(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=reference,
            peak_observation_batch=batch,
        ),
        TargetedProcessResourceReport(
            attempt=job.owners.attempt,
            resource_key=job.resource_key,
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        ),
    )


def _run_package1_alignment_job(
    job: Package1ObservationExperimentJob,
) -> AlignmentRuntimeProcessResult:
    batch = _produce_package1_observation_batch(job)
    payload = canonical_peak_observation_batch_bytes(batch)
    artifact = mint_alignment_process_result_artifact(
        processing_attempt=job.owners.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=batch.observation_artifacts,
    )
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        runtime_artifacts=(artifact, *batch.observation_artifacts),
        peak_observation_batch=batch,
    )


def _package1_alignment_error(
    job: Package1ObservationExperimentJob,
    error: Exception,
) -> AlignmentRuntimeProcessResult:
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.owners.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.FAILED,
            failure_detail=WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.EXECUTION_ERROR,
                stage="package1-alignment-process-worker",
                source_reference=f"package1-no-raw:{job.work_unit_key}",
                message=str(error),
            ),
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )


def _produce_package1_observation_batch(
    job: Package1ObservationExperimentJob,
) -> PeakObservationBatch:
    if job.delay_sec:
        time.sleep(job.delay_sec)
    source = resolve_request_acquisition_source(
        processing_owners=job.owners,
        sample=job.sample,
    )
    capability = mint_trace_acquisition_capability(
        source,
        adapter_namespace="synthetic.package1.no_raw",
        coordinate_kind=job.coordinate_kind,
    )
    return produce_peak_observation_batch(
        processing_owners=job.owners,
        sample=job.sample,
        capability=capability,
        plans=job.plans,
        adapter=_Package1NoRawBatchAdapter(job.trace_fixtures),
    )


def _mint_package1_difference_observation(
    *,
    probe: _Package1ProductOutputProbe,
    targeted: TargetedRuntimeLifecycleApplicationResult,
    alignment: AlignmentRuntimeApplicationResult,
) -> Package1DifferenceObservation:
    """Derive one receipt from the comparator-owned experiment results."""

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
    legacy_runner_call_count, product_output_paths = probe._finish()
    if legacy_runner_call_count != 0:
        raise Package0ComparatorError(
            "Package 1 observation path invoked the forbidden legacy runner"
        )
    if product_output_paths:
        raise Package0ComparatorError(
            "Package 1 observation path changed product output files: "
            + ", ".join(product_output_paths)
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
        targeted_legacy_runner_call_count=legacy_runner_call_count,
        successor_product_output_paths=product_output_paths,
        validation_status="diagnostic_only",
        _mint_token=_PACKAGE1_OBSERVATION_MINT_TOKEN,
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
                "validation_status": observation.validation_status,
            },
            "rows": normalized,
        }
    )


def validate_m5_successor_verdict_packet(
    packet_root: Path,
    *,
    repo_root: Path,
    expected_candidate_sha: str,
) -> M5SuccessorVerdictReceipt:
    """Validate one sealed feature-free M5 comparison and verdict packet."""

    if not isinstance(packet_root, Path) or not packet_root.is_dir():
        raise Package0ComparatorError("M5 packet_root must be an existing directory")
    if packet_root.is_symlink():
        raise Package0ComparatorError("M5 packet_root cannot be a symlink")
    if not isinstance(repo_root, Path) or not repo_root.is_dir():
        raise Package0ComparatorError("M5 repo_root must be an existing directory")
    _m5_commit_sha(expected_candidate_sha, field="expected_candidate_sha")

    inventory = _validate_m5_packet_inventory(packet_root)
    candidate = _m5_json_mapping(packet_root / "candidate_manifest.json")
    _validate_m5_candidate_manifest(
        candidate,
        repo_root=repo_root,
        expected_candidate_sha=expected_candidate_sha,
    )
    legacy = _m5_json_mapping(packet_root / "legacy_baseline_receipt.json")
    _validate_m5_legacy_receipt(
        legacy,
        repo_root=repo_root,
        evidence_revision=_M5_FROZEN_EVIDENCE_REVISION,
    )
    evidence_paths = _validate_m5_evidence_manifest(
        _m5_tsv_rows(
            packet_root / "evidence_manifest.tsv",
            fields=_M5_EVIDENCE_FIELDS,
        ),
        repo_root=repo_root,
        evidence_revision=_M5_FROZEN_EVIDENCE_REVISION,
    )
    contract = _m5_json_mapping(packet_root / "verdict_contract.json")
    _validate_m5_verdict_contract(contract)
    authority_audit = _m5_tsv_rows(
        packet_root / "architecture_authority_audit.tsv",
        fields=_M5_AUTHORITY_AUDIT_FIELDS,
    )
    _validate_m5_authority_audit(
        authority_audit,
        repo_root=repo_root,
        evidence_revision=_M5_FROZEN_EVIDENCE_REVISION,
    )
    dimensions = _m5_tsv_rows(
        packet_root / "comparison_dimensions.tsv",
        fields=_M5_DIMENSION_FIELDS,
    )
    _validate_m5_dimensions(
        dimensions,
        repo_root=repo_root,
        evidence_revision=_M5_FROZEN_EVIDENCE_REVISION,
    )
    cases = _m5_tsv_rows(
        packet_root / "selective_scientific_cases.tsv",
        fields=_M5_CASE_FIELDS,
    )
    _validate_m5_selective_cases(
        cases,
        repo_root=repo_root,
        evidence_revision=_M5_FROZEN_EVIDENCE_REVISION,
    )
    public_diffs = _m5_tsv_rows(
        packet_root / "public_expected_diff.tsv",
        fields=_M5_PUBLIC_DIFF_FIELDS,
    )
    _validate_m5_public_diffs(
        public_diffs,
        repo_root=repo_root,
        evidence_revision=_M5_FROZEN_EVIDENCE_REVISION,
    )
    rollback = _m5_json_mapping(packet_root / "rollback_rehearsal.json")
    _validate_m5_rollback_packet(
        rollback,
        repo_root=repo_root,
        evidence_revision=_M5_FROZEN_EVIDENCE_REVISION,
    )
    _validate_m5_evidence_locator_coverage(
        evidence_paths,
        authority_audit=authority_audit,
        dimensions=dimensions,
        cases=cases,
        public_diffs=public_diffs,
        rollback=rollback,
    )
    verdict = _m5_json_mapping(packet_root / "verdict.json")
    derived_verdict = _validate_m5_verdict(
        verdict,
        contract=contract,
        candidate=candidate,
        dimensions=dimensions,
        cases=cases,
        rollback=rollback,
    )
    _validate_m5_static_inventory_contract(inventory)

    return M5SuccessorVerdictReceipt(
        candidate_merge_sha=str(candidate["candidate_merge_sha"]),
        verdict=derived_verdict,
        evaluated_scope=str(verdict["evaluated_scope"]),
        validation_status=str(verdict["validation_status"]),
        rollback_rehearsal_status=str(rollback["status"]),
        product_authority_granted=bool(verdict["product_authority_granted"]),
        external_legacy_replay_performed=bool(
            legacy["external_legacy_replay_performed"]
        ),
        packet_sha256=_canonical_value_sha256(
            {
                "schema_version": _M5_PACKET_SCHEMA,
                "candidate_merge_sha": candidate["candidate_merge_sha"],
                "inventory": inventory,
                "verdict": derived_verdict,
            }
        ),
        _mint_token=_M5_VERDICT_RECEIPT_MINT_TOKEN,
    )


def validate_m5_rollback_rehearsal(
    staged: object,
) -> M5RollbackRehearsalReceipt:
    """Invoke the whole-attempt rollback owner and derive its receipt."""

    from xic_extractor.extraction.targeted_diagnostic_delivery import (
        TargetedDiagnosticDeliveryCandidate,
        rollback_targeted_diagnostic_delivery_candidate,
    )

    if not isinstance(staged, TargetedDiagnosticDeliveryCandidate) or (
        staged.status != "staged_candidate"
    ):
        raise Package0ComparatorError(
            "M5 rollback rehearsal requires one staged delivery candidate"
        )
    rolled_back = rollback_targeted_diagnostic_delivery_candidate(staged)
    if not isinstance(rolled_back, TargetedDiagnosticDeliveryCandidate) or (
        rolled_back.status != "rolled_back"
    ):
        raise Package0ComparatorError(
            "M5 rollback rehearsal requires one rolled-back delivery candidate"
        )
    expected = replace(
        staged,
        status="rolled_back",
        quarantine_reason="whole attempt rolled back to authoritative legacy current",
    )
    if rolled_back != expected or rolled_back.active_output is not staged.active_output:
        raise Package0ComparatorError(
            "M5 rollback rehearsal changed candidate evidence or active authority"
        )
    if staged.stage_candidate is None or staged.seal_candidate is None:
        raise Package0ComparatorError(
            "M5 rollback rehearsal lacks the staged candidate lineage"
        )
    lineage_artifact_ids = tuple(
        artifact.evidence_artifact_id for artifact in staged.lineage_artifacts
    )
    revision_ids = tuple(revision.delivery_revision_id for revision in staged.revisions)
    basis = {
        "schema_version": _M5_ROLLBACK_SCHEMA,
        "status_transition": "staged_candidate->rolled_back",
        "processing_attempt_id": staged.attempt.processing_attempt_id,
        "prior_current_delivery_id": staged.prior_current_delivery_id,
        "active_authority": "authoritative_legacy_current",
        "successor_product_authority": False,
        "lineage_artifact_ids": lineage_artifact_ids,
        "delivery_revision_ids": revision_ids,
        "delivery_stage_id": staged.stage_candidate.delivery_stage_id,
        "delivery_seal_candidate_id": (
            staged.seal_candidate.delivery_seal_candidate_id
        ),
    }
    return M5RollbackRehearsalReceipt(
        status_transition="staged_candidate->rolled_back",
        processing_attempt_id=staged.attempt.processing_attempt_id,
        prior_current_delivery_id=staged.prior_current_delivery_id,
        active_authority="authoritative_legacy_current",
        successor_product_authority=False,
        lineage_artifact_ids=lineage_artifact_ids,
        delivery_revision_ids=revision_ids,
        delivery_stage_id=staged.stage_candidate.delivery_stage_id,
        delivery_seal_candidate_id=(staged.seal_candidate.delivery_seal_candidate_id),
        receipt_sha256=_canonical_value_sha256(basis),
        _mint_token=_M5_ROLLBACK_RECEIPT_MINT_TOKEN,
    )


def _validate_m5_packet_inventory(packet_root: Path) -> tuple[dict[str, object], ...]:
    rows = _m5_tsv_rows(
        packet_root / "artifact_index.tsv",
        fields=_M5_INDEX_FIELDS,
    )
    declared_paths = tuple(
        str(_m5_safe_relative_path(row["path"], field="artifact index path"))
        for row in rows
    )
    if declared_paths != _M5_PACKET_FILES:
        raise Package0ComparatorError(
            "M5 artifact inventory must declare the exact ordered packet"
        )
    actual_entries = tuple(sorted(packet_root.iterdir(), key=lambda path: path.name))
    expected_names = tuple(sorted((*_M5_PACKET_FILES, "artifact_index.tsv")))
    if tuple(path.name for path in actual_entries) != expected_names or any(
        not path.is_file() or path.is_symlink() for path in actual_entries
    ):
        raise Package0ComparatorError(
            "M5 artifact inventory differs from the sealed packet"
        )
    normalized: list[dict[str, object]] = []
    for row in rows:
        relative = PurePosixPath(row["path"])
        payload_path = packet_root / relative
        payload = _m5_canonical_text_bytes(
            payload_path.read_bytes(),
            field=f"M5 artifact {relative}",
        )
        digest = _bytes_sha256(
            payload,
            field=f"M5 artifact {relative}",
        )
        expected_digest = _sha256(row["sha256"], f"M5 artifact {relative} sha256")
        try:
            size_bytes = int(row["size_bytes"])
        except ValueError as exc:
            raise Package0ComparatorError(
                f"M5 artifact {relative} size_bytes must be an integer"
            ) from exc
        if digest != expected_digest or size_bytes != len(payload):
            raise Package0ComparatorError(
                f"M5 artifact digest or size differs: {relative}"
            )
        if not row["role"]:
            raise Package0ComparatorError(f"M5 artifact role is missing: {relative}")
        normalized.append(
            {
                "path": str(relative),
                "sha256": digest,
                "size_bytes": size_bytes,
                "role": row["role"],
            }
        )
    normalized_inventory = tuple(normalized)
    if tuple(
        (str(row["path"]), str(row["role"])) for row in normalized_inventory
    ) != _M5_REQUIRED_PACKET_ROLES:
        raise Package0ComparatorError(
            "M5 artifact roles differ from the packet contract"
        )
    return normalized_inventory


def _validate_m5_static_inventory_contract(
    inventory: tuple[dict[str, object], ...],
) -> None:
    static_inventory = tuple(
        row for row in inventory if row["path"] not in _M5_DYNAMIC_RESULT_FILES
    )
    if (
        _canonical_value_sha256(static_inventory)
        != _M5_STATIC_INVENTORY_CONTRACT_SHA256
    ):
        raise Package0ComparatorError(
            "M5 frozen packet payload differs from its package-code trust root"
        )


def _validate_m5_candidate_manifest(
    candidate: Mapping[str, object],
    *,
    repo_root: Path,
    expected_candidate_sha: str,
) -> None:
    if set(candidate) != _M5_CANDIDATE_FIELDS:
        raise Package0ComparatorError("M5 candidate manifest field set differs")
    if candidate.get("schema_version") != _M5_CANDIDATE_SCHEMA:
        raise Package0ComparatorError("M5 candidate manifest schema differs")
    candidate_sha = _m5_commit_sha(
        candidate.get("candidate_merge_sha"),
        field="candidate_merge_sha",
    )
    if candidate_sha != expected_candidate_sha:
        raise Package0ComparatorError("M5 packet references a stale frozen candidate")
    declared_tree_sha = _m5_commit_sha(
        candidate.get("candidate_tree_sha"),
        field="candidate_tree_sha",
    )
    actual_tree_sha = _m5_git_bytes(
        repo_root,
        "rev-parse",
        f"{candidate_sha}^{{tree}}",
    ).decode("ascii").strip()
    if actual_tree_sha != declared_tree_sha:
        raise Package0ComparatorError("M5 candidate Git tree differs")
    if any(
        _m5_commit_sha(candidate.get(field), field=field) != candidate_sha
        for field in ("design_base_sha", "activation_base_sha")
    ):
        raise Package0ComparatorError(
            "M5 design and activation bases must equal the frozen candidate"
        )
    if (
        candidate.get("candidate_scope") != "cid_nl_successor_shadow_substrate"
        or candidate.get("validation_status") != "diagnostic_only"
        or candidate.get("product_authority_granted") is not False
        or candidate.get("canonical_package4_go_reachable") is not False
    ):
        raise Package0ComparatorError(
            "M5 candidate manifest overclaims scope or product authority"
        )
    milestones = candidate.get("included_milestones")
    if not isinstance(milestones, list):
        raise Package0ComparatorError("M5 included milestones are missing")
    if any(
        not isinstance(item, Mapping)
        or set(item) != _M5_CANDIDATE_MILESTONE_FIELDS
        for item in milestones
    ):
        raise Package0ComparatorError(
            "M5 candidate milestone record field set differs"
        )
    observed_milestones = {
        (item.get("milestone"), item.get("issue"), item.get("merge_sha"))
        for item in milestones
        if isinstance(item, Mapping)
    }
    expected_milestones = {
        ("M1", 161, "e30a04f8ed30a02c7424b49b24a42c844937a201"),
        ("M2", 162, "19fe1beff9925751ae572e252d796435fa248199"),
        ("M3", 163, "7d9226b26e42fa1c798726ef68e3d3773cf289b9"),
        ("Gap4/6", 156, candidate_sha),
    }
    if (
        observed_milestones != expected_milestones
        or len(milestones) != len(expected_milestones)
    ):
        raise Package0ComparatorError("M5 candidate milestone set differs")
    excluded = candidate.get("excluded_milestones")
    if not isinstance(excluded, list) or any(
        not isinstance(item, Mapping)
        or set(item) != _M5_CANDIDATE_EXCLUDED_MILESTONE_FIELDS
        for item in excluded
    ):
        raise Package0ComparatorError(
            "M5 candidate excluded milestone record field set differs"
        )
    if tuple(
        (
            item.get("milestone"),
            item.get("issue"),
            item.get("reason"),
            item.get("status"),
        )
        for item in excluded
        if isinstance(item, Mapping)
    ) != _M5_REQUIRED_EXCLUDED_MILESTONES or len(excluded) != len(
        _M5_REQUIRED_EXCLUDED_MILESTONES
    ):
        raise Package0ComparatorError("M5 candidate excluded milestone set differs")
    followups = candidate.get("known_preexisting_followups")
    if not isinstance(followups, list) or any(
        not isinstance(item, Mapping)
        or set(item) != _M5_CANDIDATE_FOLLOWUP_FIELDS
        for item in followups
    ):
        raise Package0ComparatorError(
            "M5 candidate follow-up record field set differs"
        )
    if tuple(
        (item.get("issue"), item.get("scope"))
        for item in followups
        if isinstance(item, Mapping)
    ) != _M5_REQUIRED_CANDIDATE_FOLLOWUPS or len(followups) != len(
        _M5_REQUIRED_CANDIDATE_FOLLOWUPS
    ):
        raise Package0ComparatorError("M5 candidate known follow-up set differs")
    sources = candidate.get("source_files")
    if not isinstance(sources, list) or not sources:
        raise Package0ComparatorError("M5 candidate source file seal is missing")
    seen: set[str] = set()
    for item in sources:
        if not isinstance(item, Mapping):
            raise Package0ComparatorError("M5 candidate source row is malformed")
        if set(item) != _M5_FILE_SEAL_FIELDS:
            raise Package0ComparatorError(
                "M5 candidate source record field set differs"
            )
        relative = _m5_safe_relative_path(
            item.get("path"),
            field="candidate source path",
        )
        if str(relative) in seen:
            raise Package0ComparatorError("M5 candidate source paths must be unique")
        seen.add(str(relative))
        expected_digest = _sha256(
            item.get("sha256"),
            f"candidate source {relative} sha256",
        )
        candidate_digest = _bytes_sha256(
            _m5_canonical_text_bytes(
                _m5_git_bytes(repo_root, "show", f"{candidate_sha}:{relative}"),
                field=f"candidate Git source {relative}",
            ),
            field=f"candidate Git source {relative}",
        )
        if candidate_digest != expected_digest:
            raise Package0ComparatorError(f"M5 candidate source drift: {relative}")
    if seen != _M5_REQUIRED_CANDIDATE_SOURCE_PATHS:
        raise Package0ComparatorError("M5 candidate source set differs")


def _validate_m5_legacy_receipt(
    legacy: Mapping[str, object],
    *,
    repo_root: Path,
    evidence_revision: str,
) -> None:
    if set(legacy) != _M5_LEGACY_FIELDS:
        raise Package0ComparatorError("M5 legacy receipt field set differs")
    if legacy.get("schema_version") != _M5_LEGACY_SCHEMA:
        raise Package0ComparatorError("M5 legacy receipt schema differs")
    if (
        legacy.get("validation_status") != "diagnostic_only"
        or legacy.get("legacy_is_scientific_truth") is not False
        or legacy.get("tracked_pointer_verified") is not True
        or legacy.get("external_legacy_replay_performed") is not False
        or legacy.get("baseline_replay_status") != "sealed_receipt_reused_no_raw_rerun"
    ):
        raise Package0ComparatorError("M5 legacy receipt overclaims replay authority")
    source_commit_sha = _m5_commit_sha(
        legacy.get("source_commit_sha"),
        field="M5 legacy source_commit_sha",
    )
    if (
        legacy.get("baseline_id") != _M5_REQUIRED_LEGACY_BASELINE_ID
        or source_commit_sha != _M5_REQUIRED_LEGACY_SOURCE_COMMIT_SHA
        or legacy.get("canonical_output_inventory_sha256")
        != _M5_REQUIRED_LEGACY_OUTPUT_INVENTORY_SHA256
    ):
        raise Package0ComparatorError("M5 legacy baseline identity differs")
    if (
        _m5_git_bytes(repo_root, "cat-file", "-t", source_commit_sha)
        .decode("ascii")
        .strip()
        != "commit"
    ):
        raise Package0ComparatorError("M5 legacy source Git object is not a commit")
    tracked_files = legacy.get("tracked_files")
    if not isinstance(tracked_files, list) or not tracked_files:
        raise Package0ComparatorError("M5 legacy tracked file seal is missing")
    tracked: dict[str, Mapping[str, object]] = {}
    for item in tracked_files:
        if not isinstance(item, Mapping):
            raise Package0ComparatorError("M5 legacy tracked file row is malformed")
        if set(item) != _M5_FILE_SEAL_FIELDS:
            raise Package0ComparatorError(
                "M5 legacy tracked file record field set differs"
            )
        relative = _m5_safe_relative_path(item.get("path"), field="legacy file path")
        if str(relative) in tracked:
            raise Package0ComparatorError("M5 legacy tracked files must be unique")
        tracked[str(relative)] = item
        if _bytes_sha256(
            _m5_canonical_text_bytes(
                _m5_git_bytes(
                    repo_root,
                    "show",
                    f"{evidence_revision}:{relative}",
                ),
                field=f"legacy file {relative}",
            ),
            field=f"legacy file {relative}",
        ) != _sha256(
            item.get("sha256"),
            f"legacy file {relative} sha256",
        ):
            raise Package0ComparatorError(f"M5 legacy tracked file drift: {relative}")
    if frozenset(tracked) != _M5_REQUIRED_LEGACY_TRACKED_PATHS:
        raise Package0ComparatorError("M5 legacy tracked file set differs")
    baseline_path = (
        "docs/superpowers/validation/cid_nl_legacy_baseline_v1/baseline_summary.json"
    )
    if baseline_path not in tracked:
        raise Package0ComparatorError("M5 legacy receipt lacks the baseline pointer")
    baseline = _m5_json_bytes_mapping(
        _m5_git_bytes(
            repo_root,
            "show",
            f"{evidence_revision}:{baseline_path}",
        ),
        source=f"{evidence_revision}:{baseline_path}",
    )
    canonical = baseline.get("canonical_artifact_set")
    if not isinstance(canonical, Mapping) or (
        legacy.get("baseline_id") != baseline.get("baseline_id")
        or legacy.get("source_commit_sha") != baseline.get("source_commit_sha")
        or legacy.get("canonical_output_inventory_sha256")
        != canonical.get("output_inventory_sha256")
    ):
        raise Package0ComparatorError("M5 legacy receipt differs from sealed baseline")


def _validate_m5_evidence_manifest(
    rows: tuple[dict[str, str], ...],
    *,
    repo_root: Path,
    evidence_revision: str,
) -> frozenset[str]:
    observed: set[str] = set()
    for row in rows:
        relative = _m5_safe_relative_path(row["path"], field="evidence file path")
        normalized = str(relative)
        if normalized in observed:
            raise Package0ComparatorError("M5 evidence file paths must be unique")
        observed.add(normalized)
        expected_digest = _sha256(
            row["sha256"],
            f"M5 evidence file {relative} sha256",
        )
        if _bytes_sha256(
            _m5_canonical_text_bytes(
                _m5_git_bytes(
                    repo_root,
                    "show",
                    f"{evidence_revision}:{relative}",
                ),
                field=f"M5 evidence file {relative}",
            ),
            field=f"M5 evidence file {relative}",
        ) != expected_digest:
            raise Package0ComparatorError(f"M5 evidence file drift: {relative}")
        if not row["role"]:
            raise Package0ComparatorError(
                f"M5 evidence file role is missing: {relative}"
            )
    if observed != _M5_REQUIRED_EVIDENCE_PATHS:
        raise Package0ComparatorError("M5 evidence file set differs")
    return frozenset(observed)


def _validate_m5_evidence_locator_coverage(
    evidence_paths: frozenset[str],
    *,
    authority_audit: tuple[dict[str, str], ...],
    dimensions: tuple[dict[str, str], ...],
    cases: tuple[dict[str, str], ...],
    public_diffs: tuple[dict[str, str], ...],
    rollback: Mapping[str, object],
) -> None:
    locators = [
        *(row["evidence_locator"] for row in authority_audit),
        *(row["evidence_locator"] for row in dimensions),
        *(row["evidence_locator"] for row in cases),
        *(row["evidence_locator"] for row in public_diffs),
        rollback.get("evidence_locator"),
    ]
    used_paths: set[str] = set()
    for locator in locators:
        if not isinstance(locator, str):
            raise Package0ComparatorError("M5 evidence locator is missing")
        path_text, _separator, _symbol = locator.partition("::")
        used_paths.add(
            str(_m5_safe_relative_path(path_text, field="evidence locator"))
        )
    if used_paths != evidence_paths:
        raise Package0ComparatorError("M5 evidence locator set differs from its seal")


def _validate_m5_authority_audit(
    rows: tuple[dict[str, str], ...],
    *,
    repo_root: Path,
    evidence_revision: str,
) -> None:
    observed_rows = tuple(
        tuple(row[field] for field in _M5_AUTHORITY_AUDIT_FIELDS) for row in rows
    )
    if observed_rows != _M5_REQUIRED_AUTHORITY_AUDIT_ROWS:
        raise Package0ComparatorError("M5 architecture/authority audit set differs")
    for row in rows:
        _validate_m5_locator(
            repo_root,
            row["evidence_locator"],
            evidence_revision=evidence_revision,
        )


def _validate_m5_verdict_contract(contract: Mapping[str, object]) -> None:
    if set(contract) != {
        "go_requirements",
        "product_authority_granted",
        "rules",
        "schema_version",
        "validation_status",
        "verdict_states",
    }:
        raise Package0ComparatorError("M5 verdict contract field set differs")
    if (
        contract.get("schema_version") != _M5_VERDICT_CONTRACT_SCHEMA
        or contract.get("validation_status") != "diagnostic_only"
        or contract.get("product_authority_granted") is not False
        or contract.get("go_requirements") != list(_M5_REQUIRED_GO_REQUIREMENTS)
        or contract.get("verdict_states") != list(_M5_REQUIRED_VERDICT_STATES)
    ):
        raise Package0ComparatorError("M5 verdict contract semantics differ")
    rules = contract.get("rules")
    if not isinstance(rules, Mapping) or dict(rules) != _M5_REQUIRED_VERDICT_RULES:
        raise Package0ComparatorError("M5 verdict contract rules differ")


def _validate_m5_dimensions(
    rows: tuple[dict[str, str], ...],
    *,
    repo_root: Path,
    evidence_revision: str,
) -> None:
    if _canonical_value_sha256(rows) != _M5_DIMENSION_CONTRACT_SHA256:
        raise Package0ComparatorError("M5 comparison dimension semantics differ")
    dimensions = {row["dimension"] for row in rows}
    if dimensions != _M5_REQUIRED_DIMENSIONS or len(dimensions) != len(rows):
        raise Package0ComparatorError("M5 comparison dimension set differs")
    for row in rows:
        if row["schema_version"] != _M5_PACKET_SCHEMA or row["outcome"] not in {
            "pass",
            "narrow",
            "unknown",
            "not_applicable",
            "fail",
        }:
            raise Package0ComparatorError("M5 comparison dimension row is malformed")
        if not all(
            row[field]
            for field in (
                "baseline_status",
                "candidate_status",
                "evidence_maturity",
                "claim_limit",
            )
        ):
            raise Package0ComparatorError("M5 comparison dimension lacks evidence")
        _validate_m5_locator(
            repo_root,
            row["evidence_locator"],
            evidence_revision=evidence_revision,
        )


def _validate_m5_selective_cases(
    rows: tuple[dict[str, str], ...],
    *,
    repo_root: Path,
    evidence_revision: str,
) -> None:
    case_ids = {row["case_id"] for row in rows}
    if len(case_ids) != len(rows) or len(rows) != 12:
        raise Package0ComparatorError("M5 selective case set is incomplete")
    contract_rows = tuple(
        {
            field: row[field]
            for field in (
                "case_id",
                "question",
                "expected",
                "evidence_maturity",
                "evidence_locator",
                "verdict_effect",
            )
        }
        for row in sorted(rows, key=lambda item: item["case_id"])
    )
    if _canonical_value_sha256(contract_rows) != _M5_SELECTIVE_CASE_CONTRACT_SHA256:
        raise Package0ComparatorError(
            "M5 preregistered selective case contract differs"
        )
    for row in rows:
        if (
            row["schema_version"] != _M5_PACKET_SCHEMA
            or row["status"] not in {"pass", "fail"}
            or (row["observed"] == row["expected"]) != (row["status"] == "pass")
            or row["verdict_effect"] not in {"failure_narrows", "failure_kills"}
        ):
            raise Package0ComparatorError(
                f"M5 candidate selective case result is malformed: {row['case_id']}"
            )
        if (
            not row["question"]
            or not row["evidence_maturity"]
            or not row["verdict_effect"]
        ):
            raise Package0ComparatorError("M5 selective case lacks evidence")
        _validate_m5_locator(
            repo_root,
            row["evidence_locator"],
            evidence_revision=evidence_revision,
        )


def _validate_m5_public_diffs(
    rows: tuple[dict[str, str], ...],
    *,
    repo_root: Path,
    evidence_revision: str,
) -> None:
    if _canonical_value_sha256(rows) != _M5_PUBLIC_DIFF_CONTRACT_SHA256:
        raise Package0ComparatorError("M5 public hard-zero semantics differ")
    surfaces = {row["surface"] for row in rows}
    if surfaces != _M5_REQUIRED_PUBLIC_SURFACES or len(surfaces) != len(rows):
        raise Package0ComparatorError("M5 public surface set differs")
    for row in rows:
        if (
            row["schema_version"] != _M5_PACKET_SCHEMA
            or row["expected_diff"] != "hard_zero"
            or row["observed_diff"] != "hard_zero"
            or row["status"] != "pass"
            or row["authority"] != "unchanged_existing_owner"
        ):
            raise Package0ComparatorError(
                f"M5 public hard-zero gate failed: {row['surface']}"
            )
        _validate_m5_locator(
            repo_root,
            row["evidence_locator"],
            evidence_revision=evidence_revision,
        )


def _validate_m5_rollback_packet(
    rollback: Mapping[str, object],
    *,
    repo_root: Path,
    evidence_revision: str,
) -> None:
    if set(rollback) != _M5_ROLLBACK_FIELDS:
        raise Package0ComparatorError("M5 rollback packet field set differs")
    if rollback.get("schema_version") != _M5_ROLLBACK_SCHEMA or (
        rollback.get("status") != "pass"
        or rollback.get("status_transition") != "staged_candidate->rolled_back"
        or rollback.get("active_authority_before") != "authoritative_legacy_current"
        or rollback.get("active_authority_after") != "authoritative_legacy_current"
        or rollback.get("mixed_authority_observed") is not False
        or rollback.get("successor_product_authority") is not False
        or rollback.get("candidate_evidence_retained") is not True
        or rollback.get("product_output_changed") is not False
    ):
        raise Package0ComparatorError("M5 rollback packet permits mixed authority")
    locator = rollback.get("evidence_locator")
    if not isinstance(locator, str):
        raise Package0ComparatorError("M5 rollback evidence locator is missing")
    _validate_m5_locator(
        repo_root,
        locator,
        evidence_revision=evidence_revision,
    )
    recorded = rollback.get("recorded_owner_receipt")
    if not isinstance(recorded, Mapping):
        raise Package0ComparatorError("M5 rollback owner receipt is missing")
    if set(recorded) != _M5_ROLLBACK_OWNER_RECEIPT_FIELDS:
        raise Package0ComparatorError(
            "M5 rollback owner receipt field set differs"
        )
    for field in (
        "lineage_artifact_ids_sha256",
        "delivery_revision_ids_sha256",
        "owner_receipt_sha256",
    ):
        _sha256(recorded.get(field), f"M5 rollback {field}")
    for field in ("lineage_artifact_count", "delivery_revision_count"):
        value = recorded.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise Package0ComparatorError("M5 rollback owner receipt count is invalid")
    typed_prefixes = {
        "processing_attempt_id": "xic.processing-attempt.v1:",
        "delivery_stage_id": "xic.targeted-delivery-stage-candidate.v1:",
        "delivery_seal_candidate_id": "xic.targeted-delivery-seal-candidate.v1:",
    }
    for field, prefix in typed_prefixes.items():
        value = recorded.get(field)
        if not isinstance(value, str) or not value.startswith(prefix):
            raise Package0ComparatorError(
                "M5 rollback owner receipt identity is invalid"
            )
    if recorded.get("prior_current_delivery_id") != "legacy-seal:prior-current":
        raise Package0ComparatorError("M5 rollback prior current identity differs")
    if _canonical_value_sha256(recorded) != _M5_ROLLBACK_RECEIPT_SUMMARY_SHA256:
        raise Package0ComparatorError("M5 recorded rollback owner receipt differs")


def _validate_m5_verdict(
    verdict: Mapping[str, object],
    *,
    contract: Mapping[str, object],
    candidate: Mapping[str, object],
    dimensions: tuple[dict[str, str], ...],
    cases: tuple[dict[str, str], ...],
    rollback: Mapping[str, object],
) -> str:
    if set(verdict) != _M5_VERDICT_FIELDS:
        raise Package0ComparatorError("M5 verdict field set differs")
    if verdict.get("schema_version") != _M5_PACKET_SCHEMA or (
        verdict.get("validation_status") != "diagnostic_only"
        or verdict.get("evaluated_scope") != "cid_nl_successor_shadow_substrate"
        or verdict.get("candidate_merge_sha") != candidate.get("candidate_merge_sha")
        or verdict.get("product_authority_granted") is not False
        or verdict.get("automatic_cutover_authorized") is not False
        or verdict.get("legacy_retirement_authorized") is not False
        or verdict.get("writer_expansion_authorized") is not False
    ):
        raise Package0ComparatorError("M5 verdict overclaims scope or authority")
    defects = verdict.get("candidate_defects")
    missing = verdict.get("missing_evidence")
    retained = verdict.get("retained_scope")
    excluded = verdict.get("excluded_scope")
    followups = verdict.get("known_preexisting_followups")
    gates = verdict.get("gate_status")
    if (
        not isinstance(defects, list)
        or not isinstance(missing, list)
        or not isinstance(retained, list)
        or not isinstance(excluded, list)
        or not isinstance(followups, list)
        or not isinstance(gates, Mapping)
        or not all(
            isinstance(item, str) and bool(item.strip())
            for values in (defects, missing, retained, excluded, followups)
            for item in values
        )
    ):
        raise Package0ComparatorError("M5 verdict evidence state is malformed")
    if set(gates) != _M5_VERDICT_GATE_STATUS_FIELDS:
        raise Package0ComparatorError("M5 verdict gate status field set differs")
    if defects != []:
        raise Package0ComparatorError("M5 candidate defect set differs")
    if tuple(missing) != _M5_REQUIRED_MISSING_EVIDENCE:
        raise Package0ComparatorError("M5 missing-evidence set differs")
    if tuple(retained) != _M5_REQUIRED_RETAINED_SCOPE:
        raise Package0ComparatorError("M5 retained scope differs")
    if tuple(followups) != _M5_REQUIRED_KNOWN_FOLLOWUPS:
        raise Package0ComparatorError("M5 known follow-up set differs")
    if tuple(excluded) != _M5_REQUIRED_NARROW_EXCLUSION_ORDER:
        raise Package0ComparatorError("M5 excluded scope differs")
    if dict(gates) != _M5_REQUIRED_GATE_STATUS:
        raise Package0ComparatorError("M5 gate status differs")
    has_dimension_failure = any(row["outcome"] == "fail" for row in dimensions)
    has_case_kill = any(
        row["status"] == "fail" and row["verdict_effect"] == "failure_kills"
        for row in cases
    )
    has_case_narrowing = any(
        row["status"] == "fail" and row["verdict_effect"] == "failure_narrows"
        for row in cases
    )
    has_narrowing = bool(defects) or has_case_narrowing or any(
        row["outcome"] in {"narrow", "unknown"} for row in dimensions
    )
    canonical_complete = gates.get("canonical_package4_complete") is True
    bounded_pass = gates.get("bounded_substrate_pass") is True
    public_pass = gates.get("public_hard_zero_pass") is True
    rollback_pass = (
        gates.get("rollback_pass") is True and rollback.get("status") == "pass"
    )
    if (
        canonical_complete
        and candidate.get("canonical_package4_go_reachable") is not True
    ):
        raise Package0ComparatorError(
            "M5 GO is unreachable for the frozen candidate scope"
        )
    if has_case_kill or has_dimension_failure:
        derived = "KILL"
    elif canonical_complete and not has_narrowing and not missing:
        derived = "GO"
    elif bounded_pass and public_pass and rollback_pass:
        derived = "NARROW"
    else:
        derived = "INCONCLUSIVE"
    declared = verdict.get("verdict")
    if declared == "GO" and not canonical_complete:
        raise Package0ComparatorError(
            "M5 GO is unreachable while canonical Package 4 gates are incomplete"
        )
    contract_states = contract.get("verdict_states")
    if (
        declared != derived
        or not isinstance(contract_states, list)
        or declared not in contract_states
    ):
        raise Package0ComparatorError(
            f"M5 declared verdict differs from evidence-derived verdict: {derived}"
        )
    return derived


def _m5_json_mapping(path: Path) -> Mapping[str, object]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise Package0ComparatorError(f"M5 JSON is unreadable: {path}") from exc
    return _m5_json_bytes_mapping(payload, source=str(path))


def _m5_json_bytes_mapping(
    payload: bytes,
    *,
    source: str,
) -> Mapping[str, object]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_m5_unique_json_mapping,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Package0ComparatorError(f"M5 JSON is unreadable: {source}") from exc
    if not isinstance(value, Mapping):
        raise Package0ComparatorError(f"M5 JSON must contain an object: {source}")
    return value


def _m5_unique_json_mapping(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    mapping: dict[str, object] = {}
    for key, value in pairs:
        if key in mapping:
            raise Package0ComparatorError(f"M5 JSON contains duplicate key: {key}")
        mapping[key] = value
    return mapping


def _m5_tsv_rows(
    path: Path,
    *,
    fields: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if tuple(reader.fieldnames or ()) != fields:
                raise Package0ComparatorError(f"M5 TSV fields differ: {path}")
            rows = tuple(dict(row) for row in reader)
    except OSError as exc:
        raise Package0ComparatorError(f"M5 TSV is unreadable: {path}") from exc
    if not rows or any(set(row) != set(fields) for row in rows):
        raise Package0ComparatorError(f"M5 TSV rows are malformed: {path}")
    return rows


def _m5_safe_relative_path(value: object, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise Package0ComparatorError(f"{field} must contain safe relative paths")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part in {"", ".", ".."} for part in windows_path.parts)
    ):
        raise Package0ComparatorError(f"{field} must contain safe relative paths")
    return path


def _validate_m5_locator(
    repo_root: Path,
    locator: str,
    *,
    evidence_revision: str,
) -> None:
    if not isinstance(locator, str) or not locator:
        raise Package0ComparatorError("M5 evidence locator is missing")
    path_text, separator, symbol = locator.partition("::")
    relative = _m5_safe_relative_path(path_text, field="evidence locator")
    try:
        source = _m5_git_bytes(
            repo_root,
            "show",
            f"{evidence_revision}:{relative}",
        ).decode("utf-8")
    except (Package0ComparatorError, UnicodeDecodeError):
        raise Package0ComparatorError(f"M5 evidence locator is missing: {locator}")
    if separator:
        if not symbol or f"def {symbol}(" not in source:
            raise Package0ComparatorError(
                f"M5 evidence locator symbol is missing: {locator}"
            )


def _m5_commit_sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _COMMIT_SHA_RE.fullmatch(value) is None:
        raise Package0ComparatorError(f"{field} must be a lower-case commit SHA")
    return value


def _m5_git_bytes(repo_root: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo_root), *args),
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise Package0ComparatorError(
            "M5 candidate Git repository is unavailable"
        ) from exc
    if completed.returncode != 0:
        raise Package0ComparatorError("M5 candidate Git object is unavailable")
    return completed.stdout


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


def _package1_output_root_identity(
    output_root: Path,
) -> tuple[int, int, int, int, int, str]:
    if not isinstance(output_root, Path):
        raise Package0ComparatorError(
            "Package 1 probe output_root must be a Path"
        )
    try:
        observed = os.lstat(output_root)
    except OSError as exc:
        raise Package0ComparatorError(
            "Package 1 comparator-owned output root is unavailable"
        ) from exc
    if not stat.S_ISDIR(observed.st_mode):
        raise Package0ComparatorError(
            "Package 1 comparator-owned output root must remain one directory"
        )
    marker_path = output_root / _PACKAGE1_ROOT_IDENTITY_MARKER
    try:
        marker = os.lstat(marker_path)
        marker_sha256 = hashlib.sha256(marker_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Package0ComparatorError(
            "Package 1 comparator-owned output root identity marker changed"
        ) from exc
    if not stat.S_ISREG(marker.st_mode):
        raise Package0ComparatorError(
            "Package 1 comparator-owned output root identity marker changed"
        )
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        marker.st_dev,
        marker.st_ino,
        marker_sha256,
    )


def _package1_product_output_inventory(
    output_root: Path,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(output_root, Path) or not output_root.is_dir():
        raise Package0ComparatorError(
            "Package 1 probe output_root must be an existing directory"
        )
    resolved_root = output_root.resolve()
    inventory: list[tuple[str, str]] = []
    for path in output_root.rglob("*"):
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root):
            raise Package0ComparatorError(
                "Package 1 product output path escapes the observed root"
            )
        if path.is_symlink():
            content_identity = "symlink"
        elif path.is_file():
            content_identity = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        elif path.is_dir():
            content_identity = "directory"
        else:
            content_identity = "other"
        inventory.append(
            (
                path.relative_to(output_root).as_posix(),
                content_identity,
            )
        )
    return tuple(sorted(inventory))


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


def _m5_canonical_text_bytes(value: object, *, field: str) -> bytes:
    if not isinstance(value, bytes):
        raise Package0ComparatorError(f"{field} must be UTF-8 text bytes")
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Package0ComparatorError(f"{field} must be valid UTF-8 text") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


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
    "M5RollbackRehearsalReceipt",
    "M5SuccessorVerdictReceipt",
    "Package0ComparatorError",
    "Package1DifferenceObservation",
    "Package1ObservationExperimentJob",
    "Package1ObservationTraceFixture",
    "assert_targeted_adapter_parity",
    "mint_targeted_adapter_parity_receipt",
    "run_package1_observation_experiment",
    "validate_m5_rollback_rehearsal",
    "validate_m5_successor_verdict_packet",
    "validate_package1_difference_manifest",
    "validate_package0_successor_output",
]
