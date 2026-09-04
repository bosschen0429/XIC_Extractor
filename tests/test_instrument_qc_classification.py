from pathlib import Path

from xic_extractor.instrument_qc.classification import (
    InstrumentQCClass,
    classify_instrument_qc_raw,
)


def test_sdolek_folder_classifies_as_sdolek() -> None:
    root = Path("C:/data/batch")
    raw = root / "SDOLEK" / "SDO LEK - 1.raw"

    assert classify_instrument_qc_raw(raw, root) == InstrumentQCClass.SDOLEK


def test_sdolek_filename_classifies_as_sdolek() -> None:
    root = Path("C:/data/batch")
    raw = root / "validation" / "SDOLEK-pretest.raw"

    assert classify_instrument_qc_raw(raw, root) == InstrumentQCClass.SDOLEK


def test_biological_root_raw_is_not_instrument_qc() -> None:
    root = Path("C:/data/batch")
    raw = root / "TumorBC2257_DNA.raw"

    assert classify_instrument_qc_raw(raw, root) is None


def test_non_sdolek_folders_are_not_sdolek_instrument_qc() -> None:
    root = Path("C:/data/batch")

    for folder in ("RNA", "Pairs", "validation", "except sample", "STDs"):
        raw = root / folder / "TumorBC2257_DNA.raw"
        assert classify_instrument_qc_raw(raw, root) is None


def test_classification_is_path_only_and_does_not_require_existing_file() -> None:
    root = Path("C:/missing/batch")
    raw = root / "SDOLEK" / "missing.raw"

    assert classify_instrument_qc_raw(raw, root) == InstrumentQCClass.SDOLEK
