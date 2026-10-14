"""Exact PNG transfer to a host's native file tools, without an app widget."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from fastmcp.tools.tool import ToolResult
from mcp import types


NATIVE_FILE_INSTRUCTIONS = (
    "Use the original PNG attachment already materialized by the host. If it has not been materialized, "
    "import it from source_url using the host's native file-import or code-execution tool. "
    "Write the unchanged bytes under file_name in the native output directory, "
    "and verify both size_bytes and sha256 before returning the existing file as a native image/file attachment. "
    "Use the host's actual artifact reference only after creating the file; never invent a sandbox link. "
    "Do not redraw, re-encode, render again, or use the app widget's uploadFile path. "
    "If a native importer or network-capable runtime is unavailable, report that limitation and stop. "
    "Returning a URL or displaying a preview is not a completed import. "
    "Space saving remains unconfirmed until the file appears there and is reused in a fresh chat."
)


def png_file_descriptor(*, filename, size, sha256, expires_at):
    """Common verification metadata for an original PNG, independent of transport."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.png", filename):
        raise ValueError("Expected a safe PNG filename")
    if type(size) is not int or size <= 0 or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Expected verified PNG size and SHA-256")
    return {"file_name": filename, "mime_type": "image/png", "size_bytes": size,
            "sha256": sha256, "expires_at": expires_at}


def native_file_result(descriptor, resource, *, delivery_mode, instructions):
    """Expose one verification record to both structured and text-only consumers."""
    record = {"delivery_mode": delivery_mode, "file": descriptor,
              "file_import_confirmed": False, "space_save_confirmed": False}
    # Some hosts materialize ResourceLink bytes but omit structuredContent from
    # model context. Keep the verification contract in ordinary MCP text too.
    return ToolResult(
        content=[resource,
                 types.TextContent(type="text", text=json.dumps(record, separators=(",", ":"))),
                 types.TextContent(type="text", text=instructions)],
        structured_content=record)


def native_png_handoff(*, url, filename, size, sha256, expires_at):
    """Describe a verified source; never claim that the host has imported it."""
    parsed = urlsplit(url)
    local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parsed.scheme != "https" and not local) or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Native file handoff requires an absolute HTTPS URL (or local test URL).")
    descriptor = png_file_descriptor(filename=filename, size=size, sha256=sha256, expires_at=expires_at)
    descriptor["source_url"] = url
    resource = types.ResourceLink(type="resource_link", uri=url, name=filename,
        mimeType="image/png", size=size, description="Original PNG bytes for native file import; not a saved Space file.")
    return native_file_result(descriptor, resource, delivery_mode="native_file_import",
                              instructions=NATIVE_FILE_INSTRUCTIONS)
