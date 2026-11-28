"""Reader-oriented counts of the saved routes and cell decisions."""

from xic_extractor.alignment_explanation.query import open_review_query


def review_overview(path, summary, curation_removed_ids=()):
    with open_review_query(path, summary) as query:
        return query.overview(curation_removed_ids)
