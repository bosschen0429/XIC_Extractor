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
            ("status", (("", "dR", "300", str(rt), *("detected" for _ in stems)),)),
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
    paths = {
        name: tmp_path / f"{name}.tsv"
        for name in ("matrix", "cells", "status", "review")
    }
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
    # An unpaired old row keeps its own areas but uses the reference batch's RT.
    assert [[float(v) if v else None for v in row] for row in matrix[1:]] == [
        [300, 10, 11, 12, 13, 14, 15],
        [300, 30, None, None, 23, 24, 25],
        [300, 20, 21, 22, None, None, None],
    ]

    status = tables["status"]
    assert status[3][4:] == ["detected", "detected"] + ["not_searched"] * 3
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


def test_a_paired_group_joins_its_reference_row_in_the_explanation(tmp_path):
    import hashlib

    from tests.alignment_explanation_fixtures import explanation_summary
    from tests.inspector_fixtures import NEUTRAL_LOSS_BY_TAG
    from xic_extractor.alignment_explanation.reader import read_review_evidence
    from xic_extractor.alignment_explanation.writer import ExplanationReportWriter

    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("matrix", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    samples = [{"sample_key": s, "sample_stem": s} for s in ("n1", "o1")]

    def member(sample):
        return {
            "sample_key": sample,
            "subject_kind": "peak_hypothesis",
            "subject_id": f"h-{sample}",
            "physical_peak_keys": [f"p-{sample}"],
        }

    with ExplanationReportWriter(report, review_only=True) as writer:
        writer.declare_samples(samples)
        for sample, apex in (("n1", 10.0), ("o1", 10.3)):
            writer.add_peak(sample, f"p-{sample}", (300.0, apex, 20.0))
        writer.add_final_group(
            "new-group",
            feature_id="F000001",
            disposition="established",
            members=[member("n1")],
            center_mz=300.0,
            ppm=20.0,
        )
        writer.extend_final_group(
            "new-group", disposition="provisional", members=[member("o1")]
        )
        writer.set_row_destination("new-group", "matrix")
        for sample in ("n1", "o1"):
            writer.add_cells(
                "new-group",
                [
                    {
                        "sample_key": sample,
                        "variant": "numeric",
                        "value": 100.0,
                        "apex_rt_min": {"n1": 10.0, "o1": 10.3}[sample],
                        "observation_ids": [f"p-{sample}"],
                    }
                ],
            )
        writer.finalize(
            explanation_summary(
                {
                    "samples": samples,
                    "neutral_loss_by_tag": NEUTRAL_LOSS_BY_TAG,
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                },
                review_only=True,
            )
        )

    row = read_review_evidence(report).groups["F000001"]
    assert row.disposition == "provisional"
    assert sorted(item["sample_key"] for item in row.members) == ["n1", "o1"]
