"""Restricted image/Library/native-file diagnostics; never submit rendering work."""
import base64
import hashlib
import io
import json
from pathlib import Path
import secrets
import threading
import time

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp import types
from PIL import Image, ImageDraw, ImageFont

from blender_render.native_handoff import native_png_handoff, png_file_descriptor, native_file_result
from blender_render.viewer import html_resource, inline_viewer_html

URI = "ui://blender-render/image-test-v3/"
MODES = ("background", "message", "control", "blender", "library_save", "library_reuse")

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
    if mode not in MODES:
        raise ValueError("Unknown image diagnostic mode")
    options = {"background_context": mode not in ("message", "control"),
               "image_message": mode not in ("background", "control")}
    if mode == "message":
        options["message_prompt"] = (
            " Read the five-character code and name the shape and its colour. "
            "If you cannot see the image, say NO IMAGE.")
    return inline_viewer_html(**options)


def read_test_png(path):
    """Read only the bounded public 128px fixture used by every image diagnostic."""
    with Path(path).open("rb") as source:
        data = source.read(65537)
    if len(data) > 65536:
        raise ValueError("Expected a test PNG no larger than 64 KiB")
    with Image.open(io.BytesIO(data)) as image:
        if image.size != (128, 128) or image.format != "PNG":
            raise ValueError("Expected the existing 128 by 128 CPU-test PNG")
        image.verify()
    return data


class NativeTestFile:
    """One bounded public fixture; URL and embedded transports are separate trials."""
    def __init__(self, preview_file, *, duration=3600, clock=time.time, transport="url"):
        if transport not in {"url", "embedded"}:
            raise ValueError("Native transport must be url or embedded")
        self.transport = transport
        if not 30 <= duration <= 3600:
            raise ValueError("Native test lifetime must be 30 to 3600 seconds")
        self.preview_file = Path(preview_file)
        self.duration, self.clock = duration, clock
        prefix = "blender-embedded-" if transport == "embedded" else "blender-native-"
        self.filename = prefix + secrets.token_hex(8) + ".png"
        self.resource_uri = "blender-test://native/" + self.filename
        self.path = "/" + secrets.token_urlsafe(32) + "/" + self.filename
        self.origin = ""
        self.expires_at = 0
        self.data = None
        self.handoff = None
        self.disabled = False
        self.lock = threading.Lock()

    def activate(self, origin):
        with self.lock:
            if self.origin or self.disabled:
                raise ValueError("Native test source cannot be reactivated")
            self.origin = origin.rstrip("/")
            self.expires_at = self.clock() + self.duration

    def result(self):
        with self.lock:
            if not self.origin:
                raise ValueError("Temporary native file endpoint is not ready")
            if self.disabled or self.clock() >= self.expires_at:
                self.data = self.handoff = None
                raise ValueError("Original test images are disabled or expired")
            if self.handoff is None:
                data = read_test_png(self.preview_file)
                digest = hashlib.sha256(data).hexdigest()
                if self.transport == "embedded":
                    # This opt-in diagnostic tests the standard MCP binary-resource
                    # transport. Host materialization and Space saving are unproven.
                    descriptor = png_file_descriptor(filename=self.filename, size=len(data),
                        sha256=digest, expires_at=self.expires_at)
                    descriptor["resource_uri"] = self.resource_uri
                    resource = types.EmbeddedResource(type="resource", resource=types.BlobResourceContents(
                        uri=self.resource_uri, mimeType="image/png", blob=base64.b64encode(data).decode("ascii")))
                    self.handoff = native_file_result(descriptor, resource,
                        delivery_mode="native_embedded_file_import", instructions=(
                            "This response embeds the original PNG bytes as an MCP binary resource. "
                            "Use the host-provided input attachment path, if materialized; check input "
                            "attachments as well as the output directory. If the raw resource blob is "
                            "accessible to a code tool, base64-decode it without re-encoding the PNG. "
                            "The resource_uri is an identifier, not a URL or a local file path. "
                            "Do not make a network request, call another image tool, or use an older Space copy. "
                            "Write the unchanged bytes under file_name and verify size_bytes and sha256 "
                            "using the metadata in this response. Return a real native file attachment only "
                            "after creating and verifying that file. Never invent a sandbox link. "
                            "If neither an input file nor the resource blob is accessible, report that "
                            "embedded-file import is unsupported in this run and stop. Do not ask the user "
                            "to paste bytes, a filename or a hash. A displayed preview alone does not prove "
                            "file access or Space saving. Space persistence still needs a fresh-chat test."))
                else:
                    self.handoff = native_png_handoff(url=self.origin + self.path,
                        filename=self.filename, size=len(data), sha256=digest,
                        expires_at=self.expires_at)
                self.data = data
            return self.handoff

    def disable(self):
        with self.lock:
            self.disabled = True
            self.data = self.handoff = None

    async def download(self, request):
        from starlette.responses import Response
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        with self.lock:
            if self.disabled or (self.origin and self.clock() >= self.expires_at):
                self.data = self.handoff = None
                return Response(status_code=410, headers=headers)
            if self.transport == "embedded" or self.data is None:
                return Response(status_code=404, headers=headers)
            headers.update({"Content-Disposition": 'attachment; filename="' + self.filename + '"',
                            "Content-Length": str(len(self.data))})
            return Response(self.data if request.method == "GET" else b"",
                            media_type="image/png", headers=headers)


def make_server(records, preview_file, *, native_source=None):
    records = Path(records)
    records.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    count = 0
    original_disabled = False
    library_trials = {}
    native_source = native_source or NativeTestFile(preview_file)
    native_logged = False

    def record_event(event):
        with records.open("a") as out:
            records.chmod(0o600)
            out.write(json.dumps(event) + "\n")

    class TestServer(FastMCP):
        async def _read_resource_mcp(self, uri):
            if native_source.transport == "embedded" and str(uri) == native_source.resource_uri:
                answer = native_source.result()  # Applies the same expiry/revocation checks.
                return types.ServerResult(types.ReadResourceResult(contents=[answer.content[0].resource]))
            mode = str(uri).removeprefix(URI).removesuffix(".html")
            if str(uri) == URI + mode + ".html" and mode in MODES:
                return html_resource(uri, viewer(mode), {"ui": {"csp": {"connectDomains": ["https://*.oaiusercontent.com", "https://chatgpt.com"], "resourceDomains": ["https://*.oaiusercontent.com", "https://chatgpt.com"]}},
                           "openai/widgetCSP": {"connect_domains": ["https://*.oaiusercontent.com", "https://chatgpt.com"], "resource_domains": ["https://*.oaiusercontent.com", "https://chatgpt.com"]}})
            return await super()._read_resource_mcp(uri)

    server = TestServer("Blender CPU Image Test", instructions=
        "This is a temporary image-delivery test. Open only the requested test once. "
        "Do not guess image contents from tool metadata. The random answer exists only "
        "in the image pixels. For visual-context tests, if you cannot see the image, say NO IMAGE. "
        "For the native-file test, verify the materialized file against the metadata in the tool text; "
        "report file-import failures explicitly rather than answering NO IMAGE. "
        "The saved Blender image is a prior local CPU render; these tools never start Blender. "
        "For Library testing, open the requested save test once, let the user save it, then disable_original_images "
        "only when the user asks. In a fresh chat open_library_reuse_test returns a blank picker. "
        "For native file testing call get_native_blender_png_test and use native file/code tools to import its exact bytes. "
        "No widget is involved. After saving to Space and disabling originals, attach the saved file from Space in a fresh chat. "
        "Never copy prior image answers into the fresh test chat.")

    if native_source.transport == "url":
        server.custom_route(native_source.path, methods=["GET", "HEAD"])(native_source.download)

    @server.tool(annotations={"readOnlyHint":True, "destructiveHint":False, "idempotentHint":True, "openWorldHint":False})
    def get_native_blender_png_test() -> ToolResult:
        """Import the existing 128x128 Blender PNG as a native ChatGPT image/file using the returned attachment and checksum. The tool text identifies whether the bytes are embedded or URL-backed. No widget, uploadFile, or new render. This does not itself confirm Space saving. Repeated calls return the same file."""
        nonlocal native_logged
        with lock:
            if original_disabled:
                raise ValueError("Original test images are disabled")
            answer = native_source.result()
            if not native_logged:
                file = answer.structured_content["file"]
                record_event({"event":"native_source", "transport":native_source.transport,
                              "file_name":file["file_name"],
                              "size_bytes":file["size_bytes"], "sha256":file["sha256"]})
                native_logged = True
            return answer

    def result(mode, *, library=False):
        nonlocal count
        with lock:
            if original_disabled:
                raise ValueError("Original test images are disabled. Use open_library_reuse_test; no original bytes can be served in this session.")
            if library and mode in library_trials:
                return library_trials[mode]
            if count >= 40:
                raise ValueError("This test session has reached 40 images. Restart the launcher to continue.")
            if mode == "blender":
                data = read_test_png(preview_file)
                expected = None
            else:
                data, expected = challenge()
            count += 1
            trial = secrets.token_hex(8)
            # This private local oracle is never returned by an MCP tool.
            record_event({"trial": trial, "mode": mode, "expected": expected})
            metadata = {"preview": {"mimeType": "image/png", "data": base64.b64encode(data).decode()}}
            if library:
                metadata["saveFiles"] = [{"jobId":trial, "name":"render.png", "mimeType":"image/png",
                                          "fileName":"blender-library-" + trial + ".png", "data":metadata["preview"]["data"]}]
                metadata["library"] = {"mode":"save", "sha256":hashlib.sha256(data).hexdigest()}
                record_event({"trial":trial, "event":"library_source", "sha256":metadata["library"]["sha256"]})
            answer = ToolResult(
                content=[types.TextContent(type="text", text="Test panel opened. Follow the test instructions; do not infer its image contents.")],
                structured_content={"job_id": trial, "mode": mode}, meta=metadata)
            if library:
                library_trials[mode] = answer
            return answer

    registered_resources = set()

    def register(name, mode, description, *, library=False):
        uri = URI + ("library_save" if library else mode) + ".html"
        def resource():
            return viewer("library_save" if library else mode)
        if uri not in registered_resources:
            server.resource(uri, name=name + "_panel", mime_type="text/html;profile=mcp-app")(resource)
            registered_resources.add(uri)
        def tool():
            return result(mode, library=library)
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
    register("open_library_save_test", "background",
             "Open a random code-and-shape image to save to ChatGPT Library. Save it before disabling originals.", library=True)
    register("open_blender_library_save_test", "blender",
             "Open the existing public 128x128 Blender PNG for saving to ChatGPT Library. No new render.", library=True)

    @server.tool(annotations={"readOnlyHint":False, "destructiveHint":True, "idempotentHint":True, "openWorldHint":False})
    def disable_original_images() -> dict:
        """After the user saves the image, disable ALL original image tools for this temporary session. Clear cached diagnostic image bytes. Does not delete local files or Library files; cannot be undone in this session."""
        nonlocal original_disabled
        with lock:
            original_disabled = True
            library_trials.clear()
            native_source.disable()
            record_event({"event":"originals_disabled"})
        return {"original_images_available":False, "next_step":"For native files: stop the launcher, open a fresh chat, and attach the saved file from Space. For the widget comparison: open_library_reuse_test."}

    uri = URI + "library_reuse.html"
    @server.resource(uri, mime_type="text/html;profile=mcp-app")
    def library_reuse_panel():
        return viewer("library_reuse")

    @server.tool(annotations={"readOnlyHint":True, "destructiveHint":False, "idempotentHint":True, "openWorldHint":False},
                 meta={"ui":{"resourceUri":uri}, "openai/outputTemplate":uri})
    def open_library_reuse_test() -> ToolResult:
        """Open a blank Library picker in a fresh chat, after original images have been disabled. Contains no image bytes, saved file IDs, original URLs or answers. The user selects a saved image in the panel."""
        if not original_disabled:
            raise ValueError("Save the test image and then disable_original_images before opening the reuse test.")
        return ToolResult(content=[types.TextContent(type="text", text="Original image tools are disabled. Select an image from ChatGPT Library using Use saved image.")],
                          structured_content={"original_images_available":False},
                          meta={"library":{"mode":"reuse"}})
    return server
