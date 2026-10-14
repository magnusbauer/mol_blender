"""Small, portable configuration shared by the CLI, PyMOL, and services."""
from __future__ import annotations

import json
import os
from pathlib import Path

VERSION = "0.2.0b1"


def public_origin(value: str) -> str:
    """An optional HTTPS origin shared by CLI, REST downloads, and private MCP."""
    from urllib.parse import urlsplit
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Reject malformed or out-of-range ports as well.
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.path in ("", "/") and not any(c.isspace() for c in value))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Public URL must be an HTTPS origin")
    return value.rstrip("/")


def process_thread_count(value):
    """Startup setting for a process, separate from per-render scene threads."""
    if value is not None and (type(value) is not int or not 1 <= value <= 64):
        raise ValueError("process_threads must be an integer from 1 to 64")
    return value


def process_thread_environment(threads, *, apptainer=False):
    threads = process_thread_count(threads)
    if threads is None:
        return {}
    values = {name: str(threads) for name in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")}
    if apptainer:
        values.update({"APPTAINERENV_" + name: value for name, value in list(values.items())})
    return values


def blender_thread_arguments(threads):
    threads = process_thread_count(threads)
    return ["--threads", str(threads)] if threads is not None else []


def config_root() -> Path:
    return Path(os.environ.get("BLENDER_RENDER_HOME", str(Path.home() / ".config" / "blender-render"))).expanduser()


def load_config() -> dict:
    path = config_root() / "config.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_config(config: dict) -> Path:
    root = config_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "config.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def runtime_versions() -> dict:
    from .engine import resource_bytes
    return json.loads(resource_bytes(Path(__file__).with_name("runtime.lock.json")))
