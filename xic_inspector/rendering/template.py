"""Load the shared Inspector frontend for a self-contained report."""

from importlib.resources import files

_assets = files("xic_inspector.rendering").joinpath("assets")
HTML_TEMPLATE = _assets.joinpath("inspector.html").read_text(encoding="utf-8").replace(
    "__WORKBENCH_JS__", _assets.joinpath("inspector.js").read_text(encoding="utf-8")
)
