# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender process for the trusted offline rocking utility."""
import json
import math
from pathlib import Path
import shutil
import sys

import bpy
from mathutils import Matrix, Vector

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
        scene, camera, center = renderer._render(args)
        original = camera.matrix_world.copy()
        axis = original.to_quaternion() @ Vector((0, 1, 0))
        frames = work / "frames"
        frames.mkdir()
        shutil.copyfile(output / "render.png", frames / "0000.png")
        for index, angle in enumerate(cfg["angles"][1:], 1):
            camera.matrix_world = (Matrix.Translation(center) @
                Matrix.Rotation(math.radians(angle), 4, axis) @
                Matrix.Translation(-center) @ original)
            scene.render.filepath = str(frames / f"{index:04d}.png")
            bpy.context.view_layer.update()
            bpy.ops.render.render(write_still=True)
            print(f"Rocking frame {index + 1}/{len(cfg['angles'])}", flush=True)
    finally:
        capture.close()


if __name__ == "__main__":
    main()
