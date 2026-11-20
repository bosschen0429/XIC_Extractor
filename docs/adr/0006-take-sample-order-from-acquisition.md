---
status: accepted
---

# Take sample names and injection order from the acquisition

A sequence sheet can describe a different order or use different labels;
directory enumeration and parallel header completion also need not follow the
instrument clock. The RAW acquisition determines sample identity and order.
上機資訊 annotates samples (for example with roles); its row numbers and names do
not relabel or reorder them. The same rule applies to Targeted, Discovery,
Alignment and the published readers.

Read each RAW header under its task’s own handle and keep the answer associated
with that path before ordering by acquisition time. In tests where RAW files are
empty placeholders, the fake header reader uses file times as a stand-in
clock. That fixture choice is not a fallback ordering rule for real RAW files.
