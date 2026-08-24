from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diagnostics import docs_scan as _docs_scan  # noqa: E402
from tools.diagnostics.docs_policy import (  # noqa: E402
    DOC_EXIT_RULE_MARKER,
    DOC_KIND_MARKER,
    DOC_KIND_VALUES,
    DOC_LIFECYCLE_MARKER,
    DOC_LIFECYCLE_VALUES,
    DOC_PLACEMENT_MARKER,
    DOC_PLACEMENT_VALUES,
    DOC_REPO_OWNER_MARKER,
    DOC_RETIRED_PLACEMENT_VALUES,
    DOC_RETIRED_TRACKED_DIRS,
    PERMANENT_ROOT_DOC_CONTRACTS,
    classify_doc,
    classify_doc_path,
    doc_exit_rule_value,
    doc_kind_value,
    doc_lifecycle_requires_exit_rule,
    doc_lifecycle_value,
    doc_placement_is_non_repo,
    doc_placement_requires_repo_owner,
    has_private_history_signal,
    is_invalid_superpowers_spec_payload_path,
    is_markdown_path,
    is_productization_compatibility_index_text,
    is_productization_control_plane_path,
    is_superpowers_spec_lane_path,
)

NEWLY_STAGED_STATUSES = {"A", "C", "R"}
RETIREMENT_EVIDENCE_DIR = "docs/superpowers/file-management/docs-cleanup/"
RETIREMENT_EVIDENCE_TOKENS = ("retirement-evidence", "retirement_evidence")


@dataclass(frozen=True)
class StagedMarkdown:
    status: str
    path: str
    staged_text: str | None = None


@dataclass(frozen=True)
class PlacementProblem:
    path: str
    reason: str
    required_marker: str


@dataclass(frozen=True)
class PlacementResult:
    checked_count: int
    ignored_deletions: int
    problems: tuple[PlacementProblem, ...]


def normalize_status(status: str) -> str:
    return status.strip().upper()[:1]


def parse_name_status(output: str) -> tuple[list[StagedMarkdown], int]:
    entries: list[StagedMarkdown] = []
    ignored_deletions = 0
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = normalize_status(parts[0])
        if len(parts) < 2:
            continue
        if status == "R" and len(parts) >= 3:
            source_path = parts[1]
            if is_markdown_path(source_path):
                entries.append(StagedMarkdown(status="D", path=source_path))
            else:
                ignored_deletions += 1

        path = parts[-1]
        if status == "D":
            if is_markdown_path(path):
                entries.append(StagedMarkdown(status=status, path=path))
            else:
                ignored_deletions += 1
            continue
        if status not in {"A", "M", "R", "C"}:
            continue
        if (
            is_markdown_path(path)
            or is_superpowers_spec_lane_path(path)
            or is_retirement_evidence_path(path)
        ):
            entries.append(StagedMarkdown(status=status, path=path))
    return entries, ignored_deletions


def staged_markdown(
    root: Path,
    *,
    repository_view: _docs_scan.RepositoryTextView | None = None,
) -> tuple[list[StagedMarkdown], int]:
    view = _docs_scan.require_repository_text_view(
        root,
        repository_view or _docs_scan.load_repository_text_view(root),
    )
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "diff",
            "--cached",
            "--name-status",
            "--diff-filter=AMRCD",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
        env=_docs_scan.repository_git_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff --cached failed")
    entries, ignored_deletions = parse_name_status(result.stdout)
    return [
        StagedMarkdown(
            status=entry.status,
            path=entry.path,
            staged_text=(
                None
                if normalize_status(entry.status) == "D"
                else view.read_text(entry.path)
            ),
        )
        for entry in entries
    ], ignored_deletions


def required_marker_text(*placements: str) -> str:
    placement_text = "|".join(placements)
    return (
        f"{DOC_PLACEMENT_MARKER} {placement_text}; "
        f"{DOC_REPO_OWNER_MARKER} <path-or-topic>"
    )


def is_retirement_evidence_path(path: str) -> bool:
    normalized = _docs_scan.normalize_repo_rel_path(path).lower()
    filename = Path(normalized).name
    return (
        normalized.startswith(RETIREMENT_EVIDENCE_DIR)
        and normalized.endswith(".json")
        and any(token in filename for token in RETIREMENT_EVIDENCE_TOKENS)
    )


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        return None
    return value


def _normalized_evidence_path(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _docs_scan.normalize_repo_rel_path(value)
    except ValueError:
        return None


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _staged_text_by_path(entries: Sequence[StagedMarkdown]) -> dict[str, str]:
    return {
        _docs_scan.normalize_repo_rel_path(entry.path): entry.staged_text
        for entry in entries
        if normalize_status(entry.status) != "D" and entry.staged_text is not None
    }


def _deleted_paths(entries: Sequence[StagedMarkdown]) -> frozenset[str]:
    return frozenset(
        _docs_scan.normalize_repo_rel_path(entry.path)
        for entry in entries
        if normalize_status(entry.status) == "D"
    )


def _exact_referrers(
    root: Path,
    rel_path: str,
    entries: Sequence[StagedMarkdown],
    *,
    repository_view: _docs_scan.RepositoryTextView,
) -> tuple[str, ...]:
    normalized = _docs_scan.normalize_repo_rel_path(rel_path)
    if repository_view.source == "git_index":
        return tuple(
            sorted(
                scan_rel
                for scan_rel in repository_view.text_paths
                if scan_rel != normalized
                and not is_retirement_evidence_path(scan_rel)
                and normalized in repository_view.read_text(scan_rel)
            )
        )
    deleted_paths = _deleted_paths(entries)
    staged_text_by_path = _staged_text_by_path(entries)
    scan_paths = set(repository_view.paths)
    scan_paths.update(staged_text_by_path)
    referrers: list[str] = []
    for scan_rel in sorted(scan_paths):
        scan_rel = _docs_scan.normalize_repo_rel_path(scan_rel)
        if scan_rel == normalized or scan_rel in deleted_paths:
            continue
        if is_retirement_evidence_path(scan_rel):
            continue
        scan_path = root / scan_rel
        if scan_rel in staged_text_by_path:
            text = staged_text_by_path[scan_rel]
        else:
            if not _docs_scan.is_local_path_scan_target(scan_path, scan_rel):
                continue
            text = _docs_scan.read_text(scan_path)
        if normalized in text:
            referrers.append(scan_rel)
    return tuple(sorted(referrers))


def read_deleted_text(root: Path, entry: StagedMarkdown) -> str | None:
    if entry.staged_text is not None:
        return entry.staged_text
    staged_path = entry.path.replace("\\", "/")
    result = subprocess.run(
        ["git", "show", f"HEAD:{staged_path}"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _owner_paths_exist(
    root: Path,
    owner_paths: list[str],
    entries: Sequence[StagedMarkdown],
    *,
    repository_view: _docs_scan.RepositoryTextView,
) -> bool:
    if repository_view.source == "git_index":
        return all(
            (normalized := _normalized_evidence_path(owner_path)) is not None
            and repository_view.contains(normalized)
            for owner_path in owner_paths
        )
    staged_text_by_path = _staged_text_by_path(entries)
    for owner_path in owner_paths:
        normalized = _normalized_evidence_path(owner_path)
        if normalized is None:
            return False
        if normalized in staged_text_by_path:
            continue
        try:
            resolved = _docs_scan.resolve_repo_path(root, normalized)
        except ValueError:
            return False
        if not resolved.is_file():
            return False
    return True


def _is_usable_retirement_evidence_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("review_result") != "pass_can_retire":
        return False
    for field_name in (
        "owner_paths",
        "owner_anchors",
        "absorbed_claims",
        "absorbed_negative_claims",
    ):
        values = _string_list(entry.get(field_name))
        if values is None or not values:
            return False
    if entry.get("source_copy_readback_verified") is not True:
        return False
    source_hash = entry.get("source_hash")
    if not isinstance(source_hash, str) or not source_hash.strip():
        return False
    return _string_list(entry.get("active_followups")) == []


def retirement_evidence_by_path(
    entries: Sequence[StagedMarkdown],
) -> dict[str, dict[str, object]]:
    evidence_by_path: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not is_retirement_evidence_path(entry.path):
            continue
        if entry.staged_text is None:
            continue
        try:
            data = json.loads(entry.staged_text)
        except json.JSONDecodeError:
            continue
        evidence_entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(evidence_entries, list):
            continue
        for evidence_entry in evidence_entries:
            if not _is_usable_retirement_evidence_entry(evidence_entry):
                continue
            source_path = evidence_entry.get("source_path")
            normalized = _normalized_evidence_path(source_path)
            if normalized is not None:
                evidence_by_path[normalized] = evidence_entry
    return evidence_by_path


def retirement_evidence_packets_by_path(
    entries: Sequence[StagedMarkdown],
) -> dict[str, list[dict[str, object]]]:
    packets: dict[str, list[dict[str, object]]] = {}
    for staged in entries:
        if not is_retirement_evidence_path(staged.path) or staged.staged_text is None:
            continue
        try:
            data = json.loads(staged.staged_text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        evidence_entries = data.get("entries")
        if not isinstance(evidence_entries, list):
            continue
        for item in evidence_entries:
            if not isinstance(item, dict):
                continue
            source_path = item.get("source_path")
            normalized = _normalized_evidence_path(source_path)
            if normalized is not None:
                packets.setdefault(normalized, []).append(data)
    return packets


def _head_bytes(root: Path, rel_path: str) -> bytes | None:
    return _git_object_bytes(root, "HEAD", rel_path)


def _git_object_bytes(root: Path, ref: str, rel_path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=root,
        check=False,
        capture_output=True,
        timeout=10,
    )
    return result.stdout if result.returncode == 0 else None


def _source_locator_line_range(
    source_path: str,
    source_bytes: bytes,
    locator: object,
) -> tuple[int, int] | None:
    if not isinstance(locator, str):
        return None
    match = re.fullmatch(
        re.escape(source_path) + r":L(\d+)(?:-L(\d+))?",
        locator,
    )
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or start)
    lines = source_bytes.splitlines(keepends=True)
    if start < 1 or end < start or end > len(lines):
        return None
    return start, end


def _hash_source_locator(
    source_path: str,
    source_bytes: bytes,
    locator: object,
) -> str | None:
    line_range = _source_locator_line_range(source_path, source_bytes, locator)
    if line_range is None:
        return None
    start, end = line_range
    lines = source_bytes.splitlines(keepends=True)
    return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()


def _json_pointer_exists_once(data: object, pointer: object) -> bool:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return False
    current = data
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif (
            isinstance(current, list) and token.isdigit() and int(token) < len(current)
        ):
            current = current[int(token)]
        else:
            return False
    return True


def _owner_locator_problem(
    root: Path,
    locator: object,
    staged_text_by_path: dict[str, str],
    *,
    repository_view: _docs_scan.RepositoryTextView | None = None,
) -> str:
    if not isinstance(locator, dict):
        return "owner locator must be a typed object"
    raw_path = locator.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return "owner locator path is missing"
    path = _normalized_evidence_path(raw_path)
    if path is None:
        return "owner locator path must be repository-relative"
    if path in staged_text_by_path:
        text = staged_text_by_path[path]
    elif repository_view is not None:
        _docs_scan.require_repository_text_view(root, repository_view)
        if not repository_view.contains(path):
            return f"owner path is missing: {path}"
        try:
            text = repository_view.read_text(path)
        except _docs_scan.RepositoryViewError:
            return f"owner path is not UTF-8 text: {path}"
    else:
        try:
            owner_path = _docs_scan.resolve_repo_path(root, path)
        except ValueError:
            return "owner locator path must resolve inside repository"
        if not owner_path.is_file():
            return f"owner path is missing: {path}"
        text = owner_path.read_text(encoding="utf-8", errors="strict")

    kind = locator.get("kind")
    if kind == "markdown_heading":
        heading = locator.get("heading")
        if (
            not isinstance(heading, str)
            or _docs_scan.markdown_headings(text).count(heading) != 1
        ):
            return f"owner Markdown heading is missing or non-unique: {path}#{heading}"
        return ""
    if kind in {"tsv_column", "tsv_key"}:
        reader = csv.DictReader(text.splitlines(), delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        column = locator.get("column")
        if not isinstance(column, str) or fieldnames.count(column) != 1:
            return f"owner TSV column is missing or non-unique: {path}::{column}"
        if kind == "tsv_key":
            value = locator.get("value")
            rows = list(reader)
            if (
                not isinstance(value, str)
                or sum(row.get(column) == value for row in rows) != 1
            ):
                return (
                    f"owner TSV key is missing or non-unique: {path}::{column}={value}"
                )
        return ""
    if kind == "json_pointer":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return f"owner JSON is invalid: {path}"
        if not _json_pointer_exists_once(data, locator.get("pointer")):
            return f"owner JSON pointer is missing: {path}#{locator.get('pointer')}"
        return ""
    return f"unsupported owner locator kind: {kind}"


def same_path_replacement_problems(
    root: Path,
    entry: StagedMarkdown,
    *,
    packet_candidates: dict[str, list[dict[str, object]]],
    entries: Sequence[StagedMarkdown],
    source_bytes_override: bytes | None = None,
    validate_dependencies: bool = True,
    repository_view: _docs_scan.RepositoryTextView | None = None,
) -> list[PlacementProblem]:
    path = _docs_scan.normalize_repo_rel_path(entry.path)
    problems: list[PlacementProblem] = []

    def add(reason: str) -> None:
        problems.append(
            PlacementProblem(
                path=path,
                reason=f"invalid same-path retirement transaction: {reason}",
                required_marker=(
                    "Stage one completed body_to_compatibility_index evidence "
                    "packet with exact source/readback hashes, claim mappings, "
                    "owner locators, and normalized dependencies."
                ),
            )
        )

    packets = packet_candidates.get(path, [])
    if len(packets) != 1:
        add(f"expected exactly one evidence packet, found {len(packets)}")
        return problems
    packet = packets[0]
    packet_entries = [
        item
        for item in packet.get("entries", [])
        if isinstance(item, dict)
        and _normalized_evidence_path(item.get("source_path")) == path
    ]
    if len(packet_entries) != 1:
        add(f"expected exactly one source mapping, found {len(packet_entries)}")
        return problems
    evidence = packet_entries[0]
    if (
        packet.get("transaction") != "body_to_compatibility_index"
        or evidence.get("transaction") != "body_to_compatibility_index"
    ):
        add("transaction must be body_to_compatibility_index")
    if packet.get("transaction_status") != "completed":
        add("transaction_status must be completed")
    if (
        packet.get("review_result") != "pass_can_retire"
        or evidence.get("review_result") != "pass_can_retire"
    ):
        add("review_result must be pass_can_retire")
    if (
        packet.get("source_copy_readback_verified") is not True
        or evidence.get("source_copy_readback_verified") is not True
    ):
        add("private source-copy readback is not verified")
    if (
        packet.get("source_copy_locator_readback_verified") is not True
        or evidence.get("source_copy_locator_readback_verified") is not True
    ):
        add("private source-copy locator readback is not verified")
    for label, mapping in (("packet", packet), ("source mapping", evidence)):
        for field, remaining_message in (
            ("active_followups", "active followups remain"),
            ("unresolved_claims", "unresolved claims remain"),
        ):
            values = _string_list(mapping.get(field))
            if values is None:
                add(f"{label} {field} must be a string list")
            elif values:
                add(f"{label} {remaining_message}")

    source_bytes = (
        source_bytes_override
        if source_bytes_override is not None
        else _head_bytes(root, path)
    )
    if source_bytes is None:
        add("source preimage is missing from HEAD")
        return problems
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    source_lines = source_bytes.splitlines(keepends=True)
    if packet.get("source_line_count") != len(source_lines):
        add("source_line_count does not match the HEAD preimage")
    if (
        packet.get("source_hash") != source_hash
        or evidence.get("source_hash") != source_hash
    ):
        add("source_hash does not match the HEAD preimage")
    if (
        packet.get("source_copy_hash") != source_hash
        or evidence.get("source_copy_hash") != source_hash
    ):
        add("source-copy hash does not match the preimage")
    replacement_text = entry.staged_text or ""
    replacement_hash = hashlib.sha256(replacement_text.encode("utf-8")).hexdigest()
    if (
        packet.get("replacement_hash") != replacement_hash
        or evidence.get("replacement_hash") != replacement_hash
    ):
        add("replacement_hash does not match staged compatibility index")

    sections = packet.get("source_sections")
    claims = packet.get("claims")
    if not isinstance(sections, list) or packet.get("source_section_count") != len(
        sections
    ):
        add("source section census is missing or incomplete")
        sections = []
    if not isinstance(claims, list):
        add("claim census is missing")
        claims = []
    section_ids = [
        item.get("section_id") for item in sections if isinstance(item, dict)
    ]
    if len(section_ids) != len(sections) or len(set(section_ids)) != len(section_ids):
        add("source section IDs are missing or duplicated")
    claim_ids = [item.get("claim_id") for item in claims if isinstance(item, dict)]
    if len(claim_ids) != len(claims) or len(set(claim_ids)) != len(claim_ids):
        add("claim IDs are missing or duplicated")
    allowed_dispositions = {
        "absorb",
        "structured_owner_already_authoritative",
        "historical_only",
    }
    staged_text_by_path = _staged_text_by_path(entries)
    section_claim_edges: set[tuple[str, str]] = set()
    section_ranges: list[tuple[int, int]] = []
    for section in sections:
        if not isinstance(section, dict):
            add("source section entry must be an object")
            continue
        section_id = section.get("section_id")
        if section.get("disposition") not in allowed_dispositions:
            add(f"section {section_id} has unresolved or invalid disposition")
        mapped_claims = section.get("claim_ids")
        if (
            not isinstance(mapped_claims, list)
            or not mapped_claims
            or any(claim_id not in claim_ids for claim_id in mapped_claims)
        ):
            add(f"section {section_id} has invalid claim mapping")
        if isinstance(section_id, str) and isinstance(mapped_claims, list):
            section_claim_edges.update(
                (section_id, claim_id)
                for claim_id in mapped_claims
                if isinstance(claim_id, str)
            )
        actual_section_hash = _hash_source_locator(
            path,
            source_bytes,
            section.get("source_locator"),
        )
        if actual_section_hash != section.get("section_sha256"):
            add(f"section {section_id} source locator/hash mismatch")
        line_range = _source_locator_line_range(
            path,
            source_bytes,
            section.get("source_locator"),
        )
        if line_range is not None:
            section_ranges.append(line_range)
    next_line = 1
    ranges_are_exact = len(section_ranges) == len(sections)
    for start, end in sorted(section_ranges):
        if start != next_line:
            ranges_are_exact = False
            continue
        next_line = end + 1
    if not ranges_are_exact or next_line != len(source_lines) + 1:
        add(
            "source section locators must exactly cover every HEAD preimage line "
            "once without gaps or overlaps"
        )
    claim_section_edges: set[tuple[str, str]] = set()
    for claim in claims:
        if not isinstance(claim, dict):
            add("claim entry must be an object")
            continue
        claim_id = claim.get("claim_id")
        if claim.get("disposition") not in allowed_dispositions:
            add(f"claim {claim_id} has unresolved or invalid disposition")
        summary = claim.get("summary")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or summary.strip().lower()
            in {
                "placeholder",
                "todo",
                "claim is present in owner",
            }
        ):
            add(f"claim {claim_id} has placeholder semantics")
        claim_sections = claim.get("source_section_ids")
        if (
            not isinstance(claim_sections, list)
            or not claim_sections
            or any(section_id not in section_ids for section_id in claim_sections)
        ):
            add(f"claim {claim_id} has invalid source section mapping")
        if isinstance(claim_id, str) and isinstance(claim_sections, list):
            claim_section_edges.update(
                (section_id, claim_id)
                for section_id in claim_sections
                if isinstance(section_id, str)
            )
        actual_excerpt_hash = _hash_source_locator(
            path,
            source_bytes,
            claim.get("source_locator"),
        )
        if actual_excerpt_hash != claim.get("source_excerpt_sha256"):
            add(f"claim {claim_id} source locator/hash mismatch")
        locator_problem = _owner_locator_problem(
            root,
            claim.get("owner_locator"),
            staged_text_by_path,
            repository_view=repository_view,
        )
        if locator_problem:
            add(f"claim {claim_id}: {locator_problem}")

    if section_claim_edges != claim_section_edges:
        add("section/claim mapping edges are not exactly reciprocal")

    entry_claim_ids = _string_list(evidence.get("claim_ids"))
    if entry_claim_ids is None:
        add("source mapping claim_ids must be a string list")
    elif len(entry_claim_ids) != len(set(entry_claim_ids)) or set(
        entry_claim_ids
    ) != set(claim_ids):
        add("source mapping claim_ids are duplicated or incomplete")
    expected_absorbed = {
        claim.get("claim_id")
        for claim in claims
        if isinstance(claim, dict)
        and claim.get("disposition") != "historical_only"
        and claim.get("polarity") == "positive"
        and isinstance(claim.get("claim_id"), str)
    }
    expected_absorbed_negative = {
        claim.get("claim_id")
        for claim in claims
        if isinstance(claim, dict)
        and claim.get("disposition") != "historical_only"
        and claim.get("polarity") == "negative"
        and isinstance(claim.get("claim_id"), str)
    }
    for claim in claims:
        if isinstance(claim, dict) and claim.get("polarity") not in {
            "positive",
            "negative",
        }:
            add(f"claim {claim.get('claim_id')} has invalid polarity")
    for label, mapping in (("packet", packet), ("source mapping", evidence)):
        absorbed = _string_list(mapping.get("absorbed_claims"))
        absorbed_negative = _string_list(mapping.get("absorbed_negative_claims"))
        if absorbed is None:
            add(f"{label} absorbed_claims must be a string list")
        elif len(absorbed) != len(set(absorbed)) or set(absorbed) != expected_absorbed:
            add(f"{label} absorbed_claims do not exactly match active positive claims")
        if absorbed_negative is None:
            add(f"{label} absorbed_negative_claims must be a string list")
        elif (
            len(absorbed_negative) != len(set(absorbed_negative))
            or set(absorbed_negative) != expected_absorbed_negative
        ):
            add(
                f"{label} absorbed_negative_claims do not exactly match "
                "active negative claims"
            )

    if validate_dependencies:
        evidence_path = ""
        for item in entries:
            if not is_retirement_evidence_path(item.path) or item.staged_text is None:
                continue
            try:
                candidate = json.loads(item.staged_text)
            except json.JSONDecodeError:
                continue
            if candidate == packet:
                evidence_path = _docs_scan.normalize_repo_rel_path(item.path)
                break
        census = _docs_scan.normalized_path_dependency_census(
            root,
            path,
            scan_paths=tuple(
                set(
                    (
                        repository_view
                        or _docs_scan.load_repository_text_view(
                            root,
                            allow_filesystem_fallback=True,
                        )
                    ).paths
                )
                | set(staged_text_by_path)
            ),
            overrides=staged_text_by_path,
            excluded_paths=(evidence_path,) if evidence_path else (),
            repository_view=repository_view,
        )
        if packet.get("dependencies") != census:
            add(
                "normalized dependency/referrer census changed or has "
                "residual omissions"
            )
    return problems


def deletion_retirement_problem(
    root: Path,
    entry: StagedMarkdown,
    *,
    evidence_by_path: dict[str, dict[str, object]],
    entries: Sequence[StagedMarkdown],
    repository_view: _docs_scan.RepositoryTextView,
) -> PlacementProblem | None:
    path = _docs_scan.normalize_repo_rel_path(entry.path)
    evidence = evidence_by_path.get(path)
    if evidence is None:
        return PlacementProblem(
            path=path,
            reason="lifecycle-managed doc deletion requires staged retirement evidence",
            required_marker=(
                "Stage docs/superpowers/file-management/docs-cleanup/"
                "*retirement-evidence*.json with a pass_can_retire entry "
                "for this source_path."
            ),
        )

    old_text = read_deleted_text(root, entry)
    if old_text is None:
        return PlacementProblem(
            path=path,
            reason="deleted lifecycle-managed doc blob could not be read",
            required_marker=(
                "Run the deletion from a git worktree with the source in HEAD."
            ),
        )
    if evidence.get("source_hash") != _source_hash(old_text):
        return PlacementProblem(
            path=path,
            reason="retirement evidence source_hash mismatch",
            required_marker="Regenerate evidence from the exact deleted source text.",
        )

    owner_paths = _string_list(evidence.get("owner_paths"))
    if owner_paths is None or not owner_paths or not _owner_paths_exist(
        root,
        owner_paths,
        entries,
        repository_view=repository_view,
    ):
        return PlacementProblem(
            path=path,
            reason="retirement evidence owner_paths do not all exist in repo",
            required_marker=(
                "Point owner_paths at existing/staged repo owner documents."
            ),
        )

    referrers = _exact_referrers(
        root,
        path,
        entries,
        repository_view=repository_view,
    )
    raw_evidence_referrers = _string_list(evidence.get("exact_referrers"))
    if raw_evidence_referrers is None:
        return PlacementProblem(
            path=path,
            reason="retirement evidence exact_referrers must be a string list",
            required_marker="Regenerate evidence with an exact string-list census.",
        )
    evidence_referrers = tuple(sorted(raw_evidence_referrers))
    if referrers:
        return PlacementProblem(
            path=path,
            reason="exact repo referrers require retargeting before deletion",
            required_marker=(
                "Retarget repo referrers to the durable owner before deletion."
            ),
        )
    if evidence_referrers != tuple(sorted(referrers)):
        return PlacementProblem(
            path=path,
            reason="retirement evidence exact_referrers mismatch",
            required_marker="Regenerate evidence after the final staged referrer scan.",
        )
    return None


def lifecycle_metadata_problems(path: str, text: str) -> list[PlacementProblem]:
    problems: list[PlacementProblem] = []
    doc_kind = doc_kind_value(text)
    lifecycle = doc_lifecycle_value(text)
    exit_rule = doc_exit_rule_value(text)
    if not doc_kind:
        problems.append(
            PlacementProblem(
                path=path,
                reason="lifecycle-managed doc is missing Doc kind",
                required_marker=(
                    f"{DOC_KIND_MARKER} <one of {', '.join(sorted(DOC_KIND_VALUES))}>"
                ),
            )
        )
    elif doc_kind not in DOC_KIND_VALUES:
        problems.append(
            PlacementProblem(
                path=path,
                reason=f"unknown doc kind value: {doc_kind}",
                required_marker=(
                    f"{DOC_KIND_MARKER} <one of {', '.join(sorted(DOC_KIND_VALUES))}>"
                ),
            )
        )
    if not lifecycle:
        problems.append(
            PlacementProblem(
                path=path,
                reason="lifecycle-managed doc is missing Doc lifecycle",
                required_marker=(
                    f"{DOC_LIFECYCLE_MARKER} <one of "
                    f"{', '.join(sorted(DOC_LIFECYCLE_VALUES))}>"
                ),
            )
        )
    elif lifecycle not in DOC_LIFECYCLE_VALUES:
        problems.append(
            PlacementProblem(
                path=path,
                reason=f"unknown doc lifecycle value: {lifecycle}",
                required_marker=(
                    f"{DOC_LIFECYCLE_MARKER} <one of "
                    f"{', '.join(sorted(DOC_LIFECYCLE_VALUES))}>"
                ),
            )
        )
    elif doc_lifecycle_requires_exit_rule(lifecycle) and not exit_rule:
        problems.append(
            PlacementProblem(
                path=path,
                reason=f"{lifecycle} lifecycle requires Doc exit rule",
                required_marker=(
                    f"{DOC_EXIT_RULE_MARKER} <closeout, promotion, retirement, "
                    "or Obsidian migration condition>"
                ),
            )
        )
    return problems


def check_entry(
    root: Path,
    entry: StagedMarkdown,
    *,
    evidence_by_path: dict[str, dict[str, object]] | None = None,
    packet_candidates: dict[str, list[dict[str, object]]] | None = None,
    entries: Sequence[StagedMarkdown] = (),
    repository_view: _docs_scan.RepositoryTextView,
) -> list[PlacementProblem]:
    path = entry.path.replace("\\", "/")
    status = normalize_status(entry.status)
    path_classification = classify_doc_path(path)
    if status == "D":
        if not (
            is_markdown_path(path)
            and path_classification.is_repo_doc
            and (
                path_classification.is_lifecycle_managed
                or path_classification.is_retired_tracked_dir
            )
        ):
            return []
        if path_classification.is_retired_tracked_dir:
            referrers = _exact_referrers(
                root,
                path,
                entries,
                repository_view=repository_view,
            )
            if not referrers:
                return []
            return [
                PlacementProblem(
                    path=path,
                    reason=(
                        "retired docs lane deletion still has exact repo "
                        "referrers: " + ", ".join(referrers)
                    ),
                    required_marker=(
                        "Retarget every exact repo referrer to a durable owner "
                        "before deleting this disposable construction record."
                    ),
                )
            ]
        problem = deletion_retirement_problem(
            root,
            entry,
            evidence_by_path=evidence_by_path or {},
            entries=entries,
            repository_view=repository_view,
        )
        return [] if problem is None else [problem]

    if is_invalid_superpowers_spec_payload_path(path):
        return [
            PlacementProblem(
                path=path,
                reason=(
                    "docs/superpowers/specs is retired; move JSON contracts "
                    "to docs/superpowers/schemas and non-doc artifacts to "
                    "validation or ignored output"
                ),
                required_marker=(
                    "Use GitHub Issues for construction specs and canonical "
                    "repo owners for durable contracts."
                ),
            )
        ]
    if not path_classification.is_repo_doc:
        return []

    try:
        text = (
            entry.staged_text
            if entry.staged_text is not None
            else repository_view.read_text(path)
        )
    except _docs_scan.RepositoryViewError as exc:
        return [
            PlacementProblem(
                path=path,
                reason=f"staged Markdown file could not be read: {exc}",
                required_marker=required_marker_text(
                    "formal_repo_doc",
                    "repo_support_doc",
                ),
            )
        ]

    classification = classify_doc(path, text)
    problems: list[PlacementProblem] = []
    if is_productization_control_plane_path(path) and status == "M":
        head_text = read_deleted_text(root, StagedMarkdown("D", path)) or ""
        if not is_productization_compatibility_index_text(head_text):
            problems.extend(
        same_path_replacement_problems(
            root,
            entry,
            packet_candidates=packet_candidates or {},
            entries=entries,
            repository_view=repository_view,
        )
            )
    if path in PERMANENT_ROOT_DOC_CONTRACTS:
        overrides = _staged_text_by_path(entries)
        overrides[path] = text
        for reason in _docs_scan.permanent_root_contract_problems(
            root,
            path,
            text,
            overrides=overrides,
            repository_view=repository_view,
        ):
            problems.append(
                PlacementProblem(
                    path=path,
                    reason=f"invalid permanent root contract: {reason}",
                    required_marker=(
                        "Restore the exact purpose, self-owner, and routing "
                        "landmarks declared in docs/project-layout.md."
                    ),
                )
            )
    if path == "docs/agent/codex-operating-system.md":
        overrides = _staged_text_by_path(entries)
        overrides[path] = text
        for reason in _docs_scan.governance_index_problems(
            root,
            text,
            overrides=overrides,
        ):
            problems.append(
                PlacementProblem(
                    path=path,
                    reason=f"invalid governance enforcement index: {reason}",
                    required_marker=(
                        "Restore the exact six-row governance read-back contract "
                        "and resolvable owner/executable/fixture locators."
                    ),
                )
            )
    placement = classification.placement
    repo_owner = classification.repo_owner
    lifecycle_problems: list[PlacementProblem] = []
    if status in NEWLY_STAGED_STATUSES and classification.is_lifecycle_managed:
        lifecycle_problems = lifecycle_metadata_problems(path, text)

    if classification.is_docs_root_scatter:
        problems.append(
            PlacementProblem(
                path=path,
                reason=(
                    "docs root only allows cross-cutting authority docs; "
                    "move this file under docs/agent, docs/product, "
                    "docs/architecture, docs/validation, docs/user, or "
                    "docs/superpowers"
                ),
                required_marker=(
                    "Use docs/project-layout.md root allowlist; do not add "
                    "new non-allowlisted Markdown directly under docs/."
                ),
            )
        )

    if classification.is_retired_tracked_dir and status in NEWLY_STAGED_STATUSES:
        reason = DOC_RETIRED_TRACKED_DIRS.get(
            classification.retired_tracked_dir,
            "retired docs lane is not a tracked destination",
        )
        problems.append(
            PlacementProblem(
                path=path,
                reason=f"retired docs lane: {reason}",
                required_marker=(
                    "Route to the canonical owner, a named public artifact "
                    "lane, ignored output/, ignored handoff, or Obsidian."
                ),
            )
        )

    if placement in DOC_RETIRED_PLACEMENT_VALUES:
        problems.append(
            PlacementProblem(
                path=path,
                reason=(
                    f"{placement} is retired migration vocabulary and cannot "
                    "be added or refreshed as tracked documentation"
                ),
                required_marker=(
                    "Route active work to a GitHub Issue or ignored handoff, "
                    "closeout to the PR body, and durable claims to an owner."
                ),
            )
        )

    elif placement and placement not in DOC_PLACEMENT_VALUES:
        problems.append(
            PlacementProblem(
                path=path,
                reason=f"unknown doc placement value: {placement}",
                required_marker=(
                    f"{DOC_PLACEMENT_MARKER} <one of "
                    f"{', '.join(sorted(DOC_PLACEMENT_VALUES))}>"
                ),
            )
        )
        return [*problems, *lifecycle_problems]

    if placement and doc_placement_is_non_repo(placement):
        problems.append(
            PlacementProblem(
                path=path,
                reason=(
                    f"{placement} is not a repo-trackable placement; write it "
                    "to Obsidian staged draft or ignored scratch instead"
                ),
                required_marker=required_marker_text("formal_repo_doc"),
            )
        )

    if placement and doc_placement_requires_repo_owner(placement) and not repo_owner:
        problems.append(
            PlacementProblem(
                path=path,
                reason=f"{placement} requires a repo owner",
                required_marker=f"{DOC_REPO_OWNER_MARKER} <path-or-topic>",
            )
        )

    if classification.is_canonical_owner:
        if path.startswith("docs/user/"):
            if placement and placement != "formal_repo_doc":
                problems.append(
                    PlacementProblem(
                        path=path,
                        reason=(
                            f"{placement} is not valid in docs/user; user docs "
                            "must be public user guides, not retired migration, "
                            "closeout, or artifact records"
                        ),
                        required_marker=(
                            f"{DOC_PLACEMENT_MARKER} formal_repo_doc "
                            "or omit the marker for canonical user guides"
                        ),
                    )
                )
            if has_private_history_signal(text):
                problems.append(
                    PlacementProblem(
                        path=path,
                        reason=(
                            "user guide contains private-history signals; "
                            "move implementation diary, command transcript, "
                            "or review rationale to Obsidian"
                        ),
                        required_marker=required_marker_text("formal_repo_doc"),
                    )
                )
        return [*problems, *lifecycle_problems]

    if problems:
        return [*problems, *lifecycle_problems]

    if classification.is_canonical_owner:
        return []

    if status in NEWLY_STAGED_STATUSES and not placement:
        if classification.is_high_risk_repo_doc:
            problems.append(
                PlacementProblem(
                    path=path,
                    reason=(
                        "new high-risk docs path lacks placement marker and repo owner"
                    ),
                    required_marker=required_marker_text(
                        "formal_repo_doc",
                        "repo_support_doc",
                    ),
                )
            )
        else:
            problems.append(
                PlacementProblem(
                    path=path,
                    reason=(
                        "new repo doc outside canonical owner path lacks "
                        "placement marker"
                    ),
                    required_marker=required_marker_text(
                        "formal_repo_doc",
                        "repo_support_doc",
                    ),
                )
            )
    elif (
        status == "M"
        and not placement
        and classification.is_high_risk_repo_doc
        and has_private_history_signal(text)
    ):
        problems.append(
            PlacementProblem(
                path=path,
                reason=(
                    "modified high-risk docs path looks like private history "
                    "but lacks placement marker"
                ),
                required_marker=required_marker_text(
                    "formal_repo_doc",
                    "repo_support_doc",
                ),
            )
        )

    return [*problems, *lifecycle_problems]


def check_doc_placement(
    root: Path,
    entries: Sequence[StagedMarkdown],
    *,
    ignored_deletions: int = 0,
    repository_view: _docs_scan.RepositoryTextView | None = None,
) -> PlacementResult:
    view = _docs_scan.require_repository_text_view(
        root,
        repository_view
        or _docs_scan.load_repository_text_view(
            root,
            allow_filesystem_fallback=True,
        ),
    )
    problems: list[PlacementProblem] = []
    checked_count = 0
    skipped_deletions = ignored_deletions
    evidence_by_path = retirement_evidence_by_path(entries)
    packet_candidates = retirement_evidence_packets_by_path(entries)
    for entry in entries:
        status = normalize_status(entry.status)
        if status == "D":
            path = entry.path.replace("\\", "/")
            path_classification = classify_doc_path(path)
            if not (
                is_markdown_path(path)
                and path_classification.is_repo_doc
                and (
                    path_classification.is_lifecycle_managed
                    or path_classification.is_retired_tracked_dir
                )
            ):
                skipped_deletions += 1
                continue
            checked_count += 1
            problems.extend(
                check_entry(
                    root,
                    entry,
                    evidence_by_path=evidence_by_path,
                    packet_candidates=packet_candidates,
                    entries=entries,
                    repository_view=view,
                )
            )
            continue
        if is_retirement_evidence_path(entry.path):
            continue
        if not (
            is_markdown_path(entry.path) or is_superpowers_spec_lane_path(entry.path)
        ):
            continue
        checked_count += 1
        problems.extend(
            check_entry(
                root,
                entry,
                evidence_by_path=evidence_by_path,
                packet_candidates=packet_candidates,
                entries=entries,
                repository_view=view,
            )
        )
    return PlacementResult(
        checked_count=checked_count,
        ignored_deletions=skipped_deletions,
        problems=tuple(problems),
    )


def format_result(result: PlacementResult) -> str:
    if not result.problems:
        return (
            "docs placement guard passed: "
            f"{result.checked_count} staged Markdown file(s) checked; "
            f"{result.ignored_deletions} non-lifecycle deletion(s) ignored."
        )
    lines = ["docs placement guard failed:"]
    for problem in result.problems:
        lines.append(f"- {problem.path}: {problem.reason}")
        lines.append(f"  required: {problem.required_marker}")
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged",
        action="store_true",
        help=(
            "Check staged A/M/R/C Markdown files and lifecycle-managed "
            "Markdown deletions."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Repository root. Defaults to the current checkout.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.staged:
        raise SystemExit("docs_placement_guard.py requires --staged")
    root = args.root.resolve()
    repository_view = _docs_scan.load_repository_text_view(root)
    entries, ignored_deletions = staged_markdown(
        root,
        repository_view=repository_view,
    )
    result = check_doc_placement(
        root,
        entries,
        ignored_deletions=ignored_deletions,
        repository_view=repository_view,
    )
    print(format_result(result))
    return 1 if result.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
