"""Audit repo/Obsidian documentation management health.

This checker is read-only. It catches workflow drift that the staged
docs-placement guard intentionally does not cover, such as whole
docs/superpowers routing inventory and candidates with key-concept, repo-owner,
and Obsidian-lane hints, stale branch handoffs after a commit, vault manifest
stats that no longer match the vault, pending raw/staged wiki files, missing
wiki lifecycle metadata, and local-path exposure that needs a focused
retention/privacy review.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from tools.diagnostics import docs_scan as _docs_scan  # noqa: E402
from tools.diagnostics.docs_policy import (  # noqa: E402
    DOC_CANONICAL_OWNER_FILES,
    PERMANENT_ROOT_DOC_CONTRACTS,
    classify_doc,
    classify_doc_path,
    is_invalid_superpowers_spec_payload_path,
    is_markdown_path,
)
from tools.diagnostics.docs_routing_review import (  # noqa: E402
    ROUTING_MANIFEST_FIELDS,
    TOPIC_CLUSTER_MANIFEST_FIELDS,
    docs_routing_review,
)
from tools.diagnostics.docs_topic_indexes import (  # noqa: E402
    write_topic_index_readmes as _write_topic_index_readmes,
)

_read_text = _docs_scan.read_text

DEFAULT_CONFIG = Path.home() / ".obsidian-wiki" / "config"
LOCAL_ENV_FILES = (".env.xic-local", ".env")
CANONICAL_METADATA_REVIEW_EXCLUDE_PREFIXES = ("docs/superpowers/",)

LOCAL_PATH_PATTERNS = (
    re.compile(r"C:\\Users\\user\\", re.IGNORECASE),
    re.compile(r"C:/Users/user/", re.IGNORECASE),
    re.compile(r"C:\\Vaults(?:\\|$)", re.IGNORECASE),
    re.compile(r"C:\\Xcalibur(?:\\|$)", re.IGNORECASE),
    re.compile(r"C:\\Python\d+(?:\\|$)", re.IGNORECASE),
    re.compile(r"Research Vault", re.IGNORECASE),
)
VALID_LIFECYCLES = {"draft", "reviewed", "verified", "disputed", "archived"}
VALID_TIERS = {"core", "supporting", "peripheral"}
COMPLETED_TRANSIENT_SCAN_PREFIXES = (
    "docs/superpowers/plans/",
    "docs/superpowers/specs/",
    "docs/superpowers/notes/",
    "docs/superpowers/productization/evidence/",
)
COMPLETED_TRANSIENT_LIFECYCLES = {
    "implemented",
    "superseded",
    "rejected",
    "archived",
    "retired",
}
@dataclass(frozen=True)
class AuditMessage:
    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class AuditResult:
    messages: tuple[AuditMessage, ...]
    summary: dict[str, object]

    @property
    def blockers(self) -> tuple[AuditMessage, ...]:
        return tuple(msg for msg in self.messages if msg.severity == "blocker")


def parse_yaml_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML frontmatter between ``---`` delimiters.

    Public so other diagnostics modules can reuse the same parser.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    values: dict[str, str] = {}
    current_key: str | None = None
    for line in text[3:end].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith((" ", "\t")) or stripped.startswith("- "):
            if current_key and stripped.startswith("- "):
                item = stripped[2:].strip().strip('"').strip("'")
                values[current_key] = " ".join(
                    part for part in (values.get(current_key, ""), item) if part
                )
            continue
        if ":" not in line:
            current_key = None
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        values[current_key] = value.strip().strip('"').strip("'")
    return values


def _config_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in _read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_vault(root: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    env_value = os.environ.get("OBSIDIAN_VAULT_PATH")
    if env_value:
        return Path(env_value)

    current = root.resolve()
    home = Path.home().resolve()
    for candidate_dir in (current, *current.parents):
        for filename in LOCAL_ENV_FILES:
            candidate = candidate_dir / filename
            if candidate.exists():
                values = _config_values(candidate)
                vault = values.get("OBSIDIAN_VAULT_PATH")
                if vault:
                    return Path(vault)
        if candidate_dir == home:
            break

    values = _config_values(DEFAULT_CONFIG)
    vault = values.get("OBSIDIAN_VAULT_PATH")
    return Path(vault) if vault else None


def audit_repo(
    root: Path,
    *,
    fail_on_completed_transient: bool = False,
) -> tuple[list[AuditMessage], dict[str, object]]:
    messages: list[AuditMessage] = []
    repository_view = _docs_scan.load_repository_text_view(root)
    docs = tuple(
        rel_path
        for rel_path in repository_view.paths
        if rel_path.startswith("docs/") and is_markdown_path(rel_path)
    )
    docs_by_top = Counter(
        rel_path.removeprefix("docs/").split("/", 1)[0] or "."
        for rel_path in docs
    )

    local_path_counts: Counter[str] = Counter()
    for rel_path in repository_view.text_paths:
        if not _docs_scan.is_local_path_scan_rel_path(rel_path):
            continue
        text = repository_view.read_text(rel_path)
        count = sum(len(pattern.findall(text)) for pattern in LOCAL_PATH_PATTERNS)
        if count:
            local_path_counts[rel_path] = count

    top_local_path_files = [
        {"path": path, "count": count}
        for path, count in local_path_counts.most_common(20)
    ]
    if local_path_counts:
        messages.append(
            AuditMessage(
                "warning",
                "repo",
                (
                    "local/private path exposure remains in tracked text; "
                    f"{len(local_path_counts)} files, "
                    f"{sum(local_path_counts.values())} hits"
                ),
            )
        )

    routing_review = docs_routing_review(repository_view)
    structure_review = docs_structure_review_for_repo(repository_view.paths)
    completed_transient_review = completed_transient_review_for_repo(
        repository_view
    )
    canonical_metadata_review = canonical_metadata_review_for_repo(repository_view)
    governance_rel_path = "docs/agent/codex-operating-system.md"
    governance_problems = (
        list(
            _docs_scan.governance_index_problems(
                root,
                repository_view.read_text(governance_rel_path),
                repository_view=repository_view,
            )
        )
        if repository_view.contains(governance_rel_path)
        else []
    )
    routing_candidates = int(routing_review["candidate_files"])
    metadata_review_files = int(routing_review["metadata_review_files"])
    canonical_missing_metadata = int(
        canonical_metadata_review["missing_metadata_files"]
    )
    invalid_permanent_rows = [
        row
        for row in canonical_metadata_review["top_missing_metadata"]
        if row["metadata_status"] == "invalid_permanent_root_contract"
    ]
    ordinary_canonical_metadata_count = (
        canonical_missing_metadata - len(invalid_permanent_rows)
    )
    if routing_candidates:
        invalid_count = int(
            routing_review["disposition_counts"].get("invalid_repo_placement", 0)
        )
        if invalid_count:
            messages.append(
                AuditMessage(
                    "blocker",
                    "docs",
                    (
                        "tracked repo docs declare invalid or retired placement; "
                        f"{invalid_count} file(s) need routing repair"
                    ),
                )
            )
        messages.append(
            AuditMessage(
                "warning",
                "docs",
                (
                    "repo docs routing review has candidate files; inspect "
                    "summary.repo.docs_routing_review.top_candidates before "
                    f"writing another cleanup plan ({routing_candidates} file(s))"
                ),
            )
        )

    if metadata_review_files:
        messages.append(
            AuditMessage(
                "warning",
                "docs",
                (
                    "repo docs metadata review has retained files missing "
                    "metadata; inspect "
                    "summary.repo.docs_routing_review.top_metadata_reviews "
                    f"({metadata_review_files} file(s))"
                ),
            )
        )

    if ordinary_canonical_metadata_count:
        messages.append(
            AuditMessage(
                "warning",
                "docs",
                (
                    "canonical docs metadata review found missing metadata; "
                    "inspect summary.repo.canonical_metadata_review."
                    "top_missing_metadata "
                    f"({ordinary_canonical_metadata_count} file(s))"
                ),
            )
        )

    for row in invalid_permanent_rows:
        messages.append(
            AuditMessage(
                "blocker",
                str(row["path"]),
                "invalid permanent-root contract: "
                + str(row["metadata_missing_fields"]),
            )
        )

    for problem in governance_problems:
        messages.append(
            AuditMessage(
                "blocker",
                "docs/agent/codex-operating-system.md",
                f"governance enforcement index: {problem}",
            )
        )

    root_scatter_count = int(structure_review["root_scatter_files"])
    if root_scatter_count:
        messages.append(
            AuditMessage(
                "warning",
                "docs",
                (
                    "docs root has non-allowlisted Markdown files; inspect "
                    "summary.repo.docs_structure_review.root_scatter "
                    f"({root_scatter_count} file(s))"
                ),
            )
        )

    retired_lane_count = int(structure_review["retired_lane_files"])
    if retired_lane_count:
        messages.append(
            AuditMessage(
                "blocker",
                "docs",
                (
                    "retired docs lanes contain repo-visible files; inspect "
                    "summary.repo.docs_structure_review.retired_lanes "
                    f"({retired_lane_count} file(s))"
                ),
            )
        )

    invalid_spec_payload_count = int(structure_review["invalid_spec_payload_files"])
    if invalid_spec_payload_count:
        messages.append(
            AuditMessage(
                "blocker",
                "docs",
                (
                    "docs/superpowers/specs contains non-Markdown payloads; "
                    "inspect "
                    "summary.repo.docs_structure_review.invalid_spec_payloads "
                    f"({invalid_spec_payload_count} file(s))"
                ),
            )
        )

    completed_transient_count = int(completed_transient_review["completed_files"])
    if fail_on_completed_transient and completed_transient_count:
        messages.append(
            AuditMessage(
                "blocker",
                "docs",
                (
                    "completed transient docs remain in repo; retire with "
                    "owner absorption, evidence, and a clean referrer scan "
                    f"({completed_transient_count} file(s))"
                ),
            )
        )

    duplicate_topic_owner_groups = int(
        routing_review["topic_cluster_group_counts"].get("potential_duplicate_owner", 0)
    )
    if duplicate_topic_owner_groups:
        messages.append(
            AuditMessage(
                "warning",
                "docs",
                (
                    "repo docs topic review found possible duplicate topic "
                    "owners; inspect "
                    "summary.repo.docs_routing_review.top_topic_clusters "
                    f"({duplicate_topic_owner_groups} group(s))"
                ),
            )
        )

    summary: dict[str, object] = {
        "repo_markdown_files": len(docs),
        "docs_by_top_level": dict(sorted(docs_by_top.items())),
        "local_path_files": len(local_path_counts),
        "local_path_hits": sum(local_path_counts.values()),
        "top_local_path_files": top_local_path_files,
        "docs_routing_review": routing_review,
        "docs_structure_review": structure_review,
        "completed_transient_review": completed_transient_review,
        "canonical_metadata_review": canonical_metadata_review,
        "governance_enforcement_index": {
            "problems": governance_problems,
            "status": "pass" if not governance_problems else "fail",
        },
    }
    return messages, summary


def completed_transient_review_for_repo(
    repository_view: _docs_scan.RepositoryTextView,
) -> dict[str, object]:
    completed_files: list[dict[str, str]] = []
    for raw_path in repository_view.paths:
        rel_path = raw_path.replace("\\", "/")
        if not rel_path.startswith(COMPLETED_TRANSIENT_SCAN_PREFIXES):
            continue
        if not is_markdown_path(rel_path):
            continue
        if Path(rel_path).name.lower() == "readme.md":
            continue
        classification = classify_doc(
            rel_path,
            repository_view.read_text(rel_path),
        )
        if classification.doc_lifecycle not in COMPLETED_TRANSIENT_LIFECYCLES:
            continue

        completed_files.append(
            {
                "path": rel_path,
                "doc_kind": classification.doc_kind,
                "doc_lifecycle": classification.doc_lifecycle,
                "doc_placement": classification.placement or "missing",
                "repo_owner": classification.repo_owner or "missing",
                "doc_exit_rule": classification.doc_exit_rule,
            }
        )

    return {
        "completed_files": len(completed_files),
        "top_completed_files": completed_files[:25],
    }


def docs_structure_review_for_repo(scan_paths: Sequence[str]) -> dict[str, object]:
    root_scatter: list[str] = []
    retired_lanes: list[dict[str, str]] = []
    invalid_spec_payloads: list[str] = []
    for rel_path in sorted(scan_paths):
        if is_invalid_superpowers_spec_payload_path(rel_path):
            invalid_spec_payloads.append(rel_path)
        classification = classify_doc_path(rel_path)
        if classification.is_retired_tracked_dir:
            retired_lanes.append(
                {
                    "path": rel_path,
                    "retired_dir": classification.retired_tracked_dir,
                }
            )
        if not classification.is_repo_doc:
            continue
        if classification.is_docs_root_scatter:
            root_scatter.append(rel_path)
    return {
        "root_scatter_files": len(root_scatter),
        "root_scatter": root_scatter,
        "top_root_scatter": root_scatter[:25],
        "retired_lane_files": len(retired_lanes),
        "retired_lanes": retired_lanes,
        "top_retired_lanes": retired_lanes[:25],
        "invalid_spec_payload_files": len(invalid_spec_payloads),
        "invalid_spec_payloads": invalid_spec_payloads,
        "top_invalid_spec_payloads": invalid_spec_payloads[:25],
    }


def canonical_metadata_review_for_repo(
    repository_view: _docs_scan.RepositoryTextView,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for rel_path in sorted(DOC_CANONICAL_OWNER_FILES):
        if not is_markdown_path(rel_path):
            continue
        if rel_path.startswith(CANONICAL_METADATA_REVIEW_EXCLUDE_PREFIXES):
            continue
        if not repository_view.contains(rel_path):
            if rel_path in PERMANENT_ROOT_DOC_CONTRACTS:
                rows.append(
                    {
                        "path": rel_path,
                        "metadata_status": "invalid_permanent_root_contract",
                        "metadata_missing_fields": "permanent root path is missing",
                        "doc_kind": PERMANENT_ROOT_DOC_CONTRACTS[rel_path]["kind"],
                        "doc_kind_source": "permanent_root_contract",
                        "doc_lifecycle": "permanent",
                        "repo_owner": "self",
                    }
                )
            continue
        text = repository_view.read_text(rel_path)
        if rel_path in PERMANENT_ROOT_DOC_CONTRACTS:
            contract_problems = _docs_scan.permanent_root_contract_problems(
                repository_view.root,
                rel_path,
                text,
                repository_view=repository_view,
            )
            if not contract_problems:
                continue
            rows.append(
                {
                    "path": rel_path,
                    "metadata_status": "invalid_permanent_root_contract",
                    "metadata_missing_fields": "; ".join(contract_problems),
                    "doc_kind": PERMANENT_ROOT_DOC_CONTRACTS[rel_path]["kind"],
                    "doc_kind_source": "permanent_root_contract",
                    "doc_lifecycle": "permanent",
                    "repo_owner": "self",
                }
            )
            continue
        classification = classify_doc(rel_path, text)
        if classification.metadata_status == "declared":
            continue
        rows.append(
            {
                "path": rel_path,
                "metadata_status": classification.metadata_status,
                "metadata_missing_fields": (
                    "; ".join(classification.metadata_missing_fields) or "none"
                ),
                "doc_kind": classification.doc_kind,
                "doc_kind_source": classification.doc_kind_source,
                "doc_lifecycle": classification.doc_lifecycle,
                "repo_owner": classification.repo_owner or "missing",
            }
        )
    return {
        "reviewed_files": len(rows),
        "missing_metadata_files": len(rows),
        "missing_metadata": rows,
        "top_missing_metadata": rows[:25],
    }


def _manifest_stats(manifest: Path) -> tuple[dict[str, object], list[str]]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    sources = data.get("sources", {})
    problems: list[str] = []
    source_values = sources.values() if isinstance(sources, dict) else []
    for source, metadata in sources.items() if isinstance(sources, dict) else []:
        if not Path(str(source)).exists():
            problems.append(f"manifest source missing on disk: {source}")
        if isinstance(metadata, dict):
            for key in ("pages_created", "pages_updated"):
                value = metadata.get(key)
                if value is not None and not isinstance(value, list):
                    problems.append(f"{source}: manifest {key} must be a list or null")
    stats = data.get("stats", {})
    if not isinstance(stats, dict):
        stats = {}
    return {
        "source_count": len(sources) if isinstance(sources, dict) else 0,
        "stats_total_sources_ingested": stats.get("total_sources_ingested"),
        "stats_total_pages": stats.get("total_pages"),
        "stats_total_projects": stats.get("total_projects"),
        "source_type_counts": dict(
            Counter(
                str(metadata.get("source_type", "(missing)"))
                for metadata in source_values
                if isinstance(metadata, dict)
            )
        ),
    }, problems


def _vault_link_health(md_files: Sequence[Path], vault: Path) -> dict[str, object]:
    page_keys: dict[str, str] = {}
    base_keys: dict[str, list[str]] = {}
    for path in md_files:
        rel = path.relative_to(vault).as_posix()
        no_ext = rel[:-3]
        page_keys[rel.lower()] = rel
        page_keys[no_ext.lower()] = rel
        base_keys.setdefault(path.stem.lower(), []).append(rel)

    incoming: Counter[str] = Counter()
    broken: list[dict[str, str]] = []
    for path in md_files:
        rel = path.relative_to(vault).as_posix()
        text = _link_health_text(_read_text(path))
        for raw_target in re.findall(r"\[\[([^\]\n]+)\]\]", text):
            target = raw_target.split("|", 1)[0].strip().replace("\\", "/")
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(("http:", "https:")):
                continue
            no_ext = target[:-3] if target.endswith(".md") else target
            candidates: list[str] = []
            if target.lower() in page_keys:
                candidates = [page_keys[target.lower()]]
            elif no_ext.lower() in page_keys:
                candidates = [page_keys[no_ext.lower()]]
            else:
                candidates = base_keys.get(Path(no_ext).name.lower(), [])
            if candidates:
                incoming[candidates[0]] += 1
            else:
                broken.append({"source": rel, "target": target})

    all_pages = {path.relative_to(vault).as_posix() for path in md_files}
    orphans = sorted(page for page in all_pages if incoming[page] == 0)
    return {
        "broken_wikilinks": len(broken),
        "broken_wikilink_sample": broken[:20],
        "orphan_pages": len(orphans),
        "orphan_page_sample": orphans[:20],
    }


def _link_health_text(text: str) -> str:
    """Exclude preserved source bodies from live vault link-health checks."""
    if (
        "disposition: private_history_source_copy" in text
        and "\n## Original Content\n" in text
    ):
        return text.split("\n## Original Content\n", 1)[0]
    return text


def audit_vault(vault: Path | None) -> tuple[list[AuditMessage], dict[str, object]]:
    messages: list[AuditMessage] = []
    if vault is None:
        return [
            AuditMessage(
                "warning",
                "vault",
                "Obsidian vault path is not configured; vault audit skipped",
            )
        ], {"vault_configured": False}
    if not vault.exists():
        return [
            AuditMessage(
                "blocker",
                str(vault),
                "configured Obsidian vault path does not exist",
            )
        ], {"vault_configured": True, "vault_exists": False}

    md_files = sorted(vault.rglob("*.md"))
    top_level = Counter(path.relative_to(vault).parts[0] for path in md_files)
    staging_md = (
        list((vault / "_staging").rglob("*.md"))
        if (vault / "_staging").exists()
        else []
    )
    raw_files = (
        [path for path in (vault / "_raw").glob("*") if path.name != ".gitkeep"]
        if (vault / "_raw").exists()
        else []
    )

    if staging_md:
        messages.append(
            AuditMessage(
                "blocker",
                "_staging",
                f"{len(staging_md)} staged wiki markdown files are pending review",
            )
        )
    if raw_files:
        messages.append(
            AuditMessage(
                "blocker",
                "_raw",
                f"{len(raw_files)} raw wiki files are pending ingest/classification",
            )
        )

    missing_visibility = 0
    missing_lifecycle = 0
    invalid_lifecycle = 0
    missing_tier = 0
    invalid_tier = 0
    for path in md_files:
        fm = parse_yaml_frontmatter(_read_text(path))
        tags = fm.get("tags", "")
        if "visibility/" not in tags:
            missing_visibility += 1
        lifecycle = fm.get("lifecycle")
        if not lifecycle:
            missing_lifecycle += 1
        elif lifecycle not in VALID_LIFECYCLES:
            invalid_lifecycle += 1
        tier = fm.get("tier")
        if not tier:
            missing_tier += 1
        elif tier not in VALID_TIERS:
            invalid_tier += 1

    for label, count in (
        ("missing visibility tag", missing_visibility),
        ("missing lifecycle", missing_lifecycle),
        ("invalid lifecycle", invalid_lifecycle),
        ("missing tier", missing_tier),
        ("invalid tier", invalid_tier),
    ):
        if count:
            messages.append(
                AuditMessage("warning", "vault", f"{count} pages have {label}")
            )

    manifest_summary: dict[str, object] = {"manifest_exists": False}
    manifest = vault / ".manifest.json"
    if manifest.exists():
        manifest_summary, manifest_problems = _manifest_stats(manifest)
        manifest_summary["manifest_exists"] = True
        expected_sources = manifest_summary.get("source_count")
        expected_pages = len(md_files)
        if manifest_summary.get("stats_total_sources_ingested") != expected_sources:
            messages.append(
                AuditMessage(
                    "blocker",
                    ".manifest.json",
                    "manifest stats.total_sources_ingested does not match source count",
                )
            )
        if manifest_summary.get("stats_total_pages") != expected_pages:
            messages.append(
                AuditMessage(
                    "blocker",
                    ".manifest.json",
                    "manifest stats.total_pages does not match vault markdown count",
                )
            )
        for problem in manifest_problems[:20]:
            messages.append(AuditMessage("blocker", ".manifest.json", problem))
    else:
        messages.append(
            AuditMessage("blocker", ".manifest.json", "manifest is missing")
        )

    link_health = _vault_link_health(md_files, vault)
    if link_health["broken_wikilinks"]:
        messages.append(
            AuditMessage(
                "warning",
                "vault",
                (
                    f"{link_health['broken_wikilinks']} approximate broken "
                    "wikilinks remain"
                ),
            )
        )

    summary: dict[str, object] = {
        "vault_configured": True,
        "vault_exists": True,
        "vault_path": str(vault),
        "vault_markdown_files": len(md_files),
        "vault_by_top_level": dict(sorted(top_level.items())),
        "staging_markdown_files": len(staging_md),
        "raw_top_level_files": len(raw_files),
        "frontmatter": {
            "missing_visibility": missing_visibility,
            "missing_lifecycle": missing_lifecycle,
            "invalid_lifecycle": invalid_lifecycle,
            "missing_tier": missing_tier,
            "invalid_tier": invalid_tier,
        },
        "manifest": manifest_summary,
        "link_health": link_health,
    }
    return messages, summary


def run_audit(
    root: Path = ROOT,
    vault: Path | None = None,
    *,
    include_vault: bool = True,
    fail_on_completed_transient: bool = False,
) -> AuditResult:
    repo_messages, repo_summary = audit_repo(
        root,
        fail_on_completed_transient=fail_on_completed_transient,
    )
    if include_vault:
        resolved_vault = resolve_vault(root, vault)
        vault_messages, vault_summary = audit_vault(resolved_vault)
    else:
        vault_messages = []
        vault_summary = {
            "vault_configured": vault is not None,
            "vault_skipped": True,
        }
    messages = [*repo_messages, *vault_messages]
    summary = {"repo": repo_summary, "vault": vault_summary}
    return AuditResult(tuple(messages), summary)


def write_routing_manifest_tsv(result: AuditResult, path: Path) -> None:
    review = result.summary["repo"]["docs_routing_review"]
    rows = [
        *review["candidates"],
        *review.get("route_retained_reviews", ()),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=ROUTING_MANIFEST_FIELDS,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_topic_clusters_tsv(result: AuditResult, path: Path) -> None:
    clusters = result.summary["repo"]["docs_routing_review"]["topic_clusters"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=TOPIC_CLUSTER_MANIFEST_FIELDS,
            delimiter="\t",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(clusters)


def write_topic_index_readmes(result: AuditResult, directory: Path) -> None:
    clusters_obj = result.summary["repo"]["docs_routing_review"]["topic_clusters"]
    clusters: list[dict[str, object]] = []
    if isinstance(clusters_obj, list):
        clusters = [cluster for cluster in clusters_obj if isinstance(cluster, dict)]
    _write_topic_index_readmes(clusters, directory)


def format_text(result: AuditResult) -> str:
    lines = ["docs management audit"]
    lines.append(f"blockers: {len(result.blockers)}")
    warning_count = sum(1 for msg in result.messages if msg.severity == "warning")
    lines.append(f"warnings: {warning_count}")
    if result.messages:
        lines.append("")
    for msg in result.messages:
        lines.append(f"- {msg.severity}: {msg.path}: {msg.message}")
    lines.append("")
    lines.append(json.dumps(result.summary, indent=2, ensure_ascii=False))
    return "\n".join(lines)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--vault", type=Path)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of text.",
    )
    parser.add_argument(
        "--repo-only",
        action="store_true",
        help="Skip private Obsidian vault checks and report repo state only.",
    )
    parser.add_argument(
        "--fail-on-completed-transient",
        action="store_true",
        help=(
            "Fail when implemented/superseded/archived transient docs remain."
        ),
    )
    parser.add_argument(
        "--routing-manifest-tsv",
        type=Path,
        help="Write actionable docs/superpowers routing candidates as TSV.",
    )
    parser.add_argument(
        "--topic-clusters-tsv",
        type=Path,
        help="Write docs/superpowers topic-owner clusters as TSV.",
    )
    parser.add_argument(
        "--topic-index-dir",
        type=Path,
        help=(
            "Write temporary index-only topic README files under this "
            "directory, normally ignored output/docs-topic-indexes."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_audit(
        args.root.resolve(),
        args.vault,
        include_vault=not args.repo_only,
        fail_on_completed_transient=args.fail_on_completed_transient,
    )
    if args.routing_manifest_tsv is not None:
        write_routing_manifest_tsv(result, args.routing_manifest_tsv)
    if args.topic_clusters_tsv is not None:
        write_topic_clusters_tsv(result, args.topic_clusters_tsv)
    if args.topic_index_dir is not None:
        write_topic_index_readmes(result, args.topic_index_dir)
    if args.json:
        print(
            json.dumps(
                {
                    "blockers": [msg.__dict__ for msg in result.blockers],
                    "messages": [msg.__dict__ for msg in result.messages],
                    "summary": result.summary,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(format_text(result))
    return 1 if result.blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
