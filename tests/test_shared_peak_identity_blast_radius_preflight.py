from __future__ import annotations

import pytest

from xic_extractor.diagnostics.shared_peak_identity_explanation.blast_radius import (
    CELLS_REQUIRED_FIELDS,
    preflight_tsv_artifact,
)


def test_preflight_samples_bounded_rows_without_materializing_reader() -> None:
    lines = _FailIfReadPastLimit(
        header=(
            "feature_family_id\tsample_stem\tstatus\tapex_rt\tpeak_start_rt\t"
            "peak_end_rt\trt_delta_sec\ttrace_quality\tscan_support_score\treason\n"
        ),
        rows=[
            "FAM001\tS1\tselected\t1.0\t0.9\t1.1\t0.0\tgood\t1.0\tok\n",
            "FAM002\tS2\trescued\t2.0\t1.9\t2.1\t1.0\tgood\t0.9\tok\n",
            "FAM003\tS3\tmissing\t3.0\t2.9\t3.1\t2.0\tlow\t0.1\tno\n",
        ],
        allowed_data_rows=2,
    )

    result = preflight_tsv_artifact(
        lines,
        required_fields=CELLS_REQUIRED_FIELDS,
        sample_row_limit=2,
    )

    assert result["row_count"] == "2"
    assert result["sample_count"] == "2"
    assert result["family_count"] == "2"
    assert result["missing_required_fields"] == ""
    assert result["artifact_status"] == "present_hash_unpinned"


def test_preflight_reports_missing_required_fields() -> None:
    lines = iter(
        [
            "feature_family_id\tstatus\n",
            "FAM001\tselected\n",
        ]
    )

    result = preflight_tsv_artifact(
        lines,
        required_fields=CELLS_REQUIRED_FIELDS,
        sample_row_limit=10,
    )

    assert result["row_count"] == "1"
    assert result["artifact_status"] == "present_missing_required_fields"
    assert "sample_stem" in result["missing_required_fields"]
    assert "reason" in result["missing_required_fields"]


class _FailIfReadPastLimit:
    def __init__(
        self,
        *,
        header: str,
        rows: list[str],
        allowed_data_rows: int,
    ) -> None:
        self._lines = [header, *rows]
        self._allowed_total_reads = 1 + allowed_data_rows
        self._index = 0

    def __iter__(self) -> "_FailIfReadPastLimit":
        return self

    def __next__(self) -> str:
        if self._index >= self._allowed_total_reads:
            raise AssertionError("preflight consumed beyond sample_row_limit")
        if self._index >= len(self._lines):
            raise StopIteration
        line = self._lines[self._index]
        self._index += 1
        return line


def test_preflight_rejects_negative_sample_limit() -> None:
    with pytest.raises(ValueError, match="sample_row_limit"):
        preflight_tsv_artifact(
            iter(["feature_family_id\n"]),
            required_fields=frozenset({"feature_family_id"}),
            sample_row_limit=-1,
        )
