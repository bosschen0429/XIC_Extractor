import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from xic_extractor.discovery.csv_reader import DiscoveryBatchInput
from xic_extractor.output.alignment.tsv_writer import (
    ALIGNMENT_CELLS_COLUMNS,
    ALIGNMENT_REVIEW_COLUMNS,
    BatchRowSource,
    FinalMatrixValues,
    UntargetGroupRows,
    write_batch_alignment_tsvs,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.untargeted_execution.batch_split import split_discovery_batch

SAMPLES = {"old": ("o1", "o2"), "new": ("n1", "n2", "n3")}
ORDER = {"o1": 1, "o2": 2, "n1": 3, "n2": 4, "n3": 5}


def _discovery_batch(stems):
    return DiscoveryBatchInput(
        sample_order=tuple(stems),
        peak_csvs={stem: Path(f"{stem}.csv") for stem in stems},
        raw_files={stem: None for stem in stems},
        event_csvs={stem: None for stem in stems},
        peak_receipts={stem: object() for stem in stems},
    )


def _metadata(batch_by_stem):
    return tuple(
        SampleMetadata(stem, stem, injection_order=ORDER.get(stem), batch_id=batch)
        for stem, batch in batch_by_stem.items()
    )


def test_one_declared_batch_is_the_input_itself():
    batch = _discovery_batch(("n1", "n2"))

    assert split_discovery_batch(batch, None) == (("", batch),)
    ((batch_id, only),) = split_discovery_batch(
        batch, _metadata({"n1": "B", "n2": "B"})
    )
    assert (batch_id, only) == ("B", batch)
    assert only is batch


def test_declared_batches_split_in_batch_id_order():
    batch = _discovery_batch(("n1", "o1", "n2", "o2"))

    split = split_discovery_batch(
        batch, _metadata({"n1": "new", "o1": "old", "n2": "new", "o2": "old"})
    )

    assert [(batch_id, part.sample_order) for batch_id, part in split] == [
        ("new", ("n1", "n2")),
        ("old", ("o1", "o2")),
    ]
    assert set(split[1][1].peak_csvs) == {"o1", "o2"}


def _group(batch_id, key, rt, *, destination="matrix", detected=None):
    stems = SAMPLES[batch_id]
    detected = stems if detected is None else detected
    review = dict.fromkeys(ALIGNMENT_REVIEW_COLUMNS, "")
    review.update(
        feature_id="",
        matrix_row_index="",
        neutral_loss_tag="dR",
        group_center_mz="300",
        group_center_rt=str(rt),
        detected_count=str(len(detected)),
        not_searched_count="0",
        unresolved_count="0",
        identity_reason=f"{batch_id}_reason",
        accepted_cell_count=str(len(detected)),
        accepted_backfill_count="0",
    )
    areas = {s: 10 * int(key[1:]) + ORDER[s] for s in detected}
    cells = tuple(
        (
            "",
            stem,
            "detected" if stem in detected else "absent",
            *(
                str(areas[stem]) if stem in areas and column == "area" else ""
                for column in ALIGNMENT_CELLS_COLUMNS[3:]
            ),
        )
        for stem in stems
    )
    return UntargetGroupRows(
        destination=destination,
        physical_peaks=tuple((f"{key}:{stem}", "d") for stem in detected),
        source_candidate_ids=tuple(f"{key}:{stem}" for stem in detected),
        matrix=FinalMatrixValues(300.0, rt, tuple(areas.get(s) for s in stems)),
        rows=(
            ("cells", cells),
            ("review", (tuple(review[column] for column in ALIGNMENT_REVIEW_COLUMNS),)),
        ),
    )


def _source(batch_id, groups):
    completed = SimpleNamespace(
        canonical_samples=tuple(
            SampleMetadata(stem, stem, injection_order=ORDER[stem])
            for stem in SAMPLES[batch_id]
        ),
        child_outputs=(SimpleNamespace(groups=tuple(groups)),),
    )

    def feature_key(rows):
        if rows.destination != "matrix":
            return None
        return rows.physical_peaks[0][0].split(":")[0]

    return BatchRowSource(
        batch_id, completed.canonical_samples, completed.child_outputs, feature_key
    )


def _write(tmp_path, sources, partners):
    paths = {name: tmp_path / f"{name}.tsv" for name in ("matrix", "cells", "review")}
    states = {key: "paired" for key in partners}
    for members in partners.values():
        states.update(dict.fromkeys(members, "paired"))
    count = write_batch_alignment_tsvs(
        paths,
        sources,
        reference_batch="new",
        partners=partners,
        states=states,
        to_reference_rt=lambda batch_id, rt: rt - 0.5,
    )
    tables = {}
    for name, path in paths.items():
        with path.open(encoding="utf-8", newline="") as handle:
            tables[name] = list(csv.reader(handle, delimiter="\t"))
    return count, tables


def test_paired_rows_share_one_row_and_single_batch_rows_keep_their_batch(tmp_path):
    sources = [
        _source("old", [_group("old", "O1", 10.5), _group("old", "O2", 20.5)]),
        _source("new", [_group("new", "N1", 10.0), _group("new", "N2", 30.0)]),
    ]

    count, tables = _write(tmp_path, sources, {("new", "N1"): (("old", "O1"),)})

    assert count == 3
    matrix = tables["matrix"]
    assert matrix[0] == ["Mz", "RT", "o1", "o2", "n1", "n2", "n3"]
    assert [[float(v) if v else None for v in row] for row in matrix[1:]] == [
        [300, 10, 11, 12, 13, 14, 15],
        [300, 30, None, None, 23, 24, 25],
        [300, 20, 21, 22, None, None, None],
    ]

    cells = [row for row in tables["cells"][1:] if row[0] == "F000002"]
    assert [(row[1], row[2]) for row in cells] == [
        ("o1", "not_searched"),
        ("o2", "not_searched"),
        ("n1", "detected"),
        ("n2", "detected"),
        ("n3", "detected"),
    ]

    review = {
        row[0]: dict(zip(tables["review"][0], row, strict=True))
        for row in tables["review"][1:]
    }
    paired = review["F000001"]
    assert paired["detected_count"] == "5"
    assert paired["not_searched_count"] == "0"
    assert paired["identity_reason"] == "new_reason;old_reason"
    assert (paired["batch_ids"], paired["batch_pairing"]) == ("new;old", "paired")
    single = review["F000003"]
    assert single["not_searched_count"] == "3"
    assert single["batch_ids"] == "old"


def test_a_partner_without_its_reference_row_is_not_dropped(tmp_path):
    sources = [
        _source("old", [_group("old", "O1", 10.5)]),
        _source("new", [_group("new", "N2", 30.0)]),
    ]

    with pytest.raises(ValueError, match="O1"):
        _write(tmp_path, sources, {("new", "N1"): (("old", "O1"),)})
