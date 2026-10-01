"""Current evidence source binding for the shared Inspector workflow."""

from xic_extractor.diagnostics.alignment_explanation import (
    ExplanationError,
    ExplanationUnavailableError,
)
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_inspector.sources.curve_bundle import CurveBundleLocator, CurveBundleSource


def __getattr__(name):
    # Preserve the existing public names without loading the SQLite adapter
    # (and its alignment imports) for a portable bundle.
    if name == "EvidenceSource":
        from xic_inspector.sources.cid_nl import CidNlSource

        return CidNlSource
    if name == "read_evidence_summary":
        from xic_inspector.sources.cid_nl import read_summary

        return read_summary
    raise AttributeError(name)


def open_source(path, *, query_only=False):
    """Select a source adapter without interpreting its schema here."""
    if path.suffix.lower() == ".json":
        return CurveBundleSource(path)
    from xic_inspector.sources.cid_nl import CidNlSource, read_summary
    from xic_inspector.sources.cid_nl_query import IndexedCidNlSource

    summary = read_summary(path)
    revision = summary.get("query_index_version")
    if revision == 2 or (revision == 1 and query_only):
        return IndexedCidNlSource(path, summary)
    return CidNlSource(path, query_only=query_only)


__all__ = [
    "EvidenceLocator",
    "EvidenceSource",
    "CurveBundleLocator",
    "open_source",
    "ExplanationError",
    "ExplanationUnavailableError",
    "read_evidence_summary",
]
