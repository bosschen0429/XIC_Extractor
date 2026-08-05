---
title: "Codex Environment Noise Triage"
date: "2026-06-24"
category: "workflow"
module: "agent-environment"
status: "current"
tags: ["codex", "git-lock", "hooks", "environment-noise"]
source_refs:
  - ".codex/hooks/xic_post_tool_guard.py"
  - ".codex/hooks/fixtures/assert_hook_outputs.py"
  - "docs/agent/execution-gates.md"
  - "docs/agent/parameter-settings.md"
---

# Codex Environment Noise Triage

## When To Read

Read this when PR repair, CI work, or validation is slowed down by repeated
Codex/environment warnings: Git lock/ref-lock friction, stuck read-only Git
queries, GitHub Actions not starting after a ref update, or hook warnings that
do not match the command's real failure mode.

## Problem

Environment noise can make an already hard PR stack look worse than it is. In
the historical closeout behind this note, stale read-only `git.exe` queries and
an over-broad hook warning both produced repeated lock/ref-lock noise. The
lock/ref warning was retired. The current post hook does not diagnose Git; it
only rejects pytest runs that collect zero tests or use an invalid node id.

## Tempting Wrong Path

Do not treat every environment symptom as code failure. Do not keep retrying the
same Git operation while a stale process or lock may exist. Do not infer Git
state from the current post hook because it has no Git lock/ref owner.

## Working Pattern

Separate these cases before changing code:

- actual Git lock failure: a Git command reports an error such as inability to
  create `.git/index.lock` or lock a ref;
- stuck Git metadata query: read-only `git status`, `rev-parse`, `remote -v`,
  or config query remains alive long after it should have exited;
- historical hook false positive: command output merely displayed lock-related
  text from docs, diffs, or source files. No current post-hook warning should be
  expected for this case.

For actual lock/process friction, use the failing Git command's output, then
follow the Git/runtime guidance in `docs/agent/parameter-settings.md`. Inspect
the exact lock target and live `git.exe` processes before requesting any
destructive cleanup. Searching docs or showing a diff is not Git failure
evidence.

## Evidence

- Commands actually run:
  hook fixture smoke, ruff on hook files, `git diff --check`, and diff
  secret/local-path scan.
- Code: `xic_post_tool_guard.py` only matches pytest zero-test and invalid-node
  output. It contains no Git lock/ref matching or warning path.
- Tests: `assert_hook_outputs.py` exercises the current zero-test post-hook
  contract. Git lock/ref diagnosis comes from actual Git command output, not a
  synthetic post-hook classification.
- Historical state: the over-broad lock/ref post-hook warning was retired; its
  old search/diff fixtures are not current behavior claims.

## Limits

This does not prevent external Git integrations from spawning short-lived Git
queries, and it does not automatically classify or repair Git locks. It records
the manual evidence path; destructive lock removal or process termination still
requires exact-target inspection and the applicable runtime authority.

## Next Time

1. If a Git command reports a lock/ref failure, preserve its exact command,
   cwd, shell, exit code, and error before inspecting the exact lock target and
   live `git.exe` processes.
2. If only a docs/source search or diff contains lock text, do not classify it
   as a Git failure; the current post hook should remain silent.
3. For post-hook changes, keep the owner limited to zero/invalid pytest and
   rerun hook fixtures, Ruff, diff check, and secret/local-path scan.
