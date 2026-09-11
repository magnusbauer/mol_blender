"""PyMOL command adapter. Export locally, submit through the shared service."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
# PyMOL's default `run` executes in its own globals: __file__ still names
# pymol/__init__.py, while __script__ identifies the file being run.
_PACKAGE_ROOT = str(Path(globals().get("__script__", __file__)).resolve().parent.parent)
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)
from blender_render.export import (build_render_payload, collect_view_payload,
    _normalize_cli_string, _parse_bool, _parse_int, _parse_float)


def installation_defaults():
    """Deployment settings live beside the package, outside the engine assets."""
    path = Path(_PACKAGE_ROOT) / 'pymol-defaults.json'
    if not path.exists():
        return {}
    values = json.loads(path.read_text())
    allowed = {'preset', 'mode', 'server_host', 'server_port'}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError('Unsupported PyMOL installation defaults')
    for key in ('preset', 'mode', 'server_host'):
        if key in values and (not isinstance(values[key], str) or not values[key].strip()
                              or any(char in values[key] for char in ('\n', '\0'))):
            raise ValueError('Invalid PyMOL default: ' + key)
    if 'server_port' in values and (type(values['server_port']) is not int or not 1 <= values['server_port'] <= 65535):
        raise ValueError('Invalid PyMOL default: server_port')
    return values


def try_load_preview(cmd_obj, output_png):
    try:
        cmd_obj.load_png(str(output_png), movie=1, quiet=1)
        return True
    except Exception:
        print("Rendered PNG: " + str(output_png))
        return False


def bridge_render(selection="(all)", preset="", style_json="", mode="",
                  output_dir=".", output_name="render.png", rough=0, color_follow="",
                  save_blend="", raw_view="", state=-1, server_host="", server_port="",
                  server_timeout=1800, profile="", use_apptainer=0, apptainer_image="",
                  blender_exe="", attach=1, attach_only=0, keep_temp=0,
                  width="", height="", samples="", transparent_background="",
                  blend_output="", _self=None, overrides_json="", preserve_view="", **legacy):
    if _self is None:
        from pymol import cmd
    else:
        cmd = _self
    from blender_render.client import Client, configured_client
    from blender_render.config import load_config
    text = _normalize_cli_string
    mode, profile = text(mode), text(profile)
    defaults = installation_defaults()
    preset = text(preset) or defaults.get('preset', 'preset_1_interface')
    # Explicit profiles/mode changes select their own connection. Overriding just
    # the host or port retains the lab transport, e.g. for an outside SSH tunnel.
    if not profile and (not mode or mode == defaults.get('mode', 'local')):
        mode = mode or defaults.get('mode', 'local')
        server_host = text(server_host) or defaults.get('server_host', '')
        server_port = text(server_port) or defaults.get('server_port', 8765)
    else:
        mode = mode or 'local'
        server_port = text(server_port) or 8765
    allowed = {"interactive_session", "interactive_launcher", "auto_close_interactive_session", "slurm_partition", "slurm_account", "slurm_gres", "slurm_time", "poll", "poll_interval"}
    unknown = set(legacy) - allowed
    if unknown:
        raise ValueError("Unknown blender_render options: " + ", ".join(sorted(unknown)))
    if any(str(v).strip() for k, v in legacy.items() if k.startswith("slurm_") or k == "interactive_launcher"):
        raise ValueError("Configure allocation resources in a named cluster profile using blender-render profile add.")
    if text(server_host):
        from blender_render.remote_client import _normalize_base_url
        url = _normalize_base_url(text(server_host), int(server_port))
        client = Client(url, '', lab_http=True) if mode == 'lab' else Client(url, os.environ.get("BLENDER_RENDER_TOKEN", ""))
    else:
        selected = profile or (load_config().get("default_hpc_profile", "ipd") if mode in {"interactive", "slurm", "hpc"} else mode)
        image = text(apptainer_image) if _parse_bool(use_apptainer) or mode == "apptainer" else ""
        if sys.platform == "darwin" and mode == "local":
            image = ""
        client = configured_client(selected, blender=text(blender_exe), image=image, attach_only=_parse_bool(attach_only))
        if mode in {"interactive", "slurm", "hpc"}:
            print("Interactive GPU session ready")
    payload = build_render_payload(cmd, text(selection), int(state), include_color_hints=True)
    options = {"preset": text(preset), "rough": _parse_bool(rough), "keep_temp": _parse_bool(keep_temp)}
    for key, value in (("color_follow", color_follow), ("raw_view", raw_view)):
        if str(value).strip():
            options[key] = _parse_bool(value)
    if text(style_json):
        options["style_doc"] = json.loads(Path(text(style_json)).expanduser().read_text())
        options.pop("preset", None)
    if text(overrides_json):
        options["overrides"] = json.loads(Path(text(overrides_json)).expanduser().read_text())
    if str(save_blend).strip():
        options["save_blend"] = _parse_bool(save_blend)
    if text(blend_output):
        options["save_blend"] = True
    if str(preserve_view).strip():
        options["preserve_view"] = _parse_bool(preserve_view)
    for key, value in (("width", width), ("height", height), ("samples", samples)):
        if str(value).strip():
            options[key] = int(value)
    if str(transparent_background).strip():
        options["transparent_background"] = _parse_bool(transparent_background)
    source = client.upload(payload)
    job = client.submit(source["input_id"], options)
    print("Blender render job: " + job["job_id"])
    client.wait(job["job_id"], timeout=float(server_timeout), progress=lambda s: print(f"Render {s['status']}; queue position {s['queue_position']}"))
    output = (Path(text(output_dir) or ".").expanduser() / (text(output_name) or "render.png")).with_suffix(".png").resolve()
    result = client.download(job["job_id"], output)
    if text(blend_output) and "render.blend" in result["outputs"]:
        import shutil
        destination = Path(text(blend_output)).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(result["outputs"]["render.blend"], destination)
    try_load_preview(cmd, output)
    print(str(output))
    return str(output)

blender_render = bridge_render

def register_plugin(cmd_obj=None):
    if cmd_obj is None:
        from pymol import cmd as cmd_obj
    cmd_obj.extend("blender_render", bridge_render)

try:
    from pymol import cmd
    cmd.extend("blender_render", bridge_render)
except ImportError:
    pass
