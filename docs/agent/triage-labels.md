# Triage Labels

This file owns the issue-readiness labels used by repo workflows and templates.
The planning owner defines when an issue changes state; this file defines the
exact GitHub label vocabulary.

| Label | Meaning |
| --- | --- |
| `needs-triage` | Maintainer must evaluate the issue |
| `needs-info` | A material input or frozen contract is missing |
| `ready-for-agent` | The implementation issue is fully specified and unblocked |
| `ready-for-human` | Implementation or judgment requires a human |
| `wontfix` | The issue will not be actioned |

Ensure a label exists in GitHub repository metadata before a template,
automation, or workflow relies on it. Do not create synonyms for these states.
