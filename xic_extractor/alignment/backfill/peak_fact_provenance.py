"""Run-profile provenance for owner Backfill peak facts.

The artifact holds one ``run_profile`` row that binds the signal-processing
profile used for Backfill physical peak facts.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Literal

from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
    serialize_signal_processing_profile,
)

BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION = (
    "alignment_owner_backfill_peak_fact_provenance"
)

BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS = (
    "row_kind",
    "schema_version",
    "profile_json",
)


@dataclass(frozen=True, slots=True)
class BackfillPeakFactProvenanceRow:
    profile: SignalProcessingProfile
    row_kind: Literal["run_profile"] = "run_profile"
    schema_version: str = BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class BackfillPeakFactProvenanceBatch:
    rows: tuple[BackfillPeakFactProvenanceRow, ...]


def run_profile_row(profile: SignalProcessingProfile) -> BackfillPeakFactProvenanceRow:
    return BackfillPeakFactProvenanceRow(profile)


def render_backfill_peak_fact_provenance_tsv(
    batch: BackfillPeakFactProvenanceBatch,
) -> bytes:
    if not isinstance(batch, BackfillPeakFactProvenanceBatch):
        raise TypeError("batch must be a typed provenance batch")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in batch.rows:
        writer.writerow(
            {
                "row_kind": row.row_kind,
                "schema_version": row.schema_version,
                "profile_json": serialize_signal_processing_profile(row.profile).decode(
                    "utf-8"
                ),
            }
        )
    return buffer.getvalue().encode("utf-8")


EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH = BackfillPeakFactProvenanceBatch(
    (run_profile_row(DEFAULT_SIGNAL_PROCESSING_PROFILE),)
)
