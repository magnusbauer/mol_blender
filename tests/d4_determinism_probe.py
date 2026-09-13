"""Emit normalized D4 packages for source/staging/installed comparison."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from blender_render_plugin import package_marketplace, package_plugin

HPC_PROFILE = {
    "kind": "hpc",
    "version": 2,
    "launcher": {"kind": "slurm_sbatch", "arguments": []},
    "transport": "ssh",
    "host": "render-login.example.edu",
    "remote_python": "/usr/bin/python3",
    "remote_root": "/srv/blender-render",
    "image": "/srv/containers/blender-render.sif",
    "registry": "/srv/blender-render/registry",
}


def normalized(value):
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()
                if key not in {"path", "plugin_path"}}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


with tempfile.TemporaryDirectory(prefix="blender-d4-probe-") as temporary:
    root = Path(temporary)
    outputs = {
        "hosted": package_plugin(root / "blender-render", "hosted"),
        "stdio": package_plugin(
            root / "blender-render-ipd", "private-stdio", profile="ipd",
            executable="/opt/blender-render/bin/blender-render",
            config={"profiles": {"ipd": HPC_PROFILE}}),
        "http": package_plugin(
            root / "blender-render-lab", "private-http",
            url="https://render.example.edu/mcp"),
        "bearer": package_plugin(
            root / "blender-render-bearer", "private-http",
            url="https://render.example.edu/mcp",
            bearer_token_env_var="BLENDER_RENDER_PRIVATE_TOKEN"),
        "personal_modal": package_plugin(
            root / "blender-render-modal", "private-http",
            url="https://operator--renderer.us-east.modal.direct/mcp",
            deployment_identity="personal-render-v1"),
        "marketplace": package_marketplace(
            root / "marketplace", "private-stdio",
            plugin_name="blender-render-local",
            profile="local",
            executable="/opt/blender-render/bin/blender-render"),
    }
    print(json.dumps(normalized(outputs), sort_keys=True, separators=(",", ":")))
