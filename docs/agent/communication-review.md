# Communication And Review Surfaces

This file carries the longer communication and review rules that used to live in
root `AGENTS.md`. Keep the root file limited to high-frequency guardrails.

## STE-like Engineering Language

Use STE-like controlled language for issue bodies, specs, plans, agent
contracts, review comments, PR bodies, handoffs, and operator instructions.
This is a repository writing contract, not a claim of ASD-STE100 certification
or use of its complete approved dictionary.

- Put one decision, condition, or action in each sentence. Use a direct verb and
  active voice. Use the imperative form for required actions.
- Name the actor or semantic owner when responsibility affects the result. Name
  the input, condition, threshold, and observable output when they affect
  acceptance.
- Replace words such as `best`, `appropriate`, `usually`, `intelligently`,
  `robust`, or `as needed` with the named rule that decides the outcome. If no
  rule exists, state the unresolved decision instead of making the sentence
  sound complete.
- Use the exact stable terms in
  [`domain-glossary.md`](../product/domain-glossary.md). One term has one
  meaning. One meaning has one canonical term. When a stable concept is absent,
  define it at its product or architecture owner before using it as an
  implementation requirement.
- Keep code identifiers, schema fields, issue language, and review language
  aligned with the canonical term when they refer to the same concept. State an
  explicit mapping when a public or legacy name must remain different.
- Make review findings executable. State the observed behavior, the violated
  criterion or owner, the required outcome, and the evidence that will prove
  closure. A label such as `confusing`, `fragile`, or `not clean` is not a
  complete finding.

The writing passes this contract when each instruction maps to one action or
decision, each condition is testable or explicitly unresolved, and each domain
term maps to one canonical meaning and owner.

## Verdict-First Communication

- Open every non-trivial report with 2-4 sentences written for a reader who did
  not see this work: what now works, whether it is safe to use/write/ship, and
  the next step. This opener must contain no artifact filenames, `field=value`
  pairs, or test node ids — if the reader would have to open a file to understand
  a sentence, rewrite it.
- Explain by meaning, not by internal label. Bad: `feature_source_detected tier
  B score 70`. Good: "locally detected features are now supporting evidence only
  and can no longer promote themselves to a high-confidence source."
- Command output, RED/GREEN history, and per-item self-review are evidence: put
  them after the verdict or omit them. A report is not a process log.
- When relaying a subagent's slice report, translate it into the plain-language
  verdict above; do not paste its field contract verbatim.
- During long, tool-heavy work, explain what question the current tools are
  answering and how the result changes the next action.
- Final answers for implementation or validation work should include, when
  applicable: conclusion, changed files or artifact paths, verification run,
  remaining risk, and next action.

## Human Review Surfaces

- Separate machine artifacts from human review surfaces. TSV/JSON may be
  exhaustive; Markdown specs are mostly for agents; human-facing reports should
  be short, visual or indexed, and decision-oriented.
- Use [`.github/pr_comment_template.md`](../../.github/pr_comment_template.md)
  for non-trivial inline review replies and top-level review updates. Every
  reply must separate the reviewed instance, the recurring finding class, and
  the overall PR outcome; a commit hash or “fixed” alone is not closure.
- Manual review requests should include a compact review index with identifiers:
  sample, label or family id, m/z, RT/window, status, reason, and linked
  artifact path.
- Worktree or PR closeout should leave an operator-readable handoff: branch/task
  purpose, verdict, important artifacts, validation commands/results, active
  blockers, rejected paths still likely to recur, and an explicit next-step
  recommendation. Keep it as current state, not a chronological log.
- Handoffs are branch-scoped. The default active handoff is the ignored local
  file `docs/superpowers/handoffs/current/ACTIVE.local.md`; use a branch-named
  ignored local file only when multiple branches need simultaneous state.
  Before editing a repo-tracked handoff, verify the file name and its `Branch:`
  / `Status:` match the current branch or PR workflow.
- Active handoffs should stay short enough to read every time, normally under
  about 200 lines. Completed phase summaries belong in the PR body by default;
  force-add repo archive summaries only when they are intentionally public
  evidence. Long logs, stack traces, and scratch analysis belong in Obsidian
  only when still useful.
- When long context is moved to Obsidian, the active local handoff remains a
  self-sufficient stub. Obsidian links are optional deep context, not required
  for understanding the next safe action.
- Treat repo handoffs and PR bodies as public-facing summaries. They may state
  the approved decision, validation actually run, residual risk, and next
  action, but they must not paste private Obsidian reasoning, raw command
  transcripts, local absolute paths, sample-level investigation detail, or
  private data placement back into the repo.
- PR body is the normal durable closeout surface. Condense the branch handoff
  into the PR body instead of treating the current handoff as the final record;
  add compact completed phase summaries under `docs/superpowers/closeouts/`
  only when they must remain in repo.
- Handoff retention is inventory-driven only for git-tracked handoff files.
  Ignored local handoffs do not need `RETENTION.tsv` rows. Any force-added file
  under `docs/superpowers/handoffs/current/` or
  `docs/superpowers/handoffs/archive/` must also be recorded in
  `docs/superpowers/handoffs/RETENTION.tsv`. Use
  `tools/diagnostics/handoff_retention_audit.py` before PR closeout only when
  tracked handoff files changed. The audit is not deletion approval.
- Status labels such as `[active]`, `[blocked]`, `[done]`, and `[superseded]`
  are useful in open-work sections. Remove `[done]` and `[superseded]` items
  from the active handoff during the next prune unless they prevent repeated
  mistakes.

## Review Checklist

After completing changes, quickly check:

- requirement fit;
- public contract drift;
- GUI / CLI / API behavior divergence;
- regression risk;
- missing tests;
- security issues;
- over-complexity;
- generated artifacts, marker maps, lockfiles, or docs that should be synced.

When reviewing progress or a checklist, distinguish infrastructure existence
from usable product behavior. Reports, wrappers, sidecars, and audit artifacts
prove observability; they do not prove production behavior.
