from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.diagnostics.docs_placement_guard import (
    StagedMarkdown,
    check_doc_placement,
    committed_same_path_retirement_packet_problems,
    parse_name_status,
    staged_markdown,
)


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _source_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _retirement_evidence(
    path: str,
    text: str,
    *,
    exact_referrers: list[str] | None = None,
) -> dict[str, object]:
    return {
        "version": 1,
        "entries": [
            {
                "source_path": path,
                "review_result": "pass_can_retire",
                "owner_paths": ["docs/product/backfill.md"],
                "owner_anchors": ["docs/product/backfill.md#current-direction"],
                "absorbed_claims": ["claim is present in owner"],
                "absorbed_negative_claims": ["negative_payload=none"],
                "source_copy_readback_verified": True,
                "source_hash": _source_hash(text),
                "exact_referrers": exact_referrers or [],
                "active_followups": [],
            }
        ],
    }


def _same_path_fixture(
    root: Path,
) -> tuple[str, str, str, dict[str, object]]:
    source_path = "/".join(
        (
            "docs",
            "superpowers",
            "plans",
            "2026-06-15-productization-control-plane.md",
        )
    )
    evidence_path = (
        "docs/superpowers/file-management/docs-cleanup/"
        "2026-08-01-retirement-evidence.json"
    )
    source = "# Old control plane\n\n## Durable Claim\nStable capability rule.\n"
    replacement = "# Productization compatibility index\n\nStable pointers only.\n"
    owner_path = "docs/product/backfill.md"
    _write(root, source_path, source)
    _write(root, owner_path, "# Backfill\n\n## Contract\nStable capability rule.\n")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "add", source_path, owner_path],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    source_hash = _source_hash(source)
    lines = source.encode("utf-8").splitlines(keepends=True)
    claim_hash = _source_hash(b"".join(lines[2:4]).decode("utf-8"))
    section_hash = source_hash
    replacement_hash = _source_hash(replacement)
    claim = {
        "claim_id": "CP-CLAIM-001",
        "polarity": "positive",
        "source_locator": f"{source_path}:L3-L4",
        "source_excerpt_sha256": claim_hash,
        "source_section_ids": ["CP-SECTION-001"],
        "summary": "Backfill contract owns the stable capability rule.",
        "disposition": "absorb",
        "owner_locator": {
            "path": owner_path,
            "kind": "markdown_heading",
            "heading": "Contract",
        },
    }
    source_mapping = {
        "source_path": source_path,
        "transaction": "body_to_compatibility_index",
        "review_result": "pass_can_retire",
        "source_copy_readback_verified": True,
        "source_copy_hash": source_hash,
        "source_hash": source_hash,
        "replacement_hash": replacement_hash,
        "active_followups": [],
        "unresolved_claims": [],
        "claim_ids": ["CP-CLAIM-001"],
        "absorbed_claims": ["CP-CLAIM-001"],
        "absorbed_negative_claims": [],
    }
    packet: dict[str, object] = {
        "transaction": "body_to_compatibility_index",
        "transaction_status": "completed",
        "review_result": "pass_can_retire",
        "source_path": source_path,
        "source_hash": source_hash,
        "source_copy_readback_verified": True,
        "source_copy_hash": source_hash,
        "replacement_hash": replacement_hash,
        "active_followups": [],
        "unresolved_claims": [],
        "absorbed_claims": ["CP-CLAIM-001"],
        "absorbed_negative_claims": [],
        "source_line_count": len(lines),
        "source_section_count": 1,
        "source_sections": [
            {
                "section_id": "CP-SECTION-001",
                "source_locator": f"{source_path}:L1-L4",
                "section_sha256": section_hash,
                "disposition": "absorb",
                "claim_ids": ["CP-CLAIM-001"],
            }
        ],
        "claims": [claim],
        "dependencies": {
            "literal_path_referrers": [],
            "relative_markdown_links": [],
            "relative_html_links": [],
            "control_plane_constant_consumers": [],
        },
        "entries": [source_mapping],
    }
    return source_path, evidence_path, replacement, packet


def _same_path_result(
    root: Path,
    source_path: str,
    evidence_path: str,
    replacement: str,
    packet: dict[str, object],
    *extra_entries: StagedMarkdown,
):
    return check_doc_placement(
        root,
        [
            StagedMarkdown("M", source_path, replacement),
            StagedMarkdown("A", evidence_path, json.dumps(packet)),
            *extra_entries,
        ],
    )


def _commit_same_path_fixture(
    root: Path,
    *,
    hard_zero_files: list[str] | None = None,
) -> tuple[str, str]:
    source_path, evidence_path, replacement, packet = _same_path_fixture(root)
    activation_base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    packet.update(
        {
            "activation_base_sha": activation_base,
            "hard_zero_files": hard_zero_files or [],
            "conditional_write_set": [source_path, evidence_path],
            "allowed_protected_writes": [],
            "read_only_protected_surfaces": {
                "exact_paths": [],
                "prefixes": [],
                "rule": "No protected writes are needed in this fixture.",
            },
        }
    )
    _write(root, source_path, replacement)
    _write(root, evidence_path, json.dumps(packet, indent=2) + "\n")
    subprocess.run(
        ["git", "add", source_path, evidence_path],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "retire control plane",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return source_path, evidence_path


def _problems(root: Path, status: str, path: str) -> list[str]:
    result = check_doc_placement(root, [StagedMarkdown(status, path)])
    return [problem.reason for problem in result.problems]


def test_permanent_root_contract_rejects_missing_landmark_and_non_repo_placement() -> (
    None
):
    root = Path(__file__).parents[1]
    path = "AGENTS.md"
    text = (root / path).read_text(encoding="utf-8")
    text = text.replace("## Task Routes", "## Routes", 1)
    text += "\nDoc placement: private_obsidian_note\n"

    result = check_doc_placement(
        root,
        [StagedMarkdown("M", path, text)],
    )

    reasons = [problem.reason for problem in result.problems]
    assert any("invalid permanent root contract" in reason for reason in reasons)
    assert any("not a repo-trackable placement" in reason for reason in reasons)


def test_governance_index_rejects_mechanism_drift() -> None:
    root = Path(__file__).parents[1]
    path = "docs/agent/codex-operating-system.md"
    text = (root / path).read_text(encoding="utf-8")
    owner = "docs/agent/execution-gates.md#raw-and-long-runs"
    text = text.replace(
        f"| `raw_background_launch` | `{owner}` | `hard_guard` |",
        f"| `raw_background_launch` | `{owner}` | `human_decision` |",
        1,
    )

    result = check_doc_placement(
        root,
        [StagedMarkdown("M", path, text)],
    )

    assert any(
        "invalid governance enforcement index" in problem.reason
        for problem in result.problems
    )


def test_same_path_compatibility_replacement_with_complete_evidence_passes(
    tmp_path: Path,
) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)

    result = _same_path_result(
        tmp_path,
        source_path,
        evidence_path,
        replacement,
        packet,
    )

    assert result.problems == ()


def test_committed_same_path_retirement_packet_passes_clean_checkout_review(
    tmp_path: Path,
) -> None:
    _, evidence_path = _commit_same_path_fixture(tmp_path)

    problems = committed_same_path_retirement_packet_problems(
        tmp_path,
        evidence_path,
    )

    assert problems == ()


def test_committed_same_path_retirement_packet_rejects_changed_hard_zero_path(
    tmp_path: Path,
) -> None:
    source_path, evidence_path = _commit_same_path_fixture(
        tmp_path,
        hard_zero_files=[
            "docs/superpowers/plans/2026-06-15-productization-control-plane.md"
        ],
    )

    problems = committed_same_path_retirement_packet_problems(
        tmp_path,
        evidence_path,
    )

    assert any(
        problem == f"retirement packet hard-zero paths changed: {source_path}"
        for problem in problems
    )


def test_same_path_replacement_rejects_incomplete_source_section_coverage(
    tmp_path: Path,
) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    section = packet["source_sections"][0]
    section["source_locator"] = f"{source_path}:L3-L4"
    section["section_sha256"] = packet["claims"][0]["source_excerpt_sha256"]

    result = _same_path_result(
        tmp_path,
        source_path,
        evidence_path,
        replacement,
        packet,
    )

    assert any(
        "exactly cover every HEAD preimage line" in p.reason
        for p in result.problems
    )


def test_existing_compatibility_index_update_does_not_require_retirement_packet(
    tmp_path: Path,
) -> None:
    source_path, _, _, _ = _same_path_fixture(tmp_path)
    repo_root = Path(__file__).parents[1]
    compatibility = (repo_root / source_path).read_text(encoding="utf-8")
    _write(tmp_path, source_path, compatibility)
    subprocess.run(
        ["git", "add", source_path],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "retired compatibility index",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    updated = compatibility.replace(
        "owner route changes.",
        "owner route or stable lookup key changes.",
        1,
    )

    result = check_doc_placement(
        tmp_path,
        [StagedMarkdown("M", source_path, updated)],
    )

    assert result.problems == ()


def test_same_path_replacement_requires_evidence(tmp_path: Path) -> None:
    source_path, _, replacement, _ = _same_path_fixture(tmp_path)

    result = check_doc_placement(
        tmp_path,
        [StagedMarkdown("M", source_path, replacement)],
    )

    assert any("exactly one evidence packet" in p.reason for p in result.problems)


def test_same_path_replacement_rejects_stale_source_hash(tmp_path: Path) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    packet["source_hash"] = "0" * 64

    result = _same_path_result(
        tmp_path, source_path, evidence_path, replacement, packet
    )

    assert any("source_hash" in p.reason for p in result.problems)


def test_same_path_replacement_rejects_missing_owner_anchor(tmp_path: Path) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    claim = packet["claims"][0]
    claim["owner_locator"]["heading"] = "Missing heading"

    result = _same_path_result(
        tmp_path, source_path, evidence_path, replacement, packet
    )

    assert any("owner Markdown heading" in p.reason for p in result.problems)


def test_same_path_replacement_rejects_owner_locator_outside_repo(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path, evidence_path, replacement, packet = _same_path_fixture(repo)
    outside_owner = tmp_path / "outside-owner.md"
    outside_owner.write_text("# Owner\n\n## Contract\n", encoding="utf-8")
    packet["claims"][0]["owner_locator"]["path"] = "../outside-owner.md"

    result = _same_path_result(
        repo,
        source_path,
        evidence_path,
        replacement,
        packet,
    )

    assert any(
        "owner locator path must be repository-relative" in p.reason
        for p in result.problems
    )


def test_same_path_replacement_rejects_owner_locator_symlink_escape(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path, evidence_path, replacement, packet = _same_path_fixture(repo)
    outside_owner = tmp_path / "outside-owner.md"
    outside_owner.write_text("# Owner\n\n## Contract\n", encoding="utf-8")
    try:
        (repo / "owner-link.md").symlink_to(outside_owner)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows runner lacks symbolic-link privilege")
        raise
    packet["claims"][0]["owner_locator"]["path"] = "owner-link.md"

    result = _same_path_result(
        repo,
        source_path,
        evidence_path,
        replacement,
        packet,
    )

    assert any(
        "owner locator path must resolve inside repository" in p.reason
        for p in result.problems
    )


def test_same_path_replacement_rejects_placeholder_claim(tmp_path: Path) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    packet["claims"][0]["summary"] = "placeholder"

    result = _same_path_result(
        tmp_path, source_path, evidence_path, replacement, packet
    )

    assert any("placeholder semantics" in p.reason for p in result.problems)


def test_same_path_replacement_rejects_duplicate_claim_mapping(tmp_path: Path) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    packet["claims"].append(dict(packet["claims"][0]))

    result = _same_path_result(
        tmp_path, source_path, evidence_path, replacement, packet
    )

    assert any(
        "claim IDs are missing or duplicated" in p.reason for p in result.problems
    )


def test_same_path_replacement_rejects_nonreciprocal_section_claim_edge(
    tmp_path: Path,
) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    section = dict(packet["source_sections"][0])
    section["section_id"] = "CP-SECTION-002"
    packet["source_sections"].append(section)
    packet["source_section_count"] = 2

    result = _same_path_result(
        tmp_path, source_path, evidence_path, replacement, packet
    )

    assert any("not exactly reciprocal" in p.reason for p in result.problems)


def test_same_path_replacement_rejects_incomplete_absorbed_negative_projection(
    tmp_path: Path,
) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    claim = packet["claims"][0]
    claim["polarity"] = "negative"
    packet["absorbed_claims"] = []
    packet["absorbed_negative_claims"] = [claim["claim_id"]]
    packet["entries"][0]["absorbed_claims"] = []
    packet["entries"][0]["absorbed_negative_claims"] = []

    result = _same_path_result(
        tmp_path, source_path, evidence_path, replacement, packet
    )

    assert any(
        "source mapping absorbed_negative_claims do not exactly match" in p.reason
        for p in result.problems
    )


def test_same_path_replacement_rejects_unresolved_claim(tmp_path: Path) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    packet["unresolved_claims"] = ["CP-UNRESOLVED"]

    result = _same_path_result(
        tmp_path, source_path, evidence_path, replacement, packet
    )

    assert any("unresolved claims remain" in p.reason for p in result.problems)


def test_same_path_replacement_rejects_failed_source_copy_readback(
    tmp_path: Path,
) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    packet["source_copy_readback_verified"] = False

    result = _same_path_result(
        tmp_path, source_path, evidence_path, replacement, packet
    )

    assert any("source-copy readback" in p.reason for p in result.problems)


def test_same_path_replacement_rejects_residual_dependency(tmp_path: Path) -> None:
    source_path, evidence_path, replacement, packet = _same_path_fixture(tmp_path)
    referrer = StagedMarkdown(
        "A",
        "docs/product/referrer.md",
        f"# Referrer\n\nSee `{source_path}`.\n",
    )

    result = _same_path_result(
        tmp_path,
        source_path,
        evidence_path,
        replacement,
        packet,
        referrer,
    )

    assert any("dependency/referrer census" in p.reason for p in result.problems)


def test_high_risk_dated_implementation_plan_without_marker_fails(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/plans/2026-07-01-example-implementation-plan.md"
    _write(
        tmp_path,
        path,
        "# Example plan\n\nImplementation diary and command log details.\n",
    )

    problems = _problems(tmp_path, "A", path)

    assert any("lacks placement marker" in problem for problem in problems)


def test_repo_active_stub_with_required_fields_passes(tmp_path: Path) -> None:
    path = "docs/superpowers/plans/2026-07-01-active-work-stub.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Active work stub",
                "",
                "Doc placement: repo_active_stub",
                "Doc kind: plan",
                "Doc lifecycle: active",
                "Repo owner: docs/superpowers/handoffs/current/example.md",
                "Doc exit rule: replace with branch closeout after implementation.",
                "",
                "Objective: keep the next action recoverable.",
                "Scope: docs workflow only.",
                "Constraints: no destructive cleanup.",
                "Next 1-3 actions:",
                "1. Run the checker.",
                "Verification: focused pytest.",
                "Stop rule: stop if the repo stub is not self-sufficient.",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_new_lifecycle_managed_doc_requires_kind_lifecycle_and_exit_rule(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/plans/2026-07-01-example-contract.md"
    _write(
        tmp_path,
        path,
        "# Example Contract\n\nPublic behavior contract draft.\n",
    )

    problems = _problems(tmp_path, "A", path)

    assert any("missing Doc kind" in problem for problem in problems)
    assert any("missing Doc lifecycle" in problem for problem in problems)


def test_new_lifecycle_managed_plan_with_metadata_passes(tmp_path: Path) -> None:
    path = "docs/superpowers/plans/2026-07-01-example-contract.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Example Contract",
                "",
                "Doc placement: formal_repo_doc",
                "Doc kind: plan",
                "Doc lifecycle: active",
                "Repo owner: docs/product/example.md",
                "Doc exit rule: promote accepted behavior to docs/product/example.md.",
                "",
                "Public behavior contract draft.",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_product_formal_source_of_truth_passes_without_marker(tmp_path: Path) -> None:
    path = "docs/product/backfill.md"
    _write(tmp_path, path, "# Backfill\n\nFormal public source of truth.\n")

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_user_guide_passes_without_marker(tmp_path: Path) -> None:
    path = "docs/user/targeted-extraction.md"
    _write(tmp_path, path, "# Targeted Extraction\n\nPublic user guide.\n")

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_user_guide_with_private_history_signal_fails(tmp_path: Path) -> None:
    path = "docs/user/branch-notes.md"
    _write(
        tmp_path,
        path,
        "# Branch notes\n\nImplementation diary and command transcript details.\n",
    )

    problems = _problems(tmp_path, "A", path)

    assert any("private-history signals" in problem for problem in problems)


def test_user_guide_with_non_user_placement_fails(tmp_path: Path) -> None:
    for placement in (
        "repo_active_stub",
        "branch_closeout_summary",
        "repo_stub_plus_obsidian",
        "repo_stub_plus_formal_doc",
        "ignored_artifact",
    ):
        path = f"docs/user/{placement}.md"
        _write(
            tmp_path,
            path,
            "\n".join(
                [
                    "# Misplaced user doc",
                    "",
                    f"Doc placement: {placement}",
                    "",
                    "Objective: preserve branch state.",
                ]
            ),
        )

        problems = _problems(tmp_path, "A", path)

        assert any("not valid in docs/user" in problem for problem in problems)


def test_canonical_doc_can_document_marker_schema_without_false_positive(
    tmp_path: Path,
) -> None:
    path = "docs/agent/obsidian-handoff-contract.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Contract",
                "",
                "```markdown",
                "Doc placement: <formal_repo_doc | repo_active_stub>",
                "Repo owner: <path-or-topic>",
                "```",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("M", path)])

    assert result.problems == ()


def test_transient_superpowers_specs_markdown_with_metadata_passes(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/specs/2026-07-01-example-contract.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Example contract",
                "",
                "Doc placement: formal_repo_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Repo owner: docs/product/example.md",
                "Doc exit rule: promote accepted behavior to docs/product/example.md.",
                "",
                "This is a public contract.",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_superpowers_specs_non_markdown_payload_fails(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/specs/example-schema.json"
    _write(tmp_path, path, "{}\n")

    problems = _problems(tmp_path, "A", path)

    assert any("Markdown-only" in problem for problem in problems)


def test_parse_name_status_keeps_specs_non_markdown_payloads() -> None:
    entries, ignored_deletions = parse_name_status(
        "A\tdocs/superpowers/specs/example-schema.json\nA\toutput/private.json\n"
    )

    assert ignored_deletions == 0
    assert [entry.path for entry in entries] == [
        "docs/superpowers/specs/example-schema.json"
    ]


def test_parse_name_status_treats_rename_source_as_deletion() -> None:
    entries, ignored_deletions = parse_name_status(
        "R100\t"
        "docs/superpowers/plans/2026-04-07-old-plan.md\t"
        "docs/product/old-plan.md\n"
    )

    assert ignored_deletions == 0
    assert [(entry.status, entry.path) for entry in entries] == [
        ("D", "docs/superpowers/plans/2026-04-07-old-plan.md"),
        ("R", "docs/product/old-plan.md"),
    ]


def test_repo_support_doc_requires_owner_and_passes(tmp_path: Path) -> None:
    path = "docs/superpowers/file-management/docs-cleanup/example-support-note.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Example support note",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: validation_artifact",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/backfill.md",
                "",
                "Compact validation note supporting the Backfill owner.",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_repo_support_doc_without_owner_fails(tmp_path: Path) -> None:
    path = "docs/superpowers/file-management/docs-cleanup/example-support-note.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Example support note",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: validation_artifact",
                "Doc lifecycle: archived",
                "",
                "Compact validation note supporting the Backfill owner.",
            ]
        ),
    )

    problems = _problems(tmp_path, "A", path)

    assert any("requires a repo owner" in problem for problem in problems)


def test_repo_subcontract_doc_requires_owner_and_passes(tmp_path: Path) -> None:
    path = "docs/validation/example-subcontract.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Example subcontract",
                "",
                "Doc placement: repo_subcontract_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Repo owner: docs/product/backfill.md",
                "Doc exit rule: retire after the canonical owner absorbs it.",
                "",
                "Bounded Backfill contract.",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_repo_subcontract_doc_without_owner_fails(tmp_path: Path) -> None:
    path = "docs/validation/example-subcontract.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Example subcontract",
                "",
                "Doc placement: repo_subcontract_doc",
                "Doc kind: spec",
                "Doc lifecycle: active",
                "Doc exit rule: retire after the canonical owner absorbs it.",
                "",
                "Bounded Backfill contract.",
            ]
        ),
    )

    problems = _problems(tmp_path, "A", path)

    assert any("requires a repo owner" in problem for problem in problems)


def test_branch_closeout_summary_with_pr_body_seed_passes(tmp_path: Path) -> None:
    path = (
        "docs/superpowers/closeouts/2026-07-01_codex-example_branch-closeout-summary.md"
    )
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Branch closeout",
                "",
                "## Problem",
                "",
                "## Solution",
                "",
                "## Verification",
                "",
                "## Residual Risk",
                "",
                "## PR Body Seed",
                "",
                "Problem: example.",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_file_management_cleanup_evidence_passes_without_marker(tmp_path: Path) -> None:
    path = (
        "docs/superpowers/file-management/docs-cleanup/"
        "2026-07-01_codex-docs-cleanup_git-rm-candidate-manifest.md"
    )
    _write(
        tmp_path,
        path,
        "# Candidate manifest\n\nPublic cleanup evidence for approved review.\n",
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_handoff_archive_cleanup_evidence_without_marker_fails(tmp_path: Path) -> None:
    path = (
        "docs/superpowers/handoffs/archive/"
        "2026-07-01_codex-docs-cleanup_git-rm-candidate-manifest.md"
    )
    _write(
        tmp_path,
        path,
        "# Candidate manifest\n\nPublic cleanup evidence in the wrong lane.\n",
    )

    problems = _problems(tmp_path, "A", path)

    assert any("do not belong under handoffs" in problem for problem in problems)


def test_handoff_archive_private_diary_without_marker_fails(tmp_path: Path) -> None:
    path = "docs/superpowers/handoffs/archive/2026-07-01_codex-example_branch-diary.md"
    _write(
        tmp_path,
        path,
        "# Branch diary\n\nImplementation diary and command transcript.\n",
    )

    problems = _problems(tmp_path, "A", path)

    assert any("lacks placement marker" in problem for problem in problems)


def test_force_added_local_handoff_without_marker_fails(tmp_path: Path) -> None:
    path = "docs/superpowers/handoffs/current/ACTIVE.local.md"
    _write(
        tmp_path,
        path,
        "# Active local handoff\n\nImplementation diary and next actions.\n",
    )

    problems = _problems(tmp_path, "A", path)

    assert any("lacks placement marker" in problem for problem in problems)


def test_branch_named_productization_active_stub_is_not_misplaced_record(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/handoffs/current/codex-productization.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Productization handoff",
                "",
                "Doc placement: repo_active_stub",
                "Repo owner: docs/superpowers/handoffs/current/codex-productization.md",
                "",
                "Objective: keep productization work recoverable.",
                "Scope: active branch state only.",
                "Constraints: no public status authority.",
                "Next 1-3 actions:",
                "1. Re-run the guard.",
                "Verification: hook fixture.",
                "Stop rule: stop if the stub cannot name the next action.",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_productization_status_handoff_remains_canonical(tmp_path: Path) -> None:
    path = (
        "docs/superpowers/productization/status/"
        "cc-framework-improvements-productization.md"
    )
    _write(tmp_path, path, "# Productization status anchor\n")

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_branch_closeout_summary_still_requires_pr_body_seed(tmp_path: Path) -> None:
    path = (
        "docs/superpowers/closeouts/2026-07-01_codex-example_branch-closeout-summary.md"
    )
    _write(tmp_path, path, "# Branch closeout\n\n## Verification\n")

    problems = _problems(tmp_path, "A", path)

    assert any("missing PR Body Seed" in problem for problem in problems)


def test_private_obsidian_note_staged_under_repo_docs_fails(tmp_path: Path) -> None:
    path = "docs/superpowers/notes/2026-07-01-private-review-note.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Private review note",
                "",
                "Doc placement: private_obsidian_note",
                "",
                "Review rationale and branch sequencing.",
            ]
        ),
    )

    problems = _problems(tmp_path, "A", path)

    assert any("not a repo-trackable placement" in problem for problem in problems)


def test_new_tracked_note_lane_fails_even_with_repo_stub_metadata(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/notes/2026-07-01-private-review-note.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Private review note",
                "",
                "Doc placement: repo_stub_plus_obsidian",
                "Doc kind: note",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/review-roundtrip.md",
                "",
                "This file is a sanitized public stub.",
            ]
        ),
    )

    problems = _problems(tmp_path, "A", path)

    assert any("retired docs lane" in problem for problem in problems)


def test_new_tracked_deepresearch_lane_fails(tmp_path: Path) -> None:
    path = "docs/deepresearch/2026-07-01-literature-note.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Literature note",
                "",
                "Doc placement: repo_stub_plus_obsidian",
                "Doc kind: note",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/untargeted-method.md",
                "",
                "Stable claims should be absorbed into the product owner.",
            ]
        ),
    )

    problems = _problems(tmp_path, "A", path)

    assert any("retired docs lane" in problem for problem in problems)


def test_sanitized_stub_plus_obsidian_passes(tmp_path: Path) -> None:
    path = "docs/superpowers/plans/2026-07-01-private-review-note.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Private review note",
                "",
                "Doc placement: repo_stub_plus_obsidian",
                "Doc kind: note",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/review-roundtrip.md",
                "",
                "This file is a sanitized public stub.",
            ]
        ),
    )

    result = check_doc_placement(tmp_path, [StagedMarkdown("A", path)])

    assert result.problems == ()


def test_non_allowlisted_docs_root_markdown_fails(tmp_path: Path) -> None:
    path = "docs/2026-07-01-branch-report.md"
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Branch report",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: report",
                "Doc lifecycle: archived",
                "Repo owner: docs/product/productization.md",
                "Doc exit rule: retire after summary is absorbed.",
            ]
        ),
    )

    problems = _problems(tmp_path, "A", path)

    assert any("docs root only allows" in problem for problem in problems)


def test_non_lifecycle_deletions_are_ignored_by_placement_guard() -> None:
    entries, ignored_deletions = parse_name_status("D\tdocs/product/backfill.md\n")

    result = check_doc_placement(
        Path("."),
        entries,
        ignored_deletions=ignored_deletions,
    )

    assert result.checked_count == 0
    assert result.ignored_deletions == 1
    assert result.problems == ()


def test_lifecycle_managed_deletion_requires_retirement_evidence() -> None:
    path = "docs/superpowers/plans/2026-04-07-old-plan.md"
    entries, ignored_deletions = parse_name_status(f"D\t{path}\n")

    result = check_doc_placement(
        Path("."),
        entries,
        ignored_deletions=ignored_deletions,
    )

    assert result.checked_count == 1
    assert result.ignored_deletions == 0
    assert any(
        problem.path == path
        and "deletion requires staged retirement evidence" in problem.reason
        for problem in result.problems
    )


def test_lifecycle_managed_rename_source_requires_retirement_evidence() -> None:
    source = "docs/superpowers/plans/2026-04-07-old-plan.md"
    destination = "docs/product/old-plan.md"
    entries, ignored_deletions = parse_name_status(f"R100\t{source}\t{destination}\n")

    result = check_doc_placement(
        Path("."),
        entries,
        ignored_deletions=ignored_deletions,
    )

    assert any(
        problem.path == source
        and "deletion requires staged retirement evidence" in problem.reason
        for problem in result.problems
    )


def test_lifecycle_managed_deletion_with_retirement_evidence_passes(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/plans/2026-04-07-old-plan.md"
    old_text = "# Old plan\n\nRetired source text.\n"
    evidence_path = (
        "docs/superpowers/file-management/docs-cleanup/"
        "2026-07-01-retirement-evidence.json"
    )
    evidence = _retirement_evidence(path, old_text)
    entries = [
        StagedMarkdown("D", path, old_text),
        StagedMarkdown("A", evidence_path, json.dumps(evidence)),
        StagedMarkdown("A", "docs/product/backfill.md", "# Backfill\n"),
    ]

    result = check_doc_placement(tmp_path, entries)

    assert result.checked_count == 2
    assert result.ignored_deletions == 0
    assert result.problems == ()


def test_lifecycle_managed_deletion_rejects_hash_mismatch() -> None:
    path = "docs/superpowers/plans/2026-04-07-old-plan.md"
    old_text = "# Old plan\n\nRetired source text.\n"
    evidence_path = (
        "docs/superpowers/file-management/docs-cleanup/"
        "2026-07-01-retirement-evidence.json"
    )
    evidence = _retirement_evidence(path, "different text")
    entries = [
        StagedMarkdown("D", path, old_text),
        StagedMarkdown("A", evidence_path, json.dumps(evidence)),
    ]

    result = check_doc_placement(Path("."), entries)

    assert any("source_hash mismatch" in problem.reason for problem in result.problems)


def test_lifecycle_managed_deletion_rejects_remaining_referrers(
    tmp_path: Path,
) -> None:
    path = "docs/superpowers/plans/2026-04-07-old-plan.md"
    old_text = "# Old plan\n\nRetired source text.\n"
    referrer = "docs/product/backfill.md"
    _write(tmp_path, referrer, f"# Backfill\n\nHistorical source: `{path}`.\n")
    evidence_path = (
        "docs/superpowers/file-management/docs-cleanup/"
        "2026-07-01-retirement-evidence.json"
    )
    evidence = _retirement_evidence(path, old_text)
    entries = [
        StagedMarkdown("D", path, old_text),
        StagedMarkdown("A", evidence_path, json.dumps(evidence)),
    ]

    result = check_doc_placement(tmp_path, entries)

    assert any(
        "exact repo referrers require retargeting" in problem.reason
        for problem in result.problems
    )


def test_cli_reads_staged_blob_not_worktree_after_divergence(tmp_path: Path) -> None:
    path = "docs/superpowers/plans/2026-07-01-example-implementation-plan.md"
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    _write(
        tmp_path,
        path,
        "# Example plan\n\nImplementation diary and command log details.\n",
    )
    subprocess.run(["git", "add", path], cwd=tmp_path, check=True, capture_output=True)
    _write(
        tmp_path,
        path,
        "\n".join(
            [
                "# Active work stub",
                "",
                "Doc placement: repo_active_stub",
                "Doc kind: plan",
                "Doc lifecycle: active",
                "Repo owner: docs/superpowers/handoffs/current/example.md",
                "Doc exit rule: replace with branch closeout after implementation.",
                "",
                "Objective: keep the next action recoverable.",
                "Scope: docs workflow only.",
                "Constraints: no destructive cleanup.",
                "Next 1-3 actions:",
                "1. Run the checker.",
                "Verification: focused pytest.",
                "Stop rule: stop if the repo stub is not self-sufficient.",
            ]
        ),
    )

    entries, ignored_deletions = staged_markdown(tmp_path)
    result = check_doc_placement(
        tmp_path,
        entries,
        ignored_deletions=ignored_deletions,
    )

    assert any(
        "lacks placement marker" in problem.reason for problem in result.problems
    )
