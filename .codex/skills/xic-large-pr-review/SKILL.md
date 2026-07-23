---
name: xic-large-pr-review
description: Review large or repeatedly re-reviewed XIC PRs, especially diagnostics, architecture, public contracts, packaging/runtime, RAW/evidence, performance, activation, Matrix, or review-churn changes.
---

# XIC Large PR Review

Review for **closure**, not for a plausible sample of defects. A review is
complete only when every changed file belongs to a reviewed semantic cluster,
every material claim has an oracle at the correct layer, and every discovered
failure class has a bounded universe rather than a growing list of examples.

This skill is review-only. Return findings and a verdict. Mutation, thread
replies, resolution, push, or merge require separate user authorization.

## 1. Freeze The Review Object

Start read-only and record the exact object being reviewed:

```powershell
git status --short --branch
gh pr view --json number,title,state,isDraft,baseRefName,headRefName,headRefOid,mergeable,commits,statusCheckRollup,url
$base = gh pr view --json baseRefName --jq ".baseRefName"
git merge-base "origin/$base" HEAD
git diff --stat "origin/$base...HEAD"
git diff --name-status "origin/$base...HEAD"
git log --oneline "origin/$base..HEAD"
```

Account for dirty files, renames, deletions, generated files, lockfiles, and
stacked ancestry. If the apparent PR includes unrelated ancestors, report the
base problem before reviewing implementation details.

Completion criterion: the base, merge base, local HEAD, remote PR head, dirty
scope, CI head, and complete changed-file set are known.

## 2. Build The Review-Closure Ledger

Cluster the diff by responsibility, not directory. Keep a transient ledger:

| Field | Required content |
| --- | --- |
| `cluster` | One changed behavior or responsibility |
| `files` | Every changed file assigned to this cluster |
| `semantic_owner` | The implementation, contract, or external interpreter that actually owns the behavior |
| `consumers` | Live callers, entry points, writers/readers, CI, GUI/CLI, or downstream artifacts affected |
| `claims` | Behavior, compatibility, evidence, performance, or product statements made by the PR |
| `claim_layer` | `checkout_static`, `source_runtime`, `installed_package`, `runtime_cli`, `frozen_app`, `domain_evidence`, `public_projection`, or an explicit equivalent |
| `failure_classes` | Mechanisms capable of violating the claims |
| `oracle` | Test, artifact, parity check, or manual evidence at the same claim layer |
| `state` | `unreviewed`, `reviewed`, `finding`, `verified`, `deferred`, or `inconclusive` |

Mechanical or generated changes still need an owner and synchronization oracle.
Representative sampling is valid only after the full set is enumerated and
shown to share one implementation and contract.

Keep accounting exhaustive and inspection depth risk-ranked. Deepen shared
owners, public/product claims, cross-layer behavior, weak oracles, and expensive
failure modes first; keep proven mechanical-equivalence clusters lightweight.

Completion criterion: every changed file is assigned exactly once as a primary
member, cross-cluster effects are linked, and no material claim lacks an owner
or oracle.

## 3. Review Each Cluster Mechanism-First

For every cluster:

1. Review requirement/claim fit independently from implementation quality.
2. Read the semantic owner before judging adapters or tests.
3. Trace changed and live consumers in both directions: caller to effect, and
   artifact/schema back to producer.
4. Compare base behavior with final-head behavior, including removed behavior
   and defaults not mentioned in the PR body.
5. Write one concrete counterexample that could make the implementation wrong
   while its current happy-path tests still pass.
6. Inspect the oracle as production code: its inputs, environment, independence,
   negative path, and ability to go red.
7. Record the result in the ledger before moving to the next cluster.

Always examine these six dimensions:

- authority and dependency direction;
- public and persisted contracts;
- absent, malformed, duplicate, reordered, partial, fallback, and exception
  states;
- test/oracle integrity;
- checkout, installed, runtime, platform, and artifact-layer differences;
- diagnostic evidence versus product behavior or scientific authority.

After tagging the ledger, open
[`references/review-lenses.md`](references/review-lenses.md) and apply every
conditional lens whose trigger matches a cluster. Do not load unrelated lenses.

Completion criterion: each cluster has a base-to-head judgment, a counterexample,
the applicable lenses, and a supported state.

## 4. Census Every Finding Class

A review comment is a seed, never the universe. At the first finding, before
proposing a fix, read and apply
[`references/finding-class-census.md`](references/finding-class-census.md).
The finding branch owns class predicates, authoritative enumeration, repeated-
finding freeze, claim/oracle alignment, and thread-resolution gates.

If a hosted finding reveals a changed file, claim layer, consumer, or failure
mechanism absent from the ledger, reopen the whole review; the ledger—not only
that finding class—was incomplete.

Completion criterion: every finding has an explicit universe and class oracle;
no fix is called a root fix from reviewer examples alone.

## 5. Audit Oracle Integrity

Passing tests are evidence only when they can detect the claimed failure.
Check:

- a pre-fix RED reproduction or a grounded counterexample;
- correct artifact and claim layer;
- independence from the implementation being checked;
- assertion weakening, removed coverage, new skips, and reduced fixture
  representativeness relative to the base;
- clean-checkout behavior, path casing, ignored/untracked/deleted paths, working
  directory, editable-install leakage, optional dependencies, and platform;
- negative, crossed, and stale-exception behavior;
- deterministic identity/cardinality where rows, maps, joins, caches, or
  artifacts are involved;
- exact local and hosted CI commit identity.

For every input-to-map, input-to-group, or cross-artifact join, state the
normalized key and cardinality (`one-to-one`, `one-to-many`, or `many-to-many`).
Exercise blank and duplicate keys, matching and conflicting duplicates, reversed
order, and partial input.

Completion criterion: every material claim has an oracle that can go red at the
same layer, or the claim is explicitly deferred/inconclusive.

## 6. Fresh Final-Head Pass

After fixes, review the complete merge-base-to-final-head diff again, not only
the newest patch:

1. Regenerate the changed-file set and review-closure ledger.
2. Re-run every open class census and applicable conditional lens.
3. Inspect PR body, docs, config, tests, generated artifacts, and lockfiles for
   stale claims.
4. Confirm CI belongs to the final remote SHA.
5. Query thread-aware review state and require zero unresolved actionable
   threads before a merge-ready verdict.

Completion criterion: every ledger row is `verified`, `deferred`, or
`inconclusive`; no row is `unreviewed`; every deferment and residual risk is
reader-visible.

## Evidence Labels

State the strongest evidence actually reviewed:

- `synthetic_only`
- `focused_tests`
- `ci_green`
- `8RAW_parity`
- `85RAW_parity`
- `targeted_benchmark`
- `manual_eic_ms2_review`
- `diagnostic_only`
- `inconclusive`

Do not promote one evidence tier into another. In particular, CI, 8RAW, reports,
sidecars, and diagnostics do not independently grant production, Matrix,
selected/count, activation, export, or scientific authority.

## Output

Lead with findings:

```markdown
**Findings**

- [P1/P2/P3] <mechanism> — <file:line>
  <impact, affected universe, owner, and missing or failing oracle>

**Verdict**

<merge-ready, changes required, deferred, or inconclusive>

**Review Coverage**

<clusters reviewed, conditional lenses used, class census counts, and any gaps>

**Verification Reviewed**

<exact SHA, CI/local gates, parity evidence, and strongest evidence label>

**Residual Risk**

<unproven layers, deferred claims, skipped evidence tiers, or unresolved scope>
```

No findings is a valid result only when the closure ledger has no `unreviewed`
row and every material claim has a same-layer oracle.

## Stop Rules

Return `inconclusive` rather than a merge-ready verdict when:

- base, remote head, dirty scope, or complete diff cannot be established;
- a changed file or material claim remains outside the closure ledger;
- a failure-class universe cannot be bounded;
- claim and oracle layers do not match;
- a repeated finding class remains frozen or unknown;
- CI/thread state cannot be tied to the final remote head;
- the diff is too large to build a defensible responsibility map.

For subagent review, follow `docs/agent/subagent-routing.md` only when the user
explicitly requests subagents or runtime policy authorizes them.
