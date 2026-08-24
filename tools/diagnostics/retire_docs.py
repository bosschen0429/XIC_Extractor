"""Retire disposable tracked documents after owner and referrer checks.

This tool is intentionally mechanical. Product-absorption review happens before
calling it; this script checks evidence, history disposition, and exact repo
referrers. Disposable construction records may use ``discard_git_history``;
material private history still uses an exact vault source copy. Exact repo
referrers always block retirement; the tool never creates compatibility stubs.

Usage examples:
    uv run python tools/diagnostics/retire_docs.py --sweep
    uv run python tools/diagnostics/retire_docs.py --sweep --execute
    uv run python tools/diagnostics/retire_docs.py FILE [FILE ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.diagnostics import docs_scan as _docs_scan  # noqa: E402
from tools.diagnostics.docs_policy import (  # noqa: E402
    classify_doc,
    normalize_path_text,
    retired_tracked_dir_for_path,
    source_copy_locator_note_title,
    source_copy_locator_sha256,
    source_copy_locator_text,
    source_copy_note_title,
)

VAULT_ARCHIVE_DIR = "XIC/Archives/Repo Retired"

RETIRE_CANDIDATES = (
    "docs/superpowers/notes",
    "docs/superpowers/plans",
    "docs/superpowers/specs",
)
AUTO_RETIRE_LIFECYCLES = {
    "implemented",
    "superseded",
    "rejected",
    "archived",
    "retired",
}
KEEP_LIFECYCLES = {"active", "draft"}

KEEP_PATTERNS = {
    "README.md",
    "productization_authority_manifest.v1.json",
    "productization_control_plane_schema.v1.json",
    "productization_control_plane_schema.v2.json",
}

KEEP_PREFIXES = ("docs/superpowers/plans/2026-06-15-productization-control-plane.md",)
RETIREMENT_EVIDENCE_DIR = "docs/superpowers/file-management/docs-cleanup/"
RETIREMENT_EVIDENCE_TOKENS = ("retirement-evidence", "retirement_evidence")


def _normalize_title(filename: str) -> str:
    stem = Path(filename).stem
    return stem.lower().replace("-", " ").replace("_", " ").strip()


def _find_in_vault(
    title: str,
    vault_path: Path,
    *,
    rel_path: str,
    source_text: str,
    source_bytes_hash: str,
) -> Path | None:
    normalized = _normalize_title(title)
    normalized_source_copy = _normalize_title(
        source_copy_note_title(rel_path, source_bytes_hash)
    )
    for md in vault_path.rglob("*.md"):
        known_title = _normalize_title(md.name) in {
            normalized,
            normalized_source_copy,
        }
        if known_title and _vault_copy_matches(md, rel_path, source_text):
            return md
    return None


def _should_keep(rel_path: str) -> bool:
    name = Path(rel_path).name
    if name in KEEP_PATTERNS:
        return True
    for prefix in KEEP_PREFIXES:
        if rel_path == prefix:
            return True
    if name.endswith(".json") or name.endswith(".tsv"):
        return True
    return False


@dataclass
class RetireResult:
    already_in_vault: list[tuple[str, Path]] = field(default_factory=list)
    copied_to_vault: list[tuple[str, Path]] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    referrer_bound: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    kept: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SourceCopyResult:
    source_path: str
    source_hash: str
    destination: Path | None
    readback_verified: bool
    error: str = ""
    locator_destination: Path | None = None
    locator_hash: str = ""
    locator_readback_verified: bool = False


def find_retire_candidates(repo_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for dir_rel in RETIRE_CANDIDATES:
        d = repo_root / dir_rel
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            rel = f.relative_to(repo_root).as_posix()
            if not _should_keep(rel):
                candidates.append(f)
    return candidates


def _scan_paths(repo_root: Path) -> tuple[str, ...]:
    return _docs_scan.git_visible_paths(
        repo_root
    ) or _docs_scan.fallback_local_path_scan_paths(repo_root)


def _exact_referrers(
    repo_root: Path,
    rel_path: str,
    *,
    excluded_paths: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    normalized = _docs_scan.normalize_repo_rel_path(rel_path)
    referrers: list[str] = []
    for scan_rel in _scan_paths(repo_root):
        scan_rel = _docs_scan.normalize_repo_rel_path(scan_rel)
        if scan_rel == normalized:
            continue
        if scan_rel in excluded_paths:
            continue
        if _is_retirement_evidence_path(scan_rel):
            continue
        scan_path = repo_root / scan_rel
        if not _docs_scan.is_local_path_scan_target(scan_path, scan_rel):
            continue
        if normalized in _docs_scan.read_text(scan_path):
            referrers.append(scan_rel)
    return tuple(sorted(referrers))


def _is_retirement_evidence_path(path: str) -> bool:
    normalized = _docs_scan.normalize_repo_rel_path(path).lower()
    filename = Path(normalized).name
    return (
        normalized.startswith(RETIREMENT_EVIDENCE_DIR)
        and normalized.endswith(".json")
        and any(token in filename for token in RETIREMENT_EVIDENCE_TOKENS)
    )


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def copy_source_only(
    source: Path,
    repo_root: Path,
    vault_path: Path,
    *,
    evidence: dict[str, object],
    execute: bool = False,
) -> SourceCopyResult:
    """Copy an exact source preimage to private history without repo mutation."""

    try:
        rel = source.relative_to(repo_root).as_posix()
    except ValueError:
        return SourceCopyResult("", "", None, False, "source is outside repo root")
    if not source.is_file():
        return SourceCopyResult(rel, "", None, False, "source does not exist")
    if evidence.get("transaction") != "body_to_compatibility_index":
        return SourceCopyResult(
            rel,
            "",
            None,
            False,
            "evidence transaction is not body_to_compatibility_index",
        )
    entries = _retirement_evidence_by_path(evidence)
    entry = entries.get(rel)
    if entry is None or entry.get("transaction") != "body_to_compatibility_index":
        return SourceCopyResult(
            rel,
            "",
            None,
            False,
            "missing same-path source-copy evidence entry",
        )
    unresolved_claims = _string_list(entry.get("unresolved_claims"))
    if unresolved_claims is None:
        return SourceCopyResult(
            rel,
            "",
            None,
            False,
            "source-copy evidence unresolved_claims must be a string list",
        )
    if unresolved_claims:
        return SourceCopyResult(
            rel,
            "",
            None,
            False,
            "source-copy evidence has unresolved claims",
        )

    source_bytes = source.read_bytes()
    source_hash = _source_bytes_hash(source_bytes)
    if (
        evidence.get("source_hash") != source_hash
        or entry.get("source_hash") != source_hash
    ):
        return SourceCopyResult(
            rel,
            source_hash,
            None,
            False,
            "source-copy evidence source_hash mismatch",
        )

    archive = vault_path / VAULT_ARCHIVE_DIR
    note_title = source_copy_note_title(rel, source_hash)
    destination = archive / f"{note_title}{source.suffix}"
    locator_title = source_copy_locator_note_title(rel, source_hash)
    locator_destination = archive / f"{locator_title}.md"
    locator_bytes = source_copy_locator_text(rel, source_hash).encode("utf-8")
    locator_hash = source_copy_locator_sha256(rel, source_hash)
    if execute:
        archive.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() != source_bytes:
            return SourceCopyResult(
                rel,
                source_hash,
                destination,
                False,
                "source-copy destination exists with different bytes",
            )
        if (
            locator_destination.exists()
            and locator_destination.read_bytes() != locator_bytes
        ):
            return SourceCopyResult(
                rel,
                source_hash,
                destination,
                False,
                "source-copy locator exists with different bytes",
                locator_destination=locator_destination,
                locator_hash=locator_hash,
            )
        destination.write_bytes(source_bytes)
        locator_destination.write_bytes(locator_bytes)
        verified = (
            destination.is_file()
            and destination.read_bytes() == source_bytes
            and _source_bytes_hash(destination.read_bytes()) == source_hash
        )
        locator_verified = (
            locator_destination.is_file()
            and locator_destination.read_bytes() == locator_bytes
            and _source_bytes_hash(locator_destination.read_bytes()) == locator_hash
        )
    else:
        verified = destination.is_file() and destination.read_bytes() == source_bytes
        locator_verified = (
            locator_destination.is_file()
            and locator_destination.read_bytes() == locator_bytes
        )
    error = ""
    if execute and not verified:
        error = "source-copy readback failed"
    elif execute and not locator_verified:
        error = "source-copy locator readback failed"
    return SourceCopyResult(
        rel,
        source_hash,
        destination,
        verified,
        error,
        locator_destination=locator_destination,
        locator_hash=locator_hash,
        locator_readback_verified=locator_verified,
    )


def _vault_copy_matches(path: Path, rel_path: str, source_text: str) -> bool:
    vault_text = path.read_text(encoding="utf-8", errors="ignore")
    if _source_hash(vault_text) == _source_hash(source_text):
        return True

    normalized_rel = _docs_scan.normalize_repo_rel_path(rel_path)
    normalized_vault_text = normalize_path_text(vault_text)
    if normalized_rel not in normalized_vault_text:
        return False

    marker = "\n## Original Content\n"
    if marker not in vault_text:
        return False
    original_text = vault_text.split(marker, 1)[1]
    return _source_hash(original_text) == _source_hash(source_text)


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        return None
    return value


def _retirement_evidence_by_path(
    evidence: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    if evidence is None:
        return {}
    entries = evidence.get("entries")
    if not isinstance(entries, list):
        return {}
    indexed: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source_path = entry.get("source_path")
        if isinstance(source_path, str) and source_path:
            try:
                normalized = _docs_scan.normalize_repo_rel_path(source_path)
            except ValueError:
                continue
            indexed[normalized] = entry
    return indexed


def _retirement_evidence_blocker(
    rel_path: str,
    text: str,
    evidence_by_path: dict[str, dict[str, object]],
    referrers: tuple[str, ...],
) -> str:
    entry = evidence_by_path.get(rel_path)
    if entry is None:
        return "missing retirement evidence"
    if entry.get("review_result") != "pass_can_retire":
        return "retirement evidence review_result is not pass_can_retire"
    for field_name in (
        "owner_paths",
        "owner_anchors",
        "absorbed_claims",
        "absorbed_negative_claims",
    ):
        values = _string_list(entry.get(field_name))
        if values is None or not values:
            return f"retirement evidence missing {field_name}"
    history_disposition = entry.get(
        "history_disposition", "obsidian_source_copy"
    )
    if history_disposition == "discard_git_history":
        discard_reason = entry.get("discard_reason")
        if not isinstance(discard_reason, str) or not discard_reason.strip():
            return "discard_git_history requires a discard_reason"
        if entry.get("operator_approved_temporal_retirement") is not True:
            return "discard_git_history requires operator approval"
        if not retired_tracked_dir_for_path(rel_path):
            return "discard_git_history is limited to retired tracked docs lanes"
    elif history_disposition == "obsidian_source_copy":
        if entry.get("source_copy_readback_verified") is not True:
            return "retirement evidence source copy readback is not verified"
    else:
        return "unknown retirement history_disposition"
    if entry.get("source_hash") != _source_hash(text):
        return "retirement evidence source_hash mismatch"
    active_followups = _string_list(entry.get("active_followups"))
    if active_followups is None:
        return "retirement evidence active_followups must be a string list"
    if active_followups:
        return "retirement evidence still has active followups"

    raw_evidence_referrers = _string_list(entry.get("exact_referrers"))
    if raw_evidence_referrers is None:
        return "retirement evidence exact_referrers must be a string list"
    evidence_referrers = tuple(sorted(raw_evidence_referrers))
    if referrers:
        return "exact repo referrers require retargeting"
    elif evidence_referrers:
        return "retirement evidence exact_referrers mismatch"
    return ""


def _vault_destination(fpath: Path, vault_archive: Path) -> Path:
    return vault_archive / fpath.name


def _safe_vault_destination(
    fpath: Path,
    rel_path: str,
    vault_archive: Path,
    source_text: str,
) -> Path | None:
    dest = _vault_destination(fpath, vault_archive)
    source_hash = _source_hash(source_text)
    candidates = (
        dest,
        dest.with_name(f"{dest.stem}-{source_hash[:12]}{dest.suffix}"),
        dest.with_name(f"{dest.stem}-{source_hash}{dest.suffix}"),
    )
    for candidate in candidates:
        if not candidate.exists() or _vault_copy_matches(
            candidate,
            rel_path,
            source_text,
        ):
            return candidate
    return None


def _ensure_vault_copy(
    fpath: Path,
    rel_path: str,
    vault_path: Path,
    vault_archive: Path,
    result: RetireResult,
    *,
    execute: bool,
) -> Path | None:
    source_text = fpath.read_text(encoding="utf-8", errors="ignore")
    vault_match = _find_in_vault(
        fpath.name,
        vault_path,
        rel_path=rel_path,
        source_text=source_text,
        source_bytes_hash=_source_bytes_hash(fpath.read_bytes()),
    )
    if vault_match:
        result.already_in_vault.append((rel_path, vault_match))
        return vault_match

    dest = _safe_vault_destination(fpath, rel_path, vault_archive, source_text)
    if dest is None:
        result.errors.append(
            (rel_path, "vault archive destinations all contain different content")
        )
        return None
    result.copied_to_vault.append((rel_path, dest))
    if execute:
        vault_archive.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fpath, dest)
    return dest


def _retirement_blocker(
    rel_path: str,
    text: str,
    evidence_entry: dict[str, object] | None,
) -> str:
    if (
        evidence_entry is not None
        and evidence_entry.get("history_disposition") == "discard_git_history"
        and evidence_entry.get("operator_approved_temporal_retirement") is True
        and retired_tracked_dir_for_path(rel_path)
    ):
        return ""
    classification = classify_doc(rel_path, text)
    if classification.placement == "repo_active_stub":
        return "active repo stub"
    if classification.doc_lifecycle in KEEP_LIFECYCLES:
        return f"{classification.doc_lifecycle} lifecycle"
    if classification.lifecycle_status != "declared":
        return (
            "missing or invalid lifecycle metadata; run product-absorption review "
            "before retirement"
        )
    if classification.doc_lifecycle not in AUTO_RETIRE_LIFECYCLES:
        return f"{classification.doc_lifecycle} lifecycle is not auto-retirable"
    return ""


def retire_files(
    files: list[Path],
    repo_root: Path,
    vault_path: Path | None,
    *,
    execute: bool = False,
    evidence: dict[str, object] | None = None,
) -> RetireResult:
    result = RetireResult()
    vault_archive = vault_path / VAULT_ARCHIVE_DIR if vault_path else None
    evidence_by_path = _retirement_evidence_by_path(evidence)

    for fpath in files:
        rel = fpath.relative_to(repo_root).as_posix()

        if _should_keep(rel):
            result.kept.append((rel, "protected file"))
            continue

        if not fpath.exists():
            result.errors.append((rel, "file does not exist"))
            continue

        text = fpath.read_text(encoding="utf-8", errors="ignore")
        evidence_entry = evidence_by_path.get(rel)
        blocker = _retirement_blocker(rel, text, evidence_entry)
        if blocker:
            result.kept.append((rel, blocker))
            continue

        referrers = _exact_referrers(
            repo_root,
            rel,
            excluded_paths=frozenset(result.retired),
        )
        if referrers:
            result.referrer_bound.append((rel, referrers))
            result.kept.append(
                (
                    rel,
                    "exact repo referrers require retargeting",
                )
            )
            continue

        evidence_blocker = _retirement_evidence_blocker(
            rel,
            text,
            evidence_by_path,
            referrers,
        )
        if evidence_blocker:
            result.kept.append((rel, evidence_blocker))
            continue

        history_disposition = (
            evidence_entry or {}
        ).get("history_disposition", "obsidian_source_copy")
        if history_disposition == "obsidian_source_copy":
            if vault_path is None or vault_archive is None:
                result.errors.append((rel, "vault path required for source copy"))
                continue
            vault_copy = _ensure_vault_copy(
                fpath,
                rel,
                vault_path,
                vault_archive,
                result,
                execute=execute,
            )
            if vault_copy is None:
                continue

        if execute:
            fpath.unlink()
        result.retired.append(rel)

    return result


def _resolve_vault_path() -> Path | None:
    config = Path.home() / ".obsidian-wiki" / "config"
    if not config.exists():
        return None
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("OBSIDIAN_VAULT_PATH="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            p = Path(val)
            if p.is_dir():
                return p
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path, help="specific files to retire")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="find all retire candidates",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually retire (default: dry-run)",
    )
    parser.add_argument(
        "--source-copy-only",
        action="store_true",
        help=(
            "Copy and read back an exact private source preimage for a "
            "same-path body_to_compatibility_index transaction; never mutate "
            "the repo source."
        ),
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--vault-path", type=Path, default=None)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=None,
        help="JSON retirement evidence packet required before repo retirement.",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    evidence = None
    if args.evidence is not None:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    evidence_entries = list(_retirement_evidence_by_path(evidence).values())
    discard_only = bool(evidence_entries) and all(
        entry.get("history_disposition") == "discard_git_history"
        for entry in evidence_entries
    )
    vault_path = args.vault_path or _resolve_vault_path()
    if not vault_path and not discard_only:
        print(
            (
                "ERROR: Cannot find vault path. Set OBSIDIAN_VAULT_PATH in "
                "~/.obsidian-wiki/config, or use approved discard_git_history "
                "evidence for disposable construction records."
            ),
            file=sys.stderr,
        )
        return 1

    if args.source_copy_only:
        if args.sweep or len(args.files) != 1:
            parser.error(
                "--source-copy-only requires exactly one file and cannot use "
                "--sweep"
            )
            return 1
        if args.evidence is None:
            parser.error("--source-copy-only requires --evidence")
            return 1
        if vault_path is None:
            print(
                "ERROR: --source-copy-only requires a configured vault path.",
                file=sys.stderr,
            )
            return 1
        source = (
            args.files[0].resolve()
            if args.files[0].is_absolute()
            else (repo_root / args.files[0]).resolve()
        )
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        copy_result = copy_source_only(
            source,
            repo_root,
            vault_path,
            evidence=evidence,
            execute=args.execute,
        )
        if copy_result.error:
            print(f"ERROR: {copy_result.error}", file=sys.stderr)
            return 1
        mode = "verified" if args.execute else "dry-run"
        note_title = (
            copy_result.destination.stem if copy_result.destination is not None else ""
        )
        locator_note_title = (
            copy_result.locator_destination.stem
            if copy_result.locator_destination is not None
            else ""
        )
        print(
            f"Source copy {mode}: {copy_result.source_path}; "
            f"sha256={copy_result.source_hash}; "
            f"note_title={note_title}; "
            f"readback_verified={str(copy_result.readback_verified).lower()}; "
            f"locator_note_title={locator_note_title}; "
            f"locator_sha256={copy_result.locator_hash}; "
            "locator_readback_verified="
            f"{str(copy_result.locator_readback_verified).lower()}"
        )
        return (
            0
            if (
                not args.execute
                or (
                    copy_result.readback_verified
                    and copy_result.locator_readback_verified
                )
            )
            else 1
        )

    if args.sweep:
        files = find_retire_candidates(repo_root)
    elif args.files:
        files = [
            f.resolve() if f.is_absolute() else (repo_root / f).resolve()
            for f in args.files
        ]
    else:
        parser.error("provide file paths or use --sweep")
        return 1

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"Mode: {mode}\n")
    result = retire_files(
        files,
        repo_root,
        vault_path,
        execute=args.execute,
        evidence=evidence,
    )

    if result.already_in_vault:
        print(f"Already in vault ({len(result.already_in_vault)}):")
        for rel, vault_match in result.already_in_vault:
            print(f"  {rel}")
            print(f"    vault: {vault_match}")

    if result.copied_to_vault:
        copy_label = "copied" if args.execute else "would copy"
        print(f"\nNot in vault - {copy_label} ({len(result.copied_to_vault)}):")
        for rel, dest in result.copied_to_vault:
            print(f"  {rel} -> {dest}")

    if result.retired:
        retire_label = "Retired" if args.execute else "Would retire"
        print(f"\n{retire_label} ({len(result.retired)}):")
        for rel in result.retired:
            print(f"  {rel}")

    if result.referrer_bound:
        print(f"\nReferrer-bound kept ({len(result.referrer_bound)}):")
        for rel, referrers in result.referrer_bound:
            print(f"  {rel}")
            print(f"    referrers: {'; '.join(referrers)}")

    if result.kept:
        print(f"\nKept ({len(result.kept)}):")
        for rel, reason in result.kept:
            print(f"  {rel} ({reason})")

    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for rel, reason in result.errors:
            print(f"  {rel} ({reason})")

    total = len(result.retired)
    summary_label = "repo action(s)" if args.execute else "repo action(s) available"
    print(
        f"\nSummary: {total} {summary_label}, "
        f"{len(result.kept)} kept, {len(result.errors)} errors"
    )
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
