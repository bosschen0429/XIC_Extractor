from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
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
    M5SuccessorVerdictReceipt,
    Package0ComparatorError,
    validate_m5_rollback_rehearsal,
    validate_m5_successor_verdict_packet,
)

PACKET = Path("docs/superpowers/validation/m5_successor_verdict_v1")
FROZEN_CANDIDATE_SHA = "fbe502bd9d38f39c65a450a9f760ad76664ec3cd"


def test_m5_sealed_text_files_have_checkout_stable_line_endings() -> None:
    candidate = json.loads((PACKET / "candidate_manifest.json").read_text("utf-8"))
    legacy = json.loads((PACKET / "legacy_baseline_receipt.json").read_text("utf-8"))
    sealed_paths = {
        *(path.as_posix() for path in PACKET.iterdir() if path.is_file()),
        *(row["path"] for row in candidate["source_files"]),
        *(row["path"] for row in legacy["tracked_files"]),
        *(row["path"] for row in _read_tsv(PACKET / "evidence_manifest.tsv")),
    }
    completed = subprocess.run(
        ["git", "check-attr", "-z", "text", "eol", "--", *sorted(sealed_paths)],
        check=True,
        capture_output=True,
    )
    fields = completed.stdout.decode("utf-8").split("\0")
    attributes: dict[str, dict[str, str]] = {}
    for offset in range(0, len(fields) - 1, 3):
        path, attribute, value = fields[offset : offset + 3]
        attributes.setdefault(path, {})[attribute] = value

    unstable = {
        path: attributes.get(path, {})
        for path in sorted(sealed_paths)
        if attributes.get(path) != {"text": "set", "eol": "lf"}
    }
    assert unstable == {}


def test_m5_packet_accepts_platform_line_endings_without_weakening_tamper_gate(
    tmp_path: Path,
) -> None:
    packet = _copy_packet(tmp_path / "crlf-checkout")
    manifest_path = packet / "candidate_manifest.json"
    manifest_path.write_bytes(
        _canonical_text_bytes(manifest_path.read_bytes()).replace(b"\n", b"\r\n")
    )

    receipt = validate_m5_successor_verdict_packet(
        packet,
        repo_root=Path("."),
        expected_candidate_sha=FROZEN_CANDIDATE_SHA,
    )

    assert receipt.verdict == "NARROW"


def test_m5_packet_rejects_non_utf8_sealed_text(tmp_path: Path) -> None:
    packet = _copy_packet(tmp_path / "non-utf8-packet")
    (packet / "candidate_manifest.json").write_bytes(b"\xff")

    with pytest.raises(Package0ComparatorError, match="must be valid UTF-8 text"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


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


def test_m5_verdict_receipt_requires_comparator_minting() -> None:
    with pytest.raises(Package0ComparatorError, match="must be minted by"):
        M5SuccessorVerdictReceipt(
            candidate_merge_sha=FROZEN_CANDIDATE_SHA,
            verdict="GO",
            evaluated_scope="forged",
            validation_status="production_ready",
            rollback_rehearsal_status="pass",
            product_authority_granted=True,
            external_legacy_replay_performed=False,
            packet_sha256="0" * 64,
            _mint_token=object(),
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("go_requirements", [], "contract semantics differ"),
        (
            "go_requirements",
            ["rewritten promotion contract"],
            "contract semantics differ",
        ),
        ("verdict_states", ["NARROW", "GO", "KILL"], "contract semantics differ"),
        (
            "rules",
            {"missing_evidence_never_promotes": True},
            "contract rules differ",
        ),
    ),
)
def test_m5_packet_rejects_verdict_contract_semantic_drift(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    packet = _copy_packet(tmp_path / f"verdict-contract-{field}")
    contract_path = packet / "verdict_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract[field] = replacement
    contract_path.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "verdict_contract.json")

    with pytest.raises(Package0ComparatorError, match=message):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


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


@pytest.mark.parametrize(
    ("field", "replacement", "error"),
    (
        ("candidate_defects", ["invented defect"], "candidate defect set differs"),
        ("missing_evidence", [], "missing-evidence set differs"),
        ("retained_scope", ["rewritten scope"], "retained scope differs"),
        (
            "known_preexisting_followups",
            ["Issue #999 invented follow-up"],
            "known follow-up set differs",
        ),
        ("gate_status", {"bounded_substrate_pass": True}, "gate status differs"),
    ),
)
def test_m5_packet_rejects_rewritten_verdict_semantic_boundary(
    tmp_path: Path,
    field: str,
    replacement: object,
    error: str,
) -> None:
    packet = _copy_packet(tmp_path / f"rewritten-{field}")
    verdict_path = packet / "verdict.json"
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    verdict[field] = replacement
    verdict_path.write_text(
        json.dumps(verdict, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "verdict.json")

    with pytest.raises(Package0ComparatorError, match=error):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


@pytest.mark.parametrize(
    ("field", "replacement", "error"),
    (
        ("excluded_milestones", [], "excluded milestone set differs"),
        (
            "known_preexisting_followups",
            [{"issue": 999, "scope": "invented follow-up"}],
            "known follow-up set differs",
        ),
    ),
)
def test_m5_packet_rejects_rewritten_candidate_scope_boundary(
    tmp_path: Path,
    field: str,
    replacement: object,
    error: str,
) -> None:
    packet = _copy_packet(tmp_path / f"rewritten-candidate-{field}")
    manifest_path = packet / "candidate_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = replacement
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _refresh_index(packet, "candidate_manifest.json")

    with pytest.raises(Package0ComparatorError, match=error):
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

    with pytest.raises(Package0ComparatorError, match="missing-evidence set differs"):
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


@pytest.mark.parametrize(
    ("row_index", "field", "replacement"),
    (
        (0, "schema_version", "m5_successor_verdict_packet_v2"),
        (0, "audit_id", "renamed_dependency_gate"),
        (0, "audit_kind", "duplicate_authority"),
        (0, "responsibility_or_boundary", "rewritten boundary"),
        (0, "expected_count", "1"),
        (0, "observed_count", "1"),
        (0, "status", "unknown"),
        (0, "evidence_maturity", "asserted_only"),
        (0, "evidence_locator", "tests/test_m5_successor_verdict.py"),
        (0, "claim_limit", "unbounded"),
        (2, "responsibility_or_boundary", "rewritten owner"),
        (2, "observed_count", "2"),
        (2, "evidence_locator", "xic_extractor/output/csv_writers.py"),
        (2, "claim_limit", "repo-wide authority proof"),
    ),
)
def test_m5_packet_rejects_authority_audit_semantic_drift(
    tmp_path: Path,
    row_index: int,
    field: str,
    replacement: str,
) -> None:
    packet = _copy_packet(tmp_path / f"authority-audit-{row_index}-{field}")
    audit_path = packet / "architecture_authority_audit.tsv"
    rows = _read_tsv(audit_path)
    rows[row_index][field] = replacement
    _write_tsv(audit_path, rows)
    _refresh_index(packet, "architecture_authority_audit.tsv")

    with pytest.raises(Package0ComparatorError, match="authority audit set differs"):
        validate_m5_successor_verdict_packet(
            packet,
            repo_root=Path("."),
            expected_candidate_sha=FROZEN_CANDIDATE_SHA,
        )


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
    payload = _canonical_text_bytes((packet / changed_path).read_bytes())
    for row in rows:
        if row["path"] == changed_path:
            row["sha256"] = hashlib.sha256(payload).hexdigest().upper()
            row["size_bytes"] = str(len(payload))
            break
    else:
        raise AssertionError(f"index row not found: {changed_path}")
    _write_tsv(index_path, rows)


def _canonical_text_bytes(payload: bytes) -> bytes:
    text = payload.decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
