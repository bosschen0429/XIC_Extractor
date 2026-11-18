# Delivery

How work moves from an Issue to a merged PR.

## Issue

- The Issue says what to do: the outcome, the scope, and how completion is
  checked. Add owners, consumers, migration or rollback detail only when the
  change replaces an owner, migrates persisted data, or changes public behavior.
- A new instruction from the user overrides conflicting Issue text. Update the
  Issue when outcome, scope or acceptance changes and the remote write is
  authorized. Otherwise report the discrepancy without editing GitHub.
- An experimental change is compared with the current formal version. Report
  gains, losses and cost, then adopt or reject it in the Issue.

## Scope

- One Issue, one PR by default. Split only when each part is usable on its own;
  size, file count and review time are not reasons to split.
- Keep unrelated findings out of the PR. Flag a material follow-up; open a
  separate Issue only when authorized.
- Review comments are input, not scope. Fix a comment that shows a real defect
  introduced by the diff or a missed acceptance; otherwise answer it.

## Acceptance Review

Before declaring implementation complete, match every current acceptance
criterion (the Issue plus latest user decisions) to an observed result or an
explicit not-applicable rationale. Missing, failed or unrun required checks
remain incomplete even when CI is green.

During PR review, verify that the cited evidence applies to the reviewed code,
inputs and real entry path, and that actual changes match the
[Expected-Diff Contract](product-validation-contract.md#expected-diff-contract).
Unit tests do not replace RAW, browser or other real-interface validation when
the acceptance criterion requires it.

Keep this reconciliation in the implementation report, PR body or linked
Issue. Reuse applicable retained evidence; do not rerun it merely because the
final change only affects documentation.

## Before Push

- Confirm the branch and what is staged, run the checks the diff affects, and
  report their actual results. `.github/workflows/ci.yml` owns the full command
  set; hosted checks on the final remote SHA decide merge readiness.
- Generated output stays in ignored `output/`. Check a clean checkout only when
  packaging, installation, CI or tracked generated files change.

## PR Body

Use the `pr` skill to write or revise the PR body.

## Closing

An Issue stays open until its acceptance is met. Durable decisions go into the
owning document; the PR keeps the review story.
