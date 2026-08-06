# Pull Request Comment Template

Use these templates for non-trivial PR review responses. Keep the reply short,
but do not replace evidence with “fixed”, a test count, or a commit hash alone.

## Inline review reply

```markdown
**Disposition:** fixed / partially fixed / deferred / unsupported / duplicate

**Observed failure:**
<!-- Restate the concrete behavior or contract violation. -->

**Root cause / class:**
<!-- Name the responsible owner or boundary. Include the finding-class ID when known. -->

**Change:**
<!-- Commit SHA plus the behavioral change; do not provide only the SHA. -->

**Oracle and evidence:**
<!-- Test/check/artifact, exact result, and the remote SHA that contains it. -->

**Closure:**
<!--
State separately:
- this review instance: open / closed;
- recurring class: open / closed / not applicable;
- PR outcome: unchanged / changed, with the remaining blocker if any.
Do not infer class closure from one repaired instance.
-->

**Follow-up:**
<!-- Issue/PR owner, or “none”. -->
```

### Compact fixed example

```markdown
**Disposition:** fixed in `abc1234`.

**Observed failure:** The new test was absent from every hosted test shard, so
the CI shard census failed before tests ran.

**Root cause / class:** CI registration omission; implementation defect, not a
product or Class A contract change.

**Change:** Registered the exact test path in `alignment-core`.

**Oracle and evidence:** `python -m tools.testing.test_shards --check` reports
no unassigned files; the shard contract plus focused test suite pass at
`abc1234`.

**Closure:** This review instance is closed. Global Class A and the PR's
product-readiness claim are unchanged.

**Follow-up:** none.
```

## Top-level review update

Use this after several threads, a force-push/rebuild, or a material change in
the reviewable story.

```markdown
## Review update — `<remote SHA>`

### Outcome
<!-- What is now true, whether the PR is reviewable, and the remaining blocker. -->

### Finding disposition

| Finding/thread | Disposition | Root cause/class | Commit | Oracle | Class status |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |

### Verification

| Check/evidence | Result | What it proves |
| --- | --- | --- |
|  |  |  |

### Remaining risk and follow-up

1. <!-- Maximum three material items, each with an owner or issue. -->
```

## Resolution rules

- Reply only after the change exists on the named remote SHA.
- Resolve a thread only when its observed failure and oracle are both closed.
- Keep `instance`, `class`, and `overall PR outcome` as separate statements.
- If the plan gained the missing rule only after review found it, call that a
  corrective refreeze; do not present it as proof that the original plan was
  complete.
- Route an out-of-scope but valid finding to a named follow-up before resolving
  it. “Not in this PR” without an owner is not a disposition.
- Do not paste raw logs, private paths, secrets, or private data into comments.
