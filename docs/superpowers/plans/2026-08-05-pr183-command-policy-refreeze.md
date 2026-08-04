# PR #183 Command Policy Refreeze

Doc placement: repo_support_doc
Doc kind: plan
Doc lifecycle: active
Repo owner: docs/agent/codex-operating-system.md
Doc exit rule: Absorb the final bounded command-policy contract into the repo
owner, record `pass_can_retire`, then retire this plan through the documented
docs lifecycle.

Status: frozen for implementation at `d08fdd753d550883528c8b9a4e43825652e475c4`

## Problem And Observable Loss

PR #183 introduced a custom scanner that attempts to recognize Git invocations
inside Bash, PowerShell, cmd, `env -S`, wrappers, compound statements, and
control blocks. Five review threads then found successive grammar gaps. The
observable losses are repeated patch/review cycles and command shapes that can
bypass the intended Git prompt or destructive-command guard.

Success means the repository no longer claims to interpret arbitrary shell
grammar, every repository-owned Git command shape has one deterministic policy
outcome, and the existing direct Git prompt paths remain usable.

## Semantic Owner And Interface

`tools/command_policy.py` owns one pure classification interface for a raw
command string:

- one bounded, direct Git invocation with subcommand, tail, and global options;
- an explicit nested shell/command wrapper that must be rewritten without
  inspecting its payload;
- an ambiguous Git-bearing command that must be rewritten; or
- no command shape owned by this policy.

The project execpolicy remains the only prompt owner. `PreToolUse` may deny an
ambiguous command but cannot mint a prompt decision. The sandbox doctor renders
the same ambiguity as `REWRITE_COMMAND`.

## Invariants

1. Direct `git push` reaches the existing `git push` prompt rule.
2. Direct `git -c ... push` reaches the existing `git -c` prompt rule.
3. Direct read-only optioned Git commands such as `git -C <repo> status` remain
   usable without a hook denial.
4. Every owned network Git subcommand (`clone`, `fetch`, `ls-remote`, `pull`,
   `push`) with a global-option prefix that execpolicy cannot prompt is a
   rewrite in both the hook and doctor.
5. Explicit nested shell/command wrappers found at the bounded outer executable
   position are denied before payload inspection, including after a POSIX
   assignment prefix or initial call operator.
   Compound, assignment-RHS, path-qualified, or otherwise unsupported
   Git-bearing commands are ambiguous and denied before execution.
6. Executable aliases plus malformed or unsupported direct Git global-option
   grammar are ambiguous; they are never guessed into an execpolicy prefix or
   subcommand.
   Git subcommands must use canonical lowercase spelling because execpolicy
   prefixes are exact; wrapper words outside the bounded outer executable
   position remain unowned data unless the command is Git-bearing. PowerShell
   assignment RHS, later compound segments, shell control words, and dynamic
   executable expressions are not interpreted by this locator.
7. Direct destructive prefixes continue to be owned by execpolicy. Any
   ambiguous or optioned form that cannot reach that exact prefix is denied by
   the hook.
8. Hook policy and doctor consume the same classification result; neither
   re-parses shell text.
9. Quoted Git text is inert only for a bounded top-level search/output consumer
   that cannot spawn another process, and never when shell command-expansion
   syntax is present.
10. The separate RAW hard guard may inspect its own domain signature across a
    non-inert lexical segment: `Start-Process` plus a RAW launch signal or a
    propagated RAW argument variable. It does not make command policy a wrapper
    payload parser, and unrelated signals in separate segments do not combine.
    An inert text command is exempt only when it has no evaluated subexpression,
    command substitution, or unquoted control structure.

## Forbidden Strategies

- No shell control-word lists, recursive wrapper descent, or emulation of
  Bash, PowerShell, cmd, WSL, `env`, `command`, or `exec` grammar.
- No new special case for an individual review example.
- No silent allow after a tokenization failure or unsupported Git option.
- No claim that repo hooks enforce behavior hidden inside arbitrary Python,
  shell eval, aliases, or other process-launching programs.

## Scope And Transactions

This transaction may change only the command-policy module, its two callers,
the commit-policy helper that previously reclassified the same text, focused
tests, and the two execution-contract documents that describe the observable
behavior. Live host probes, hook retirement, skill retirement, line-ending
policy, and unrelated PR #183 changes are separate transactions.

## Counterexamples And Oracle

The conformance oracle crosses command shape with expected outcome:

| Shape | Representative command | Expected outcome |
| --- | --- | --- |
| direct/plain | `git push origin HEAD` | hook silent; execpolicy prompt |
| direct/config | `git -c safe.directory=C:/repo push` | hook silent; execpolicy prompt |
| direct/read-only option | `git -C C:/repo status --short` | hook silent |
| direct/network option | `git -C C:/repo push` | hook deny; doctor rewrite |
| direct/unsupported option | `git --unknown value push` | hook deny; doctor rewrite |
| executable alias | `git.exe`/`git.cmd`/`git.bat`/`git.ps1`/`git.com`/`git.sh` | hook deny; doctor rewrite |
| quoted executable | `"git" push origin HEAD` | hook deny; doctor rewrite |
| noncanonical subcommand | `git PUSH origin HEAD` | hook deny; doctor rewrite |
| nested shell | `cmd /c if exist .git git -C C:/repo push` | hook deny; doctor rewrite |
| nested shell without Git | `bash -lc "echo ready"` | hook deny; doctor rewrite |
| control block | `pwsh -Command "if ($true) { git push }"` | hook deny; doctor rewrite |
| assignment RHS | `$result = git -C C:/repo push` | hook deny; doctor rewrite |
| prefixed wrapper | `FOO=1 bash -lc "git push"` | hook deny; doctor rewrite |
| prefixed wrapper without Git | `& bash -lc "echo ready"` | hook deny; doctor rewrite |
| typed/member call assignment without Git | `[string]$runner = & "bash" -lc "echo ready"` | hook silent; doctor ready |
| wrapper word as data | `python env\\script.py` | hook silent; doctor ready |
| wrapper option value | `python tool.py --shell=bash` | hook silent; doctor ready |
| POSIX assignment value | `SHELL=bash python tool.py` | hook silent; doctor ready |
| call-operator option value | `& python tool.py --shell=bash` | hook silent; doctor ready |
| shell comparison data | `[ $x = bash ]` | hook silent; doctor ready |
| quoted assignment data | `$runner = "bash"` | hook silent; doctor ready |
| quoted separator data | `python tool.py ";" bash` | hook silent; doctor ready |
| later compound wrapper without Git | `true; 2>&1 bash -lc "echo"` | hook silent; doctor ready |
| redirection-prefixed wrapper without Git | `>trace.log bash -lc "echo"` | hook silent; doctor ready |
| redirection | `git push>output.log` | hook deny; doctor rewrite |
| command substitution | `Write-Output "$(git push)"` | hook deny; doctor rewrite |
| spawning search mode | `rg --pre "git" PATTERN push` | hook deny; doctor rewrite |
| top-level quoted search text | `Select-String -Pattern "git push" -Path README.md` | hook silent |

A newly discovered shell construct must already land in the ambiguous outcome.
If it instead requires another grammar branch, this design fails and must be
replaced rather than patched.

## Verification And Rollback

Focused hook and doctor tests must cover every row above plus all prior review
regressions. Run the execution-config smoke/scan contract, `git diff --check`,
and a fresh fixed-diff review. Rollback is one local commit revert; no data or
schema migration is involved.
