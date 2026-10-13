"""Start a restricted, temporary ChatGPT image-delivery diagnostic.

Only random test pictures and the already-rendered public 1BNA PNG are exposed.
There are no upload, shell, file-reading or Blender job tools. Each run uses an
unguessable capability URL. Nothing is added to the normal render service.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp import types
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
URI = "ui://blender-render/image-test-v1/"
MODES = ("background", "message", "control", "blender")


def challenge():
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
    colour, rgb = secrets.choice([("red", "#dc2626"), ("blue", "#2563eb"), ("green", "#16a34a")])
    shape = secrets.choice(["circle", "square", "triangle"])
    picture = Image.new("RGB", (128, 128), "white")
    draw = ImageDraw.Draw(picture)
    draw.text((64, 16), code, font=ImageFont.load_default(size=24), fill="black", anchor="mt")
    if shape == "circle":
        draw.ellipse((41, 65, 87, 111), fill=rgb)
    elif shape == "square":
        draw.rectangle((41, 65, 87, 111), fill=rgb)
    else:
        draw.polygon([(64, 61), (37, 112), (91, 112)], fill=rgb)
    data = io.BytesIO()
    picture.save(data, format="PNG")
    return data.getvalue(), {"code": code, "colour": colour, "shape": shape}


def viewer(mode):
    html = (ROOT / "blender_render/inline_preview.html").read_text()
    # Keep the actual production sender/display code; suppress only the other
    # route's advertised capability so these tests cannot validate one another.
    marker = "hostCapabilities = message.result.hostCapabilities || {};"
    assert html.count(marker) == 1
    disable = []
    if mode in ("message", "control"):
        disable.append("delete hostCapabilities.updateModelContext;")
    if mode in ("background", "control"):
        disable.append("delete hostCapabilities.message;")
    html = html.replace(marker, marker + "\n" + "\n".join(disable))
    if mode == "message":
        html = html.replace(" Describe what you can see in this image.",
            " Read the five-character code and name the shape and its colour. If you cannot see the image, say NO IMAGE.")
    titles = {"background": "Background context test", "message": "Image message test",
              "control": "Control: the image is displayed here but not sent to the model",
              "blender": "Previously rendered public Blender image; no new render is started"}
    return html.replace('<main id="render">', '<main id="render"><p>' + titles[mode] + '</p>')


def make_server(records, preview_file):
    records = Path(records)
    records.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    count = 0

    class TestServer(FastMCP):
        async def _read_resource_mcp(self, uri):
            mode = str(uri).removeprefix(URI).removesuffix(".html")
            if str(uri) == URI + mode + ".html" and mode in MODES:
                return types.ServerResult(types.ReadResourceResult(contents=[types.TextResourceContents(
                    uri=str(uri), mimeType="text/html;profile=mcp-app", text=viewer(mode),
                    _meta={"ui": {"csp": {"connectDomains": [], "resourceDomains": []}},
                           "openai/widgetCSP": {"connect_domains": [], "resource_domains": []}})]))
            return await super()._read_resource_mcp(uri)

    server = TestServer("Blender CPU Image Test", instructions=
        "This is a temporary image-delivery test. Open only the requested test once. "
        "Do not guess image contents from tool metadata. The random answer exists only "
        "in the image pixels. If you cannot see the image, say NO IMAGE. "
        "The saved Blender image is a prior local CPU render; these tools never start Blender.")

    def result(mode):
        nonlocal count
        with lock:
            if count >= 40:
                raise ValueError("This test session has reached 40 images. Restart the launcher to continue.")
            if mode == "blender":
                data = Path(preview_file).read_bytes()
                with Image.open(io.BytesIO(data)) as img:
                    if img.size != (128, 128) or img.format != "PNG":
                        raise ValueError("Expected the existing 128 by 128 CPU-test PNG")
                expected = None
            else:
                data, expected = challenge()
            count += 1
            trial = secrets.token_hex(8)
            # This private local oracle is never returned by an MCP tool.
            with records.open("a") as out:
                records.chmod(0o600)
                out.write(json.dumps({"trial": trial, "mode": mode, "expected": expected}) + "\n")
        return ToolResult(
            content=[types.TextContent(type="text", text="Test panel opened. Follow the test instructions; do not infer its image contents.")],
            structured_content={"job_id": trial, "mode": mode},
            meta={"preview": {"mimeType": "image/png", "data": base64.b64encode(data).decode()}})

    def register(name, mode, description):
        uri = URI + mode + ".html"
        def resource():
            return viewer(mode)
        server.resource(uri, name=name + "_panel", mime_type="text/html;profile=mcp-app")(resource)
        def tool():
            return result(mode)
        server.tool(name=name, description=description,
            annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
            meta={"ui": {"resourceUri": uri}, "openai/outputTemplate": uri})(tool)

    register("open_background_test", "background",
             "Open a new random test image. The panel sends image context; the user then asks about it in a separate message.")
    register("open_message_test", "message",
             "Open a new random test image. The user clicks Ask ChatGPT about this image; background image context is disabled.")
    register("open_no_image_control", "control",
             "Open a diagnostic panel with both image-delivery routes disabled. The user can see the picture; the model should not receive it.")
    register("open_saved_blender_test", "blender",
             "Show the saved 128 by 128 public 1BNA CPU render through the updated viewer. Does not start a new render.")
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-only", action="store_true", help="Skip the HTTPS tunnel")
    parser.add_argument("--duration", type=int, default=3600, help="Session lifetime, at most one hour")
    args = parser.parse_args()
    if not 30 <= args.duration <= 3600:
        parser.error("duration must be 30 to 3600 seconds")
    tunnel_exe = None if args.local_only else shutil.which("cloudflared")
    if not args.local_only and not tunnel_exe:
        parser.error("cloudflared is required for the temporary HTTPS connection")
    state = ROOT / ".validation/image-handoff-test"
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    server = make_server(state / "expected.jsonl", ROOT / ".validation/cpu-128/render.png")
    path = "/" + secrets.token_urlsafe(32) + "/mcp"
    import uvicorn
    app = server.http_app(path=path, stateless_http=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    http = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=http.run, kwargs={"sockets": [sock]}, daemon=True)
    tunnel = None
    try:
        thread.start()
        deadline = time.monotonic() + 15
        while not http.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Local test server failed to start")
            time.sleep(.1)
        origin = f"http://127.0.0.1:{port}"
        if tunnel_exe:
            tunnel = subprocess.Popen([tunnel_exe, "tunnel", "--no-autoupdate", "--url", origin],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            lines = queue.Queue()
            def consume():
                for line in tunnel.stderr:
                    lines.put(line)
                lines.put(None)
            threading.Thread(target=consume, daemon=True).start()
            deadline = time.monotonic() + 60
            while True:
                try:
                    line = lines.get(timeout=max(.1, deadline-time.monotonic()))
                except queue.Empty:
                    raise RuntimeError("Temporary HTTPS connection timed out")
                if line is None:
                    raise RuntimeError("Temporary HTTPS connection exited before it was ready")
                match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if match:
                    origin = match.group(0)
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Temporary HTTPS connection timed out")
        url = origin + path
        url_file = state / "connection-url.txt"
        url_file.write_text(url + "\n")
        url_file.chmod(0o600)
        print("\nBlender CPU Image Test", flush=True)
        print("MCP URL: " + url, flush=True)
        print("Authentication setting: None (access is restricted by this random URL).", flush=True)
        print("Only test images are available; there are no render, upload or filesystem tools.", flush=True)
        print("Keep this terminal open. Press Ctrl+C to stop. Expires after one hour or less.\n", flush=True)
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline and thread.is_alive():
            if tunnel is not None and tunnel.poll() is not None:
                raise RuntimeError("The temporary HTTPS connection stopped; restart the launcher")
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel is not None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait(timeout=5)
        http.should_exit = True
        thread.join(timeout=5)
        sock.close()
        print("Test connection stopped.", flush=True)


if __name__ == "__main__":
    main()
