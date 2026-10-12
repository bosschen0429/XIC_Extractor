"""Run-profile provenance for owner Backfill peak facts.

The artifact holds one ``run_profile`` row that binds the signal-processing
profile used for Backfill physical peak facts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from typing import Literal

from xic_extractor.configuration.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
    SignalProcessingProfile,
)

BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION = (
    "alignment_owner_backfill_peak_fact_provenance"
)
BACKFILL_PROVENANCE_ROW_ID_PREFIX = "owner-backfill-provenance-row:"

BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS = (
    "row_kind",
    "schema_version",
    "profile_schema_version",
    "profile_id",
    "profile_hash",
    "signal_processing_method",
    "signal_processing_window_points",
    "physical_signal_formula",
    "projection_content_id",
)


@dataclass(frozen=True, slots=True)
class BackfillPeakFactProvenanceRow:
    profile_schema_version: str
    profile_id: str
    profile_hash: str
    signal_processing_method: str
    signal_processing_window_points: int
    physical_signal_formula: str
    row_kind: Literal["run_profile"] = "run_profile"
    schema_version: str = BACKFILL_PEAK_FACT_PROVENANCE_SCHEMA_VERSION

    @property
    def projection_content_id(self) -> str:
        payload = [
            str(getattr(self, name))
            for name in BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS[:-1]
        ]
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return f"{BACKFILL_PROVENANCE_ROW_ID_PREFIX}{digest}"


@dataclass(frozen=True, slots=True)
class BackfillPeakFactProvenanceBatch:
    rows: tuple[BackfillPeakFactProvenanceRow, ...]


def run_profile_row(profile: SignalProcessingProfile) -> BackfillPeakFactProvenanceRow:
    return BackfillPeakFactProvenanceRow(
        profile_schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        profile_hash=profile.profile_hash,
        signal_processing_method=profile.method,
        signal_processing_window_points=profile.window_points,
        physical_signal_formula=profile.physical_signal_formula,
    )


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
                name: str(getattr(row, name))
                for name in BACKFILL_PEAK_FACT_PROVENANCE_COLUMNS
            }
        )
    return buffer.getvalue().encode("utf-8")


EMPTY_BACKFILL_PEAK_FACT_PROVENANCE_BATCH = BackfillPeakFactProvenanceBatch(
    (run_profile_row(DEFAULT_SIGNAL_PROCESSING_PROFILE),)
)
