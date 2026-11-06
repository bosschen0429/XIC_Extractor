"""Load the shared Inspector frontend for a self-contained report."""

from importlib.resources import files

_assets = files("xic_inspector.rendering").joinpath("assets")
INSPECTOR_JS = "\n".join(
    _assets.joinpath(name).read_text(encoding="utf-8")
    for name in ("inspector.js", "delta_mass.js")
)
HTML_TEMPLATE = (
    _assets.joinpath("inspector.html")
    .read_text(encoding="utf-8")
    .replace(
        "__INSPECTOR_CSS__",
        _assets.joinpath("inspector.css").read_text(encoding="utf-8"),
    )
    .replace("__INSPECTOR_JS__", INSPECTOR_JS)
)
