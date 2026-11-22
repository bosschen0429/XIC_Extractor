"""Local replay snapshots: the formal objects an Alignment stage consumes.

A snapshot is the compressed pickle of the current code's objects, so a stage
replays exactly what the full run handed it and a new field needs no snapshot
code. Snapshots are not exchange files: loading one runs code it names, and it
only matches the code that captured it. Replay captures you made yourself, and
recapture after upstream code changes.
"""

from __future__ import annotations

import gzip
import pickle
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from xic_extractor.alignment.grouping_experiment import GroupingInputs
from xic_extractor.execution._serialization import (
    owner_pickle_dumps,
    owner_pickle_loads,
)


def save_snapshot(path: Path, value: object) -> None:
    with gzip.open(path, "wb", compresslevel=1) as stream:
        stream.write(owner_pickle_dumps(value, ()))


def load_snapshot(path: Path) -> Any:
    try:
        with gzip.open(path, "rb") as stream:
            return owner_pickle_loads(stream.read(), ())
    except (
        gzip.BadGzipFile,
        EOFError,
        pickle.UnpicklingError,
        AttributeError,
        ImportError,
    ) as exc:
        raise ValueError(
            f"{path} was not captured by the current code; capture it again"
        ) from exc


def write_grouping_inputs(
    path: Path, inputs: GroupingInputs, *, provenance: Mapping[str, Any]
) -> None:
    save_snapshot(path, (inputs, dict(provenance)))


def read_grouping_inputs(path: Path) -> tuple[GroupingInputs, dict[str, Any]]:
    return cast(tuple[GroupingInputs, dict[str, Any]], load_snapshot(path))
