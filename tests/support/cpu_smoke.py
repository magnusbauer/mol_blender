"""One explicit checkout preview of the public CPU fixture, using shared execution."""
import json
from pathlib import Path
import tempfile

from blender_render import developer, runtime


def run_cpu_smoke(*, checkout, runtime_config, output):
    checkout = Path(checkout).resolve()
    if Path(runtime.__file__).resolve().parent != checkout / "blender_render":
        raise ValueError("Import blender_render from the selected checkout (set PYTHONPATH to that checkout)")
    config = json.loads(Path(runtime_config).read_text())
    site = Path(config["runtime_site"])
    options = json.loads((checkout / "examples/cpu-smoke-options.json").read_text())
    threads = options["overrides"]["renderer"]["threads"]
    with tempfile.TemporaryDirectory(prefix="blender-cpu-smoke-") as directory:
        scratch = Path(directory)
        payload = runtime.export_file(checkout / "examples/1bna.cif",
                                      python=config["pymol_python"], process_threads=threads)
        bundle = scratch / "bundle.json"
        bundle.write_text(json.dumps(payload))
        return developer.preview(checkout=checkout, bundle=bundle, options=options,
                                 catalog_dir=scratch, output=Path(output),
                                 blender=Path(config["blender"]), runtime_paths=[site],
                                 ccd=site / "biotite/structure/info/components.bcif",
                                 timeout=180, process_threads=threads)
