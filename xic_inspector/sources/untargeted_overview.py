"""Reader-oriented counts and ordering from saved routes and cell decisions."""

from collections import Counter

from xic_extractor.diagnostics.alignment_explanation import (
    _object,
    open_review_population,
)
from xic_extractor.diagnostics.explanation_review import (
    SELECTION_FLAGS,
    attention_reason,
)
from xic_inspector.selection.review import reason_category


def review_overview(path, summary, scope=None):
    rows = {}
    coverage: Counter[str] = Counter()
    sample_count = len(summary["samples"])
    scope = (
        set(scope)
        if scope is not None
        else {s["canonical_sample_identity_id"] for s in summary["samples"]}
    )
    with open_review_population(path, summary) as population:
        for record in population:
            row, cells = record["row"], record["cells"]
            numeric = {
                c["canonical_sample_identity_id"]
                for c in cells
                if c["variant"] == "numeric"
            }
            unresolved = sum(c["variant"] == "unresolved" for c in cells)
            destination = row["route"]["destination"]
            local = [c for c in cells if c["canonical_sample_identity_id"] in scope]
            rows[row["row_key_id"]] = {
                "destination": destination,
                "numeric_count": len(numeric),
                "unresolved_count": unresolved,
                "review": {
                    "sample_count": len(scope),
                    "numeric_count": sum(c["variant"] == "numeric" for c in local),
                    "zero_count": sum(
                        c["variant"] == "numeric" and c.get("value") == 0 for c in local
                    ),
                    "decision_count": len(local),
                    "attention_count": 0,
                    "reasons": ["unresolved"]
                    if any(c["variant"] == "unresolved" for c in local)
                    else [],
                },
            }
            if destination == "matrix":
                coverage.update(numeric)
    matrix = [r for r in rows.values() if r["destination"] == "matrix"]
    calibration = summary.get("known_istd_reference", {}).get("calibration", {})
    return {
        "kind": "untargeted",
        "matrix_count": len(matrix),
        "unresolved_count": sum(r["unresolved_count"] > 0 for r in matrix),
        "complete_count": sum(r["numeric_count"] == sample_count for r in matrix),
        "archive_count": sum(r["destination"] == "archive" for r in rows.values()),
        "uncalibrated_count": sum(
            s.get("status") == "unavailable" for s in calibration.get("samples", [])
        ),
        "samples": [
            {
                "id": s["canonical_sample_identity_id"],
                "label": s.get("sample_stem"),
                "numeric_count": coverage[s["canonical_sample_identity_id"]],
            }
            for s in summary["samples"]
        ],
    }, rows


def indexed_review_facts(db, summary, rows, scope):
    """Read compact saved flags and reason links; never load curves or whole items."""
    scope_mask = sum(
        1 << i
        for i, sample in enumerate(summary["samples"])
        if sample["canonical_sample_identity_id"] in scope
    )
    facts = {}
    for item_id, identifier, packed in db.execute(
        "SELECT i.id,i.identifier,r.sample_facts FROM query_items i "
        "JOIN query_review r ON r.item=i.id"
    ):
        width = len(packed) // (len(SELECTION_FLAGS) + 1)
        masks = [
            int.from_bytes(packed[i : i + width], "little") & scope_mask
            for i in range(0, len(packed), width)
        ]
        present, _, attention, _, _, zeros, *_ = masks
        fact = dict(
            rows.get(identifier, {}).get(
                "review",
                {
                    "sample_count": len(scope),
                    "numeric_count": 0,
                    "zero_count": zeros.bit_count(),
                    "decision_count": 0,
                    "reasons": [],
                },
            )
        )
        fact["attention_count"] = attention.bit_count()
        fact["reasons"] = set(fact["reasons"])
        if present:
            facts[item_id] = fact

    # Shared records are decoded once, even when linked to many samples/items.
    records = {
        (kind, rid): _object(payload)
        for kind, rid, payload in db.execute(
            "SELECT kind,record_id,payload FROM records "
            "WHERE kind IN ('method_assessments','initial_edges','final_groups')"
        )
    }
    for item_id, gid in db.execute("SELECT id,group_id FROM query_items"):
        if item_id not in facts:
            continue
        group = records.get(("final_groups", gid), {})
        category = reason_category(group.get("disposition"), group=True)
        if category:
            facts[item_id]["reasons"].add(category)
    for item_id, item_kind, identifier, gid, sid, kind, rid in db.execute(
        "SELECT DISTINCT i.id,i.kind,i.identifier,i.group_id,"
        "s.sample_id,l.kind,l.record_id "
        "FROM query_items i JOIN query_members m ON m.item=i.id "
        "JOIN query_candidates c ON c.id=m.candidate "
        "JOIN query_samples s ON s.id=c.sample "
        "JOIN links l ON l.candidate_id=c.candidate_id AND l.sample_id=s.sample_id "
        "WHERE l.kind IN ('method_assessments','initial_edges')"
    ):
        if item_id not in facts or sid not in scope:
            continue
        record = records[kind, rid]
        if record.get("canonical_sample_identity_id", record.get("sample_id")) not in (
            None,
            sid,
        ):
            continue
        if record.get("group_hypothesis_id") not in (None, gid):
            continue
        if item_kind == "row" and record.get("row_key_id") not in (None, identifier):
            continue
        category = reason_category(attention_reason(kind, record))
        if category:
            facts[item_id]["reasons"].add(category)
    for fact in facts.values():
        fact["reasons"] = sorted(fact["reasons"])
    return facts


def ordered_review_items(db, rows, scope):
    selected = []
    for item, kind, identifier in db.execute(
        "SELECT id,kind,identifier FROM query_items"
    ):
        facts = rows.get(identifier, {})
        destination = facts.get("destination")
        if (
            scope == "all"
            or scope == "candidates"
            and kind == "candidate"
            or scope == destination
            or scope == "unresolved"
            and destination == "matrix"
            and facts["unresolved_count"]
        ):
            selected.append((item, facts))
    selected.sort(
        key=lambda value: (
            -value[1].get("unresolved_count", 0),
            -value[1].get("numeric_count", 0),
            value[0],
        )
    )
    return [item for item, _ in selected]
