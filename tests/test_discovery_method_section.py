import pytest

from gui.sections.discovery_method_section import (
    ADVANCED_NUMERIC_FIELDS,
    POSITIVE_DISCOVERY_OVERRIDE_FIELDS,
    DiscoveryMethodSection,
)
from xic_extractor.presets import apply_to_discovery, load_preset
from xic_extractor.presets.apply import _POSITIVE_FLOAT_FIELDS


def test_discovery_method_positive_overrides_match_discovery_validator() -> None:
    gui_numeric_fields = {name for name, *_ in ADVANCED_NUMERIC_FIELDS}

    assert POSITIVE_DISCOVERY_OVERRIDE_FIELDS == (
        _POSITIVE_FLOAT_FIELDS & gui_numeric_fields
    )


@pytest.mark.parametrize("field", sorted(POSITIVE_DISCOVERY_OVERRIDE_FIELDS))
def test_discovery_method_clamps_zero_positive_overrides(
    field: str,
    qtbot,
) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)

    section.set_override(field, 0.0)

    assert section.numeric_value(field) > 0
    assert section.get_values()["overrides"][field] > 0
    assert section.is_valid() is True
    _assert_discovery_accepts(section)


@pytest.mark.parametrize("field", sorted(POSITIVE_DISCOVERY_OVERRIDE_FIELDS))
def test_discovery_method_quarantines_persisted_zero_positive_overrides(
    field: str,
    qtbot,
) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)

    section.load({"preset": "dna_dr", "overrides": {field: 0.0}})

    assert section.numeric_value(field) > 0
    assert section.get_values()["overrides"][field] == 0.0
    assert section.is_valid() is False
    assert "已保存的方法覆寫無效" in section.validation_reason()


def test_discovery_method_allows_zero_rt_lower_bound(qtbot) -> None:
    section = DiscoveryMethodSection(("dna_dr",))
    qtbot.addWidget(section)

    section.set_override("rt_min", 0.0)

    assert section.is_valid() is True


def test_discovery_method_preset_display_names_and_preview(qtbot) -> None:
    presets = (
        "dna_dr",
        "dna_dr_product_ready",
        "rna_ribose",
        "rna_mer",
        "phosphorylation_h3po4",
        "glucuronidation",
        "hexose_glycosylation",
    )
    section = DiscoveryMethodSection(presets)
    qtbot.addWidget(section)

    combo = section._preset
    assert combo.count() == 3

    # Check that itemData has canonical keys and itemText has rich labels
    for idx, key in enumerate(("dna_dr", "rna_ribose", "rna_mer")):
        assert combo.itemData(idx) == key
        assert len(combo.itemText(idx)) > len(key)

    assert "DNA" in combo.itemText(0)
    assert "116.0474 Da" in combo.itemText(0)

    # Check preview badges
    preview_text = section._preset_preview.text()
    assert "DNA_dR" in preview_text
    assert "116.047" in preview_text
    assert "NL 容差" in preview_text
    assert "Precursor 容差" in preview_text
    assert "RT" in preview_text


def _assert_discovery_accepts(section: DiscoveryMethodSection) -> None:
    apply_to_discovery(
        load_preset("dna_dr"),
        explicit_tuning_overrides=section.get_values()["overrides"],
    )
