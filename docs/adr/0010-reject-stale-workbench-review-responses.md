---
status: accepted
---

# Reject stale Workbench review payloads without discarding history

A saved-result or curve payload can arrive after a different result or sample
has become current, or after the Targeted curve drawer has closed. Let the
user keep reading or change selection while the payload loads. Response
order alone cannot decide which result to display.

- **Mount only for the current selection.** The
  [review client](../../xic_workbench/assets/workspace.js) accepts a payload
  only for the current mount slot and detail, and mounts it in the Targeted
  drawer only while open. An error from a superseded mount must not replace
  the new selection's feedback either. Check curve start replies against
  the current detail and curve request, and poll replies against the
  current job. End the old poll on replacement rather than relying on the
  server to keep exposing the old job.
- **Rejecting a response is not cancelling acquisition.** The
  [server workspace](../../xic_workbench/workspace.py) signals cancellation
  when opening a result, running a query, selecting an item or starting a
  replacement curve job.
  Returning to a cached sample sends no replacement curve request, so the
  client explicitly cancels a pending read. Closing the drawer does not
  itself cancel a read: a still-current acquisition can complete and record
  its view ID without mounting its plot.
- **History is a deliberate return.** A history entry for the same result
  retains the curve snapshot even if a filter has since replaced the live
  detail. If captured while acquisition is pending, the snapshot can receive
  its view ID on completion. Back can close the drawer without discarding an
  already mounted reading (including its RT range). Forward can reuse that
  reading or request the saved snapshot again; it does not revive a discarded
  payload.

A history entry identifies a result and its curation, not just its evidence
file: two curations of the same run can name different items. The server owns
one active query, so restore it **after** earlier queries finish, even when their
replies were discarded. Only then reuse the matching reading snapshot. A new
batch supersedes old links; reload starts from the server's current batch.
Curve feedback belongs to its plot or drawer and cannot clear a current query
error. If a new curation finishes while review is busy, adopt it on unlock.

The [browser regressions](../../tests/test_workbench_browser.py) hold the old
payload, switch or close, then release it and let its consumer run before
checking the mount and feedback. They distinguish the expected view from any
nonempty view by checking its ID or sample, and check that Forward restores
the same snapshot without another RAW read.
