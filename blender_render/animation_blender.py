# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera animation over the scene constructed by the shared Blender renderer."""
import shutil
import tempfile
from pathlib import Path
import math
import bpy
from mathutils import Matrix, Vector
from blender_render.animation import angles, package_frames


def render(scene, camera, center, output_png, config):
    original = camera.matrix_world.copy()
    original_path = scene.render.filepath
    axis = original.to_quaternion() @ Vector((0, 1, 0))
    motion = angles(config)
    try:
        with tempfile.TemporaryDirectory(prefix='animation-', dir=output_png.parent) as directory:
            directory = Path(directory)
            shutil.copyfile(output_png, directory / '0000.png')
            for index, angle in enumerate(motion[1:], 1):
                camera.matrix_world = (Matrix.Translation(center) @
                    Matrix.Rotation(math.radians(angle), 4, axis) @
                    Matrix.Translation(-center) @ original)
                scene.render.filepath = str(directory / f'{index:04d}.png')
                bpy.context.view_layer.update()
                bpy.ops.render.render(write_still=True)
                print(f'Animation frame {index + 1}/{len(motion)}', flush=True)
            package_frames(directory, output_png.with_suffix('.frames.zip'), config,
                           scene.render.resolution_x, scene.render.resolution_y)
    finally:
        camera.matrix_world = original
        scene.render.filepath = original_path
        bpy.context.view_layer.update()
    return dict(config, frames=len(motion), angles_deg=motion, axis='initial_camera_up',
                center=list(center), encoding_background='white')
