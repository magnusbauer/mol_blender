"""Trusted-checkout developer CLI. Fresh processes, no services or remote profiles."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
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
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("Development source snapshots do not accept symlinks: " + str(path))
        if path.is_file():
            files[path.relative_to(package).as_posix()] = digest_file(path)
    return files


def source_digest(files: dict) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def snapshot_source(checkout: Path, destination: Path) -> dict:
    source = checkout / "blender_render"
    if not (source / "blender_custom_render.py").is_file():
        raise ValueError("Expected a trusted checkout with blender_render/blender_custom_render.py")
    before = source_files(source)
    for name in before:
        target = destination / "blender_render" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    copied = source_files(destination / "blender_render")
    if before != copied or before != source_files(source):
        raise RuntimeError("Source changed while snapshotting; retry once edits are saved")
    def git(*args):
        result = subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    return {"checkout": str(checkout), "snapshot": str(destination),
            "git_head": git("rev-parse", "HEAD"), "git_branch": git("branch", "--show-current"),
            "git_status": git("status", "--short"), "files": before, "sha256": source_digest(before)}


def isolated_environment(root: Path) -> dict[str, str]:
    # Do not inherit tokens, hosted configuration, developer PYTHONPATH, or a
    # production worker socket. Runtime import locations are explicit inputs.
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "SYSTEMROOT", "LANG") if key in os.environ}
    for folder in ("home", "tmp", "config", "sessions", "runs", "cache", "blender-config", "blender-resources"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(root / "home"), TMPDIR=str(root / "tmp"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               BLENDER_RENDER_HOME=str(root / "config"),
               PYMOL_BRIDGE_SESSION_ROOT=str(root / "sessions"),
               PYMOL_BRIDGE_TMP_ROOT=str(root / "runs"),
               XDG_CACHE_HOME=str(root / "cache"), BLENDER_USER_CONFIG=str(root / "blender-config"),
               BLENDER_USER_RESOURCES=str(root / "blender-resources"))
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
    from blender_render.authoring import _local_path
    data = _local_path(identifier, catalog_dir).read_bytes()
    path = _local_path(identifier, destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"id": identifier, "sha256": hashlib.sha256(data).hexdigest()}


def preview(*, checkout: Path, bundle: Path, options: dict, catalog_dir: Path,
            output: Path, blender: Path, runtime_paths: list[Path], ccd: Path,
            timeout: float = 600, keep_source: bool = False, runtime_wheels: Path | None = None,
            process_threads: int | None = None) -> dict:
    """Snapshot, then resolve AND render using the snapshot in fresh processes."""
    if timeout < 30:
        raise ValueError("Preview timeout must be at least 30 seconds")
    from blender_render.config import process_thread_count
    process_threads = process_thread_count(process_threads)
    for path in (bundle, blender, ccd):
        if not path.is_file():
            raise ValueError("Required local input/runtime file is missing: " + str(path))
    if not runtime_paths or not all(path.is_dir() for path in runtime_paths):
        raise ValueError("Provide existing dependency/extension directories with --runtime-path")
    output.mkdir(parents=True, exist_ok=False)
    run = Path(tempfile.mkdtemp(prefix="blender-render-dev-"))
    try:
        identity = snapshot_source(checkout.resolve(), run / "source")
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
                  "ccd_sha256": digest_file(run / "components.bcif"), "timeout": timeout,
                  "process_threads": process_threads}
        config_path = run / "preview.json"
        config_path.write_text(json.dumps(config, indent=2, allow_nan=False))
        with (output / "preview.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-B", "-I", str(run / "source/blender_render/developer.py"),
                                        "_run", str(config_path)], cwd=run, env=isolated_environment(run),
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout + 30)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        if code:
            raise RuntimeError("Preview failed; see " + str(output / "preview.log"))
        return json.loads((output / "preview.json").read_text())
    finally:
        if keep_source:
            (output / "retained-source.txt").write_text(str(run) + "\n")
        else:
            shutil.rmtree(run)


def _run(config_path: Path) -> None:
    """Fresh snapshot Python process; the ordinary rendering transaction owns staging."""
    from blender_render.authoring import resolve_authored
    from blender_render.config import VERSION, blender_thread_arguments, process_thread_environment
    from blender_render.core import validate_payload_document, build_local_blender_command
    from blender_render.rendering import RenderServerState, _process_render_request
    from blender_render.render_worker_manager import WorkerError

    cfg = json.loads(config_path.read_text())
    root, output = Path(cfg["root"]), Path(cfg["output"])
    package = Path(__file__).resolve().parent
    identity = cfg["identity"]
    if source_files(package) != identity["files"]:
        raise RuntimeError("Source snapshot identity mismatch")
    if "catalog_snapshot" in identity:
        from blender_render.authoring import _local_path
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
    identity["execution"] = {"process_threads": cfg.get("process_threads")}
    # Code, input, spec and runtime identity are recorded even though previews
    # deliberately have no cache. A failed run cannot be returned as an old PNG.
    identity["preview_key"] = source_digest({"source": identity["sha256"],
        "bundle": identity["bundle_sha256"], "spec": resolved["render_spec"]["sha256"],
        "blender": digest_file(Path(cfg["blender"])), "ccd": cfg["ccd_sha256"],
        "runtime_paths": cfg["runtime_source_paths"], "runtime_wheels": identity.get("runtime_wheels", {}),
        "process_threads": cfg.get("process_threads")})
    (output / "source.json").write_text(json.dumps(identity, indent=2))

    class FreshBlender:
        def submit_render(self, *, structure_path, view_json, style_json, output_png, run_manifest, timeout):
            command = build_local_blender_command(blender_exe=cfg["blender"], preset=None,
                structure_path=structure_path, view_json_path=view_json, style_json_path=style_json,
                output_png=output_png, custom_script=package / "developer_blender.py",
                run_manifest_path=run_manifest, use_apptainer=False, apptainer_image="")
            command[1:1] = ["--factory-startup", "--disable-autoexec", "--python-use-system-env", "--python-exit-code", "1"]
            threads = cfg.get("process_threads")
            command[1:1] = blender_thread_arguments(threads)
            env = dict(os.environ)
            env.update(PYTHONPATH=os.pathsep.join([str(package.parent), *cfg["runtime_paths"]]),
                       PRESS_BIOTITE_CCD_PATH=cfg["ccd"], BLENDER_RENDER_DEV_CONFIG=str(config_path))
            env.update(process_thread_environment(threads))
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

    result = _process_render_request(request_id=root.name, request_doc={"payload": payload, **resolved},
                                    state=RenderServerState(FreshBlender(), cfg["timeout"]))
    (output / "render.png").write_bytes(base64.b64decode(result.pop("png_base64")))
    if "blend_base64" in result:
        (output / "render.blend").write_bytes(base64.b64decode(result.pop("blend_base64")))
    (output / "manifest.json").write_text(json.dumps(result["manifest"], indent=2))
    identity.update(blender=result["manifest"]["development_source"], worker=result["worker"],
                    png_sha256=digest_file(output / "render.png"), status="completed", output=str(output))
    (output / "preview.json").write_text(json.dumps(identity, indent=2))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="blender-render dev", description=__doc__)
    p.add_argument("--catalog-dir", type=Path, default=Path.home() / ".config/blender-render/presets")
    commands = p.add_subparsers(dest="action", required=True)
    commands.add_parser("catalog")
    commands.add_parser("schema")
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
    run = commands.add_parser("preview")
    run.add_argument("bundle", type=Path, help="JSON StructureBundle from the existing exporter")
    run.add_argument("--checkout", type=Path, default=Path.cwd(),
                     help="Trusted checkout; defaults to the working directory, never installed site-packages")
    select = run.add_mutually_exclusive_group()
    select.add_argument("--preset", default="preset_1_interface")
    select.add_argument("--style-json", type=Path)
    run.add_argument("--overrides-json", type=Path)
    run.add_argument("--output", type=Path, required=True, help="New directory; never overwrite a previous preview")
    run.add_argument("--blender", type=Path, required=True)
    run.add_argument("--runtime-path", type=Path, action="append", required=True)
    run.add_argument("--runtime-wheels", type=Path, help="Existing extension's bundled wheel directory; extracted only into isolated state")
    run.add_argument("--ccd", type=Path, required=True)
    run.add_argument("--timeout", type=float, default=600)
    run.add_argument("--process-threads", type=int, help="Blender startup and numerical-library threads; separate from renderer.threads")
    run.add_argument("--keep-source", action="store_true", help="Retain isolated snapshot/state for debugging")
    run.add_argument("--save-blend", action="store_true", default=None)
    run.add_argument("--preserve-view", action=argparse.BooleanOptionalAction, default=None,
                     help="Override the stored camera policy; explicit camera JSON wins")
    return p


def execute(args):
    from blender_render import authoring, core
    if args.action == "catalog":
        return authoring.list_presets(args.catalog_dir)
    if args.action == "schema":
        return json.loads(core.STYLE_SCHEMA_PATH.read_text())
    if args.action == "inspect":
        return authoring.inspect_preset(args.preset, args.catalog_dir)
    if args.action == "validate":
        from blender_render.spec import style_digest
        style = core.load_and_validate_style_json(args.style_json)
        return {"valid": True, "sha256": style_digest(style)}
    if args.action == "save":
        return authoring.save_preset(args.preset, core.load_and_validate_style_json(args.style_json),
            args.catalog_dir, description=args.description, replace=args.replace,
            camera_policy=args.camera_policy,
            provenance=json.loads(args.provenance.read_text()) if args.provenance else None)
    if args.action == "reference":
        return authoring.reference_brief(args.image, json.loads(args.observations.read_text()))
    options = {"style_doc": json.loads(args.style_json.read_text())} if args.style_json else {"preset": args.preset}
    if args.overrides_json:
        options["overrides"] = json.loads(args.overrides_json.read_text())
    if args.save_blend is not None:
        options["save_blend"] = args.save_blend
    if args.preserve_view is not None:
        options["preserve_view"] = args.preserve_view
    return preview(checkout=args.checkout, bundle=args.bundle, options=options, catalog_dir=args.catalog_dir,
                   output=args.output, blender=args.blender, runtime_paths=args.runtime_path,
                   ccd=args.ccd, timeout=args.timeout, keep_source=args.keep_source, runtime_wheels=args.runtime_wheels,
                   process_threads=args.process_threads)


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        if args and args[0] == "_run":
            _run(Path(args[1]))
        else:
            print(json.dumps(execute(parser().parse_args(args)), indent=2))
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print("blender-render dev: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
