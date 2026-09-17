# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender process for the trusted offline rocking utility."""
import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_render import blender_custom_render as renderer
from blender_render import worker_ccd


def main():
    cfg = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
    work, output = Path(cfg["work"]), Path(cfg["output"])
    renderer._configure_molecularnodes_import_path()
    capture = worker_ccd.initialize()
    try:
        args = renderer._parse_argv(["renderer", "--structure", str(work / "structure.cif"),
            "--view-json", str(work / "view.json"), "--style-json", str(output / "resolved-style.json"),
            "--output-png", str(output / "render.png"), "--run-manifest", str(output / "manifest.json")])
        renderer._render(args)
    finally:
        capture.close()


if __name__ == "__main__":
    main()
