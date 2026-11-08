"""Real configuration operations with no analysis execution or evidence reader."""

from pathlib import Path

from xic_workbench import config_io
from xic_workbench.analysis_workspace import AnalysisWorkspace


class IdleJobs:
    """Fail if a configuration-only scenario unexpectedly starts analysis."""

    busy = False
    has_unseen_completion = False

    def state(self):
        return {"job": None, "results": {}}

    def start(self, kind, values):
        raise AssertionError(f"Configuration-only test started {kind}")

    def cancel(self, job_id):
        raise AssertionError("Configuration-only test cancelled analysis")

    def acknowledge_completion(self, job_id):
        raise AssertionError("Configuration-only test acknowledged analysis")

    def remember_samples(self, raw_dir, rows, overview):
        pass

    def close(self):
        pass


class NoCuration:
    def describe_source(self, config_dir, values):
        raise AssertionError("Configuration-only test described Matrix curation")

    def preview_curation(self, config_dir, values):
        raise AssertionError("Configuration-only test computed Matrix curation")


def analysis_workspace(config_dir: Path, defaults: dict[str, str]):
    return AnalysisWorkspace(
        config_dir, defaults, jobs=IdleJobs(), curation=NoCuration()
    )


class ConfigurationWorkspace:
    """HTTP session adapter for tests of configuration, not review orchestration."""

    def __init__(self, *, config_dir=None, defaults=None):
        self.defaults = dict(defaults or {})
        self.analysis = analysis_workspace(
            config_dir or config_io.CONFIG_DIR, self.defaults
        )
        self.closing = False

    def analysis_action(self, action, values):
        return self.analysis.action(action, values)

    def close(self):
        self.analysis.close()
