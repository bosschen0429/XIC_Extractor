"""Validate the repository's Atomic issue-to-PR delivery contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ISSUE_REQUIRED_SECTIONS = (
    "Goal",
    "Execution binding",
    "Delivery graph placement and handoff",
    "Semantic owner and finite production universe",
    "Owner-local asset disposition",
    "Scope ceiling and non-goals",
    "Executable acceptance contract",
    "Atomic delivery boundary",
    "Local resolution and parent escalation",
)
LEGACY_AUDIT_SECTIONS = (
    "Current packet",
    "Problem and observable loss",
    "Atomic outcome",
    "Asset disposition and write lease",
    "Acceptance and counterexamples",
    "Rollback",
    "Non-goals and stop rule",
    "Completion contract",
)
ACCEPTANCE_FIELDS = (
    "Actor",
    "Input",
    "Real entry path",
    "Observable",
    "Expected result",
    "Stable oracle",
    "Falsifying counterexample",
    "Failure semantics",
    "Cancellation semantics",
    "Partial-write semantics",
    "Preserved invariants",
    "Non-goals",
    "Rollback",
    "Close rule",
)
UNIT_FIELDS = (
    "Bounded outcome",
    "Current consumer",
    "Independently decisive oracle",
    "Independently useful",
    "Independently reviewable",
    "Independently testable",
    "Independently mergeable",
    "Independently reversible",
    "Later sibling required",
    "Split rationale",
    "Replaced surface disposition",
    "Rollback",
)
INDEPENDENCE_FIELDS = (
    "Independently useful",
    "Independently reviewable",
    "Independently testable",
    "Independently mergeable",
    "Independently reversible",
)
DELIVERY_PLANS = {
    "one-ticket-one-atomic-pr",
    "independently-atomic-multi-pr",
}
PR_DELIVERY_MODES = {"closes-entire-ticket", "independent-atomic-unit"}
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
ISSUE_IDENTITY_RE = re.compile(
    r"^(?P<repository>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<number>[1-9][0-9]*)$"
)
HEADING_RE = re.compile(r"(?m)^(?P<marks>#{1,6})[ \t]+(?P<title>.+?)[ \t]*$")
FIELD_RE = re.compile(
    r"(?m)^-[ \t]+(?P<name>[A-Za-z][A-Za-z0-9 /-]*):[ \t]*(?P<value>.*)$"
)
PLACEHOLDER_RE = re.compile(r"\b(?:TBD|TODO|FILL[_ -]?ME)\b", re.IGNORECASE)
FUTURE_CONSUMER_RE = re.compile(
    r"\b(?:later|future|eventual|sibling|not[ -]?yet)\b|after[ \t]+#|"
    r"first consumer arrives",
    re.IGNORECASE,
)
FORBIDDEN_SPLIT_SEAM_RE = re.compile(
    r"\b(?:size|large|too[ -]large)\b|\bLOC\b|lines?[ -]of[ -]code|"
    r"file[ -]count|module(?:s|[ -]count)?|"
    r"test[ -]count|context[ -](?:window|size|budget)|review[ -]time|"
    r"construction[ -](?:stage|phase)|implementation[ -]phase",
    re.IGNORECASE,
)
ACTIVATION_RE = re.compile(r"\b(?:activat\w*|replac\w*)\b", re.IGNORECASE)
CLOSING_LINK_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?[ \t]+#(?P<number>[1-9][0-9]*)\b",
    re.IGNORECASE,
)
REF_LINK_RE = re.compile(r"\bRefs[ \t]+#(?P<number>[1-9][0-9]*)\b", re.IGNORECASE)


def validate_issue_contract(
    body: str,
    *,
    labels: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return deterministic implementation-issue contract violations."""

    errors: list[str] = []
    semantic_body = _semantic_body(body)
    sections = _sections(semantic_body, level=3)
    required = _require_sections(sections, ISSUE_REQUIRED_SECTIONS, errors)

    goal = required.get("Goal")
    acceptance = required.get("Executable acceptance contract")
    if goal is not None and not _substantive(goal):
        errors.append("required section is empty: Goal")
    if acceptance is None or not _substantive(acceptance):
        errors.append("goal has no decisive executable acceptance")
    else:
        acceptance_fields = _declared_fields(acceptance)
        _require_fields(
            acceptance_fields,
            ACCEPTANCE_FIELDS,
            errors,
            context="Executable acceptance contract",
        )

    execution_binding = required.get("Execution binding", "").strip()
    provisional = execution_binding.casefold().startswith("provisional -")
    if (
        execution_binding
        and not SHA_RE.fullmatch(execution_binding)
        and not provisional
    ):
        errors.append(
            "Execution binding must be a 40-character commit SHA or start with "
            "'provisional -'"
        )

    normalized_labels = {label.casefold() for label in labels}
    graph = required.get("Delivery graph placement and handoff", "")
    blocked = bool(re.search(r"\bblocked(?:[ -]by)?\b", graph, re.IGNORECASE))
    if "ready-for-agent" in normalized_labels and (provisional or blocked):
        errors.append("ready-for-agent cannot certify a blocked or provisional issue")

    atomic = required.get("Atomic delivery boundary")
    if atomic is not None:
        _validate_issue_atomic_boundary(atomic, errors)
    if re.search(r"\b(?:stacked|split) PRs?\b", semantic_body, re.IGNORECASE) and (
        FORBIDDEN_SPLIT_SEAM_RE.search(semantic_body)
    ):
        errors.append(
            "split rationale cannot use size, context, review-time, or "
            "construction-stage seams"
        )

    return tuple(_deduplicate(errors))


def validate_pr_contract(
    body: str,
    expected_head_sha: str,
    *,
    expected_repository: str | None = None,
) -> tuple[str, ...]:
    """Return deterministic Atomic PR closure violations."""

    errors: list[str] = []
    semantic_body = _semantic_body(body)
    sections = _sections(semantic_body, level=2)
    required = _require_sections(
        sections,
        (
            "Ticket-to-PR closure receipt",
            "Verification",
            "Issue and delivery linkage",
        ),
        errors,
    )

    if not SHA_RE.fullmatch(expected_head_sha):
        errors.append(
            "expected head SHA must contain exactly 40 hexadecimal characters"
        )

    closure = required.get("Ticket-to-PR closure receipt")
    closure_values: dict[str, str] = {}
    if closure is not None:
        closure_fields = _declared_fields(closure)
        closure_values = _require_fields(
            closure_fields,
            ("Issue identity", "Ticket delivery", *UNIT_FIELDS),
            errors,
            context="Ticket-to-PR closure receipt",
        )

    identity_match = ISSUE_IDENTITY_RE.fullmatch(
        closure_values.get("Issue identity", "").strip()
    )
    if closure_values.get("Issue identity") and identity_match is None:
        errors.append(
            "Issue identity must use exact repository-qualified form owner/repo#N"
        )
    if (
        identity_match is not None
        and expected_repository is not None
        and identity_match.group("repository").casefold()
        != expected_repository.casefold()
    ):
        errors.append("Issue identity repository must match the validated repository")

    delivery_mode = closure_values.get("Ticket delivery", "").casefold()
    if delivery_mode and delivery_mode not in PR_DELIVERY_MODES:
        errors.append(
            "Ticket delivery must be closes-entire-ticket or independent-atomic-unit"
        )
    _validate_unit(
        closure_values,
        errors,
        context="Ticket-to-PR closure receipt",
        split_unit=delivery_mode == "independent-atomic-unit",
    )

    verification = required.get("Verification")
    if verification is not None:
        reviewed_head = _require_fields(
            _declared_fields(verification),
            ("Reviewed head",),
            errors,
            context="Verification",
        ).get("Reviewed head")
        if reviewed_head and reviewed_head.casefold() != expected_head_sha.casefold():
            errors.append("Reviewed head must match the validated head SHA")

    linkage_section = required.get("Issue and delivery linkage")
    linkage = ""
    if linkage_section is not None:
        linkage = _require_fields(
            _declared_fields(linkage_section),
            ("Issue linkage",),
            errors,
            context="Issue and delivery linkage",
        ).get("Issue linkage", "")

    issue_number = identity_match.group("number") if identity_match else None
    closing_match = CLOSING_LINK_RE.search(linkage)
    ref_match = REF_LINK_RE.search(linkage)
    if delivery_mode == "closes-entire-ticket" and closing_match is None:
        errors.append("closes-entire-ticket requires a supported closing issue linkage")
    if delivery_mode == "independent-atomic-unit":
        if closing_match is not None or ref_match is None:
            errors.append(
                "independent-atomic-unit must use Refs #N without closing the issue"
            )
    linked_number = (
        closing_match.group("number")
        if closing_match is not None
        else ref_match.group("number")
        if ref_match is not None
        else None
    )
    if issue_number and linked_number and issue_number != linked_number:
        errors.append(
            "Issue identity and Issue linkage must name the same issue number"
        )

    return tuple(_deduplicate(errors))


def _validate_issue_atomic_boundary(section: str, errors: list[str]) -> None:
    first_unit = re.search(r"(?m)^####[ \t]+Proposed PR unit:", section)
    header = section[: first_unit.start()] if first_unit else section
    plan = (
        _require_fields(
            _declared_fields(header),
            ("Delivery plan",),
            errors,
            context="Atomic delivery boundary",
        )
        .get("Delivery plan", "")
        .casefold()
    )
    if plan and plan not in DELIVERY_PLANS:
        errors.append(
            "Delivery plan must be one-ticket-one-atomic-pr or "
            "independently-atomic-multi-pr"
        )

    units = _sections(section, level=4, title_prefix="Proposed PR unit:")
    unit_count = sum(len(values) for values in units.values())
    if unit_count == 0:
        errors.append(
            "Atomic delivery boundary must declare at least one Proposed PR unit"
        )
        return
    if plan == "one-ticket-one-atomic-pr" and unit_count != 1:
        errors.append(
            "one-ticket-one-atomic-pr must declare exactly one Proposed PR unit"
        )
    if plan == "independently-atomic-multi-pr" and unit_count < 2:
        errors.append(
            "independently-atomic-multi-pr must declare at least two PR units"
        )

    for heading, values in units.items():
        unit_name = heading.split(":", 1)[1].strip()
        if not _substantive(unit_name):
            errors.append("Proposed PR unit must have a substantive unique name")
        for value in values:
            fields = _declared_fields(value)
            unit_values = _require_fields(
                fields,
                UNIT_FIELDS,
                errors,
                context=f"Proposed PR unit '{unit_name}'",
            )
            _validate_unit(
                unit_values,
                errors,
                context=f"Proposed PR unit '{unit_name}'",
                split_unit=plan == "independently-atomic-multi-pr",
            )


def _validate_unit(
    values: dict[str, str],
    errors: list[str],
    *,
    context: str,
    split_unit: bool,
) -> None:
    for field in INDEPENDENCE_FIELDS:
        value = values.get(field)
        if value and value.casefold() != "yes":
            errors.append(f"{context}: {field} must be yes")
    later = values.get("Later sibling required")
    if later and later.casefold() != "no":
        errors.append(f"{context}: Later sibling required must be no")

    consumer = values.get("Current consumer", "")
    if consumer and FUTURE_CONSUMER_RE.search(consumer):
        errors.append(
            f"{context}: current consumer must exist before any later sibling"
        )

    split_rationale = values.get("Split rationale", "")
    if split_unit and FORBIDDEN_SPLIT_SEAM_RE.search(split_rationale):
        errors.append(
            f"{context}: split rationale cannot use size, context, review-time, "
            "or construction-stage seams"
        )

    outcome = values.get("Bounded outcome", "")
    disposition = values.get("Replaced surface disposition", "").strip()
    normalized_disposition = disposition.casefold()
    valid_disposition = False
    if normalized_disposition.startswith("retire:"):
        valid_disposition = _substantive(disposition.split(":", 1)[1])
    elif normalized_disposition.startswith("retain:"):
        before, separator, obligation = disposition.partition("Obligation:")
        valid_disposition = (
            bool(separator)
            and _substantive(before.split(":", 1)[1])
            and _substantive(obligation)
        )
    elif normalized_disposition.startswith("not applicable:"):
        valid_disposition = _substantive(disposition.split(":", 1)[1])
    if disposition and not valid_disposition:
        errors.append(
            f"{context}: Replaced surface disposition must declare Retire, "
            "Retain with Obligation, or a reasoned Not applicable"
        )
    if ACTIVATION_RE.search(outcome) and normalized_disposition.startswith(
        "not applicable:"
    ):
        errors.append(
            f"{context}: activation or replacement requires retirement or an "
            "explicit retention obligation"
        )


def _sections(
    body: str,
    *,
    level: int,
    title_prefix: str | None = None,
) -> dict[str, tuple[str, ...]]:
    matches = list(HEADING_RE.finditer(body))
    sections: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        if len(match.group("marks")) != level:
            continue
        title = match.group("title").strip()
        if title_prefix is not None and not title.startswith(title_prefix):
            continue
        end = len(body)
        for next_match in matches[index + 1 :]:
            if len(next_match.group("marks")) <= level:
                end = next_match.start()
                break
        sections.setdefault(title, []).append(body[match.end() : end].strip())
    return {key: tuple(values) for key, values in sections.items()}


def _require_sections(
    sections: dict[str, tuple[str, ...]],
    required: Sequence[str],
    errors: list[str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    for heading in required:
        matches = sections.get(heading, ())
        if not matches:
            errors.append(f"missing required section: {heading}")
            continue
        if len(matches) > 1:
            errors.append(f"required section appears more than once: {heading}")
        values[heading] = matches[0]
        if not _substantive(matches[0]):
            errors.append(f"required section is empty: {heading}")
    return values


def _declared_fields(section: str) -> dict[str, tuple[str, ...]]:
    fields: dict[str, list[str]] = {}
    for match in FIELD_RE.finditer(section):
        fields.setdefault(match.group("name").strip(), []).append(
            match.group("value").strip()
        )
    return {key: tuple(values) for key, values in fields.items()}


def _require_fields(
    fields: dict[str, tuple[str, ...]],
    required: Sequence[str],
    errors: list[str],
    *,
    context: str,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in required:
        matches = fields.get(field, ())
        if not matches:
            errors.append(f"{context}: missing declared field: {field}")
            continue
        if len(matches) > 1:
            errors.append(f"{context}: declared field appears more than once: {field}")
        value = matches[0]
        values[field] = value
        if not _substantive(value):
            errors.append(
                f"{context}: declared field is empty or a placeholder: {field}"
            )
    return values


def _semantic_body(body: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    return re.sub(r"```.*?```|~~~.*?~~~", "", without_comments, flags=re.DOTALL)


def _substantive(value: str) -> bool:
    if PLACEHOLDER_RE.search(value):
        return False
    if value.strip().casefold() in {"yes", "no"}:
        return True
    plain = re.sub(r"[`*_<>\[\](){}#|:;,.!?/\\=+-]", " ", value)
    return len(re.sub(r"\s+", "", plain)) >= 3


def _deduplicate(errors: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(errors))


def _load_body_file(path: Path) -> tuple[str, tuple[str, ...]]:
    document = path.read_text(encoding="utf-8")
    if not document.startswith("---\n"):
        return document, ()
    try:
        _, metadata, body = document.split("---\n", 2)
    except ValueError:
        return document, ()
    labels: list[str] = []
    for line in metadata.splitlines():
        if line.startswith("labels:"):
            labels.extend(
                value.strip()
                for value in line.split(":", 1)[1].split(",")
                if value.strip()
            )
    return body.lstrip(), tuple(labels)


def _audit_legacy_issue_contract(
    body: str,
    labels: Sequence[str],
) -> tuple[str | None, tuple[str, ...]]:
    """Classify the current pre-template issue form without certifying it."""

    errors: list[str] = []
    sections = _sections(_semantic_body(body), level=2)
    required = _require_sections(sections, LEGACY_AUDIT_SECTIONS, errors)
    current_packet = required.get("Current packet")
    values: dict[str, str] = {}
    if current_packet is not None:
        values = _require_fields(
            _declared_fields(current_packet),
            ("Packet ID", "Execution base", "Status", "Delivery contract"),
            errors,
            context="Current packet",
        )

    execution_base = values.get("Execution base", "")
    if execution_base and re.search(r"\b[0-9a-fA-F]{40}\b", execution_base) is None:
        errors.append("Current packet: Execution base must identify a commit SHA")

    delivery_contract = values.get("Delivery contract", "")
    if (
        delivery_contract
        and re.search(
            r"\bone ticket,?[ \t]+one Atomic PR\b",
            delivery_contract,
            re.IGNORECASE,
        )
        is None
    ):
        errors.append(
            "Current packet: Delivery contract must be one ticket, one Atomic PR"
        )

    acceptance = required.get("Acceptance and counterexamples", "")
    if acceptance and re.search(r"(?m)^[ \t]*1\.[ \t]+", acceptance) is None:
        errors.append("Acceptance and counterexamples must contain a numbered oracle")

    normalized_labels = {label.casefold() for label in labels}
    normalized_status = values.get("Status", "").strip().strip("`").casefold()
    classification: str | None = None
    if normalized_status.startswith("ready-for-agent"):
        classification = "conforming-ready"
        if "ready-for-agent" not in normalized_labels:
            errors.append("ready status must agree with the ready-for-agent label")
    elif normalized_status.startswith("blocked"):
        classification = "conforming-blocked-unready"
        if "ready-for-agent" in normalized_labels:
            errors.append("blocked status cannot carry the ready-for-agent label")
    elif normalized_status:
        errors.append("Current packet: Status must be ready-for-agent or blocked")

    return classification, tuple(_deduplicate(errors))


def _run_gh_json(arguments: Sequence[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["gh", *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise RuntimeError("GitHub CLI 'gh' is required for live validation") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"GitHub read failed: {detail}") from exc
    return json.loads(result.stdout)


def _repository_name(repo: str | None) -> str:
    if repo:
        return repo
    result = _run_gh_json(("repo", "view", "--json", "nameWithOwner"))
    return str(result["nameWithOwner"])


def _body_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the repository Atomic ticket-to-PR contract."
    )
    parser.add_argument("--kind", choices=("issue", "pr"))
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--head-sha")
    parser.add_argument("--issue", type=int)
    parser.add_argument("--pr", type=int)
    parser.add_argument("--repo")
    args = parser.parse_args(argv)

    offline = args.kind is not None or args.body_file is not None
    live_count = int(args.issue is not None) + int(args.pr is not None)
    if offline and live_count:
        parser.error(
            "offline --kind/--body-file cannot be combined with --issue or --pr"
        )
    if offline and (args.kind is None or args.body_file is None):
        parser.error("offline validation requires both --kind and --body-file")
    if not offline and live_count != 1:
        parser.error("choose exactly one live target: --issue N or --pr N")
    if args.kind == "pr" and args.head_sha is None:
        parser.error("offline PR validation requires --head-sha")
    if args.kind == "issue" and args.head_sha is not None:
        parser.error("--head-sha applies only to PR validation")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run offline fixture or live read-only delivery-contract validation."""

    args = _parse_args(argv)
    audit_classification: str | None = None
    try:
        if args.kind is not None:
            body, labels = _load_body_file(args.body_file)
            if args.kind == "issue":
                errors = validate_issue_contract(body, labels=labels)
            else:
                errors = validate_pr_contract(body, args.head_sha)
            identity = f"file:{args.body_file} body_sha256={_body_digest(body)}"
        else:
            repository = _repository_name(args.repo)
            if args.issue is not None:
                payload = _run_gh_json(
                    (
                        "issue",
                        "view",
                        str(args.issue),
                        "--repo",
                        repository,
                        "--json",
                        "number,body,labels,state,url",
                    )
                )
                body = str(payload.get("body") or "")
                labels = tuple(
                    str(label.get("name") or "") for label in payload.get("labels", [])
                )
                errors = validate_issue_contract(body, labels=labels)
                legacy_classification, legacy_errors = _audit_legacy_issue_contract(
                    body,
                    labels,
                )
                if errors and legacy_classification is not None and not legacy_errors:
                    errors = ()
                    audit_classification = legacy_classification
                readiness = (
                    "ready"
                    if "ready-for-agent" in {label.casefold() for label in labels}
                    else "blocked-or-unready"
                )
                body_sha = _body_digest(body)
                identity = (
                    f"{repository}#{payload['number']} body_sha256={body_sha} "
                    f"readiness={readiness} url={payload['url']}"
                )
            else:
                payload = _run_gh_json(
                    (
                        "pr",
                        "view",
                        str(args.pr),
                        "--repo",
                        repository,
                        "--json",
                        "number,body,headRefOid,state,url",
                    )
                )
                body = str(payload.get("body") or "")
                head_sha = str(payload.get("headRefOid") or "")
                errors = validate_pr_contract(
                    body,
                    head_sha,
                    expected_repository=repository,
                )
                identity = (
                    f"{repository} PR#{payload['number']} head={head_sha} "
                    f"body_sha256={_body_digest(body)} url={payload['url']}"
                )
    except (OSError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        print(f"FAIL {identity}")
        for error in errors:
            print(f"- {error}")
        return 1
    if audit_classification is not None:
        print(f"AUDIT {identity} contract={audit_classification} canonical=false")
        return 0
    print(f"PASS {identity}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
