from dataclasses import dataclass


@dataclass(frozen=True)
class OutputColumn:
    name: str
    advanced: bool = False
    description: str = ""


TARGETED_OUTPUT_SCHEMA_VERSION = "targeted_output"
TARGETED_LONG_CSV_SCHEMA_VERSION = "targeted_long_csv"
TARGETED_DIAGNOSTIC_CSV_SCHEMA_VERSION = "targeted_diagnostics_csv"

# The delivered workbook is a measurement table, not the internal long-row dump.
# Measurements, counted status and the two label columns show; the collapsed
# secondary columns retain peak evidence. A target's role lives on Targets.
# `依據` and `疑點` hold fixed labels (`result_labels`), which also decide
# whether a row needs a look.
WORKBOOK_RESULT_HEADERS: tuple[str, ...] = (
    "SampleName",
    "Target",
    "RT",
    "Area",
    "IntSmoothed",
    "Counted Detection",
    "依據",
    "疑點",
    "PeakStart",
    "PeakEnd",
    "Group",
    "Int",
    "PeakWidth",
)
WORKBOOK_DETAIL_HEADERS = frozenset(
    WORKBOOK_RESULT_HEADERS[WORKBOOK_RESULT_HEADERS.index("PeakStart") :]
)

MS1_SUFFIXES: tuple[str, ...] = (
    "RT",
    "Int",
    "IntSmoothed",
    "Area",
    "PeakStart",
    "PeakEnd",
    "PeakWidth",
)

TARGETED_PRODUCT_PROJECTION_HEADERS: tuple[str, ...] = (
    "Product State",
    "Counted Detection",
    "Review State",
    "Projection Reason",
    "Projection Support Reasons",
    "Projection Review Reasons",
    "Projection Conflict Reasons",
    "Projection Not Counted Reasons",
    "Projection Exclusion Reasons",
    "Benchmark Eligibility State",
)

LONG_COLUMNS: tuple[OutputColumn, ...] = (
    OutputColumn("SampleName"),
    OutputColumn("Group"),
    OutputColumn("Target"),
    OutputColumn("Role"),
    OutputColumn("ISTD Pair"),
    OutputColumn("RT", description="smoothed peak apex RT (min)"),
    OutputColumn(
        "Area",
        description="Gaussian15-smoothed positive AsLS residual area (counts·s)",
    ),
    OutputColumn("NL", advanced=True),
    OutputColumn("Int", advanced=True, description="raw apex intensity"),
    OutputColumn("IntSmoothed", description="selected smoothed peak apex intensity"),
    OutputColumn("PeakStart", description="integration start RT (min)"),
    OutputColumn("PeakEnd", description="integration end RT (min)"),
    OutputColumn("PeakWidth", advanced=True),
    OutputColumn("依據", description="the fact the saved decision rests on"),
    OutputColumn("疑點", description="facts left uncertain, separated by 、"),
    *(
        OutputColumn(header, advanced=True)
        for header in TARGETED_PRODUCT_PROJECTION_HEADERS
    ),
)
LONG_HEADERS: tuple[str, ...] = tuple(column.name for column in LONG_COLUMNS)
LONG_ADVANCED_HEADERS: frozenset[str] = frozenset(
    column.name for column in LONG_COLUMNS if column.advanced
)

DIAGNOSTIC_HEADERS: tuple[str, ...] = (
    "SampleName",
    "Target",
    "Issue",
    "Reason",
)
