# DDA Exclusion Lists

A Targeted Mass Exclusion list keeps a DDA method from spending MS2 scans on
ions that the solvent and the system emit in every injection. `xic-exclusion-list`
builds one list per method family from a blank injection; `--help` and the
starter recipe (`--new`) describe the inputs.

## Targets Always Win

A row must never stop an ion that a method is meant to fragment. The instrument
excludes every precursor within the exclusion tolerance of a row and treats every
precursor within the inclusion tolerance of a target as that target, so a row is
safe only when the two m/z windows do not overlap while both are active. With
±20 ppm on both sides a row therefore stays more than about 40 ppm from every
target; a method that excludes at ±10 ppm needs about 30 ppm. Requiring only the
row's m/z to lie outside the target's window would still let the row's own window
swallow the target's edge.

A target with an RT window protects that window only, so a timed row is cut
around it. A target without RT, or any target in a list without RT, protects the
whole run, so the row is dropped. Every target file in the recipe applies to every
list, including files that only some methods load. The report names each row
that was removed or cut and the target that caused it.

## What Fills The List

Named exclusions come first, in recipe order, and then background ions from the
blank. A background ion can enter only when its apex reaches the method's MS2
intensity threshold; weaker ions never trigger and would waste an entry. Ions
rank by the number of scans in which they are among the scan's 20 most intense
centroids within the method's scan range, then by summed intensity. Ranking
within the scan range matters: ions a method does not acquire never compete for
its precursor slots.

An ion seen in at least half of the blank's scans excludes the whole run;
otherwise each run of scans where it elutes becomes a window padded by 0.5 min.
A row that another row already covers in m/z and RT is not listed twice.

## RT Windows

RT windows assume the method runs the blank's LC gradient. A method with a
different gradient uses a list without RT, where each ion is one whole-run row.

## Limits

- Isotope peaks stay on the list: with monoisotopic precursor selection off, the
  instrument can pick them as precursors.
- The list reflects one blank. Ions that appear only with samples or carryover
  are not covered.
- The CSV columns follow the Method Editor import template with RT; the import
  of a list without RT has not been checked on the instrument.

## Verification

- `tests/test_exclusion_list_selection.py`
- `tests/test_exclusion_list_background.py`
- `tests/test_exclusion_list_recipe.py`
