from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import scripts.check_agent_delivery_contract as contract_module
from scripts.check_agent_delivery_contract import (
    main,
    validate_issue_contract,
    validate_pr_contract,
)

FIXTURES = Path("tests/fixtures/agent_delivery_contract")
HEAD_SHA = "a" * 40


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_complete_atomic_issue_and_pr_contracts_pass() -> None:
    assert validate_issue_contract(_fixture("valid_issue.md")) == ()
    assert validate_pr_contract(_fixture("valid_pr.md"), HEAD_SHA) == ()


def test_each_independently_useful_multi_pr_unit_can_pass_alone() -> None:
    assert validate_issue_contract(_fixture("valid_multi_issue.md")) == ()
    assert validate_pr_contract(_fixture("valid_multi_pr.md"), HEAD_SHA) == ()
    assert validate_pr_contract(_fixture("valid_multi_pr_two.md"), HEAD_SHA) == ()


def test_generic_size_word_cannot_justify_a_multi_pr_seam() -> None:
    body = _fixture("valid_multi_pr.md").replace(
        "a distinct docs-rendering failure domain with an existing repository consumer",
        "the change is too large for one PR",
    )

    assert any(
        "split rationale cannot use size" in error
        for error in validate_pr_contract(body, HEAD_SHA)
    )


def test_pr_identity_must_match_the_validated_repository() -> None:
    body = _fixture("valid_pr.md").replace(
        "Chao-hu-Lab/XIC_Extractor#368",
        "other/repository#368",
    )

    assert (
        "Issue identity repository must match the validated repository"
        in validate_pr_contract(
            body,
            HEAD_SHA,
            expected_repository="Chao-hu-Lab/XIC_Extractor",
        )
    )


@pytest.mark.parametrize(
    ("fixture", "kind", "expected"),
    (
        (
            "goal_without_acceptance.md",
            "issue",
            "goal has no decisive executable acceptance",
        ),
        (
            "foundation_pr.md",
            "pr",
            "current consumer must exist before any later sibling",
        ),
        (
            "size_split_pr.md",
            "pr",
            "split rationale cannot use size, context, review-time, or "
            "construction-stage seams",
        ),
        (
            "activation_without_retirement_pr.md",
            "pr",
            "activation or replacement requires retirement or an explicit "
            "retention obligation",
        ),
        (
            "provisional_ready_issue.md",
            "issue_with_labels",
            "ready-for-agent cannot certify a blocked or provisional issue",
        ),
    ),
)
def test_required_counterexamples_fail_closed(
    fixture: str,
    kind: str,
    expected: str,
) -> None:
    body = _fixture(fixture)
    labels: tuple[str, ...] = ()
    if kind == "issue_with_labels":
        body, labels = _split_fixture_metadata(body)
        errors = validate_issue_contract(body, labels=labels)
    elif kind == "issue":
        errors = validate_issue_contract(body)
    else:
        errors = validate_pr_contract(body, HEAD_SHA)

    assert any(expected in error for error in errors), errors


@pytest.mark.parametrize(
    ("original", "replacement", "expected"),
    (
        (
            "### Goal",
            "### Intended outcome",
            "missing required section: Goal",
        ),
        (
            "## Ticket-to-PR closure receipt",
            "## Delivery summary",
            "missing required section: Ticket-to-PR closure receipt",
        ),
    ),
)
def test_arbitrary_heading_synonyms_do_not_satisfy_the_contract(
    original: str,
    replacement: str,
    expected: str,
) -> None:
    source = "valid_issue.md" if original.startswith("###") else "valid_pr.md"
    body = _fixture(source).replace(original, replacement, 1)
    errors = (
        validate_issue_contract(body)
        if source.startswith("valid_issue")
        else validate_pr_contract(body, HEAD_SHA)
    )

    assert expected in errors


def test_duplicate_required_heading_fails_closed() -> None:
    body = _fixture("valid_issue.md") + "\n### Goal\n\nA second goal.\n"

    assert "required section appears more than once: Goal" in validate_issue_contract(
        body
    )


def test_extra_prose_cannot_supply_a_missing_declared_field() -> None:
    body = _fixture("valid_pr.md").replace(
        "- Current consumer: implementation authors and PR reviewers using "
        "repository templates now\n",
        "Current consumer: implementation authors and PR reviewers using "
        "repository templates now\n",
        1,
    )

    assert any(
        "missing declared field: Current consumer" in error
        for error in validate_pr_contract(body, HEAD_SHA)
    )


def test_issue_form_renders_a_validator_accepted_contract() -> None:
    template = yaml.safe_load(
        Path(".github/ISSUE_TEMPLATE/implementation_change.yml").read_text(
            encoding="utf-8"
        )
    )
    valid_sections = _issue_sections(_fixture("valid_issue.md"))
    rendered = []
    for field in template["body"]:
        label = field["attributes"]["label"]
        rendered.append(f"### {label}\n\n{valid_sections[label]}")

    assert validate_issue_contract("\n\n".join(rendered)) == ()


def test_pr_template_projects_a_validator_accepted_closure_receipt() -> None:
    template = Path(".github/pull_request_template.md").read_text(encoding="utf-8")
    source = _fixture("valid_pr.md")
    closure = _h2_section(source, "Ticket-to-PR closure receipt")
    verification = _h2_section(source, "Verification")
    linkage = _h2_section(source, "Issue and delivery linkage")
    rendered = _replace_h2_section(template, "Ticket-to-PR closure receipt", closure)
    rendered = _replace_h2_section(rendered, "Verification", verification)
    rendered = _replace_h2_section(rendered, "Issue and delivery linkage", linkage)

    assert validate_pr_contract(rendered, HEAD_SHA) == ()


def test_offline_cli_uses_body_file_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    issue_path = tmp_path / "issue.md"
    issue_path.write_text(_fixture("valid_issue.md"), encoding="utf-8")
    pr_path = tmp_path / "pr.md"
    pr_path.write_text(_fixture("valid_pr.md"), encoding="utf-8")

    assert main(["--kind", "issue", "--body-file", str(issue_path)]) == 0
    assert (
        main(["--kind", "pr", "--body-file", str(pr_path), "--head-sha", HEAD_SHA]) == 0
    )
    assert capsys.readouterr().out.count("PASS") == 2


def test_multi_pr_issue_and_each_pr_unit_pass_the_cli_oracle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "--kind",
                "issue",
                "--body-file",
                str(FIXTURES / "valid_multi_issue.md"),
            ]
        )
        == 0
    )
    for name in ("valid_multi_pr.md", "valid_multi_pr_two.md"):
        assert (
            main(
                [
                    "--kind",
                    "pr",
                    "--body-file",
                    str(FIXTURES / name),
                    "--head-sha",
                    HEAD_SHA,
                ]
            )
            == 0
        )
    assert capsys.readouterr().out.count("PASS") == 3


@pytest.mark.parametrize(
    ("status", "labels", "classification"),
    (
        ("ready-for-agent", ("ready-for-agent",), "conforming-ready"),
        ("blocked by #362", (), "conforming-blocked-unready"),
    ),
)
def test_live_audit_classifies_legacy_atomic_issues_without_canonical_certification(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    labels: tuple[str, ...],
    classification: str,
) -> None:
    body = _fixture("legacy_atomic_issue.md").replace(
        "- Status: `ready-for-agent`",
        f"- Status: `{status}`",
    )

    def fake_run_gh_json(arguments: tuple[str, ...]) -> dict[str, object]:
        assert arguments[:2] == ("issue", "view")
        return {
            "number": 364,
            "body": body,
            "labels": [{"name": label} for label in labels],
            "state": "OPEN",
            "url": "https://github.com/Chao-hu-Lab/XIC_Extractor/issues/364",
        }

    monkeypatch.setattr(contract_module, "_run_gh_json", fake_run_gh_json)

    assert main(["--issue", "364", "--repo", "Chao-hu-Lab/XIC_Extractor"]) == 0
    output = capsys.readouterr().out
    assert "AUDIT" in output
    assert f"contract={classification}" in output
    assert "canonical=false" in output


def _split_fixture_metadata(document: str) -> tuple[str, tuple[str, ...]]:
    assert document.startswith("---\n")
    _, metadata, body = document.split("---\n", 2)
    labels = tuple(
        value.strip()
        for line in metadata.splitlines()
        if line.startswith("labels:")
        for value in line.split(":", 1)[1].split(",")
        if value.strip()
    )
    return body.lstrip(), labels


def _issue_sections(document: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in document.splitlines():
        if line.startswith("### ") and not line.startswith("#### "):
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = line[4:]
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections


def _h2_section(document: str, heading: str) -> str:
    marker = f"## {heading}\n"
    start = document.index(marker) + len(marker)
    next_heading = document.find("\n## ", start)
    return document[
        start : next_heading if next_heading >= 0 else len(document)
    ].strip()


def _replace_h2_section(document: str, heading: str, value: str) -> str:
    marker = f"## {heading}\n"
    start = document.index(marker) + len(marker)
    next_heading = document.find("\n## ", start)
    end = next_heading if next_heading >= 0 else len(document)
    return document[:start] + "\n" + value + "\n" + document[end:]
