"""Exercise test isolation in fresh processes without touching user settings."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


def _run_test_environment(
    tmp_path: Path, source: str
) -> subprocess.CompletedProcess[str]:
    checkout = tmp_path / "checkout"
    tests = checkout / "tests"
    tests.mkdir(parents=True)
    (checkout / "config").mkdir()
    (checkout / "config" / "settings.txt").write_text("original", encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("conftest.py"), tests / "conftest.py")
    (tests / "test_environment.py").write_text(dedent(source), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--confcutdir",
            str(checkout),
            "--basetemp",
            str(checkout / "tmp"),
            str(tests),
        ],
        cwd=checkout,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_pure_domain_test_does_not_load_workbench_or_raw_reader(tmp_path):
    result = _run_test_environment(
        tmp_path,
        """
        import sys
        from xic_extractor.peak_detection.baseline import bounded_trace_interval

        def test_domain_calculation():
            assert bounded_trace_interval(-1, 4, 3) == (0, 3)
            assert not any(name == 'xic_workbench' or name.startswith('xic_workbench.')
                           for name in sys.modules)
            assert 'xic_extractor.raw_reader' not in sys.modules
        """,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_test_environment_still_rejects_repository_config_writes(tmp_path):
    result = _run_test_environment(
        tmp_path,
        """
        from pathlib import Path

        def test_accidental_config_write():
            Path('config/settings.txt').write_text('changed', encoding='utf-8')
        """,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Tests modified repository config: settings.txt" in result.stdout


def test_lazily_imported_raw_reader_cache_is_isolated_between_tests(tmp_path):
    result = _run_test_environment(
        tmp_path,
        """
        from types import SimpleNamespace

        def test_first_fake_dll_load(tmp_path):
            from xic_extractor import raw_reader
            def fake_import(name):
                return SimpleNamespace(get_runtime_info=lambda: 'ready',
                                       AddReference=lambda _path: None)
            raw_reader.ensure_assemblies_loaded(tmp_path, fake_import)
            assert raw_reader._ASSEMBLIES_LOADED

        def test_next_test_starts_without_fake_dll_cache():
            from xic_extractor import raw_reader
            assert not raw_reader._ASSEMBLIES_LOADED
        """,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_explanation_metadata_fixture_does_not_load_runtime_or_test_modules():
    script = dedent(
        """
        import sys
        from tests.alignment_explanation_fixtures import (
            explanation_summary, curve_provenance,
        )

        explanation_summary({'samples': [{'sample_key': 'S1'}]})
        curve_provenance()
        assert not any(name.startswith('tests.test_') for name in sys.modules)
        assert not any(name.startswith('xic_extractor.untargeted_execution')
                       for name in sys.modules)
        assert 'xic_extractor.raw_reader' not in sys.modules
        """,
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
