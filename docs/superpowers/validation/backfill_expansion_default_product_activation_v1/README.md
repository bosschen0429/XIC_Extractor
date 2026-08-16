# Backfill Expansion Candidate Replay v1

Status: `backfill_expansion_candidate_packet_held`.

This bundle is a candidate replay for the bounded 666-cell Backfill
expansion packet. It is not public default activation because
shift-aware standard-peak support and MS1 own-max evidence are not
wired into the per-cell evidence chain.

- Candidate replay cells: `666`.
- Rows: `20`.
- Dry-run written cells: `666`.
- Unused expected-diff rows: `0`.
- Candidate cells blocked from public authority: `666`.
- Earlier held cells outside authority: `263`.

The full replay matrix, full provenance, row summary, source summary,
candidate manifest, and expected-diff TSV stay externalized under
`output/validation/`. Version control keeps only this compact summary,
checks, and row manifest.

This retained packet is a historical integrity oracle only. The current checker
validates its stored counts and hashes; it cannot regenerate the classification
or grant writer authority. Any future authority requires a separate,
current original-owner rehydration contract plus an explicit expected-diff spec.
