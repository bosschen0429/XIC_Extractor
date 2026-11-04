# Delivery

How work moves from an Issue to a merged PR.

## Issue

- The Issue says what to do: the outcome, the scope, and how completion is
  checked. Add owners, consumers, migration or rollback detail only when the
  change replaces an owner, migrates persisted data, or changes public behavior.
- A new instruction from the user overrides conflicting Issue text. Update the
  Issue when outcome, scope or acceptance changes.
- An experimental change is compared with the current formal version. Report
  gains, losses and cost, then adopt or reject it in the Issue.

## Scope

- One Issue, one PR by default. Split only when each part is usable on its own;
  size, file count and review time are not reasons to split.
- Work found along the way that this PR does not need goes to a new Issue.
- Review comments are input, not scope. Fix a comment that shows a real defect
  introduced by the diff or a missed acceptance; otherwise answer it.

## Before Push

- Confirm the branch and what is staged, run the checks the diff affects, and
  report their actual results. `.github/workflows/ci.yml` owns the full command
  set; hosted checks on the final remote SHA decide merge readiness.
- Generated output stays in ignored `output/`. Check a clean checkout only when
  packaging, installation, CI or tracked generated files change.
- RAW and long runs use the runner in [Local Execution](parameter-settings.md);
  `--preflight-only` inspects settings without running. Keep logs under the
  task's output directory and report against the task's acceptance check.

## PR Body

Write for a reviewer who judges intent and behavior without reading every
line, in the user's language and product terms. Use the `pr` skill and the
[PR template](../../.github/pull_request_template.md): what changes and why,
what supports it, and what happens if it is wrong.

- Report the checks actually run and what they confirmed; keep manual
  inspection distinct from execution. Never invent a before/after run or a
  scientific readiness claim.
- Put commands and detailed evidence in a collapsed block or a link.
- Link the Issue instead of repeating it. Leave construction history out.

Status reports lead with the result and keep facts, inference and unknowns
apart.

## Closing

An Issue stays open until its acceptance is met. Durable decisions go into the
owning document; the PR keeps the review story.
