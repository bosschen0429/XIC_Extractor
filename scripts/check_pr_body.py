"""Validate the proportional PR Evidence Receipt contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

REQUIRED_SECTIONS = (
    ("outcome", "Outcome"),
    ("problem and observable loss", "Problem and observable loss"),
    ("what changed", "What changed"),
    ("scope and contracts", "Scope and contracts"),
    ("review guide", "Review guide"),
    ("verification", "Verification"),
    ("residual risk and rollback", "Residual risk and rollback"),
    ("issue and delivery linkage", "Issue and delivery linkage"),
    ("authority check", "Authority Check"),
)
RECEIPT_COLUMNS = (
    "claim",
    "basis",
    "evidence and binding",
    "result",
    "what it proves",
    "what it does not prove",
)
ALLOWED_BASES = {"contract", "observed", "decision", "inference"}
ALLOWED_LANES = {"shared", "targeted", "untargeted", "workflow-only"}
ALLOWED_PRODUCT_OUTPUT_CHANGES = {"yes", "no"}
ALLOWED_PUBLIC_CONTRACTS = {
    "cli",
    "config",
    "tsv-csv schema",
    "workbook",
    "run metadata",
    "matrix identity",
    "selected peak-area",
    "counted detection",
    "none",
}
ALLOWED_EVIDENCE_STATUSES = {
    "diagnostic_only",
    "shadow_ready",
    "production_candidate",
    "production_ready",
    "inconclusive",
}
PLACEHOLDER_TOKEN_RE = re.compile(
    r"\b(?:TBD|TODO|FILL[_ -]?ME)\b",
    flags=re.IGNORECASE,
)
HEADING_RE = re.compile(r"(?m)^#{1,6}[ \t]+(.+?)[ \t]*$")
SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
LOCAL_PATH_PATTERNS = (
    re.compile(r"(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(r"(?<![A-Za-z0-9:])(?:\\\\|//)[^\\/\s]+[\\/]"),
    re.compile(r"(?<![A-Za-z0-9:/<])/(?!/)[^\s<>()]+"),
    re.compile(r"(?i)\b(?:file|vscode)://"),
)
REQUIRED_AUTHORITY_ACKS = (
    "This PR does not grant ProductWriter or matrix/workbook authority from "
    "diagnostics, sidecars, adjudication, review packets, or lockbox labels "
    "unless an explicit contract and expected-diff are included.",
    "Unrelated dirty files were not reverted or rewritten.",
)


def validate_pr_body(body: str, expected_head_sha: str) -> tuple[str, ...]:
    """Return deterministic contract violations for a PR body."""

    errors: list[str] = []
    stripped_body = _strip_html_comments(body)
    semantic_body = _strip_fenced_code(stripped_body)
    sections = _extract_sections(semantic_body)

    if PLACEHOLDER_TOKEN_RE.search(semantic_body):
        errors.append("PR body contains a TBD, TODO, or FILL_ME placeholder")
    if any(pattern.search(body) for pattern in LOCAL_PATH_PATTERNS):
        errors.append("PR body contains a private local absolute path")

    for key, display_name in REQUIRED_SECTIONS:
        values = sections.get(key, ())
        if not values:
            errors.append(f"missing required section: {display_name}")
            continue
        if len(values) > 1:
            errors.append(f"required section appears more than once: {display_name}")
        if not _has_substantive_content(values[0]):
            errors.append(f"required section is empty: {display_name}")

    scope = _first_section(sections, "scope and contracts")
    lane = _require_field(scope, "Lane", errors)
    product_change = _require_field(scope, "Product output change", errors)
    public_contracts = _require_field(
        scope,
        "Public contracts at risk",
        errors,
        allow_none=True,
    )

    normalized_lane = _normalize_value(lane)
    if lane is not None and normalized_lane not in ALLOWED_LANES:
        errors.append("Lane must select exactly one allowed option")

    normalized_product_change = _normalize_value(product_change)
    if (
        product_change is not None
        and normalized_product_change not in ALLOWED_PRODUCT_OUTPUT_CHANGES
    ):
        errors.append("Product output change must select yes or no")

    if public_contracts is not None:
        selected_contracts = {
            part.strip().casefold()
            for part in re.split(r"[,;]", _plain_markdown(public_contracts))
            if part.strip()
        }
        if not selected_contracts or not selected_contracts <= ALLOWED_PUBLIC_CONTRACTS:
            errors.append("Public contracts at risk must select allowed options")
        elif "none" in selected_contracts and len(selected_contracts) != 1:
            errors.append("Public contracts at risk must use none by itself")

    verification = _first_section(sections, "verification")
    reviewed_head = _require_field(verification, "Reviewed head", errors)
    evidence_status = _require_field(
        verification,
        "Validation evidence status",
        errors,
    )

    normalized_expected_sha = expected_head_sha.strip().casefold()
    if SHA_RE.fullmatch(normalized_expected_sha) is None:
        errors.append("expected head SHA must be exactly 40 hexadecimal characters")

    normalized_reviewed_head = _normalize_value(reviewed_head)
    if reviewed_head is not None:
        if SHA_RE.fullmatch(normalized_reviewed_head) is None:
            errors.append(
                "Reviewed head must be exactly 40 hexadecimal characters"
            )
        elif normalized_reviewed_head != normalized_expected_sha:
            errors.append("Reviewed head does not match the pull request head SHA")

    normalized_evidence_status = _normalize_value(evidence_status)
    if (
        evidence_status is not None
        and normalized_evidence_status not in ALLOWED_EVIDENCE_STATUSES
    ):
        errors.append("Validation evidence status must select an allowed option")
    receipt_rows, receipt_errors = _receipt_rows(verification)
    errors.extend(receipt_errors)
    if not receipt_rows:
        errors.append("Evidence Receipt must contain at least one non-empty row")
    for row_number, row in enumerate(receipt_rows, start=1):
        _validate_receipt_row(
            row,
            row_number=row_number,
            expected_head_sha=normalized_expected_sha,
            errors=errors,
        )

    residual = _first_section(sections, "residual risk and rollback")
    residual_risk = _require_field(residual, "Residual risk", errors)
    rollback = _require_field(residual, "Rollback/stop", errors)
    _require_none_rationale(residual_risk, "Residual risk", errors)
    _require_none_rationale(rollback, "Rollback/stop", errors)

    linkage = _first_section(sections, "issue and delivery linkage")
    issue_linkage = _require_field(linkage, "Issue linkage", errors)
    acceptance = _require_field(linkage, "Acceptance closed", errors)
    if issue_linkage is not None and not _valid_issue_linkage(issue_linkage):
        errors.append(
            "Issue linkage must use a supported closing keyword, Refs #N, "
            "or No issue with rationale"
        )
    if acceptance is not None and _is_placeholder(acceptance):
        errors.append("Acceptance closed must contain a concrete statement")

    authority = _first_section(sections, "authority check")
    if re.search(r"(?m)^\s*[-*+]\s+\[\s*\]", authority):
        errors.append("Authority Check contains an unchecked checkbox")
    checked_authority = {
        " ".join(match.group(1).split())
        for match in re.finditer(
            r"(?mi)^\s*[-*+]\s+\[[xX]\]\s+(.+?)\s*$",
            authority,
        )
    }
    for acknowledgement in REQUIRED_AUTHORITY_ACKS:
        if acknowledgement not in checked_authority:
            errors.append(
                "Authority Check is missing required checked assertion: "
                f"{acknowledgement}"
            )

    return tuple(errors)


def _strip_html_comments(body: str) -> str:
    return re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)


def _strip_fenced_code(body: str) -> str:
    visible: list[str] = []
    fence_character: str | None = None
    minimum_closing_length = 0

    for line in body.splitlines(keepends=True):
        candidate = line.rstrip("\r\n")
        if fence_character is not None:
            if re.fullmatch(
                rf"[ ]{{0,3}}{re.escape(fence_character)}"
                rf"{{{minimum_closing_length},}}[ \t]*",
                candidate,
            ):
                fence_character = None
                minimum_closing_length = 0
            continue

        opening = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", candidate)
        if opening is None:
            visible.append(line)
            continue
        fence = opening.group(1)
        info = opening.group(2)
        if fence.startswith("`") and "`" in info:
            visible.append(line)
            continue
        fence_character = fence[0]
        minimum_closing_length = len(fence)

    return "".join(visible)


def _extract_sections(body: str) -> dict[str, tuple[str, ...]]:
    matches = list(HEADING_RE.finditer(body))
    collected: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        title = _normalize_heading(match.group(1))
        if title not in {key for key, _ in REQUIRED_SECTIONS}:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        collected.setdefault(title, []).append(body[match.end() : end])
    return {key: tuple(values) for key, values in collected.items()}


def _normalize_heading(value: str) -> str:
    return " ".join(_plain_markdown(value).split()).casefold()


def _plain_markdown(value: str) -> str:
    return value.strip().strip("`*_ ")


def _normalize_value(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(_plain_markdown(value).split()).casefold()


def _first_section(
    sections: dict[str, tuple[str, ...]],
    key: str,
) -> str:
    values = sections.get(key, ())
    return values[0] if values else ""


def _has_substantive_content(section: str) -> bool:
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(
            r"[-*+]\s+(?:\[[ xX]\]\s+)?(?:\*\*|__)?[^:|]+"
            r"(?:\*\*|__)?\s*:\s*",
            stripped,
        ):
            continue
        cells = _split_markdown_row(stripped)
        if cells is not None:
            normalized = tuple(_normalize_header_cell(cell) for cell in cells)
            if normalized == RECEIPT_COLUMNS or all(
                _is_table_separator(cell) for cell in cells
            ):
                continue
        if _is_placeholder(stripped) or re.search(r"\w", stripped) is None:
            continue
        return True
    return False


def _field_value(section: str, label: str) -> str | None:
    pattern = re.compile(
        rf"(?mi)^\s*(?:[-*+]\s*)?(?:\*\*|__)?{re.escape(label)}"
        rf"(?:\*\*|__)?\s*:\s*(.*?)\s*$"
    )
    match = pattern.search(section)
    if match is None:
        return None
    value = match.group(1).strip()
    return value or None


def _require_field(
    section: str,
    label: str,
    errors: list[str],
    *,
    allow_none: bool = False,
) -> str | None:
    value = _field_value(section, label)
    if value is None:
        errors.append(f"{label} is missing or empty")
        return None
    if _is_placeholder(value) and not (
        allow_none and _normalize_value(value) == "none"
    ):
        errors.append(f"{label} contains a placeholder")
    return value


def _receipt_rows(section: str) -> tuple[list[list[str]], list[str]]:
    lines = section.splitlines()
    errors: list[str] = []
    for index, line in enumerate(lines):
        cells = _split_markdown_row(line)
        if cells is None:
            continue
        normalized = tuple(_normalize_header_cell(cell) for cell in cells)
        if normalized != RECEIPT_COLUMNS:
            continue
        if index + 1 >= len(lines):
            return [], ["Evidence Receipt table is missing its separator row"]
        separator = _split_markdown_row(lines[index + 1])
        if separator is None or len(separator) != len(RECEIPT_COLUMNS) or not all(
            _is_table_separator(cell) for cell in separator
        ):
            return [], ["Evidence Receipt table has an invalid separator row"]

        rows: list[list[str]] = []
        for data_line in lines[index + 2 :]:
            if not data_line.strip():
                break
            data_cells = _split_markdown_row(data_line)
            if data_cells is None:
                break
            if len(data_cells) != len(RECEIPT_COLUMNS):
                errors.append(
                    "Evidence Receipt row must contain exactly six semantic cells"
                )
                continue
            if any(cell.strip() for cell in data_cells):
                rows.append(data_cells)
        return rows, errors
    return [], ["Evidence Receipt table must use the exact six semantic columns"]


def _split_markdown_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None

    cells: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(stripped):
        character = stripped[index]
        if character == "\\" and index + 1 < len(stripped):
            next_character = stripped[index + 1]
            if next_character == "|":
                current.append("|")
                index += 2
                continue
        if character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        index += 1
    cells.append("".join(current).strip())

    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def _normalize_header_cell(value: str) -> str:
    return " ".join(_plain_markdown(value).split()).casefold()


def _is_table_separator(value: str) -> bool:
    return re.fullmatch(r":?-{3,}:?", value.strip()) is not None


def _validate_receipt_row(
    row: list[str],
    *,
    row_number: int,
    expected_head_sha: str,
    errors: list[str],
) -> None:
    for column, value in zip(RECEIPT_COLUMNS, row, strict=True):
        if _is_placeholder(value):
            errors.append(
                f"Evidence Receipt row {row_number} column {column!r} "
                "is empty or placeholder"
            )

    basis = _normalize_value(row[1])
    if basis not in ALLOWED_BASES:
        errors.append(
            f"Evidence Receipt row {row_number} basis must be contract, "
            "observed, decision, or inference"
        )

    if _semantic_text(row[4]) == _semantic_text(row[5]):
        errors.append(
            f"Evidence Receipt row {row_number} must distinguish what the "
            "evidence proves from what it does not prove"
        )

    if not _binding_is_traceable(row[2], expected_head_sha):
        errors.append(
            f"Evidence Receipt row {row_number} must bind repo evidence to "
            "reviewed HEAD or provide external input/config/hash/locator"
        )


def _semantic_text(value: str) -> str:
    return " ".join(re.findall(r"\w+", _plain_markdown(value).casefold()))


def _binding_is_traceable(value: str, expected_head_sha: str) -> bool:
    normalized = " ".join(_plain_markdown(value).split()).casefold()
    fields = _binding_fields(normalized)
    repo_head = fields.get("head", "")
    if repo_head:
        has_matching_head = repo_head == expected_head_sha
        has_locator = bool(fields.get("command") or fields.get("locator"))
        return has_matching_head and has_locator

    external_fields = ("input", "config", "hash", "locator")
    return all(fields.get(field) for field in external_fields)


def _binding_fields(value: str) -> dict[str, str]:
    field_names = ("head", "command", "input", "config", "hash", "locator")
    next_field = "|".join(field_names)
    fields: dict[str, str] = {}
    for field in field_names:
        match = re.search(
            rf"\b{field}\s*(?:=|:)\s*(.*?)"
            rf"(?=\s*[;,]\s*(?:{next_field})\s*(?:=|:)|$)",
            value,
        )
        if match is None:
            continue
        field_value = match.group(1).strip(" ;,")
        if not _is_placeholder(field_value):
            fields[field] = field_value
    return fields


def _is_placeholder(value: str) -> bool:
    normalized = _normalize_value(value)
    return not normalized or re.search(r"\w", normalized) is None or normalized in {
        "-",
        "—",
        "n/a",
        "na",
        "none",
        "placeholder",
    } or PLACEHOLDER_TOKEN_RE.search(normalized) is not None


def _require_none_rationale(
    value: str | None,
    label: str,
    errors: list[str],
) -> None:
    if value is None:
        return
    normalized = _normalize_value(value)
    if normalized == "none":
        errors.append(f"{label} uses None without a rationale")
        return
    if re.match(r"^none\b", normalized):
        rationale = re.match(
            r"^none\s*(?:[-—:;]|because\b)\s*(.+)$",
            normalized,
        )
        if rationale is None or _is_placeholder(rationale.group(1)):
            errors.append(f"{label} uses None without a rationale")


def _valid_issue_linkage(value: str) -> bool:
    normalized = " ".join(_plain_markdown(value).split())
    issue_reference = (
        r"(?:close(?:s|d)?|fix(?:es|ed)?|resolve(?:s|d)?|refs)"
        r"\s*:?\s*#\d+"
    )
    if re.fullmatch(
        rf"(?i){issue_reference}(?:\s*[,;]\s*{issue_reference})*",
        normalized,
    ):
        return True
    no_issue = re.fullmatch(
        r"(?is)No issue\s*(?:[-—:;]|because\b)\s*(.+)",
        normalized,
    )
    return no_issue is not None and not _is_placeholder(no_issue.group(1))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--github-event", type=Path)
    source.add_argument("--body-file", type=Path)
    parser.add_argument("--head-sha")
    args = parser.parse_args(argv)
    if args.body_file is not None and not args.head_sha:
        parser.error("--body-file requires --head-sha")
    if args.github_event is not None and args.head_sha:
        parser.error("--head-sha is only valid with --body-file")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.github_event is not None:
            event = json.loads(args.github_event.read_text(encoding="utf-8"))
            if not isinstance(event, dict):
                raise ValueError("GitHub event must be a JSON object")
            pull_request = event.get("pull_request")
            if pull_request is None:
                print(
                    "PR Evidence Receipt check skipped: event is not a "
                    "pull_request event."
                )
                return 0
            if not isinstance(pull_request, dict):
                raise ValueError("pull_request must be a JSON object")
            user = pull_request.get("user")
            login = user.get("login") if isinstance(user, dict) else None
            if login == "dependabot[bot]":
                print(
                    "PR Evidence Receipt check exempted: dependabot[bot] "
                    "creates machine-authored dependency PR bodies."
                )
                return 0
            if not isinstance(login, str) or not login:
                raise ValueError("pull_request.user.login is missing")
            body_value = pull_request.get("body")
            body = "" if body_value is None else body_value
            if not isinstance(body, str):
                raise ValueError("pull_request.body must be text or null")
            head = pull_request.get("head")
            head_sha = head.get("sha") if isinstance(head, dict) else None
            if not isinstance(head_sha, str):
                raise ValueError("pull_request.head.sha is missing")
        else:
            body = args.body_file.read_text(encoding="utf-8")
            head_sha = args.head_sha
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"PR Evidence Receipt input error: {exc}", file=sys.stderr)
        return 2

    errors = validate_pr_body(body, head_sha)
    if errors:
        print("PR Evidence Receipt validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"PR Evidence Receipt valid for reviewed head {head_sha}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
