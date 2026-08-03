from __future__ import annotations

import csv
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import pytest

import xic_extractor.processing_identity as processing_identity
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_diagnostic_method import _method_request
from xic_extractor.extraction.targeted_diagnostic_application import (
    build_canonical_targeted_application_service,
    targeted_diagnostic_request_provider,
)
from xic_extractor.extraction.targeted_diagnostic_delivery import (
    rollback_targeted_diagnostic_delivery_candidate,
)
from xic_extractor.extraction.targeted_service import (
    build_targeted_application_request,
)
from xic_extractor.package0_successor_comparator import (
    Package0ComparatorError,
    _validate_m5_authority_audit,
    validate_m5_rollback_rehearsal,
    validate_m5_successor_verdict_packet,
)

PACKET = Path("docs/superpowers/validation/m5_successor_verdict_v1")
FROZEN_CANDIDATE_SHA = "fbe502bd9d38f39c65a450a9f760ad76664ec3cd"


def test_m5_packet_binds_frozen_candidate_legacy_rollback_and_narrow_verdict() -> None:
    receipt = validate_m5_successor_verdict_packet(
        PACKET,
        repo_root=Path("."),
        expected_candidate_sha=FROZEN_CANDIDATE_SHA,
    )

    assert receipt.candidate_merge_sha == FROZEN_CANDIDATE_SHA
    assert receipt.verdict == "NARROW"
    assert receipt.evaluated_scope == "cid_nl_successor_shadow_substrate"
    assert receipt.validation_status == "diagnostic_only"
    assert receipt.rollback_rehearsal_status == "pass"
    assert receipt.product_authority_granted is False
    assert receipt.external_legacy_replay_performed is False
    assert len(receipt.packet_sha256) == 64


def test_m5_packet_rejects_stale_candidate_sha() -> None:
    with pytest.raises(Package0ComparatorError, match="stale frozen candidate"):
        validate_m5_successor_verdict_packet(
            PACKET,
            repo_root=Path("."),
            expected_candidate_sha="0" * 40,
        )


def test_m5_packet_rejects_missing_or_tampered_artifact(tmp_path: Path) -> None:
    missing = _copy_packet(tmp_path / "missing")
    (missing / "selective_scientific_cases.tsv").unlink()
    with pytest.raises(Package0ComparatorError, match="artifact inventory"):
        validate_m5_successor_verdict_packet(
            missing,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )

    tampered = _copy_packet(tmp_path / "tampered")
    with (tampered / "README.md").open("a", encoding="utf-8") as handle:
        handle.write("tampered\n")
    with pytest.raises(Package0ComparatorError, match="artifact digest"):
        validate_m5_successor_verdict_packet(
            tampered,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


@pytest.mark.parametrize(
    "escaped_path",
    (
        "../outside.json",
        "..\\outside.json",
        "\\outside.json",
        "C:/outside.json",
        "\\\\host\\share",
    ),
)
def test_m5_packet_rejects_path_escape(
    tmp_path: Path,
    escaped_path: str,
) -> None:
    packet = _copy_packet(tmp_path / "path-escape")
    index_path = packet / "artifact_index.tsv"
    rows = _read_tsv(index_path)
    rows[0]["path"] = escaped_path
    _write_tsv(index_path, rows)

    with pytest.raises(Package0ComparatorError, match="safe relative paths"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_drifted_external_evidence_seal(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "evidence-drift")
    evidence_path = packet / "evidence_manifest.tsv"
    rows = _read_tsv(evidence_path)
    rows[0]["sha256"] = "0" * 64
    _write_tsv(evidence_path, rows)
    _refresh_index(packet, "evidence_manifest.tsv")

    with pytest.raises(Package0ComparatorError, match="evidence file drift"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_unsealed_evidence_locator(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "unsealed-locator")
    diff_path = packet / "public_expected_diff.tsv"
    rows = _read_tsv(diff_path)
    rows[0]["evidence_locator"] = "README.md"
    _write_tsv(diff_path, rows)
    _refresh_index(packet, "public_expected_diff.tsv")

    with pytest.raises(Package0ComparatorError, match="locator set differs"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_narrow_verdict_without_exact_excluded_scope(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path / "missing-narrow-exclusion")
    verdict_path = packet / "verdict.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["excluded_scope"].remove("legacy retirement")
    verdict_path.write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "verdict.json")

    with pytest.raises(Package0ComparatorError, match="excluded scope differs"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_changed_preregistered_selective_case(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path / "changed-selective-contract")
    cases_path = packet / "selective_scientific_cases.tsv"
    rows = _read_tsv(cases_path)
    rows[0]["question"] = "Easier post-result question?"
    _write_tsv(cases_path, rows)
    _refresh_index(packet, "selective_scientific_cases.tsv")

    with pytest.raises(Package0ComparatorError, match="preregistered selective"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_selective_failure_can_narrow_without_invalidating_packet(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path / "selective-narrow")
    cases_path = packet / "selective_scientific_cases.tsv"
    rows = _read_tsv(cases_path)
    union_case = next(row for row in rows if row["case_id"] == "union_tag_provenance")
    union_case["observed"] = "shared_wrong_receipt"
    union_case["status"] = "fail"
    _write_tsv(cases_path, rows)
    _refresh_index(packet, "selective_scientific_cases.tsv")

    receipt = validate_m5_successor_verdict_packet(
        packet,
        repo_root=Path("."),
        expected_candidate_sha=FROZEN_CANDIDATE_SHA,
    )

    assert receipt.verdict == "NARROW"


def test_m5_selective_core_failure_can_kill_candidate(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "selective-kill")
    cases_path = packet / "selective_scientific_cases.tsv"
    rows = _read_tsv(cases_path)
    split_case = next(row for row in rows if row["case_id"] == "separated_peaks")
    split_case["observed"] = "collapsed"
    split_case["status"] = "fail"
    _write_tsv(cases_path, rows)
    verdict_path = packet / "verdict.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["verdict"] = "KILL"
    verdict_path.write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "selective_scientific_cases.tsv")
    _refresh_index(packet, "verdict.json")

    receipt = validate_m5_successor_verdict_packet(
        packet,
        repo_root=Path("."),
        expected_candidate_sha=FROZEN_CANDIDATE_SHA,
    )

    assert receipt.verdict == "KILL"


def test_m5_packet_rejects_go_when_package4_gates_are_incomplete(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path / "forced-go")
    verdict_path = packet / "verdict.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["verdict"] = "GO"
    verdict_path.write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "verdict.json")

    with pytest.raises(Package0ComparatorError, match="GO is unreachable"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_coordinated_go_rewrite(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path / "coordinated-go")
    verdict_path = packet / "verdict.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict["verdict"] = "GO"
    verdict["gate_status"]["canonical_package4_complete"] = True
    verdict["missing_evidence"] = []
    verdict_path.write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    dimension_path = packet / "comparison_dimensions.tsv"
    dimensions = _read_tsv(dimension_path)
    for row in dimensions:
        row["outcome"] = "pass"
    _write_tsv(dimension_path, dimensions)
    _refresh_index(packet, "verdict.json")
    _refresh_index(packet, "comparison_dimensions.tsv")

    with pytest.raises(Package0ComparatorError, match="GO is unreachable"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_requires_exact_candidate_source_set(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "missing-source")
    manifest_path = packet / "candidate_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_files"].pop()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "candidate_manifest.json")

    with pytest.raises(Package0ComparatorError, match="candidate source set differs"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_candidate_git_tree_drift(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "candidate-tree-drift")
    manifest_path = packet / "candidate_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidate_tree_sha"] = "0" * 40
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "candidate_manifest.json")

    with pytest.raises(Package0ComparatorError, match="candidate Git tree differs"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_requires_exact_legacy_tracked_set(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "missing-legacy-pointer")
    receipt_path = packet / "legacy_baseline_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["tracked_files"].pop()
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "legacy_baseline_receipt.json")

    with pytest.raises(
        Package0ComparatorError,
        match="legacy tracked file set differs",
    ):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_candidate_source_drift(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "source-drift")
    manifest_path = packet / "candidate_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "candidate_manifest.json")

    with pytest.raises(Package0ComparatorError, match="candidate source drift"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_unapproved_public_diff(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "public-diff")
    diff_path = packet / "public_expected_diff.tsv"
    rows = _read_tsv(diff_path)
    rows[0]["observed_diff"] = "changed"
    rows[0]["status"] = "fail"
    _write_tsv(diff_path, rows)
    _refresh_index(packet, "public_expected_diff.tsv")

    with pytest.raises(Package0ComparatorError, match="public hard-zero"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_packet_rejects_authority_audit_count_drift(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "authority-audit-drift")
    audit_path = packet / "architecture_authority_audit.tsv"
    rows = _read_tsv(audit_path)
    rows[-1]["observed_count"] = "2"
    _write_tsv(audit_path, rows)
    _refresh_index(packet, "architecture_authority_audit.tsv")

    with pytest.raises(Package0ComparatorError, match="authority audit failed"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


@pytest.mark.parametrize(
    ("owner_module", "return_type"),
    (
        (
            "xic_extractor.alignment.cell_observations",
            "CellObservationRef",
        ),
        (
            "xic_extractor.peak_detection.cid_nl_candidate_set_relation",
            "DdaCidNlCandidateSetRelationRef",
        ),
        (
            "xic_extractor.peak_detection.cid_nl_method_assessment",
            "CidNlMethodAssessmentRef",
        ),
        (
            "xic_extractor.alignment.identity_settlement",
            "IdentitySettlementDecision",
        ),
        (
            "xic_extractor.extraction.targeted_diagnostic_delivery",
            "TargetedDiagnosticDeliveryCandidate",
        ),
    ),
)
def test_m5_authority_census_rejects_private_aliased_constructor_owner(
    tmp_path: Path,
    owner_module: str,
    return_type: str,
) -> None:
    audit_rows = _copy_m5_authority_sources(tmp_path)
    duplicate = tmp_path / "xic_extractor" / "duplicate_delivery_owner.py"
    duplicate.write_text(
        f"from {owner_module} import (\n"
        f"    {return_type} as Candidate,\n"
        ")\n\n"
        "def _settle_shadow() -> Candidate:\n"
        "    return Candidate()\n",
        encoding="utf-8",
    )

    with pytest.raises(Package0ComparatorError, match="executable census differs"):
        _validate_m5_authority_audit(audit_rows, repo_root=tmp_path)


@pytest.mark.parametrize(
    ("replace_import", "replace_call"),
    (
        (
            "from dataclasses import replace as transition\n",
            'transition(candidate, status="rolled_back")',
        ),
        (
            "import dataclasses as dc\n",
            'dc.replace(candidate, status="rolled_back")',
        ),
        (
            "from dataclasses import replace\ntransition = replace\n",
            'transition(candidate, status="rolled_back")',
        ),
        (
            "import dataclasses as dc\ntransition = dc.replace\n",
            'transition(candidate, status="rolled_back")',
        ),
    ),
)
def test_m5_authority_census_rejects_private_typed_replace_owner(
    tmp_path: Path,
    replace_import: str,
    replace_call: str,
) -> None:
    audit_rows = _copy_m5_authority_sources(tmp_path)
    duplicate = tmp_path / "xic_extractor" / "duplicate_delivery_owner.py"
    duplicate.write_text(
        f"{replace_import}"
        "from xic_extractor.extraction.targeted_diagnostic_delivery import (\n"
        "    TargetedDiagnosticDeliveryCandidate as Candidate,\n"
        ")\n\n"
        "def _rollback_shadow(candidate: Candidate) -> Candidate:\n"
        f"    return {replace_call}\n",
        encoding="utf-8",
    )

    with pytest.raises(Package0ComparatorError, match="executable census differs"):
        _validate_m5_authority_audit(audit_rows, repo_root=tmp_path)


def test_m5_authority_census_ignores_private_typed_accessor(tmp_path: Path) -> None:
    audit_rows = _copy_m5_authority_sources(tmp_path)
    accessor = tmp_path / "xic_extractor" / "cell_observation_accessor.py"
    accessor.write_text(
        "from xic_extractor.alignment.cell_observations import (\n"
        "    CellObservationRef as CellRef,\n"
        ")\n\n"
        "def _target_cell(existing: CellRef) -> CellRef:\n"
        "    return existing\n",
        encoding="utf-8",
    )

    _validate_m5_authority_audit(audit_rows, repo_root=tmp_path)


def test_m5_packet_rejects_recorded_rollback_receipt_drift(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "rollback-receipt-drift")
    rollback_path = packet / "rollback_rehearsal.json"
    rollback = json.loads(rollback_path.read_text(encoding="utf-8"))
    rollback["recorded_owner_receipt"]["delivery_stage_id"] = "wrong-stage"
    rollback_path.write_text(
        json.dumps(rollback, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "rollback_rehearsal.json")

    with pytest.raises(Package0ComparatorError, match="receipt identity is invalid"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


def test_m5_rehearses_whole_attempt_rollback_without_mixed_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        processing_identity.uuid,
        "uuid4",
        lambda: uuid.UUID("00000000-0000-0000-0000-000000000165"),
    )
    request, _fixture = _method_request(tmp_path)

    with targeted_diagnostic_request_provider(
        lambda _application_request, _actual_output: request,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    ):
        execution = build_canonical_targeted_application_service(
            lambda _config, _targets, **_kwargs: request.authoritative_legacy_output
        ).execute_application(
            build_targeted_application_request(
                request.peak_config,
                list(request.targets),
            )
        )

    outcome = execution.diagnostic_outcome
    assert outcome is not None
    assert outcome.delivery_candidate is not None
    staged = outcome.delivery_candidate
    rolled_back = rollback_targeted_diagnostic_delivery_candidate(staged)

    receipt = validate_m5_rollback_rehearsal(staged, rolled_back)
    packet_rollback = json.loads(
        (PACKET / "rollback_rehearsal.json").read_text(encoding="utf-8")
    )
    recorded = packet_rollback["recorded_owner_receipt"]

    assert receipt.status_transition == "staged_candidate->rolled_back"
    assert receipt.prior_current_delivery_id == request.prior_current_delivery_id
    assert receipt.active_authority == "authoritative_legacy_current"
    assert receipt.successor_product_authority is False
    assert receipt.lineage_artifact_ids == tuple(
        artifact.evidence_artifact_id for artifact in staged.lineage_artifacts
    )
    assert len(receipt.receipt_sha256) == 64
    assert len(receipt.lineage_artifact_ids) == recorded["lineage_artifact_count"]
    assert len(receipt.delivery_revision_ids) == recorded["delivery_revision_count"]
    assert receipt.processing_attempt_id == recorded["processing_attempt_id"]
    assert receipt.prior_current_delivery_id == recorded["prior_current_delivery_id"]
    assert _sequence_sha256(receipt.lineage_artifact_ids) == recorded[
        "lineage_artifact_ids_sha256"
    ]
    assert _sequence_sha256(receipt.delivery_revision_ids) == recorded[
        "delivery_revision_ids_sha256"
    ]
    assert receipt.delivery_stage_id == recorded["delivery_stage_id"]
    assert receipt.delivery_seal_candidate_id == recorded[
        "delivery_seal_candidate_id"
    ]
    assert receipt.receipt_sha256 == recorded["owner_receipt_sha256"]


def _sequence_sha256(values: tuple[str, ...]) -> str:
    payload = json.dumps(
        list(values),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def _copy_packet(destination: Path) -> Path:
    shutil.copytree(PACKET, destination)
    return destination


def _copy_m5_authority_sources(
    destination_root: Path,
) -> tuple[dict[str, str], ...]:
    audit_rows = tuple(
        _read_tsv(PACKET / "architecture_authority_audit.tsv")
    )
    for row in audit_rows:
        source = Path(row["evidence_locator"].partition("::")[0])
        destination = destination_root / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return audit_rows


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    assert rows
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=tuple(rows[0]),
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _refresh_index(packet: Path, changed_path: str) -> None:
    index_path = packet / "artifact_index.tsv"
    rows = _read_tsv(index_path)
    payload = (packet / changed_path).read_bytes()
    for row in rows:
        if row["path"] == changed_path:
            row["sha256"] = hashlib.sha256(payload).hexdigest().upper()
            row["size_bytes"] = str(len(payload))
            break
    else:
        raise AssertionError(f"index row not found: {changed_path}")
    _write_tsv(index_path, rows)
