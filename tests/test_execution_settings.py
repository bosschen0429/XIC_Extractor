from __future__ import annotations

from pathlib import Path

import pytest

from xic_extractor import execution_settings
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


def test_load_profile_reads_only_worker_setting_and_defaults_missing_or_blank(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit.csv"
    explicit.write_text(
        "key,value\n"
        "signal_processing_window_points,15\n"
        "ms1_morphology_smoothing_window_points,21\n"
        "parallel_workers,5\n",
        encoding="utf-8",
    )
    missing = tmp_path / "missing-worker.csv"
    missing.write_text("key,value\nparallel_mode,serial\n", encoding="utf-8")
    blank = tmp_path / "blank-worker.csv"
    blank.write_text("key,value\nparallel_workers,\n", encoding="utf-8")
    monkeypatch.setattr(execution_settings.os, "cpu_count", lambda: 9)

    assert execution_settings.load_execution_profile(explicit).parallel_workers == 5
    assert execution_settings.load_execution_profile(missing).parallel_workers == 8
    assert execution_settings.load_execution_profile(blank).parallel_workers == 8


def test_explicit_override_bypasses_invalid_saved_worker(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.csv"
    settings_path.write_text(
        "key,value\nparallel_workers,invalid\n", encoding="utf-8"
    )

    profile = execution_settings.load_execution_profile(
        settings_path, parallel_workers=3
    )

    assert profile.parallel_workers == 3


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
