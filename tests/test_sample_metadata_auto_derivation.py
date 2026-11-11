import pytest

from xic_extractor.sample_metadata import (
    SampleMetadata,
    infer_sample_role,
)
from xic_extractor.untargeted_execution.partition.request import (
    IdentityContractError,
    _metadata_by_stem,
)


def test_infer_sample_role() -> None:
    # 1. Keywords in sample_name / comment
    assert infer_sample_role("Unknown", sample_name="QC_01") == "qc"
    assert infer_sample_role("Unknown", sample_name="NAA_QC_01") == "qc"
    assert infer_sample_role("Unknown", sample_name="Pooled_QC1") == "qc"
    assert infer_sample_role("Unknown", sample_name="Tissue_Pool_QC") == "qc"
    assert infer_sample_role("Unknown", sample_name="Solvent_Blank") == "blank"
    assert infer_sample_role("Unknown", sample_name="Solvent_Wash") == "other"
    assert infer_sample_role("Unknown", sample_name="Calibrator_Level_1") == "std"
    assert infer_sample_role("Unknown", sample_name="STD_100uM") == "std"

    # 2. Thermo SampleType
    assert infer_sample_role("QC", sample_name="Run01") == "qc"
    assert infer_sample_role("Blank", sample_name="Run02") == "blank"
    assert infer_sample_role("Standard", sample_name="Run03") == "std"
    assert infer_sample_role("Unknown", sample_name="Subject_001") == "sample"

    # 3. Fallbacks
    assert infer_sample_role("", sample_name="Subject_002") == "sample"
    assert infer_sample_role(None, sample_name="") == "other"


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


def test_infer_sample_role_extensions() -> None:
    assert infer_sample_role(None, sample_name="SDOLEK-1") == "other"
    assert infer_sample_role(None, sample_name="SDO-posttest") == "other"
    assert infer_sample_role(None, sample_name="SDOS_Tumor_1") == "sample"
    assert infer_sample_role(None, sample_name="ColumnWash") == "other"
    assert infer_sample_role(None, sample_name="StopFlow") == "other"
    assert infer_sample_role(None, sample_name="250ppb_Mix_STDs") == "std"
