"""Regroup an isolated scalar CID-NL workset with frozen drift and a peer-supported row.

The diagnostic JSON records complete group memberships, dispositions and the reason
for every primary-row selection or vacancy. No RAW acquisition or Matrix writes.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from xic_extractor.diagnostics.cid_nl_trace_partition import regroup_channel
from xic_extractor.diagnostics.stage_snapshot import read_grouping_inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs, _ = read_grouping_inputs(args.inputs)
    try:
        slots, groups = regroup_channel(inputs)
    except ValueError as error:
        parser.error(str(error))
    with args.output.open("x", encoding="utf8") as f:
        json.dump(
            dict(
                evidence="diagnostic_only",
                channel=inputs.worksets[0].key,
                slots=[asdict(s) for s in slots],
                groups=[
                    dict(
                        disposition=g.disposition,
                        evidence=g.evidence,
                        owners=g.owner_ids,
                    )
                    for g in groups
                ],
            ),
            f,
            indent=2,
            allow_nan=False,
        )


if __name__ == "__main__":
    main()
