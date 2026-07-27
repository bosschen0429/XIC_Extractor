# Package 2 Targeted CID-NL Method Validation v1

Doc placement: Repo validation artifact
Repo owner: `xic_extractor.extraction.targeted_diagnostic_method`
Doc kind: validation_artifact
Doc lifecycle: retained Package 2 closure evidence
Validation status: `diagnostic_only`
Validation tier: no-RAW engineering migration

This packet proves one bounded six-sample Targeted CID-NL method:
constrained synthetic Discovery, immutable diagnostic selection, existing
cross-sample Alignment, existing evidence-based Backfill, legacy-validated
projection, and the unchanged `xic_results.csv` writer. The fixture contains
two observed hypotheses, one eligible Backfill, one true missing, ambiguity,
and Discovery failure.

The frozen CSV is 403 bytes with SHA-256
`f0a471bd81046cefaa924d619b934957505ee22591b17820a73143f7b4126937`.
The existing writer is unchanged from the Package 1 base at Git blob
`6792d0fcd4d15b3cf23f2ce9e174fdbf96ed8399`. The DifferenceManifest records
exact-zero trace, hypothesis, Alignment membership, Backfill integration,
selected result, CSV bytes, Matrix emission, and writer behavior.

Targeted's process scheduler and Alignment's process runner consume the same
canonical method receipt through true spawned workers. Delivery requires both
runtime finalizations to succeed. Failure or cancellation quarantines the whole
successor attempt before writer invocation; exact-byte differences remain in
the attempt namespace; rollback keeps diagnostic evidence while returning the
active output and prior current delivery to legacy. Existing executable runtime
tests additionally cover worker loss, partial artifacts, conflicting replay,
resource cleanup, and terminal artifact disposition.

[`artifact_inventory.tsv`](artifact_inventory.tsv) names every retained oracle
and owner dependency. [`difference_manifest.tsv`](difference_manifest.tsv) is
machine-checked by the Package 2 method tests. The canonical manifest SHA-256 is
`377932de8bac5a929467a108658a3f9c5899a8b0e78fb7c05e277cacec0f2755`.

This packet grants no RAW/DLL, 8RAW/85RAW, Matrix/XLSX, publication, product
writer, schema, public CLI/config, maturity-tier, or production-readiness
authority. Package 3 remains inactive.
