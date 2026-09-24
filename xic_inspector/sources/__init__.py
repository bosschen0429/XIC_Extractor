"""Current evidence source binding for the shared Inspector workflow."""

from xic_extractor.diagnostics.cid_nl_explanation import (
    EvidenceLocator,
    ExplanationError,
    ExplanationUnavailableError,
)
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


def open_source(path):
    """Select a source adapter without interpreting its schema here."""
    if path.suffix.lower() == ".json":
        return CurveBundleSource(path)
    from xic_inspector.sources.cid_nl import CidNlSource

    return CidNlSource(path)

__all__ = [
    "EvidenceLocator",
    "EvidenceSource",
    "CurveBundleLocator",
    "open_source",
    "ExplanationError",
    "ExplanationUnavailableError",
    "read_evidence_summary",
]
