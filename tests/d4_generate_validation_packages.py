"""Generate persistent public D4 validation variants from one package form."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from blender_render_plugin import package_marketplace, package_plugin


root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=False)
outputs = {
    "hosted": package_plugin(root / "hosted", "hosted"),
    "private_stdio": package_plugin(
        root / "private-stdio", "private-stdio", profile="local",
        executable="/opt/blender-render/bin/blender-render"),
    "private_http": package_plugin(
        root / "private-http", "private-http",
        url="https://render.example.edu/mcp"),
    "private_bearer": package_plugin(
        root / "private-bearer", "private-http",
        url="https://render.example.edu/mcp",
        bearer_token_env_var="BLENDER_RENDER_PRIVATE_TOKEN"),
    "personal_modal": package_plugin(
        root / "personal-modal", "private-http",
        url="https://operator--renderer.us-east.modal.direct/mcp",
        deployment_identity="personal-render-v1"),
    "marketplace": package_marketplace(
        root / "marketplace", "private-stdio",
        plugin_name="blender-render-local", profile="local",
        executable="/opt/blender-render/bin/blender-render"),
}
print(json.dumps(outputs, sort_keys=True))
