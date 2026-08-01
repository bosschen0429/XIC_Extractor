from __future__ import annotations

from pathlib import Path

import pytest

from tools.diagnostics.docs_policy import (
    DOC_ROUTING_SCHEMAS_INDEX_PATH,
    DOC_ROUTING_SPECS_INDEX_PATH,
    PERMANENT_ROOT_DOC_CONTRACTS,
    classify_doc,
    classify_doc_path,
    is_productization_compatibility_index_text,
)
from tools.diagnostics.docs_scan import (
    normalize_repo_rel_path,
    normalized_path_dependency_census,
    permanent_root_contract_problems,
    resolve_repo_path,
    resolve_repo_target,
)


def test_permanent_root_contract_is_exactly_four_paths() -> None:
    assert set(PERMANENT_ROOT_DOC_CONTRACTS) == {
        "AGENTS.md",
        "README.md",
        "docs/architecture-contract.md",
        "docs/project-layout.md",
    }


@pytest.mark.parametrize(
    "path",
    (
        "../outside.md",
        "../../outside.md",
        "/tmp/outside.md",
        "C:\\outside.md",
        "\\\\server\\share\\outside.md",
        ".",
        "",
    ),
)
def test_repo_relative_path_normalizer_rejects_escape_paths(path: str) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        normalize_repo_rel_path(path)


def test_repo_relative_path_normalizer_accepts_internal_path() -> None:
    assert normalize_repo_rel_path("./docs/product/../product/backfill.md") == (
        "docs/product/backfill.md"
    )


def test_relative_link_resolution_rejects_repo_escape() -> None:
    assert resolve_repo_target("docs/guide.md", "../../outside.md") is None


def test_repo_path_resolution_rejects_symlink_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (repo / "owner.md").symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows runner lacks symbolic-link privilege")
        raise

    with pytest.raises(ValueError, match="resolves outside repository"):
        resolve_repo_path(repo, "owner.md")


def test_current_control_plane_is_a_compatibility_index_preimage() -> None:
    root = Path(__file__).parents[1]
    text = (
        root
        / "docs/superpowers/plans/2026-06-15-productization-control-plane.md"
    ).read_text(encoding="utf-8")

    assert is_productization_compatibility_index_text(text)
    assert not is_productization_compatibility_index_text(
        "# Productization compatibility index\n"
    )


def test_current_permanent_root_contracts_have_positive_landmarks() -> None:
    root = Path(__file__).parents[1]

    for rel_path in PERMANENT_ROOT_DOC_CONTRACTS:
        assert (
            permanent_root_contract_problems(
                root,
                rel_path,
                (root / rel_path).read_text(encoding="utf-8"),
            )
            == ()
        )


def test_permanent_root_contract_rejects_missing_route_and_wrong_owner() -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("(docs/product/README.md)", "(missing-product-owner.md)", 1)
    text += "\nRepo owner: docs/product/productization.md\n"

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert any("required route must resolve" in problem for problem in problems)
    assert any("conflicting Repo owner" in problem for problem in problems)


def test_permanent_root_contract_ignores_fenced_only_route() -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("(docs/product/README.md)", "(missing-product-owner.md)", 1)
    text += "\n```markdown\n[fenced route](docs/product/README.md)\n```\n"

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required route must resolve: docs/product/README.md" in problems


@pytest.mark.parametrize(
    "code_only_route",
    (
        "`[sample](docs/product/README.md)`",
        "``[sample](docs/product/README.md)``",
        "``code\n[sample](docs/product/README.md)\n``",
        "    [sample](docs/product/README.md)",
        ">     [sample](docs/product/README.md)",
        "-     [sample](docs/product/README.md)",
        "1.     [sample](docs/product/README.md)",
        "> -     [sample](docs/product/README.md)",
        "- -     [sample](docs/product/README.md)",
        "-   \t[sample](docs/product/README.md)",
    ),
)
def test_permanent_root_contract_ignores_non_fenced_code_only_route(
    code_only_route: str,
) -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("(docs/product/README.md)", "(missing-product-owner.md)", 1)
    text += "\n" + code_only_route + "\n"

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required route must resolve: docs/product/README.md" in problems


@pytest.mark.parametrize(
    "non_link_route",
    (
        "[unused]: docs/product/README.md",
        "![sample](docs/product/README.md)",
        "<!-- [sample](docs/product/README.md) -->",
        r"\[sample](docs/product/README.md)",
    ),
)
def test_permanent_root_contract_requires_rendered_clickable_route(
    non_link_route: str,
) -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("(docs/product/README.md)", "(missing-product-owner.md)", 1)
    text += "\n" + non_link_route + "\n"

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required route must resolve: docs/product/README.md" in problems


def test_permanent_root_contract_uses_first_reference_definition() -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("(docs/product/README.md)", "(missing-product-owner.md)", 1)
    text += (
        "\n[sample][route]\n\n"
        "[route]: missing-product-owner.md\n"
        "[route]: docs/product/README.md\n"
    )

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required route must resolve: docs/product/README.md" in problems


@pytest.mark.parametrize(
    "code_only_landmark",
    (
        "`This document owns`",
        "``This document owns``",
        "    This document owns",
        ">     This document owns",
    ),
)
def test_permanent_root_contract_ignores_non_fenced_code_only_landmark(
    code_only_landmark: str,
) -> None:
    root = Path(__file__).parents[1]
    rel_path = "docs/architecture-contract.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("This document owns", "This document describes", 1)
    text += "\n" + code_only_landmark + "\n"

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required purpose landmark must occur exactly once: This document owns" in (
        problems
    )


def test_permanent_root_contract_ignores_html_comment_only_heading() -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace(
        "# XIC Extractor Agent Contract",
        "# XIC Extractor Repository",
        1,
    )
    text += "\n<!--\n# XIC Extractor Agent Contract\n-->\n"

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required heading must occur exactly once: XIC Extractor Agent Contract" in (
        problems
    )


def test_permanent_root_contract_ignores_html_comment_only_landmark() -> None:
    root = Path(__file__).parents[1]
    rel_path = "docs/architecture-contract.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("This document owns", "This document describes", 1)
    text += "\n<!-- This document owns -->\n"

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required purpose landmark must occur exactly once: This document owns" in (
        problems
    )


def test_permanent_root_contract_ignores_nested_fenced_only_route() -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("(docs/product/README.md)", "(missing-product-owner.md)", 1)
    text += (
        "\n100. Example only\n"
        "     ```markdown\n"
        "     [fenced route](docs/product/README.md)\n"
        "     ```\n"
    )

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required route must resolve: docs/product/README.md" in problems


@pytest.mark.parametrize(
    "fenced_example",
    (
        ("> ```markdown\n"
         "> [fenced route](docs/product/README.md)\n"
         "> ```\n"),
        ("> 1. ```markdown\n"
         ">    [fenced route](docs/product/README.md)\n"
         ">    ```\n"),
    ),
)
def test_permanent_root_contract_ignores_blockquoted_fenced_only_route(
    fenced_example: str,
) -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("(docs/product/README.md)", "(missing-product-owner.md)", 1)
    text += "\n" + fenced_example

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required route must resolve: docs/product/README.md" in problems


@pytest.mark.parametrize(
    "fenced_example",
    (
        ("```markdown\n"
         "- ```\n"
         "[fenced route](docs/product/README.md)\n"
         "```\n"),
        ("> ```markdown\n"
         "> > ```\n"
         "> [fenced route](docs/product/README.md)\n"
         "> ```\n"),
        ("```markdown\n"
         "    ```\n"
         "[fenced route](docs/product/README.md)\n"
         "```\n"),
        ("> ```markdown\n"
         ">     ```\n"
         "> [fenced route](docs/product/README.md)\n"
         "> ```\n"),
        ("- ```markdown\n"
         "```\n"
         "[fenced route](docs/product/README.md)\n"
         "  ```\n"),
        ("- > ```markdown\n"
         "> ```\n"
         "> [fenced route](docs/product/README.md)\n"
         "  > ```\n"),
    ),
)
def test_permanent_root_contract_ignores_faux_closing_container_in_fence(
    fenced_example: str,
) -> None:
    root = Path(__file__).parents[1]
    rel_path = "AGENTS.md"
    text = (root / rel_path).read_text(encoding="utf-8")
    text = text.replace("(docs/product/README.md)", "(missing-product-owner.md)", 1)
    text += "\n" + fenced_example

    problems = permanent_root_contract_problems(root, rel_path, text)

    assert "required route must resolve: docs/product/README.md" in problems


def test_dependency_census_resolves_relative_markdown_html_and_constant_consumers(
    tmp_path: Path,
) -> None:
    target = "/".join(
        (
            "docs",
            "superpowers",
            "plans",
            "2026-06-15-productization-control-plane.md",
        )
    )
    files = {
        "docs/guide/ref.md": (
            "[control][control-plane]\n\n"
            "[control-plane]:\n  ../superpowers/plans/"
            "2026-06-15-productization-control-plane.md\n"
        ),
        "docs/report.html": (
            "<a HREF=superpowers/plans/"
            "2026-06-15-productization-control-plane.md>control</a>\n"
        ),
        "notes.txt": f"literal: {target}\n",
        "tools/check.py": "from policy import " + "CONTROL_" + "PLANE_PATH\n",
    }
    for rel_path, text in files.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    census = normalized_path_dependency_census(
        tmp_path,
        target,
        scan_paths=tuple(files),
    )

    assert census["literal_path_referrers"] == ["notes.txt"]
    assert census["relative_markdown_links"] == [
        {
            "referrer": "docs/guide/ref.md",
            "raw_target": (
                "../superpowers/plans/"
                "2026-06-15-productization-control-plane.md"
            ),
            "resolved_path": target,
        }
    ]
    assert census["relative_html_links"] == [
        {
            "referrer": "docs/report.html",
            "raw_target": (
                "superpowers/plans/"
                "2026-06-15-productization-control-plane.md"
            ),
            "resolved_path": target,
        }
    ]
    assert census["control_plane_constant_consumers"] == ["tools/check.py"]


def test_docs_policy_marks_validation_as_route_retained_contract_surface() -> None:
    classification = classify_doc(
        "docs/superpowers/validation/current-packet.md",
        "# Validation packet\n\nDoc kind: validation_artifact\n",
    )

    assert classification.is_docs_routing_scan_target
    assert classification.is_validation_or_fixture
    assert not classification.is_high_risk_repo_doc
    assert classification.doc_kind == "validation_artifact"
    assert classification.doc_kind_source == "declared"


def test_docs_policy_marks_plan_as_high_risk_lifecycle_doc_not_history() -> None:
    classification = classify_doc_path(
        "docs/superpowers/plans/2026-07-01-example-plan.md"
    )

    assert classification.is_repo_doc
    assert classification.is_high_risk_repo_doc
    assert classification.is_lifecycle_managed
    assert not classification.is_legacy_history
    assert classification.inferred_kind == "plan"


def test_docs_policy_uses_metadata_before_path_kind_inference() -> None:
    classification = classify_doc(
        "docs/superpowers/plans/2026-07-01-generated-cleanup-table.md",
        "\n".join(
            [
                "# Cleanup table",
                "",
                "Doc placement: repo_support_doc",
                "Doc kind: manifest",
                "Doc lifecycle: archived",
                "Repo owner: docs/project-layout.md",
                "Doc exit rule: keep while cleanup referrers need this manifest.",
            ]
        ),
    )

    assert classification.doc_kind == "manifest"
    assert classification.doc_kind_source == "declared"
    assert classification.path_classification.inferred_kind == "plan"
    assert classification.doc_lifecycle == "archived"
    assert classification.lifecycle_status == "declared"


def test_docs_policy_falls_back_to_path_kind_without_metadata() -> None:
    classification = classify_doc(
        "docs/superpowers/goals/2026-07-01-open-question.md",
        "# Open question\n\nNeeds a route decision.\n",
    )

    assert classification.doc_kind == "goal"
    assert classification.doc_kind_source == "inferred"
    assert classification.doc_lifecycle == "unknown"
    assert classification.lifecycle_status == "missing_lifecycle"


def test_docs_policy_reads_non_repo_placement_from_metadata() -> None:
    classification = classify_doc(
        "docs/superpowers/notes/private-review-note.md",
        "\n".join(
            [
                "# Private note",
                "",
                "Doc placement: private_obsidian_note",
                "Doc kind: note",
                "Doc lifecycle: archived",
                "Doc exit rule: source-copy to Obsidian and keep no repo body.",
            ]
        ),
    )

    assert classification.placement == "private_obsidian_note"
    assert classification.doc_kind == "note"
    assert classification.lifecycle_status == "declared"


def test_docs_policy_keeps_root_authority_docs_canonical_not_high_risk() -> None:
    classification = classify_doc(
        "docs/diagnostic-ledger.md",
        "\n".join(
            [
                "# Diagnostic Ledger",
                "",
                "Doc placement: formal_repo_doc",
                "Doc kind: report",
                "Doc lifecycle: active",
                "Repo owner: docs/diagnostic-ledger.md",
                (
                    "Doc exit rule: keep active while expensive RAW rerun "
                    "memory is needed."
                ),
            ]
        ),
    )

    assert classification.is_repo_doc
    assert classification.is_canonical_owner
    assert classification.is_canonical_owner_source == "path"
    assert not classification.is_high_risk_repo_doc
    assert classification.doc_kind == "report"
    assert classification.doc_kind_source == "declared"
    assert classification.repo_owner == "docs/diagnostic-ledger.md"
    assert classification.is_allowed_docs_root_file
    assert not classification.is_docs_root_scatter


def test_docs_policy_marks_non_allowlisted_docs_root_file_as_scatter() -> None:
    classification = classify_doc_path("docs/2026-07-01-branch-report.md")

    assert classification.is_docs_root_file
    assert classification.is_docs_root_scatter
    assert not classification.is_allowed_docs_root_file


def test_docs_policy_marks_retired_superpowers_lanes() -> None:
    classification = classify_doc_path(
        "docs/superpowers/notes/2026-07-01-observation.md"
    )

    assert classification.is_retired_tracked_dir
    assert classification.retired_tracked_dir == "docs/superpowers/notes/"


def test_docs_policy_marks_deepresearch_as_retired_lane() -> None:
    classification = classify_doc_path(
        "docs/deepresearch/Untargeted LC-MS Feature Discovery Background.md"
    )

    assert classification.is_retired_tracked_dir
    assert classification.retired_tracked_dir == "docs/deepresearch/"


def test_docs_policy_marks_specs_as_transient_markdown_lane() -> None:
    classification = classify_doc_path(
        "docs/superpowers/specs/2026-07-01-example-contract.md"
    )

    assert not classification.is_retired_tracked_dir
    assert classification.is_high_risk_repo_doc
    assert classification.is_lifecycle_managed
    assert classification.inferred_kind == "spec"


def test_formal_repo_doc_placement_upgrades_canonical_owner() -> None:
    classification = classify_doc(
        "docs/superpowers/notes/some-important-note.md",
        "\n".join(
            [
                "# Important note",
                "",
                "Doc placement: formal_repo_doc",
                "Doc kind: note",
                "Doc lifecycle: active",
                "Repo owner: docs/superpowers/notes/some-important-note.md",
                "Doc exit rule: keep while referenced.",
            ]
        ),
    )

    assert classification.is_canonical_owner
    assert classification.is_canonical_owner_source == "metadata_override"
    assert not classification.is_high_risk_repo_doc
    assert classification.is_high_risk_source == "metadata_cleared"


def test_any_placement_clears_high_risk() -> None:
    classification = classify_doc(
        "docs/superpowers/notes/private-review.md",
        "\n".join(
            [
                "# Private review",
                "",
                "Doc placement: repo_stub_plus_obsidian",
                "Doc kind: note",
                "Doc lifecycle: archived",
                "Repo owner: docs/superpowers/notes/private-review.md",
                "Doc exit rule: source-copy to Obsidian.",
            ]
        ),
    )

    assert not classification.is_high_risk_repo_doc
    assert classification.is_high_risk_source == "metadata_cleared"
    assert not classification.is_canonical_owner


def test_no_placement_keeps_high_risk() -> None:
    classification = classify_doc(
        "docs/superpowers/notes/unclassified-note.md",
        "# Unclassified note\n\nNo metadata markers.\n",
    )

    assert classification.is_high_risk_repo_doc
    assert classification.is_high_risk_source == "path"
    assert not classification.is_canonical_owner
    assert classification.is_canonical_owner_source == "path"


def test_canonical_path_stays_canonical_without_metadata() -> None:
    classification = classify_doc(
        "docs/product/user-guide.md",
        "# User guide\n\nNo placement metadata.\n",
    )

    assert classification.is_canonical_owner
    assert classification.is_canonical_owner_source == "path"
    assert not classification.is_high_risk_repo_doc
    assert classification.is_high_risk_source == "path"


def test_lifecycle_managed_not_overridden_by_metadata() -> None:
    classification = classify_doc(
        "docs/superpowers/plans/2026-07-01-example-plan.md",
        "\n".join(
            [
                "# Example plan",
                "",
                "Doc placement: formal_repo_doc",
                "Doc kind: plan",
                "Doc lifecycle: active",
                "Repo owner: docs/superpowers/plans/2026-07-01-example-plan.md",
                "Doc exit rule: keep while plan is active.",
            ]
        ),
    )

    assert classification.is_lifecycle_managed


def test_superpowers_index_constants_match_case_sensitive_paths() -> None:
    assert DOC_ROUTING_SPECS_INDEX_PATH == "docs/superpowers/specs/README.md"
    assert DOC_ROUTING_SCHEMAS_INDEX_PATH == "docs/superpowers/schemas/README.md"
    assert Path(DOC_ROUTING_SPECS_INDEX_PATH).exists()
    assert Path(DOC_ROUTING_SCHEMAS_INDEX_PATH).exists()
