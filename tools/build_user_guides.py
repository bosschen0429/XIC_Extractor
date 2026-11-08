"""Include the same offline guide in installed Python wheels."""

import runpy
from pathlib import Path

from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self) -> None:
        super().run()
        root = Path(__file__).resolve().parents[1]
        # Setuptools loads this command by file in an isolated environment;
        # the checkout is not an installed/importable package there.
        render_user_guides = runpy.run_path(str(root / "xic_workbench/user_guide.py"))[
            "render_user_guides"
        ]
        for relative, data in render_user_guides(root).items():
            target = Path(self.build_lib) / "xic_workbench/assets/help" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
