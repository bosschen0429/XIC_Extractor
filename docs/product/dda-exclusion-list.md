# DDA Exclusion Lists

A Targeted Mass Exclusion list keeps a DDA method from spending MS2 scans on
ions that the solvent and the system emit in every injection.
`xic-exclusion-list` builds one list per method family from a blank injection;
`--help` and the starter recipe (`--new`) describe the inputs, and the code and
its tests own the tolerances, ranking and windows.

| Decision | Owner | Tests |
| --- | --- | --- |
| Target protection and timed cuts | `exclusion_list.selection` | [`test_exclusion_list_selection.py`](../../tests/test_exclusion_list_selection.py) |
| Background ranking and RT windows | `exclusion_list.background` | [`test_exclusion_list_background.py`](../../tests/test_exclusion_list_background.py) |
| Recipe | `exclusion_list.recipe` | [`test_exclusion_list_recipe.py`](../../tests/test_exclusion_list_recipe.py) |
| Build and CSV export | `exclusion_list.build`, `exclusion_list.export` | [`test_exclusion_list_cli.py`](../../tests/test_exclusion_list_cli.py) |

## Targets Always Win

A row must never stop an ion that a method is meant to fragment. The
instrument excludes every precursor within the exclusion tolerance of a row and
treats every precursor within the inclusion tolerance of a target as that
target, so a row is safe only when the two m/z windows never overlap while both
are active; requiring only the row's m/z to lie outside the target's window
would still let the row's own window swallow the target's edge. Every target
file in the recipe protects every list, including files only some methods load.

## Why It Works This Way

- A background ion below the method's MS2 intensity threshold never triggers,
  so it would only waste an entry.
- Ions rank within the method's scan range because ions a method does not
  acquire never compete for its precursor slots.
- RT windows assume the method runs the blank's LC gradient; a method with a
  different gradient uses a list without RT.

## Limits

- Isotope peaks stay on the list: with monoisotopic precursor selection off,
  the instrument can pick them as precursors.
- The list reflects one blank; ions that appear only with samples or carryover
  are not covered.
- The CSV columns follow the Method Editor import template with RT; the import
  of a list without RT has not been checked on the instrument.
