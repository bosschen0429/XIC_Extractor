from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from xic_extractor.extraction.untargeted_cid_nl_real_data_inputs import (
    IdentityContractError,
    _metadata_by_stem,
)
from xic_extractor.raw_reader import (
    RawSampleHeader,
    derive_sample_metadata_from_raw_files,
)
from xic_extractor.sample_metadata import (
    SampleMetadata,
    infer_sample_role,
)


def test_infer_sample_role() -> None:
    # 1. Keywords in sample_name / comment
    assert infer_sample_role("Unknown", sample_name="QC_01") == "qc"
    assert infer_sample_role("Unknown", sample_name="NAA_QC_01") == "qc"
    assert infer_sample_role("Unknown", sample_name="Pooled_QC1") == "pooled_qc"
    assert infer_sample_role("Unknown", sample_name="Tissue_Pool_QC") == "pooled_qc"
    assert infer_sample_role("Unknown", sample_name="Solvent_Blank") == "blank"
    assert infer_sample_role("Unknown", sample_name="Solvent_Wash") == "solvent"
    assert (
        infer_sample_role("Unknown", sample_name="Calibrator_Level_1") == "calibrator"
    )
    assert infer_sample_role("Unknown", sample_name="STD_100uM") == "calibrator"

    # 2. Thermo SampleType
    assert infer_sample_role("QC", sample_name="Run01") == "qc"
    assert infer_sample_role("Blank", sample_name="Run02") == "blank"
    assert infer_sample_role("Standard", sample_name="Run03") == "calibrator"
    assert infer_sample_role("Unknown", sample_name="Subject_001") == "study_sample"

    # 3. Fallbacks
    assert infer_sample_role("", sample_name="Subject_002") == "study_sample"
    assert infer_sample_role(None, sample_name="") == "unknown"


def test_derive_sample_metadata_single_batch() -> None:
    raw_a = Path("/data/batch1/SampleA.raw")
    raw_b = Path("/data/batch1/SampleB.raw")
    raw_qc = Path("/data/batch1/QC1.raw")

    headers = {
        raw_a: RawSampleHeader(
            raw_path=str(raw_a),
            raw_stem="SampleA",
            sample_name="Sample A",
            sample_id="1",
            row_number=10,
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 1, 10, 0),
            vial="A:01",
            comment="",
        ),
        raw_b: RawSampleHeader(
            raw_path=str(raw_b),
            raw_stem="SampleB",
            sample_name="Sample B",
            sample_id="2",
            row_number=20,
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 1, 11, 0),
            vial="A:02",
            comment="",
        ),
        raw_qc: RawSampleHeader(
            raw_path=str(raw_qc),
            raw_stem="QC1",
            sample_name="QC1",
            sample_id="3",
            row_number=1,
            sample_type="QC",
            creation_date=datetime(2026, 1, 1, 9, 0),
            vial="A:03",
            comment="",
        ),
    }

    with patch(
        "xic_extractor.raw_reader.read_raw_sample_header",
        side_effect=lambda p, dll_dir: headers[p],
    ):
        rows = derive_sample_metadata_from_raw_files(
            [raw_a, raw_b, raw_qc],
            dll_dir=Path("/dummy/dll"),
        )

    assert len(rows) == 3
    # Should be sorted by row_number ascending: QC1 (1), SampleA (10), SampleB (20)
    assert [r.raw_stem for r in rows] == ["QC1", "SampleA", "SampleB"]
    assert [r.injection_order for r in rows] == [1, 10, 20]
    assert [r.sample_role for r in rows] == ["qc", "study_sample", "study_sample"]
    assert all(r.batch_id == "batch1" for r in rows)


def test_derive_sample_metadata_multi_batch_reset() -> None:
    raw_b1_1 = Path("/data/Batch_1/Sample1.raw")
    raw_b1_2 = Path("/data/Batch_1/Sample2.raw")
    raw_b2_1 = Path("/data/Batch_2/Sample3.raw")
    raw_b2_2 = Path("/data/Batch_2/Sample4.raw")

    headers = {
        raw_b1_1: RawSampleHeader(
            raw_path=str(raw_b1_1),
            raw_stem="Sample1",
            sample_name="S1",
            sample_id="1",
            row_number=1,
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 1, 10, 0),
            vial="A:01",
            comment="",
        ),
        raw_b1_2: RawSampleHeader(
            raw_path=str(raw_b1_2),
            raw_stem="Sample2",
            sample_name="S2",
            sample_id="2",
            row_number=2,
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 1, 11, 0),
            vial="A:02",
            comment="",
        ),
        raw_b2_1: RawSampleHeader(
            raw_path=str(raw_b2_1),
            raw_stem="Sample3",
            sample_name="S3",
            sample_id="3",
            row_number=1,  # Reset in batch 2
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 2, 10, 0),
            vial="B:01",
            comment="",
        ),
        raw_b2_2: RawSampleHeader(
            raw_path=str(raw_b2_2),
            raw_stem="Sample4",
            sample_name="S4",
            sample_id="4",
            row_number=2,  # Reset in batch 2
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 2, 11, 0),
            vial="B:02",
            comment="",
        ),
    }

    with patch(
        "xic_extractor.raw_reader.read_raw_sample_header",
        side_effect=lambda p, dll_dir: headers[p],
    ):
        rows = derive_sample_metadata_from_raw_files(
            [raw_b1_1, raw_b1_2, raw_b2_1, raw_b2_2],
            dll_dir=Path("/dummy/dll"),
        )

    assert len(rows) == 4
    batch1_rows = [r for r in rows if r.batch_id == "Batch_1"]
    batch2_rows = [r for r in rows if r.batch_id == "Batch_2"]

    assert [r.injection_order for r in batch1_rows] == [1, 2]
    assert [r.injection_order for r in batch2_rows] == [1, 2]

    # Test _metadata_by_stem: multi-batch duplicate injection_orders are valid
    meta_dict = _metadata_by_stem(rows)
    assert len(meta_dict) == 4


def test_metadata_by_stem_rejects_duplicate_injection_order_within_same_batch() -> None:
    rows = (
        SampleMetadata(
            sample_name="S1",
            raw_stem="S1",
            injection_order=1,
            batch_id="Batch1",
        ),
        SampleMetadata(
            sample_name="S2",
            raw_stem="S2",
            injection_order=1,  # Duplicate in same batch
            batch_id="Batch1",
        ),
    )
    with pytest.raises(
        IdentityContractError, match="injection orders must be complete and unique"
    ):
        _metadata_by_stem(rows)


def test_derive_sample_metadata_creation_date_fallback() -> None:
    raw_1 = Path("/data/batch/S1.raw")
    raw_2 = Path("/data/batch/S2.raw")

    headers = {
        raw_1: RawSampleHeader(
            raw_path=str(raw_1),
            raw_stem="S1",
            sample_name="Unknown",  # Placeholder name
            sample_id="",
            row_number=None,  # Missing row number
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 1, 12, 0),
            vial="",
            comment="",
        ),
        raw_2: RawSampleHeader(
            raw_path=str(raw_2),
            raw_stem="S2",
            sample_name="Unknown",  # Duplicate placeholder name
            sample_id="",
            row_number=None,
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 1, 11, 0),  # Earlier date
            vial="",
            comment="",
        ),
    }

    with patch(
        "xic_extractor.raw_reader.read_raw_sample_header",
        side_effect=lambda p, dll_dir: headers[p],
    ):
        rows = derive_sample_metadata_from_raw_files(
            [raw_1, raw_2],
            dll_dir=Path("/dummy/dll"),
        )

    # S2 had earlier creation date -> injection_order 1, S1 -> 2
    assert [r.raw_stem for r in rows] == ["S2", "S1"]
    assert [r.injection_order for r in rows] == [1, 2]
    # Sample names should fall back to raw_stem rather than keeping duplicate "Unknown"
    assert [r.sample_name for r in rows] == ["S2", "S1"]


def test_infer_sample_role_extensions() -> None:
    assert infer_sample_role(None, sample_name="SDOLEK-1") == "system_suitability"
    assert infer_sample_role(None, sample_name="SDO-posttest") == "system_suitability"
    assert infer_sample_role(None, sample_name="SDOS_Tumor_1") == "study_sample"
    assert infer_sample_role(None, sample_name="ColumnWash") == "solvent"
    assert infer_sample_role(None, sample_name="StopFlow") == "solvent"
    assert infer_sample_role(None, sample_name="250ppb_Mix_STDs") == "calibrator"


def test_derive_metadata_with_batch_id_ignores_subfolders() -> None:
    top = Path("/data/run/Sample1.raw")
    nested = Path("/data/run/except sample/Sample2.raw")
    headers = {
        path: RawSampleHeader(
            raw_path=str(path),
            raw_stem=path.stem,
            sample_name=path.stem,
            sample_id="1",
            row_number=None,
            sample_type="Unknown",
            creation_date=datetime(2026, 1, 1, 10 + index),
            vial="",
            comment="",
        )
        for index, path in enumerate((top, nested))
    }

    with patch(
        "xic_extractor.raw_reader.read_raw_sample_header",
        side_effect=lambda p, dll_dir: headers[p],
    ):
        rows = derive_sample_metadata_from_raw_files(
            [nested, top], dll_dir=Path("/dummy/dll"), batch_id="run"
        )

    assert [(r.raw_stem, r.batch_id, r.injection_order) for r in rows] == [
        ("Sample1", "run", 1),
        ("Sample2", "run", 2),
    ]
