# UI Design Principles

Workbench and Inspector organize the interface around the user's question and
next decision, rather than the controls or internal data available to display.
Implementation and review use these principles:

- **Answer → evidence → details.** Define the minimum workflow by the answer
  the user can obtain. Reuse the domain owner's search and evaluation before
  presenting results; the renderer exposes those results and links directly to
  their evidence. Manual scan browsing serves additional investigation, not
  work the existing method can already do.
- **Remove before hiding.** Delete redundant labels, counts and explanations
  before moving useful secondary information into details. Collapsing clutter
  alone does not simplify the workflow.
- **Concise, not ambiguous.** Omit relationships obvious from context, but keep
  the quantities and relationships needed for interpretation. A two-position
  comparison can say `Δm/z` without repeating both position names; a support
  label must still identify what evidence supports what result.
- **Terse copy everywhere.** Every hint, status, note and error uses the
  shortest wording that still says what to fill or what happened, in the same
  register across the interface: no rationale, no restating nearby labels, one
  example at most. Mixed lengths and tones read as clutter.
- **Quiet routine results, visible limitations.** An overview need not repeat
  the same no-finding message for every item. Keep distinctions such as no
  matching evidence versus no acquired data available, and surface failures or
  uncertainty when they affect the user's interpretation or next action.
- **Keep annotations connected.** Place labels near their evidence, with space
  or leader lines where needed. Moving a label out of a plot must preserve its
  visual association with the marked feature.

## Interface Verification

Verify the answer-to-evidence journey with representative data in the actual
interface. Check that the user can identify the result and reach its evidence
without searching internal records or reconstructing missing relationships;
working controls and passing tests alone do not establish this. When a user
requests a principle applied across a view, inspect that view for the same
problem; a local annotation alone does not authorize a wider redesign.

## Styling And Themes

Workbench and Inspector choose style tokens by semantic role, not by current
color: warnings, selection, disabled controls and actions keep their own
meanings. If a role is missing, extend the owning style definitions and their
fallbacks instead of borrowing an unrelated token. Verify the component in the
themes and fallbacks the UI actually supports.
