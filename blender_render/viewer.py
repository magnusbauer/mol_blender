"""Static viewer assets and MCP resource envelopes; no rendering or job access."""
from importlib.resources import files
import json


def inline_viewer_html(*, background_context=True, image_message=True,
                       message_prompt=" Describe what you can see in this image."):
    """Configure presentation at a named bootstrap slot, never by editing JavaScript."""
    if type(background_context) is not bool or type(image_message) is not bool:
        raise ValueError("Viewer delivery switches must be booleans")
    if not isinstance(message_prompt, str) or not message_prompt.strip():
        raise ValueError("Viewer message prompt must be nonempty text")
    options = json.dumps({"backgroundContext": background_context,
                          "imageMessage": image_message, "messagePrompt": message_prompt})
    # JSON inside a script element must not be able to close the element.
    options = options.replace("<", "\\u003c")
    html = files("blender_render").joinpath("inline_preview.html").read_text()
    marker = "<!-- viewer-options -->"
    if html.count(marker) != 1:
        raise ValueError("Packaged viewer is missing its initialization slot")
    return html.replace(marker, '<script id="viewer-options" type="application/json">' + options + '</script>')


def html_resource(uri, html, metadata):
    """Preserve resource-content metadata across the pinned FastMCP conversion."""
    from mcp import types
    return types.ServerResult(types.ReadResourceResult(contents=[
        types.TextResourceContents(uri=str(uri), mimeType="text/html;profile=mcp-app",
                                   text=html, _meta=metadata)
    ]))
