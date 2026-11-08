"""JSON projection of application receipts for the Workbench transport."""

import dataclasses
from pathlib import Path
from typing import Any


def json_value(value: Any) -> Any:
    """Project path-bearing application receipts onto the JSON transport."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_value(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value
