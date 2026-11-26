import pytest

from xic_extractor.sample_metadata import (
    SampleMetadata,
    infer_sample_role,
)
from xic_extractor.untargeted_execution.partition.request import (
    IdentityContractError,
    compose_partition_context,
)


def test_infer_sample_role() -> None:
    assert infer_sample_role("Unknown", sample_text="QC_01") == "qc"
    assert infer_sample_role("Unknown", sample_text="NAA_QC_01") == "qc"
    assert infer_sample_role("Unknown", sample_text="Pooled_QC1") == "qc"
    assert infer_sample_role("Unknown", sample_text="Tissue_Pool_QC") == "qc"
    assert infer_sample_role("Unknown", sample_text="Solvent_Blank") == "blank"
    assert infer_sample_role("Unknown", sample_text="Solvent_Wash") == "other"
    assert infer_sample_role("Unknown", sample_text="Calibrator_Level_1") == "std"
    assert infer_sample_role("Unknown", sample_text="STD_100uM") == "std"

    assert infer_sample_role("QC", sample_text="Run01") == "qc"
    assert infer_sample_role("Blank", sample_text="Run02") == "blank"
    assert infer_sample_role("Standard", sample_text="Run03") == "std"
    assert infer_sample_role("Unknown", sample_text="Subject_001") == "sample"

    assert infer_sample_role("", sample_text="Subject_002") == "sample"
    assert infer_sample_role(None, sample_text="") == "other"


def test_partition_context_rejects_duplicate_injection_order() -> None:
    rows = (
        SampleMetadata(
            sample_label="S1",
            raw_stem="S1",
            injection_order=1,
            batch_id="Batch1",
        ),
        SampleMetadata(
            sample_label="S2",
            raw_stem="S2",
            injection_order=1,
            batch_id="Batch1",
        ),
    )
    with pytest.raises(
        IdentityContractError, match="injection_order values must be unique"
    ):
        compose_partition_context(
            method_subject=None,
            settings=None,
            paths_by_stem={},
            metadata_by_stem={row.raw_stem: row for row in rows},
            source_expectations={},
            peak_map={},
            receipt_map={},
            signal_processing_profile=None,
        )


def test_infer_sample_role_extensions() -> None:
    assert infer_sample_role(None, sample_text="SDOLEK-1") == "other"
    assert infer_sample_role(None, sample_text="SDO-posttest") == "other"
    assert infer_sample_role(None, sample_text="SDOS_Tumor_1") == "sample"
    assert infer_sample_role(None, sample_text="ColumnWash") == "other"
    assert infer_sample_role(None, sample_text="StopFlow") == "other"
    assert infer_sample_role(None, sample_text="250ppb_Mix_STDs") == "std"
