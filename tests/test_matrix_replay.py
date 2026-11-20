import csv
import json

from scripts.run_alignment import main


def test_matrix_only_joins_batch_qualified_groups_and_keeps_missing_cells(tmp_path):
    # Both batches use the same local IDs. Column ownership follows injection
    # order, not the order of files, area fields or reference-batch selection.
    def group(key, mz, areas):
        return {
            "key": key,
            "mz": mz,
            "rt": 2,
            "areas": areas,
            "physical_peaks": [[f"{next(iter(areas))}-{key}", "digest"]],
            "source_candidate_ids": [],
        }

    def save(name, data):
        path = tmp_path / name
        path.write_text(json.dumps(data))
        return str(path)

    def cells(batch, order, groups):
        return save(
            batch,
            {
                "schema": "finalized_matrix_cells",
                "batches": {batch: {"samples": order, "groups": groups}},
            },
        )

    old = cells(
        "old",
        {"a": 1, "c": 3},
        [
            group("shared", 301, {"c": 30, "a": None}),
            group("solo", 302, {"c": None, "a": 0}),
        ],
    )
    new = cells(
        "new",
        {"b": 2, "d": 4},
        [
            group("shared", 300, {"d": None, "b": 123.456789}),
        ],
    )
    pairing = {
        "schema": "matrix_batch_join",
        "reference_batch": "new",
        "rows": [[["new", "shared"], ["old", "shared"]], [["old", "solo"]]],
        "maps": {"old": None},
    }
    out = tmp_path / "replay"

    def run():
        return main(
            [
                "matrix",
                "--backfill",
                old,
                new,
                "--pairing",
                save("join", pairing),
                "--output-dir",
                str(out),
            ]
        )

    assert run() == 0
    matrix = out / "alignment_matrix.tsv"
    with matrix.open(newline="", encoding="utf-8") as stream:
        assert list(csv.reader(stream, delimiter="\t")) == [
            ["Mz", "RT", "a", "b", "c", "d"],
            ["300.0000", "2", "", "1.23457e+02", "3.00000e+01", ""],
            ["302.0000", "2", "0.00000e+00", "", "", ""],
        ]
    assert list(out.iterdir()) == [matrix]
    # A pairing file from a different grouping must not silently drop a row.
    out = tmp_path / "stale"
    pairing["rows"].pop()
    assert run() == 2
    assert not (out / "alignment_matrix.tsv").exists()
