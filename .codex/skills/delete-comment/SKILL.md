---
name: delete-comment
description: Audit and retire source comments in XIC Extractor, and preserve decision rationale.
---

# Delete comments

Run from the repository root with this checkout’s Python. Use the requested scope;
without a path, inventory the entire tracked codebase. Stage new files before measuring.

1. Run `python scripts/list_comments.py` (JSONL: path, line, comment).
   Read the containing code and consumer before deciding. Group continuations, not
   different decisions. Strings, docstrings, shipped user instructions, legal notices
   and machine directives are not prose to delete.
2. Record the original revision, path, line, classification and disposition in
   ignored `output/comment-audit/`. **Tautology** repeats visible code; **Stale**
   describes a superseded implementation. Delete both. **Excuse** excuses a
   concrete workaround: link an existing open issue or, if authorized, file one
   with the cost and an observable exit criterion, then remove the comment.
   If filing is not authorized, keep the text and report the pending issue.
3. **Decision Record** explains why a plausible alternative is wrong. Consolidate
   related rationale in an accepted ADR under `docs/adr/`; link to the owner
   instead of creating a second specification. Remove the source text only after
   its rationale (including any independent test oracle) is recoverable. Mark
   remaining decisions as deferred; never mechanically strip every token.
4. Run the relevant checks and `git diff --check`. Verify behavior, e.g.
   compare Python ASTs for a comment-only batch. Report removed and remaining
   **physical comment-only lines** separately from inline comments and the net
   diff including ADRs/tooling.
