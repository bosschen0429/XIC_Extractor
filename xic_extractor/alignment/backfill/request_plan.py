"""Backfill acquisition settings retained in coverage identity and diagnostics."""

from __future__ import annotations

from typing import Literal

from xic_extractor.alignment.config import BackfillXicBackend

BackfillWindowStrategy = Literal["exact", "super-window"]
BACKFILL_WINDOW_STRATEGIES: tuple[BackfillWindowStrategy, ...] = (
    "exact",
    "super-window",
)
BackfillValidationMode = Literal["primary", "prefilter", "validation"]
BACKFILL_VALIDATION_MODES: tuple[BackfillValidationMode, ...] = (
    "primary",
    "prefilter",
    "validation",
)
BACKFILL_XIC_BACKENDS: tuple[BackfillXicBackend, ...] = (
    "raw",
    "ms1_index",
    "ms1_index_hybrid",
)
BACKFILL_TRACE_RESULT_SEMANTICS = "owner_backfill_xic_trace"
