from pathlib import Path

from xic_extractor.extraction.rt_prior_library import (
    LIBRARY_FIELDNAMES,
    LibraryEntry,
    load_library,
    write_pending_update,
)


def test_load_empty_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "lib.csv"
    p.write_text(
        "config_hash,target_label,role,istd_pair,median_delta_rt,"
        "sigma_delta_rt,median_abs_rt,sigma_abs_rt,n_samples,updated_at\n",
        encoding="utf-8",
    )
    assert load_library(p, "anyhash") == {}


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_library(tmp_path / "does_not_exist.csv", "h") == {}


def test_load_filters_by_config_hash(tmp_path: Path) -> None:
    p = tmp_path / "lib.csv"
    p.write_text(
        "config_hash,target_label,role,istd_pair,median_delta_rt,"
        "sigma_delta_rt,median_abs_rt,sigma_abs_rt,n_samples,updated_at\n"
        "aaaa1111,A,analyte,d3-A,0.10,0.02,,,10,2026-01-01T00:00:00\n"
        "bbbb2222,B,analyte,d3-B,0.05,0.01,,,8,2026-01-01T00:00:00\n"
        "aaaa1111,d3-A,ISTD,,,,9.03,0.18,10,2026-01-01T00:00:00\n",
        encoding="utf-8",
    )
    lib = load_library(p, "aaaa1111")
    assert set(lib.keys()) == {("A", "analyte"), ("d3-A", "ISTD")}
    entry = lib[("A", "analyte")]
    assert isinstance(entry, LibraryEntry)
    assert entry.median_delta_rt == 0.10
    assert entry.sigma_delta_rt == 0.02
    assert entry.n_samples == 10
    istd_entry = lib[("d3-A", "ISTD")]
    assert istd_entry.median_abs_rt == 9.03
    assert istd_entry.sigma_abs_rt == 0.18


def test_load_optional_float_whitespace_is_missing(tmp_path: Path) -> None:
    p = tmp_path / "lib.csv"
    p.write_text(
        "config_hash,target_label,role,istd_pair,median_delta_rt,"
        "sigma_delta_rt,median_abs_rt,sigma_abs_rt,n_samples,updated_at\n"
        "aaaa1111,A,analyte,d3-A, ,  ,,, 10 ,2026-01-01T00:00:00\n",
        encoding="utf-8",
    )
    entry = load_library(p, "aaaa1111")[("A", "analyte")]
    assert entry.median_delta_rt is None
    assert entry.sigma_delta_rt is None
    assert entry.n_samples == 10


def test_write_pending_round_trip(tmp_path: Path) -> None:
    lib = tmp_path / "lib.csv"
    lib.write_text("existing library content\n", encoding="utf-8")
    entries = [
        LibraryEntry(
            config_hash="aaaa1111",
            target_label="A",
            role="analyte",
            istd_pair="d3-A",
            median_delta_rt=0.10,
            sigma_delta_rt=0.02,
            median_abs_rt=None,
            sigma_abs_rt=None,
            n_samples=10,
            updated_at="2026-04-20T12:00:00",
        )
    ]
    pending = write_pending_update(lib, entries)
    assert pending == lib.with_suffix(".pending.csv")
    assert pending.exists()
    assert lib.read_text(encoding="utf-8") == "existing library content\n"
    lines = pending.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == ",".join(LIBRARY_FIELDNAMES)
    assert "0.100000" in lines[1]
    assert "0.020000" in lines[1]
    loaded = load_library(pending, "aaaa1111")
    assert loaded[("A", "analyte")].median_delta_rt == 0.1


def test_write_pending_update_writes_header_for_empty_entries(
    tmp_path: Path,
) -> None:
    lib = tmp_path / "nested" / "lib.csv"

    pending = write_pending_update(lib, [])

    assert pending == lib.with_suffix(".pending.csv")
    assert pending.read_text(encoding="utf-8").splitlines() == [
        ",".join(LIBRARY_FIELDNAMES)
    ]
