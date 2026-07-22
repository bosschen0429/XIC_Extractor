from __future__ import annotations

import os
from pathlib import Path

import pytest

from xic_extractor.artifact_paths import require_disjoint_artifact_paths


def test_artifact_paths_reject_exact_input_output_collision(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.csv"
    artifact.write_text("sealed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="input/output paths must be disjoint"):
        require_disjoint_artifact_paths(
            input_paths=(artifact,),
            output_paths=(artifact,),
            label="unit producer",
        )


def test_artifact_paths_reject_hard_link_alias_collision(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    output_alias = tmp_path / "output.csv"
    source.write_text("sealed\n", encoding="utf-8")
    try:
        os.link(source, output_alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    with pytest.raises(ValueError, match="input/output paths must be disjoint"):
        require_disjoint_artifact_paths(
            input_paths=(source,),
            output_paths=(output_alias,),
            label="unit producer",
        )


def test_artifact_paths_accept_disjoint_nonexistent_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    source.write_text("sealed\n", encoding="utf-8")

    require_disjoint_artifact_paths(
        input_paths=(source,),
        output_paths=(tmp_path / "new-output.csv",),
        label="unit producer",
    )
