"""Export a static, light-theme guide SVG from an Archify 3.1 HTML artifact.

Documentation-only adapter: keeps authored geometry and SVG paint, not the
viewer runtime or embedded fonts. No browser or third-party packages needed.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from xml.etree import ElementTree as ET


def local_references(root: ET.Element) -> set[str]:
    """Find paint/link targets, including those inside a referenced definition."""
    text = ET.tostring(root, encoding="unicode")
    return set(re.findall(r"url\(#([^)]*)\)", text)) | {
        value[1:]
        for element in root.iter()
        for key, value in element.attrib.items()
        if key.endswith("href") and value.startswith("#")
    }


def export_svg(html: str) -> str:
    """Extract the supported classic Architecture diagram; fail on format drift."""
    if '<meta name="generator" content="archify 3.1.0">' not in html:
        raise ValueError("Expected an Archify 3.1.0 artifact")
    match = re.search(r'<svg viewBox="[^"]+" role="img".*?</svg>', html, re.S)
    if match is None:
        raise ValueError("Missing primary Architecture SVG")
    root = ET.fromstring(match[0])
    if root.get("data-preset") != "classic":
        raise ValueError("Only the classic preset is supported")

    # The current guides use nodes and edges, not structural frames. Do not
    # silently discard a future frame's context-sensitive paint.
    classes = {name for e in root.iter() for name in e.get("class", "").split()}
    if classes & {"c-region", "c-security-group", "c-lane", "phase-rule", "phase-chip"}:
        raise ValueError("Structural-frame paint is not supported")

    for parent in root.iter():
        for child in list(parent):
            if child.tag == "style":
                parent.remove(child)
        # Project the two active node-UI rules into static paint hooks before
        # removing viewer metadata. The remaining internal rules target dark
        # themes, other presets, or structural frames (rejected above).
        if "data-node-id" in parent.attrib:
            for child in parent:
                if child.tag == "rect" and "c-mask" not in child.get("class", ""):
                    child.set("stroke-width", "1.25px")
        if "data-edge-from" in parent.attrib:
            for child in parent:
                if child.tag == "rect" and "c-mask" in child.get("class", "").split():
                    child.set("class", child.get("class", "") + " edge-label")

    # Keep only definitions reachable from the visible diagram, then follow
    # references inside live definitions. Merely being inside defs is not use.
    defs = root.find("defs")
    if defs is not None:
        definitions = {child.get("id"): child for child in defs}
        needed = set().union(*(local_references(e) for e in root if e is not defs))
        pending = list(needed)
        while pending:
            definition = definitions.get(pending.pop())
            if definition is not None:
                new = local_references(definition) - needed
                needed.update(new)
                pending.extend(new)
        for child in list(defs):
            if child.get("id") not in needed:
                defs.remove(child)

    classes = {name for e in root.iter() for name in e.get("class", "").split()}
    # These are flat semantic paint rules in Archify 3.1, not a general CSS parser.
    # Never copy @font-face, viewer layouts, animation or interactive-state rules.
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    styles = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)
    light = re.search(r'\[data-theme="light"\]\s*\{([^{}]+)\}', styles)
    if light is None:
        raise ValueError("Missing light-theme variables")
    variables = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", light[1]))
    rules = []
    for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", styles):
        selector = selector.strip()
        supported = re.fullmatch(r"\.[ctam]-[\w-]+", selector) or re.fullmatch(
            r"svg \.(?:semantic-sigil(?: > \*| \.sigil-fill)?|s-[\w-]+)", selector
        )
        if supported and set(re.findall(r"\.([\w-]+)", selector)) <= classes:
            rules.append(f"{selector} {{ {declarations.strip()} }}")
    if "edge-label" in classes:
        rules.append(
            ".c-mask.edge-label { rx: 7px; stroke: var(--lane-stroke); "
            "stroke-width: .75px; }"
        )
    css = "\n".join(rules)
    used = set(re.findall(r"var\((--[\w-]+)\)", css + ET.tostring(root, "unicode")))
    if used - variables.keys():
        raise ValueError(
            f"Unresolved paint variables: {sorted(used - variables.keys())}"
        )
    css = (
        "svg {\n"
        + "\n".join(
            f"  {key}: {value};" for key, value in variables.items() if key in used
        )
        + "\n}\n"
        + css
    )

    root.set("xmlns", "http://www.w3.org/2000/svg")
    _, _, width, height = root.attrib["viewBox"].split()
    root.set("width", width)
    root.set("height", height)
    root.set(
        "style",
        "background: #fff; font-family: Consolas, 'Microsoft JhengHei', monospace",
    )
    referenced_ids = local_references(root) | set(
        root.get("aria-labelledby", "").split()
    )
    for element in root.iter():
        for key in list(element.attrib):
            if key.startswith("data-") or key in ("tabindex", "aria-pressed"):
                del element.attrib[key]
        if element.get("id") not in referenced_ids:
            element.attrib.pop("id", None)
        if element is not root:
            # Group titles retain the label, without a false "focus" action.
            element.attrib.pop("aria-label", None)
            if re.fullmatch(r"--step:\s*\d+", element.get("style", "")):
                del element.attrib["style"]
        if element.get("role") == "button":
            del element.attrib["role"]
        if (
            element.tag not in ("text", "title", "desc")
            and not (element.text or "").strip()
        ):
            element.text = None
        if not (element.tail or "").strip():
            element.tail = None
    style = ET.Element("style")
    style.text = "\n" + css + "\n"
    root.insert(0, style)
    ET.indent(root, space="  ")
    body = "\n".join(
        line.rstrip() for line in ET.tostring(root, encoding="unicode").splitlines()
    )
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Verified Archify HTML under output/")
    parser.add_argument("output", type=Path, help="Destination .svg")
    args = parser.parse_args()
    svg = export_svg(args.input.read_text(encoding="utf-8"))
    args.output.write_text(svg, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
