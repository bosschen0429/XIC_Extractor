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


class ScopedMs2WindowReader:
    """Fake-reader ``read_ms2_window``: scope admits events; errors are kept."""

    def read_ms2_window(self, rt_min, rt_max, *, precursor_scope):
        return tuple(
            event
            for event in self.iter_ms2_scans(rt_min, rt_max)
            if event.scan is None
            or precursor_scope(event.scan.precursor_mz, event.scan.precursor_metadata)
        )
