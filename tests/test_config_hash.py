from pathlib import Path

from xic_extractor.configuration.hashing import (
    _settings_csv_bytes_with_overrides,
    compute_config_hash,
)
from xic_extractor.configuration.loader import load_config


def test_same_bytes_same_hash(tmp_path: Path) -> None:
    targets = tmp_path / "targets.csv"
    settings = tmp_path / "settings.csv"
    targets.write_bytes(b"label,mz\nA,100\n")
    settings.write_bytes(b"key,value\ndata_dir,C:/x\n")
    assert compute_config_hash(targets, settings) == compute_config_hash(
        targets, settings
    )


def test_different_targets_different_hash(tmp_path: Path) -> None:
    targets_a = tmp_path / "a.csv"
    targets_b = tmp_path / "b.csv"
    settings = tmp_path / "s.csv"
    targets_a.write_bytes(b"label\nA\n")
    targets_b.write_bytes(b"label\nB\n")
    settings.write_bytes(b"key\ndata_dir\n")
    assert compute_config_hash(targets_a, settings) != compute_config_hash(
        targets_b, settings
    )


def test_different_settings_different_hash(tmp_path: Path) -> None:
    targets = tmp_path / "targets.csv"
    settings_a = tmp_path / "a.csv"
    settings_b = tmp_path / "b.csv"
    targets.write_bytes(b"label\nA\n")
    settings_a.write_bytes(b"key,value\ndata_dir,first\n")
    settings_b.write_bytes(b"key,value\ndata_dir,second\n")
    assert compute_config_hash(targets, settings_a) != compute_config_hash(
        targets, settings_b
    )


def test_hash_uses_separator_between_files(tmp_path: Path) -> None:
    targets_a = tmp_path / "targets_a.csv"
    settings_a = tmp_path / "settings_a.csv"
    targets_b = tmp_path / "targets_b.csv"
    settings_b = tmp_path / "settings_b.csv"
    targets_a.write_bytes(b"a")
    settings_a.write_bytes(b"bc")
    targets_b.write_bytes(b"ab")
    settings_b.write_bytes(b"c")
    assert compute_config_hash(targets_a, settings_a) != compute_config_hash(
        targets_b, settings_b
    )


def test_hash_is_8_hex_chars(tmp_path: Path) -> None:
    t = tmp_path / "t.csv"
    s = tmp_path / "s.csv"
    t.write_bytes(b"x")
    s.write_bytes(b"y")
    h = compute_config_hash(t, s)
    assert len(h) == 8
    assert all(c in "0123456789abcdef" for c in h)


def test_load_config_hash_reflects_new_override_key(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "raw"
    dll_dir = tmp_path / "dll"
    data_dir.mkdir(parents=True)
    dll_dir.mkdir(parents=True)
    config_dir.mkdir()
    (config_dir / "settings.csv").write_text(
        "\ufeffkey,value,description\n"
        f"data_dir,{data_dir},data_dir\n"
        f"dll_dir,{dll_dir},dll_dir\n",
        encoding="utf-8",
    )
    (config_dir / "targets.csv").write_text(
        "\ufefflabel,mz,expected_rt_min,cid_nl_tag,role,istd_pair\n"
        "ISTD,258.1085,9,dR,istd,\n",
        encoding="utf-8",
    )

    base_config, _ = load_config(config_dir)
    override_a, _ = load_config(
        config_dir,
        settings_overrides={"injection_order_source": str(tmp_path / "order.csv")},
    )
    override_b, _ = load_config(
        config_dir,
        settings_overrides={"injection_order_source": str(tmp_path / "order.csv")},
    )

    assert override_a.config_hash != base_config.config_hash
    assert override_a.config_hash == override_b.config_hash


def test_settings_override_bytes_preserve_hash_input_contract(tmp_path: Path) -> None:
    settings = tmp_path / "settings.csv"
    settings.write_text(
        "\ufeffkey,value,description\nexisting,old,existing description\n",
        encoding="utf-8",
    )

    rendered = _settings_csv_bytes_with_overrides(
        settings,
        {"existing": "new", "added": "42"},
    )

    assert rendered == (
        b"\xef\xbb\xbf"
        b"key,value,description\r\n"
        b"existing,new,existing description\r\n"
        b"added,42,added\r\n"
    )
