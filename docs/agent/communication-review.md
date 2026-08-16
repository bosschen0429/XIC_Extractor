# Communication And Review Surfaces

This file carries the longer communication and review rules that used to live in
root `AGENTS.md`. Keep the root file limited to high-frequency guardrails.

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
  into the PR body instead of treating the current handoff as the final record.
  Do not add a tracked closeout summary or force-add local handoff state.
- `docs/superpowers/handoffs/RETENTION.tsv` is a zero-record compatibility
  sentinel for the read-only audit. Any tracked file under `current/` or
  `archive/` is a placement error; move its durable claim to an owner or PR body
  and keep execution context ignored.
- Status labels such as `[active]`, `[blocked]`, `[done]`, and `[superseded]`
  are useful in open-work sections. Remove `[done]` and `[superseded]` items
  from the active handoff during the next prune unless they prevent repeated
  mistakes.

## PR Evidence Receipt

The PR body is the canonical receipt for the proposed diff at its reviewed
head. Record every decision-bearing claim with these exact columns:

| Claim | Basis | Evidence and binding | Result | What it proves | What it does not prove |
| --- | --- | --- | --- | --- | --- |

- `Basis` is one of `contract`, `observed`, `decision`, or `inference`.
- Repository evidence records `head=<40-character SHA>` plus either
  `command=<exact command>` or `locator=<repo path or public URL>`. External
  evidence records `input`, `config`, `hash`, and a durable `locator` so
  another reviewer can identify the same evidence without a private local
  path.
- Keep a small PR's receipt inline and proportional, normally one to three
  rows. Add only the rows needed to support its decision-bearing claims.
- Science, product-output, or promotion claims link to the existing
  expected-diff, promotion, RAW-validation, and manual-review owners that apply
  to that lane. The receipt does not create a universal evidence manifest or a
  new scientific or product authority.
- The checker verifies completeness, allowed vocabulary, SHA binding, and
  traceability. A reviewer still decides whether the chosen oracle supports the
  claim and whether the stated limitation is accurate.

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
