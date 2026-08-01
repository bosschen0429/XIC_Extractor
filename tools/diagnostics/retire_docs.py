"""Retire completed transient documents from repo to vault.

This tool is intentionally mechanical. Product-absorption review happens before
calling it; this script only checks lifecycle metadata, vault backup, and exact
repo referrers. Completed transient docs with no exact referrers leave the repo.
Referrer-bound docs can be converted to short same-path stubs with
``--stub-bound``; otherwise they are kept for a referrer rewrite pass.

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
) -> Path | None:
    normalized = _normalize_title(title)
    for md in vault_path.rglob("*.md"):
        if _normalize_title(md.name) == normalized and _vault_copy_matches(
            md,
            rel_path,
            source_text,
        ):
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
    stubbed: list[tuple[str, str]] = field(default_factory=list)
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


def _exact_referrers(repo_root: Path, rel_path: str) -> tuple[str, ...]:
    normalized = _docs_scan.normalize_repo_rel_path(rel_path)
    referrers: list[str] = []
    for scan_rel in _scan_paths(repo_root):
        scan_rel = _docs_scan.normalize_repo_rel_path(scan_rel)
        if scan_rel == normalized:
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
    if _string_list(entry.get("unresolved_claims")):
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
    destination = archive / (f"{source.stem}-source-{source_hash[:12]}{source.suffix}")
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
        destination.write_bytes(source_bytes)
        verified = (
            destination.is_file()
            and destination.read_bytes() == source_bytes
            and _source_bytes_hash(destination.read_bytes()) == source_hash
        )
    else:
        verified = destination.is_file() and destination.read_bytes() == source_bytes
    return SourceCopyResult(
        rel,
        source_hash,
        destination,
        verified,
        "" if (verified or not execute) else "source-copy readback failed",
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


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


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
            indexed[_docs_scan.normalize_repo_rel_path(source_path)] = entry
    return indexed


def _retirement_evidence_blocker(
    rel_path: str,
    text: str,
    evidence_by_path: dict[str, dict[str, object]],
    referrers: tuple[str, ...],
    *,
    stub_bound: bool,
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
        if not _string_list(entry.get(field_name)):
            return f"retirement evidence missing {field_name}"
    if entry.get("source_copy_readback_verified") is not True:
        return "retirement evidence source copy readback is not verified"
    if entry.get("source_hash") != _source_hash(text):
        return "retirement evidence source_hash mismatch"
    if _string_list(entry.get("active_followups")):
        return "retirement evidence still has active followups"

    evidence_referrers = tuple(sorted(_string_list(entry.get("exact_referrers"))))
    if referrers:
        if not stub_bound:
            return "exact repo referrers require retargeting or --stub-bound"
        if evidence_referrers != tuple(sorted(referrers)):
            return "retirement evidence exact_referrers mismatch"
    elif evidence_referrers:
        return "retirement evidence exact_referrers mismatch"
    return ""


def _vault_destination(fpath: Path, vault_archive: Path) -> Path:
    return vault_archive / fpath.name


def _safe_vault_destination(fpath: Path, vault_archive: Path, source_text: str) -> Path:
    dest = _vault_destination(fpath, vault_archive)
    if not dest.exists():
        return dest
    if _vault_copy_matches(
        dest,
        fpath.as_posix(),
        source_text,
    ):
        return dest
    return dest.with_name(f"{dest.stem}-{_source_hash(source_text)[:12]}{dest.suffix}")


def _ensure_vault_copy(
    fpath: Path,
    rel_path: str,
    vault_path: Path,
    vault_archive: Path,
    result: RetireResult,
    *,
    execute: bool,
) -> Path:
    source_text = fpath.read_text(encoding="utf-8", errors="ignore")
    vault_match = _find_in_vault(
        fpath.name,
        vault_path,
        rel_path=rel_path,
        source_text=source_text,
    )
    if vault_match:
        result.already_in_vault.append((rel_path, vault_match))
        return vault_match

    dest = _safe_vault_destination(fpath, vault_archive, source_text)
    result.copied_to_vault.append((rel_path, dest))
    if execute:
        vault_archive.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fpath, dest)
    return dest


def _stub_text(rel_path: str, text: str) -> str:
    classification = classify_doc(rel_path, text)
    owner = classification.repo_owner or classification.path
    kind = classification.doc_kind if classification.doc_kind != "unknown" else "note"
    return "\n".join(
        [
            "# Retired Document Stub",
            "",
            "Doc placement: repo_stub_plus_obsidian",
            f"Doc kind: {kind}",
            "Doc lifecycle: archived",
            f"Repo owner: {owner}",
            (
                "Doc exit rule: delete this stub after exact repo referrers move "
                f"to {owner}."
            ),
            "",
            "Status: retired_original_in_obsidian",
            "",
            f"Original repo path: `{rel_path}`",
            f"Current repo authority: `{owner}`",
            f"Obsidian source hint: `source_repo_path:{rel_path}`",
            "",
            "The long-form original is private history. This stub only preserves",
            "compatibility for remaining exact path references.",
            "",
        ]
    )


def _retirement_blocker(rel_path: str, text: str) -> str:
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
    vault_path: Path,
    *,
    execute: bool = False,
    stub_bound: bool = False,
    evidence: dict[str, object] | None = None,
) -> RetireResult:
    result = RetireResult()
    vault_archive = vault_path / VAULT_ARCHIVE_DIR
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
        blocker = _retirement_blocker(rel, text)
        if blocker:
            result.kept.append((rel, blocker))
            continue

        referrers = _exact_referrers(repo_root, rel)
        if referrers and not stub_bound:
            result.referrer_bound.append((rel, referrers))
            result.kept.append(
                (
                    rel,
                    "exact repo referrers; rerun with --stub-bound or retarget refs",
                )
            )
            continue

        evidence_blocker = _retirement_evidence_blocker(
            rel,
            text,
            evidence_by_path,
            referrers,
            stub_bound=stub_bound,
        )
        if evidence_blocker:
            result.kept.append((rel, evidence_blocker))
            continue

        _ensure_vault_copy(
            fpath,
            rel,
            vault_path,
            vault_archive,
            result,
            execute=execute,
        )

        if referrers and stub_bound:
            owner = classify_doc(rel, text).repo_owner
            if not owner:
                result.kept.append(
                    (rel, "exact repo referrers but no Repo owner for stub target")
                )
                continue
            if execute:
                fpath.write_text(_stub_text(rel, text), encoding="utf-8")
            result.stubbed.append((rel, owner))
        else:
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
        "--stub-bound",
        action="store_true",
        help=(
            "Replace completed docs that still have exact repo referrers with "
            "same-path compatibility stubs instead of keeping them."
        ),
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

    vault_path = args.vault_path or _resolve_vault_path()
    if not vault_path:
        print(
            (
                "ERROR: Cannot find vault path. Set OBSIDIAN_VAULT_PATH in "
                "~/.obsidian-wiki/config"
            ),
            file=sys.stderr,
        )
        return 1

    repo_root = args.repo_root.resolve()

    if args.source_copy_only:
        if args.sweep or args.stub_bound or len(args.files) != 1:
            parser.error(
                "--source-copy-only requires exactly one file and cannot use "
                "--sweep or --stub-bound"
            )
            return 1
        if args.evidence is None:
            parser.error("--source-copy-only requires --evidence")
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
        print(
            f"Source copy {mode}: {copy_result.source_path}; "
            f"sha256={copy_result.source_hash}; "
            f"readback_verified={str(copy_result.readback_verified).lower()}"
        )
        return 0 if (copy_result.readback_verified or not args.execute) else 1

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
    evidence = None
    if args.evidence is not None:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))

    result = retire_files(
        files,
        repo_root,
        vault_path,
        execute=args.execute,
        stub_bound=args.stub_bound,
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

    if result.stubbed:
        print(
            f"\n{'Stubbed' if args.execute else 'Would stub'} ({len(result.stubbed)}):"
        )
        for rel, owner in result.stubbed:
            print(f"  {rel} -> {owner}")

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

    total = len(result.retired) + len(result.stubbed)
    summary_label = "repo action(s)" if args.execute else "repo action(s) available"
    print(
        f"\nSummary: {total} {summary_label}, "
        f"{len(result.kept)} kept, {len(result.errors)} errors"
    )
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
