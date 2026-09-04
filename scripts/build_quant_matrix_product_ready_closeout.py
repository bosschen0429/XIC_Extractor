"""Build/check the QuantMatrix Product Ready closeout packet.

This product-ready closeout collects the promotion candidate packet v2 and
default activation replay gate into one closeout artifact. It is still
read-only: it does not activate ProductWriter, write default matrix outputs,
change workbooks/GUI behavior, or alter selected peaks/areas/counting.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_quant_matrix_default_activation_dry_run import (
    DEFAULT_ACTIVATION_DRY_RUN_OUTPUT_DIR,
    validate_quant_matrix_default_activation_dry_run,
)
from scripts.build_quant_matrix_promotion_packet_v2 import (
    DEFAULT_OUTPUT_DIR as DEFAULT_PACKET_V2_DIR,
)
from scripts.build_quant_matrix_promotion_packet_v2 import (
    validate_quant_matrix_promotion_packet_v2,
)
from scripts.build_quant_matrix_real_bundle import (
    DEFAULT_ACCEPTED_BACKFILL_COUNT,
    DEFAULT_DOWNSTREAM_SCOPE,
    DEFAULT_SOURCE_RUN_ID,
)
from xic_extractor.tabular_io import (
    file_sha256,
    optional_int,
    read_tsv_required,
    write_tsv,
)

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PRODUCT_READY_CLOSEOUT_OUTPUT_DIR = Path(
    "docs/superpowers/validation/quant_matrix_product_ready_closeout_v1",
)
DEFAULT_PROMOTION_PACKET_V2_SUMMARY_JSON = (
    DEFAULT_PACKET_V2_DIR / "quant_matrix_promotion_packet_v2_summary.json"
)
DEFAULT_ACTIVATION_DRY_RUN_SUMMARY_JSON = (
    DEFAULT_ACTIVATION_DRY_RUN_OUTPUT_DIR
    / "quant_matrix_default_activation_dry_run_summary.json"
)

PRODUCT_READY_CLOSEOUT_SCHEMA = "quant_matrix_product_ready_closeout_v1"
PRODUCT_READY_CLOSEOUT_CHECK_SCHEMA = (
    "quant_matrix_product_ready_closeout_check_v1"
)

CHECK_COLUMNS = (
    "schema_version",
    "check_id",
    "status",
    "source",
    "meaning",
)


def build_quant_matrix_product_ready_closeout(
    *,
    output_dir: Path = DEFAULT_PRODUCT_READY_CLOSEOUT_OUTPUT_DIR,
    source_root: Path = ROOT,
    promotion_packet_v2_summary_json: Path = DEFAULT_PROMOTION_PACKET_V2_SUMMARY_JSON,
    activation_dry_run_summary_json: Path = DEFAULT_ACTIVATION_DRY_RUN_SUMMARY_JSON,
    expected_source_run_id: str = DEFAULT_SOURCE_RUN_ID,
    expected_downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    expected_accepted_backfill_count: int = DEFAULT_ACCEPTED_BACKFILL_COUNT,
) -> Mapping[str, Path]:
    promotion_packet_summary = _resolve_source(
        promotion_packet_v2_summary_json,
        source_root=source_root,
    )
    activation_dry_run_summary = _resolve_source(
        activation_dry_run_summary_json,
        source_root=source_root,
    )
    _raise_if_problems(
        "promotion packet v2 check failed",
        validate_quant_matrix_promotion_packet_v2(
            summary_json=promotion_packet_summary,
            source_root=source_root,
            expected_source_run_id=expected_source_run_id,
            expected_downstream_scope=expected_downstream_scope,
            expected_accepted_backfill_count=expected_accepted_backfill_count,
        ),
    )
    _raise_if_problems(
        "default activation dry-run check failed",
        validate_quant_matrix_default_activation_dry_run(
            summary_json=activation_dry_run_summary,
            source_root=source_root,
            expected_source_run_id=expected_source_run_id,
            expected_downstream_scope=expected_downstream_scope,
            expected_accepted_backfill_count=expected_accepted_backfill_count,
        ),
    )

    packet_payload = _read_json_object(promotion_packet_summary)
    dry_run_payload = _read_json_object(activation_dry_run_summary)
    check_rows = _check_rows(
        packet_payload=packet_payload,
        dry_run_payload=dry_run_payload,
    )
    _raise_if_problems(
        "product ready closeout checks failed",
        _check_row_problems(check_rows),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    checks_tsv = output_dir / "quant_matrix_product_ready_closeout_checks.tsv"
    write_tsv(
        checks_tsv,
        check_rows,
        CHECK_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    summary_json = output_dir / "quant_matrix_product_ready_closeout_summary.json"
    _write_summary(
        summary_json,
        output_dir=output_dir,
        source_root=source_root,
        promotion_packet_summary=promotion_packet_summary,
        activation_dry_run_summary=activation_dry_run_summary,
        packet_payload=packet_payload,
        dry_run_payload=dry_run_payload,
        checks_tsv=checks_tsv,
        check_rows=check_rows,
    )
    return {
        "summary_json": summary_json,
        "checks_tsv": checks_tsv,
    }


def validate_quant_matrix_product_ready_closeout(
    *,
    summary_json: Path = DEFAULT_PRODUCT_READY_CLOSEOUT_OUTPUT_DIR
    / "quant_matrix_product_ready_closeout_summary.json",
    source_root: Path = ROOT,
    expected_source_run_id: str = DEFAULT_SOURCE_RUN_ID,
    expected_downstream_scope: str = DEFAULT_DOWNSTREAM_SCOPE,
    expected_accepted_backfill_count: int = DEFAULT_ACCEPTED_BACKFILL_COUNT,
) -> list[str]:
    problems: list[str] = []
    try:
        payload = _read_json_object(summary_json)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [str(exc)]

    _append_expected_field_problems(payload, problems)
    if payload.get("source_run_id") != expected_source_run_id:
        problems.append(
            "product ready closeout source_run_id mismatch: "
            f"expected {expected_source_run_id}",
        )
    if payload.get("downstream_scope") != expected_downstream_scope:
        problems.append(
            "product ready closeout downstream_scope mismatch: "
            f"expected {expected_downstream_scope}",
        )
    if (
        optional_int(payload.get("accepted_backfill_count", ""))
        != expected_accepted_backfill_count
    ):
        problems.append(
            "product ready closeout accepted_backfill_count mismatch: "
            f"expected {expected_accepted_backfill_count}",
        )

    inputs = _input_artifacts(payload, source_root=source_root, problems=problems)
    artifacts = _output_artifacts(payload, summary_json.parent, problems)
    promotion_packet_summary = inputs.get("promotion_packet_v2_summary_json")
    activation_dry_run_summary = inputs.get("activation_dry_run_summary_json")
    checks_tsv = artifacts.get("checks_tsv")

    if promotion_packet_summary is not None:
        problems.extend(
            "promotion packet v2: " + problem
            for problem in validate_quant_matrix_promotion_packet_v2(
                summary_json=promotion_packet_summary,
                source_root=source_root,
                expected_source_run_id=expected_source_run_id,
                expected_downstream_scope=expected_downstream_scope,
                expected_accepted_backfill_count=expected_accepted_backfill_count,
            )
        )
    if activation_dry_run_summary is not None:
        problems.extend(
            "default activation dry-run: " + problem
            for problem in validate_quant_matrix_default_activation_dry_run(
                summary_json=activation_dry_run_summary,
                source_root=source_root,
                expected_source_run_id=expected_source_run_id,
                expected_downstream_scope=expected_downstream_scope,
                expected_accepted_backfill_count=expected_accepted_backfill_count,
            )
        )
    if (
        promotion_packet_summary is not None
        and activation_dry_run_summary is not None
        and checks_tsv is not None
    ):
        expected_rows = _check_rows(
            packet_payload=_read_json_object(promotion_packet_summary),
            dry_run_payload=_read_json_object(activation_dry_run_summary),
        )
        try:
            actual_rows = read_tsv_required(checks_tsv, CHECK_COLUMNS)
        except (OSError, ValueError) as exc:
            problems.append(f"product ready closeout checks TSV: {exc}")
            actual_rows = ()
        normalized_expected = [
            {column: row.get(column, "") for column in CHECK_COLUMNS}
            for row in expected_rows
        ]
        normalized_actual = [
            {column: row.get(column, "") for column in CHECK_COLUMNS}
            for row in actual_rows
        ]
        if normalized_actual != normalized_expected:
            problems.append("product ready closeout checks TSV is stale")
        problems.extend(_check_row_problems(normalized_expected))
        _append_source_payload_problems(
            payload,
            packet_payload=_read_json_object(promotion_packet_summary),
            dry_run_payload=_read_json_object(activation_dry_run_summary),
            problems=problems,
        )
    return problems


def _check_rows(
    *,
    packet_payload: Mapping[str, Any],
    dry_run_payload: Mapping[str, Any],
) -> list[dict[str, str]]:
    rows = [
        _check_row(
            "promotion_packet_v2_validated",
            packet_payload.get("readiness_label") == "production_ready_candidate_packet"
            and packet_payload.get("production_ready") is True
            and packet_payload.get("may_promote_default_quant_matrix") is True,
            "promotion_candidate_packet_v2",
            "Promotion candidate packet is a production-ready candidate packet.",
        ),
        _check_row(
            "science_evidence_complete",
            packet_payload.get("scientific_confidence_status") == "pass"
            and packet_payload.get("missing_science_evidence") == []
            and packet_payload.get("validation_tiers")
            == {
                "85raw_large_cohort": "pass",
                "heldout_oracle": "pass",
                "downstream_impact_smoke": "pass",
            },
            "promotion_candidate_packet_v2",
            "Large-cohort, heldout-oracle, and downstream-impact tiers pass.",
        ),
        _check_row(
            "default_activation_dry_run_passed",
            dry_run_payload.get("default_activation_dry_run_gate_status") == "pass"
            and dry_run_payload.get("all_reference_outputs_match") is True
            and dry_run_payload.get("dry_run_unused_expected_diff_count") == "0",
            "default_activation_replay",
            (
                "Default activation replay reproduces the real/candidate "
                "QuantMatrix bundle exactly."
            ),
        ),
        _check_row(
            "expected_diff_closed",
            str(dry_run_payload.get("dry_run_expected_diff_count", ""))
            == str(dry_run_payload.get("dry_run_written_backfill_count", ""))
            and dry_run_payload.get("dry_run_expected_diff_acceptance_status")
            == "pass",
            "default_activation_replay",
            "Expected-diff rows are all written during the dry-run gate.",
        ),
        _check_row(
            "authority_unchanged",
            packet_payload.get("write_authority") is False
            and dry_run_payload.get("write_authority") is False
            and packet_payload.get("default_quant_matrix_changed") is False
            and dry_run_payload.get("default_quant_matrix_changed") is False
            and dry_run_payload.get("default_matrix_files_written") is False,
            "promotion_candidate_packet_default_activation_replay_authority_flags",
            "Closeout does not grant matrix authority or write default outputs.",
        ),
        _check_row(
            "product_ready_candidate_closeout",
            packet_payload.get("source_run_id") == dry_run_payload.get("source_run_id")
            and packet_payload.get("downstream_scope")
            == dry_run_payload.get("downstream_scope")
            and packet_payload.get("accepted_backfill_count")
            == dry_run_payload.get("accepted_backfill_count"),
            "promotion_candidate_packet_default_activation_replay_source_binding",
            (
                "Promotion candidate packet and default activation replay bind "
                "to the same current rule-selected source run."
            ),
        ),
    ]
    return rows


def _check_row(
    check_id: str,
    passed: bool,
    source: str,
    meaning: str,
) -> dict[str, str]:
    return {
        "schema_version": PRODUCT_READY_CLOSEOUT_CHECK_SCHEMA,
        "check_id": check_id,
        "status": "pass" if passed else "fail",
        "source": source,
        "meaning": meaning,
    }


def _check_row_problems(rows: Sequence[Mapping[str, str]]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        check_id = row.get("check_id", "")
        seen.add(check_id)
        if row.get("schema_version") != PRODUCT_READY_CLOSEOUT_CHECK_SCHEMA:
            problems.append(f"{check_id}: schema_version mismatch")
        if row.get("status") != "pass":
            problems.append(f"{check_id}: check did not pass")
    expected_ids = {
        "promotion_packet_v2_validated",
        "science_evidence_complete",
        "default_activation_dry_run_passed",
        "expected_diff_closed",
        "authority_unchanged",
        "product_ready_candidate_closeout",
    }
    missing = sorted(expected_ids - seen)
    if missing:
        problems.append("missing product ready closeout checks: " + ", ".join(missing))
    return problems


def _write_summary(
    summary_json: Path,
    *,
    output_dir: Path,
    source_root: Path,
    promotion_packet_summary: Path,
    activation_dry_run_summary: Path,
    packet_payload: Mapping[str, Any],
    dry_run_payload: Mapping[str, Any],
    checks_tsv: Path,
    check_rows: Sequence[Mapping[str, str]],
) -> None:
    payload = {
        "schema_version": PRODUCT_READY_CLOSEOUT_SCHEMA,
        "artifact_role": "product_ready_closeout",
        "status": "pass",
        "closeout_label": "product_ready_default_matrix_candidate",
        "source_run_id": packet_payload.get("source_run_id", ""),
        "downstream_scope": packet_payload.get("downstream_scope", ""),
        "accepted_backfill_count": packet_payload.get("accepted_backfill_count", 0),
        "promotion_packet_readiness_label": packet_payload.get("readiness_label", ""),
        "promotion_packet_production_ready": packet_payload.get(
            "production_ready",
            False,
        ),
        "promotion_packet_may_promote_default_quant_matrix": packet_payload.get(
            "may_promote_default_quant_matrix",
            False,
        ),
        "dry_run_gate_status": dry_run_payload.get(
            "default_activation_dry_run_gate_status",
            "",
        ),
        "dry_run_expected_diff_count": dry_run_payload.get(
            "dry_run_expected_diff_count",
            "",
        ),
        "dry_run_written_backfill_count": dry_run_payload.get(
            "dry_run_written_backfill_count",
            "",
        ),
        "dry_run_unused_expected_diff_count": dry_run_payload.get(
            "dry_run_unused_expected_diff_count",
            "",
        ),
        "all_reference_outputs_match": dry_run_payload.get(
            "all_reference_outputs_match",
            False,
        ),
        "check_count": len(check_rows),
        "product_ready_candidate": True,
        "default_quant_matrix_product_ready_candidate": True,
        "may_activate_default_quant_matrix_with_explicit_contract": True,
        "requires_product_writer_activation_commit": True,
        "explicit_activation_not_in_this_commit": True,
        "read_only": True,
        "write_authority": False,
        "scorer_ran": False,
        "raw_or_85raw_ran": False,
        "product_writer_changed": False,
        "default_quant_matrix_changed": False,
        "default_matrix_files_written": False,
        "workbook_or_gui_changed": False,
        "selected_peak_area_or_counting_changed": False,
        "broad_backfill_unparked": False,
        "input_artifacts": {
            "promotion_packet_v2_summary_json": _source_relpath(
                promotion_packet_summary,
                source_root=source_root,
            ),
            "promotion_packet_v2_summary_json_sha256": file_sha256(
                promotion_packet_summary,
            ),
            "activation_dry_run_summary_json": _source_relpath(
                activation_dry_run_summary,
                source_root=source_root,
            ),
            "activation_dry_run_summary_json_sha256": file_sha256(
                activation_dry_run_summary,
            ),
        },
        "artifacts": {
            "checks_tsv": _artifact_record(checks_tsv, base_dir=output_dir),
        },
        "activation_contract": (
            "A future explicit ProductWriter activation may make the default "
            "quant matrix include detected values plus the current rule-selected "
            "Backfill values, provided it uses this expected-diff-bound contract "
            "and keeps provenance/reconstructability sidecars."
        ),
        "authority_statement": (
            "Product-ready closeout closes the evidence packet as a Product "
            "Ready default "
            "matrix candidate only. It does not itself write default matrix "
            "files, change ProductWriter behavior, or expand authority beyond "
            "the current rule-selected Backfill membership."
        ),
        "residual_risks": [
            "ProductWriter default activation still requires a separate "
            "explicit commit.",
            "This product-ready closeout ran no scorer and no RAW/85RAW.",
            "Broad Backfill remains parked.",
        ],
    }
    summary_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _append_expected_field_problems(
    payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    for field, expected_text in (
        ("schema_version", PRODUCT_READY_CLOSEOUT_SCHEMA),
        ("artifact_role", "product_ready_closeout"),
        ("status", "pass"),
        ("closeout_label", "product_ready_default_matrix_candidate"),
        ("promotion_packet_readiness_label", "production_ready_candidate_packet"),
        ("dry_run_gate_status", "pass"),
        ("dry_run_unused_expected_diff_count", "0"),
    ):
        if payload.get(field) != expected_text:
            problems.append(f"product ready closeout {field} mismatch")
    for field, expected_bool in (
        ("promotion_packet_production_ready", True),
        ("promotion_packet_may_promote_default_quant_matrix", True),
        ("all_reference_outputs_match", True),
        ("product_ready_candidate", True),
        ("default_quant_matrix_product_ready_candidate", True),
        ("may_activate_default_quant_matrix_with_explicit_contract", True),
        ("requires_product_writer_activation_commit", True),
        ("explicit_activation_not_in_this_commit", True),
        ("read_only", True),
        ("write_authority", False),
        ("scorer_ran", False),
        ("raw_or_85raw_ran", False),
        ("product_writer_changed", False),
        ("default_quant_matrix_changed", False),
        ("default_matrix_files_written", False),
        ("workbook_or_gui_changed", False),
        ("selected_peak_area_or_counting_changed", False),
        ("broad_backfill_unparked", False),
    ):
        if payload.get(field) is not expected_bool:
            problems.append(
                f"product ready closeout {field} must be "
                f"{str(expected_bool).lower()}",
            )


def _append_source_payload_problems(
    payload: Mapping[str, Any],
    *,
    packet_payload: Mapping[str, Any],
    dry_run_payload: Mapping[str, Any],
    problems: list[str],
) -> None:
    field_pairs = (
        ("source_run_id", "source_run_id"),
        ("downstream_scope", "downstream_scope"),
        ("accepted_backfill_count", "accepted_backfill_count"),
        ("promotion_packet_readiness_label", "readiness_label"),
        ("promotion_packet_production_ready", "production_ready"),
        (
            "promotion_packet_may_promote_default_quant_matrix",
            "may_promote_default_quant_matrix",
        ),
    )
    for payload_field, packet_field in field_pairs:
        if payload.get(payload_field) != packet_payload.get(packet_field):
            problems.append(f"product ready closeout {payload_field} is stale")
    dry_run_pairs = (
        ("dry_run_gate_status", "default_activation_dry_run_gate_status"),
        ("dry_run_expected_diff_count", "dry_run_expected_diff_count"),
        ("dry_run_written_backfill_count", "dry_run_written_backfill_count"),
        ("dry_run_unused_expected_diff_count", "dry_run_unused_expected_diff_count"),
        ("all_reference_outputs_match", "all_reference_outputs_match"),
    )
    for payload_field, dry_run_field in dry_run_pairs:
        if payload.get(payload_field) != dry_run_payload.get(dry_run_field):
            problems.append(f"product ready closeout {payload_field} is stale")


def _input_artifacts(
    payload: Mapping[str, Any],
    *,
    source_root: Path,
    problems: list[str],
) -> dict[str, Path]:
    raw = payload.get("input_artifacts")
    if not isinstance(raw, dict):
        problems.append("product ready closeout input_artifacts must be an object")
        return {}
    result: dict[str, Path] = {}
    for field in (
        "promotion_packet_v2_summary_json",
        "activation_dry_run_summary_json",
    ):
        path_value = str(raw.get(field, "")).strip()
        sha_value = str(raw.get(f"{field}_sha256", "")).strip()
        if not path_value:
            problems.append(f"product ready closeout {field} is missing")
            continue
        path = _resolve_source(Path(path_value), source_root=source_root)
        result[field] = path
        if not path.is_file():
            problems.append(f"product ready closeout {field} does not exist")
            continue
        if not _is_sha256(sha_value) or file_sha256(path) != sha_value.upper():
            problems.append(f"product ready closeout {field}_sha256 mismatch")
    return result


def _output_artifacts(
    payload: Mapping[str, Any],
    output_dir: Path,
    problems: list[str],
) -> dict[str, Path]:
    raw = payload.get("artifacts")
    if not isinstance(raw, dict):
        problems.append("product ready closeout artifacts must be an object")
        return {}
    result: dict[str, Path] = {}
    for label, raw_entry in raw.items():
        if not isinstance(raw_entry, dict):
            problems.append(f"product ready closeout {label} entry invalid")
            continue
        relpath = str(raw_entry.get("path", "")).strip()
        sha256 = str(raw_entry.get("sha256", "")).strip()
        path = Path(relpath)
        if not relpath or path.is_absolute() or ".." in path.parts:
            problems.append(f"product ready closeout {label} path invalid")
            continue
        resolved = (output_dir / path).resolve(strict=False)
        try:
            resolved.relative_to(output_dir.resolve(strict=False))
        except ValueError:
            problems.append(f"product ready closeout {label} path escapes output")
            continue
        result[str(label)] = resolved
        if not resolved.is_file():
            problems.append(f"product ready closeout {label} does not exist")
            continue
        if not _is_sha256(sha256) or file_sha256(resolved) != sha256.upper():
            problems.append(f"product ready closeout {label} sha256 mismatch")
    return result


def _artifact_record(path: Path, *, base_dir: Path) -> dict[str, str]:
    return {
        "path": path.resolve(strict=False).relative_to(
            base_dir.resolve(strict=False),
        ).as_posix(),
        "sha256": file_sha256(path),
    }


def _source_relpath(path: Path, *, source_root: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(
            source_root.resolve(strict=True),
        ).as_posix()
    except ValueError:
        return str(path.resolve(strict=True))


def _resolve_source(path: Path, *, source_root: Path) -> Path:
    return path if path.is_absolute() else source_root / path


def _read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _raise_if_problems(label: str, problems: Sequence[str]) -> None:
    if problems:
        raise ValueError(f"{label}: " + "; ".join(problems))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.check_only:
        summary_json = args.summary_json or (
            args.output_dir / "quant_matrix_product_ready_closeout_summary.json"
        )
        problems = validate_quant_matrix_product_ready_closeout(
            summary_json=summary_json,
            source_root=args.source_root,
            expected_source_run_id=args.expected_source_run_id,
            expected_downstream_scope=args.expected_downstream_scope,
            expected_accepted_backfill_count=args.expected_accepted_backfill_count,
        )
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(f"product_ready_closeout_summary_json: {summary_json}")
        print("product_ready_closeout_status: pass")
        return 0
    try:
        outputs = build_quant_matrix_product_ready_closeout(
            output_dir=args.output_dir,
            source_root=args.source_root,
            promotion_packet_v2_summary_json=args.promotion_packet_v2_summary_json,
            activation_dry_run_summary_json=args.activation_dry_run_summary_json,
            expected_source_run_id=args.expected_source_run_id,
            expected_downstream_scope=args.expected_downstream_scope,
            expected_accepted_backfill_count=args.expected_accepted_backfill_count,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for label, path in outputs.items():
        print(f"{label}: {path}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_PRODUCT_READY_CLOSEOUT_OUTPUT_DIR,
    )
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument(
        "--promotion-packet-v2-summary-json",
        type=Path,
        default=DEFAULT_PROMOTION_PACKET_V2_SUMMARY_JSON,
    )
    parser.add_argument(
        "--activation-dry-run-summary-json",
        type=Path,
        default=DEFAULT_ACTIVATION_DRY_RUN_SUMMARY_JSON,
    )
    parser.add_argument("--expected-source-run-id", default=DEFAULT_SOURCE_RUN_ID)
    parser.add_argument(
        "--expected-downstream-scope",
        default=DEFAULT_DOWNSTREAM_SCOPE,
    )
    parser.add_argument(
        "--expected-accepted-backfill-count",
        type=int,
        default=DEFAULT_ACCEPTED_BACKFILL_COUNT,
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--summary-json", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
