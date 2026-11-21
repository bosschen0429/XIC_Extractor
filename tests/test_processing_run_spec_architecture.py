from __future__ import annotations

import subprocess
import sys
import textwrap


def test_fresh_run_spec_import_adds_only_the_owner_module() -> None:
    code = textwrap.dedent(
        """
        import importlib.abc
        import sys

        import xic_extractor.processing_identity
        import xic_extractor.configuration.signal_processing_profile
        import xic_extractor.validation_primitives
        import xic_extractor.configuration.mass_recalibration
        baseline = {
            name for name in sys.modules if name.startswith("xic_extractor")
        }

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.startswith("xic_extractor.discovery"):
                    raise RuntimeError(f"blocked Discovery import: {fullname}")
                return None

        blocker = Blocker()
        sys.meta_path.insert(0, blocker)
        import xic_extractor.processing_run_spec
        delta = {
            name for name in sys.modules
            if name.startswith("xic_extractor") and name not in baseline
        }
        assert delta == {"xic_extractor.processing_run_spec"}, sorted(delta)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_settings_validation_local_imports_keep_forbidden_modules_absent() -> None:
    code = textwrap.dedent(
        """
        import importlib.abc
        import sys
        import xic_extractor.processing_run_spec as owner
        from xic_extractor.discovery.settings import DiscoverySettings
        from xic_extractor.configuration.cid_nl_profile import NeutralLossProfile

        prohibited = {
            "xic_extractor.discovery.pipeline",
            "xic_extractor.discovery.ms1_capture",
            "xic_extractor.discovery.ms2_events",
            "xic_extractor.discovery.csv_writer",
            "xic_extractor.raw_reader",
            "xic_workbench",
            "scripts.run_discovery",
        }

        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in prohibited:
                    raise RuntimeError(f"blocked runtime import: {fullname}")
                return None

        settings = DiscoverySettings(
            neutral_loss_profile=NeutralLossProfile("DNA_dR", 116.0474)
        )
        sys.meta_path.insert(0, Blocker())
        owner.validate_cid_nl_discovery_settings(settings)
        assert prohibited.isdisjoint(sys.modules), sorted(
            prohibited.intersection(sys.modules)
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
