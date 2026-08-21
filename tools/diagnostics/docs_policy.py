from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

CONTROL_PLANE_PATH = "docs/superpowers/plans/2026-06-15-productization-control-plane.md"


def source_copy_note_title(source_path: str, source_hash: str) -> str:
    """Return the deterministic title of an exact retired source copy."""

    return f"{Path(source_path).stem}-source-{source_hash[:12]}"


def source_copy_locator_note_title(source_path: str, source_hash: str) -> str:
    """Return the deterministic title of the searchable locator sidecar."""

    return f"{source_copy_note_title(source_path, source_hash)}-locator"


def source_copy_locator_text(source_path: str, source_hash: str) -> str:
    """Render the non-authoritative locator without changing exact source bytes."""

    exact_note_title = source_copy_note_title(source_path, source_hash)
    return "\n".join(
        (
            "# Retired source locator",
            "",
            f"source_repo_path:{source_path}",
            "",
            f"Exact source note: [[{exact_note_title}]]",
            "",
            f"Source SHA-256: `{source_hash}`",
            "",
        )
    )


def source_copy_locator_sha256(source_path: str, source_hash: str) -> str:
    """Hash the deterministic searchable locator sidecar."""

    return hashlib.sha256(
        source_copy_locator_text(source_path, source_hash).encode("utf-8")
    ).hexdigest()


PRODUCTIZATION_COMPATIBILITY_INDEX_MARKERS = (
    "# Productization compatibility index",
    "Doc placement: formal_repo_doc",
    "Doc kind: manifest",
    "Doc lifecycle: active",
    "This path is a compatibility lookup surface.",
    "## Authority routes",
    "## Historical source",
    "## Lane anchor index",
)
HANDOFF_CURRENT_DIR = "docs/superpowers/handoffs/current/"
HANDOFF_ARCHIVE_DIR = "docs/superpowers/handoffs/archive/"
DEFAULT_LOCAL_ACTIVE_HANDOFF_PATH = f"{HANDOFF_CURRENT_DIR}ACTIVE.local.md"
PRODUCTIZATION_STATUS_ANCHOR_PATH = (
    "docs/superpowers/productization/status/cc-framework-improvements-productization.md"
)
# Backward-compatible import name for existing hook code. The file is no longer
# a handoff; keep the alias until hook call sites are renamed in a focused pass.
PRODUCTIZATION_STATUS_HANDOFF_PATH = PRODUCTIZATION_STATUS_ANCHOR_PATH
CLOSEOUT_DIR = "docs/superpowers/closeouts/"

PRODUCTIZATION_SCHEMA_REL = (
    "docs/superpowers/schemas/productization_control_plane_schema.v3.json"
)
PRODUCTIZATION_STATUS_INDEX_REL = (
    "docs/superpowers/validation/productization_status_index_v1.tsv"
)
PRODUCTIZATION_AUTHORITY_MANIFEST_REL = (
    "docs/superpowers/schemas/productization_authority_manifest.v1.json"
)
QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_SCHEMA_REL = (
    "docs/superpowers/schemas/quantitative_projection_authority_manifest_schema.v1.json"
)
QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_REL = (
    "docs/superpowers/schemas/quantitative_projection_authority_manifest.v1.json"
)
PRODUCTIZATION_CONTROL_PLANE_SOURCE_HASH = (
    "97778ff9fb5c316ac58ee5f34964de8f13e9dd5f7dfd18e04e0cb6490fa213e5"
)
PRODUCTIZATION_CONTROL_PLANE_SOURCE_COPY_NOTE_TITLE = source_copy_note_title(
    CONTROL_PLANE_PATH,
    PRODUCTIZATION_CONTROL_PLANE_SOURCE_HASH,
)
PRODUCTIZATION_CONTROL_PLANE_SOURCE_COPY_LOCATOR_NOTE_TITLE = (
    source_copy_locator_note_title(
        CONTROL_PLANE_PATH,
        PRODUCTIZATION_CONTROL_PLANE_SOURCE_HASH,
    )
)
MECHANICAL_ADJUDICATION_INDEX_REL = (
    "docs/superpowers/validation/mechanical_adjudication_index_v1.tsv"
)
SUPERPOWERS_SPEC_DIR = "docs/superpowers/specs/"

DOC_PLACEMENT_MARKER = "Doc placement:"
DOC_REPO_OWNER_MARKER = "Repo owner:"
DOC_KIND_MARKER = "Doc kind:"
DOC_LIFECYCLE_MARKER = "Doc lifecycle:"
DOC_EXIT_RULE_MARKER = "Doc exit rule:"
DOC_PLACEMENT_VALUES = {
    "formal_repo_doc",
    "repo_subcontract_doc",
    "repo_support_doc",
    "repo_active_stub",
    "branch_closeout_summary",
    "repo_stub_plus_obsidian",
    "repo_stub_plus_formal_doc",
    "private_obsidian_note",
    "ignored_artifact",
    "throwaway_scratch",
}
DOC_RETIRED_PLACEMENT_VALUES = {
    "repo_active_stub",
    "branch_closeout_summary",
    "repo_stub_plus_obsidian",
    "repo_stub_plus_formal_doc",
}
DOC_PLACEMENTS_REQUIRING_REPO_OWNER = {
    "formal_repo_doc",
    "repo_subcontract_doc",
    "repo_support_doc",
    "repo_active_stub",
    "branch_closeout_summary",
    "repo_stub_plus_obsidian",
    "repo_stub_plus_formal_doc",
    "ignored_artifact",
}
NON_REPO_DOC_PLACEMENTS = {
    "private_obsidian_note",
    "throwaway_scratch",
}
DOC_KIND_VALUES = {
    "plan",
    "spec",
    "note",
    "goal",
    "report",
    "manifest",
    "handoff",
    "closeout",
    "validation_artifact",
    "product_doc",
}
DOC_LIFECYCLE_VALUES = {
    "draft",
    "active",
    "implemented",
    "superseded",
    "rejected",
    "archived",
    "retired",
}
DOC_LIFECYCLES_REQUIRING_EXIT_RULE = {
    "draft",
    "active",
    "implemented",
    "superseded",
    "rejected",
}

# These are the only documents whose permanent path supplies placement, kind,
# purpose, and self-ownership. Generic lifecycle/exit metadata is waived, but
# their positive landmarks and routes are validated separately.
PERMANENT_ROOT_DOC_CONTRACTS = {
    "AGENTS.md": {
        "kind": "agent_route_contract",
        "purpose": "repo agent routing contract",
        "owner": "self",
        "required_headings": ("XIC Extractor Agent Contract", "Task Routes"),
        "required_text": ("Task Routes",),
        "required_routes": (
            "docs/agent/parameter-settings.md",
            "docs/product/README.md",
            "docs/agent/product-validation-contract.md",
            "docs/architecture-contract.md",
            "docs/agent/execution-gates.md",
            "tools/diagnostics/INDEX.md",
            "docs/solutions/README.md",
            "docs/product/productization.md",
            "docs/project-layout.md",
            "docs/agent/codex-operating-system.md",
        ),
        "required_referrers": (),
    },
    "README.md": {
        "kind": "public_project_entrypoint",
        "purpose": "public project entrypoint",
        "owner": "self",
        "required_headings": ("XIC Extractor", "Documentation"),
        "required_text": ("Documentation",),
        "required_routes": (
            "docs/user/README.md",
            "docs/product/README.md",
            "docs/architecture-contract.md",
            "docs/project-layout.md",
            "docs/lc-msms-evidence-rules.md",
        ),
        "required_referrers": (),
    },
    "docs/architecture-contract.md": {
        "kind": "architecture_dependency_owner",
        "purpose": "architecture and dependency owner",
        "owner": "self",
        "required_headings": (
            "Architecture Contract",
            "Dependency Direction",
            "Ownership Map",
        ),
        "required_text": ("This document owns",),
        "required_routes": (),
        "required_referrers": (
            "AGENTS.md",
            "README.md",
            "docs/project-layout.md",
        ),
    },
    "docs/project-layout.md": {
        "kind": "file_placement_lifecycle_owner",
        "purpose": "file placement and documentation lifecycle owner",
        "owner": "self",
        "required_headings": ("Project Layout", "Permanent root document contract"),
        "required_text": ("目錄收納規則單一可信來源", "source-of-truth 與歷程邊界"),
        "required_routes": (
            "docs/architecture-contract.md",
            "docs/product",
            "docs/agent/obsidian-handoff-contract.md",
        ),
        "required_referrers": (),
    },
}
PERMANENT_ROOT_METADATA_FIELDS_WAIVED = frozenset(
    {DOC_LIFECYCLE_MARKER.rstrip(":"), DOC_EXIT_RULE_MARKER.rstrip(":")}
)

# Membership follows the two-stage admission predicate in
# docs/agent/codex-operating-system.md#governance-enforcement-index. This dict
# mirrors admitted current mechanisms; it is not the policy owner or a backlog
# for aspirational enforcement.
GOVERNANCE_RULE_FAMILY_CONTRACTS = {
    "docs_lifecycle_retirement": {
        "owner": "docs/agent/obsidian-handoff-contract.md#retirement-evidence-gate",
        "mechanism": "ci_checker",
        "executable": "tools/diagnostics/docs_placement_guard.py",
        "readback": "tests/test_docs_placement_guard.py",
        "residual": "none",
    },
    "destructive_git_filesystem": {
        "owner": "docs/agent/codex-operating-system.md#current-repo-local-rules",
        "mechanism": "hard_guard",
        "executable": ".codex/rules/xic-safety.rules",
        "readback": "tests/test_config_codex_hooks.py",
        "residual": "none",
    },
    "raw_background_launch": {
        "owner": "docs/agent/execution-gates.md#raw-and-long-runs",
        "mechanism": "hard_guard",
        "executable": ".codex/hooks/xic_pre_tool_guard.py",
        "readback": "tests/test_config_codex_hooks.py",
        "residual": "none",
    },
    "execution_affecting_config": {
        "owner": "docs/agent/codex-operating-system.md#change-protocol",
        "mechanism": "advisory_context",
        "executable": ".codex/hooks/xic_pre_tool_guard.py",
        "readback": "tests/test_config_codex_hooks.py",
        "residual": "none",
    },
    "pr_validation_gate": {
        "owner": "docs/agent/execution-gates.md#pr-verification-gate",
        "mechanism": "ci_checker",
        "executable": "tools/testing/test_shards.py",
        "readback": ".github/workflows/ci.yml",
        "residual": "none",
    },
}
GOVERNANCE_RULE_FAMILY_IDS = tuple(GOVERNANCE_RULE_FAMILY_CONTRACTS)
GOVERNANCE_MECHANISM_VALUES = frozenset(
    {"hard_guard", "ci_checker", "advisory_context", "human_decision"}
)


def is_productization_control_plane_path(path: str) -> bool:
    return normalize_path_text(path).removeprefix("./") == CONTROL_PLANE_PATH


def is_productization_compatibility_index_text(text: str) -> bool:
    """Identify a previously retired control-plane compatibility preimage."""

    return all(
        text.count(marker) == 1
        for marker in PRODUCTIZATION_COMPATIBILITY_INDEX_MARKERS
    )


DOC_ROOT_ALLOWLIST_FILES = {
    "docs/architecture-contract.md",
    "docs/diagnostic-ledger.md",
    "docs/lc-msms-evidence-rules.md",
    "docs/project-layout.md",
}
DOC_RETIRED_TRACKED_DIRS = {
    "docs/deepresearch/": (
        "deepresearch is private-history context now; absorb durable takeaways "
        "into docs/product/ or another canonical owner"
    ),
    "docs/superpowers/deepresearch/": (
        "deepresearch is private-history context now; absorb durable takeaways "
        "into docs/product/ or another canonical owner"
    ),
    "docs/superpowers/goals/": (
        "construction goals belong in GitHub Issues; durable decisions belong "
        "in a canonical repo owner"
    ),
    "docs/superpowers/notes/": (
        "generic notes are private-first now; use ignored handoff, output/, "
        "Obsidian, a formal owner, or a narrowly named public artifact lane"
    ),
    "docs/superpowers/pulse-reports/": (
        "pulse reports are generated read-side snapshots; write to output/ "
        "unless promoted to productization/evidence/"
    ),
    "docs/superpowers/reports/": (
        "reports is too broad; route public HTML, fixtures, probes, and "
        "validation evidence to their explicit owner lanes"
    ),
    "docs/superpowers/handoffs/current/": (
        "active handoffs are ignored local state and must not be force-added"
    ),
    "docs/superpowers/handoffs/archive/": (
        "handoff archives are private or disposable history and must not be tracked"
    ),
    "docs/superpowers/plans/": (
        "construction plans and PRDs belong in GitHub Issues; only the "
        "checker-bound productization compatibility index is exempt"
    ),
    "docs/superpowers/specs/": (
        "construction specs belong in GitHub Issues; durable contracts belong "
        "in product, agent, architecture, schema, or validation owners"
    ),
    "docs/superpowers/closeouts/": (
        "branch and PR closeout history belongs in the GitHub PR body"
    ),
    "docs/superpowers/file-management/docs-cleanup/": (
        "one-off cleanup inventories, approval records, and retirement evidence "
        "belong under ignored output"
    ),
    "docs/superpowers/topics/": (
        "topics duplicated docs/product; generate temporary indexes under "
        "ignored output/docs-topic-indexes/"
    ),
}
DOC_RETIRED_TRACKED_DIR_PREFIXES = tuple(DOC_RETIRED_TRACKED_DIRS)
DOC_RETIRED_TRACKED_EXEMPTIONS = frozenset(
    {
        CONTROL_PLANE_PATH,
    }
)

DOC_CANONICAL_OWNER_DIRS = [
    "docs/product/",
    "docs/user/",
    "docs/agent/",
    "docs/engineering-skills/",
    "docs/solutions/",
    "docs/superpowers/schemas/",
    "docs/superpowers/validation/",
    "docs/superpowers/fixtures/",
    "docs/superpowers/productization/",
    "docs/validation/",
    "tests/fixtures/",
]
DOC_CANONICAL_OWNER_FILES = {
    "AGENTS.md",
    "README.md",
    "docs/architecture-contract.md",
    "docs/diagnostic-ledger.md",
    "docs/lc-msms-evidence-rules.md",
    "docs/product/domain-glossary.md",
    "docs/project-layout.md",
    "docs/superpowers/README.md",
    "docs/superpowers/handoffs/README.md",
    PRODUCTIZATION_STATUS_HANDOFF_PATH,
    CONTROL_PLANE_PATH,
}
HIGH_RISK_DOC_DIRS = [
    "docs/deepresearch/",
    "docs/superpowers/deepresearch/",
    "docs/superpowers/handoffs/current/",
    "docs/superpowers/handoffs/archive/",
    "docs/superpowers/notes/",
    "docs/superpowers/plans/",
    "docs/superpowers/reports/",
    SUPERPOWERS_SPEC_DIR,
]
DOC_LIFECYCLE_MANAGED_DIRS = [
    "docs/deepresearch/",
    "docs/superpowers/deepresearch/",
    "docs/superpowers/goals/",
    "docs/superpowers/notes/",
    "docs/superpowers/plans/",
    "docs/superpowers/pulse-reports/",
    "docs/superpowers/reports/",
    SUPERPOWERS_SPEC_DIR,
]
MISPLACED_HANDOFF_PUBLIC_RECORD_PATTERNS = [
    re.compile(r"(?:^|[_-])branch-closeout-summary(?:\.|$)", re.IGNORECASE),
    re.compile(r"(?:^|[_-])file-management-approval-plan(?:\.|$)", re.IGNORECASE),
    re.compile(r"(?:^|[_-])git-rm-candidate-manifest(?:\.|$)", re.IGNORECASE),
    re.compile(r"(?:^|[_-])historical-referrer", re.IGNORECASE),
    re.compile(r"(?:^|[_-])productization_handoff-prune(?:[_\-.]|$)", re.IGNORECASE),
    re.compile(r"(?:^|[_-])public-surface-stub-audit(?:\.|$)", re.IGNORECASE),
    re.compile(r"(?:^|[_-])source-of-truth-queue(?:\.|$)", re.IGNORECASE),
]

DOC_ROUTING_SCAN_PREFIXES = ("docs/superpowers/",)
DOC_ROUTING_HANDOFF_PREFIX = "docs/superpowers/handoffs/"
DOC_ROUTING_VALIDATION_PREFIXES = (
    "docs/superpowers/fixtures/",
    "docs/superpowers/validation/",
)
DOC_ROUTING_GOVERNANCE_PREFIXES = (
    "docs/superpowers/closeouts/",
    "docs/superpowers/file-management/",
    "docs/superpowers/productization/",
)
DOC_ROUTING_LEGACY_HISTORY_PREFIXES = (
    "docs/superpowers/deepresearch/",
    "docs/superpowers/goals/",
    "docs/superpowers/notes/",
    "docs/superpowers/pulse-reports/",
    "docs/superpowers/reports/",
)
DOC_ROUTING_MECHANICAL_REFERRER_PREFIXES = (
    ".codex/",
    "scripts/",
    "tests/",
    "tools/",
    "docs/superpowers/file-management/docs-cleanup/referrers/",
)
# Legacy generated topic-index location. The repo no longer tracks this tree;
# keep the classifier so historical manifests or local scratch indexes do not
# accidentally become candidate referrers or topic owners.
DOC_ROUTING_TOPIC_PREFIX = "docs/superpowers/topics/"
DOC_ROUTING_SCHEMAS_INDEX_PATH = "docs/superpowers/schemas/README.md"
DOC_ROUTING_AUTHORITY_REFERRER_PATHS = frozenset(
    {
        "docs/superpowers/validation/ARTIFACT_INVENTORY.tsv",
        "docs/superpowers/validation/lockbox_review_packets_v1/packet_index.tsv",
        "docs/superpowers/validation/productization_status_index_v1.tsv",
        "docs/superpowers/schemas/productization_authority_manifest.v1.json",
        QUANTITATIVE_PROJECTION_AUTHORITY_MANIFEST_REL,
    }
)
PRIVATE_HISTORY_SIGNALS = [
    "implementation diary",
    "command log",
    "command transcript",
    "review rationale",
    "branch sequencing",
    "development diary",
    "private obsidian",
    "raw transcript",
]


@dataclass(frozen=True)
class DocPathClassification:
    path: str
    is_markdown: bool
    is_repo_doc: bool
    is_canonical_owner: bool
    is_high_risk_repo_doc: bool
    is_lifecycle_managed: bool
    is_branch_closeout_summary: bool
    is_misplaced_handoff_public_record: bool
    is_docs_routing_scan_target: bool
    is_docs_routing_handoff: bool
    is_validation_or_fixture: bool
    is_governance_artifact: bool
    is_legacy_history: bool
    is_topic_index: bool
    is_specs_index: bool
    is_superpowers_spec: bool
    is_docs_root_file: bool
    is_allowed_docs_root_file: bool
    is_docs_root_scatter: bool
    is_retired_tracked_dir: bool
    retired_tracked_dir: str
    inferred_kind: str


@dataclass(frozen=True)
class DocClassification:
    path: str
    path_classification: DocPathClassification
    placement: str
    repo_owner: str
    declared_kind: str
    doc_kind: str
    doc_kind_source: str
    declared_lifecycle: str
    doc_lifecycle: str
    doc_exit_rule: str
    lifecycle_status: str
    metadata_status: str
    metadata_missing_fields: tuple[str, ...]
    is_markdown: bool
    is_repo_doc: bool
    is_canonical_owner: bool
    is_canonical_owner_source: str
    is_high_risk_repo_doc: bool
    is_high_risk_source: str
    is_lifecycle_managed: bool
    is_branch_closeout_summary: bool
    is_misplaced_handoff_public_record: bool
    is_docs_routing_scan_target: bool
    is_docs_routing_handoff: bool
    is_validation_or_fixture: bool
    is_governance_artifact: bool
    is_legacy_history: bool
    is_topic_index: bool
    is_specs_index: bool
    is_superpowers_spec: bool
    is_docs_root_file: bool
    is_allowed_docs_root_file: bool
    is_docs_root_scatter: bool
    is_retired_tracked_dir: bool
    retired_tracked_dir: str


def normalize_path_text(text: str) -> str:
    normalized = text.replace("\\", "/")
    while "/./" in normalized:
        normalized = normalized.replace("/./", "/")
    return normalized


def marker_value(text: str, marker: str) -> str:
    marker_lower = marker.lower()
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.lower().startswith(marker_lower):
            return stripped.split(":", 1)[1].strip().strip("`")
    return ""


def doc_placement_value(text: str) -> str:
    return marker_value(text, DOC_PLACEMENT_MARKER)


def repo_owner_value(text: str) -> str:
    return marker_value(text, DOC_REPO_OWNER_MARKER)


def doc_kind_value(text: str) -> str:
    return marker_value(text, DOC_KIND_MARKER)


def doc_lifecycle_value(text: str) -> str:
    return marker_value(text, DOC_LIFECYCLE_MARKER)


def doc_exit_rule_value(text: str) -> str:
    return marker_value(text, DOC_EXIT_RULE_MARKER)


def has_private_history_signal(text: str) -> bool:
    lowered = text.lower()
    return any(signal in lowered for signal in PRIVATE_HISTORY_SIGNALS)


def doc_placement_requires_repo_owner(placement: str) -> bool:
    return placement in DOC_PLACEMENTS_REQUIRING_REPO_OWNER


def doc_placement_is_non_repo(placement: str) -> bool:
    return placement in NON_REPO_DOC_PLACEMENTS


def doc_lifecycle_requires_exit_rule(lifecycle: str) -> bool:
    return lifecycle in DOC_LIFECYCLES_REQUIRING_EXIT_RULE


def path_is_under(path: str, root: str) -> bool:
    normalized_path = normalize_path_text(path).lstrip("./")
    normalized_root = normalize_path_text(root).lstrip("./")
    if normalized_root.endswith("/"):
        return normalized_path.startswith(normalized_root)
    return normalized_path == normalized_root


def path_startswith_any(rel_path: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel_path.startswith(prefix) for prefix in prefixes)


def is_markdown_path(path: str) -> bool:
    normalized = normalize_path_text(path).lower()
    return normalized.endswith((".md", ".markdown"))


def is_repo_doc_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    return is_markdown_path(normalized) and (
        normalized in DOC_CANONICAL_OWNER_FILES or normalized.startswith("docs/")
    )


def is_branch_closeout_summary_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./").lower()
    return normalized.startswith(CLOSEOUT_DIR) and normalized.endswith(
        "_branch-closeout-summary.md"
    )


def is_public_handoff_archive_evidence_path(path: str) -> bool:
    return False


def is_misplaced_handoff_public_record_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    if not (
        normalized.startswith(HANDOFF_CURRENT_DIR)
        or normalized.startswith(HANDOFF_ARCHIVE_DIR)
    ):
        return False
    filename = normalized.rsplit("/", 1)[-1]
    return any(
        pattern.search(filename) for pattern in MISPLACED_HANDOFF_PUBLIC_RECORD_PATTERNS
    )


def is_canonical_doc_owner_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    if normalized in DOC_CANONICAL_OWNER_FILES:
        return True
    if normalized in DOC_ROOT_ALLOWLIST_FILES:
        return True
    if is_public_handoff_archive_evidence_path(normalized):
        return True
    return any(path_is_under(normalized, root) for root in DOC_CANONICAL_OWNER_DIRS)


def is_high_risk_repo_doc_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    if normalized in DOC_CANONICAL_OWNER_FILES:
        return False
    if is_branch_closeout_summary_path(normalized):
        return False
    return any(path_is_under(normalized, root) for root in HIGH_RISK_DOC_DIRS)


def is_lifecycle_managed_doc_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    return any(path_is_under(normalized, root) for root in DOC_LIFECYCLE_MANAGED_DIRS)


def is_docs_root_file_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    return (
        is_markdown_path(normalized)
        and normalized.startswith("docs/")
        and normalized.count("/") == 1
    )


def is_superpowers_spec_lane_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    return path_is_under(normalized, SUPERPOWERS_SPEC_DIR)


def is_invalid_superpowers_spec_payload_path(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    return is_superpowers_spec_lane_path(normalized) and not is_markdown_path(
        normalized
    )


def retired_tracked_dir_for_path(path: str) -> str:
    normalized = normalize_path_text(path).lstrip("./")
    if normalized in DOC_RETIRED_TRACKED_EXEMPTIONS:
        return ""
    for root in DOC_RETIRED_TRACKED_DIR_PREFIXES:
        if path_is_under(normalized, root):
            return root
    return ""


def is_docs_routing_scan_target(path: str) -> bool:
    normalized = normalize_path_text(path).lstrip("./")
    return is_repo_doc_path(normalized) and path_startswith_any(
        normalized, DOC_ROUTING_SCAN_PREFIXES
    )


def infer_doc_kind_from_path(path: str) -> str:
    normalized = normalize_path_text(path).lstrip("./")
    if "/plans/" in normalized:
        return "plan"
    if "/specs/" in normalized:
        return "spec"
    if "/schemas/" in normalized:
        return "manifest"
    if "/goals/" in normalized:
        return "goal"
    if "/reports/" in normalized or "/pulse-reports/" in normalized:
        return "report"
    if "/deepresearch/" in normalized or "/notes/" in normalized:
        return "note"
    if "/handoffs/" in normalized:
        return "handoff"
    if "/closeouts/" in normalized:
        return "closeout"
    if "/validation/" in normalized or "/fixtures/" in normalized:
        return "validation_artifact"
    if "/file-management/" in normalized:
        return "manifest"
    return "product_doc"


def classify_doc_path(path: str) -> DocPathClassification:
    normalized = normalize_path_text(path).lstrip("./")
    normalized_lower = normalized.lower()
    is_topic_index = normalized_lower.startswith(DOC_ROUTING_TOPIC_PREFIX)
    is_specs_index = normalized_lower == DOC_ROUTING_SCHEMAS_INDEX_PATH
    is_superpowers_spec = (
        normalized_lower.startswith(SUPERPOWERS_SPEC_DIR) and not is_specs_index
    )
    is_docs_root_file = is_docs_root_file_path(normalized)
    is_allowed_docs_root_file = normalized in DOC_ROOT_ALLOWLIST_FILES
    retired_tracked_dir = retired_tracked_dir_for_path(normalized)
    return DocPathClassification(
        path=normalized,
        is_markdown=is_markdown_path(normalized),
        is_repo_doc=is_repo_doc_path(normalized),
        is_canonical_owner=is_canonical_doc_owner_path(normalized),
        is_high_risk_repo_doc=is_high_risk_repo_doc_path(normalized),
        is_lifecycle_managed=is_lifecycle_managed_doc_path(normalized),
        is_branch_closeout_summary=is_branch_closeout_summary_path(normalized),
        is_misplaced_handoff_public_record=(
            is_misplaced_handoff_public_record_path(normalized)
        ),
        is_docs_routing_scan_target=is_docs_routing_scan_target(normalized),
        is_docs_routing_handoff=(
            normalized.startswith(DOC_ROUTING_HANDOFF_PREFIX)
            and normalized != "docs/superpowers/handoffs/README.md"
        ),
        is_validation_or_fixture=path_startswith_any(
            normalized, DOC_ROUTING_VALIDATION_PREFIXES
        ),
        is_governance_artifact=path_startswith_any(
            normalized, DOC_ROUTING_GOVERNANCE_PREFIXES
        ),
        is_legacy_history=path_startswith_any(
            normalized, DOC_ROUTING_LEGACY_HISTORY_PREFIXES
        ),
        is_topic_index=is_topic_index,
        is_specs_index=is_specs_index,
        is_superpowers_spec=is_superpowers_spec,
        is_docs_root_file=is_docs_root_file,
        is_allowed_docs_root_file=is_allowed_docs_root_file,
        is_docs_root_scatter=is_docs_root_file and not is_allowed_docs_root_file,
        is_retired_tracked_dir=bool(retired_tracked_dir),
        retired_tracked_dir=retired_tracked_dir,
        inferred_kind=infer_doc_kind_from_path(normalized),
    )


def classify_doc(path: str, text: str) -> DocClassification:
    path_classification = classify_doc_path(path)
    placement = doc_placement_value(text)
    repo_owner = repo_owner_value(text)
    declared_kind = doc_kind_value(text)
    lifecycle = doc_lifecycle_value(text)
    exit_rule = doc_exit_rule_value(text)
    if declared_kind:
        doc_kind = declared_kind
        doc_kind_source = "declared"
    else:
        doc_kind = path_classification.inferred_kind
        doc_kind_source = "inferred"

    if not lifecycle:
        lifecycle_value = "unknown"
        lifecycle_status = "missing_lifecycle"
    elif lifecycle not in DOC_LIFECYCLE_VALUES:
        lifecycle_value = lifecycle
        lifecycle_status = "invalid_lifecycle"
    elif doc_lifecycle_requires_exit_rule(lifecycle) and not exit_rule:
        lifecycle_value = lifecycle
        lifecycle_status = "missing_exit_rule"
    else:
        lifecycle_value = lifecycle
        lifecycle_status = "declared"

    missing_fields: list[str] = []
    if not placement:
        missing_fields.append(DOC_PLACEMENT_MARKER.rstrip(":"))
    if not repo_owner and (
        not placement or doc_placement_requires_repo_owner(placement)
    ):
        missing_fields.append(DOC_REPO_OWNER_MARKER.rstrip(":"))
    if not declared_kind:
        missing_fields.append(DOC_KIND_MARKER.rstrip(":"))
    if not lifecycle:
        missing_fields.append(DOC_LIFECYCLE_MARKER.rstrip(":"))
    if not exit_rule and (not lifecycle or doc_lifecycle_requires_exit_rule(lifecycle)):
        missing_fields.append(DOC_EXIT_RULE_MARKER.rstrip(":"))
    metadata_missing_fields = tuple(missing_fields)

    if path_classification.is_canonical_owner:
        is_canonical_owner = True
        is_canonical_owner_source = "path"
    elif placement == "formal_repo_doc":
        is_canonical_owner = True
        is_canonical_owner_source = "metadata_override"
    else:
        is_canonical_owner = False
        is_canonical_owner_source = "path"

    if placement and path_classification.is_high_risk_repo_doc:
        is_high_risk_repo_doc = False
        is_high_risk_source = "metadata_cleared"
    else:
        is_high_risk_repo_doc = path_classification.is_high_risk_repo_doc
        is_high_risk_source = "path"

    return DocClassification(
        path=path_classification.path,
        path_classification=path_classification,
        placement=placement,
        repo_owner=repo_owner,
        declared_kind=declared_kind,
        doc_kind=doc_kind,
        doc_kind_source=doc_kind_source,
        declared_lifecycle=lifecycle,
        doc_lifecycle=lifecycle_value,
        doc_exit_rule=exit_rule or "missing",
        lifecycle_status=lifecycle_status,
        metadata_status=(
            "declared" if not metadata_missing_fields else "missing_metadata"
        ),
        metadata_missing_fields=metadata_missing_fields,
        is_markdown=path_classification.is_markdown,
        is_repo_doc=path_classification.is_repo_doc,
        is_canonical_owner=is_canonical_owner,
        is_canonical_owner_source=is_canonical_owner_source,
        is_high_risk_repo_doc=is_high_risk_repo_doc,
        is_high_risk_source=is_high_risk_source,
        is_lifecycle_managed=path_classification.is_lifecycle_managed,
        is_branch_closeout_summary=path_classification.is_branch_closeout_summary,
        is_misplaced_handoff_public_record=(
            path_classification.is_misplaced_handoff_public_record
        ),
        is_docs_routing_scan_target=path_classification.is_docs_routing_scan_target,
        is_docs_routing_handoff=path_classification.is_docs_routing_handoff,
        is_validation_or_fixture=path_classification.is_validation_or_fixture,
        is_governance_artifact=path_classification.is_governance_artifact,
        is_legacy_history=path_classification.is_legacy_history,
        is_topic_index=path_classification.is_topic_index,
        is_specs_index=path_classification.is_specs_index,
        is_superpowers_spec=path_classification.is_superpowers_spec,
        is_docs_root_file=path_classification.is_docs_root_file,
        is_allowed_docs_root_file=path_classification.is_allowed_docs_root_file,
        is_docs_root_scatter=path_classification.is_docs_root_scatter,
        is_retired_tracked_dir=path_classification.is_retired_tracked_dir,
        retired_tracked_dir=path_classification.retired_tracked_dir,
    )
