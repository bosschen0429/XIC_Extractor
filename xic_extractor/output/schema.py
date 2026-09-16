from dataclasses import dataclass


@dataclass(frozen=True)
class OutputColumn:
    name: str
    advanced: bool = False
    description: str = ""


TARGETED_OUTPUT_SCHEMA_VERSION = "targeted_output_v2"
TARGETED_LONG_CSV_SCHEMA_VERSION = "targeted_long_csv_v2"
TARGETED_DIAGNOSTIC_CSV_SCHEMA_VERSION = "targeted_diagnostics_csv_v1"
TARGETED_SCORE_BREAKDOWN_CSV_SCHEMA_VERSION = "targeted_score_breakdown_csv_v1"

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
    "Legacy Authority Status",
    "Benchmark Eligibility State",
)
TARGETED_PRODUCT_VISIBLE_HEADERS: frozenset[str] = frozenset(
    {"Product State", "Counted Detection", "Review State"}
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
        description="Gaussian15-smoothed positive AsLS residual area",
    ),
    OutputColumn("NL"),
    OutputColumn("Int", advanced=True, description="raw apex intensity"),
    OutputColumn("IntSmoothed", description="selected smoothed peak apex intensity"),
    OutputColumn("PeakStart", advanced=True),
    OutputColumn("PeakEnd", advanced=True),
    OutputColumn("PeakWidth", advanced=True),
    OutputColumn("Confidence", advanced=True),
    OutputColumn("Reason"),
    *(
        OutputColumn(header, advanced=header not in TARGETED_PRODUCT_VISIBLE_HEADERS)
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

SCORE_BREAKDOWN_HEADERS: tuple[str, ...] = (
    "SampleName",
    "Target",
    "Final Confidence",
    "Detection Counted",
    "Product State",
    "Review State",
    "Projection Reason",
    "Legacy Authority Status",
    "Caps",
    "Raw Score",
    "Support",
    "Concerns",
    "Base Score",
    "Positive Points",
    "Negative Points",
    "symmetry",
    "local_sn",
    "nl_support",
    "rt_prior",
    "rt_centrality",
    "noise_shape",
    "peak_width",
    "Quality Penalty",
    "Quality Flags",
    "Total Severity",
    "Confidence",
    "Prior RT",
    "Prior Source",
)
