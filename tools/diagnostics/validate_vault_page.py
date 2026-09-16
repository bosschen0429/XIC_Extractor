from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Repository imports follow the bootstrap for direct-script execution.
from tools.diagnostics.docs_management_audit import parse_yaml_frontmatter  # noqa: E402

VALID_LIFECYCLES = {"draft", "reviewed", "verified", "disputed", "archived"}
VALID_TIERS = {"core", "supporting", "peripheral"}


def validate_vault_page(text: str) -> list[str]:
    """Return a list of validation errors. Empty list means valid."""
    fm = parse_yaml_frontmatter(text)
    errors: list[str] = []

    if not fm:
        errors.append("missing YAML frontmatter (no --- delimiters)")
        return errors

    summary = fm.get("summary", "").strip()
    if not summary:
        errors.append("missing required field: summary")

    lifecycle = fm.get("lifecycle", "").strip()
    if not lifecycle:
        errors.append("missing required field: lifecycle")
    elif lifecycle not in VALID_LIFECYCLES:
        errors.append(
            f"invalid lifecycle '{lifecycle}'"
            f" — valid: {', '.join(sorted(VALID_LIFECYCLES))}"
        )

    tier = fm.get("tier", "").strip()
    if not tier:
        errors.append("missing required field: tier")
    elif tier not in VALID_TIERS:
        errors.append(
            f"invalid tier '{tier}' — valid: {', '.join(sorted(VALID_TIERS))}"
        )

    tags = fm.get("tags", "")
    if "visibility/" not in tags:
        errors.append(
            "missing visibility tag — tags must include a visibility/ prefix"
            " (e.g., visibility/internal, visibility/public)"
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate vault page metadata before promotion",
    )
    parser.add_argument("page_path", type=Path, help="path to vault page .md file")
    args = parser.parse_args(argv)

    text = args.page_path.read_text(encoding="utf-8")
    errors = validate_vault_page(text)

    if errors:
        print(f"BLOCKED — {len(errors)} validation error(s):")
        for err in errors:
            print(f"  • {err}")
        return 1

    print("PASS — page metadata is valid for promotion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
