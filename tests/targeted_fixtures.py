"""Synthetic Targeted inputs shared by method and Workbench tests."""


def targeted_rows():
    """A synthetic deuterated ISTD and its paired analyte."""
    common = {
        "mz": "250",
        "expected_rt_min": "2",
        "method": "dR",
        "role": "analyte",
        "istd_pair": "",
    }
    return [
        {
            **common,
            "label": "standard",
            "role": "istd",
            "isotope_label_type": "deuterated",
            "note": "標準品",
        },
        {
            **common,
            "label": "analyte",
            "istd_pair": "standard",
            "paired_rt_relation": "istd_not_later_than_pair",
            "sample_applicability": "rna_containing",
            "note": "方法 A",
        },
    ]
