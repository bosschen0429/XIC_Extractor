### Goal

Repository implementation authors and reviewers can decide whether one delivery closes its ticket without reconstructing the finish line from issue history.

### Execution binding

0123456789abcdef0123456789abcdef01234567

### Delivery graph placement and handoff

Independent repository-process issue; no parent, blocker, predecessor handoff, or successor handoff.

### Semantic owner and finite production universe

Owner: docs/agent/planning-workflows.md. Consumers: the implementation issue form, PR template, repository validator, and planning-contract tests. Product and scientific code are excluded.

### Owner-local asset disposition

Adapt the owner and both templates; retire the prior completion wording after all named consumers use the executable contract.

### Scope ceiling and non-goals

Only repository delivery planning is in scope. Product behavior, scientific authority, schemas, writers, and external workflow policy are non-goals.

### Executable acceptance contract

- Actor: implementation author
- Input: a repository implementation issue body
- Real entry path: python -m scripts.check_agent_delivery_contract --kind issue --body-file <path>
- Observable: the command exit status and specific diagnostics
- Expected result: complete Atomic contracts return zero and incomplete contracts return non-zero
- Stable oracle: tracked positive and negative Markdown fixtures
- Falsifying counterexample: a Goal with no independently decisive Acceptance returns zero
- Failure semantics: invalid contracts fail closed without changing GitHub state
- Cancellation semantics: interrupted validation writes no repository or GitHub state
- Partial-write semantics: not applicable - validation is read-only and writes no contract state
- Preserved invariants: planning-workflows remains the sole semantic owner
- Non-goals: the validator does not grant product, scientific, or merge authority
- Rollback: revert the owner, templates, validator, fixtures, and tests together
- Close rule: close only after every declared oracle passes on one reviewed head

### Atomic delivery boundary

- Delivery plan: one-ticket-one-atomic-pr

#### Proposed PR unit: whole-ticket

- Bounded outcome: publish and enforce the complete repository delivery contract
- Current consumer: implementation authors and PR reviewers using repository templates now
- Independently decisive oracle: both offline fixture modes and the focused planning-contract suite pass
- Independently useful: yes
- Independently reviewable: yes
- Independently testable: yes
- Independently mergeable: yes
- Independently reversible: yes
- Later sibling required: no
- Split rationale: not applicable - this one Atomic PR closes the ticket
- Replaced surface disposition: Retire: prior non-executable completion wording after the validator and templates land together
- Rollback: revert this Atomic PR

### Local resolution and parent escalation

Fail closed on a missing declared field. Stop only for a current contradiction requiring a second semantic owner or external authority.
