---
name: xic-compound
description: Manual XIC solution-note workflow for reusable lessons from non-obvious fixes, validation patterns, productization decisions, or repeated agent failures.
disable-model-invocation: true
---

# XIC Compound

Capture one reusable XIC learning while context is fresh. This is not a
handoff. A handoff says where the branch is now; a solution note says what a
future agent should know before solving a similar problem again.

## When To Use

Use after:

- a bug fix with a non-obvious root cause;
- a productization lane moved tier or stayed blocked for a clear reason;
- real-data validation changed the next decision;
- a repeated agent failure produced a better workflow rule;
- a narrow diagnostic pattern became reusable.

Do not use for routine commits, one-off status updates, broad manifestos, or
unfinished speculation.

## Workflow

1. Check dirty scope with `git status --short --branch`. Do not fold unrelated
   local edits into the note.
2. Gather only durable evidence: changed files, commands actually run, output
   artifact paths, reviewer findings, and relevant productization/spec entries.
3. Search first:
   ```powershell
   rg -n "<topic>|<module>|<error>|<artifact>" docs\solutions docs\superpowers\notes docs\superpowers\plans
   ```
   Update an existing note if it already covers the same root cause and fix.
4. Write one note under `docs/solutions/<category>/<slug>.md` using
   `docs/solutions/templates/xic-solution-note-template.md`.
5. Keep it short and searchable. Prefer stable reason codes, artifact paths,
   and commands over transcript narrative.
6. If the note affects productization maturity, reference the status index and
   promotion contract instead of duplicating mutable tier state.
7. Run `git diff --check`. For docs-only notes, also manually check YAML
   frontmatter delimiters and unquoted `: ` / ` #` in scalar values.

## Categories

- `productization`: tier moves, writer authority, expected-diff, lane blockers.
- `validation`: 8RAW/85RAW/no-RAW replay, oracle design, artifact parity.
- `workflow`: agent process, subagent review, handoff, tool routing.
- `architecture`: ownership boundaries, public contracts, refactor lessons.
- `testing`: focused tests, fixtures, CI gate behavior.
- `bugs`: concrete regressions and root-cause fixes.

## Quality Bar

A useful note lets a future agent answer:

- What symptom or decision should make me read this?
- What was the mistaken path?
- What rule or implementation pattern worked?
- What evidence proved it?
- What should I do first next time?

Common failure: writing a second handoff. If the document mostly says "current
status" instead of "reusable lesson", put continuation state in the handoff and
product state in the status index instead.
