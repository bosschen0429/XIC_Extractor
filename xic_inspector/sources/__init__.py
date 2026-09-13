"""Current evidence source binding for the shared Inspector workflow."""

from xic_extractor.diagnostics.cid_nl_explanation import (
    EvidenceLocator,
    ExplanationError,
    ExplanationUnavailableError,
)
from xic_inspector.sources.cid_nl import CidNlSource as EvidenceSource
from xic_inspector.sources.cid_nl import read_summary as read_evidence_summary

__all__ = [
    "EvidenceLocator",
    "EvidenceSource",
    "ExplanationError",
    "ExplanationUnavailableError",
    "read_evidence_summary",
]
