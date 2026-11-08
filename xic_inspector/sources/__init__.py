"""Current evidence source binding for the shared Inspector workflow."""

from xic_extractor.alignment_explanation.errors import (
    ExplanationError,
    ExplanationUnavailableError,
)
from xic_extractor.evidence_artifacts import EvidenceLocator
from xic_inspector.sources.curve_bundle import (
    CurveBundleLocator,
    CurveBundleSource,
    read_review_json,
)
from xic_inspector.sources.delta_mass import DeltaMassReviewSource


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
    if path.suffix.lower() == ".xlsx":
        from xic_inspector.sources.targeted_workbook import TargetedWorkbookSource

        return TargetedWorkbookSource(path)
    if path.suffix.lower() == ".json":
        from xic_extractor.diagnostics.delta_mass_review_data import REVIEW_SCHEMA

        loaded = read_review_json(path)
        if (
            isinstance(loaded[0], dict)
            and loaded[0].get("schema_version") == REVIEW_SCHEMA
        ):
            return DeltaMassReviewSource(path, loaded)
        return CurveBundleSource(path, loaded=loaded)
    from xic_inspector.sources.cid_nl import CidNlSource, read_summary
    from xic_inspector.sources.cid_nl_query import IndexedCidNlSource

    summary = read_summary(path)
    from xic_extractor.alignment_explanation.review import REVIEW_SCHEMA_VERSION

    if summary["schema_version"] == REVIEW_SCHEMA_VERSION:
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
