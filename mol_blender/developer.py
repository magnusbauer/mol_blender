"""Trusted-checkout developer CLI. Fresh processes, no services or remote profiles."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(package: Path) -> dict[str, str]:
    """All shipped package code/assets, including relevant uncommitted files."""
    files = {}
    for path in sorted(package.rglob("*")):
        if path.name == ".DS_Store" or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("Development source snapshots do not accept symlinks: " + str(path))
        if path.is_file():
            files[path.relative_to(package).as_posix()] = digest_file(path)
    return files


def source_digest(files: dict) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def snapshot_source(checkout: Path, destination: Path) -> dict:
    source = checkout / "mol_blender"
    if not (source / "blender_custom_render.py").is_file():
        raise ValueError("Expected a trusted checkout with mol_blender/blender_custom_render.py")
    before = source_files(source)
    for name in before:
        target = destination / "mol_blender" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    copied = source_files(destination / "mol_blender")
    if before != copied or before != source_files(source):
        raise RuntimeError("Source changed while snapshotting; retry once edits are saved")
    def git(*args):
        try:
            result = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)
        except FileNotFoundError:
            # Installed wheels and minimal containers need no Git executable.
            # The complete byte inventory above remains the source identity.
            return None
        return result.stdout.strip() if result.returncode == 0 else None
    return {"checkout": str(checkout), "snapshot": str(destination),
            "git_head": git("rev-parse", "HEAD"), "git_branch": git("branch", "--show-current"),
            "git_status": git("status", "--short"), "files": before, "sha256": source_digest(before)}


def isolated_environment(root: Path) -> dict[str, str]:
    # Do not inherit tokens, hosted configuration, developer PYTHONPATH, or a
    # production worker socket. Runtime import locations are explicit inputs.
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "SYSTEMROOT", "LANG") if key in os.environ}
    # Keep resource restrictions through the isolated subprocess. Dropping CUDA
    # visibility would let a Slurm render enumerate GPUs outside its allocation.
    for key in ('CUDA_VISIBLE_DEVICES', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
        value = os.environ.get(key)
        if value is None:
            continue
        pattern = r'[A-Za-z0-9_,./-]{0,256}' if key == 'CUDA_VISIBLE_DEVICES' else r'[1-9][0-9]{0,3}'
        if not re.fullmatch(pattern, value):
            raise ValueError('Invalid compute resource setting: ' + key)
        env[key] = value
    # Apptainer --nv places host driver libraries here. Preserve only this fixed
    # container path, never arbitrary caller LD_LIBRARY_PATH entries.
    driver_path = '/.singularity.d/libs'
    if (os.environ.get('APPTAINER_CONTAINER') and Path(driver_path).is_dir() and
            driver_path in os.environ.get('LD_LIBRARY_PATH', '').split(os.pathsep)):
        env['LD_LIBRARY_PATH'] = driver_path
    for folder in ("home", "tmp", "config", "sessions", "runs", "cache", "blender-config", "blender-resources"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(root / "home"), TMPDIR=str(root / "tmp"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               MOL_BLENDER_HOME=str(root / "config"),
               PYMOL_BRIDGE_SESSION_ROOT=str(root / "sessions"),
               PYMOL_BRIDGE_TMP_ROOT=str(root / "runs"),
               XDG_CACHE_HOME=str(root / "cache"), BLENDER_USER_CONFIG=str(root / "blender-config"),
               BLENDER_USER_RESOURCES=str(root / "blender-resources"))
    if os.name == 'nt':
        env.update(TEMP=str(root / 'tmp'), TMP=str(root / 'tmp'),
                   USERPROFILE=str(root / 'home'), APPDATA=str(root / 'config'),
                   LOCALAPPDATA=str(root / 'cache'), PYTHONUTF8='1')
    return env


def stage_runtime_wheels(directory: Path, destination: Path) -> dict[str, str]:
    """Reuse installed extension wheels without invoking Blender's shared installer."""
    wheels = sorted(directory.glob("*.whl"))
    if not wheels:
        raise ValueError("No bundled runtime wheels in " + str(directory))
    hashes = {}
    for wheel in wheels:
        data = wheel.read_bytes()
        hashes[wheel.name] = hashlib.sha256(data).hexdigest()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if not (destination / member.filename).resolve().is_relative_to(destination.resolve()):
                    raise ValueError("Invalid bundled wheel path")
            archive.extractall(destination)
    return hashes


def capture_preset(identifier: str, catalog_dir: Path, destination: Path) -> dict:
    """Capture exact bytes; only snapshot Python may interpret the envelope/style."""
    from mol_blender.authoring import _local_path
    data = _local_path(identifier, catalog_dir).read_bytes()
    path = _local_path(identifier, destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"id": identifier, "sha256": hashlib.sha256(data).hexdigest()}


def preview(*, checkout: Path, bundle: Path, options: dict, catalog_dir: Path,
            output: Path, blender: Path, runtime_paths: list[Path], ccd: Path,
            timeout: float = 600, keep_source: bool = False, runtime_wheels: Path | None = None,
            warm_worker: dict | None = None, cancelled=None) -> dict:
    """Snapshot and resolve in fresh Python; optionally use an identical warm Blender."""
    from mol_blender.errors import RenderCancelled
    if warm_worker and cancelled is not None:
        raise ValueError('Cancellation callbacks require a fresh local render')
    if cancelled is not None and cancelled():
        raise RenderCancelled('Local render cancelled')
    if timeout < 30:
        raise ValueError("Preview timeout must be at least 30 seconds")
    for path in (bundle, blender, ccd):
        if not path.is_file():
            raise ValueError("Required local input/runtime file is missing: " + str(path))
    if not runtime_paths or not all(path.is_dir() for path in runtime_paths):
        raise ValueError("Provide existing dependency/extension directories with --runtime-path")
    output.mkdir(parents=True, exist_ok=False)
    run = Path(tempfile.mkdtemp(prefix="mol-blender-dev-"))
    try:
        identity = snapshot_source(checkout.resolve(), run / "source")
        if warm_worker and warm_worker['source_sha256'] != identity['sha256']:
            raise RuntimeError('Warm worker source changed; stop the worker and retry')
        paths = [str(path.resolve()) for path in runtime_paths]
        if runtime_wheels:
            identity["runtime_wheels"] = stage_runtime_wheels(runtime_wheels, run / "runtime")
            paths.insert(0, str(run / "runtime"))
        # Input and catalog bytes are captured too: no mutable files are read by
        # a queued preview after dispatch. Resolution remains in snapshot Python.
        (run / "bundle.json").write_bytes(bundle.read_bytes())
        identifier = options.get("preset", "")
        if "style_doc" not in options and identifier.startswith(("user/", "project/")):
            identity["catalog_snapshot"] = capture_preset(identifier, catalog_dir, run / "catalog")
        identity["bundle_sha256"] = digest_file(run / "bundle.json")
        # Freeze CCD input before dispatch. The Blender process captures this
        # private copy again; rendering never uses the caller's runtime file.
        (run / "components.bcif").write_bytes(ccd.read_bytes())
        config = {"identity": identity, "options": options, "root": str(run),
                  "output": str(output.resolve()), "blender": str(blender.resolve()),
                  "runtime_paths": paths,
                  "runtime_source_paths": [str(path.resolve()) for path in runtime_paths],
                  "ccd": str(run / "components.bcif"), "ccd_source": str(ccd.resolve()),
                  "ccd_sha256": digest_file(run / "components.bcif"), "timeout": timeout}
        if warm_worker:
            config['warm_worker'] = warm_worker
        config_path = run / "preview.json"
        config_path.write_text(json.dumps(config, indent=2, allow_nan=False))
        with (output / "preview.log").open("w") as log:
            from mol_blender.platform_support import owned_process
            process_owner = owned_process([sys.executable, "-B", "-I", str(run / "source/mol_blender/developer.py"),
                                        "_run", str(config_path)], cwd=run, env=isolated_environment(run),
                                       stdout=log, stderr=subprocess.STDOUT)
            process = process_owner.__enter__()
            try:
                deadline = time.monotonic() + timeout + 30
                while True:
                    if cancelled is not None and cancelled():
                        raise RenderCancelled('Local render cancelled')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(process.args, timeout + 30)
                    try:
                        code = process.wait(timeout=min(.2, remaining) if cancelled is not None else remaining)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                # Reap owned descendants before removing their source snapshot.
                process_owner.__exit__(None, None, None)
        if code:
            raise RuntimeError("Preview failed; see " + str(output / "preview.log"))
        return json.loads((output / "preview.json").read_text())
    finally:
        if keep_source:
            (output / "retained-source.txt").write_text(str(run) + "\n")
        else:
            from mol_blender.platform_support import remove_tree
            remove_tree(run)


def _run(config_path: Path) -> None:
    """Fresh snapshot Python process; the ordinary rendering transaction owns staging."""
    from mol_blender.authoring import resolve_authored
    from mol_blender.config import VERSION
    from mol_blender.core import validate_payload_document, build_local_blender_command
    from mol_blender.rendering import RenderState, _process_render_request
    from mol_blender.errors import WorkerError

    cfg = json.loads(config_path.read_text())
    root, output = Path(cfg["root"]), Path(cfg["output"])
    package = Path(__file__).resolve().parent
    identity = cfg["identity"]
    if source_files(package) != identity["files"]:
        raise RuntimeError("Source snapshot identity mismatch")
    if "catalog_snapshot" in identity:
        from mol_blender.authoring import _local_path
        captured = identity["catalog_snapshot"]
        if digest_file(_local_path(captured["id"], root / "catalog")) != captured["sha256"]:
            raise RuntimeError("Catalog snapshot digest mismatch")
    payload = validate_payload_document(json.loads((root / "bundle.json").read_text()))
    resolved = resolve_authored(cfg["options"], payload, root / "catalog")
    resolved["job_timeout_seconds"] = cfg["timeout"]
    if resolved["style_doc"]["output"].get("raw_view"):
        raise ValueError("Developer previews require a PNG render, not raw_view")
    (output / "resolved-style.json").write_text(json.dumps(resolved["style_doc"], indent=2))
    identity.update(render_spec=resolved["render_spec"], package_version=VERSION,
                    controller_module=str(Path(__file__).resolve()), controller_python=sys.executable,
                    controller_python_version=sys.version, cache_hit=False)
    # Code, input, spec and runtime identity are recorded even though previews
    # deliberately have no cache. A failed run cannot be returned as an old PNG.
    identity["preview_key"] = source_digest({"source": identity["sha256"],
        "bundle": identity["bundle_sha256"], "spec": resolved["render_spec"]["sha256"],
        "blender": digest_file(Path(cfg["blender"])), "ccd": cfg["ccd_sha256"],
        "runtime_paths": cfg["runtime_source_paths"], "runtime_wheels": identity.get("runtime_wheels", {})})
    (output / "source.json").write_text(json.dumps(identity, indent=2))

    class FreshBlender:
        def submit_render(self, *, structure_path, view_json, style_json, output_png, run_manifest, timeout):
            command = build_local_blender_command(blender_exe=cfg["blender"], preset=None,
                structure_path=structure_path, view_json_path=view_json, style_json_path=style_json,
                output_png=output_png, custom_script=package / "developer_blender.py",
                run_manifest_path=run_manifest)
            command[1:1] = ["--factory-startup", "--disable-autoexec", "--python-use-system-env", "--python-exit-code", "1"]
            env = dict(os.environ)
            env.update(PYTHONPATH=os.pathsep.join([str(package.parent), *cfg["runtime_paths"]]),
                       PRESS_BIOTITE_CCD_PATH=cfg["ccd"], MOL_BLENDER_DEV_CONFIG=str(config_path))
            started = time.monotonic()
            with (output / "blender.log").open("a") as log:
                process = subprocess.Popen(command, cwd=root / "source", env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    code = process.wait(timeout=timeout)
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
            if code:
                raise WorkerError("Fresh Blender preview failed; see " + str(output / "blender.log"))
            return {"pid": process.pid, "runtime_seconds": time.monotonic() - started,
                    "command": command, "fresh_process": True}

    worker = FreshBlender()
    if cfg.get('warm_worker'):
        from mol_blender.warm_worker import RemoteBlender
        worker = RemoteBlender(cfg['warm_worker'])
    result = _process_render_request(request_id=root.name, request_doc={"payload": payload, **resolved},
                                    state=RenderState(worker, cfg["timeout"]))
    (output / "render.png").write_bytes(base64.b64decode(result.pop("png_base64")))
    if "blend_base64" in result:
        (output / "render.blend").write_bytes(base64.b64decode(result.pop("blend_base64")))
    for key, name in [('mp4_base64', 'render.mp4'), ('gif_base64', 'render.gif'), ('frames_base64', 'frames.zip')]:
        if key in result:
            (output / name).write_bytes(base64.b64decode(result.pop(key)))
    from mol_blender.animation import required_names
    for name in required_names(resolved['style_doc']):
        if not (output / name).is_file():
            raise RuntimeError('Render artifact missing: ' + name)
    (output / "manifest.json").write_text(json.dumps(result["manifest"], indent=2))
    identity.update(blender=result["manifest"]["development_source"], worker=result["worker"],
                    png_sha256=digest_file(output / "render.png"), status="completed", output=str(output))
    (output / "preview.json").write_text(json.dumps(identity, indent=2))


def parser() -> argparse.ArgumentParser:
    from mol_blender.config import config_root
    from mol_blender.presets import DEFAULT_PRESET
    p = argparse.ArgumentParser(prog="mol-blender dev", description=__doc__)
    p.add_argument("--catalog-dir", type=Path, default=config_root() / "presets")
    commands = p.add_subparsers(dest="action", required=True)
    commands.add_parser("catalog")
    commands.add_parser("schema")
    edited = commands.add_parser('from-blend', help='Save a local preset from an edited mol-blender scene')
    edited.add_argument('scene', type=Path)
    edited.add_argument('--preset', required=True, help='New user/name or project/name preset')
    edited.add_argument('--description', default='')
    edited.add_argument('--blender', default='')
    edited.add_argument('--camera-policy', choices=('preserve_input', 'authored'), default='preserve_input')
    edited.add_argument('--replace', action='store_true')
    edited.add_argument('--allow-partial', action='store_true', help='Explicitly accept reported unsupported edits being omitted')
    edited.add_argument('--report', type=Path, help='New JSON file for the extracted style and unsupported-change report')
    edited.add_argument('--timeout', type=float, default=120)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("preset")
    validate = commands.add_parser("validate")
    validate.add_argument("style_json", type=Path)
    save = commands.add_parser("save")
    save.add_argument("preset")
    save.add_argument("style_json", type=Path)
    save.add_argument("--description", required=True)
    save.add_argument("--provenance", type=Path)
    save.add_argument("--replace", action="store_true")
    save.add_argument("--camera-policy", choices=("preserve_input", "authored"), default="preserve_input",
                      help="Preserve input pose by default; authored reuses the saved camera")
    reference = commands.add_parser("reference")
    reference.add_argument("image", type=Path)
    reference.add_argument("--observations", type=Path, required=True)
    reference.add_argument("--style-output", type=Path, help="Write a validated full style JSON from the recorded base and overrides")
    run = commands.add_parser("preview")
    run.add_argument("bundle", type=Path, help="JSON StructureBundle from the existing exporter")
    run.add_argument("--checkout", type=Path, default=Path.cwd(),
                     help="Trusted checkout; defaults to the working directory, never installed site-packages")
    select = run.add_mutually_exclusive_group()
    select.add_argument("--preset", default=DEFAULT_PRESET)
    select.add_argument("--style-json", type=Path)
    run.add_argument("--overrides-json", type=Path)
    run.add_argument("--output", type=Path, required=True, help="New directory; never overwrite a previous preview")
    run.add_argument("--blender", type=Path, required=True)
    run.add_argument("--runtime-path", type=Path, action="append", required=True)
    run.add_argument("--runtime-wheels", type=Path, help="Existing extension's bundled wheel directory; extracted only into isolated state")
    run.add_argument("--ccd", type=Path, required=True)
    run.add_argument("--timeout", type=float, default=600)
    run.add_argument("--keep-source", action="store_true", help="Retain isolated snapshot/state for debugging")
    run.add_argument("--save-blend", action="store_true", default=None)
    run.add_argument("--preserve-view", action=argparse.BooleanOptionalAction, default=None,
                     help="Override the stored camera policy; explicit camera JSON wins")
    return p


def execute(args):
    from mol_blender import authoring, core
    if args.action == 'from-blend':
        from mol_blender.scene.edit import save_from_blend
        return save_from_blend(args.scene, args.preset, description=args.description, catalog_dir=args.catalog_dir,
                               blender=args.blender, camera_policy=args.camera_policy, replace=args.replace,
                               allow_partial=args.allow_partial, report=args.report, timeout=args.timeout)
    if args.action == "catalog":
        return authoring.list_presets(args.catalog_dir)
    if args.action == "schema":
        return json.loads(core.STYLE_SCHEMA_PATH.read_text())
    if args.action == "inspect":
        return authoring.inspect_preset(args.preset, args.catalog_dir)
    if args.action == "validate":
        from mol_blender.spec import style_digest
        style = core.load_and_validate_style_json(args.style_json)
        return {"valid": True, "sha256": style_digest(style)}
    if args.action == "save":
        return authoring.save_preset(args.preset, core.load_and_validate_style_json(args.style_json),
            args.catalog_dir, description=args.description, replace=args.replace,
            camera_policy=args.camera_policy,
            provenance=json.loads(args.provenance.read_text()) if args.provenance else None)
    if args.action == "reference":
        brief = authoring.reference_brief(args.image, json.loads(args.observations.read_text()))
        if args.style_output:
            from mol_blender.spec import resolve_options
            decision = brief["preset_decision"]
            style = resolve_options({"preset": decision["preset_id"], "overrides": decision["overrides"]}, {})["style_doc"]
            args.style_output.parent.mkdir(parents=True, exist_ok=True)
            with args.style_output.open("x") as output:
                output.write(json.dumps(style, indent=2, allow_nan=False) + "\n")
            brief["style_output"] = str(args.style_output.resolve())
        return brief
    options = {"style_doc": json.loads(args.style_json.read_text())} if args.style_json else {"preset": args.preset}
    if args.overrides_json:
        options["overrides"] = json.loads(args.overrides_json.read_text())
    if args.save_blend is not None:
        options["save_blend"] = args.save_blend
    if args.preserve_view is not None:
        options["preserve_view"] = args.preserve_view
    return preview(checkout=args.checkout, bundle=args.bundle, options=options, catalog_dir=args.catalog_dir,
                   output=args.output, blender=args.blender, runtime_paths=args.runtime_path,
                   ccd=args.ccd, timeout=args.timeout, keep_source=args.keep_source, runtime_wheels=args.runtime_wheels)


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        if args and args[0] == "_run":
            _run(Path(args[1]))
        else:
            print(json.dumps(execute(parser().parse_args(args)), indent=2))
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print("mol-blender dev: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
