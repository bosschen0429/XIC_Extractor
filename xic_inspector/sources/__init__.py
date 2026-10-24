"""Current evidence source binding for the shared Inspector workflow."""

from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    ExplanationUnavailableError,
)
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_inspector.sources.curve_bundle import CurveBundleLocator, CurveBundleSource


def __getattr__(name):
    if name == "read_evidence_summary":
        from xic_inspector.sources.cid_nl import read_summary

        return read_summary
    raise AttributeError(name)


def open_source(path):
    """Select a source adapter without interpreting its schema here."""
    if path.suffix.lower() == ".xlsx":
        from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

        return TargetedWorkbookSource(path)
    if path.suffix.lower() == ".json":
        return CurveBundleSource(path)
    from xic_inspector.sources.cid_nl import read_summary
    from xic_inspector.sources.cid_nl_query import IndexedCidNlSource

    summary = read_summary(path)
    return IndexedCidNlSource(path, summary)


__all__ = [
    "EvidenceLocator",
    "CurveBundleLocator",
    "open_source",
    "ExplanationError",
    "ExplanationUnavailableError",
    "read_evidence_summary",
]
