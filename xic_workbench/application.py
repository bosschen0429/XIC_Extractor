"""Compose the analysis and review services used by the shipped Workbench."""

from pathlib import Path
from typing import Any

from xic_workbench import config_io
from xic_workbench.analysis_workspace import AnalysisWorkspace
from xic_workbench.jobs import AnalysisJobs
from xic_workbench.workspace import ReviewWorkspace


class _MatrixCuration:
    """Load curation only when the user opens or previews a completed Matrix."""

    def describe_source(
        self, config_dir: Path, values: dict[str, Any]
    ) -> dict[str, Any]:
        from xic_workbench.matrix_curation import describe_source

        return describe_source(config_dir, values)

    def preview_curation(
        self, config_dir: Path, values: dict[str, Any]
    ) -> dict[str, Any]:
        from xic_workbench.matrix_curation import preview_curation

        return preview_curation(config_dir, values)


def create_workspace(
    *, defaults: dict[str, str] | None = None, config_dir: Path | None = None
) -> ReviewWorkspace:
    directory = config_dir or config_io.CONFIG_DIR
    analysis = AnalysisWorkspace(
        directory,
        dict(defaults or {}),
        jobs=AnalysisJobs(directory),
        curation=_MatrixCuration(),
    )
    return ReviewWorkspace(analysis)
