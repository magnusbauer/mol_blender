"""One structure-to-image transaction, shared by every compute backend."""
from __future__ import annotations
import base64
import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from blender_render import core as _bridge_runner
from blender_render.render_worker_manager import WorkerError

@dataclass
class RenderServerState:
    worker: Any
    default_job_timeout: float = 600
    verbose: bool = False
    cancelled: Any = None
    progress: Any = None

    def log(self, message):
        logging.getLogger(__name__).debug(message)

def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def _ensure_png_suffix(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        return "render.png"
    if value.lower().endswith(".png"):
        return value
    return f"{value}.png"


def _suggest_output_name(*, request_doc: dict[str, Any], preset: str, style_doc: dict[str, Any]) -> str:
    requested = str(request_doc.get("output_name", "")).strip()
    if requested:
        return _ensure_png_suffix(requested)

    if preset:
        preset_name = _bridge_runner.PRESET_OUTPUT_FILENAMES.get(preset)
        if preset_name:
            return _ensure_png_suffix(preset_name)

    output = style_doc.get("output") if isinstance(style_doc, dict) else None
    if isinstance(output, dict):
        configured = str(output.get("filename", "")).strip()
        if configured:
            return _ensure_png_suffix(configured)
    return "custom_render.png"


def _resolved_request(request_doc: dict[str, Any]) -> dict[str, Any]:
    from blender_render.spec import OPTION_KEYS, resolve_options, style_digest
    envelope_keys = {"payload", "output_name", "job_timeout_seconds", "style_json", "blend_output", "render_spec",
                     "bundle_sha256", "expected_engine", "execution_context"}
    unknown = set(request_doc) - OPTION_KEYS - envelope_keys
    if unknown:
        raise ValueError("Unsupported render options: " + ", ".join(sorted(unknown)))
    # Current services send a snapshot. Legacy direct callers are translated
    # into the same resolver once, before any worker or staging activity.
    if "render_spec" in request_doc:
        if 'bundle_sha256' in request_doc:
            from blender_render.execution import request_snapshot, validate_snapshot
            validate_snapshot(request_snapshot(request_doc), timeout=request_doc['job_timeout_seconds'], max_bytes=512*1024**2)
        elif {'expected_engine', 'execution_context'} & request_doc.keys():
            raise ValueError('Incomplete execution envelope')
        style = _bridge_runner.validate_style_document(request_doc.get("style_doc"))
        metadata = request_doc["render_spec"]
        if not isinstance(metadata, dict) or metadata.get("sha256") != style_digest(style):
            raise _bridge_runner.RunnerError("Resolved render specification hash mismatch")
        from blender_render.presets import required_capabilities
        if metadata.get('required_capabilities') != required_capabilities(style):
            raise _bridge_runner.RunnerError('Resolved render capability mismatch')
        return request_doc
    options = {key: request_doc[key] for key in OPTION_KEYS if key in request_doc}
    if request_doc.get("style_json") and "style_doc" not in options:
        options["style_doc"] = _bridge_runner.load_and_validate_style_json(Path(request_doc["style_json"]).expanduser())
    if request_doc.get("blend_output"):
        options["save_blend"] = True
    resolved = resolve_options(options, request_doc.get("payload", {}), timeout=request_doc.get("job_timeout_seconds", 600))
    if request_doc.get("output_name"):
        resolved["output_name"] = request_doc["output_name"]
    return {**request_doc, **resolved}


def _prepare_style_doc(request_doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    resolved = _resolved_request(request_doc)
    return resolved.get("preset", ""), resolved["style_doc"]


def _process_render_request(*, request_id: str, request_doc: dict[str, Any], state: RenderServerState) -> dict[str, Any]:
    started = time.monotonic()
    state.log(f"request-validate id={request_id}")
    payload = request_doc.get("payload")
    if not isinstance(payload, dict):
        raise _bridge_runner.RunnerError("Request JSON must include object field 'payload'.")
    payload = _bridge_runner.validate_payload_document(payload)

    request_doc = _resolved_request(request_doc)
    preset, style_doc = request_doc.get("preset", ""), request_doc["style_doc"]
    state.log(f"request-style-ready id={request_id} preset={preset or '<style_doc>'}")

    animation = style_doc['output'].get('animation')
    animation_encoder = None
    if animation and animation.get('mode') != 'none':
        from .animation import encoder
        animation_encoder = encoder()  # Fail before GPU work on this execution host.
    keep_temp = _to_bool(request_doc.get("keep_temp", False))
    job_timeout_raw = request_doc.get("job_timeout_seconds")
    job_timeout = state.default_job_timeout
    if job_timeout_raw is not None:
        try:
            job_timeout = max(30.0, float(job_timeout_raw))
        except Exception:
            job_timeout = state.default_job_timeout

    deadline = started + job_timeout
    run_id_raw = payload.get("run_id")
    run_id = str(run_id_raw).strip() if isinstance(run_id_raw, str) and run_id_raw.strip() else request_id

    temp_root: Path | None = None
    try:
        temp_root, _bundle_dir, structure_path, pdb_fallback_path, view_path = _bridge_runner._materialize_payload_bundle(payload, run_id)
        state.log(
            "request-bundle id="
            f"{request_id} temp_root={temp_root} structure={structure_path.name} "
            f"pdb_fallback={int(pdb_fallback_path is not None)}"
        )
        run_manifest_path = temp_root / "run_manifest.json"
        _bridge_runner._write_run_manifest(
            run_manifest_path,
            payload,
            chosen_structure_path=structure_path,
            style_doc=style_doc,
        )

        manifest = _bridge_runner._load_json(run_manifest_path)
        manifest["render_spec"] = request_doc["render_spec"]
        manifest["resolved_preset"] = style_doc
        manifest['bundle_sha256'] = request_doc.get('bundle_sha256')
        manifest['expected_engine'] = request_doc.get('expected_engine')
        _bridge_runner._write_json_atomic(run_manifest_path, manifest)

        style_json_path = temp_root / "style.json"
        style_json_path.write_text(json.dumps(style_doc, indent=2), encoding="utf-8")

        preset_gate_target = _bridge_runner._is_preset_1_gate_target(
            preset=preset,
            style_json_path=str(request_doc.get("style_json", "") or ""),
            style_doc=style_doc,
        )
        if preset_gate_target and structure_path.suffix.lower() != ".cif":
            raise _bridge_runner.RunnerError(
                "Preset gate failure for preset_1_interface. Payload did not provide a usable CIF export."
            )

        output_name = _suggest_output_name(request_doc=request_doc, preset=preset, style_doc=style_doc)
        output_png = temp_root / output_name
        state.log(f"request-render id={request_id} output={output_png}")

        used_structure = structure_path

        def _submit_render_with_structure(structure_file: Path) -> dict[str, Any]:
            if time.monotonic() >= deadline or (state.cancelled and state.cancelled()):
                raise WorkerError("Render cancelled or execution deadline expired before dispatch")
            _bridge_runner._write_run_manifest(
                run_manifest_path,
                payload,
                chosen_structure_path=structure_file,
                style_doc=style_doc,
            )
            return state.worker.submit_render(
                structure_path=structure_file,
                view_json=view_path,
                style_json=style_json_path,
                output_png=output_png,
                run_manifest=run_manifest_path,
                timeout=max(.001, deadline - time.monotonic()),
                **({'expected_engine': request_doc['expected_engine'],
                    'required_capabilities': request_doc['render_spec']['required_capabilities']}
                   if 'expected_engine' in request_doc else {}),
                **({'progress': state.progress} if state.progress else {}),
            )

        try:
            worker_result = _submit_render_with_structure(used_structure)
        except WorkerError as exc:
            if (
                used_structure.suffix.lower() == ".cif"
                and pdb_fallback_path is not None
                and not preset_gate_target
                and "cif" in str(exc).lower()
                and any(word in str(exc).lower() for word in ("parse", "load", "format"))
                and not (state.cancelled and state.cancelled())
            ):
                state.log(f"request-render-retry-pdb id={request_id}")
                worker_result = _submit_render_with_structure(pdb_fallback_path)
                used_structure = pdb_fallback_path
            else:
                raise

        if not output_png.exists():
            raise _bridge_runner.RunnerError(
                "Worker reported completion but output PNG is missing. "
                f"expected={output_png}"
            )

        _bridge_runner._write_run_manifest(
            run_manifest_path,
            payload,
            chosen_structure_path=used_structure,
            style_doc=style_doc,
        )
        manifest_doc = _bridge_runner._load_json(run_manifest_path)
        if preset_gate_target:
            _bridge_runner._enforce_preset_1_backend_gates(manifest=manifest_doc, manifest_path=run_manifest_path)

        animation_artifacts = {}
        if animation_encoder:
            from .animation import unpack, encode
            archive = output_png.with_suffix('.frames.zip')
            with unpack(archive) as (frames, metadata):
                for extension in (('mp4', 'gif') if animation.get('gif') else ('mp4',)):
                    path = output_png.with_suffix('.' + extension)
                    encode(frames, metadata, path, executable=animation_encoder,
                           deadline=deadline, cancelled=state.cancelled)
                    animation_artifacts[extension + '_base64'] = base64.b64encode(path.read_bytes()).decode('ascii')
            animation_artifacts['frames_base64'] = base64.b64encode(archive.read_bytes()).decode('ascii')
        png_base64 = base64.b64encode(output_png.read_bytes()).decode("ascii")

        response = {
            **animation_artifacts,
            "status": "completed",
            "mode": "server",
            "request_id": request_id,
            "run_id": run_id,
            "output_name": output_name,
            "png_base64": png_base64,
            "runtime_seconds": worker_result.get("runtime_seconds", 0.0),
            "manifest": manifest_doc,
            "manifest_path": str(run_manifest_path),
            "temp_dir": str(temp_root),
            "temp_kept": bool(keep_temp),
            "worker": worker_result,
            'timings': worker_result.get('timings', []),
        }

        output_blend_path = _bridge_runner._resolve_output_blend(output_png, style_doc)
        if output_blend_path is not None and output_blend_path.exists():
            response["output_blend_name"] = output_blend_path.name
            response["blend_base64"] = base64.b64encode(output_blend_path.read_bytes()).decode("ascii")

        return response
    finally:
        if temp_root is not None and temp_root.exists() and not keep_temp:
            state.log(f"request-cleanup id={request_id} temp_root={temp_root}")
            shutil.rmtree(temp_root, ignore_errors=True)
