from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from xic_extractor.alignment.early_loser_shadow import EarlyLoserShadowReport


def write_early_loser_shadow_json(
    path: Path,
    report: EarlyLoserShadowReport,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            asdict(report),
            handle,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")
    return path
