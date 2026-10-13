"""Render the public 1BNA fixture on two CPU threads at 128 x 128, four samples.

Reuse an explicitly supplied local runtime; never install or modify that runtime.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from blender_render.developer import preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    checkout = Path(__file__).resolve().parents[1]
    config = json.loads(args.runtime_config.read_text())
    runtime = Path(config["runtime_site"])
    options = json.loads((checkout / "examples/cpu-smoke-options.json").read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="blender-cpu-smoke-") as scratch:
        bundle = Path(scratch) / "bundle.json"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="2",
                   OPENBLAS_NUM_THREADS="2", VECLIB_MAXIMUM_THREADS="2")
        subprocess.run([config["pymol_python"], str(checkout / "blender_render/export.py"),
                        str(checkout / "examples/1bna.cif"), str(bundle)],
                       check=True, env=env, timeout=120, capture_output=True, text=True)
        result = preview(checkout=checkout, bundle=bundle, options=options,
                         catalog_dir=Path(scratch), output=args.output,
                         blender=Path(config["blender"]), runtime_paths=[runtime],
                         ccd=runtime / "biotite/structure/info/components.bcif", timeout=180)
    from PIL import Image
    with Image.open(args.output / "render.png") as image:
        assert image.size == (128, 128), image.size
    manifest = json.loads((args.output / "manifest.json").read_text())
    assert manifest["cycles_device"]["device"] == "CPU"
    assert manifest["render_threads"] == {"mode": "FIXED", "count": 2}
    assert manifest["resolved_preset"]["output"]["samples"] == 4
    command = result["worker"]["command"]
    assert command[command.index("--threads") + 1] == "2"
    print(json.dumps({"status": result["status"], "device": "CPU", "threads": 2,
                      "size": [128,128], "samples": 4,
                      "worker_seconds": result["worker"]["runtime_seconds"],
                      "output": str(args.output.resolve())}, indent=2))


if __name__ == "__main__":
    main()
