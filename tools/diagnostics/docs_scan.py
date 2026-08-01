"""Shared text-file discovery helpers for docs diagnostics."""

from __future__ import annotations

import posixpath
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

LOCAL_PATH_SCAN_EXCLUSIONS = {"tools/diagnostics/docs_management_audit.py"}
LOCAL_PATH_SCAN_FALLBACK_DIRS = (
    "config",
    "docs",
    "tests",
    "tools",
    "scripts",
    ".github",
    ".codex/hooks",
)
LOCAL_PATH_SCAN_TEXT_SUFFIXES = {
    ".cfg",
    ".csv",
    ".env",
    ".example",
    ".htm",
    ".html",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}
LOCAL_PATH_SCAN_TEXT_FILENAMES = {
    ".gitignore",
    "AGENTS.md",
    "README.md",
}


def repo_rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def normalize_repo_rel_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = posixpath.normpath(normalized)
    if (
        normalized in {"", ".", ".."}
        or normalized.startswith(("/", "../"))
        or re.match(r"^[A-Za-z]:/", normalized)
    ):
        raise ValueError(f"path must be repository-relative: {path!r}")
    return normalized


def resolve_repo_path(root: Path, path: str) -> Path:
    """Resolve a repository-relative path without following links outside root."""

    normalized = normalize_repo_rel_path(path)
    resolved_root = root.resolve()
    resolved_path = (resolved_root / normalized).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path resolves outside repository: {path!r}") from exc
    return resolved_path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def git_visible_paths(root: Path) -> tuple[str, ...] | None:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return tuple(
        line.strip().replace("\\", "/")
        for line in completed.stdout.splitlines()
        if line.strip()
    )


def fallback_local_path_scan_paths(root: Path) -> tuple[str, ...]:
    paths: set[str] = set()
    for base in LOCAL_PATH_SCAN_FALLBACK_DIRS:
        base_path = root / base
        if base_path.is_file():
            paths.add(repo_rel(base_path, root))
        elif base_path.exists():
            paths.update(repo_rel(path, root) for path in base_path.rglob("*"))
    for filename in LOCAL_PATH_SCAN_TEXT_FILENAMES:
        if (root / filename).exists():
            paths.add(filename)
    return tuple(sorted(paths))


def is_local_path_scan_target(path: Path, rel_path: str) -> bool:
    if rel_path in LOCAL_PATH_SCAN_EXCLUSIONS:
        return False
    if not path.is_file():
        return False
    if path.name in LOCAL_PATH_SCAN_TEXT_FILENAMES:
        return True
    return path.suffix.lower() in LOCAL_PATH_SCAN_TEXT_SUFFIXES


def markdown_headings(text: str) -> tuple[str, ...]:
    headings: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            headings.append(match.group(1))
    return tuple(headings)


def markdown_heading_fragment(heading: str) -> str:
    fragment = heading.strip().lower()
    fragment = re.sub(r"[^\w\- ]", "", fragment, flags=re.UNICODE)
    fragment = re.sub(r"\s+", "-", fragment)
    return fragment


def markdown_link_targets(text: str) -> tuple[str, ...]:
    targets: list[str] = []
    for raw in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        raw = raw.strip()
        if raw.startswith("<") and ">" in raw:
            target = raw[1 : raw.index(">")]
        else:
            target = raw.split(maxsplit=1)[0]
        if target:
            targets.append(target)
    return tuple(targets)


def html_link_targets(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"href=[\"']([^\"']+)[\"']", text))


def resolve_repo_target(referrer: str, raw_target: str) -> str | None:
    target = raw_target.strip().strip("<>").split("#", 1)[0]
    if not target or "://" in target or target.startswith(("#", "/", "mailto:")):
        return None
    resolved = posixpath.normpath(
        str(PurePosixPath(referrer).parent / PurePosixPath(target))
    )
    try:
        return normalize_repo_rel_path(resolved)
    except ValueError:
        return None


def resolved_markdown_targets(referrer: str, text: str) -> tuple[str, ...]:
    return tuple(
        resolved
        for target in markdown_link_targets(text)
        if (resolved := resolve_repo_target(referrer, target)) is not None
    )


def permanent_root_contract_problems(
    root: Path,
    rel_path: str,
    text: str,
    *,
    overrides: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    from tools.diagnostics.docs_policy import (  # local import avoids a cycle
        PERMANENT_ROOT_DOC_CONTRACTS,
        doc_placement_value,
        repo_owner_value,
    )

    contract = PERMANENT_ROOT_DOC_CONTRACTS.get(rel_path)
    if contract is None:
        return ()
    overrides = overrides or {}
    problems: list[str] = []
    headings = markdown_headings(text)
    for heading in contract["required_headings"]:
        if headings.count(heading) != 1:
            problems.append(f"required heading must occur exactly once: {heading}")
    for marker in contract["required_text"]:
        if text.count(marker) != 1:
            problems.append(
                f"required purpose landmark must occur exactly once: {marker}"
            )

    resolved_routes = resolved_markdown_targets(rel_path, text)
    for route in contract["required_routes"]:
        if resolved_routes.count(route) < 1:
            problems.append(f"required route must resolve: {route}")
        if route not in overrides and not (root / route).exists():
            problems.append(f"required route target is missing: {route}")

    for referrer in contract["required_referrers"]:
        if referrer in overrides:
            referrer_text = overrides[referrer]
        else:
            referrer_path = root / referrer
            if not referrer_path.is_file():
                problems.append(f"required referrer is missing: {referrer}")
                continue
            referrer_text = read_text(referrer_path)
        if resolved_markdown_targets(referrer, referrer_text).count(rel_path) < 1:
            problems.append(f"required referrer must route: {referrer} -> {rel_path}")

    placement = doc_placement_value(text)
    if placement and placement != "formal_repo_doc":
        problems.append(
            "permanent root path has conflicting Doc placement: " + placement
        )
    repo_owner = repo_owner_value(text)
    if repo_owner and repo_owner != contract["owner"]:
        problems.append("permanent root path has conflicting Repo owner: " + repo_owner)
    return tuple(problems)


def normalized_path_dependency_census(
    root: Path,
    target_path: str,
    *,
    scan_paths: Sequence[str] | None = None,
    overrides: Mapping[str, str] | None = None,
    excluded_paths: Sequence[str] = (),
) -> dict[str, object]:
    from tools.diagnostics.docs_policy import is_productization_control_plane_path

    normalized_target = normalize_repo_rel_path(target_path)
    overrides = overrides or {}
    excluded = {normalize_repo_rel_path(path) for path in excluded_paths}
    paths = set(
        scan_paths or git_visible_paths(root) or fallback_local_path_scan_paths(root)
    )
    paths.update(overrides)
    literal: list[str] = []
    markdown: list[dict[str, str]] = []
    html: list[dict[str, str]] = []
    constants: list[str] = []
    control_plane_constant_name = "CONTROL_" + "PLANE_PATH"
    for rel in sorted(paths):
        normalized = normalize_repo_rel_path(rel)
        if normalized == normalized_target or normalized in excluded:
            continue
        path = root / normalized
        if normalized in overrides:
            text = overrides[normalized]
        elif is_local_path_scan_target(path, normalized):
            text = read_text(path)
        else:
            continue
        if normalized_target in text:
            literal.append(normalized)
        if normalized.lower().endswith((".md", ".markdown")):
            for raw_target in markdown_link_targets(text):
                resolved = resolve_repo_target(normalized, raw_target)
                if resolved == normalized_target:
                    markdown.append(
                        {
                            "referrer": normalized,
                            "raw_target": raw_target,
                            "resolved_path": resolved,
                        }
                    )
        if normalized.lower().endswith((".html", ".htm")):
            for raw_target in html_link_targets(text):
                resolved = resolve_repo_target(normalized, raw_target)
                if resolved == normalized_target:
                    html.append(
                        {
                            "referrer": normalized,
                            "raw_target": raw_target,
                            "resolved_path": resolved,
                        }
                    )
        if is_productization_control_plane_path(normalized_target) and (
            control_plane_constant_name in text
        ):
            constants.append(normalized)
    return {
        "literal_path_referrers": sorted(set(literal)),
        "relative_markdown_links": sorted(
            markdown, key=lambda item: (item["referrer"], item["raw_target"])
        ),
        "relative_html_links": sorted(
            html, key=lambda item: (item["referrer"], item["raw_target"])
        ),
        "control_plane_constant_consumers": sorted(set(constants)),
    }


def governance_index_problems(
    root: Path,
    text: str,
    *,
    overrides: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    from tools.diagnostics.docs_policy import (
        GOVERNANCE_MECHANISM_VALUES,
        GOVERNANCE_RULE_FAMILY_CONTRACTS,
    )

    overrides = overrides or {}
    rows: list[list[str]] = []
    table_started = False
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            if table_started:
                break
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == "Rule family":
            table_started = True
            continue
        if not table_started or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    problems: list[str] = []
    row_ids = [row[0] for row in rows]
    expected_ids = list(GOVERNANCE_RULE_FAMILY_CONTRACTS)
    if len(rows) != len(expected_ids) or set(row_ids) != set(expected_ids):
        problems.append(
            "governance index must contain exactly the six declared rule families"
        )
    if len(row_ids) != len(set(row_ids)):
        problems.append("governance index contains duplicate rule-family rows")

    for row in rows:
        if len(row) != 6:
            problems.append(f"governance row {row[0]} must have exactly six cells")
            continue
        rule_id, owner, mechanism, executable, readback, residual = row
        if rule_id not in GOVERNANCE_RULE_FAMILY_CONTRACTS:
            problems.append(f"governance index contains unknown rule family: {rule_id}")
            continue
        expected = GOVERNANCE_RULE_FAMILY_CONTRACTS[rule_id]
        actual = {
            "owner": owner,
            "mechanism": mechanism,
            "executable": executable,
            "readback": readback,
            "residual": residual,
        }
        for field, expected_value in expected.items():
            if actual[field] != expected_value:
                problems.append(
                    f"governance row {rule_id} {field} mismatch: "
                    f"expected {expected_value}, got {actual[field]}"
                )
        if mechanism not in GOVERNANCE_MECHANISM_VALUES:
            problems.append(
                f"governance row {rule_id} has invalid mechanism: {mechanism}"
            )

        owner_path, separator, fragment = owner.partition("#")
        if not separator or not fragment:
            problems.append(f"governance row {rule_id} owner locator is incomplete")
        else:
            owner_text = overrides.get(owner_path)
            if owner_text is None:
                owner_file = root / owner_path
                owner_text = read_text(owner_file) if owner_file.is_file() else None
            if owner_text is None:
                problems.append(
                    f"governance row {rule_id} owner path is missing: {owner_path}"
                )
            else:
                fragments = [
                    markdown_heading_fragment(heading)
                    for heading in markdown_headings(owner_text)
                ]
                if fragments.count(fragment) != 1:
                    problems.append(
                        f"governance row {rule_id} owner anchor is missing or "
                        f"non-unique: {owner}"
                    )
        for field, locator in (("executable", executable), ("readback", readback)):
            if locator == "none":
                continue
            if locator not in overrides and not (root / locator).is_file():
                problems.append(
                    f"governance row {rule_id} {field} locator is missing: {locator}"
                )
        if residual not in {"none", "#166"}:
            problems.append(f"governance row {rule_id} residual must be none or #166")
    return tuple(problems)
