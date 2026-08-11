from __future__ import annotations

import csv
import hashlib
import re
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKET_DIR = (
    REPO_ROOT
    / "docs"
    / "superpowers"
    / "validation"
    / "standard_peak_shared_evidence_t0_v1"
)
FROZEN_GIT_VERSION = "git:db3fa5e6c1d2756afe1c600b57a81ef17aef2443"

PACKET_FILES = {
    "README.md",
    "rule_disposition_v1.tsv",
    "historical_failure_matrix_v1.tsv",
    "call_cost_baseline_v1.tsv",
}
RULE_COLUMNS = (
    "rule_id",
    "source_path",
    "source_symbol_or_stage",
    "current_callers",
    "named_question",
    "semantic_layer",
    "input_owner",
    "input_identity",
    "candidate_or_row_population",
    "threshold_or_policy_version",
    "current_output_effect",
    "value_or_authority_changing",
    "current_decision_owner",
    "current_authority_owner",
    "proposed_disposition",
    "proposed_owner",
    "retained_oracle",
    "positive_case",
    "counterexample",
    "failure_behavior",
    "rollback_or_non_adoption",
    "status",
    "final_successor_owner",
    "implementation_child",
    "final_runtime_disposition",
    "downstream_input",
    "parity_or_expected_diff",
    "deletion_gate",
)
FAILURE_COLUMNS = (
    "failure_class_id",
    "source_issue_pr_doc_test",
    "old_behavior",
    "observable_loss",
    "root_layer",
    "replacement_risk",
    "new_characterization",
    "positive_case",
    "counterexample",
    "acceptance_oracle",
    "rollback_or_stop",
    "status",
)
CALL_COST_COLUMNS = (
    "scope_id",
    "entrypoint",
    "stage",
    "unit",
    "raw_open_count",
    "vendor_or_acquisition_call_count",
    "trace_evaluation_count",
    "baseline_or_smoothing_count",
    "integration_count",
    "artifact_read_count",
    "artifact_write_count",
    "renderer_count",
    "count_owner",
    "evidence_status",
    "source_locator",
    "source_version_or_hash",
    "notes",
)
COUNT_COLUMNS = (
    "raw_open_count",
    "vendor_or_acquisition_call_count",
    "trace_evaluation_count",
    "baseline_or_smoothing_count",
    "integration_count",
    "artifact_read_count",
    "artifact_write_count",
    "renderer_count",
)

DISPOSITIONS = {
    "shared_peak_fact",
    "stage_policy",
    "review_orchestration",
    "authority",
    "artifact_finalization",
    "compatibility_adapter",
    "oracle_or_falsification",
    "renderer_only",
    "retire",
    "unresolved",
}
EVIDENCE_STATUSES = {
    "observed_retained",
    "owner_derived",
    "static_call_path",
    "unknown",
}
TERMINAL_DISPOSITIONS = {
    "PROMOTE_TO_T1",
    "NARROW",
    "KEEP_AS_BOUNDED_ORACLE",
}
FINAL_RUNTIME_DISPOSITIONS = {
    "live_shared_fact",
    "live_stage_policy",
    "live_authority_adapter",
    "live_compatibility",
    "offline_oracle",
    "renderer_only",
    "retire",
}
PARITY_OR_EXPECTED_DIFF = {
    "exact_parity",
    "approved_expected_diff_only",
    "offline_oracle_only",
    "renderer_no_semantic_diff",
    "retire_after_no_live_callers",
}
IMPLEMENTATION_CHILDREN = {"#240", "#241", "#243", "#247", "#250", "#251"}
LIVE_RUNTIME_DISPOSITIONS = {
    "live_shared_fact",
    "live_stage_policy",
    "live_authority_adapter",
    "live_compatibility",
}
ROUTE_CUTOVER_STATUS = "BLOCKED_PENDING_250_251"
EXPECTED_RULE_IDS = {f"SP-{number:03d}" for number in range(1, 56)}
EXPECTED_FAILURE_IDS = {
    *(f"HF-{number:03d}" for number in range(1, 18)),
    "FC-A",
    "FC-B",
    "FC-C",
    "FC-D",
    "FC-E",
    "FC-G",
}
EXPECTED_CALL_COST_IDS = {f"CC-{number:03d}" for number in range(1, 22)}
REQUIRED_HISTORICAL_FAILURES = {
    "HF-001",  # local no-RAW gain versus complete 8RAW target
    "HF-002",  # diagnostic evidence versus writer authority
    "HF-003",  # published hashes and actual write-count re-derivation
    "HF-004",  # bounded 511 versus broad 4,613 population
    "HF-005",  # held-out boundary failure
    "HF-006",  # missing overlay evidence
    "HF-007",  # legacy/mechanism oracle versus independent truth
    "HF-008",  # oversized migration and duplicate owner
    "HF-014",  # non-causal dependency chain
    "HF-015",  # missing successor owner and deletion gate
    "HF-016",  # raw compatibility versus Gaussian quantitation
    "HF-017",  # bound recipe reload/rebind regression
    "FC-A",
    "FC-B",
    "FC-C",
    "FC-D",
    "FC-E",
    "FC-G",
}
EXPECTED_SHARED_IDENTITIES = {
    "SP-011": (
        "sample_stem|feature_family_id|canonical_rt_min[]|canonical_intensity[]"
    ),
    "SP-012": (
        "sample_stem|feature_family_id|peak_bounds|canonical_rt_min[]|"
        "canonical_intensity[]"
    ),
    "SP-013": ("sample_stem|feature_family_id|local_window|own_max_normalized_trace"),
    "SP-014": (
        "sample_stem|feature_family_id|local_window|global_window|competing_peak_set"
    ),
}
EXPECTED_DISTINCT_STATES = {
    "SP-018": "technical_unavailable",
    "SP-006": "hold_review",
    "SP-034": "reject_named_question",
    "SP-028": "authority_denied",
}


def test_packet_is_closed_complete_and_non_authorizing() -> None:
    terminal, rules, failures, call_costs = _validate_packet(PACKET_DIR)

    assert terminal == "PROMOTE_TO_T1"
    assert _read_route_cutover(PACKET_DIR / "README.md") == ROUTE_CUTOVER_STATUS
    assert {row["rule_id"] for row in rules} == EXPECTED_RULE_IDS
    assert {row["failure_class_id"] for row in failures} == EXPECTED_FAILURE_IDS
    assert {row["scope_id"] for row in call_costs} == EXPECTED_CALL_COST_IDS
    assert REQUIRED_HISTORICAL_FAILURES <= {row["failure_class_id"] for row in failures}


def test_duplicate_crossed_partial_and_schema_mutations_fail_closed(
    tmp_path: Path,
) -> None:
    duplicate = _copy_packet(tmp_path / "duplicate")
    rows = _read_tsv(duplicate / "rule_disposition_v1.tsv", RULE_COLUMNS)
    rows[1]["rule_id"] = rows[0]["rule_id"]
    _write_tsv(duplicate / "rule_disposition_v1.tsv", rows, RULE_COLUMNS)
    with pytest.raises(ValueError, match="duplicate rule_id"):
        _validate_packet(duplicate)

    crossed = _copy_packet(tmp_path / "crossed")
    rows = _read_tsv(crossed / "rule_disposition_v1.tsv", RULE_COLUMNS)
    by_id = {row["rule_id"]: row for row in rows}
    by_id["SP-011"]["input_identity"], by_id["SP-012"]["input_identity"] = (
        by_id["SP-012"]["input_identity"],
        by_id["SP-011"]["input_identity"],
    )
    _write_tsv(crossed / "rule_disposition_v1.tsv", rows, RULE_COLUMNS)
    with pytest.raises(ValueError, match="crossed identity"):
        _validate_packet(crossed)

    partial = _copy_packet(tmp_path / "partial")
    rows = _read_tsv(partial / "rule_disposition_v1.tsv", RULE_COLUMNS)
    rows = [row for row in rows if row["rule_id"] != "SP-055"]
    _write_tsv(partial / "rule_disposition_v1.tsv", rows, RULE_COLUMNS)
    with pytest.raises(ValueError, match="partial rule population"):
        _validate_packet(partial)

    missing = _copy_packet(tmp_path / "missing")
    rows = _read_tsv(missing / "rule_disposition_v1.tsv", RULE_COLUMNS)
    missing_columns = RULE_COLUMNS[:-1]
    _write_tsv(
        missing / "rule_disposition_v1.tsv",
        [{key: row[key] for key in missing_columns} for row in rows],
        missing_columns,
    )
    with pytest.raises(ValueError, match="closed schema"):
        _validate_packet(missing)

    extra = _copy_packet(tmp_path / "extra")
    rows = _read_tsv(extra / "rule_disposition_v1.tsv", RULE_COLUMNS)
    extra_columns = (*RULE_COLUMNS, "undeclared_semantic_field")
    for row in rows:
        row["undeclared_semantic_field"] = "must fail"
    _write_tsv(extra / "rule_disposition_v1.tsv", rows, extra_columns)
    with pytest.raises(ValueError, match="closed schema"):
        _validate_packet(extra)

    dual_child = _copy_packet(tmp_path / "dual-child")
    rows = _read_tsv(dual_child / "rule_disposition_v1.tsv", RULE_COLUMNS)
    next(row for row in rows if row["rule_id"] == "SP-011")["implementation_child"] = (
        "#240;#251"
    )
    _write_tsv(dual_child / "rule_disposition_v1.tsv", rows, RULE_COLUMNS)
    with pytest.raises(ValueError, match="invalid implementation child"):
        _validate_packet(dual_child)

    diagnostic_live_owner = _copy_packet(tmp_path / "diagnostic-live-owner")
    rows = _read_tsv(
        diagnostic_live_owner / "rule_disposition_v1.tsv",
        RULE_COLUMNS,
    )
    next(row for row in rows if row["rule_id"] == "SP-003")["final_successor_owner"] = (
        "tools.diagnostics.retained_backfill_evidence_gate"
    )
    _write_tsv(
        diagnostic_live_owner / "rule_disposition_v1.tsv",
        rows,
        RULE_COLUMNS,
    )
    with pytest.raises(ValueError, match="diagnostic owner remains live"):
        _validate_packet(diagnostic_live_owner)


def test_stale_evidence_unresolved_and_state_collapse_fail_closed(
    tmp_path: Path,
) -> None:
    stale = _copy_packet(tmp_path / "stale")
    rows = _read_tsv(stale / "call_cost_baseline_v1.tsv", CALL_COST_COLUMNS)
    next(row for row in rows if row["scope_id"] == "CC-018")[
        "source_version_or_hash"
    ] = "0" * 64
    _write_tsv(stale / "call_cost_baseline_v1.tsv", rows, CALL_COST_COLUMNS)
    with pytest.raises(ValueError, match="stale source hash"):
        _validate_packet(stale)

    unresolved = _copy_packet(tmp_path / "unresolved")
    rows = _read_tsv(
        unresolved / "rule_disposition_v1.tsv",
        RULE_COLUMNS,
    )
    next(row for row in rows if row["rule_id"] == "SP-011")["proposed_disposition"] = (
        "unresolved"
    )
    _write_tsv(
        unresolved / "rule_disposition_v1.tsv",
        rows,
        RULE_COLUMNS,
    )
    with pytest.raises(ValueError, match="unresolved blocks promotion"):
        _validate_packet(unresolved)

    collapsed = _copy_packet(tmp_path / "collapsed")
    rows = _read_tsv(collapsed / "rule_disposition_v1.tsv", RULE_COLUMNS)
    next(row for row in rows if row["rule_id"] == "SP-018")["status"] = (
        "reject_named_question"
    )
    _write_tsv(collapsed / "rule_disposition_v1.tsv", rows, RULE_COLUMNS)
    with pytest.raises(ValueError, match="state distinction"):
        _validate_packet(collapsed)


def _validate_packet(
    packet_dir: Path,
) -> tuple[
    str,
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    actual_files = {path.name for path in packet_dir.iterdir() if path.is_file()}
    if actual_files != PACKET_FILES:
        raise ValueError(
            f"packet file set is not closed: {sorted(actual_files)}",
        )

    terminal = _read_terminal(packet_dir / "README.md")
    route_cutover = _read_route_cutover(packet_dir / "README.md")
    if route_cutover != ROUTE_CUTOVER_STATUS:
        raise ValueError(f"unexpected route cutover status: {route_cutover}")
    rules = _read_tsv(packet_dir / "rule_disposition_v1.tsv", RULE_COLUMNS)
    failures = _read_tsv(
        packet_dir / "historical_failure_matrix_v1.tsv",
        FAILURE_COLUMNS,
    )
    call_costs = _read_tsv(
        packet_dir / "call_cost_baseline_v1.tsv",
        CALL_COST_COLUMNS,
    )

    _validate_rule_rows(rules, terminal)
    _validate_failure_rows(failures)
    _validate_call_cost_rows(call_costs, terminal)
    return terminal, rules, failures, call_costs


def _read_terminal(readme: Path) -> str:
    matches = re.findall(
        r"^Terminal disposition: ([A-Z0-9_]+)$",
        readme.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if len(matches) != 1 or matches[0] not in TERMINAL_DISPOSITIONS:
        raise ValueError(f"expected exactly one terminal disposition: {matches}")
    return matches[0]


def _read_route_cutover(readme: Path) -> str:
    matches = re.findall(
        r"^Route cutover status: ([A-Z0-9_]+)$",
        readme.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if len(matches) != 1:
        raise ValueError(f"expected exactly one route cutover status: {matches}")
    return matches[0]


def _validate_rule_rows(rows: list[dict[str, str]], terminal: str) -> None:
    rule_ids = [row["rule_id"] for row in rows]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("duplicate rule_id")
    if set(rule_ids) != EXPECTED_RULE_IDS:
        raise ValueError("partial rule population")

    for row in rows:
        if row["proposed_disposition"] not in DISPOSITIONS:
            raise ValueError("unknown proposed disposition")
        if row["value_or_authority_changing"] not in {"TRUE", "FALSE"}:
            raise ValueError("invalid value_or_authority_changing flag")
        if row["value_or_authority_changing"] == "TRUE":
            _require_named_owner(row, "current_decision_owner")
            _require_named_owner(row, "current_authority_owner")
        if row["final_runtime_disposition"] not in FINAL_RUNTIME_DISPOSITIONS:
            raise ValueError("unknown final runtime disposition")
        if row["parity_or_expected_diff"] not in PARITY_OR_EXPECTED_DIFF:
            raise ValueError("unknown parity or expected-diff rule")
        if row["implementation_child"] not in IMPLEMENTATION_CHILDREN:
            raise ValueError("invalid implementation child")
        if row["final_runtime_disposition"] in LIVE_RUNTIME_DISPOSITIONS and row[
            "final_successor_owner"
        ].startswith("tools.diagnostics"):
            raise ValueError("diagnostic owner remains live")
        if row["proposed_disposition"] == "shared_peak_fact":
            if row["proposed_owner"] != "xic_extractor/peak_detection":
                raise ValueError("shared peak fact has wrong owner")
            if row["value_or_authority_changing"] != "FALSE":
                raise ValueError("evidence fact grants value or authority")
            effect = row["current_output_effect"].lower()
            if any(token in effect for token in ("matrix", "write", "authority")):
                raise ValueError("evidence fact grants Matrix/write authority")
            if not all(
                row[field]
                for field in ("retained_oracle", "positive_case", "counterexample")
            ):
                raise ValueError("migrated candidate lacks falsification cases")
            if row["implementation_child"] != "#240":
                raise ValueError("shared peak fact has wrong implementation child")
            if row["final_runtime_disposition"] != "live_shared_fact":
                raise ValueError("shared peak fact has wrong final disposition")

    for rule_id in ("SP-035", "SP-045"):
        row = next(row for row in rows if row["rule_id"] == rule_id)
        if row["implementation_child"] != "#250":
            raise ValueError("quantitation projection bypasses #250")
        if row["parity_or_expected_diff"] != "approved_expected_diff_only":
            raise ValueError("quantitation projection lacks expected-diff gate")

    legacy_shift = next(row for row in rows if row["rule_id"] == "SP-019")
    if legacy_shift["final_runtime_disposition"] != "offline_oracle":
        raise ValueError("legacy best-shift policy remains live")

    for rule_id, expected_identity in EXPECTED_SHARED_IDENTITIES.items():
        row = next(row for row in rows if row["rule_id"] == rule_id)
        if row["input_identity"] != expected_identity:
            raise ValueError(f"crossed identity for {rule_id}")

    observed_states = {}
    for rule_id, expected_status in EXPECTED_DISTINCT_STATES.items():
        row = next(row for row in rows if row["rule_id"] == rule_id)
        observed_states[rule_id] = row["status"]
        if row["status"] != expected_status:
            raise ValueError(f"state distinction lost for {rule_id}")
    if len(set(observed_states.values())) != len(observed_states):
        raise ValueError("state distinction collapsed")

    if terminal == "PROMOTE_TO_T1" and any(
        row["proposed_disposition"] == "unresolved" for row in rows
    ):
        raise ValueError("unresolved blocks promotion")


def _validate_failure_rows(rows: list[dict[str, str]]) -> None:
    failure_ids = [row["failure_class_id"] for row in rows]
    if len(failure_ids) != len(set(failure_ids)):
        raise ValueError("duplicate historical failure class")
    if set(failure_ids) != EXPECTED_FAILURE_IDS:
        raise ValueError("partial historical failure population")
    if not REQUIRED_HISTORICAL_FAILURES <= set(failure_ids):
        raise ValueError("required historical failure is missing")
    for row in rows:
        for field in (
            "old_behavior",
            "observable_loss",
            "replacement_risk",
            "new_characterization",
            "positive_case",
            "counterexample",
            "acceptance_oracle",
            "rollback_or_stop",
        ):
            if not row[field]:
                raise ValueError(
                    f"historical failure is not behavioral: "
                    f"{row['failure_class_id']} {field}",
                )


def _validate_call_cost_rows(
    rows: list[dict[str, str]],
    terminal: str,
) -> None:
    scope_ids = [row["scope_id"] for row in rows]
    if len(scope_ids) != len(set(scope_ids)):
        raise ValueError("duplicate call-cost scope")
    if set(scope_ids) != EXPECTED_CALL_COST_IDS:
        raise ValueError("partial call-cost population")

    for row in rows:
        evidence_status = row["evidence_status"]
        if evidence_status not in EVIDENCE_STATUSES:
            raise ValueError("unknown call-cost evidence status")
        if evidence_status == "static_call_path":
            if any(row[field] != "not_observed" for field in COUNT_COLUMNS):
                raise ValueError("static call path masquerades as observed count")
            if "not observed" not in row["notes"].lower():
                raise ValueError("static call path lacks measurement qualifier")
        if evidence_status in {"static_call_path", "owner_derived", "unknown"}:
            if row["source_version_or_hash"] != FROZEN_GIT_VERSION:
                raise ValueError("stale source version")
        if evidence_status == "observed_retained":
            source = REPO_ROOT / row["source_locator"]
            if not source.is_file():
                raise ValueError("retained source is not tracked in-repo evidence")
            source_text = source.read_text(encoding="utf-8")
            actual_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            if actual_hash != row["source_version_or_hash"]:
                raise ValueError("stale source hash")
        if evidence_status == "unknown":
            notes = row["notes"]
            if "direction_changing=" not in notes or "next_loop=" not in notes:
                raise ValueError("unknown lacks direction and next-loop qualifier")
            if terminal == "PROMOTE_TO_T1" and "direction_changing=TRUE" in notes:
                raise ValueError("direction-changing unknown blocks promotion")


def _require_named_owner(row: dict[str, str], field: str) -> None:
    value = row[field].strip().lower()
    if value in {"", "none", "unknown", "n/a", "na"}:
        raise ValueError(f"value/authority-changing row lacks {field}")


def _read_tsv(path: Path, expected_columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != expected_columns:
            raise ValueError(f"closed schema mismatch for {path.name}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"empty tracked packet table: {path.name}")
    for row in rows:
        if None in row or any(
            value is None or not value.strip() for value in row.values()
        ):
            raise ValueError(f"missing required field in {path.name}")
    return rows


def _write_tsv(
    path: Path,
    rows: list[dict[str, str]],
    columns: tuple[str, ...],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _copy_packet(target: Path) -> Path:
    shutil.copytree(PACKET_DIR, target)
    return target
