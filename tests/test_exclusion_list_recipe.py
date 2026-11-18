import math

import openpyxl
import pytest

from scripts.run_exclusion_list import main
from xic_extractor.exclusion_list.recipe import (
    ExclusionListError,
    read_named_exclusions,
    read_recipe,
    read_targets,
)


def test_named_sheet_skips_rows_marked_x_and_reads_the_column_right_of_mz(tmp_path):
    path = tmp_path / "named.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "DNA"
    for row in [
        (None, "m/z", None),
        (None, 228.0979, "dC"),
        (None, 229.1012, "x"),
        (None, 114.0667, "\tDi-H-C"),
        (None, 130.0, None),
    ]:
        sheet.append(row)
    workbook.save(path)

    entries = read_named_exclusions(path, "DNA")

    assert [(e.name, e.mz) for e in entries] == [("dC", 228.0979), ("Di-H-C", 114.0667)]


def test_target_csv_with_bom_and_rt_columns(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text(  # Excel writes a BOM before the first header
        "m/z,t start (min),t stop (min)\n252.11,15.5,19.5\n", encoding="utf-8-sig"
    )
    plain = tmp_path / "plain.csv"
    plain.write_text("Compound,m/z\nSDO,311.0814\n", encoding="utf-8")

    (timed,) = read_targets(path)
    (whole_run,) = read_targets(plain)

    assert (timed.mz, timed.start, timed.stop) == (252.11, 15.5, 19.5)
    assert (whole_run.name, whole_run.start, whole_run.stop) == ("SDO", 0.0, math.inf)


def test_misspelled_recipe_key_is_reported(tmp_path):
    (tmp_path / "blank.raw").write_bytes(b"")
    (tmp_path / "t.csv").write_text("m/z\n300\n", encoding="utf-8")
    recipe = tmp_path / "recipe.toml"
    recipe.write_text(
        'blank = "blank.raw"\ntargets = ["t.csv"]\n'
        '[[lists]]\nname = "EX"\nscan_range = [145, 800]\nrt = true\n'
        "exclusion_ppm = 10\n",
        encoding="utf-8",
    )

    with pytest.raises(ExclusionListError, match="exclusion_ppm"):
        read_recipe(recipe)


def test_starter_recipe_uses_only_known_keys(tmp_path):
    recipe = tmp_path / "recipe.toml"

    assert main([str(recipe), "--new"]) == 0
    # Valid keys get as far as resolving the blank the user has yet to fill in.
    with pytest.raises(ExclusionListError, match="blank RAW not found"):
        read_recipe(recipe)
