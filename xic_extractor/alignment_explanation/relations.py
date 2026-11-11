"""How stored records relate to Discovery candidates, derived when a report is read.

The report stores each fact once; which candidates explain a group or a curve
follows from those facts:

- a member relates the candidates whose terminal hypothesis it is, and the
  candidates bound to one of its peaks;
- a peak binds the candidate whose terminal peak it is, and the candidates of a
  Backfill SUPPORT assessment that lists the peak (Backfill has no terminal);
- an initial group keeps only candidates of the method subjects its
  hypothesis members belong to;
- a curve relates its own candidate, or else the candidates bound to its peak
  and those of its provenance method subject.

Each relation is computed into a TEMP table the first time a connection asks
for it. Nothing is written into a report, so the rule stays with the reader.
"""

from __future__ import annotations

import sqlite3

_PEAK_CANDIDATES = """
    SELECT terminal_peak AS peak, id AS candidate FROM main.candidates
    WHERE terminal_peak IS NOT NULL
    UNION
    SELECT j.value, c.id FROM main.method_assessments a, json_each(a.peaks) j
    JOIN main.candidates c
      ON c.method_subject = a.method_subject AND c.sample = a.sample
    WHERE a.detector_invocation_id = 'untargeted-cid-nl-backfill'
      AND a.method_result = 'SUPPORT'"""

_MEMBER_CANDIDATES = """
    SELECT m.id AS member, c.id AS candidate FROM main.members m
    JOIN main.candidates c
      ON c.sample = m.sample AND c.hypothesis_id = m.subject_id
    WHERE m.subject_kind = 'peak_hypothesis'
    UNION
    SELECT m.id, pc.candidate FROM main.members m
    JOIN main.peaks p ON p.sample = m.sample AND p.physical_peak_key = m.subject_id
    JOIN temp.peak_candidates pc ON pc.peak = p.id
    WHERE m.subject_kind IS NOT 'peak_hypothesis'
    UNION
    SELECT m.id, pc.candidate FROM main.members m, json_each(m.peaks) j
    JOIN temp.peak_candidates pc ON pc.peak = j.value"""

_GROUP_CANDIDATES = """
    SELECT DISTINCT m.final_group AS group_id, mc.candidate
    FROM temp.member_candidates mc JOIN main.members m ON m.id = mc.member
    WHERE m.final_group IS NOT NULL"""

_INITIAL_GROUP_CANDIDATES = """
    WITH methods AS (
        SELECT DISTINCT m.initial_group AS group_id, c.method_subject
        FROM main.members m
        JOIN main.candidates c
          ON c.sample = m.sample AND c.hypothesis_id = m.subject_id
        WHERE m.subject_kind = 'peak_hypothesis' AND m.initial_group IS NOT NULL)
    SELECT DISTINCT m.initial_group AS group_id, mc.candidate
    FROM temp.member_candidates mc
    JOIN main.members m ON m.id = mc.member
    JOIN main.candidates c ON c.id = mc.candidate
    JOIN methods ON methods.group_id = m.initial_group
      AND methods.method_subject = c.method_subject"""

_CURVE_CANDIDATES = """
    SELECT cu.payload_id, c.id AS candidate FROM main.curves cu
    JOIN main.samples s ON s.sample_key = cu.sample_id
    JOIN main.candidates c ON c.candidate_id = cu.candidate_id AND c.sample = s.id
    WHERE cu.candidate_id IS NOT NULL
    UNION
    SELECT cu.payload_id, pc.candidate FROM main.curves cu
    JOIN main.samples s ON s.sample_key = cu.sample_id
    JOIN main.peaks p
      ON p.sample = s.id AND p.physical_peak_key = cu.physical_peak_key
    JOIN temp.peak_candidates pc ON pc.peak = p.id
    WHERE cu.candidate_id IS NULL
    UNION
    SELECT cu.payload_id, c.id FROM main.curves cu
    JOIN main.samples s ON s.sample_key = cu.sample_id
    JOIN main.candidates c ON c.method_subject = cu.method_subject AND c.sample = s.id
    WHERE cu.candidate_id IS NULL AND cu.method_subject IS NOT NULL"""

# name: (query, prerequisites, index columns)
_RELATIONS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "peak_candidates": (_PEAK_CANDIDATES, (), ("peak",)),
    "member_candidates": (_MEMBER_CANDIDATES, ("peak_candidates",), ("member",)),
    "group_candidates": (
        _GROUP_CANDIDATES,
        ("member_candidates",),
        ("group_id", "candidate"),
    ),
    "initial_group_candidates": (
        _INITIAL_GROUP_CANDIDATES,
        ("member_candidates",),
        ("group_id", "candidate"),
    ),
    "curve_candidates": (
        _CURVE_CANDIDATES,
        ("peak_candidates",),
        ("payload_id", "candidate"),
    ),
}


def relation(db: sqlite3.Connection, name: str) -> str:
    """Compute a relation on this connection once; return its table name."""
    query, prerequisites, columns = _RELATIONS[name]
    exists = db.execute(
        "SELECT 1 FROM temp.sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if exists is None:
        for prerequisite in prerequisites:
            relation(db, prerequisite)
        db.execute(f"CREATE TEMP TABLE {name} AS {query}")
        for column in columns:
            db.execute(f"CREATE INDEX temp.{name}_{column} ON {name}({column})")
    return f"temp.{name}"
