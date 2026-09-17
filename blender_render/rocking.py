"""Trusted offline rocking/spin animation; uses the ordinary molecular scene builder.

Run with a DSS-assigned StructureBundle from blender_render.export.
This is a developer utility, not a hosted-service animation API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def angles(frames: int, amplitude: float) -> list[float]:
    from .animation import normalize, angles as motion_angles
    config = normalize(dict(mode='rock', duration=frames/24, fps=24, rock_angle=amplitude),
                       dict(width=2, height=2))
    return motion_angles(config)


def spin_angles(frames: int) -> list[float]:
    from .animation import normalize, angles as motion_angles
    config = normalize(dict(mode='360', duration=frames/24, fps=24), dict(width=2, height=2))
    return motion_angles(config)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encode(ffmpeg: str, frames: Path, output: Path, fps: int, name: str = "rocking") -> None:
    from .animation import encode as encode_movie
    metadata = dict(fps=fps, frames=len(list(frames.glob('*.png'))))
    for extension in ('mp4', 'gif'):
        encode_movie(frames, metadata, output / f'{name}.{extension}', executable=ffmpeg)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    runtime = parser.add_mutually_exclusive_group(required=True)
    runtime.add_argument("--blender", help="Installed Blender with Molecular Nodes")
    runtime.add_argument("--image", type=Path, help="Pinned Apptainer runtime image")
    parser.add_argument("--preset", default="neon_duplex_hero")
    parser.add_argument("--motion", choices=("rocking", "spin"), default="rocking")
    parser.add_argument("--frames", type=int, default=72)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--amplitude", type=float, default=None, help="Rocking amplitude in degrees (default: 12); not used for spin")
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=540)
    parser.add_argument("--samples", type=int, default=64)
    args = parser.parse_args(argv)
    if args.motion == "spin" and args.amplitude is not None:
        parser.error("--amplitude applies only to rocking")
    amplitude = 12 if args.amplitude is None else args.amplitude
    motion = spin_angles(args.frames) if args.motion == "spin" else angles(args.frames, amplitude)
    if not 1 <= args.fps <= 60 or min(args.width, args.height) < 2 or args.width % 2 or args.height % 2:
        parser.error("FPS must be 1–60 and dimensions must be positive even integers")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("ffmpeg is required for GIF and MP4 output")
    from .core import validate_payload_document
    from .spec import resolve_options
    bundle = validate_payload_document(json.loads(args.bundle.read_text()))
    if not bundle.get("cif_text") or not bundle.get("dss", {}).get("ran"):
        parser.error("A CIF bundle with completed PyMOL DSS assignment is required")
    options = {"preset": args.preset, "preserve_view": False,
        "animation": "360" if args.motion == "spin" else "rock",
        "duration": args.frames / args.fps, "fps": args.fps, "gif": True,
        "width": args.width, "height": args.height, "samples": args.samples}
    if args.motion == 'rocking':
        options['rock_angle'] = amplitude
    resolved = resolve_options(options, bundle)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    package = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix=".rocking-", dir=output) as temporary:
        work = Path(temporary)
        (work / "structure.cif").write_text(bundle["cif_text"])
        view = dict(bundle["view"])
        for field in ("ss_hints", "color_hints"):
            if field in bundle:
                view[field] = bundle[field]
        (work / "view.json").write_text(json.dumps(view))
        (output / "resolved-style.json").write_text(json.dumps(resolved["style_doc"], indent=2))
        (work / "config.json").write_text(json.dumps({"angles": motion, "work": str(work),
                                                     "output": str(output)}))
        command = [args.blender or "blender", "--background", "--factory-startup",
                   "--disable-autoexec", "--python-use-system-env", "--python-exit-code", "1",
                   "--python", str(package / "rocking_blender.py"), "--", str(work / "config.json")]
        if args.image:
            command = ["apptainer", "exec", "--nv", "--cleanenv", "--bind", str(package.parent),
                       "--bind", str(output), "--env",
                       f"PYTHONPATH={package.parent}:/opt/runtime/site", str(args.image.resolve()), *command]
        with (output / "render.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        from .animation import export
        for extension in ('mp4', 'gif'):
            export(output / 'render.frames.zip', output / f'{args.motion}.{extension}', executable=ffmpeg)
        manifest = json.loads((output / "manifest.json").read_text())
        manifest.update(animation={"kind": "camera_orbit", "axis": "initial_camera_up",
            "motion": args.motion,
            **({"rotation_deg": 360} if args.motion == "spin" else {"amplitude_deg": amplitude}), "angles_deg": motion, "frames": args.frames,
            "fps": args.fps, "duration_seconds": args.frames / args.fps,
            "loop": ("constant speed" if args.motion == "spin" else "sinusoidal") + ", no duplicate endpoint"},
            bundle_sha256=digest(args.bundle), render_spec=resolved["render_spec"],
            source_sha256={name: digest(package / name) for name in
                ("rocking.py", "rocking_blender.py", "animation.py", "animation_blender.py", "blender_custom_render.py")},
            artifacts={name: digest(output / name) for name in
                (f"{args.motion}.mp4", f"{args.motion}.gif", "render.png", "render.frames.zip", "resolved-style.json")})
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
