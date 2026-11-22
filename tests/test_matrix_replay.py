import csv
from dataclasses import replace

from scripts.run_alignment import main
from xic_extractor.alignment.batch_correspondence import (
    BatchCorrespondence,
    BatchMap,
    FeatureCorrespondence,
)
from xic_extractor.diagnostics.matrix_replay import save_matrix_cells, save_matrix_join
from xic_extractor.output.alignment.tsv_writer import (
    BatchRowSource,
    FinalMatrixValues,
    UntargetChildOutputs,
    UntargetGroupRows,
)
from xic_extractor.sample_metadata import SampleMetadata


def test_matrix_only_joins_batch_qualified_groups_and_keeps_missing_cells(tmp_path):
    def group(key, mz, sample, areas):
        return key, UntargetGroupRows(
            "matrix",
            ((f"{sample}-{key}", "digest"),),
            (),
            rows=(),
            matrix=FinalMatrixValues(mz, 2, areas),
        )

    def cells(batch, order, groups):
        keys = {rows: key for key, rows in groups}
        path = tmp_path / f"{batch}.pickle.gz"
        save_matrix_cells(
            path,
            (
                BatchRowSource(
                    batch,
                    tuple(
                        SampleMetadata(stem, stem, number, batch_id=batch)
                        for stem, number in order
                    ),
                    (UntargetChildOutputs(tuple(keys)),),
                    keys.__getitem__,
                ),
            ),
        )
        return str(path)

    old = cells(
        "old",
        (("a", 1), ("c", 3)),
        [group("shared", 301, "c", (None, 30)), group("solo", 302, "a", (0, None))],
    )
    new = cells(
        "new", (("b", 2), ("d", 4)), [group("shared", 300, "b", (123.456789, None))]
    )
    features = (
        FeatureCorrespondence("new", "shared", "paired", (("old", "shared"),), 2, ()),
        FeatureCorrespondence("old", "shared", "paired", (("new", "shared"),), 2, ()),
        FeatureCorrespondence("old", "solo", "unpaired_no_map", (), None, ()),
    )
    pairing = BatchCorrespondence(
        "new",
        (BatchMap("old", (), {}, None, (), (), None, {}, ()),),
        features,
    )
    out = tmp_path / "replay"

    def run(*join_args):
        return main(
            [
                "matrix",
                "--backfill",
                old,
                new,
                *join_args,
                "--output-dir",
                str(out),
            ]
        )

    def save(name, correspondence):
        path = tmp_path / name
        save_matrix_join(path, correspondence)
        return str(path)

    assert run("--pairing", save("join", pairing)) == 0
    matrix = out / "alignment_matrix.tsv"
    with matrix.open(newline="", encoding="utf-8") as stream:
        assert list(csv.reader(stream, delimiter="\t")) == [
            ["Mz", "RT", "a", "b", "c", "d"],
            ["300.0000", "2", "", "1.23457e+02", "3.00000e+01", ""],
            ["302.0000", "2", "0.00000e+00", "", "", ""],
        ]
    assert list(out.iterdir()) == [matrix]
    out = tmp_path / "missing-join"
    assert run() == 2
    assert not (out / "alignment_matrix.tsv").exists()

    out = tmp_path / "stale"
    stale = replace(pairing, features=features[:2])
    assert run("--pairing", save("stale-join", stale)) == 2
    assert not (out / "alignment_matrix.tsv").exists()
