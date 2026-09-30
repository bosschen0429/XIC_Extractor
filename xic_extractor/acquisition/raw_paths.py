"""Locate acquisition files for the declared sample set."""

from pathlib import Path


def existing_raw_paths(
    *,
    sample_order: tuple[str, ...],
    raw_files: dict[str, Path | None],
    raw_dir: Path,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for sample_stem in sample_order:
        raw_file = raw_files.get(sample_stem)
        candidates: list[Path] = []
        if raw_file is not None and str(raw_file):
            # Analysis members may sit in subfolders of raw_dir.
            if raw_file.is_absolute() and raw_file.resolve().is_relative_to(
                raw_dir.resolve()
            ):
                candidates.append(raw_file)
            candidates.append(raw_dir / raw_file.name)
        candidates.append(raw_dir / f"{sample_stem}.raw")
        for candidate in candidates:
            if candidate.exists():
                paths[sample_stem] = candidate
                break
    return paths

