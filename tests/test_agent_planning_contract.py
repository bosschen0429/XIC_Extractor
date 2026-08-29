import re
from pathlib import Path

import pytest


def _section(document: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^### {re.escape(heading)}\n(?P<body>.*?)(?=^## |^### |\Z)",
        document,
    )
    assert match is not None, f"missing planning section: {heading}"
    return match.group("body")


def test_wayfinder_decisions_compile_into_one_delivery_graph_contract() -> None:
    workflow = Path("docs/agent/planning-workflows.md").read_text(encoding="utf-8")
    tracker = Path("docs/agent/issue-tracker.md").read_text(encoding="utf-8")
    contract = _section(workflow, "Wayfinder-To-Delivery Compilation")
    normalized = contract.casefold()

    assert "decision map" in normalized
    assert "same parent issue" in normalized
    assert "does not reopen" in normalized
    assert "does not authorize a production diff" in normalized
    assert "exactly one carrier issue" in normalized
    assert (
        "native dependencies are the schedule gate, not handoff semantics"
        in normalized
    )
    assert "scope ceiling and stop authority" in normalized
    assert "main may block" in normalized
    assert "review comment itself has no authority" in normalized
    assert "an independent issue states `independent`" in workflow.casefold()
    assert "reviewer suggestion alone" in workflow.casefold()
    assert "implementation delivery" in tracker
    assert "excluded from the wayfinder frontier" in tracker.casefold()


def test_implementation_issue_template_exposes_node_edge_and_finish_line() -> None:
    template = Path(
        ".github/ISSUE_TEMPLATE/implementation_change.yml"
    ).read_text(encoding="utf-8")
    normalized = " ".join(template.split())

    assert "id: graph_placement" in normalized
    assert "provisional" in normalized
    assert "typed predecessor outputs" in normalized
    assert "exactly one carrier issue" in normalized
    assert "use `independent` when no edge exists" in normalized
    assert "label: Ticket completion contract" in normalized
    assert "not required for closure" in normalized
    assert "close or hand off" in normalized
    assert "reachable wrong output cannot close ticket-locally" in normalized


@pytest.mark.parametrize(
    ("incident", "route"),
    (
        (
            "stale execution binding after predecessor delivery",
            "automatic ticket-local reconciliation",
        ),
        (
            "unproved local ambiguity without a constructible wrong output",
            "bounded probe or fail closed",
        ),
        (
            "constructible parent contradiction: reachable wrong output cannot "
            "close without changing a frozen parent decision",
            "refreeze the smallest parent decision",
        ),
    ),
)
def test_map_ticket_regressions_have_bounded_routes(
    incident: str,
    route: str,
) -> None:
    workflow = Path("docs/agent/planning-workflows.md").read_text(encoding="utf-8")
    contract = _section(workflow, "Wayfinder-To-Delivery Compilation")

    assert f"| {incident} | {route} |" in contract
