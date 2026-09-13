"""Build installable Blender Render plugin variants from one shared template."""

from .package import (
    PACKAGE_ROUTES,
    package_marketplace,
    package_plugin,
    template_root,
    validate_marketplace,
    validate_package,
)

__all__ = [
    "PACKAGE_ROUTES",
    "package_marketplace",
    "package_plugin",
    "template_root",
    "validate_marketplace",
    "validate_package",
]
