from __future__ import annotations

from pathlib import Path

import pytest

from xic_extractor.configuration import execution_profile as execution_settings
from xic_extractor.configuration.execution_profile import in_process_execution
from xic_extractor.configuration.models import ExtractionConfig


def test_canonical_profile_uses_cpu_budget_and_parallel_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution_settings.os, "cpu_count", lambda: 12)

    profile = execution_settings.load_execution_profile()

    assert profile.parallel_workers == 11
    assert profile.alignment_workers == 4


@pytest.mark.parametrize(
    ("parallel_workers", "alignment_workers"), [(0, None), (None, 0)]
)
def test_profile_rejects_invalid_worker_counts(
    parallel_workers: int | None,
    alignment_workers: int | None,
) -> None:
    with pytest.raises(ValueError, match="integer >= 1"):
        execution_settings.load_execution_profile(
            parallel_workers=parallel_workers,
            alignment_workers=alignment_workers,
        )


@pytest.mark.parametrize("backend", ["serial", "Process", ""])
def test_entry_rejects_unknown_backend(backend: str) -> None:
    with pytest.raises(ValueError, match="backend must be one of process, thread"):
        execution_settings.load_execution_profile(backend=backend)


def test_worker_counts_are_internal_unless_a_caller_overrides(monkeypatch):
    monkeypatch.setattr(execution_settings.os, "cpu_count", lambda: 9)

    assert execution_settings.load_execution_profile() == (
        execution_settings.ExecutionProfile(8, 4)
    )
    assert execution_settings.load_execution_profile(
        parallel_workers=3, alignment_workers=2
    ) == execution_settings.ExecutionProfile(3, 2)


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
        peak_min_prominence_ratio=0.1,
        ms2_precursor_tol_da=1.6,
        nl_min_intensity_ratio=0.01,
        execution=in_process_execution(),
    )

    assert config.execution == execution_settings.ExecutionProfile(
        parallel_workers=4, backend="thread", overlap_partitions=False
    )
