"""Shared Workbench operation errors."""


class WorkspaceBusy(ValueError):
    """A running acquisition must finish or be cancelled first."""
