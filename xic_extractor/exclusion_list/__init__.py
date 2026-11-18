"""DDA Targeted Mass Exclusion lists built from a solvent blank."""


class ExclusionListError(ValueError):
    """A recipe, a file it cites or an output cannot be used as given."""
