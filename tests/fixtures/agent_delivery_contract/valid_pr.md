## Outcome

Repository delivery contracts now have a deterministic Atomic closure check.

## Problem and observable loss

Reviewers otherwise reconstruct acceptance and split validity from issue history.

## What changed

The owner, projections, validator, and behavioral fixtures now ship together.

## Scope and contracts

- Lane: workflow-only
- Product output change: no
- Public contracts at risk: none
- Execution base: 0123456789abcdef0123456789abcdef01234567
- Semantic owner: docs/agent/planning-workflows.md
- Finite production universe: implementation issues and PR bodies in Chao-hu-Lab/XIC_Extractor
- Scope delta from issue: None; the PR matches the issue.

## Ticket-to-PR closure receipt

- Issue identity: Chao-hu-Lab/XIC_Extractor#368
- Ticket delivery: closes-entire-ticket
- Bounded outcome: enforce the complete Atomic ticket-to-PR contract
- Current consumer: implementation authors and PR reviewers using repository templates now
- Independently decisive oracle: focused delivery-contract fixtures and planning-contract tests pass
- Independently useful: yes
- Independently reviewable: yes
- Independently testable: yes
- Independently mergeable: yes
- Independently reversible: yes
- Later sibling required: no
- Split rationale: not applicable - this PR closes the entire ticket
- Replaced surface disposition: Retire: prior non-executable template wording after this validator lands
- Rollback: revert this Atomic PR

## Review guide

Review the owner, then the projections, validator failure semantics, and fixtures.

## Verification

- Reviewed head: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
- Validation evidence status: diagnostic_only

| Claim | Basis | Evidence and binding | Result | What it proves | What it does not prove |
| --- | --- | --- | --- | --- | --- |
| Atomic contract is executable | contract | head=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; command=.venv/Scripts/python.exe -m pytest tests/test_agent_delivery_contract.py -q | pass | declared invalid contracts fail | scientific readiness |

## Residual risk and rollback

- Residual risk: exact canonical headings require issue authors to use the repository template.
- Rollback/stop: revert this PR if valid canonical contracts are rejected.

## Issue and delivery linkage

- Issue linkage: Closes #368
- Acceptance closed: all issue acceptance criteria close on this head.

## Authority Check

- [x] This PR does not grant ProductWriter or matrix/workbook authority from diagnostics, sidecars, adjudication, review packets, or lockbox labels unless an explicit contract and expected-diff are included.
- [x] Unrelated dirty files were not reverted or rewritten.
