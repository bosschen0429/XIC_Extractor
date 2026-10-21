"""Classify docs/superpowers files into repo and Obsidian routing groups."""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.diagnostics import docs_scan as _docs_scan  # noqa: E402
from tools.diagnostics.docs_policy import (  # noqa: E402
    DOC_RETIRED_PLACEMENT_VALUES,
    DOC_ROUTING_AUTHORITY_REFERRER_PATHS,
    DOC_ROUTING_MECHANICAL_REFERRER_PREFIXES,
    PRODUCTIZATION_AUTHORITY_MANIFEST_REL,
    PRODUCTIZATION_SCHEMA_REL,
    PRODUCTIZATION_STATUS_INDEX_REL,
    DocClassification,
    classify_doc,
    classify_doc_path,
    doc_placement_is_non_repo,
    has_private_history_signal,
    infer_doc_kind_from_path,
    is_canonical_doc_owner_path,
    path_startswith_any,
    repo_owner_value,
    validation_readme_reference_scope,
)

DOC_ROUTING_REFERRER_EXCLUDE_PATTERNS = (
    re.compile(
        r"docs/superpowers/file-management/docs-cleanup/.*routing-manifest\.md$"
    ),
    re.compile(
        r"docs/superpowers/file-management/docs-cleanup/.*routing-manifest\.tsv$"
    ),
    re.compile(r"docs/superpowers/file-management/docs-cleanup/.*topic-clusters\.tsv$"),
    re.compile(r"output/docs-topic-indexes/[^/]+/README\.md$"),
)
DOC_ROUTE_OBSIDIAN_ORIGINAL = "obsidian_original"
DOC_ROUTE_REPO_DISTILLED_PLUS_OBSIDIAN_ORIGINAL = (
    "repo_distilled_plus_obsidian_original"
)
DOC_ROUTE_REPO_PRODUCT_DOC = "repo_product_doc"
DOC_ROUTE_NEEDS_DECISION = "needs_route_decision"
PRODUCTIZATION_OWNER_ROUTE = (
    "docs/product/productization.md; "
    f"{PRODUCTIZATION_STATUS_INDEX_REL}; "
    f"{PRODUCTIZATION_AUTHORITY_MANIFEST_REL}; "
    f"{PRODUCTIZATION_SCHEMA_REL}; "
    "docs/agent/product-validation-contract.md"
)
DOC_ROUTING_SIGNAL_PATTERNS = (
    ("command_evidence", re.compile(r"\b(?:git add|git commit|uv run|pytest)\b")),
    ("branch_or_pr_history", re.compile(r"\b(?:Branch|PR Body Seed|Commit Split):")),
    (
        "implementation_history",
        re.compile(
            r"\b(?:implementation plan|branch diary|closeout|"
            r"review rationale|command narrative)\b",
            re.IGNORECASE,
        ),
    ),
)
DOC_ROUTING_TOPIC_RULES = (
    (
        "architecture and cleanup",
        (
            "peak-pipeline cleanup",
            "peak pipeline cleanup",
            "cleanup foundation",
            "handoff-spine",
            "technical debt",
        ),
        "docs/architecture-contract.md",
    ),
    (
        "backfill and quant matrix",
        ("backfill", "quant matrix", "matrix", "autowrite"),
        "docs/product/backfill.md; docs/product/quant-matrix.md",
    ),
    (
        "discovery",
        ("discovery", "cid-nl", "untarget"),
        "docs/product/discovery.md",
    ),
    (
        "alignment",
        ("alignment", "matrix handoff", "owner", "gap-fill"),
        "docs/product/alignment.md",
    ),
    (
        "targeted selection",
        ("targeted", "expected-diff", "selected hypothesis"),
        "docs/product/targeted-selection.md",
    ),
    (
        "sample metadata and qc",
        (
            "sample-metadata",
            "sample_metadata",
            "sample metadata",
            "instrument-qc",
            "instrument_qc",
            "injection_order",
            "injection-order",
        ),
        "docs/product/sample-metadata-qc.md",
    ),
    (
        "evidence semantics",
        ("evidence", "score", "hypothesis", "identity", "trace"),
        "docs/product/evidence-spine.md; docs/lc-msms-evidence-rules.md",
    ),
    (
        "productization",
        ("productization", "maturity", "active lane", "productwriter"),
        PRODUCTIZATION_OWNER_ROUTE,
    ),
    (
        "run provenance",
        ("command", "replay", "manifest", "metadata"),
        "docs/product/run-provenance.md",
    ),
)
DOC_TOPIC_FOLDER_SLUGS = {
    "architecture and cleanup": "architecture-cleanup",
    "backfill and quant matrix": "backfill-and-quant-matrix",
    "discovery": "discovery",
    "alignment": "alignment",
    "targeted selection": "targeted-selection",
    "sample metadata and qc": "sample-metadata-qc",
    "evidence semantics": "evidence-semantics",
    "productization": "productization",
    "run provenance": "run-provenance",
    "docs workflow or historical context": "docs-workflow",
}
ROUTING_MANIFEST_FIELDS = (
    "path",
    "disposition",
    "doc_kind",
    "doc_kind_source",
    "doc_lifecycle",
    "doc_exit_rule",
    "lifecycle_status",
    "wiki_skill_route",
    "wiki_next_action",
    "doc_route",
    "repo_body_role",
    "digestion_status",
    "digestion_next_action",
    "support_retention_reason",
    "support_next_action",
    "information_value",
    "repo_owner_hint",
    "topic_key",
    "topic_role",
    "topic_owner_claim",
    "topic_subcontract_claim",
    "topic_cluster_size",
    "topic_cluster_status",
    "topic_cluster_sample",
    "obsidian_lane",
    "obsidian_original_hint",
    "repo_pointer_required",
    "referrer_status",
    "exact_referrers",
    "sample_referrers",
    "placement",
    "repo_owner",
    "required_before_move",
    "destructive_allowed_now",
    "candidate",
    "reason",
)
TOPIC_CLUSTER_MANIFEST_FIELDS = (
    "topic_key",
    "topic_cluster_status",
    "file_count",
    "topic_owner_count",
    "topic_owner_claim_count",
    "subcontract_count",
    "supporting_count",
    "candidate_count",
    "delegated_handoff_count",
    "repo_owner_hint",
    "suggested_repo_topic_folder",
    "suggested_obsidian_topic_folder",
    "topic_next_action",
    "owner_paths",
    "owner_claims",
    "subcontract_paths",
    "subcontract_claims",
    "supporting_sample_paths",
    "candidate_paths",
    "sample_paths",
    "digestion_review_count",
    "digestion_status_counts",
    "support_retention_counts",
    "bound_support_sample_paths",
    "compressible_support_sample_paths",
)
DOC_ROUTING_OWNER_TOPIC_RULES = (
    (
        "docs/product/backfill.md",
        "backfill and quant matrix",
        "docs/product/backfill.md; docs/product/quant-matrix.md",
    ),
    (
        "docs/product/quant-matrix.md",
        "backfill and quant matrix",
        "docs/product/backfill.md; docs/product/quant-matrix.md",
    ),
    (
        "docs/product/discovery.md",
        "discovery",
        "docs/product/discovery.md",
    ),
    (
        "docs/product/alignment.md",
        "alignment",
        "docs/product/alignment.md",
    ),
    (
        "docs/product/targeted-selection.md",
        "targeted selection",
        "docs/product/targeted-selection.md",
    ),
    (
        "docs/product/sample-metadata-qc.md",
        "sample metadata and qc",
        "docs/product/sample-metadata-qc.md",
    ),
    (
        "docs/product/evidence-spine.md",
        "evidence semantics",
        "docs/product/evidence-spine.md; docs/lc-msms-evidence-rules.md",
    ),
    (
        "docs/product/family-hypothesis-boundary.md",
        "evidence semantics",
        "docs/product/evidence-spine.md; docs/lc-msms-evidence-rules.md",
    ),
    (
        "docs/lc-msms-evidence-rules.md",
        "evidence semantics",
        "docs/product/evidence-spine.md; docs/lc-msms-evidence-rules.md",
    ),
    (
        "docs/product/productization.md",
        "productization",
        PRODUCTIZATION_OWNER_ROUTE,
    ),
    (
        "docs/superpowers/plans/2026-06-15-productization-control-plane.md",
        "productization",
        PRODUCTIZATION_OWNER_ROUTE,
    ),
    (
        "docs/product/run-provenance.md",
        "run provenance",
        "docs/product/run-provenance.md",
    ),
    (
        "docs/project-layout.md",
        "docs workflow or historical context",
        "docs/project-layout.md; docs/agent/obsidian-handoff-contract.md",
    ),
    (
        "docs/agent/obsidian-handoff-contract.md",
        "docs workflow or historical context",
        "docs/project-layout.md; docs/agent/obsidian-handoff-contract.md",
    ),
    (
        "docs/architecture-contract.md",
        "architecture and cleanup",
        "docs/architecture-contract.md",
    ),
)

DIGESTION_REVIEW_STATUSES = {
    "needs_route_decision",
    "needs_distillation_to_owner_and_obsidian",
    "duplicate_owner_review",
    "sub_contract_review",
    "owner_with_cleanup_candidates",
    "external_owner_with_cleanup_candidates",
    "owner_missing_for_candidates",
    "support_surface_review",
    "canonical_owner_with_support_review",
}


@dataclass(frozen=True)
class DocsRoutingCandidate:
    path: str
    disposition: str
    doc_kind: str
    doc_kind_source: str
    doc_lifecycle: str
    doc_exit_rule: str
    lifecycle_status: str
    metadata_status: str
    metadata_missing_fields: str
    wiki_skill_route: str
    wiki_next_action: str
    doc_route: str
    repo_body_role: str
    digestion_status: str
    digestion_next_action: str
    support_retention_reason: str
    support_next_action: str
    reason: str
    placement: str
    repo_owner: str
    information_value: str
    repo_owner_hint: str
    topic_key: str
    topic_role: str
    topic_owner_claim: str
    topic_subcontract_claim: str
    topic_cluster_size: int
    topic_cluster_status: str
    topic_cluster_sample: str
    obsidian_lane: str
    obsidian_original_hint: str
    repo_pointer_required: str
    referrer_status: str
    exact_referrers: int
    sample_referrers: str
    required_before_move: str
    destructive_allowed_now: str
    candidate: bool


def _is_docs_routing_scan_target(rel_path: str) -> bool:
    return classify_doc_path(rel_path).is_docs_routing_scan_target


def _path_startswith_any(rel_path: str, prefixes: Sequence[str]) -> bool:
    return path_startswith_any(rel_path, tuple(prefixes))


def _routing_signals(text: str) -> list[str]:
    signals: list[str] = []
    if has_private_history_signal(text):
        signals.append("private_history_signal")
    for label, pattern in DOC_ROUTING_SIGNAL_PATTERNS:
        if pattern.search(text):
            signals.append(label)
    return signals


def _topic_hint(path: str, text: str) -> tuple[str, str]:
    path_haystack = path.lower()
    productization_routing_paths = {
        "docs/superpowers/goals/readme.md",
        "docs/superpowers/plans/readme.md",
        "docs/superpowers/plans/2026-06-15-productization-control-plane.md",
        "docs/superpowers/pulse-reports/readme.md",
    }
    if path_haystack in productization_routing_paths:
        return (
            "productization",
            PRODUCTIZATION_OWNER_ROUTE,
        )
    if path_haystack == "docs/superpowers/readme.md":
        return (
            "docs workflow or historical context",
            "docs/project-layout.md; docs/agent/obsidian-handoff-contract.md",
        )
    docs_workflow_routing_paths = {
        "docs/superpowers/notes/2026-06-25-obsidian-migration-classification-inventory.md",
    }
    if (
        path_haystack.startswith("docs/superpowers/file-management/")
        or path_haystack.startswith("docs/superpowers/closeouts/")
        or path_haystack.startswith("docs/superpowers/handoffs/")
        or path_haystack in docs_workflow_routing_paths
    ):
        return (
            "docs workflow or historical context",
            "docs/project-layout.md; docs/agent/obsidian-handoff-contract.md",
        )
    repo_owner_haystack = repo_owner_value(text).lower().replace("\\", "/")
    for owner_hint, information_value, repo_owner_hint in DOC_ROUTING_OWNER_TOPIC_RULES:
        if owner_hint in repo_owner_haystack:
            return information_value, repo_owner_hint

    for information_value, needles, repo_owner_hint in DOC_ROUTING_TOPIC_RULES:
        if any(needle in path_haystack for needle in needles):
            return information_value, repo_owner_hint

    text_haystack = text[:4000].lower()
    for information_value, needles, repo_owner_hint in DOC_ROUTING_TOPIC_RULES:
        if any(needle in text_haystack for needle in needles):
            return information_value, repo_owner_hint
    return (
        "docs workflow or historical context",
        "docs/project-layout.md; docs/agent/obsidian-handoff-contract.md",
    )


def _topic_slug(topic_key: str) -> str:
    return DOC_TOPIC_FOLDER_SLUGS.get(
        topic_key,
        re.sub(r"[^a-z0-9]+", "-", topic_key.lower()).strip("-") or "uncategorized",
    )


def _topic_title(topic_key: str) -> str:
    return " ".join(
        "QC" if word.lower() == "qc" else word.capitalize()
        for word in topic_key.split()
    )


def _suggested_repo_topic_folder(topic_key: str) -> str:
    return f"output/docs-topic-indexes/{_topic_slug(topic_key)}/"


def _suggested_obsidian_topic_folder(topic_key: str) -> str:
    title = _topic_title(topic_key.replace("/", " "))
    return f"XIC/20 Archived Plans And Specs/Topic Archives/{title}/"


def _topic_next_action(status: str) -> str:
    if status == "potential_duplicate_owner":
        return (
            "resolve files claiming the same repo owner; keep one owner, demote "
            "siblings to bounded support or retire them, and regenerate an "
            "ignored topic index only if browsing support surfaces is still useful"
        )
    if status == "multiple_subtopic_owners":
        return (
            "confirm each owner-like file is a distinct sub-contract; point all "
            "of them back to the big-direction owner"
        )
    if status == "owner_plus_cleanup_candidates":
        return (
            "keep the owner, close out cleanup candidates, and route originals "
            "through Obsidian before any path move"
        )
    if status == "external_owner_with_cleanup_candidates":
        return (
            "use the external canonical owner named by repo_owner_hint; close "
            "cleanup candidates only after stable claims are absorbed and "
            "Obsidian originals/referrers are preserved"
        )
    if status == "owner_missing_for_candidates":
        return (
            "create or choose the canonical owner before moving candidate text "
            "to Obsidian"
        )
    if status == "multiple_support_surfaces":
        return (
            "keep support files only if they point at the owner and do not "
            "redefine the topic"
        )
    if status == "subcontracts_with_owner":
        return (
            "confirm each sub-contract still points at the canonical owner and "
            "does not duplicate another sub-contract"
        )
    return "no immediate topic consolidation needed"


def _obsidian_lane(path: str, disposition: str) -> str:
    if disposition not in {
        "formalize_then_obsidian",
        "needs_human_review",
        "invalid_repo_placement",
    }:
        return "none"
    if "/plans/" in path:
        return "XIC/20 Archived Plans And Specs/Topic Archives/<topic>/Plans"
    if "/specs/" in path:
        return (
            "XIC/20 Archived Plans And Specs/Topic Archives/<topic>/Specs And Designs"
        )
    if "/goals/" in path:
        return "XIC/20 Archived Plans And Specs/Topic Archives/<topic>/Goals"
    if "/reports/" in path or "/pulse-reports/" in path:
        return (
            "XIC/20 Archived Plans And Specs/Topic Archives/<topic>/Reports And Pulses"
        )
    if "/deepresearch/" in path:
        return "XIC/30 Research Notes/Deepresearch"
    if "/validation/" in path or "/fixtures/" in path:
        return "XIC/50 Validation Context/Run Narratives"
    return (
        "XIC/20 Archived Plans And Specs/Topic Archives/<topic>/"
        "Notes Decisions Closeouts"
    )


def _doc_route(disposition: str) -> str:
    if disposition == "invalid_repo_placement":
        return DOC_ROUTE_OBSIDIAN_ORIGINAL
    if disposition == "formalize_then_obsidian":
        return DOC_ROUTE_REPO_DISTILLED_PLUS_OBSIDIAN_ORIGINAL
    if disposition == "needs_human_review":
        return DOC_ROUTE_NEEDS_DECISION
    return DOC_ROUTE_REPO_PRODUCT_DOC


def _repo_body_role(route: str) -> str:
    if route == DOC_ROUTE_OBSIDIAN_ORIGINAL:
        return "original_not_repo"
    if route == DOC_ROUTE_REPO_DISTILLED_PLUS_OBSIDIAN_ORIGINAL:
        return "distilled_repo_claim"
    if route == DOC_ROUTE_REPO_PRODUCT_DOC:
        return "repo_source_of_truth"
    return "route_pending"


def _digestion_status(record: DocsRoutingCandidate, cluster_status: str) -> str:
    if record.doc_route == DOC_ROUTE_NEEDS_DECISION:
        return "needs_route_decision"
    if record.doc_route in {
        DOC_ROUTE_OBSIDIAN_ORIGINAL,
        DOC_ROUTE_REPO_DISTILLED_PLUS_OBSIDIAN_ORIGINAL,
    }:
        return "needs_distillation_to_owner_and_obsidian"
    if record.topic_role == "delegated_handoff":
        return "delegated_handoff_retention"
    if record.topic_role == "repo_subcontract_owner":
        if record.repo_owner and record.lifecycle_status == "declared":
            return "sub_contract_retained"
        return "sub_contract_review"
    if cluster_status == "potential_duplicate_owner":
        return "duplicate_owner_review"
    if cluster_status == "multiple_subtopic_owners":
        return "sub_contract_review"
    if cluster_status == "owner_plus_cleanup_candidates":
        return "owner_with_cleanup_candidates"
    if cluster_status == "external_owner_with_cleanup_candidates":
        if (
            record.topic_role == "repo_supporting_artifact"
            and record.repo_owner
            and record.lifecycle_status == "declared"
        ):
            return "support_surface_retained"
        return "external_owner_with_cleanup_candidates"
    if cluster_status == "owner_missing_for_candidates":
        return "owner_missing_for_candidates"
    if record.topic_role == "repo_supporting_artifact":
        if record.repo_owner and record.lifecycle_status == "declared":
            return "support_surface_retained"
        if cluster_status == "multiple_support_surfaces":
            return "support_surface_review"
        return "support_surface_retained"
    if record.topic_role == "repo_topic_owner":
        if cluster_status == "multiple_support_surfaces":
            return "canonical_owner_with_support_review"
        return "canonical_owner"
    return "route_retained_unclassified"


def _digestion_next_action(status: str) -> str:
    if status == "needs_route_decision":
        return (
            "choose a durable route before any Obsidian write, repo rewrite, "
            "move, or deletion"
        )
    if status == "needs_distillation_to_owner_and_obsidian":
        return (
            "distill stable repo claims into the canonical owner, preserve the "
            "Obsidian original pointer, then close lifecycle/referrers"
        )
    if status == "duplicate_owner_review":
        return (
            "merge or demote duplicate owner claimants so one repo owner carries "
            "the contract"
        )
    if status == "sub_contract_review":
        return (
            "confirm each owner-like file is a distinct sub-contract and point "
            "it back to the big-direction owner/index"
        )
    if status == "sub_contract_retained":
        return (
            "keep while the declared owner/lifecycle stay current; review only "
            "if the owner or exit rule becomes stale"
        )
    if status == "owner_with_cleanup_candidates":
        return (
            "keep the owner, absorb stable claims from cleanup candidates, and "
            "route originals through Obsidian"
        )
    if status == "external_owner_with_cleanup_candidates":
        return (
            "absorb stable claims into the external canonical owner named by "
            "repo_owner_hint, then route originals through Obsidian/referrer "
            "closeout"
        )
    if status == "owner_missing_for_candidates":
        return (
            "create or choose a canonical owner before moving candidate text to "
            "Obsidian"
        )
    if status == "support_surface_review":
        return (
            "verify the support surface points at the owner and does not redefine "
            "the topic; remove it after owner absorption and a clean referrer "
            "scan if it only preserves history"
        )
    if status == "canonical_owner_with_support_review":
        return (
            "keep the owner but review support files for backlinks, overlap, and "
            "exit rules"
        )
    if status == "delegated_handoff_retention":
        return "move active context to ignored handoff storage; do not track it"
    if status == "support_surface_retained":
        return "keep only while it remains a support surface with a clear owner"
    if status == "canonical_owner":
        return "no immediate digestion action unless owner role becomes stale"
    return "review manually before treating this file as digested"


def _support_retention_reason(record: DocsRoutingCandidate) -> str:
    if record.topic_role != "repo_supporting_artifact":
        return "not_support_surface"
    referrers = record.sample_referrers
    if any(path in referrers for path in DOC_ROUTING_AUTHORITY_REFERRER_PATHS):
        return "authority_or_status_anchor"
    if record.doc_lifecycle == "active":
        return "active_support_surface"
    if record.exact_referrers:
        if _only_mechanical_exact_referrers(record):
            return "mechanical_referrer_anchor"
        if _archived_retention_anchor(record):
            return "archived_retention_anchor"
        return "exact_referrer_bound_support"
    if _archived_retention_anchor(record):
        return "archived_retention_anchor"
    if record.doc_lifecycle == "archived":
        return "archived_compressible_support"
    return "ordinary_support_surface"


def _support_next_action(reason: str) -> str:
    if reason == "authority_or_status_anchor":
        return (
            "do not move or rewrite until the status index or authority "
            "manifest has a replacement compact artifact, hash update, and "
            "focused checker coverage"
        )
    if reason == "exact_referrer_bound_support":
        return (
            "retarget exact repo referrers to the owner or a compact oracle "
            "reference before moving or deleting"
        )
    if reason == "mechanical_referrer_anchor":
        return (
            "keep out of the docs cleanup queue; update only with the owning "
            "code, test, hook, tool, or historical-referrer-map contract"
        )
    if reason == "archived_retention_anchor":
        return (
            "keep as declared archived evidence until its exit rule fires or a "
            "later retained index supersedes it"
        )
    if reason == "active_support_surface":
        return "keep current until its declared exit rule fires"
    if reason == "archived_compressible_support":
        return (
            "eligible for owner absorption and Obsidian source-copy handling; "
            "preserve the path until a fresh referrer scan is clean"
        )
    if reason == "ordinary_support_surface":
        return "review for owner absorption, retirement, or Obsidian-only route"
    return "not a support surface"


def _with_support_retention(
    records: Sequence[DocsRoutingCandidate],
) -> list[DocsRoutingCandidate]:
    retained: list[DocsRoutingCandidate] = []
    for record in records:
        reason = _support_retention_reason(record)
        retained.append(
            replace(
                record,
                support_retention_reason=reason,
                support_next_action=_support_next_action(reason),
            )
        )
    return retained


def _digestion_requires_review(status: str) -> bool:
    return status in DIGESTION_REVIEW_STATUSES


def _support_retention_requires_followup(reason: str) -> bool:
    return reason in {
        "exact_referrer_bound_support",
        "archived_compressible_support",
        "ordinary_support_surface",
    }


def _record_requires_digestion_followup(record: DocsRoutingCandidate) -> bool:
    return (
        _digestion_requires_review(
            record.digestion_status,
        )
        or _support_retention_requires_followup(
            record.support_retention_reason,
        )
        or _record_requires_metadata_followup(record)
    )


def _record_requires_metadata_followup(record: DocsRoutingCandidate) -> bool:
    if record.metadata_status == "declared":
        return False
    return record.disposition in {
        "keep_repo_canonical",
        "keep_repo_marked",
    }


def _sample_referrer_paths(record: DocsRoutingCandidate) -> tuple[str, ...]:
    if record.sample_referrers == "none":
        return ()
    return tuple(
        path.strip() for path in record.sample_referrers.split(";") if path.strip()
    )


def _only_mechanical_exact_referrers(record: DocsRoutingCandidate) -> bool:
    referrers = _sample_referrer_paths(record)
    return (
        record.exact_referrers > 0
        and len(referrers) == record.exact_referrers
        and all(
            referrer.startswith(DOC_ROUTING_MECHANICAL_REFERRER_PREFIXES)
            for referrer in referrers
        )
    )


def _archived_retention_anchor(record: DocsRoutingCandidate) -> bool:
    if record.doc_lifecycle != "archived":
        return False
    if not record.path.startswith("docs/superpowers/file-management/docs-cleanup/"):
        return False
    exit_rule = record.doc_exit_rule.strip().lower()
    if not exit_rule or exit_rule == "missing":
        return False
    return exit_rule.startswith("keep ")


def _is_specs_index_path(path: str) -> bool:
    return classify_doc_path(path).is_specs_index


def _is_superpowers_spec_path(path: str) -> bool:
    return classify_doc_path(path).is_superpowers_spec


def _topic_role(classification: DocClassification, disposition: str) -> str:
    placement = classification.placement
    if classification.is_specs_index:
        return "repo_supporting_artifact"
    if disposition in {
        "formalize_then_obsidian",
        "invalid_repo_placement",
        "needs_human_review",
    }:
        return "needs_distillation_or_route"
    if disposition == "delegated_handoff_retention":
        return "delegated_handoff"
    if placement == "repo_subcontract_doc":
        return "repo_subcontract_owner"
    if placement == "repo_support_doc":
        return "repo_supporting_artifact"
    if placement == "formal_repo_doc":
        return "repo_topic_owner"
    if classification.is_superpowers_spec:
        return "repo_subcontract_owner"
    if disposition == "keep_repo_canonical":
        return "repo_topic_owner"
    return "repo_supporting_artifact"


def _topic_owner_claim(path: str, topic_role: str, repo_owner: str) -> str:
    if topic_role != "repo_topic_owner":
        return "not_owner"
    return repo_owner or path


def _topic_subcontract_claim(path: str, topic_role: str, repo_owner: str) -> str:
    if topic_role != "repo_subcontract_owner":
        return "not_subcontract"
    return repo_owner or path


def _obsidian_original_hint(path: str, route: str) -> str:
    if route == DOC_ROUTE_REPO_PRODUCT_DOC:
        return "none"
    return f"source_repo_path:{path}"


def _repo_pointer_required(route: str) -> str:
    if route in {
        DOC_ROUTE_OBSIDIAN_ORIGINAL,
        DOC_ROUTE_REPO_DISTILLED_PLUS_OBSIDIAN_ORIGINAL,
    }:
        return "yes"
    if route == DOC_ROUTE_NEEDS_DECISION:
        return "decision_pending"
    return "no"


def _wiki_skill_route(route: str) -> str:
    if route == DOC_ROUTE_OBSIDIAN_ORIGINAL:
        return "wiki-query -> wiki-ingest -> wiki-lint -> wiki-stage-commit"
    if route == DOC_ROUTE_REPO_DISTILLED_PLUS_OBSIDIAN_ORIGINAL:
        return "wiki-query -> wiki-ingest/wiki-update -> wiki-lint -> wiki-stage-commit"
    if route == DOC_ROUTE_NEEDS_DECISION:
        return "wiki-status -> wiki-query -> critical review before write"
    return "wiki-query optional; wiki-update optional after durable repo change"


def _wiki_next_action(path: str, route: str) -> str:
    source_hint = f"source_repo_path:{path}"
    if route == DOC_ROUTE_OBSIDIAN_ORIGINAL:
        return (
            f"find or create Obsidian original for {source_hint}; use "
            "obsidian-markdown syntax, lint/read back, then promote staged writes"
        )
    if route == DOC_ROUTE_REPO_DISTILLED_PLUS_OBSIDIAN_ORIGINAL:
        return (
            f"query wiki for existing {source_hint}; distill stable repo claims "
            "first, then ingest/update the Obsidian original and lint/read back"
        )
    if route == DOC_ROUTE_NEEDS_DECISION:
        return (
            f"check wiki status and query for {source_hint}; decide route before "
            "any Obsidian write or repo removal"
        )
    return (
        "no mandatory wiki write; optionally sync durable project knowledge after "
        "repo owner is updated"
    )


def _infer_doc_kind(path: str) -> str:
    return infer_doc_kind_from_path(path)


class _LifecycleMetadata(TypedDict):
    doc_kind: str
    doc_kind_source: str
    doc_lifecycle: str
    doc_exit_rule: str
    lifecycle_status: str


class _RoutingMetadata(_LifecycleMetadata):
    metadata_status: str
    metadata_missing_fields: str
    wiki_skill_route: str
    wiki_next_action: str
    doc_route: str
    repo_body_role: str
    digestion_status: str
    digestion_next_action: str
    support_retention_reason: str
    support_next_action: str
    information_value: str
    repo_owner_hint: str
    topic_key: str
    topic_role: str
    topic_owner_claim: str
    topic_subcontract_claim: str
    topic_cluster_size: int
    topic_cluster_status: str
    topic_cluster_sample: str
    obsidian_lane: str
    obsidian_original_hint: str
    repo_pointer_required: str
    referrer_status: str
    exact_referrers: int
    sample_referrers: str
    destructive_allowed_now: str


class DocsRoutingReview(TypedDict):
    scanned_files: int
    candidate_files: int
    route_retained_files: int
    route_retained_review_files: int
    metadata_review_files: int
    kept_files: int
    all_disposition_counts: dict[str, int]
    disposition_counts: dict[str, int]
    all_doc_route_counts: dict[str, int]
    doc_route_counts: dict[str, int]
    all_doc_kind_counts: dict[str, int]
    all_digestion_status_counts: dict[str, int]
    route_retained_digestion_status_counts: dict[str, int]
    candidate_digestion_status_counts: dict[str, int]
    all_lifecycle_status_counts: dict[str, int]
    metadata_status_counts: dict[str, int]
    lifecycle_status_counts: dict[str, int]
    all_topic_cluster_counts: dict[str, int]
    topic_cluster_group_counts: dict[str, int]
    topic_clusters: list[dict[str, object]]
    top_topic_clusters: list[dict[str, object]]
    candidates: list[dict[str, object]]
    top_candidates: list[dict[str, object]]
    route_retained_reviews: list[dict[str, object]]
    top_route_retained_reviews: list[dict[str, object]]
    metadata_reviews: list[dict[str, object]]
    top_metadata_reviews: list[dict[str, object]]


def _lifecycle_metadata(path: str, text: str) -> _LifecycleMetadata:
    classification = classify_doc(path, text)
    return {
        "doc_kind": classification.doc_kind,
        "doc_kind_source": classification.doc_kind_source,
        "doc_lifecycle": classification.doc_lifecycle,
        "doc_exit_rule": classification.doc_exit_rule,
        "lifecycle_status": classification.lifecycle_status,
    }


def _routing_metadata(path: str, text: str, disposition: str) -> _RoutingMetadata:
    classification = classify_doc(path, text)
    information_value, repo_owner_hint = _topic_hint(path, text)
    route = _doc_route(disposition)
    topic_role = _topic_role(classification, disposition)
    repo_owner = repo_owner_value(text)
    return {
        **_lifecycle_metadata(path, text),
        "metadata_status": classification.metadata_status,
        "metadata_missing_fields": (
            "; ".join(classification.metadata_missing_fields) or "none"
        ),
        "wiki_skill_route": _wiki_skill_route(route),
        "wiki_next_action": _wiki_next_action(path, route),
        "doc_route": route,
        "repo_body_role": _repo_body_role(route),
        "digestion_status": "unclustered",
        "digestion_next_action": "cluster docs before deciding digestion state",
        "support_retention_reason": "unclustered",
        "support_next_action": "cluster docs before deciding support retention",
        "information_value": information_value,
        "repo_owner_hint": repo_owner_hint,
        "topic_key": information_value,
        "topic_role": topic_role,
        "topic_owner_claim": _topic_owner_claim(path, topic_role, repo_owner),
        "topic_subcontract_claim": _topic_subcontract_claim(
            path, topic_role, repo_owner
        ),
        "topic_cluster_size": 1,
        "topic_cluster_status": "unclustered",
        "topic_cluster_sample": path,
        "obsidian_lane": _obsidian_lane(path, disposition),
        "obsidian_original_hint": _obsidian_original_hint(path, route),
        "repo_pointer_required": _repo_pointer_required(route),
        "referrer_status": "not_scanned",
        "exact_referrers": 0,
        "sample_referrers": "none",
        "destructive_allowed_now": "no",
    }


def _is_referrer_scan_target(rel_path: str) -> bool:
    if any(
        pattern.match(rel_path) for pattern in DOC_ROUTING_REFERRER_EXCLUDE_PATTERNS
    ):
        return False
    return _docs_scan.is_local_path_scan_rel_path(rel_path)


def _exact_referrer_paths(
    repository_view: _docs_scan.RepositoryTextView,
    candidate: DocsRoutingCandidate,
) -> list[str]:
    candidate_path = candidate.path
    family_prefix, needles = validation_readme_reference_scope(
        candidate_path,
        list(repository_view.paths),
    )
    referrers: list[str] = []
    for rel_path in repository_view.text_paths:
        normalized = rel_path.replace("\\", "/")
        if normalized == candidate_path or (
            family_prefix and normalized.startswith(family_prefix)
        ):
            continue
        if not _is_referrer_scan_target(normalized):
            continue
        text = repository_view.read_text(normalized)
        if any(needle in text for needle in needles):
            referrers.append(normalized)
    return sorted(set(referrers))


def _with_referrer_status(
    repository_view: _docs_scan.RepositoryTextView,
    candidate: DocsRoutingCandidate,
) -> DocsRoutingCandidate:
    referrers = _exact_referrer_paths(repository_view, candidate)
    if not referrers:
        return replace(
            candidate,
            referrer_status="none",
            exact_referrers=0,
            sample_referrers="none",
        )
    authority_referrers = [
        path for path in referrers if path in DOC_ROUTING_AUTHORITY_REFERRER_PATHS
    ]
    other_referrers = [
        path for path in referrers if path not in DOC_ROUTING_AUTHORITY_REFERRER_PATHS
    ]
    sample_referrers = authority_referrers + other_referrers
    return replace(
        candidate,
        referrer_status="exact_repo_referrers_present",
        exact_referrers=len(referrers),
        sample_referrers="; ".join(sample_referrers[:5]),
    )


def _with_unbound_validation_review(
    candidate: DocsRoutingCandidate,
) -> DocsRoutingCandidate:
    if (
        candidate.disposition != "keep_repo_validation_or_fixture"
        or candidate.exact_referrers
    ):
        return candidate
    return replace(
        candidate,
        disposition="review_unbound_validation_surface",
        reason="validation surface has no declared owner or external family referrer",
        required_before_move=(
            "absorb any durable claim into its owner, then retire the unbound "
            "surface or add the current machine consumer"
        ),
        candidate=True,
    )


def _topic_cluster_status(group: Sequence[DocsRoutingCandidate]) -> str:
    owner_claims = [
        record.topic_owner_claim
        for record in group
        if record.topic_role == "repo_topic_owner"
    ]
    owner_count = len(owner_claims)
    repeated_claim_count = owner_count - len(set(owner_claims))
    candidate_count = sum(1 for record in group if record.candidate)
    subcontract_count = sum(
        1 for record in group if record.topic_role == "repo_subcontract_owner"
    )
    if repeated_claim_count:
        return "potential_duplicate_owner"
    if owner_count > 1:
        return "multiple_subtopic_owners"
    if owner_count == 1 and candidate_count:
        return "owner_plus_cleanup_candidates"
    if owner_count == 0 and candidate_count:
        if group and group[0].repo_owner_hint:
            return "external_owner_with_cleanup_candidates"
        return "owner_missing_for_candidates"
    if subcontract_count:
        return "subcontracts_with_owner"
    if len(group) > 1:
        return "multiple_support_surfaces"
    return "single_surface"


def _with_topic_cluster_status(
    records: Sequence[DocsRoutingCandidate],
) -> list[DocsRoutingCandidate]:
    groups: dict[tuple[str, str], list[DocsRoutingCandidate]] = {}
    for record in records:
        groups.setdefault((record.topic_key, record.repo_owner_hint), []).append(record)

    clustered: list[DocsRoutingCandidate] = []
    for group in groups.values():
        status = _topic_cluster_status(group)
        sorted_group = sorted(group, key=lambda item: item.path)
        sample = "; ".join(record.path for record in sorted_group[:5])
        for record in group:
            digestion_status = _digestion_status(record, status)
            clustered.append(
                replace(
                    record,
                    topic_cluster_size=len(group),
                    topic_cluster_status=status,
                    topic_cluster_sample=sample,
                    digestion_status=digestion_status,
                    digestion_next_action=_digestion_next_action(digestion_status),
                )
            )
    return clustered


def _topic_cluster_rows(
    records: Sequence[DocsRoutingCandidate],
) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], list[DocsRoutingCandidate]] = {}
    for record in records:
        groups.setdefault((record.topic_key, record.repo_owner_hint), []).append(record)

    rows: list[dict[str, object]] = []
    for (topic_key, repo_owner_hint), group in groups.items():
        roles = Counter(record.topic_role for record in group)
        digestion_status_counts = Counter(record.digestion_status for record in group)
        status = _topic_cluster_status(group)
        sorted_group = sorted(group, key=lambda item: item.path)
        owner_paths = [
            record.path
            for record in sorted_group
            if record.topic_role == "repo_topic_owner"
        ]
        owner_claims = sorted(
            {
                record.topic_owner_claim
                for record in sorted_group
                if record.topic_role == "repo_topic_owner"
            }
        )
        subcontract_paths = [
            record.path
            for record in sorted_group
            if record.topic_role == "repo_subcontract_owner"
        ]
        subcontract_claims = sorted(
            {
                record.topic_subcontract_claim
                for record in sorted_group
                if record.topic_role == "repo_subcontract_owner"
            }
        )
        supporting_paths = [
            record.path
            for record in sorted_group
            if record.topic_role == "repo_supporting_artifact"
        ]
        support_retention_counts = Counter(
            record.support_retention_reason
            for record in sorted_group
            if record.topic_role == "repo_supporting_artifact"
        )
        bound_support_paths = [
            record.path
            for record in sorted_group
            if record.support_retention_reason
            in {
                "authority_or_status_anchor",
                "mechanical_referrer_anchor",
                "archived_retention_anchor",
                "exact_referrer_bound_support",
                "active_support_surface",
            }
        ]
        compressible_support_paths = [
            record.path
            for record in sorted_group
            if record.support_retention_reason
            in {
                "archived_compressible_support",
                "ordinary_support_surface",
            }
        ]
        candidate_paths = [record.path for record in sorted_group if record.candidate]
        rows.append(
            {
                "topic_key": topic_key,
                "repo_owner_hint": repo_owner_hint,
                "topic_cluster_status": status,
                "file_count": len(group),
                "topic_owner_count": roles.get("repo_topic_owner", 0),
                "topic_owner_claim_count": len(owner_claims),
                "subcontract_count": roles.get("repo_subcontract_owner", 0),
                "supporting_count": roles.get("repo_supporting_artifact", 0),
                "candidate_count": len(candidate_paths),
                "delegated_handoff_count": roles.get("delegated_handoff", 0),
                "suggested_repo_topic_folder": (
                    _suggested_repo_topic_folder(topic_key)
                ),
                "suggested_obsidian_topic_folder": (
                    _suggested_obsidian_topic_folder(topic_key)
                ),
                "topic_next_action": _topic_next_action(status),
                "sample_paths": "; ".join(record.path for record in sorted_group[:8]),
                "digestion_review_count": sum(
                    1
                    for record in sorted_group
                    if _record_requires_digestion_followup(record)
                ),
                "digestion_status_counts": "; ".join(
                    f"{status}:{count}"
                    for status, count in sorted(digestion_status_counts.items())
                ),
                "support_retention_counts": "; ".join(
                    f"{reason}:{count}"
                    for reason, count in sorted(support_retention_counts.items())
                )
                or "none",
                "owner_paths": "; ".join(owner_paths[:8]) or "none",
                "owner_claims": "; ".join(owner_claims[:8]) or "none",
                "subcontract_paths": "; ".join(subcontract_paths[:8]) or "none",
                "subcontract_claims": "; ".join(subcontract_claims[:8]) or "none",
                "supporting_sample_paths": "; ".join(supporting_paths[:8]) or "none",
                "bound_support_sample_paths": "; ".join(bound_support_paths[:8])
                or "none",
                "compressible_support_sample_paths": "; ".join(
                    compressible_support_paths[:8]
                )
                or "none",
                "candidate_paths": "; ".join(candidate_paths[:8]) or "none",
            }
        )

    priority = {
        "potential_duplicate_owner": 0,
        "multiple_subtopic_owners": 1,
        "owner_missing_for_candidates": 2,
        "external_owner_with_cleanup_candidates": 3,
        "owner_plus_cleanup_candidates": 4,
        "multiple_support_surfaces": 5,
        "subcontracts_with_owner": 6,
        "single_surface": 7,
    }
    return sorted(
        rows,
        key=lambda item: (
            priority.get(str(item["topic_cluster_status"]), 99),
            str(item["topic_key"]),
            str(item["repo_owner_hint"]),
        ),
    )


def _classify_docs_routing_candidate(
    repository_view: _docs_scan.RepositoryTextView,
    rel_path: str,
) -> DocsRoutingCandidate | None:
    normalized = rel_path.replace("\\", "/")
    if not repository_view.contains(normalized) or not _is_docs_routing_scan_target(
        normalized
    ):
        return None

    text = repository_view.read_text(normalized)
    classification = classify_doc(normalized, text)
    placement = classification.placement
    repo_owner = classification.repo_owner
    if placement in DOC_RETIRED_PLACEMENT_VALUES:
        return DocsRoutingCandidate(
            path=normalized,
            disposition="invalid_repo_placement",
            reason=f"tracked repo doc declares retired placement {placement!r}",
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "invalid_repo_placement"),
            required_before_move=(
                "absorb durable claims into the canonical owner, preserve any "
                "needed original outside the repo, close exact referrers, then "
                "retire the tracked document without a replacement stub"
            ),
            candidate=True,
        )

    if placement and doc_placement_is_non_repo(placement):
        return DocsRoutingCandidate(
            path=normalized,
            disposition="invalid_repo_placement",
            reason=f"tracked repo doc declares non-repo placement {placement!r}",
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "invalid_repo_placement"),
            required_before_move=(
                "choose obsidian_original or "
                "repo_distilled_plus_obsidian_original; move the body to an "
                "approved Obsidian staged draft while preserving a "
                "source_repo_path pointer"
            ),
            candidate=True,
        )

    if classification.is_docs_routing_handoff:
        return DocsRoutingCandidate(
            path=normalized,
            disposition="delegated_handoff_retention",
            reason="tracked handoff state is a migration error",
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "delegated_handoff_retention"),
            required_before_move=(
                "move durable claims to owners or the PR body, then remove the "
                "tracked handoff"
            ),
            candidate=False,
        )

    if placement:
        return DocsRoutingCandidate(
            path=normalized,
            disposition="keep_repo_marked",
            reason=f"repo doc declares placement {placement!r}",
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "keep_repo_marked"),
            required_before_move="none unless the declared owner becomes stale",
            candidate=False,
        )

    if classification.is_validation_or_fixture:
        return DocsRoutingCandidate(
            path=normalized,
            disposition="keep_repo_validation_or_fixture",
            reason="checker-readable validation or fixture surface",
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "keep_repo_validation_or_fixture"),
            required_before_move=(
                "do not move without a checker-aware migration, retained summary, "
                "hash/referrer update, and focused tests"
            ),
            candidate=False,
        )

    if classification.is_governance_artifact:
        return DocsRoutingCandidate(
            path=normalized,
            disposition="keep_repo_governance_artifact",
            reason="public docs-governance, productization, or closeout surface",
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "keep_repo_governance_artifact"),
            required_before_move=(
                "keep unless a later referrer-aware archive/removal patch is approved"
            ),
            candidate=False,
        )

    if is_canonical_doc_owner_path(normalized):
        return DocsRoutingCandidate(
            path=normalized,
            disposition="keep_repo_canonical",
            reason="canonical repo owner path",
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "keep_repo_canonical"),
            required_before_move="none unless the canonical owner role changes",
            candidate=False,
        )

    signals = _routing_signals(text)
    if signals:
        return DocsRoutingCandidate(
            path=normalized,
            disposition="formalize_then_obsidian",
            reason="high-risk docs lane contains " + ", ".join(signals),
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "formalize_then_obsidian"),
            required_before_move=(
                "extract stable public claims to a canonical repo owner, then "
                "move long diary/review/command context to Obsidian only after "
                "pilot write/readback, source_repo_path pointer, lifecycle "
                "closeout, and referrer scan"
            ),
            candidate=True,
        )

    if classification.is_high_risk_repo_doc or classification.is_legacy_history:
        return DocsRoutingCandidate(
            path=normalized,
            disposition="needs_human_review",
            reason="high-risk docs lane has no placement marker",
            placement=placement,
            repo_owner=repo_owner,
            **_routing_metadata(normalized, text, "needs_human_review"),
            required_before_move=(
                "choose one fixed route: obsidian_original, "
                "repo_distilled_plus_obsidian_original, or repo_product_doc "
                "and record lifecycle/exit rule before any move"
            ),
            candidate=True,
        )

    return DocsRoutingCandidate(
        path=normalized,
        disposition="needs_human_review",
        reason="docs/superpowers markdown is outside known durable lanes",
        placement=placement,
        repo_owner=repo_owner,
        **_routing_metadata(normalized, text, "needs_human_review"),
        required_before_move=(
            "choose one fixed route: obsidian_original, "
            "repo_distilled_plus_obsidian_original, or repo_product_doc "
            "and record lifecycle/exit rule before any move"
        ),
        candidate=True,
    )


def docs_routing_review(
    repository_view: _docs_scan.RepositoryTextView,
) -> DocsRoutingReview:
    _docs_scan.require_repository_text_view(repository_view.root, repository_view)
    records = [
        record
        for rel_path in repository_view.paths
        if (record := _classify_docs_routing_candidate(repository_view, rel_path))
        is not None
    ]
    records = [_with_referrer_status(repository_view, record) for record in records]
    records = [_with_unbound_validation_review(record) for record in records]
    records = _with_topic_cluster_status(records)
    records = _with_support_retention(records)
    candidates = [record for record in records if record.candidate]
    all_disposition_counts = Counter(record.disposition for record in records)
    disposition_counts = Counter(candidate.disposition for candidate in candidates)
    all_doc_route_counts = Counter(record.doc_route for record in records)
    doc_route_counts = Counter(candidate.doc_route for candidate in candidates)
    all_doc_kind_counts = Counter(record.doc_kind for record in records)
    all_digestion_status_counts = Counter(record.digestion_status for record in records)
    route_retained_records = [record for record in records if not record.candidate]
    route_retained_digestion_status_counts = Counter(
        record.digestion_status for record in route_retained_records
    )
    route_retained_review_records = [
        record
        for record in route_retained_records
        if _record_requires_digestion_followup(record)
    ]
    metadata_review_records = [
        record
        for record in route_retained_records
        if _record_requires_metadata_followup(record)
    ]
    all_lifecycle_status_counts = Counter(record.lifecycle_status for record in records)
    metadata_status_counts = Counter(record.metadata_status for record in records)
    all_topic_cluster_counts = Counter(
        record.topic_cluster_status for record in records
    )
    topic_cluster_rows = _topic_cluster_rows(records)
    topic_cluster_group_counts = Counter(
        str(row["topic_cluster_status"]) for row in topic_cluster_rows
    )
    lifecycle_status_counts = Counter(
        candidate.lifecycle_status for candidate in candidates
    )
    candidate_digestion_status_counts = Counter(
        candidate.digestion_status for candidate in candidates
    )
    sorted_candidates = sorted(
        candidates,
        key=lambda item: (
            item.disposition != "invalid_repo_placement",
            item.disposition,
            item.path,
        ),
    )
    candidate_rows = [asdict(candidate) for candidate in sorted_candidates]
    route_retained_review_rows = [
        asdict(record)
        for record in sorted(
            route_retained_review_records,
            key=lambda item: (
                item.topic_key,
                item.support_retention_reason,
                item.digestion_status,
                item.path,
            ),
        )
    ]
    metadata_review_rows = [
        asdict(record)
        for record in sorted(
            metadata_review_records,
            key=lambda item: (
                item.metadata_missing_fields,
                item.topic_key,
                item.path,
            ),
        )
    ]
    return {
        "scanned_files": len(records),
        "candidate_files": len(candidates),
        "route_retained_files": len(route_retained_records),
        "route_retained_review_files": len(route_retained_review_records),
        "metadata_review_files": len(metadata_review_records),
        "kept_files": len(records) - len(candidates),
        "all_disposition_counts": dict(sorted(all_disposition_counts.items())),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "all_doc_route_counts": dict(sorted(all_doc_route_counts.items())),
        "doc_route_counts": dict(sorted(doc_route_counts.items())),
        "all_doc_kind_counts": dict(sorted(all_doc_kind_counts.items())),
        "all_digestion_status_counts": dict(
            sorted(all_digestion_status_counts.items())
        ),
        "route_retained_digestion_status_counts": dict(
            sorted(route_retained_digestion_status_counts.items())
        ),
        "candidate_digestion_status_counts": dict(
            sorted(candidate_digestion_status_counts.items())
        ),
        "all_lifecycle_status_counts": dict(
            sorted(all_lifecycle_status_counts.items())
        ),
        "metadata_status_counts": dict(sorted(metadata_status_counts.items())),
        "lifecycle_status_counts": dict(sorted(lifecycle_status_counts.items())),
        "all_topic_cluster_counts": dict(sorted(all_topic_cluster_counts.items())),
        "topic_cluster_group_counts": dict(sorted(topic_cluster_group_counts.items())),
        "topic_clusters": topic_cluster_rows,
        "top_topic_clusters": topic_cluster_rows[:25],
        "candidates": candidate_rows,
        "top_candidates": candidate_rows[:25],
        "route_retained_reviews": route_retained_review_rows,
        "top_route_retained_reviews": route_retained_review_rows[:25],
        "metadata_reviews": metadata_review_rows,
        "top_metadata_reviews": metadata_review_rows[:25],
    }
