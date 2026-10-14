"""Launch a temporary public-image diagnostic; never start a render.

Use --preview for an existing 128x128 public-fixture PNG. Session records and the
capability URL are removed on exit unless --output explicitly requests retention.
"""
import argparse
from contextlib import contextmanager
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time


@contextmanager
def session_directory(output=None):
    if output is not None:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        yield output
    else:
        with tempfile.TemporaryDirectory(prefix="blender-image-handoff-") as directory:
            yield Path(directory)


def main(argv=None):
    # Test support stays outside the installed rendering package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from support.image_handoff import make_server, NativeTestFile
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-only", action="store_true", help="Skip the HTTPS tunnel")
    parser.add_argument("--duration", type=int, default=3600, help="Session lifetime, at most one hour")
    parser.add_argument("--preview", type=Path, required=True, help="Existing 128x128 public-fixture PNG")
    parser.add_argument("--output", type=Path, help="New directory to retain diagnostic records; otherwise temporary")
    parser.add_argument("--native-transport", choices=("url", "embedded"), default="url",
                        help="Native handoff experiment: URL download or exact bytes embedded in MCP")
    args = parser.parse_args(argv)
    if not 30 <= args.duration <= 3600:
        parser.error("duration must be 30 to 3600 seconds")
    tunnel_exe = None if args.local_only else shutil.which("cloudflared")
    if not args.local_only and not tunnel_exe:
        parser.error("cloudflared is required for the temporary HTTPS connection")
    with session_directory(args.output) as state:
        native_source = NativeTestFile(args.preview, duration=args.duration, transport=args.native_transport)
        server = make_server(state / "expected.jsonl", args.preview, native_source=native_source)
        run_session(server, state, args.duration, tunnel_exe, native_source)


def run_session(server, state, duration, tunnel_exe, native_source):
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
        native_source.activate(origin)
        url = origin + path
        url_file = state / "connection-url.txt"
        url_file.write_text(url + "\n")
        url_file.chmod(0o600)
        print("\nBlender CPU Image Test", flush=True)
        print("MCP URL: " + url, flush=True)
        print("Authentication setting: None (access is restricted by this random URL).", flush=True)
        print("Native transport: " + native_source.transport + " (Space saving requires live verification).", flush=True)
        print("Only diagnostic images are available. Native-file test: get_native_blender_png_test. No renders or user-file uploads.", flush=True)
        print("Keep this terminal open. Press Ctrl+C to stop. Expires after one hour or less.\n", flush=True)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and thread.is_alive():
            if tunnel is not None and tunnel.poll() is not None:
                raise RuntimeError("The temporary HTTPS connection stopped; restart the launcher")
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        native_source.disable()
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
