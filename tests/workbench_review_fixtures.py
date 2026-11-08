"""Real evidence review with no analysis-job executor."""

from tests.workbench_config_fixtures import analysis_workspace
from xic_workbench import config_io
from xic_workbench.workspace import ReviewWorkspace


def review_workspace(*, config_dir=None, defaults=None):
    return ReviewWorkspace(
        analysis_workspace(config_dir or config_io.CONFIG_DIR, dict(defaults or {}))
    )
