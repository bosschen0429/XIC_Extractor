"""Exercise test isolation in fresh processes without touching user settings."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


def _run_test_environment(
    tmp_path: Path,
    source: str,
    *,
    pytest_args: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    checkout = tmp_path / "checkout"
    tests = checkout / "tests"
    tests.mkdir(parents=True)
    (checkout / "config").mkdir()
    (checkout / "config" / "settings.txt").write_text("original", encoding="utf-8")
    shutil.copyfile(Path(__file__).with_name("conftest.py"), tests / "conftest.py")
    shutil.copyfile(
        Path(__file__).resolve().parents[1] / "pyproject.toml",
        checkout / "pyproject.toml",
    )
    (tests / "test_environment.py").write_text(dedent(source), encoding="utf-8")
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("PYTEST_DEBUG_TEMPROOT", None)
    env.update(environment or {})
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
            *pytest_args,
            str(tests),
        ],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("pytest_args", [(), ("--noconftest",)])
def test_temporary_directories_stay_in_the_checkout(tmp_path, pytest_args):
    result = _run_test_environment(
        tmp_path,
        """
        from pathlib import Path

        def test_directory(tmp_path):
            assert tmp_path.is_relative_to(Path.cwd() / 'tmp_runtime')
        """,
        pytest_args=pytest_args,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("override", ["basetemp", "PYTEST_DEBUG_TEMPROOT"])
def test_explicit_temporary_directory_overrides_are_preserved(tmp_path, override):
    directory = tmp_path / "explicit-temp"
    directory.mkdir()
    result = _run_test_environment(
        tmp_path,
        f"""
        from pathlib import Path

        def test_directory(tmp_path):
            assert tmp_path.is_relative_to(Path({str(directory)!r}))
        """,
        pytest_args=("--noconftest", "--basetemp", str(directory))
        if override == "basetemp"
        else ("--noconftest",),
        environment={override: str(directory)}
        if override == "PYTEST_DEBUG_TEMPROOT"
        else None,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("runner_args", [(), ("-p", "xdist.plugin", "-n", "2")])
def test_temporary_setup_permission_error_is_an_environment_block(
    tmp_path, runner_args
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "denied_temp.py").write_text(
        dedent(
            """
            import pytest

            def denied(self):
                raise PermissionError('temporary directory access denied')

            pytest.TempPathFactory.getbasetemp = denied
            """
        ),
        encoding="utf-8",
    )
    result = _run_test_environment(
        tmp_path,
        """
        from pathlib import Path

        def test_body(tmp_path):
            Path('body-ran').touch()
        """,
        pytest_args=("--noconftest", "-p", "denied_temp", *runner_args),
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, (
        result.stdout + result.stderr
    )
    assert "ENVIRONMENT_BLOCKED" in result.stdout + result.stderr
    assert "temporary directory access denied" in result.stdout + result.stderr
    assert not (tmp_path / "checkout" / "body-ran").exists()


def test_unusable_temporary_root_stops_before_test_execution(tmp_path):
    root = tmp_path / "not-a-directory"
    root.write_text("keep this file", encoding="utf-8")
    result = _run_test_environment(
        tmp_path,
        """
        from pathlib import Path

        def test_body():
            Path('body-ran').touch()
        """,
        pytest_args=("--noconftest",),
        environment={"PYTEST_DEBUG_TEMPROOT": str(root)},
    )

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, (
        result.stdout + result.stderr
    )
    assert "ENVIRONMENT_BLOCKED" in result.stdout + result.stderr
    assert root.read_text(encoding="utf-8") == "keep this file"
    assert not (tmp_path / "checkout" / "body-ran").exists()


@pytest.mark.parametrize("exception", ["AssertionError", "PermissionError"])
def test_test_body_failures_are_not_temporary_environment_errors(tmp_path, exception):
    result = _run_test_environment(
        tmp_path,
        f"""
        def test_body():
            raise {exception}('real test failure')
        """,
        pytest_args=("--noconftest",),
    )

    assert result.returncode == pytest.ExitCode.TESTS_FAILED, (
        result.stdout + result.stderr
    )
    assert "ENVIRONMENT_BLOCKED" not in result.stdout + result.stderr
    assert "FAILED" in result.stdout


def test_overlapping_sessions_keep_separate_temporary_directories(tmp_path):
    result = _run_test_environment(
        tmp_path,
        r"""
        from pathlib import Path
        import subprocess
        import sys

        def test_parent(tmp_path):
            sentinel = tmp_path / 'active'
            sentinel.write_text('keep', encoding='utf-8')
            peer = tmp_path / 'test_peer.py'
            peer.write_text(
                'from pathlib import Path\n'
                'def test_peer(tmp_path):\n'
                f'    assert tmp_path.parent != Path({str(tmp_path.parent)!r})\n',
                encoding='utf-8',
            )
            child = subprocess.run(
                [sys.executable, '-m', 'pytest', '--noconftest', '-q', str(peer)],
                capture_output=True, text=True, timeout=30,
            )
            assert child.returncode == 0, child.stdout + child.stderr
            assert sentinel.read_text(encoding='utf-8') == 'keep'
        """,
        pytest_args=("--noconftest",),
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_default_application_settings_never_resolve_to_the_checkout():
    """Local config/ holds the developer's own ignored settings, not test input."""
    from xic_workbench import config_io, untargeted_config

    checkout_config = (Path(config_io.__file__).parents[1] / "config").resolve()
    defaults = (config_io.CONFIG_DIR, untargeted_config.CONFIG_DIR)

    assert all(Path(d).resolve() != checkout_config for d in defaults)


def test_pure_domain_test_does_not_load_raw_reader(tmp_path):
    result = _run_test_environment(
        tmp_path,
        """
        import sys
        from xic_extractor.peak_detection.baseline import bounded_trace_interval

        def test_domain_calculation():
            assert bounded_trace_interval(-1, 4, 3) == (0, 3)
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


def test_ui_fixture_imports_do_not_load_application_or_test_modules():
    script = dedent(
        """
        import sys
        from tests.inspector_fixtures import read_report_payload
        from tests.targeted_fixtures import targeted_rows
        from tests.workbench_http import workbench_client

        targeted_rows()
        assert not any(name.startswith('tests.test_') for name in sys.modules)
        assert 'xic_workbench' not in sys.modules
        assert 'xic_extractor.raw_reader' not in sys.modules
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
