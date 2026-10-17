"""Export a static, light-theme guide SVG from an Archify 3.1 HTML artifact.

Documentation-only adapter: keeps authored geometry and SVG paint, not the
viewer runtime or embedded fonts. No browser or third-party packages needed.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from xml.etree import ElementTree as ET


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

    # These are flat semantic paint rules in Archify 3.1, not a general CSS parser.
    # Never copy @font-face, viewer layouts, animation or interactive-state rules.
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
    styles = re.sub(r"/\*.*?\*/", "", styles, flags=re.S)
    light = re.search(r'\[data-theme="light"\]\s*\{([^{}]+)\}', styles)
    if light is None:
        raise ValueError("Missing light-theme variables")
    rules = ["svg { " + light[1].strip() + " }"]
    for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", styles):
        selector = selector.strip()
        if re.fullmatch(r"\.[ctam]-[\w-]+", selector) or re.fullmatch(
            r"svg \.(?:semantic-sigil(?: > \*| \.sigil-fill)?|s-[\w-]+)", selector
        ):
            rules.append(f"{selector} {{ {declarations.strip()} }}")
    css = "\n".join(rules)
    defined = set(re.findall(r"(--[\w-]+)\s*:", css))
    used = set(re.findall(r"var\((--[\w-]+)\)", css + match[0]))
    if used - defined:
        raise ValueError(f"Unresolved paint variables: {sorted(used - defined)}")

    root.set("xmlns", "http://www.w3.org/2000/svg")
    root.set("data-theme", "light")
    _, _, width, height = root.attrib["viewBox"].split()
    root.set("width", width)
    root.set("height", height)
    root.set(
        "style",
        "background: #fff; font-family: Consolas, 'Microsoft JhengHei', monospace",
    )
    # Preserve internal node/frame paint and data attributes it selects, but do
    # not advertise inert nodes as keyboard buttons in a standalone SVG.
    for element in root.iter():
        for key in ("tabindex", "aria-pressed", "data-animation", "data-animate"):
            element.attrib.pop(key, None)
        if element.get("role") == "button":
            del element.attrib["role"]
    style = ET.Element("style")
    style.text = "\n" + css + "\n"
    root.insert(0, style)
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
