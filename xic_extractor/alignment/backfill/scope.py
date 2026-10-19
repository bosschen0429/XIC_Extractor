from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from xic_extractor.alignment.backfill.evidence_contract import (
    SkippedEvidenceRecord,
)

BackfillScope = Literal["full-audit", "production-equivalent", "selected-families"]
REQUEST_PLAN_VERSION = "alignment-parity-scoped-owner-backfill-request-plan"


def skipped_evidence_summary(rows: Sequence[SkippedEvidenceRecord]) -> dict[str, int]:
    return {
        "skipped_evidence_row_count": len(rows),
        "raw_xic_requests_skipped": sum(row.raw_xic_requests_skipped for row in rows),
    }
