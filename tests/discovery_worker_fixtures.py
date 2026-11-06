"""Helpers shared by test_discovery_worker and other test modules."""

from typing import Any

from xic_extractor.output.alignment.pipeline_outputs import (
    AlignmentRunOutputs as _AlignmentRunOutputs,
)


def _alignment_outputs(
    **kwargs: Any,
) -> _AlignmentRunOutputs:
    return _AlignmentRunOutputs(
        **kwargs,
    )
