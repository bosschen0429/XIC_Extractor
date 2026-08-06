"""Enumerate and validate the bounded ModeTrack Gate C persisted boundary.

The retained catalog is diagnostic-only.  It records existing semantic owners
and contradiction oracles; it does not grant persisted artifacts, receipts, or
review files Matrix-writing authority.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
import types
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, is_dataclass
from dataclasses import fields as dataclass_fields
from functools import cache
from pathlib import Path
from typing import Union, get_args, get_origin, get_type_hints

from xic_extractor.alignment import quant_matrix_version as quant_matrix
from xic_extractor.peak_detection.observations import PeakObservationContent

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT_PATH = (
    ROOT
    / "docs/superpowers/validation/"
    "issue191_gate_c_persisted_boundary_contract_v1/field_contract.tsv"
)
SCHEMA_VERSION = "issue191_gate_c_persisted_boundary_field_contract_v1"
RESULT_QMV_SCHEMA_DESCRIPTION = (
    "no_root_schema__QuantMatrixVersionRows("
    "cell=quant_matrix_cell_provenance_v1,"
    "diff=quant_matrix_version_expected_diff_summary_v1,"
    "row=quant_matrix_row_summary_v1)"
)

CONTRACT_COLUMNS = (
    "schema_version",
    "claim_id",
    "artifact_id",
    "artifact_schema",
    "enumeration_source",
    "canonical_field_path",
    "observed_type",
    "ingress_kind",
    "consumer",
    "semantic_claim",
    "semantic_class",
    "semantic_owner",
    "verification_source",
    "completeness_relation",
    "semantic_failure",
    "fail_closed_point",
    "contradiction_oracle",
    "resolution",
)
ALLOWED_SEMANTIC_CLASSES = frozenset(
    {"primitive", "locally_rederivable", "raw_owner_only", "audit_only"}
)
ALLOWED_RESOLUTIONS = frozenset({"fixed", "verified_unaffected", "unknown"})

FIXTURE = "tests/issue180_m9_raw_validation_fixture.py"
RUNNER = "tests/issue180_m9_gate_c_85raw_runner.py"
ALIGNMENT_TEST = "tests/test_alignment_mode_track_conservative_backfill.py"
RUNNER_TEST = "tests/test_issue180_m9_gate_c_85raw_runner.py"
OBSERVATION_TEST = "tests/test_peak_observation_refs.py"
ARCHITECTURE_CONTRACT = (
    "docs/agent/architecture-public-contracts.md#Persisted Semantic Rehydration"
)


@dataclass(frozen=True)
class ArtifactSpec:
    artifact_id: str
    artifact_schema: str
    enumeration_source: str
    ingress_kind: str
    consumer: str
    format: str = "json"
    expected_root_schema: str | None = None


@dataclass(frozen=True)
class FieldPolicy:
    semantic_class: str
    semantic_owner: str
    verification_source: str
    completeness_relation: str
    semantic_failure: str
    fail_closed_point: str
    contradiction_oracle: str
    resolution: str


V2 = "docs/superpowers/validation/issue180_m9_gate_c_85raw_v2"
V3 = "docs/superpowers/validation/issue180_m9_gate_c_closed_writer_v3"

ARTIFACT_SPECS = (
    ArtifactSpec(
        "launch_manifest",
        "issue180_m9_gate_c_launch_manifest_v2",
        f"{RUNNER}::build_launch_inputs",
        "producer_self_reload",
        f"{RUNNER}::run_frozen_launch",
    ),
    ArtifactSpec(
        "c1_mode_track_source",
        "m9_mode_track_source_v2",
        f"{V2}/c1_proposal/mode_track_source.json",
        "persisted_cross_stage_input",
        f"{FIXTURE}::_rehydrate_gate_c_review_set",
        expected_root_schema="m9_mode_track_source_v2",
    ),
    ArtifactSpec(
        "c1_proposal",
        "issue180_m9_gate_c_proposal_v2",
        f"{V2}/c1_proposal/proposal.json",
        "persisted_cross_stage_input",
        f"{FIXTURE}::_rehydrate_gate_c_review_set",
        expected_root_schema="issue180_m9_gate_c_proposal_v2",
    ),
    ArtifactSpec(
        "c1_proposal_receipt",
        "issue180_m9_gate_c_proposal_receipt_v2",
        f"{V2}/c1_proposal/proposal_receipt.json",
        "persisted_cross_stage_input",
        f"{FIXTURE}::_rehydrate_gate_c_review_set",
        expected_root_schema="issue180_m9_gate_c_proposal_receipt_v2",
    ),
    ArtifactSpec(
        "timing_payload",
        "issue180_m9_validation_heartbeat_v1",
        f"{V3}/c2_replay/timing.live.json",
        "producer_output",
        f"{FIXTURE}::_run_mode_track_batch",
        expected_root_schema="issue180_m9_validation_heartbeat_v1",
    ),
    ArtifactSpec(
        "manual_review",
        "issue180_m9_gate_c_manual_review_v1",
        f"{V3}/review_transfer/manual_review_v1.json",
        "operator_supplied_input",
        f"{FIXTURE}::_validate_manual_review",
        expected_root_schema="issue180_m9_gate_c_manual_review_v1",
    ),
    ArtifactSpec(
        "physical_replay",
        "issue180_m9_gate_c_observation_replay_v1",
        f"{V3}/review_transfer/manual_review_observation_replay_v1.json",
        "persisted_cross_stage_input",
        f"{FIXTURE}::_validate_manual_review_replay",
        expected_root_schema="issue180_m9_gate_c_observation_replay_v1",
    ),
    ArtifactSpec(
        "review_transfer_receipt",
        "issue180_m9_gate_c_review_transfer_receipt_v1",
        f"{V3}/review_transfer/review_transfer_receipt.json",
        "persisted_cross_stage_input",
        f"{FIXTURE}::_validate_review_transfer_receipt",
        expected_root_schema="issue180_m9_gate_c_review_transfer_receipt_v1",
    ),
    ArtifactSpec(
        "review_checked_manifest",
        "production_acceptance_manifest_v1",
        f"{V3}/review_transfer/checked_manifest_v1.tsv",
        "persisted_cross_stage_input",
        f"{FIXTURE}::_validate_review_transfer_receipt",
        format="tsv",
        expected_root_schema="production_acceptance_manifest_v1",
    ),
    ArtifactSpec(
        "fresh_mode_track_source",
        "m9_mode_track_source_v2",
        f"{V3}/c2_replay/fresh_mode_track_source.json",
        "producer_output",
        f"{FIXTURE}::finalize_gate_c_fresh_projection",
        expected_root_schema="m9_mode_track_source_v2",
    ),
    ArtifactSpec(
        "fresh_owner_receipt",
        "issue180_m9_gate_c_fresh_owner_receipt_v1",
        f"{V3}/c2_replay/fresh_owner_receipt.json",
        "producer_output",
        f"{FIXTURE}::_finalize_gate_c_transaction",
        expected_root_schema="issue180_m9_gate_c_fresh_owner_receipt_v1",
    ),
    ArtifactSpec(
        "terminal_checked_manifest",
        "production_acceptance_manifest_v1",
        f"{V3}/c2_final/checked_manifest_v1.tsv",
        "producer_output",
        f"{FIXTURE}::finalize_gate_c_fresh_projection",
        format="tsv",
        expected_root_schema="production_acceptance_manifest_v1",
    ),
    ArtifactSpec(
        "result_qmv",
        RESULT_QMV_SCHEMA_DESCRIPTION,
        f"{V3}/c2_final/result_qmv.json",
        "producer_output",
        f"{FIXTURE}::_validate_gate_c_fresh_terminal",
    ),
    ArtifactSpec(
        "final_receipt",
        "issue180_m9_gate_c_final_receipt_v3",
        f"{V3}/c2_final/final_receipt.json",
        "producer_output",
        f"{FIXTURE}::_finalize_gate_c_transaction",
        expected_root_schema="issue180_m9_gate_c_final_receipt_v3",
    ),
    ArtifactSpec(
        "transaction_receipt",
        "issue180_m9_gate_c_transaction_receipt_v2",
        f"{V3}/c2_final/transaction_receipt.json",
        "producer_output",
        f"{RUNNER}::run_frozen_launch",
        expected_root_schema="issue180_m9_gate_c_transaction_receipt_v2",
    ),
)

TYPED_JSON_SUBTREE_OWNERS: Mapping[
    str, tuple[tuple[str, type[object]], ...]
] = {
    "c1_mode_track_source": (
        ("$.rows[].peak_observation.content", PeakObservationContent),
    ),
    "physical_replay": (
        ("$.rows[].observation_content", PeakObservationContent),
    ),
    "fresh_mode_track_source": (
        ("$.rows[].peak_observation.content", PeakObservationContent),
    ),
}

DYNAMIC_MATRIX_ROW_PATHS = {
    "c1_proposal": "$.input_quant_matrix_rows[]",
    "result_qmv": "$.quant_matrix_rows[]",
}
MATRIX_ROW_PATHS = {
    "c1_proposal": (
        "$.input_matrix_identity_rows[]",
        "$.input_quant_matrix_rows[]",
    ),
    "result_qmv": ("$.quant_matrix_rows[]",),
}

POLICIES = {
    "launch": FieldPolicy(
        "locally_rederivable",
        f"{RUNNER}::build_launch_inputs",
        f"{RUNNER}::run_frozen_launch",
        "The persisted manifest must equal a fresh full launch manifest and all "
        "filesystem, checkout, command, and C2 input bindings are recomputed.",
        "A stale or caller-authored launch value could dispatch a different run.",
        "run_frozen_launch, before C1/C2 dispatch",
        f"{RUNNER_TEST}::test_frozen_launch_rejects_checkout_dirtied_after_preflight",
        "fixed",
    ),
    "source_subject": FieldPolicy(
        "raw_owner_only",
        f"{FIXTURE}::_rehydrate_gate_c_review_set",
        f"{FIXTURE}::_validate_fresh_mode_track_projection",
        "The complete persisted review-subject census must equal the fresh C2 "
        "RAW-owner subject census before a validated review set is minted.",
        "A persisted subject identity or raw area could authorize a different cell.",
        "_rehydrate_gate_c_review_set, before _ValidatedGateCReviewSet minting",
        f"{ALIGNMENT_TEST}::test_gate_c_fresh_owner_rejects_resealed_sample_stem_claim",
        "fixed",
    ),
    "observation": FieldPolicy(
        "locally_rederivable",
        "xic_extractor/peak_detection/observations.py::"
        "verify_persisted_peak_observation_claims",
        f"{FIXTURE}::_validate_manual_review_replay",
        "Exact persisted observation fields are parsed and content identity, apex, "
        "height, and trapezoid raw area are recomputed from retained values.",
        "A re-sealed signal value could contradict its content ID or raw-area claim.",
        "_validate_manual_review_replay, before review-subject acceptance",
        f"{OBSERVATION_TEST}::test_persisted_content_rejects_resealed_signal_derived_claim",
        "fixed",
    ),
    "proposal_fresh": FieldPolicy(
        "raw_owner_only",
        f"{FIXTURE}::_validate_fresh_mode_track_projection",
        f"{FIXTURE}::_validate_fresh_proposed_keys",
        "The full scientific projection and complete candidate identity/decision "
        "map must equal the fresh C2 RAW-owner result.",
        "A persisted eligibility or candidate decision could widen writer membership.",
        "_rehydrate_gate_c_review_set, before manual-review rehydration",
        f"{ALIGNMENT_TEST}::test_gate_c_fresh_owner_rejects_resealed_rejected_to_proposed_claim",
        "fixed",
    ),
    "binding": FieldPolicy(
        "locally_rederivable",
        f"{FIXTURE}::_validate_proposal_payload",
        f"{FIXTURE}::_rehydrate_gate_c_review_set",
        "Schema, hashes, row census, keys, and cross-artifact bindings are "
        "recomputed from the complete persisted packet.",
        "A missing, foreign, duplicate, or re-sealed binding could join unlike "
        "evidence.",
        "Gate C packet validation, before fresh review-set minting",
        f"{ALIGNMENT_TEST}::test_gate_c_finalizer_rejects_tampered_proposal",
        "fixed",
    ),
    "human_primitive": FieldPolicy(
        "primitive",
        f"{FIXTURE}::_validate_manual_review",
        f"{FIXTURE}::_validate_manual_review",
        "Every fresh pending subject must have one bound, nonblank human review "
        "record and the verdict must be pass.",
        "A missing, foreign, duplicate, or non-passing human verdict cannot authorize.",
        "_validate_manual_review, before _ValidatedGateCReviewSet minting",
        f"{ALIGNMENT_TEST}::test_gate_c_rejects_failed_review_and_ignores_external_manifest",
        "fixed",
    ),
    "review_binding": FieldPolicy(
        "locally_rederivable",
        f"{FIXTURE}::_validate_manual_review",
        f"{FIXTURE}::_rehydrate_gate_c_review_set",
        "Review keys, proposal/source/replay digests, attempt identity, verdict, and "
        "complete subject coverage are checked against the current packet.",
        "A review could be replayed against a different proposal or subject.",
        "manual review/replay validation, before review-set minting",
        f"{ALIGNMENT_TEST}::test_gate_c_review_transfer_rebinds_exact_subjects_and_finalizes",
        "fixed",
    ),
    "transfer_binding": FieldPolicy(
        "locally_rederivable",
        f"{FIXTURE}::_validate_review_transfer_receipt",
        f"{FIXTURE}::_validate_review_transfer_receipt",
        "Current proposal/review/replay/manifest hashes and exact subject count/digest "
        "are recomputed; product_authority must remain none_review_input_only.",
        "A transfer receipt could bind a different reviewed packet.",
        "_validate_review_transfer_receipt, before fresh C2 execution",
        f"{ALIGNMENT_TEST}::test_gate_c_review_transfer_receipt_rejects_resealed_input_binding",
        "fixed",
    ),
    "fresh_output": FieldPolicy(
        "raw_owner_only",
        "xic_extractor/alignment/untargeted_cid_nl_golden_path.py::"
        "build_untargeted_cid_nl_mode_track_projection",
        f"{FIXTURE}::finalize_gate_c_fresh_projection",
        "The field is emitted from the current in-process typed C2 RAW-owner "
        "projection; the persisted file supplies no later scientific authority.",
        "A caller-authored output projection could replace the current owner result.",
        "finalize_gate_c_fresh_projection typed-input boundary",
        f"{ALIGNMENT_TEST}::test_gate_c_writer_boundary_accepts_only_fresh_typed_authority",
        "verified_unaffected",
    ),
    "terminal_manifest": FieldPolicy(
        "raw_owner_only",
        "xic_extractor/alignment/production_acceptance.py::"
        "load_checked_production_acceptance_manifest",
        f"{FIXTURE}::_validate_pending_manifest_equality",
        "All rows are derived from fresh pending rows, owner-loaded, then compared "
        "field-for-field with the fresh proposal before QMV construction.",
        "A caller-authored manifest field could select or value a different Matrix "
        "cell.",
        "finalize_gate_c_fresh_projection, before QMV construction",
        f"{ALIGNMENT_TEST}::test_gate_c_rejects_failed_review_and_ignores_external_manifest",
        "verified_unaffected",
    ),
    "qmv": FieldPolicy(
        "raw_owner_only",
        "xic_extractor/alignment/production_acceptance.py::"
        "build_quant_matrix_version_from_checked_manifest",
        f"{FIXTURE}::_validate_gate_c_fresh_terminal",
        "QMV rows, provenance, summaries, expected diff, and rollback are built from "
        "the fresh checked owner result and validated as one terminal result.",
        "A missing, extra, or unrelated Matrix value could escape expected-diff "
        "closure.",
        "_validate_gate_c_fresh_terminal, before final publication",
        f"{ALIGNMENT_TEST}::test_gate_c_terminal_oracle_rejects_owner_output_with_missing_value",
        "verified_unaffected",
    ),
    "terminal": FieldPolicy(
        "locally_rederivable",
        f"{FIXTURE}::_finalize_gate_c_transaction",
        f"{FIXTURE}::finalize_gate_c_fresh_projection",
        "Counts, statuses, digests, rollback, and terminal authority are derived only "
        "after owner checks; final artifacts publish together by atomic rename.",
        "A caller-authored terminal value could claim partial or false success.",
        "closed writer transaction, before final-directory publication",
        f"{ALIGNMENT_TEST}::test_gate_c_transaction_receipt_publishes_atomically_or_not_at_all",
        "verified_unaffected",
    ),
    "audit_source": FieldPolicy(
        "audit_only",
        "xic_extractor/alignment/untargeted_cid_nl_golden_path.py::"
        "build_untargeted_cid_nl_mode_track_projection",
        ARCHITECTURE_CONTRACT,
        "The byte/hash envelope is retained, but this field is not rehydrated as "
        "downstream scientific authority and cannot select or value a writer cell.",
        "Treating the audit field as authority would exceed retained owner evidence.",
        "not consumed by the closed writer; fresh typed projection remains "
        "authoritative",
        f"{ALIGNMENT_TEST}::test_gate_c_fresh_projection_ignores_resealed_c1_writer_values",
        "verified_unaffected",
    ),
    "audit_manifest": FieldPolicy(
        "audit_only",
        "xic_extractor/alignment/production_acceptance.py::"
        "load_checked_production_acceptance_manifest",
        ARCHITECTURE_CONTRACT,
        "The transferred manifest is hash-bound review evidence only; the finalizer "
        "generates and owner-loads a new manifest from fresh pending rows.",
        "Treating the external manifest as writer input could bypass fresh membership.",
        "external manifest is ignored before fresh checked-manifest construction",
        f"{ALIGNMENT_TEST}::test_gate_c_rejects_failed_review_and_ignores_external_manifest",
        "verified_unaffected",
    ),
    "audit_receipt": FieldPolicy(
        "audit_only",
        f"{FIXTURE}::transfer_gate_c_review",
        ARCHITECTURE_CONTRACT,
        "Historical lineage is retained as a well-formed digest only and is not a "
        "current scientific or writer-authority input.",
        "A historical digest cannot prove the embedded historical semantics.",
        "not consumed as current writer authority",
        f"{ALIGNMENT_TEST}::test_gate_c_review_transfer_receipt_rejects_resealed_input_binding",
        "verified_unaffected",
    ),
    "audit_timing": FieldPolicy(
        "audit_only",
        f"{FIXTURE}::_run_mode_track_batch",
        ARCHITECTURE_CONTRACT,
        "Timing is observational telemetry and is not an acceptance, value, count, "
        "or terminal-authority input.",
        "A timing marker cannot establish scientific or transaction success.",
        "not consumed by Gate C acceptance or publication",
        f"{ALIGNMENT_TEST}::test_gate_c_transaction_receipt_publishes_atomically_or_not_at_all",
        "verified_unaffected",
    ),
    "fresh_receipt": FieldPolicy(
        "locally_rederivable",
        f"{FIXTURE}::run_gate_c_fresh_finalization",
        f"{FIXTURE}::_finalize_gate_c_transaction",
        "Fresh source, proposal, review, call-cost, and owner-authority bindings are "
        "derived in the current C2 process and hash-bound into the transaction.",
        "A supplied receipt count, digest, or authority could misstate the fresh run.",
        "run_gate_c_fresh_finalization, before transaction finalization",
        f"{ALIGNMENT_TEST}::test_gate_c_writer_boundary_accepts_only_fresh_typed_authority",
        "verified_unaffected",
    ),
}


def declared_launch_manifest_shapes() -> tuple[dict[str, object], ...]:
    """Return C1/C2 structural witnesses without private paths or runtime data."""
    launch_contract = {
        "schema_version": "issue180_m9_gate_c_launch_contract_v2",
        "validation_status": "diagnostic_only",
        "precursor_mz": 0.0,
        "neutral_loss_da": 0.0,
        "alignment_config": {"anchor_min_seed_events": 0},
        "trajectory_policy": {
            "trajectory_policy_version": "",
            "band_policy_version": "",
            "prediction_half_width_min": 0.0,
            "max_interpolation_gap": 0,
            "max_extrapolation_gap": 0,
        },
        "call_budget": {
            "discovery_raw_open_count_max": 0,
            "m7_raw_open_count_max": 0,
            "total_raw_open_count_max": 0,
            "m7_xic_batch_count_max": 0,
        },
        "product_authority": "",
    }
    base: dict[str, object] = {
        "schema_version": "issue180_m9_gate_c_launch_manifest_v2",
        "stage": "",
        "launch_contract": launch_contract,
        "code_sha": "",
        "execution": {
            "executable": "",
            "working_directory": "",
            "argv": [""],
            "command_sha256": "",
            "worktree_status": "",
        },
        "metadata": {"path": "", "sha256": ""},
        "raw": {
            "root": "",
            "sample_count": 0,
            "identities": [
                {"raw_stem": "", "path": "", "size_bytes": 0, "mtime_ns": 0}
            ],
            "universe_sha256": "",
        },
        "dll": {"root": "", "tree_sha256": ""},
        "manifest_root": "",
        "output_dir": "",
        "launch_manifest_path": "",
    }
    c1 = {
        **base,
        "c2_inputs": None,
        "receipt_targets": {
            "proposal": "",
            "proposal_receipt": "",
            "runtime": "",
            "timing_live": "",
        },
    }
    c2 = {
        **base,
        "c2_inputs": {
            "proposal_dir": "",
            "proposal_sha256": "",
            "proposal_receipt_sha256": "",
            "manual_review": {"path": "", "sha256": ""},
            "manual_review_replay": {"path": "", "sha256": ""},
            "review_transfer_receipt": {"path": "", "sha256": ""},
            "checked_manifest": {"path": "", "sha256": ""},
            "final_output_dir": "",
        },
        "receipt_targets": {
            "fresh_owner_receipt": "",
            "runtime": "",
            "timing_live": "",
            "final_receipt": "",
            "transaction_receipt": "",
        },
    }
    return c1, c2


def enumerate_gate_c_boundary() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for spec in ARTIFACT_SPECS:
        fields = _artifact_fields(spec)
        for path, observed_type in sorted(fields.items()):
            for semantic_claim in _semantic_claims(
                spec.artifact_id,
                path,
                observed_type,
            ):
                policy = _field_policy(spec.artifact_id, path, semantic_claim)
                rows.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "claim_id": "|".join(
                            (spec.artifact_id, path, semantic_claim)
                        ),
                        "artifact_id": spec.artifact_id,
                        "artifact_schema": spec.artifact_schema,
                        "enumeration_source": spec.enumeration_source,
                        "canonical_field_path": path,
                        "observed_type": observed_type,
                        "ingress_kind": spec.ingress_kind,
                        "consumer": spec.consumer,
                        "semantic_claim": semantic_claim,
                        "semantic_class": policy.semantic_class,
                        "semantic_owner": policy.semantic_owner,
                        "verification_source": policy.verification_source,
                        "completeness_relation": policy.completeness_relation,
                        "semantic_failure": policy.semantic_failure,
                        "fail_closed_point": policy.fail_closed_point,
                        "contradiction_oracle": policy.contradiction_oracle,
                        "resolution": policy.resolution,
                    }
                )
    return sorted(
        rows,
        key=lambda row: (
            row["artifact_id"],
            row["canonical_field_path"],
            row["semantic_claim"],
        ),
    )


def canonical_json_fields(
    payload: Mapping[str, object],
    *,
    artifact_id: str,
    matrix_header: Sequence[str] | None = None,
) -> dict[str, str]:
    """Return stable named-field paths, collapsing dynamic Matrix columns."""
    fields: dict[str, set[str]] = {}
    sample_columns = _matrix_sample_columns(
        payload,
        artifact_id=artifact_id,
        matrix_header=matrix_header,
    )
    _collect_json_fields(
        payload,
        "$",
        fields,
        artifact_id,
        sample_columns=sample_columns,
    )
    return {
        path: _canonical_json_type_union(types)
        for path, types in fields.items()
    }


def declared_launch_manifest_fields() -> dict[str, str]:
    fields: dict[str, set[str]] = {}
    for shape in declared_launch_manifest_shapes():
        _collect_json_fields(
            shape,
            "$",
            fields,
            "launch_manifest",
            sample_columns=frozenset(),
        )
    return {
        path: _canonical_json_type_union(types)
        for path, types in fields.items()
    }


def _canonical_json_type_union(types: Iterable[str]) -> str:
    canonical_types = set(types)
    if "array[empty]" in canonical_types and any(
        observed_type.startswith("array[")
        and observed_type != "array[empty]"
        for observed_type in canonical_types
    ):
        canonical_types.remove("array[empty]")
    return "|".join(sorted(canonical_types))


def check_gate_c_persisted_boundary_contract(
    *, contract_path: Path = DEFAULT_CONTRACT_PATH
) -> list[str]:
    problems: list[str] = []
    try:
        generated = enumerate_gate_c_boundary()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"enumeration failed: {exc}"]
    try:
        retained = _read_contract(contract_path)
    except (OSError, ValueError) as exc:
        return [f"contract: {exc}"]

    _validate_rows(generated, problems=problems)
    _validate_rows(retained, problems=problems)
    if retained != generated:
        retained_by_id = {row.get("claim_id", ""): row for row in retained}
        generated_by_id = {row["claim_id"]: row for row in generated}
        missing = sorted(generated_by_id.keys() - retained_by_id.keys())
        stale = sorted(retained_by_id.keys() - generated_by_id.keys())
        changed = sorted(
            claim_id
            for claim_id in generated_by_id.keys() & retained_by_id.keys()
            if generated_by_id[claim_id] != retained_by_id[claim_id]
        )
        problems.append(
            "retained contract differs from unchanged enumeration "
            f"(generated={len(generated)}, retained={len(retained)}, "
            f"missing={len(missing)}, stale={len(stale)}, changed={len(changed)})"
        )
        for label, values in (
            ("missing", missing),
            ("stale", stale),
            ("changed", changed),
        ):
            if values:
                problems.append(f"{label} claim ids: " + ", ".join(values[:5]))
    return problems


def write_gate_c_persisted_boundary_contract(
    *, contract_path: Path = DEFAULT_CONTRACT_PATH
) -> None:
    rows = enumerate_gate_c_boundary()
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    with contract_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CONTRACT_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _artifact_fields(spec: ArtifactSpec) -> dict[str, str]:
    if spec.artifact_id == "launch_manifest":
        return declared_launch_manifest_fields()

    source_path = ROOT / spec.enumeration_source
    matrix_header = (
        _retained_proposal_matrix_header()
        if spec.artifact_id == "result_qmv"
        else None
    )
    return artifact_fields_from_path(
        spec,
        source_path,
        source_label=spec.enumeration_source,
        matrix_header=matrix_header,
    )


def artifact_fields_from_path(
    spec: ArtifactSpec,
    source_path: Path,
    *,
    source_label: str | None = None,
    matrix_header: Sequence[str] | None = None,
) -> dict[str, str]:
    """Enumerate one artifact while enforcing its declared live schema marker."""

    source = source_label or source_path.as_posix()
    if spec.format == "tsv":
        with source_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if reader.fieldnames is None:
                raise ValueError(f"{source}: missing TSV header")
            rows = list(reader)
        if not rows:
            raise ValueError(f"{source}: TSV has no rows")
        if spec.expected_root_schema is not None and {
            row.get("schema_version", "") for row in rows
        } != {spec.expected_root_schema}:
            raise ValueError(f"{source}: row schema changed")
        return {f"column:{column}": "string" for column in reader.fieldnames}

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{source}: JSON root must be an object")
    if (
        spec.expected_root_schema is not None
        and payload.get("schema_version") != spec.expected_root_schema
    ):
        raise ValueError(f"{source}: root schema changed")
    if spec.artifact_id == "result_qmv":
        _validate_result_qmv_components(payload, source=source)
    observed_fields = canonical_json_fields(
        payload,
        artifact_id=spec.artifact_id,
        matrix_header=matrix_header,
    )
    return _normalize_owner_declared_fields(
        spec,
        observed_fields,
        source=source,
    )


def _normalize_owner_declared_fields(
    spec: ArtifactSpec,
    observed_fields: Mapping[str, str],
    *,
    source: str,
) -> dict[str, str]:
    normalized = dict(observed_fields)
    for root_path, owner_type in TYPED_JSON_SUBTREE_OWNERS.get(
        spec.artifact_id, ()
    ):
        declared_fields = _declared_dataclass_json_fields(owner_type, root_path)
        observed_subtree = {
            path: observed_type
            for path, observed_type in observed_fields.items()
            if path == root_path or path.startswith(f"{root_path}.")
        }
        if set(observed_subtree) != set(declared_fields):
            missing = sorted(set(declared_fields) - set(observed_subtree))
            unexpected = sorted(set(observed_subtree) - set(declared_fields))
            raise ValueError(
                f"{source}: {owner_type.__name__} field paths changed "
                f"(missing={missing[:5]}, unexpected={unexpected[:5]})"
            )
        for path, observed_type in observed_subtree.items():
            declared_types = _split_json_type_union(declared_fields[path])
            incompatible = sorted(
                concrete_type
                for concrete_type in _split_json_type_union(observed_type)
                if not _concrete_type_matches_declaration(
                    concrete_type,
                    declared_types,
                )
            )
            if incompatible:
                raise ValueError(
                    f"{source}: {path} has incompatible live type "
                    f"{'|'.join(incompatible)}; "
                    f"{owner_type.__name__} declares "
                    f"{declared_fields[path]}"
                )
        normalized.update(declared_fields)
    return normalized


def _declared_dataclass_json_fields(
    owner_type: type[object], root_path: str
) -> dict[str, str]:
    if not is_dataclass(owner_type):
        raise ValueError(f"{owner_type!r} is not a dataclass semantic owner")
    declared = {root_path: "object"}
    _append_dataclass_json_fields(owner_type, root_path, declared)
    return declared


def _append_dataclass_json_fields(
    owner_type: type[object],
    root_path: str,
    declared: dict[str, str],
) -> None:
    type_hints = get_type_hints(owner_type)
    for owner_field in dataclass_fields(owner_type):
        annotation = type_hints.get(owner_field.name)
        if annotation is None:
            raise ValueError(
                f"{owner_type.__name__}.{owner_field.name} has no type annotation"
            )
        path = f"{root_path}.{owner_field.name}"
        declared[path] = _annotation_json_type(annotation)
        if isinstance(annotation, type) and is_dataclass(annotation):
            _append_dataclass_json_fields(annotation, path, declared)


def _annotation_json_type(annotation: object) -> str:
    origin = get_origin(annotation)
    if origin in {types.UnionType, Union}:
        union_types: set[str] = set()
        for member in get_args(annotation):
            union_types.update(
                _split_json_type_union(_annotation_json_type(member))
            )
        return "|".join(sorted(union_types))
    if annotation is type(None):
        return "null"
    if annotation is bool:
        return "boolean"
    if annotation in {float, int}:
        return "number"
    if annotation is str:
        return "string"
    if isinstance(annotation, type) and is_dataclass(annotation):
        return "object"
    if origin in {tuple, list}:
        args = get_args(annotation)
        if not args:
            raise ValueError(
                f"unsupported untyped JSON array annotation: {annotation!r}"
            )
        element_annotations = args[:-1] if args[-1:] == (Ellipsis,) else args
        element_types: set[str] = set()
        for element_annotation in element_annotations:
            element_types.update(
                _split_json_type_union(
                    _annotation_json_type(element_annotation)
                )
            )
        return f"array[{'|'.join(sorted(element_types))}]"
    raise ValueError(f"unsupported JSON type annotation: {annotation!r}")


def _split_json_type_union(type_union: str) -> set[str]:
    members: set[str] = set()
    start = 0
    bracket_depth = 0
    for index, character in enumerate(type_union):
        if character == "[":
            bracket_depth += 1
        elif character == "]":
            bracket_depth -= 1
        elif character == "|" and bracket_depth == 0:
            members.add(type_union[start:index])
            start = index + 1
    members.add(type_union[start:])
    if "" in members or bracket_depth != 0:
        raise ValueError(f"invalid canonical JSON type union: {type_union!r}")
    return members


def _concrete_type_matches_declaration(
    concrete_type: str,
    declared_types: set[str],
) -> bool:
    if concrete_type in declared_types:
        return True
    return concrete_type == "array[empty]" and any(
        declared_type.startswith("array[") for declared_type in declared_types
    )


def _validate_result_qmv_components(
    payload: Mapping[str, object], *, source: str
) -> None:
    expected_components = {
        "cell_provenance_rows": "quant_matrix_cell_provenance_v1",
        "expected_diff_summary_rows": "quant_matrix_version_expected_diff_summary_v1",
        "row_summary_rows": "quant_matrix_row_summary_v1",
    }
    if set(payload) != {*expected_components, "quant_matrix_rows"}:
        raise ValueError(f"{source}: QMV component set changed")
    for field, expected_schema in expected_components.items():
        rows = payload.get(field)
        if (
            not isinstance(rows, list)
            or not rows
            or any(
                not isinstance(row, dict)
                or row.get("schema_version") != expected_schema
                for row in rows
            )
        ):
            raise ValueError(f"{source}: {field} schema changed")
    matrix_rows = payload.get("quant_matrix_rows")
    if not isinstance(matrix_rows, list) or not matrix_rows or any(
        not isinstance(row, dict) for row in matrix_rows
    ):
        raise ValueError(f"{source}: quant_matrix_rows changed")


def _retained_proposal_matrix_header() -> tuple[str, ...]:
    proposal_spec = next(
        spec for spec in ARTIFACT_SPECS if spec.artifact_id == "c1_proposal"
    )
    proposal_path = ROOT / proposal_spec.enumeration_source
    payload = json.loads(proposal_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{proposal_spec.enumeration_source}: invalid proposal")
    return _declared_matrix_header(
        payload.get("matrix_header"),
        source=proposal_spec.enumeration_source,
    )


def _matrix_sample_columns(
    payload: Mapping[str, object],
    *,
    artifact_id: str,
    matrix_header: Sequence[str] | None,
) -> frozenset[str]:
    row_path = DYNAMIC_MATRIX_ROW_PATHS.get(artifact_id)
    if row_path is None:
        return frozenset()

    if artifact_id == "c1_proposal":
        embedded_header = _declared_matrix_header(
            payload.get("matrix_header"),
            source="c1_proposal.matrix_header",
        )
        if matrix_header is not None and tuple(matrix_header) != embedded_header:
            raise ValueError("c1_proposal: supplied Matrix header changed")
        header = embedded_header
        row_field = "input_quant_matrix_rows"
    else:
        if matrix_header is None:
            raise ValueError("result_qmv: Matrix header context is required")
        header = _declared_matrix_header(
            matrix_header,
            source="result_qmv Matrix header",
        )
        row_field = "quant_matrix_rows"

    try:
        sample_columns = tuple(quant_matrix._sample_columns(header))
    except ValueError as exc:
        raise ValueError(f"{artifact_id}: invalid Matrix header: {exc}") from exc
    if not sample_columns or len(set(sample_columns)) != len(sample_columns):
        raise ValueError(f"{artifact_id}: Matrix sample columns changed")

    rows = payload.get(row_field)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{artifact_id}: {row_field} must contain rows")
    expected_fields = set(header)
    if any(not isinstance(row, dict) or set(row) != expected_fields for row in rows):
        raise ValueError(f"{artifact_id}: {row_field} differs from Matrix header")
    return frozenset(sample_columns)


def _declared_matrix_header(value: object, *, source: str) -> tuple[str, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(column, str) or not column for column in value)
    ):
        raise ValueError(f"{source}: Matrix header must be non-empty strings")
    header = tuple(value)
    if len(set(header)) != len(header):
        raise ValueError(f"{source}: Matrix header contains duplicates")
    return header


def _collect_json_fields(
    value: object,
    parent: str,
    fields: dict[str, set[str]],
    artifact_id: str,
    *,
    sample_columns: frozenset[str],
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = _normalized_json_key(
                artifact_id,
                parent,
                str(key),
                sample_columns=sample_columns,
            )
            path = f"{parent}.{normalized_key}"
            fields.setdefault(path, set()).add(_json_type(child))
            _collect_json_fields(
                child,
                path,
                fields,
                artifact_id,
                sample_columns=sample_columns,
            )
    elif isinstance(value, list):
        for child in value:
            _collect_json_fields(
                child,
                f"{parent}[]",
                fields,
                artifact_id,
                sample_columns=sample_columns,
            )


def _normalized_json_key(
    artifact_id: str,
    parent: str,
    key: str,
    *,
    sample_columns: frozenset[str],
) -> str:
    if parent == DYNAMIC_MATRIX_ROW_PATHS.get(artifact_id) and key in sample_columns:
        return "{sample_column}"
    return key


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        if not value:
            return "array[empty]"
        element_types = "|".join(sorted({_json_type(item) for item in value}))
        return f"array[{element_types}]"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _semantic_claims(
    artifact_id: str,
    path: str,
    observed_type: str,
) -> tuple[str, ...]:
    leaf = path.rsplit(".", 1)[-1].removeprefix("column:")
    lowered = leaf.lower()
    claims: list[str]
    if lowered in {"rt", "intensity", "mz"} and _is_observation_content_path(
        artifact_id,
        path,
    ):
        claims = ["numeric_value", "observation_content_identity_component"]
        if "array" in observed_type:
            claims.insert(0, "container_shape_and_presence")
    elif lowered in {"mz", "rt"} and _is_matrix_row_path(artifact_id, path):
        claims = ["matrix_coordinate_numeric_value"]
    elif (artifact_id, path) in {
        ("c1_mode_track_source", "$.rows[].physical_ids"),
        ("fresh_mode_track_source", "$.rows[].physical_ids"),
    }:
        claims = [
            "container_shape_and_presence",
            "identity_collection",
            "index_2_observation_content_identity",
        ]
    elif "array" in observed_type:
        claims = ["container_shape_and_presence"]
        if "array[object]" not in observed_type:
            claims.append("array_element_values")
    elif "object" in observed_type:
        claims = ["container_shape_and_presence"]
    elif leaf == "{sample_column}":
        claims = ["matrix_cell_numeric_value", "matrix_sample_column_identity"]
    else:
        claims = ["field_value"]
    return tuple(claims)


def _is_observation_content_path(artifact_id: str, path: str) -> bool:
    return any(
        owner_type is PeakObservationContent
        and (path == root_path or path.startswith(f"{root_path}."))
        for root_path, owner_type in TYPED_JSON_SUBTREE_OWNERS.get(
            artifact_id, ()
        )
    )


def _is_matrix_row_path(artifact_id: str, path: str) -> bool:
    return any(
        path.startswith(f"{root_path}.")
        for root_path in MATRIX_ROW_PATHS.get(artifact_id, ())
    )


def _field_policy(
    artifact_id: str, path: str, semantic_claim: str
) -> FieldPolicy:
    if artifact_id == "launch_manifest":
        return POLICIES["launch"]
    if artifact_id == "c1_mode_track_source":
        if path.startswith("$.rows[].peak_observation.content"):
            return POLICIES["observation"]
        if semantic_claim == "index_2_observation_content_identity" or path in {
            "$.rows[].group_hypothesis_id",
            "$.rows[].sample_stem",
            "$.rows[].sample_identity_id",
            "$.rows[].quant_value",
        }:
            return POLICIES["source_subject"]
        if path in {"$.schema_version", "$.rows"}:
            return POLICIES["binding"]
        return POLICIES["audit_source"]
    if artifact_id == "c1_proposal":
        if path.startswith("$.mode_track_scientific_projection_rows"):
            return POLICIES["proposal_fresh"]
        if path.startswith("$.candidate_decision_rows") and path.rsplit(".", 1)[
            -1
        ] in {
            "group_hypothesis_id",
            "sample_identity_id",
            "sample_stem",
            "peak_observation_content_id",
            "decision",
            "reason_code",
        }:
            return POLICIES["proposal_fresh"]
        if path.startswith("$.pending_acceptance_rows") and path.rsplit(".", 1)[
            -1
        ] in {"peak_hypothesis_id", "sample_stem", "quant_value"}:
            return POLICIES["source_subject"]
        if path in {
            "$.schema_version",
            "$.source_artifact_relpath",
            "$.source_artifact_sha256",
            "$.candidate_decision_rows",
            "$.mode_track_scientific_projection_rows",
            "$.pending_acceptance_rows",
        }:
            return POLICIES["binding"]
        return POLICIES["audit_source"]
    if artifact_id == "c1_proposal_receipt":
        return POLICIES["binding"]
    if artifact_id == "timing_payload":
        return POLICIES["audit_timing"]
    if artifact_id == "manual_review":
        if path.rsplit(".", 1)[-1] in {
            "reviewer_id",
            "reviewed_at",
            "evidence_locator",
            "notes",
            "verdict",
        }:
            return POLICIES["human_primitive"]
        return POLICIES["review_binding"]
    if artifact_id == "physical_replay":
        if path.startswith("$.rows[].observation_content"):
            return POLICIES["observation"]
        if path in {"$.discovery_index_sha256", "$.raw_open_count"}:
            return POLICIES["audit_receipt"]
        return POLICIES["review_binding"]
    if artifact_id == "review_transfer_receipt":
        if path.rsplit(".", 1)[-1].startswith("reviewed_"):
            return POLICIES["audit_receipt"]
        return POLICIES["transfer_binding"]
    if artifact_id == "review_checked_manifest":
        return POLICIES["audit_manifest"]
    if artifact_id == "fresh_mode_track_source":
        return POLICIES["fresh_output"]
    if artifact_id == "fresh_owner_receipt":
        return POLICIES["fresh_receipt"]
    if artifact_id == "terminal_checked_manifest":
        return POLICIES["terminal_manifest"]
    if artifact_id == "result_qmv":
        return POLICIES["qmv"]
    if artifact_id in {"final_receipt", "transaction_receipt"}:
        return POLICIES["terminal"]
    raise ValueError(f"no field policy for {artifact_id} {path} {semantic_claim}")


def _read_contract(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != CONTRACT_COLUMNS:
            raise ValueError("header must exactly match contract columns")
        return list(reader)


def _validate_rows(rows: Sequence[Mapping[str, str]], *, problems: list[str]) -> None:
    claim_ids = [row["claim_id"] for row in rows]
    duplicates = sorted(
        claim_id
        for claim_id, count in Counter(claim_ids).items()
        if claim_id and count > 1
    )
    if duplicates:
        problems.append("duplicate claim ids: " + ", ".join(duplicates[:5]))
    observed_artifacts = {row["artifact_id"] for row in rows}
    expected_artifacts = {spec.artifact_id for spec in ARTIFACT_SPECS}
    if observed_artifacts != expected_artifacts:
        problems.append("artifact universe differs from the frozen specification")
    for index, row in enumerate(rows, start=2):
        for column in CONTRACT_COLUMNS:
            if not row[column].strip():
                problems.append(f"row {index}: {column} is blank")
        if row["semantic_class"] not in ALLOWED_SEMANTIC_CLASSES:
            problems.append(
                f"row {index}: unsupported semantic_class {row['semantic_class']!r}"
            )
        if row["resolution"] not in ALLOWED_RESOLUTIONS:
            problems.append(
                f"row {index}: unsupported resolution {row['resolution']!r}"
            )
        if row["resolution"] == "unknown":
            problems.append(f"row {index}: unresolved claim {row['claim_id']}")
        for column in (
            "enumeration_source",
            "consumer",
            "semantic_owner",
            "verification_source",
            "contradiction_oracle",
        ):
            _check_repo_references(
                row[column],
                label=column,
                row_index=index,
                problems=problems,
                executable=column == "contradiction_oracle",
            )


def _check_repo_references(
    value: str,
    *,
    label: str,
    row_index: int,
    problems: list[str],
    executable: bool,
) -> None:
    for reference in _split_refs(value):
        path = _repo_reference_path(reference)
        if path is None or not path.is_file():
            problems.append(f"row {row_index}: {label} does not resolve: {reference}")
            continue
        if executable:
            if "::" not in reference or not _python_symbol_resolves(
                path, reference.split("::", 1)[1]
            ):
                problems.append(
                    f"row {row_index}: executable oracle does not resolve: {reference}"
                )
        elif "::" in reference and not _python_symbol_resolves(
            path, reference.split("::", 1)[1]
        ):
            problems.append(
                f"row {row_index}: {label} anchor does not resolve: {reference}"
            )
        elif "#" in reference and not _markdown_heading_resolves(
            path, reference.split("#", 1)[1]
        ):
            problems.append(
                f"row {row_index}: {label} anchor does not resolve: {reference}"
            )


def _split_refs(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _repo_reference_path(reference: str) -> Path | None:
    path_text = reference.split("::", 1)[0].split("#", 1)[0].strip()
    relative = Path(path_text)
    if (
        not path_text
        or relative.is_absolute()
        or relative.drive
        or ".." in relative.parts
    ):
        return None
    resolved = (ROOT / relative).resolve()
    return resolved if resolved.is_relative_to(ROOT.resolve()) else None


@cache
def _python_symbol_resolves(path: Path, symbol: str) -> bool:
    if path.suffix != ".py" or not symbol:
        return False
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == symbol
        for node in module.body
    )


@cache
def _markdown_heading_resolves(path: Path, heading: str) -> bool:
    if path.suffix.lower() != ".md" or not heading:
        return False
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return bool(
        re.search(
            rf"^#{{1,6}}\s+{re.escape(heading)}\s*#*\s*$",
            source,
            flags=re.MULTILINE,
        )
    )


def contract_summary(rows: Iterable[Mapping[str, str]]) -> dict[str, int]:
    counts = Counter(row["resolution"] for row in rows)
    return {key: counts.get(key, 0) for key in sorted(ALLOWED_RESOLUTIONS)}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate the retained TSV before checking it",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.write:
        write_gate_c_persisted_boundary_contract(contract_path=args.contract)
    problems = check_gate_c_persisted_boundary_contract(contract_path=args.contract)
    if problems:
        print("Gate C persisted-boundary contract failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    rows = _read_contract(args.contract)
    summary = contract_summary(rows)
    print(
        "Gate C persisted-boundary contract is valid: "
        f"claims={len(rows)}, fixed={summary['fixed']}, "
        f"verified_unaffected={summary['verified_unaffected']}, "
        f"unknown={summary['unknown']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
