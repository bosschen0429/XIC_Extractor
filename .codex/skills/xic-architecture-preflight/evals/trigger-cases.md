# Trigger Cases

These cases document the existing `xic-architecture-preflight` route. They do
not widen or narrow the skill trigger.

## Should Trigger

- "Change matrix extraction behavior, then run 85RAW validation." This exact
  prompt is exercised by `.codex/hooks/fixtures/assert_hook_outputs.py`; root
  `AGENTS.md` requires the architecture preflight for the Matrix behavior edit
  before the separate RAW gate.
- "Optimize matrix extraction performance, then run 85RAW validation." This
  exact prompt is exercised by `.codex/hooks/fixtures/assert_hook_outputs.py`.
  `docs/agent/planning-workflows.md#skills-and-subagents` requires the skill for
  preset-performance changes, and root `AGENTS.md` requires it after
  `xic-performance-diagnosis` and before code edits.
- "Implement immutable manifest fingerprint reuse and a manifest-bound atomic
  publish bundle." This is the delivered Issue #213 / PR #215 boundary task.
  The skill description requires preflight when crossing an evidence/runtime
  boundary or changing a public contract; the skill must bind the manifest
  owner, call-cost, public contract, and failure oracle before edits.

## Should Not Trigger

- "Correct wording in the no-RAW pytest documentation without changing runner
  behavior." This is an ordinary docs fix, explicitly excluded by the skill
  description; use the documentation owner and focused docs checks.
- "Fix one focused unit-test assertion inside an established serializer owner."
  This is a focused fix inside an established owner, explicitly excluded by the
  skill description; use the ordinary implementation/test workflow.
- "Summarize the unresolved review comments on this PR." PR comment handling is
  explicitly excluded by the skill description; use the GitHub comment or
  review workflow.
- "Explain the current Matrix ownership without changing it." Read-only
  explanation is explicitly excluded by the skill description; answer from the
  current architecture owners.

## Boundary Case

- "Add a CLI that only renders an existing typed diagnostic result." Decision:
  trigger when the CLI or persisted output is new or behaviorally changed,
  because the skill must bind the public contract and boundary projection. Do
  not trigger for wording-only help text or docs about an unchanged CLI; those
  remain ordinary docs work. This follows the public-contract gate in
  `docs/agent/subagent-routing.md#hard-triggers` without turning all CLI wording
  into architecture work.
