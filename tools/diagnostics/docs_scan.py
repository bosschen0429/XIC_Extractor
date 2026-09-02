"""Shared text-file discovery helpers for docs diagnostics."""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

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


class RepositoryViewError(RuntimeError):
    """Raised when one authoritative repository snapshot cannot be constructed."""


@dataclass(frozen=True)
class RepositoryIndexEntry:
    path: str
    mode: str
    oid: str
    stage: int


@dataclass(frozen=True, eq=False)
class RepositoryTextView:
    """Immutable path and UTF-8 text view of one Git-index or fixture snapshot."""

    root: Path
    source: str
    entries: Mapping[str, RepositoryIndexEntry]
    texts: Mapping[str, str]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(self.entries)

    @property
    def text_paths(self) -> tuple[str, ...]:
        return tuple(self.texts)

    def contains(self, path: str) -> bool:
        return normalize_repo_rel_path(path) in self.entries

    def contains_path_or_prefix(self, path: str) -> bool:
        normalized = normalize_repo_rel_path(path)
        prefix = normalized.rstrip("/") + "/"
        return normalized in self.entries or any(
            candidate.startswith(prefix) for candidate in self.entries
        )

    def read_text(self, path: str) -> str:
        normalized = normalize_repo_rel_path(path)
        if normalized not in self.entries:
            raise RepositoryViewError(
                f"repository snapshot path is missing: {normalized}"
            )
        try:
            return self.texts[normalized]
        except KeyError as exc:
            raise RepositoryViewError(
                f"repository snapshot path is not available as UTF-8 text: {normalized}"
            ) from exc


_OWNED_REPOSITORY_TEXT_VIEWS: weakref.WeakSet[RepositoryTextView] = weakref.WeakSet()


def _register_repository_text_view(view: RepositoryTextView) -> RepositoryTextView:
    _OWNED_REPOSITORY_TEXT_VIEWS.add(view)
    return view


def require_repository_text_view(
    root: Path,
    view: RepositoryTextView,
) -> RepositoryTextView:
    """Reject caller-fabricated or cross-root repository authority."""

    if view not in _OWNED_REPOSITORY_TEXT_VIEWS:
        raise RepositoryViewError("repository view must be owner-created")
    expected_root = root.resolve()
    if view.root != expected_root:
        raise RepositoryViewError(
            "repository view root mismatch: "
            f"expected={expected_root}, actual={view.root}"
        )
    return view


def repository_git_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_")
    }


def _parse_index_entries(data: bytes) -> dict[str, RepositoryIndexEntry]:
    if data and not data.endswith(b"\0"):
        raise RepositoryViewError("malformed Git index record")
    entries: dict[str, RepositoryIndexEntry] = {}
    for raw_record in data.split(b"\0"):
        if not raw_record:
            continue
        try:
            metadata, raw_path = raw_record.split(b"\t", 1)
            mode_bytes, oid_bytes, stage_bytes = metadata.split(b" ")
            mode = mode_bytes.decode("ascii")
            oid = oid_bytes.decode("ascii")
            stage = int(stage_bytes.decode("ascii"))
            path = normalize_repo_rel_path(raw_path.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RepositoryViewError("malformed Git index record") from exc
        if not re.fullmatch(r"[0-7]{6}", mode) or not re.fullmatch(
            r"[0-9a-fA-F]+", oid
        ):
            raise RepositoryViewError("malformed Git index record")
        if stage != 0:
            raise RepositoryViewError(
                f"Git index contains non-zero stage {stage}: {path}"
            )
        if path in entries:
            raise RepositoryViewError(
                f"Git index contains duplicate normalized path: {path}"
            )
        entries[path] = RepositoryIndexEntry(path, mode, oid.lower(), stage)
    return entries


def _parse_batch_blobs(
    data: bytes,
    requested_oids: Sequence[str],
) -> dict[str, bytes]:
    offset = 0
    blobs: dict[str, bytes] = {}
    for requested_oid in requested_oids:
        header_end = data.find(b"\n", offset)
        if header_end < 0:
            raise RepositoryViewError("malformed git cat-file batch response")
        try:
            header = data[offset:header_end].decode("ascii")
            returned_oid, object_type, raw_size = header.split(" ")
            size = int(raw_size)
        except (UnicodeDecodeError, ValueError) as exc:
            raise RepositoryViewError(
                "malformed git cat-file batch response"
            ) from exc
        if size < 0:
            raise RepositoryViewError("malformed git cat-file batch byte count")
        if returned_oid.lower() != requested_oid.lower() or object_type != "blob":
            raise RepositoryViewError(
                f"git cat-file returned an unexpected object for {requested_oid}"
            )
        content_start = header_end + 1
        content_end = content_start + size
        if content_end >= len(data) or data[content_end : content_end + 1] != b"\n":
            raise RepositoryViewError("malformed git cat-file batch byte count")
        blobs[requested_oid.lower()] = data[content_start:content_end]
        offset = content_end + 1
    if data[offset:]:
        raise RepositoryViewError("git cat-file batch returned trailing data")
    return blobs


def _has_git_marker_at_or_above(root: Path) -> bool:
    resolved = root.resolve()
    candidates = (resolved, *resolved.parents)
    return any((candidate / ".git").exists() for candidate in candidates)


def _filesystem_repository_text_view(
    root: Path,
    *,
    required_text_paths: Sequence[str],
) -> RepositoryTextView:
    required = {normalize_repo_rel_path(path) for path in required_text_paths}
    paths = tuple(
        sorted(
            repo_rel(path, root)
            for path in root.rglob("*")
            if path.is_file()
        )
    )
    entries = {
        path: RepositoryIndexEntry(path, "filesystem", "filesystem", 0)
        for path in paths
    }
    texts: dict[str, str] = {}
    for path in paths:
        if path not in required and not is_local_path_scan_rel_path(path):
            continue
        try:
            texts[path] = (root / path).read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RepositoryViewError(
                f"repository snapshot text is not UTF-8: {path}"
            ) from exc
    return _register_repository_text_view(
        RepositoryTextView(
            root=root.resolve(),
            source="filesystem_fallback",
            entries=MappingProxyType(entries),
            texts=MappingProxyType(texts),
        )
    )


def load_repository_text_view(
    root: Path,
    *,
    required_text_paths: Sequence[str] = (),
    allow_filesystem_fallback: bool = False,
) -> RepositoryTextView:
    """Load one owner-created result-tree view without mixing worktree bytes."""

    requested_root = root.resolve()
    env = repository_git_env()
    try:
        top_level = subprocess.run(
            ["git", "-C", str(requested_root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        if allow_filesystem_fallback and not _has_git_marker_at_or_above(
            requested_root
        ):
            return _filesystem_repository_text_view(
                requested_root,
                required_text_paths=required_text_paths,
            )
        raise RepositoryViewError(
            f"Git worktree root cannot be resolved: {requested_root}"
        ) from exc
    git_root = Path(top_level.stdout.strip()).resolve()
    if git_root != requested_root:
        if allow_filesystem_fallback and not _has_git_marker_at_or_above(
            requested_root
        ):
            return _filesystem_repository_text_view(
                requested_root,
                required_text_paths=required_text_paths,
            )
        raise RepositoryViewError(
            "repository audit root must equal the Git worktree root: "
            f"root={requested_root}, git_top_level={git_root}"
        )

    try:
        listed = subprocess.run(
            [
                "git",
                "-C",
                str(requested_root),
                "ls-files",
                "--cached",
                "--stage",
                "-z",
            ],
            check=True,
            capture_output=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RepositoryViewError("Git index cannot be enumerated") from exc
    entries = _parse_index_entries(listed.stdout)
    required = {normalize_repo_rel_path(path) for path in required_text_paths}
    text_paths = tuple(
        path
        for path in sorted(entries)
        if path in required or is_local_path_scan_rel_path(path)
    )
    requested_oids = tuple(sorted({entries[path].oid for path in text_paths}))
    blobs: dict[str, bytes] = {}
    if requested_oids:
        try:
            batched = subprocess.run(
                ["git", "-C", str(requested_root), "cat-file", "--batch"],
                check=True,
                input=("\n".join(requested_oids) + "\n").encode("ascii"),
                capture_output=True,
                timeout=60,
                env=env,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RepositoryViewError("Git index text blobs cannot be read") from exc
        blobs = _parse_batch_blobs(batched.stdout, requested_oids)
    texts: dict[str, str] = {}
    for path in text_paths:
        try:
            texts[path] = blobs[entries[path].oid].decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RepositoryViewError(
                f"repository snapshot text is not UTF-8: {path}"
            ) from exc
    return _register_repository_text_view(
        RepositoryTextView(
            root=requested_root,
            source="git_index",
            entries=MappingProxyType(dict(sorted(entries.items()))),
            texts=MappingProxyType(texts),
        )
    )


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


def is_local_path_scan_rel_path(rel_path: str) -> bool:
    if rel_path in LOCAL_PATH_SCAN_EXCLUSIONS:
        return False
    rel = PurePosixPath(rel_path)
    if rel.name in LOCAL_PATH_SCAN_TEXT_FILENAMES:
        return True
    return rel.suffix.lower() in LOCAL_PATH_SCAN_TEXT_SUFFIXES


def is_local_path_scan_target(path: Path, rel_path: str) -> bool:
    return path.is_file() and is_local_path_scan_rel_path(rel_path)


def markdown_without_fenced_code(text: str) -> str:
    """Remove fenced code while preserving the surrounding line structure."""

    def column_width(value: str, *, start: int = 0) -> int:
        column = start
        for character in value:
            if character == "\t":
                column += 4 - (column % 4)
            else:
                column += 1
        return column - start

    def leading_indent_width(value: str) -> int:
        match = re.match(r"^[ \t]*", value)
        return column_width(match.group(0)) if match is not None else 0

    def opening_container_content(
        line: str,
    ) -> tuple[str, tuple[int, ...], int]:
        """Remove opening containers and capture their indentation contract."""

        content = line
        blockquote_indents: list[int] = []
        list_indent_width = 0
        while True:
            blockquote = re.match(r"^[ \t]*>[ \t]?", content)
            if blockquote:
                marker = blockquote.group(0)
                quote_indent = column_width(marker[: marker.index(">")])
                blockquote_indents.append(
                    list_indent_width + (quote_indent if quote_indent > 3 else 0)
                )
                content = content[blockquote.end() :]
                # Closing detection consumes all indentation before this quote
                # marker, so only list containers inside the final quote remain.
                list_indent_width = 0
                continue
            list_item = re.match(
                r"^[ \t]*(?:[-+*]|\d{1,9}[.)])[ \t]+",
                content,
            )
            if list_item:
                list_indent_width += column_width(
                    list_item.group(0),
                    start=list_indent_width,
                )
                content = content[list_item.end() :]
                continue
            opening_indent = leading_indent_width(content)
            final_container_indent = list_indent_width + (
                opening_indent if opening_indent > 3 else 0
            )
            return content, tuple(blockquote_indents), final_container_indent

    def closing_container_content(
        line: str,
        blockquote_indents: tuple[int, ...],
    ) -> str | None:
        """Remove only the blockquote context captured by the opening fence."""

        content = line
        for expected_indent in blockquote_indents:
            blockquote = re.match(r"^[ \t]*>[ \t]?", content)
            if blockquote is None:
                return None
            marker = blockquote.group(0)
            actual_indent = column_width(marker[: marker.index(">")])
            if not expected_indent <= actual_indent <= expected_indent + 3:
                return None
            content = content[blockquote.end() :]
        return content

    visible: list[str] = []
    fence_character = ""
    fence_length = 0
    fence_blockquote_indents: tuple[int, ...] = ()
    fence_container_indent_width = 0
    for line in text.splitlines():
        if not fence_character:
            logical_line, blockquote_indents, container_indent_width = (
                opening_container_content(line)
            )
            # Container removal also covers list indentation.  Accepting any
            # remaining whitespace is fail-closed for governance parsing: an
            # even-more-indented marker is code, not visible contract text.
            opening = re.match(r"^[ \t]*(`{3,}|~{3,})", logical_line)
            if opening:
                marker = opening.group(1)
                fence_character = marker[0]
                fence_length = len(marker)
                fence_blockquote_indents = blockquote_indents
                fence_container_indent_width = container_indent_width
                visible.append("")
                continue
            visible.append(line)
            continue
        logical_line = closing_container_content(line, fence_blockquote_indents)
        if (
            logical_line is not None
            and leading_indent_width(logical_line)
            >= fence_container_indent_width
            and leading_indent_width(logical_line)
            <= fence_container_indent_width + 3
            and re.match(
                rf"^[ \t]*{re.escape(fence_character)}"
                rf"{{{fence_length},}}[ \t]*$",
                logical_line,
            )
        ):
            fence_character = ""
            fence_length = 0
            fence_blockquote_indents = ()
            fence_container_indent_width = 0
        visible.append("")
    return "\n".join(visible)


def markdown_without_block_code(text: str) -> str:
    """Remove fenced and indented code while preserving line structure."""

    def column_width(value: str, *, start: int = 0) -> int:
        column = start
        for character in value:
            if character == "\t":
                column += 4 - (column % 4)
            else:
                column += 1
        return column - start

    unfenced_lines: list[str] = []
    for line in markdown_without_fenced_code(text).splitlines():
        # A four-column top-level indent is an indented code block, not rendered
        # contract text.  Strip the same construct inside blockquotes after
        # consuming their container markers.  Treating uncertain deeply nested
        # continuations as code is intentionally fail-closed for positive
        # governance assertions.
        logical_line = line
        while True:
            blockquote = re.match(r"^[ \t]{0,3}>[ \t]?", logical_line)
            if blockquote is not None:
                logical_line = logical_line[blockquote.end() :]
                continue
            list_item = re.match(
                r"^([ \t]{0,3})([-+*]|\d{1,9}[.)])([ \t]+)",
                logical_line,
            )
            if list_item is None:
                break
            leading, marker, padding = list_item.groups()
            padding_start = column_width(leading) + len(marker)
            if column_width(padding, start=padding_start) > 4:
                logical_line = "    "
                break
            logical_line = logical_line[list_item.end() :]
        if re.match(r"^(?: {4}|\t)", logical_line):
            unfenced_lines.append("")
        else:
            unfenced_lines.append(line)

    return "\n".join(unfenced_lines)


def markdown_without_code(text: str) -> str:
    """Remove all Markdown code surfaces while preserving line structure."""

    unfenced = markdown_without_block_code(text)
    visible: list[str] = []
    index = 0
    while index < len(unfenced):
        if unfenced[index] != "`":
            visible.append(unfenced[index])
            index += 1
            continue
        run_end = index + 1
        while run_end < len(unfenced) and unfenced[run_end] == "`":
            run_end += 1
        run_length = run_end - index
        closing_start = run_end
        closing_end = -1
        while closing_start < len(unfenced):
            closing_start = unfenced.find("`", closing_start)
            if closing_start < 0:
                break
            candidate_end = closing_start + 1
            while (
                candidate_end < len(unfenced)
                and unfenced[candidate_end] == "`"
            ):
                candidate_end += 1
            if candidate_end - closing_start == run_length:
                closing_end = candidate_end
                break
            closing_start = candidate_end
        if closing_end < 0:
            visible.append(unfenced[index:run_end])
            index = run_end
            continue
        visible.extend(
            "\n" if character == "\n" else " "
            for character in unfenced[index:closing_end]
        )
        index = closing_end
    return "".join(visible)


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def markdown_without_html_comments(text: str) -> str:
    """Remove Markdown HTML comments while preserving line structure."""

    visible: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("<!--", index) and not _is_escaped(text, index):
            comment_end = text.find("-->", index + 4)
            end = len(text) if comment_end < 0 else comment_end + 3
            visible.extend(
                "\n" if character == "\n" else " "
                for character in text[index:end]
            )
            index = end
            continue
        visible.append(text[index])
        index += 1
    return "".join(visible)


def markdown_visible_text(text: str) -> str:
    """Remove Markdown surfaces that do not produce visible contract text."""

    return markdown_without_html_comments(markdown_without_code(text))


def markdown_headings(text: str) -> tuple[str, ...]:
    headings: list[str] = []
    for line in markdown_visible_text(text).splitlines():
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
    text = markdown_visible_text(text)
    targets: list[str] = []
    occupied_spans: list[tuple[int, int]] = []
    inline_pattern = re.compile(r"\[((?:\\.|[^\]\\])*)\]\(([^)\n]+)\)")
    for match in inline_pattern.finditer(text):
        occupied_spans.append(match.span())
        if _is_escaped(text, match.start()) or (
            match.start() > 0 and text[match.start() - 1] == "!"
        ):
            continue
        raw = match.group(2)
        raw = raw.strip()
        if raw.startswith("<") and ">" in raw:
            target = raw[1 : raw.index(">")]
        else:
            target = raw.split(maxsplit=1)[0]
        if target:
            targets.append(target)
    definitions: dict[str, str] = {}
    definition_spans: list[tuple[int, int]] = []
    for match in re.finditer(
        (
            r"^[ \t]{0,3}\[((?:\\.|[^\]\\\n])+?)\]:[ \t]*"
            r"(?:\n[ \t]*)?(?:<([^>\n]+)>|([^\s]+))"
        ),
        text,
        re.M,
    ):
        definition_spans.append(match.span())
        label = _normalized_reference_label(match.group(1))
        target = (match.group(2) or match.group(3) or "").strip()
        if target:
            definitions.setdefault(label, target)

    reference_pattern = re.compile(
        r"\[((?:\\.|[^\]\\\n])*)\]\[((?:\\.|[^\]\\\n])*)\]"
    )
    for match in reference_pattern.finditer(text):
        occupied_spans.append(match.span())
        if _is_escaped(text, match.start()) or (
            match.start() > 0 and text[match.start() - 1] == "!"
        ):
            continue
        label = _normalized_reference_label(match.group(2) or match.group(1))
        if label in definitions:
            targets.append(definitions[label])

    shortcut_pattern = re.compile(r"\[((?:\\.|[^\]\\\n])+?)\]")
    excluded_spans = occupied_spans + definition_spans
    for match in shortcut_pattern.finditer(text):
        if any(start <= match.start() < end for start, end in excluded_spans):
            continue
        if _is_escaped(text, match.start()) or (
            match.start() > 0 and text[match.start() - 1] == "!"
        ):
            continue
        if match.end() < len(text) and text[match.end()] in "([:":
            continue
        label = _normalized_reference_label(match.group(1))
        if label in definitions:
            targets.append(definitions[label])
    return tuple(targets)


def _normalized_reference_label(label: str) -> str:
    unescaped = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])", r"\1", label)
    return " ".join(unescaped.casefold().split())


def html_link_targets(text: str) -> tuple[str, ...]:
    text = markdown_without_html_comments(text)
    targets: list[str] = []
    for match in re.finditer(
        r"\bhref\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s\"'=<>`]+))",
        text,
        re.I,
    ):
        target = next((value for value in match.groups() if value is not None), "")
        if target:
            targets.append(target)
    return tuple(targets)


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
    repository_view: RepositoryTextView | None = None,
) -> tuple[str, ...]:
    from tools.diagnostics.docs_policy import (  # local import avoids a cycle
        PERMANENT_ROOT_DOC_CONTRACTS,
        doc_placement_value,
        repo_owner_value,
    )

    if repository_view is not None:
        require_repository_text_view(root, repository_view)
    contract = PERMANENT_ROOT_DOC_CONTRACTS.get(rel_path)
    if contract is None:
        return ()
    overrides = overrides or {}
    problems: list[str] = []
    visible_text = markdown_visible_text(text)
    headings = markdown_headings(visible_text)
    for heading in contract["required_headings"]:
        if headings.count(heading) != 1:
            problems.append(f"required heading must occur exactly once: {heading}")
    for marker in contract["required_text"]:
        if visible_text.count(marker) != 1:
            problems.append(
                f"required purpose landmark must occur exactly once: {marker}"
            )

    resolved_routes = resolved_markdown_targets(rel_path, visible_text)
    for route in contract["required_routes"]:
        if resolved_routes.count(route) < 1:
            problems.append(f"required route must resolve: {route}")
        route_exists = route in overrides or (
            repository_view.contains_path_or_prefix(route)
            if repository_view is not None
            else (root / route).exists()
        )
        if not route_exists:
            problems.append(f"required route target is missing: {route}")

    for referrer in contract["required_referrers"]:
        if referrer in overrides:
            referrer_text = overrides[referrer]
        elif repository_view is not None:
            if not repository_view.contains(referrer):
                problems.append(f"required referrer is missing: {referrer}")
                continue
            try:
                referrer_text = repository_view.read_text(referrer)
            except RepositoryViewError:
                problems.append(f"required referrer is not UTF-8 text: {referrer}")
                continue
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
    repository_view: RepositoryTextView | None = None,
) -> dict[str, object]:
    from tools.diagnostics.docs_policy import is_productization_control_plane_path

    if repository_view is not None:
        require_repository_text_view(root, repository_view)
    normalized_target = normalize_repo_rel_path(target_path)
    overrides = overrides or {}
    excluded = {normalize_repo_rel_path(path) for path in excluded_paths}
    paths = set(
        scan_paths
        or (repository_view.paths if repository_view is not None else ())
        or git_visible_paths(root)
        or fallback_local_path_scan_paths(root)
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
        elif repository_view is not None:
            if normalized not in repository_view.texts:
                continue
            text = repository_view.read_text(normalized)
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


def _markdown_table_cells(line: str) -> list[str]:
    return [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
