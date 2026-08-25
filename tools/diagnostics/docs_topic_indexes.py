"""Render generated docs topic index README files for ignored review output."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from tools.diagnostics.docs_routing_review import _topic_slug, _topic_title


def _manifest_path_values(value: object) -> list[str]:
    if not isinstance(value, str) or value == "none":
        return []
    return [item.strip() for item in value.split(";") if item.strip()]


def _markdown_path_list(paths: Sequence[str]) -> list[str]:
    if not paths:
        return ["- none"]
    return [f"- `{path}`" for path in paths]


def _topic_index_markdown(cluster: dict[str, object]) -> str:
    topic_key = str(cluster["topic_key"])
    slug = _topic_slug(topic_key)
    repo_owner_hint = str(cluster["repo_owner_hint"])
    repo_topic_folder = str(cluster["suggested_repo_topic_folder"])
    obsidian_topic_folder = str(cluster["suggested_obsidian_topic_folder"])
    topic_next_action = str(cluster["topic_next_action"])
    digestion_review_count = str(cluster.get("digestion_review_count", "0"))
    digestion_status_counts = str(
        cluster.get("digestion_status_counts", "none")
    )
    support_retention_counts = str(
        cluster.get("support_retention_counts", "none")
    )
    title = _topic_title(topic_key)
    owner_paths = _manifest_path_values(cluster.get("owner_paths"))
    candidate_paths = _manifest_path_values(cluster.get("candidate_paths"))
    support_paths = _manifest_path_values(cluster.get("supporting_sample_paths"))
    bound_support_paths = _manifest_path_values(
        cluster.get("bound_support_sample_paths")
    )
    compressible_support_paths = _manifest_path_values(
        cluster.get("compressible_support_sample_paths")
    )

    lines = [
        f"# {title} Topic Index",
        "",
        "Doc placement: ignored_artifact",
        "Doc kind: manifest",
        "Doc lifecycle: active",
        f"Repo owner: {repo_owner_hint}",
        (
            "Doc exit rule: Regenerate on demand from docs_management_audit; "
            "do not track as durable repo documentation."
        ),
        "",
        "This file is a navigation and cleanup index. It does not define product "
        "behavior, validation policy, matrix authority, or selected values.",
        "",
        "## Authority",
        "",
        f"- Canonical owner hint: `{repo_owner_hint}`",
        f"- Generated index folder: `{repo_topic_folder}`",
        f"- Obsidian archive lane: `{obsidian_topic_folder}`",
        "",
        "## Cluster State",
        "",
        f"- Status: `{cluster['topic_cluster_status']}`",
        f"- Files: {cluster['file_count']}",
        f"- Owner-like files: {cluster['topic_owner_count']}",
        f"- Support files: {cluster['supporting_count']}",
        f"- Cleanup candidates: {cluster['candidate_count']}",
        f"- Delegated handoff files: {cluster['delegated_handoff_count']}",
        f"- Digestion review files: {digestion_review_count}",
        f"- Digestion status counts: `{digestion_status_counts}`",
        f"- Support retention counts: `{support_retention_counts}`",
        "",
        "## Next Action",
        "",
        f"- {topic_next_action}",
        "",
        "## Support Retention",
        "",
        "Bound support surfaces are still authority/status anchors, active "
        "packets, mechanical referrer anchors, archived retention records, "
        "or exact references. Compressible support surfaces are the first "
        "place to absorb stable claims into owners and move long originals "
        "to Obsidian after a fresh referrer scan. Retired placement values are "
        "invalid inputs, not retained support surfaces.",
        "",
        "### Bound Support Samples",
        "",
        *_markdown_path_list(bound_support_paths),
        "",
        "### Compressible Support Samples",
        "",
        *_markdown_path_list(compressible_support_paths),
        "",
        "## Owner-Like Files To Review",
        "",
        *_markdown_path_list(owner_paths),
        "",
        "## Cleanup Candidates",
        "",
        *_markdown_path_list(candidate_paths),
        "",
        "## Support Samples",
        "",
        *_markdown_path_list(support_paths),
        "",
        "## Source",
        "",
        "- Generated from `tools/diagnostics/docs_management_audit.py`.",
        "- Inputs are the current checkout and the audit invocation that "
        "generated this ignored index.",
        "",
    ]
    if not slug:
        raise ValueError("topic slug cannot be empty")
    return "\n".join(lines)


def write_topic_index_readmes(
    clusters: Sequence[dict[str, object]],
    directory: Path,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        topic_key = str(cluster["topic_key"])
        topic_dir = directory / _topic_slug(topic_key)
        topic_dir.mkdir(parents=True, exist_ok=True)
        (topic_dir / "README.md").write_text(
            _topic_index_markdown(cluster),
            encoding="utf-8",
        )
