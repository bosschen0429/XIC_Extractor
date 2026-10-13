from __future__ import annotations

from pathlib import Path

import pytest

from xic_extractor.configuration import execution_profile as execution_settings
from xic_extractor.configuration.models import ExtractionConfig


def test_canonical_profile_uses_cpu_budget_and_parallel_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution_settings.os, "cpu_count", lambda: 12)

    profile = execution_settings.resolve_execution_profile()

    assert profile.parallel_workers == 11
    assert profile.alignment_workers == 4


def test_settings_and_explicit_overrides_have_one_precedence_chain() -> None:
    from_settings = execution_settings.resolve_execution_profile(
        {"parallel_workers": "6"}
    )
    explicit = execution_settings.resolve_execution_profile(
        {"parallel_workers": "6"}, parallel_workers=3, alignment_workers=2
    )

    assert from_settings == execution_settings.ExecutionProfile(6, 4)
    assert explicit == execution_settings.ExecutionProfile(3, 2)


@pytest.mark.parametrize(
    ("settings", "parallel_workers", "alignment_workers"),
    [
        ({"parallel_workers": "0"}, None, None),
        ({"parallel_workers": "bad"}, None, None),
        (None, 0, None),
        (None, None, 0),
    ],
)
def test_profile_rejects_invalid_worker_counts(
    settings: dict[str, str] | None,
    parallel_workers: int | None,
    alignment_workers: int | None,
) -> None:
    with pytest.raises(ValueError, match="integer >= 1"):
        execution_settings.resolve_execution_profile(
            settings,
            parallel_workers=parallel_workers,
            alignment_workers=alignment_workers,
        )


@pytest.mark.parametrize(
    "saved,expected",
    [({"parallel_workers": "5"}, 5), ({}, 8), ({"parallel_workers": ""}, 8)],
)
def test_profile_reads_application_state(monkeypatch, tmp_path, saved, expected):
    from xic_extractor.configuration.workspace_state import save_workspace

    monkeypatch.setattr(execution_settings, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(execution_settings.os, "cpu_count", lambda: 9)
    save_workspace(tmp_path / "config", settings=saved)
    assert execution_settings.load_execution_profile().parallel_workers == expected


def test_explicit_override_bypasses_invalid_saved_worker(monkeypatch, tmp_path):
    from xic_extractor.configuration.workspace_state import save_workspace

    monkeypatch.setattr(execution_settings, "runtime_root", lambda: tmp_path)
    save_workspace(tmp_path / "config", settings={"parallel_workers": "invalid"})
    assert (
        execution_settings.load_execution_profile(parallel_workers=3).parallel_workers
        == 3
    )


@pytest.mark.parametrize("frozen", [False, True])
def test_default_settings_resolve_from_development_or_executable_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frozen: bool
) -> None:
    runtime_root = tmp_path / ("bundle" if frozen else "checkout")
    config_dir = runtime_root / "config"
    config_dir.mkdir(parents=True)
    from xic_extractor.configuration.workspace_state import save_workspace

    (runtime_root / "pyproject.toml").touch()
    save_workspace(config_dir, settings={"parallel_workers": "7"})
    from xic_extractor.configuration import paths

    # This test exercises root selection itself, within its disposable checkout.
    monkeypatch.setattr(execution_settings, "runtime_root", paths.runtime_root)
    monkeypatch.setattr(paths.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(runtime_root / "xic.exe"))
    monkeypatch.setattr(
        paths,
        "_PACKAGE_PARENT",
        runtime_root,
    )

    assert execution_settings.load_execution_profile().parallel_workers == 7


def test_extraction_config_default_factory_uses_shared_parallel_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(execution_settings.os, "cpu_count", lambda: 5)
    config = ExtractionConfig(
        data_dir=tmp_path,
        dll_dir=tmp_path,
        output_csv=tmp_path / "out.csv",
        diagnostics_csv=tmp_path / "diagnostics.csv",
        smooth_window=15,
        smooth_polyorder=3,
        peak_rel_height=0.95,
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
    )

    assert config.parallel_mode == "serial"
    assert config.parallel_workers == 4
