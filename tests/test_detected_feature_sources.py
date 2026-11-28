from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.mark.parametrize("route", ("features", "local_ms1", "cid_nl"))
def test_generic_feature_and_cid_nl_routes_work_without_delta_mass_provider(
    tmp_path: Path, route: str
) -> None:
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class UnavailableDeltaMassProvider(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in {
                    "xic_extractor.discovery.delta_mass_strategy",
                    "xic_extractor.discovery.delta_mass_patterns",
                }:
                    raise ImportError(f"Delta Mass provider is unavailable: {fullname}")
                return None

        sys.meta_path.insert(0, UnavailableDeltaMassProvider())
        if sys.argv[1] == "local_ms1":
            import xic_extractor.discovery.local_ms1_features
        elif sys.argv[1] == "features":
            from xic_extractor.discovery.detected_features import DetectedFeature

            features = tuple(
                DetectedFeature(
                    sample_stem=sample, feature_id="feature-1",
                    source_feature_row_id=None, mz=mz, rt_apex_min=8.5,
                    rt_start_min=8.4, rt_end_min=8.6, height=height,
                    area=height * 2, trace_quality="OK", shape_score=None,
                    source="test_feature_source",
                )
                for sample, mz, height in (
                    ("sample-a", 500.0, 250.0), ("sample-b", 600.0, 800.0)
                )
            )
            assert [feature.mz for feature in features] == [500.0, 600.0]
        else:
            from scripts.run_discovery import main
            from xic_extractor.presets import (
                apply_to_discovery, bind_cid_nl_recipe, load_preset,
            )

            preset = load_preset("dna_dr")
            settings = apply_to_discovery(preset)
            assert settings.selected_tag_names == ("DNA_dR",)
            assert settings.neutral_loss_profiles[0].neutral_loss_da == 116.0474
            recipe = bind_cid_nl_recipe(preset, "discovery_only")
            assert recipe.discovery_settings == settings
            try:
                main(["--help"])
            except SystemExit as exc:
                assert exc.code == 0
            else:
                raise AssertionError("CLI help did not complete argparse handling")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, route, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
