"""Reproducible molecular rendering with lightweight, lazy client imports."""
from .config import VERSION as __version__


def __getattr__(name):
    if name in {
        "bridge_render",
        "bridge_status",
        "bridge_result",
        "bridge_cancel",
        "bridge_recover",
        "build_render_payload",
        "register_plugin",
    }:
        from . import pymol_bridge_plugin
        return getattr(pymol_bridge_plugin, name)
    raise AttributeError(name)
