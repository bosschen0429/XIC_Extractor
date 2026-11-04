from __future__ import annotations

import csv
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools.diagnostics.docs_management_audit import (
    run_audit,
    write_routing_manifest_tsv,
    write_topic_clusters_tsv,
    write_topic_index_readmes,
)
from tools.diagnostics.docs_policy import CONTROL_PLANE_PATH

CURRENT_HANDOFF = (
    "docs/superpowers/handoffs/current/codex-docs-cleanup-official-docs-and-handoff.md"
)
PROJECT_ROOT = Path(__file__).parents[1]
MAINTAINED_VALIDATION_HTML = (
    "docs/superpowers/validation/evidence_chain_status_guide.html",
    "docs/superpowers/validation/evidence_overlay_interpretation_guide.html",
)


def _isolated_git_subprocess_env() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }


@pytest.fixture(autouse=True)
def _clear_outer_git_repository_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)


def test_isolated_git_subprocess_env_drops_outer_repository_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_INDEX_FILE", "outer-index")
    monkeypatch.setenv("GIT_WORK_TREE", "outer-worktree")

    env = _isolated_git_subprocess_env()

    assert "GIT_INDEX_FILE" not in env
    assert "GIT_WORK_TREE" not in env
    assert env["PATH"] == os.environ["PATH"]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_permanent_root_contracts(root: Path) -> None:
    route_targets = (
        "docs/agent/parameter-settings.md",
        "docs/product/README.md",
        "docs/agent/product-validation-contract.md",
        "docs/agent/execution-gates.md",
        "tools/diagnostics/INDEX.md",
        "docs/product/productization.md",
        "docs/agent/codex-operating-system.md",
        "docs/user/README.md",
        "docs/agent/obsidian-handoff-contract.md",
    )
    for rel_path in route_targets:
        _write(root / rel_path, f"# {Path(rel_path).stem}\n")
    _write(
        root / "docs/agent/codex-operating-system.md",
        (PROJECT_ROOT / "docs/agent/codex-operating-system.md").read_text(
            encoding="utf-8"
        ),
    )
    _write(
        root / "docs/lc-msms-evidence-rules.md",
        "\n".join(
            [
                "# LC-MS/MS evidence rules",
                "",
                "Doc placement: formal_repo_doc",
                "Doc kind: product_doc",
                "Doc lifecycle: active",
                "Repo owner: docs/lc-msms-evidence-rules.md",
                "Doc exit rule: retire after a replacement owner absorbs the rules.",
            ]
        )
        + "\n",
    )
    _write(
        root / "AGENTS.md",
        "\n".join(
            [
                "# XIC Extractor Agent Contract",
                "",
                "## Task Routes",
                "",
                "- [Parameters](docs/agent/parameter-settings.md)",
                "- [Product](docs/product/README.md)",
                "- [Validation](docs/agent/product-validation-contract.md)",
                "- [Architecture](docs/architecture-contract.md)",
                "- [Execution](docs/agent/execution-gates.md)",
                "- [Diagnostics](tools/diagnostics/INDEX.md)",
                "- [Productization](docs/product/productization.md)",
                "- [Layout](docs/project-layout.md)",
                "- [Agent system](docs/agent/codex-operating-system.md)",
            ]
        )
        + "\n",
    )
    _write(
        root / "README.md",
        "\n".join(
            [
                "# XIC Extractor",
                "",
                "## Documentation",
                "",
                "- [Users](docs/user/README.md)",
                "- [Product](docs/product/README.md)",
                "- [Architecture](docs/architecture-contract.md)",
                "- [Layout](docs/project-layout.md)",
                "- [Evidence](docs/lc-msms-evidence-rules.md)",
            ]
        )
        + "\n",
    )
    _write(
        root / "docs/architecture-contract.md",
        "\n".join(
            [
                "# Architecture Contract",
                "",
                "This document owns the dependency contract.",
                "",
                "## Dependency Direction",
                "",
                "Domain points inward.",
                "",
                "## Ownership Map",
                "",
                "Owners are explicit.",
            ]
        )
        + "\n",
    )
    _write(
        root / "docs/project-layout.md",
        "\n".join(
            [
                "# Project Layout",
                "",
                "[Architecture](architecture-contract.md)",
                "[Product](product)",
                "[Handoff](agent/obsidian-handoff-contract.md)",
                "",
                "## Permanent root document contract",
                "",
                "目錄收納規則單一可信來源",
                "",
                "source-of-truth 與歷程邊界",
            ]
        )
        + "\n",
    )


def _clean_repo(root: Path) -> None:
    _write_permanent_root_contracts(root)
    _write(
        root / CONTROL_PLANE_PATH,
        (PROJECT_ROOT / CONTROL_PLANE_PATH).read_text(encoding="utf-8"),
    )


def _git(root: Path, *args: str) -> None:
    """Run git in a test repository, reporting git's own message on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        env=_isolated_git_subprocess_env(),
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} exited {result.returncode} in {root}: "
        f"{result.stderr.strip()}"
    )


def _init_and_stage_repo(root: Path) -> dict[str, str]:
    _git(root, "init")
    # Fixture docs paths under a deep runner temp root pass Windows' 260-char limit.
    _git(root, "config", "core.longpaths", "true")
    _git(root, "add", ".")
    return _isolated_git_subprocess_env()


def _clean_vault(vault: Path) -> None:
    _write(
        vault / "index.md",
        "\n".join(
            [
                "---",
                "title: Index",
                "tags: [visibility/internal]",
                "lifecycle: draft",
                "tier: supporting",
                "---",
                "",
                "# Index",
            ]
        ),
    )
    manifest = {
        "version": 1,
        "last_updated": "2026-06-26T00:00:00Z",
        "sources": {},
        "projects": {},
        "stats": {
            "total_sources_ingested": 0,
            "total_pages": 1,
            "total_projects": 0,
        },
    }
    (vault / ".manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )


def _run_audit(
    root: Path,
    vault: Path | None = None,
    *,
    repo_only: bool = False,
    preserve_index: bool = False,
):
    if root.resolve() != PROJECT_ROOT:
        if preserve_index:
            assert (root / ".git").exists()
        elif not (root / ".git").exists():
            _init_and_stage_repo(root)
        else:
            _git(root, "add", "--all")
    return run_audit(
        root,
        vault,
        include_vault=not repo_only,
    )


def test_repo_only_audit_skips_vault_blockers(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "missing-vault"
    _clean_repo(root)

    result = _run_audit(root, vault, repo_only=True)

    assert result.summary["vault"]["vault_skipped"] is True
    assert not any(msg.path == str(vault) for msg in result.messages)
    assert not any(msg.path == ".manifest.json" for msg in result.messages)


def test_repo_only_completed_transient_spec_is_advisory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    spec_path = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    _write(
        root / spec_path,
        "\n".join(
            [
                "# Backfill spec",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: spec",
                "Doc lifecycle: implemented",
                "Repo owner: docs/product/backfill.md",
                "Doc exit rule: retire after product owner absorption.",
                "",
                "Durable backfill decision now belongs in product docs.",
            ]
        ),
    )

    result = _run_audit(
        root,
        repo_only=True,
    )

    review = result.summary["repo"]["completed_transient_review"]
    assert review["completed_files"] == 1
    assert review["top_completed_files"][0]["path"] == spec_path
    _write(
        root / spec_path,
        (root / spec_path)
        .read_text(encoding="utf-8")
        .replace("Doc lifecycle: implemented", "Doc lifecycle: active"),
    )
    active_result = _run_audit(root, repo_only=True)
    assert result.blockers == active_result.blockers


def test_repo_only_archived_productization_summary_inventory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    summary_path = (
        "docs/superpowers/productization/evidence/2026-07-01-framework-phase-summary.md"
    )
    _write(
        root / summary_path,
        "\n".join(
            [
                "# Productization phase summary",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: report",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/productization.md",
                "Doc exit rule: remove after owner absorption.",
                "",
                "Completed branch history.",
            ]
        ),
    )

    result = _run_audit(
        root,
        repo_only=True,
    )

    review = result.summary["repo"]["completed_transient_review"]
    assert review["completed_files"] == 1
    assert review["top_completed_files"][0]["path"] == summary_path


def test_repo_only_active_spec_is_excluded_from_completed_inventory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    _write(
        root / "docs/superpowers/specs/2026-07-01-backfill-spec.md",
        "\n".join(
            [
                "# Backfill spec",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Repo owner: docs/product/backfill.md",
                "Doc exit rule: retire after implementation and absorption.",
                "",
                "Active execution contract.",
            ]
        ),
    )

    result = _run_audit(
        root,
        repo_only=True,
    )

    review = result.summary["repo"]["completed_transient_review"]
    assert review["completed_files"] == 0


def test_repo_only_completed_transient_stub_inventory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    stub_path = "docs/superpowers/specs/2026-07-01-backfill-spec.md"
    _write(
        root / stub_path,
        "\n".join(
            [
                "# Backfill spec",
                "",
                "Doc placement: repo_stub_plus_obsidian",
                "Doc kind: spec",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/backfill.md",
                "Doc exit rule: retire after exact referrers no longer need this path.",
                "",
                "Source-copy stub; current claims live in docs/product/backfill.md.",
            ]
        ),
    )

    result = _run_audit(
        root,
        repo_only=True,
    )

    review = result.summary["repo"]["completed_transient_review"]
    assert review["completed_files"] == 1


def test_repo_only_completed_active_stub_inventory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    stub_path = "docs/superpowers/plans/2026-07-01-backfill-plan.md"
    _write(
        root / stub_path,
        "\n".join(
            [
                "# Backfill plan",
                "",
                "Doc placement: repo_active_stub",
                "Doc kind: plan",
                "Doc lifecycle: implemented",
                "Repo owner: docs/product/backfill.md",
                "Doc exit rule: retire after implementation and absorption.",
                "",
                "Objective: keep work recoverable.",
                "Scope: docs workflow only.",
                "Constraints: no private context required.",
                "Next 1-3 actions: none.",
                "Verification: focused pytest.",
                "Stop rule: stop after product owner absorption.",
            ]
        ),
    )

    result = _run_audit(
        root,
        repo_only=True,
    )

    review = result.summary["repo"]["completed_transient_review"]
    assert review["completed_files"] == 1
    assert review["top_completed_files"][0]["path"] == stub_path


def test_tracked_current_handoff_is_a_retired_lane_blocker(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_vault(vault)
    _write(
        root / CURRENT_HANDOFF,
        "Status: batches are staged; no commit has been made.\n",
    )

    result = _run_audit(root, vault)

    assert any("retired docs lanes" in msg.message for msg in result.blockers)


def test_manifest_stats_mismatch_is_a_blocker(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    manifest_path = vault / ".manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stats"]["total_pages"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_audit(root, vault)

    assert any("stats.total_pages" in msg.message for msg in result.blockers)


def test_local_machine_path_is_reported_as_warning(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    _write(
        root / "docs/agent/parameter-settings.md",
        "RAW root: C:\\Xcalibur\n",
    )

    result = _run_audit(root, vault)

    assert result.blockers == ()
    assert any(
        msg.severity == "warning" and "local/private path exposure" in msg.message
        for msg in result.messages
    )


def test_local_machine_path_warning_uses_staged_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    rel_path = "config/env.example"
    _write(root / rel_path, "XIC_RAW_ROOT=C:\\Xcalibur\\data\n")
    _init_and_stage_repo(root)
    _write(root / rel_path, "XIC_RAW_ROOT=portable\n")

    result = _run_audit(root, repo_only=True, preserve_index=True)

    top_hits = result.summary["repo"]["top_local_path_files"]
    assert any(item["path"] == rel_path for item in top_hits)


def test_docs_structure_review_reports_root_scatter(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    _write(
        root / "docs/2026-07-01-branch-report.md",
        "# Branch report\n\nHistorical branch note.\n",
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_structure_review"]
    assert review["root_scatter_files"] == 1
    assert review["top_root_scatter"] == ["docs/2026-07-01-branch-report.md"]
    assert any(
        msg.severity == "warning" and "docs root has non-allowlisted" in msg.message
        for msg in result.messages
    )


def test_docs_structure_review_reports_retired_lanes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    _write(
        root / "docs/superpowers/notes/2026-07-01-observation.md",
        "# Observation\n\nPrivate-first note.\n",
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_structure_review"]
    assert review["retired_lane_files"] == 1
    assert review["top_retired_lanes"] == [
        {
            "path": "docs/superpowers/notes/2026-07-01-observation.md",
            "retired_dir": "docs/superpowers/notes/",
        }
    ]
    assert any(
        msg.severity == "blocker" and "retired docs lanes" in msg.message
        for msg in result.messages
    )


def test_docs_structure_review_reports_deepresearch_as_retired_lane(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    _write(
        root / "docs/deepresearch/2026-07-01-literature-note.md",
        "# Literature note\n\nPrivate research context.\n",
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_structure_review"]
    assert review["retired_lane_files"] == 1
    assert review["top_retired_lanes"] == [
        {
            "path": "docs/deepresearch/2026-07-01-literature-note.md",
            "retired_dir": "docs/deepresearch/",
        }
    ]


def test_repo_docs_routing_review_reports_private_history_candidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/plans/2026-07-01-backfill-implementation-plan.md"
    _write(
        root / candidate_path,
        "\n".join(
            [
                "# Example implementation plan",
                "",
                "Implementation diary and command transcript.",
                "Run `uv run pytest` after `git add`.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 1
    assert review["disposition_counts"] == {"formalize_then_obsidian": 1}
    assert review["doc_route_counts"] == {"repo_distilled_plus_obsidian_original": 1}
    assert review["candidate_digestion_status_counts"] == {
        "needs_distillation_to_owner_and_obsidian": 1
    }
    assert review["lifecycle_status_counts"] == {"missing_lifecycle": 1}
    assert review["top_candidates"][0]["path"] == candidate_path
    assert review["top_candidates"][0]["doc_kind"] == "plan"
    assert review["top_candidates"][0]["doc_kind_source"] == "inferred"
    assert review["top_candidates"][0]["doc_lifecycle"] == "unknown"
    assert review["top_candidates"][0]["doc_exit_rule"] == "missing"
    assert review["top_candidates"][0]["lifecycle_status"] == "missing_lifecycle"
    assert "wiki-ingest/wiki-update" in review["top_candidates"][0]["wiki_skill_route"]
    assert (
        "distill stable repo claims" in review["top_candidates"][0]["wiki_next_action"]
    )
    assert review["top_candidates"][0]["doc_route"] == (
        "repo_distilled_plus_obsidian_original"
    )
    assert review["top_candidates"][0]["repo_body_role"] == "distilled_repo_claim"
    assert review["top_candidates"][0]["digestion_status"] == (
        "needs_distillation_to_owner_and_obsidian"
    )
    assert (
        "distill stable repo claims"
        in review["top_candidates"][0]["digestion_next_action"]
    )
    assert review["top_candidates"][0]["obsidian_original_hint"] == (
        f"source_repo_path:{candidate_path}"
    )
    assert review["top_candidates"][0]["repo_pointer_required"] == "yes"
    assert review["top_candidates"][0]["information_value"] == (
        "backfill and quant matrix"
    )
    assert "docs/product/backfill.md" in review["top_candidates"][0]["repo_owner_hint"]
    assert review["top_candidates"][0]["obsidian_lane"].endswith("/Plans")
    assert any(
        msg.severity == "warning" and "repo docs routing review" in msg.message
        for msg in result.messages
    )


def test_repo_docs_routing_review_prefers_topic_over_generic_roadmap(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = (
        "docs/superpowers/plans/2026-07-01-cid-nl-discovery-product-roadmap.md"
    )
    _write(
        root / candidate_path,
        "# Discovery roadmap\n\nRun `uv run pytest` after migration.\n",
    )

    result = _run_audit(root, vault)

    candidate = result.summary["repo"]["docs_routing_review"]["top_candidates"][0]
    assert candidate["path"] == candidate_path
    assert candidate["information_value"] == "discovery"
    assert candidate["repo_owner_hint"] == "docs/product/discovery.md"
    assert candidate["doc_route"] == "repo_distilled_plus_obsidian_original"


def test_repo_docs_routing_review_requires_fixed_route_decision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/goals/2026-07-01-open-question.md"
    _write(
        root / candidate_path,
        "# Open question\n\nThis needs classification before cleanup.\n",
    )

    result = _run_audit(root, vault)

    candidate = result.summary["repo"]["docs_routing_review"]["top_candidates"][0]
    assert candidate["path"] == candidate_path
    assert candidate["disposition"] == "needs_human_review"
    assert candidate["doc_kind"] == "goal"
    assert candidate["doc_kind_source"] == "inferred"
    assert candidate["doc_route"] == "needs_route_decision"
    assert candidate["repo_body_role"] == "route_pending"
    assert candidate["obsidian_original_hint"] == f"source_repo_path:{candidate_path}"
    assert candidate["repo_pointer_required"] == "decision_pending"
    assert candidate["wiki_skill_route"] == (
        "wiki-status -> wiki-query -> critical review before write"
    )
    assert "decide route before any Obsidian write" in candidate["wiki_next_action"]
    assert "choose one fixed route" in candidate["required_before_move"]
    assert "lifecycle/exit rule" in candidate["required_before_move"]


def test_repo_docs_routing_review_reports_exact_referrers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/notes/backfill-review-note.md"
    _write(
        root / candidate_path,
        "# Backfill review\n\nReview rationale and branch sequencing.\n",
    )
    _write(
        root / "docs/product/backfill.md",
        f"# Backfill\n\nHistorical source: `{candidate_path}`.\n",
    )

    result = _run_audit(root, vault)

    candidate = result.summary["repo"]["docs_routing_review"]["top_candidates"][0]
    assert candidate["path"] == candidate_path
    assert candidate["referrer_status"] == "exact_repo_referrers_present"
    assert candidate["exact_referrers"] == 1
    assert candidate["sample_referrers"] == "docs/product/backfill.md"
    assert candidate["destructive_allowed_now"] == "no"
    assert candidate["obsidian_original_hint"] == (f"source_repo_path:{candidate_path}")
    assert candidate["repo_pointer_required"] == "yes"


def test_write_routing_manifest_tsv_writes_reviewable_candidate_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/plans/2026-07-01-backfill-plan.md"
    _write(
        root / candidate_path,
        "# Backfill plan\n\nImplementation diary and command transcript.\n",
    )
    manifest_path = tmp_path / "routing-manifest.tsv"

    result = _run_audit(root, vault)
    write_routing_manifest_tsv(result, manifest_path)

    with manifest_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    candidate_row = next(row for row in rows if row["path"] == candidate_path)
    assert candidate_row["doc_kind"] == "plan"
    assert candidate_row["doc_lifecycle"] == "unknown"
    assert candidate_row["lifecycle_status"] == "missing_lifecycle"
    assert "wiki-ingest/wiki-update" in candidate_row["wiki_skill_route"]
    assert candidate_row["doc_route"] == "repo_distilled_plus_obsidian_original"
    assert candidate_row["repo_body_role"] == "distilled_repo_claim"
    assert candidate_row["digestion_status"] == (
        "needs_distillation_to_owner_and_obsidian"
    )
    assert "distill stable repo claims" in candidate_row["digestion_next_action"]
    assert candidate_row["destructive_allowed_now"] == "no"
    assert candidate_row["information_value"] == "backfill and quant matrix"
    assert candidate_row["obsidian_original_hint"] == (
        f"source_repo_path:{candidate_path}"
    )
    assert candidate_row["repo_pointer_required"] == "yes"
    assert candidate_row["referrer_status"] == "none"
    assert candidate_row["topic_key"] == "backfill and quant matrix"
    assert candidate_row["topic_role"] == "needs_distillation_or_route"
    assert candidate_row["topic_cluster_status"] == (
        "external_owner_with_cleanup_candidates"
    )


def test_repo_docs_routing_review_preserves_declared_lifecycle_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/plans/2026-07-01-active-backfill-plan.md"
    _write(
        root / candidate_path,
        "\n".join(
            [
                "# Active Backfill Plan",
                "",
                "Doc kind: plan",
                "Doc lifecycle: active",
                (
                    "Doc exit rule: close PR, distill claims to "
                    "docs/product/backfill.md, then move original to Obsidian."
                ),
                "",
                "Implementation diary and command transcript.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    candidate = result.summary["repo"]["docs_routing_review"]["top_candidates"][0]
    assert candidate["doc_kind"] == "plan"
    assert candidate["doc_kind_source"] == "declared"
    assert candidate["doc_lifecycle"] == "active"
    assert candidate["doc_exit_rule"].startswith("close PR")
    assert candidate["lifecycle_status"] == "declared"


def test_write_routing_manifest_tsv_is_not_limited_to_top_candidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    for index in range(30):
        _write(
            root / (f"docs/superpowers/plans/2026-07-{index + 1:02d}-bulk-plan.md"),
            "# Bulk plan\n\nImplementation diary and command transcript.\n",
        )
    manifest_path = tmp_path / "routing-manifest.tsv"

    result = _run_audit(root, vault)
    write_routing_manifest_tsv(result, manifest_path)

    with manifest_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 30
    assert len(review["top_candidates"]) == 25
    candidate_paths = {
        row["path"]
        for row in rows
        if row["topic_role"] == "needs_distillation_or_route"
    }
    assert len(candidate_paths) == 30


def test_write_routing_manifest_tsv_includes_retained_followup_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    support_path = "docs/superpowers/validation/backfill-run.md"
    referrer_path = "docs/superpowers/schemas/README.md"
    _write(
        root / support_path,
        "\n".join(
            [
                "# Backfill support",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: validation_artifact",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/backfill.md",
                "Doc exit rule: retire after exact referrers point at the owner.",
                "",
                "Backfill validation support.",
            ]
        ),
    )
    _write(
        root / referrer_path,
        "\n".join(
            [
                "# Schemas",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: manifest",
                "Doc lifecycle: active",
                "Repo owner: docs/product/productization.md",
                "Doc exit rule: update when planning surfaces change.",
                "",
                f"Historical support lives at `{support_path}`.",
            ]
        ),
    )
    manifest_path = tmp_path / "routing-manifest.tsv"

    result = _run_audit(root, vault)
    write_routing_manifest_tsv(result, manifest_path)

    with manifest_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    support_row = next(row for row in rows if row["path"] == support_path)
    assert support_row["candidate"] == "False"
    assert support_row["support_retention_reason"] == "exact_referrer_bound_support"
    assert support_row["referrer_status"] == "exact_repo_referrers_present"


def test_generated_routing_manifest_is_not_counted_as_candidate_referrer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/plans/2026-07-01-backfill-plan.md"
    _write(
        root / candidate_path,
        "# Backfill plan\n\nImplementation diary and command transcript.\n",
    )
    manifest_path = (
        root / "docs/superpowers/file-management/docs-cleanup/"
        "2026-07-01_docs-superpowers-routing-manifest.tsv"
    )

    first_result = _run_audit(root, vault)
    write_routing_manifest_tsv(first_result, manifest_path)
    second_result = _run_audit(root, vault)

    candidate = second_result.summary["repo"]["docs_routing_review"]["top_candidates"][
        0
    ]
    assert candidate["path"] == candidate_path
    assert candidate["referrer_status"] == "none"


def test_generated_topic_indexes_are_not_counted_as_candidate_referrers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/plans/2026-07-01-backfill-plan.md"
    _write(
        root / candidate_path,
        "# Backfill plan\n\nImplementation diary and command transcript.\n",
    )
    _write(
        root / "docs/superpowers/file-management/docs-cleanup/"
        "2026-07-01_docs-superpowers-topic-clusters.tsv",
        f"path\n{candidate_path}\n",
    )
    _write(
        root / "output/docs-topic-indexes/backfill-and-quant-matrix/README.md",
        f"# Backfill Index\n\n- `{candidate_path}`\n",
    )

    result = _run_audit(root, vault)

    candidate = result.summary["repo"]["docs_routing_review"]["top_candidates"][0]
    assert candidate["path"] == candidate_path
    assert candidate["referrer_status"] == "none"


@pytest.mark.parametrize(
    "placement",
    ["private_obsidian_note", "ignored_artifact"],
)
def test_repo_docs_routing_review_blocks_non_repo_placement(
    tmp_path: Path,
    placement: str,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/notes/private-review-note.md"
    _write(
        root / candidate_path,
        "\n".join(
            [
                "# Private review note",
                "",
                f"Doc placement: {placement}",
                "",
                "Review rationale and branch sequencing.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["disposition_counts"] == {"invalid_repo_placement": 1}
    assert review["doc_route_counts"] == {"obsidian_original": 1}
    assert review["top_candidates"][0]["doc_route"] == "obsidian_original"
    assert review["top_candidates"][0]["repo_body_role"] == "original_not_repo"
    assert review["top_candidates"][0]["obsidian_lane"].endswith(
        "Notes Decisions Closeouts"
    )
    assert any(
        msg.severity == "blocker"
        and "tracked repo docs declare invalid or retired placement" in msg.message
        for msg in result.messages
    )


def test_repo_docs_routing_review_surfaces_unbound_validation_for_absorption(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    validation_path = "docs/superpowers/validation/current-packet.md"
    _write(
        root / validation_path,
        "# Validation Packet\n\nRun `uv run pytest` to verify the checker.\n",
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 1
    candidate = review["top_candidates"][0]
    assert candidate["path"] == validation_path
    assert candidate["disposition"] == "review_unbound_validation_surface"
    assert candidate["exact_referrers"] == 0
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if validation_path in item["candidate_paths"]
    )
    assert cluster["candidate_count"] == 1
    assert candidate["digestion_status"] != "support_surface_retained"
    assert "owner" in candidate["digestion_next_action"]


def test_repo_docs_routing_review_retains_validation_family_with_consumer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    family = "docs/superpowers/validation/current-packet"
    validation_path = f"{family}/README.md"
    summary_path = f"{family}/current_packet_summary.json"
    _write(root / validation_path, "# Validation Packet\n")
    _write(root / summary_path, "{}\n")
    _write(root / "scripts/check_current_packet.py", f'PATH = "{summary_path}"\n')

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 0
    assert review["all_disposition_counts"]["keep_repo_validation_or_fixture"] == 1


def test_repo_docs_routing_review_keeps_canonical_superpowers_readme(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    readme_path = "docs/superpowers/README.md"
    _write(
        root / readme_path,
        (
            "# Superpowers\n\n"
            "Private history belongs in Obsidian; repo docs keep routing rules.\n"
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 0
    assert review["all_disposition_counts"]["keep_repo_canonical"] == 1
    assert review["all_doc_route_counts"]["repo_product_doc"] >= 1
    assert review["top_topic_clusters"][0]["topic_owner_count"] == 1


def test_repo_docs_routing_review_reports_canonical_missing_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    readme_path = "docs/superpowers/README.md"
    _write(
        root / readme_path,
        "# Superpowers\n\nCanonical routing guide without governance metadata.\n",
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 0
    assert review["metadata_review_files"] == 1
    assert review["metadata_status_counts"]["missing_metadata"] >= 1
    metadata_review = review["top_metadata_reviews"][0]
    assert metadata_review["path"] == readme_path
    assert metadata_review["metadata_status"] == "missing_metadata"
    assert metadata_review["metadata_missing_fields"] == (
        "Doc placement; Repo owner; Doc kind; Doc lifecycle; Doc exit rule"
    )
    retained_review_paths = {
        item["path"] for item in review["top_route_retained_reviews"]
    }
    assert readme_path in retained_review_paths
    assert any(
        msg.severity == "warning" and "repo docs metadata review" in msg.message
        for msg in result.messages
    )


def test_repo_docs_topic_review_flags_duplicate_topic_owners(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    first_path = "docs/superpowers/validation/backfill-owner-a.md"
    second_path = "docs/superpowers/validation/backfill-owner-b.md"
    formal_owner_body = "\n".join(
        [
            "# Backfill owner",
            "",
            "Doc placement: formal_repo_doc",
            "Repo owner: docs/product/backfill.md",
            "",
            "Backfill and quant matrix source of truth.",
        ]
    )
    _write(root / first_path, formal_owner_body)
    _write(root / second_path, formal_owner_body)

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 0
    assert review["topic_cluster_group_counts"]["potential_duplicate_owner"] == 1
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if item["topic_cluster_status"] == "potential_duplicate_owner"
    )
    assert cluster["topic_cluster_status"] == "potential_duplicate_owner"
    assert cluster["topic_owner_count"] == 2
    assert cluster["file_count"] == 2
    assert first_path in cluster["sample_paths"]
    assert second_path in cluster["sample_paths"]
    assert any(
        msg.severity == "warning" and "possible duplicate topic owners" in msg.message
        for msg in result.messages
    )


def test_repo_docs_topic_review_distinguishes_distinct_subcontracts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    first_path = "docs/superpowers/validation/method-manifest-contract.md"
    second_path = "docs/superpowers/validation/artifact-replay-policy.md"
    _write(
        root / first_path,
        "\n".join(
            [
                "# Method manifest",
                "",
                "Doc placement: repo_subcontract_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Repo owner: docs/product/targeted-workbook.md",
                (
                    "Doc exit rule: retire after docs/product/targeted-workbook.md "
                    "absorbs it."
                ),
                "",
                "Manifest replay contract.",
            ]
        ),
    )
    _write(
        root / second_path,
        "\n".join(
            [
                "# Artifact replay policy",
                "",
                "Doc placement: repo_subcontract_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Repo owner: docs/product/targeted-workbook.md",
                (
                    "Doc exit rule: retire after docs/product/targeted-workbook.md "
                    "absorbs it."
                ),
                "",
                "Replay artifact manifest contract.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if item["topic_key"] == "run provenance"
    )
    assert cluster["topic_cluster_status"] == "subcontracts_with_owner"
    assert cluster["topic_owner_count"] == 0
    assert cluster["subcontract_count"] == 2
    assert first_path in cluster["subcontract_paths"]
    assert second_path in cluster["subcontract_paths"]
    assert not [
        msg
        for msg in result.messages
        if "possible duplicate topic owners" in msg.message
    ]


def test_sample_metadata_contract_gets_own_topic_before_run_provenance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    sample_metadata_path = "docs/superpowers/validation/sample-metadata-contract.md"
    _write(
        root / sample_metadata_path,
        "\n".join(
            [
                "# Sample metadata contract",
                "",
                "Doc placement: repo_subcontract_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Repo owner: docs/product/sample-metadata-qc.md",
                (
                    "Doc exit rule: retire after docs/product/sample-metadata-qc.md "
                    "absorbs it."
                ),
                "",
                "sample_metadata injection_order source.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 0
    assert review["top_candidates"] == []
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if item["topic_key"] == "sample metadata and qc"
    )
    assert cluster["repo_owner_hint"] == "docs/product/sample-metadata-qc.md"
    assert sample_metadata_path in cluster["subcontract_paths"]
    assert sample_metadata_path not in cluster["owner_paths"]
    assert not [
        item
        for item in review["top_topic_clusters"]
        if item["topic_key"] == "run provenance"
        and sample_metadata_path in item["sample_paths"]
    ]


def test_productization_routing_index_wins_over_backfill_body_mentions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    index_path = "docs/superpowers/goals/README.md"
    _write(
        root / index_path,
        "\n".join(
            [
                "# Productization Goals",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: goal",
                "Doc lifecycle: active",
                "Repo owner: docs/product/productization.md",
                ("Doc exit rule: update when productization goal routing changes."),
                "",
                "Backfill roadmap entries can be linked here, but this page is",
                "the productization goal routing index.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if item["topic_key"] == "productization"
    )
    assert index_path in cluster["supporting_sample_paths"]
    assert cluster["topic_owner_count"] == 1
    assert index_path not in cluster["owner_paths"]


@pytest.mark.parametrize(
    ("placement", "repo_owner"),
    [
        ("repo_active_stub", "docs/agent/obsidian-handoff-contract.md"),
        ("branch_closeout_summary", "docs/agent/obsidian-handoff-contract.md"),
        ("repo_stub_plus_obsidian", "docs/agent/obsidian-handoff-contract.md"),
        ("repo_stub_plus_formal_doc", "docs/product/targeted-selection.md"),
    ],
)
def test_retired_stub_placements_are_routed_as_invalid_candidates(
    tmp_path: Path,
    placement: str,
    repo_owner: str,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    candidate_path = "docs/superpowers/notes/2026-06-25-retired-stub.md"
    _write(
        root / candidate_path,
        "\n".join(
            [
                "# Retired stub",
                "",
                f"Doc placement: {placement}",
                "Doc kind: note",
                "Doc lifecycle: archived",
                f"Repo owner: {repo_owner}",
                "Doc exit rule: retire after referrer closeout.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 1
    assert review["disposition_counts"] == {"invalid_repo_placement": 1}
    candidate = review["top_candidates"][0]
    assert candidate["path"] == candidate_path
    assert candidate["doc_route"] == "obsidian_original"
    assert candidate["repo_body_role"] == "original_not_repo"
    assert candidate["topic_role"] == "needs_distillation_or_route"
    assert candidate["support_retention_reason"] == "not_support_surface"


def test_complete_support_docs_are_not_reopened_by_multiple_support_surfaces(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    first_path = "docs/superpowers/notes/2026-06-25-cleanup-inventory.md"
    second_path = "docs/superpowers/file-management/docs-cleanup/manifest.md"
    for path in (first_path, second_path):
        _write(
            root / path,
            "\n".join(
                [
                    "# Cleanup support",
                    "",
                    "Doc placement: repo_support_doc",
                    "Doc kind: manifest",
                    "Doc lifecycle: active",
                    "Repo owner: docs/agent/obsidian-handoff-contract.md",
                    "Doc exit rule: update when docs workflow routing changes.",
                    "",
                    "Docs workflow support surface.",
                ]
            ),
        )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if item["topic_key"] == "docs workflow or historical context"
    )
    assert cluster["topic_cluster_status"] == "multiple_support_surfaces"
    assert "support_surface_retained:2" in cluster["digestion_status_counts"]
    retained_review_paths = {
        item["path"] for item in review["top_route_retained_reviews"]
    }
    assert first_path not in retained_review_paths
    assert second_path not in retained_review_paths


def test_exact_referrer_bound_support_counts_as_followup_queue(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    support_path = "docs/superpowers/validation/backfill-run.md"
    referrer_path = "docs/superpowers/schemas/README.md"
    _write(
        root / support_path,
        "\n".join(
            [
                "# Backfill support",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: validation_artifact",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/backfill.md",
                "Doc exit rule: retire after exact referrers point at the owner.",
                "",
                "Backfill validation support.",
            ]
        ),
    )
    _write(
        root / referrer_path,
        "\n".join(
            [
                "# Schemas",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: manifest",
                "Doc lifecycle: active",
                "Repo owner: docs/product/productization.md",
                "Doc exit rule: update when planning surfaces change.",
                "",
                f"Historical support lives at `{support_path}`.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if support_path in item["supporting_sample_paths"]
    )
    assert cluster["digestion_review_count"] == 1
    assert "exact_referrer_bound_support:1" in cluster["support_retention_counts"]
    retained_review_paths = {
        item["path"] for item in review["top_route_retained_reviews"]
    }
    assert support_path in retained_review_paths


def test_mechanical_referrer_anchor_is_not_docs_followup_queue(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    support_path = "docs/superpowers/notes/productization-test-anchor.md"
    _write(
        root / support_path,
        "\n".join(
            [
                "# Goals",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: note",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/productization.md",
                "Doc exit rule: keep while tests assert the routing fixture path.",
                "",
                "Productization routing fixture.",
            ]
        ),
    )
    _write(
        root / "tests/test_goal_anchor.py",
        f"GOAL_INDEX = {support_path!r}\n",
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if support_path in item["supporting_sample_paths"]
    )
    assert "mechanical_referrer_anchor:1" in cluster["support_retention_counts"]
    assert support_path in cluster["bound_support_sample_paths"]
    retained_review_paths = {item["path"] for item in review["route_retained_reviews"]}
    assert support_path not in retained_review_paths


def test_active_support_with_docs_referrer_stays_out_of_followup_queue(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    support_path = "docs/superpowers/file-management/current-routing.md"
    _write(
        root / support_path,
        "\n".join(
            [
                "# Current routing",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: manifest",
                "Doc lifecycle: active",
                "Repo owner: docs/agent/obsidian-handoff-contract.md",
                "Doc exit rule: regenerate when docs routing changes.",
                "",
                "Current docs routing queue.",
            ]
        ),
    )
    _write(
        root / "docs/superpowers/README.md",
        "\n".join(
            [
                "# Superpowers",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: manifest",
                "Doc lifecycle: active",
                "Repo owner: docs/project-layout.md",
                "Doc exit rule: update when docs routing changes.",
                "",
                f"Current routing lives at `{support_path}`.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if support_path in item["supporting_sample_paths"]
    )
    assert "active_support_surface:2" in cluster["support_retention_counts"]
    retained_review_paths = {item["path"] for item in review["route_retained_reviews"]}
    assert support_path not in retained_review_paths


def test_archived_retention_anchor_stays_out_of_followup_queue(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    support_path = "docs/superpowers/file-management/docs-cleanup/archive.md"
    _write(
        root / support_path,
        "\n".join(
            [
                "# Cleanup archive",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: manifest",
                "Doc lifecycle: archived",
                "Repo owner: docs/agent/obsidian-handoff-contract.md",
                (
                    "Doc exit rule: Keep as the historical approval record "
                    "unless a later retained index supersedes it."
                ),
                "",
                "Historical cleanup approval record.",
            ]
        ),
    )
    _write(
        root / "docs/superpowers/README.md",
        "\n".join(
            [
                "# Superpowers",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: manifest",
                "Doc lifecycle: active",
                "Repo owner: docs/project-layout.md",
                "Doc exit rule: update when docs routing changes.",
                "",
                f"Archived cleanup record: `{support_path}`.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if support_path in item["supporting_sample_paths"]
    )
    assert "archived_retention_anchor:1" in cluster["support_retention_counts"]
    retained_review_paths = {item["path"] for item in review["route_retained_reviews"]}
    assert support_path not in retained_review_paths


def test_write_topic_clusters_tsv_writes_folder_consolidation_map(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    first_path = "docs/superpowers/validation/backfill-owner-a.md"
    second_path = "docs/superpowers/validation/backfill-owner-b.md"
    body = "\n".join(
        [
            "# Backfill owner",
            "",
            "Doc placement: formal_repo_doc",
            "Repo owner: docs/product/backfill.md",
            "",
            "Backfill and quant matrix source of truth.",
        ]
    )
    _write(root / first_path, body)
    _write(root / second_path, body)
    clusters_path = tmp_path / "topic-clusters.tsv"

    result = _run_audit(root, vault)
    write_topic_clusters_tsv(result, clusters_path)

    with clusters_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    row = next(
        item
        for item in rows
        if item["topic_cluster_status"] == "potential_duplicate_owner"
    )
    assert row["topic_key"] == "backfill and quant matrix"
    assert row["suggested_repo_topic_folder"] == (
        "output/docs-topic-indexes/backfill-and-quant-matrix/"
    )
    assert row["suggested_obsidian_topic_folder"].endswith(
        "/Backfill And Quant Matrix/"
    )
    assert "resolve files claiming the same repo owner" in row["topic_next_action"]
    assert int(row["digestion_review_count"]) == 2
    assert "duplicate_owner_review:2" in row["digestion_status_counts"]
    assert first_path in row["owner_paths"]
    assert second_path in row["owner_paths"]


def test_write_topic_index_readmes_creates_navigation_only_indexes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    owner_path = "docs/superpowers/validation/backfill-owner.md"
    _write(
        root / owner_path,
        "\n".join(
            [
                "# Backfill owner",
                "",
                "Doc placement: formal_repo_doc",
                "Repo owner: docs/product/backfill.md",
                "",
                "Backfill and quant matrix source of truth.",
            ]
        ),
    )
    index_dir = tmp_path / "topics"

    result = _run_audit(root, vault)
    write_topic_index_readmes(result, index_dir)

    readme = (index_dir / "backfill-and-quant-matrix" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "Doc placement: ignored_artifact" in readme
    assert "Doc kind: manifest" in readme
    assert "navigation and cleanup index" in readme
    assert "does not define product behavior" in readme
    assert "Digestion review files:" in readme
    assert "Digestion status counts:" in readme
    assert "Support retention counts:" in readme
    assert "## Support Retention" in readme
    assert "Canonical owner hint: `docs/product/backfill.md" in readme
    assert "Generated index folder: `output/docs-topic-indexes/" in readme
    assert f"- `{owner_path}`" in readme


def test_topic_review_separates_bound_and_compressible_support_surfaces(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    authority_bound_path = "docs/superpowers/notes/backfill-bound-packet.md"
    explanation_bound_path = (
        "docs/superpowers/notes/backfill-explanation-bound-packet.md"
    )
    exact_ref_path = "docs/superpowers/notes/backfill-oracle-note.md"
    compressible_path = "docs/superpowers/notes/backfill-archived-note.md"
    support_body = [
        "Doc placement: repo_support_doc",
        "Doc kind: validation_artifact",
        "Doc lifecycle: archived",
        "Repo owner: docs/product/backfill.md",
        "Doc exit rule: keep while referenced by validation policy.",
        "",
        "Backfill support artifact.",
    ]
    for path in (
        authority_bound_path,
        explanation_bound_path,
        exact_ref_path,
        compressible_path,
    ):
        _write(root / path, "# Backfill support\n\n" + "\n".join(support_body))
    _write(
        root / "docs/superpowers/validation/productization_status_index.tsv",
        (f"lane\tcurrent_artifact\nbroad_backfill\t{authority_bound_path}\n"),
    )
    _write(
        root / "docs/superpowers/productization/status/"
        "productization_authority_manifest.json",
        json.dumps(
            {
                "decision_packet": authority_bound_path,
                "explanation_only_sources": [{"artifact": explanation_bound_path}],
            }
        ),
    )
    for index in range(6):
        _write(
            root / f"docs/product/ref-{index}.md",
            f"# Ref {index}\n\nExtra reference: `{authority_bound_path}`.\n",
        )
    _write(
        root / "docs/product/backfill.md",
        f"# Backfill\n\nOracle reference: `{exact_ref_path}`.\n",
    )
    index_dir = tmp_path / "topics"

    result = _run_audit(root, vault)
    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if item["topic_key"] == "backfill and quant matrix"
    )

    assert "authority_or_status_anchor:2" in cluster["support_retention_counts"]
    assert "exact_referrer_bound_support:1" in cluster["support_retention_counts"]
    assert "archived_compressible_support:1" in cluster["support_retention_counts"]
    assert authority_bound_path in cluster["bound_support_sample_paths"]
    assert explanation_bound_path in cluster["bound_support_sample_paths"]
    assert exact_ref_path in cluster["bound_support_sample_paths"]
    assert compressible_path in cluster["compressible_support_sample_paths"]

    write_topic_index_readmes(result, index_dir)
    readme = (index_dir / "backfill-and-quant-matrix" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "### Bound Support Samples" in readme
    assert f"- `{authority_bound_path}`" in readme
    assert f"- `{compressible_path}`" in readme


def test_repo_docs_topic_review_separates_support_surfaces_from_owners(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    validation_path = "docs/superpowers/validation/backfill-run.md"
    manifest_path = "docs/superpowers/validation/backfill-manifest.md"
    _write(root / validation_path, "# Backfill validation\n\nBackfill check.\n")
    _write(root / manifest_path, "# Backfill manifest\n\nBackfill queue.\n")

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    assert review["candidate_files"] == 2
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if validation_path in item["sample_paths"]
    )
    assert cluster["topic_cluster_status"] == "external_owner_with_cleanup_candidates"
    assert cluster["topic_owner_count"] == 0
    assert cluster["supporting_count"] == 2
    assert cluster["digestion_review_count"] == 2
    assert (
        "external_owner_with_cleanup_candidates:2" in cluster["digestion_status_counts"]
    )
    candidate_paths = {
        item["path"]
        for item in review["top_candidates"]
        if item["digestion_status"] == "external_owner_with_cleanup_candidates"
    }
    assert candidate_paths == {validation_path, manifest_path}
    assert not [
        msg
        for msg in result.messages
        if "possible duplicate topic owners" in msg.message
    ]


def test_repo_support_doc_is_not_counted_as_topic_owner(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    support_path = "docs/superpowers/notes/backfill-support-note.md"
    _write(
        root / support_path,
        "\n".join(
            [
                "# Backfill support note",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: validation_artifact",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/backfill.md",
                "",
                "Backfill validation support note.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if support_path in item["supporting_sample_paths"]
    )
    assert cluster["topic_key"] == "backfill and quant matrix"
    assert cluster["topic_cluster_status"] == "single_surface"
    assert cluster["topic_owner_count"] == 0
    assert cluster["supporting_count"] == 1
    assert support_path not in cluster["owner_paths"]


def test_file_management_docs_route_to_docs_workflow_even_with_topic_keywords(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    manifest_path = "docs/superpowers/file-management/backfill-manifest.md"
    _write(root / manifest_path, "# Backfill manifest\n\nBackfill queue.\n")

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if manifest_path in item["sample_paths"]
    )
    assert cluster["topic_key"] == "docs workflow or historical context"
    assert cluster["repo_owner_hint"] == (
        "docs/project-layout.md; docs/agent/obsidian-handoff-contract.md"
    )


def test_repo_subcontract_doc_is_not_counted_as_topic_owner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    subcontract_path = "docs/superpowers/validation/backfill-subcontract.md"
    _write(
        root / subcontract_path,
        "\n".join(
            [
                "# Backfill subcontract",
                "",
                "Doc placement: repo_subcontract_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Repo owner: docs/product/backfill.md",
                "Doc exit rule: retire after docs/product/backfill.md absorbs it.",
                "",
                "Bounded Backfill contract.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if subcontract_path in item["subcontract_paths"]
    )
    assert cluster["topic_key"] == "backfill and quant matrix"
    assert cluster["topic_cluster_status"] == "subcontracts_with_owner"
    assert cluster["topic_owner_count"] == 0
    assert cluster["subcontract_count"] == 1
    assert cluster["digestion_review_count"] == 0
    assert subcontract_path not in cluster["owner_paths"]
    assert subcontract_path in cluster["subcontract_paths"]
    assert cluster["subcontract_claims"] == "docs/product/backfill.md"


def test_unmarked_superpowers_specs_path_is_retired_lane(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    spec_path = "docs/superpowers/specs/backfill-contract.md"
    _write(
        root / spec_path,
        "\n".join(
            [
                "# Backfill contract",
                "",
                "Backfill matrix product contract.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    structure = result.summary["repo"]["docs_structure_review"]
    assert structure["retired_lane_files"] == 1
    assert structure["retired_lanes"] == [
        {"path": spec_path, "retired_dir": "docs/superpowers/specs/"}
    ]
    assert any(item for item in review["top_candidates"] if item["path"] == spec_path)


def test_docs_audit_treats_spec_assets_like_markdown(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    payload_path = "docs/superpowers/specs/example-schema.json"
    _write(root / payload_path, "{}\n")

    result = _run_audit(root, vault)

    (root / payload_path).unlink()
    _write(root / "docs/superpowers/specs/example-schema.md", "# Schema\n")
    markdown_result = _run_audit(root, vault)
    assert result.blockers == markdown_result.blockers


def test_docs_audit_uses_staged_payload_missing_from_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    git_env = _isolated_git_subprocess_env()
    subprocess.run(
        ["git", "init"], cwd=root, check=True, capture_output=True, env=git_env
    )
    subprocess.run(
        ["git", "add", "."], cwd=root, check=True, capture_output=True, env=git_env
    )
    payload_path = "docs/superpowers/specs/rogue.json"
    _write(root / payload_path, "{}\n")
    subprocess.run(
        ["git", "add", payload_path],
        cwd=root,
        check=True,
        capture_output=True,
        env=git_env,
    )
    (root / payload_path).unlink()

    result = _run_audit(root, repo_only=True, preserve_index=True)

    structure = result.summary["repo"]["docs_structure_review"]
    assert {"path": payload_path, "retired_dir": "docs/superpowers/specs/"} in (
        structure["retired_lanes"]
    )


def test_docs_audit_ignores_staged_deletion_with_worktree_copy(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    git_env = _isolated_git_subprocess_env()
    subprocess.run(
        ["git", "init"], cwd=root, check=True, capture_output=True, env=git_env
    )
    subprocess.run(
        ["git", "add", "."], cwd=root, check=True, capture_output=True, env=git_env
    )
    retired_path = "docs/superpowers/specs/staged-deletion.md"
    _write(root / retired_path, "# Staged deletion\n")
    subprocess.run(
        ["git", "add", retired_path],
        cwd=root,
        check=True,
        capture_output=True,
        env=git_env,
    )
    subprocess.run(
        ["git", "rm", "--cached", "--force", retired_path],
        cwd=root,
        check=True,
        capture_output=True,
        env=git_env,
    )

    result = _run_audit(root, repo_only=True, preserve_index=True)

    structure = result.summary["repo"]["docs_structure_review"]
    assert structure["retired_lane_files"] == 0
    assert retired_path not in structure["retired_lanes"]


def test_docs_audit_routes_from_staged_text_not_later_worktree_text(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    git_env = _init_and_stage_repo(root)
    path = "docs/superpowers/notes/index-routing.md"
    staged_text = "\n".join(
        (
            "# Index routing",
            "",
            "Doc placement: private_obsidian_note",
            "Doc kind: note",
            "Doc lifecycle: active",
            "Repo owner: docs/notes/index-routing.md",
        )
    )
    _write(root / path, staged_text + "\n")
    subprocess.run(
        ["git", "add", path], cwd=root, check=True, capture_output=True, env=git_env
    )
    _write(
        root / path,
        staged_text.replace("private_obsidian_note", "formal_repo_doc") + "\n",
    )

    result = _run_audit(root, repo_only=True, preserve_index=True)

    candidates = result.summary["repo"]["docs_routing_review"]["candidates"]
    assert any(
        row["path"] == path and row["disposition"] == "invalid_repo_placement"
        for row in candidates
    )


def test_docs_audit_completed_lifecycle_uses_staged_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    git_env = _init_and_stage_repo(root)
    path = "docs/superpowers/plans/index-completed.md"
    staged_text = "\n".join(
        (
            "# Index completed",
            "",
            "Doc placement: repo_support_doc",
            "Doc kind: plan",
            "Doc lifecycle: implemented",
            "Repo owner: docs/product/backfill.md",
            "Doc exit rule: retire after owner absorption.",
        )
    )
    _write(root / path, staged_text + "\n")
    subprocess.run(
        ["git", "add", path], cwd=root, check=True, capture_output=True, env=git_env
    )
    _write(root / path, staged_text.replace("implemented", "active") + "\n")

    result = _run_audit(
        root,
        repo_only=True,
        preserve_index=True,
    )

    completed = result.summary["repo"]["completed_transient_review"]
    assert path in {row["path"] for row in completed["top_completed_files"]}


def test_docs_audit_permanent_root_uses_staged_text(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _clean_repo(root)
    git_env = _init_and_stage_repo(root)
    path = "README.md"
    valid_worktree_text = (root / path).read_text(encoding="utf-8")
    _write(root / path, "# Broken staged README\n")
    subprocess.run(
        ["git", "add", path], cwd=root, check=True, capture_output=True, env=git_env
    )
    _write(root / path, valid_worktree_text)

    result = _run_audit(root, repo_only=True, preserve_index=True)

    assert any(
        message.severity == "blocker" and message.path == path
        for message in result.messages
    )


def test_repo_owner_marker_controls_topic_before_body_keywords(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    subcontract_path = "docs/superpowers/validation/targeted-selection-subcontract.md"
    _write(
        root / subcontract_path,
        "\n".join(
            [
                "# Targeted selection subcontract",
                "",
                "Doc placement: repo_subcontract_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Repo owner: docs/product/targeted-selection.md",
                (
                    "Doc exit rule: retire after docs/product/targeted-selection.md "
                    "absorbs it."
                ),
                "",
                (
                    "This text mentions Backfill, ProductWriter, and matrix "
                    "history only as background."
                ),
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if subcontract_path in item["subcontract_paths"]
    )
    assert cluster["topic_key"] == "targeted selection"
    assert cluster["repo_owner_hint"] == ("docs/product/targeted-selection.md")
    assert cluster["topic_cluster_status"] == "subcontracts_with_owner"
    assert cluster["subcontract_claims"] == "docs/product/targeted-selection.md"


def test_architecture_contract_owner_routes_cleanup_specs_out_of_backfill(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    spec_path = "docs/superpowers/validation/peak-pipeline-cleanup.md"
    _write(
        root / spec_path,
        "\n".join(
            [
                "# Peak pipeline cleanup",
                "",
                "Doc placement: repo_subcontract_doc",
                "Doc kind: spec",
                "Doc lifecycle: archived",
                "Repo owner: docs/architecture-contract.md",
                "Doc exit rule: keep as historical architecture cleanup context.",
                "",
                "This cleanup spec mentions Backfill only as a historical phase.",
            ]
        ),
    )

    result = _run_audit(root, vault)

    review = result.summary["repo"]["docs_routing_review"]
    cluster = next(
        item
        for item in review["top_topic_clusters"]
        if spec_path in item["subcontract_paths"]
    )
    assert cluster["topic_key"] == "architecture and cleanup"
    assert cluster["repo_owner_hint"] == "docs/architecture-contract.md"
    assert cluster["digestion_review_count"] == 0


def test_ignored_local_handoff_is_not_a_docs_management_problem(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    root.mkdir()
    git_env = _isolated_git_subprocess_env()
    subprocess.run(
        ["git", "init"],
        cwd=root,
        check=True,
        capture_output=True,
        env=git_env,
    )
    _clean_repo(root)
    _clean_vault(vault)
    _write(
        root / ".gitignore",
        "docs/superpowers/handoffs/current/*\n",
    )
    _write(
        root / "docs/superpowers/handoffs/current/ACTIVE.local.md",
        "# Local handoff\n\nStatus: active.\n",
    )
    subprocess.run(
        ["git", "add", "."],
        cwd=root,
        check=True,
        capture_output=True,
        env=git_env,
    )

    result = _run_audit(root, vault)

    assert result.blockers == ()
    assert not [msg for msg in result.messages if msg.severity == "warning"]


def test_permanent_root_allows_reworded_headings_and_purpose(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)

    rewrites = {
        "AGENTS.md": (
            ("XIC Extractor Agent Contract", "Repository Guidance"),
            ("Task Routes", "Where Responsibilities Live"),
        ),
        "README.md": (
            ("XIC Extractor", "XIC Project"),
            ("Documentation", "Reading Guide"),
        ),
        "docs/architecture-contract.md": (
            ("Architecture Contract", "System Architecture"),
            ("Dependency Direction", "Allowed Dependencies"),
            ("Ownership Map", "Module Owners"),
        ),
        "docs/project-layout.md": (
            ("Project Layout", "Repository Map"),
            ("Permanent root document contract", "Root Documents"),
            ("目錄收納規則單一可信來源", "檔案放置規則由此文件維護"),
            ("source-of-truth 與歷程邊界", "正式文件與歷史記錄的界線"),
        ),
    }
    for path, substitutions in rewrites.items():
        text = (root / path).read_text(encoding="utf-8")
        for old, new in substitutions:
            text = text.replace(old, new)
        _write(root / path, text + "\n正式文件的用途可在說明文字中重複提及。\n" * 2)

    result = _run_audit(root, vault)

    assert result.blockers == ()


@pytest.mark.parametrize(
    ("path", "replacement", "expected_problem"),
    [
        (
            "AGENTS.md",
            "# Agent Guide\n\n[README](README.md)\n",
            "required route must resolve: docs/agent/parameter-settings.md",
        ),
        (
            "README.md",
            " \n\t\n",
            "permanent root document has no visible text",
        ),
        (
            "README.md",
            "<!-- hidden -->\n```md\n# Hidden\n```\n",
            "permanent root document has no visible text",
        ),
    ],
)
def test_permanent_root_rejects_missing_routes_and_empty_content(
    tmp_path: Path,
    path: str,
    replacement: str,
    expected_problem: str,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    _write(root / path, replacement)

    result = _run_audit(root, vault)

    review = result.summary["repo"]["canonical_metadata_review"]
    rows = review["top_missing_metadata"]
    row = next(item for item in rows if item["path"] == path)
    assert row["metadata_status"] == "invalid_permanent_root_contract"
    assert expected_problem in row["metadata_missing_fields"]
    assert any(
        msg.severity == "blocker" and msg.path == path for msg in result.messages
    )


def test_permanent_root_rejects_missing_target_and_referrer(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    (root / "docs/agent/parameter-settings.md").unlink()
    readme = root / "README.md"
    _write(
        readme,
        readme.read_text(encoding="utf-8").replace(
            "[Architecture](docs/architecture-contract.md)", "Architecture overview"
        ),
    )

    result = _run_audit(root, vault)

    rows = result.summary["repo"]["canonical_metadata_review"]["top_missing_metadata"]
    problems_by_path = {row["path"]: row["metadata_missing_fields"] for row in rows}
    assert (
        "required route target is missing: docs/agent/parameter-settings.md"
        in (problems_by_path["AGENTS.md"])
    )
    expected_referrer = (
        "required referrer must route: README.md -> docs/architecture-contract.md"
    )
    assert expected_referrer in (problems_by_path["docs/architecture-contract.md"])
    assert {"AGENTS.md", "README.md", "docs/architecture-contract.md"} <= {
        message.path for message in result.blockers
    }


def test_canonical_metadata_review_blocks_missing_permanent_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    (root / "README.md").unlink()

    result = _run_audit(root, vault)

    assert any(
        msg.severity == "blocker"
        and msg.path == "README.md"
        and "permanent root path is missing" in msg.message
        for msg in result.messages
    )


def test_env_example_local_machine_path_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    _write(root / "config" / "env.example", "XIC_RAW_ROOT=C:\\Xcalibur\\data\n")

    result = _run_audit(root, vault)

    assert result.blockers == ()
    top_hits = result.summary["repo"]["top_local_path_files"]
    assert any(item["path"] == "config/env.example" for item in top_hits)


def test_xic_local_env_configures_vault_when_env_var_is_unset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
    _clean_repo(root)
    _clean_vault(vault)
    _write(root / ".env.xic-local", f"OBSIDIAN_VAULT_PATH={vault}\n")

    result = _run_audit(root)

    assert result.blockers == ()
    assert result.summary["vault"]["vault_configured"] is True
    assert result.summary["vault"]["vault_path"] == str(vault)


def test_wikilink_heading_anchor_is_not_reported_broken(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    _write(
        vault / "Target.md",
        "\n".join(
            [
                "---",
                "tags: [visibility/internal]",
                "lifecycle: draft",
                "tier: supporting",
                "---",
                "# Heading",
            ]
        ),
    )
    _write(
        vault / "index.md",
        "\n".join(
            [
                "---",
                "tags: [visibility/internal]",
                "lifecycle: draft",
                "tier: supporting",
                "---",
                "[[Target#Heading]]",
            ]
        ),
    )
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    manifest["stats"]["total_pages"] = 2
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_audit(root, vault)

    assert result.summary["vault"]["link_health"]["broken_wikilinks"] == 0


def test_private_source_copy_original_content_wikilinks_are_not_live_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)
    _write(
        vault / "Source Copy.md",
        "\n".join(
            [
                "---",
                "tags: [visibility/internal]",
                "lifecycle: draft",
                "tier: supporting",
                "disposition: private_history_source_copy",
                "---",
                "# Source Copy",
                "",
                "[[Live Missing Link]]",
                "",
                "## Original Content",
                "",
                "Historical body mentions [[Historical Missing Link]].",
            ]
        ),
    )
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    manifest["stats"]["total_pages"] = 2
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_audit(root, vault)

    sample = result.summary["vault"]["link_health"]["broken_wikilink_sample"]
    assert result.summary["vault"]["link_health"]["broken_wikilinks"] == 1
    assert sample == [{"source": "Source Copy.md", "target": "Live Missing Link"}]


def test_multiline_frontmatter_tags_count_as_visibility(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _write(
        vault / "index.md",
        "\n".join(
            [
                "---",
                "tags:",
                "  - visibility/internal",
                "lifecycle: draft",
                "tier: supporting",
                "---",
                "# Index",
            ]
        ),
    )
    manifest = {
        "version": 1,
        "sources": {},
        "projects": {},
        "stats": {
            "total_sources_ingested": 0,
            "total_pages": 1,
            "total_projects": 0,
        },
    }
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_audit(root, vault)

    assert result.summary["vault"]["frontmatter"]["missing_visibility"] == 0


def test_clean_repo_and_vault_have_no_blockers(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    vault = tmp_path / "vault"
    _clean_repo(root)
    _clean_vault(vault)

    result = _run_audit(root, vault)

    assert result.blockers == ()
    assert result.summary["repo"]["docs_routing_review"]["scanned_files"] == 1
    assert result.summary["repo"]["docs_routing_review"]["candidate_files"] == 0
