"""HTTP client helpers for remote Blender render server."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any


class RemoteRenderError(RuntimeError):
    """Raised when remote render operations fail."""


def _normalize_base_url(host: str, port: int) -> str:
    host_text = str(host or "").strip()
    if not host_text:
        host_text = "127.0.0.1"
    if host_text.startswith("http://") or host_text.startswith("https://"):
        return host_text.rstrip("/")
    return f"http://{host_text}:{int(port)}"


def _decode_json_response(data: bytes, *, context: str) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive parsing
        raise RemoteRenderError(f"{context}: could not decode JSON response") from exc
    if not isinstance(payload, dict):
        raise RemoteRenderError(f"{context}: expected JSON object response")
    return payload


def _http_json(*, method, base_url, path, body, timeout):
    from .client import Client, ClientError
    try:
        return Client(base_url, os.environ.get("BLENDER_RENDER_TOKEN", ""), timeout).request(method, path, body)
    except ClientError as exc:
        raise RemoteRenderError(str(exc)) from exc


def submit_render(
    *,
    host: str,
    port: int,
    payload: dict[str, Any],
    preset: str = "",
    style_doc: dict[str, Any] | None = None,
    rough: bool = False,
    raw_view: bool | None = None,
    color_follow: bool | None = None,
    save_blend: bool | None = None,
    blend_output: str = "",
    keep_temp: bool = False,
    output_name: str = "",
    timeout: float = 1800.0,
) -> dict[str, Any]:
    from .client import Client, ClientError
    client = Client(_normalize_base_url(host, int(port)), os.environ.get("BLENDER_RENDER_TOKEN", ""), timeout)
    options = dict(rough=bool(rough), keep_temp=bool(keep_temp))
    for key, value in (("raw_view", raw_view), ("color_follow", color_follow), ("save_blend", save_blend)):
        if value is not None:
            options[key] = value
    if blend_output:
        options["save_blend"] = True
    if style_doc is not None:
        options["style_doc"] = style_doc
    elif preset:
        options["preset"] = preset
    try:
        source = client.upload(payload)
        job = client.submit(source["input_id"], options)
        result = client.wait(job["job_id"], timeout=timeout)
        response = {"status":"completed", "job_id":job["job_id"], "output_name":output_name or "render.png", "manifest":result["manifest"]}
        from .artifacts import FIELDS
        for item in result["artifacts"]:
            if item["name"] in FIELDS:
                raw = client.request("GET", f"/v1/jobs/{job['job_id']}/artifacts/{item['name']}", binary=True)
                response[FIELDS[item["name"]]] = base64.b64encode(raw).decode()
        return response
    except ClientError as exc:
        raise RemoteRenderError(str(exc)) from exc


def healthz(*, host: str, port: int, timeout: float = 5.0) -> dict[str, Any]:
    base_url = _normalize_base_url(host, int(port))
    return _http_json(method="GET", base_url=base_url, path="/healthz", body=None, timeout=timeout)


def decode_png_bytes(response_doc: dict[str, Any]) -> bytes:
    value = response_doc.get("png_base64")
    if not isinstance(value, str) or not value.strip():
        raise RemoteRenderError("Remote response contains empty png_base64 payload.")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise RemoteRenderError("Remote response png_base64 payload is invalid.") from exc


def write_png_response(*, response_doc: dict[str, Any], output_png: Path) -> Path:
    output_path = Path(output_png).expanduser()
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).absolute()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(decode_png_bytes(response_doc))
    return output_path


def ensure_png_suffix(name: str) -> str:
    filename = str(name or "").strip()
    if not filename:
        return "render.png"
    if filename.lower().endswith(".png"):
        return filename
    return f"{filename}.png"
