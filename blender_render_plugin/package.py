"""Deterministic, credential-free packaging for Blender Render host routes.

The repository keeps one reviewed skill and asset template. This module emits a
hosted package, a private local/HPC STDIO package, or a private HTTPS package
without allowing a private install to inherit the shared hosted connection.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


PACKAGE_ROUTES = ("hosted", "private-stdio", "private-http")
_PLUGIN_NAME = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\Z")
_PROFILE_NAME = re.compile(r"[A-Za-z0-9_-]+\Z")
_APP_ID = re.compile(r"plugin_asdk_app_[A-Za-z0-9]+\Z")
_MARKETPLACE_NAME = re.compile(r"[A-Za-z0-9_-]+\Z")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DEPLOYMENT_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_COMMON_DIRECTORIES = ("assets", "skills")
_GENERATOR_RECORD = ".blender-render-generator.json"
_GENERATOR_FORMAT_VERSION = 2
_MODAL_HOST_SUFFIXES = (".modal.direct", ".modal.run")
_PLUGIN_OWNED_REQUIRED = frozenset({
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "assets/molecule.svg",
    "routing.json",
    "skills/develop/SKILL.md",
    "skills/render/SKILL.md",
})
_PLUGIN_OWNED_OPTIONAL = frozenset({".app.json"})
_MARKETPLACE_OWNED_REQUIRED = frozenset({".agents/plugins/marketplace.json"})
_HOSTED_CONNECTION_SENTENCE = (
    "The included HTTPS configuration currently uses an account-restricted private beta. "
    "A local stdio adapter uses the user's configured Mac, HPC, or personal Modal profile."
)


def template_root() -> Path:
    """Return the installed template, or the repository template in a checkout."""
    installed = Path(__file__).resolve().with_name("template")
    if installed.is_dir():
        return installed
    checkout = Path(__file__).resolve().parents[1] / "plugins" / "blender-render"
    if checkout.is_dir():
        return checkout
    raise RuntimeError("The Blender Render plugin template is not installed")


def _tree_inventory(root: Path) -> dict[str, str]:
    root = root.resolve()
    files: dict[str, str] = {}

    def walk(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if path.is_symlink():
                raise ValueError("Plugin packages cannot contain symlinks")
            if path.is_dir():
                walk(path)
            elif path.is_file() and path.resolve().is_relative_to(root):
                files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                raise ValueError("Plugin package inputs must be regular files")

    walk(root)
    return dict(sorted(files.items()))


def copy_template(source: Path, destination: Path) -> dict[str, str]:
    """Copy a complete template without following links; used only by wheel build."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Plugin template destination must be new")
    inventory = _tree_inventory(source)
    destination.mkdir(parents=True)
    for name, digest in inventory.items():
        data = (source / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Plugin template changed while it was being copied")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if _tree_inventory(destination) != inventory:
        raise ValueError("Copied plugin template did not verify")
    return inventory


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Plugin configuration contains duplicate key: {key}")
        value[key] = item
    return value


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(), object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("Plugin configuration must be a JSON object")
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _relative_target(root: Path, value: object, label: str, *, directory: bool) -> Path:
    if not isinstance(value, str) or not value.startswith("./") or "\\" in value:
        raise ValueError(f"{label} must be a plugin-relative path")
    relative = PurePosixPath(value[2:])
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} must stay inside the plugin")
    target = root.joinpath(*relative.parts)
    if target.is_symlink() or target.resolve().is_relative_to(root) is not True:
        raise ValueError(f"{label} must stay inside the plugin")
    if directory and not target.is_dir():
        raise ValueError(f"{label} directory is missing")
    if not directory and not target.is_file():
        raise ValueError(f"{label} file is missing")
    return target


def _generator_allowlist(kind: str, *, plugin_name: str | None = None) -> set[str]:
    if kind == "plugin":
        allowed = set(_PLUGIN_OWNED_REQUIRED | _PLUGIN_OWNED_OPTIONAL)
    elif kind == "marketplace":
        name = _validate_name(plugin_name, "plugin name", _PLUGIN_NAME)
        prefix = f"plugins/{name}/"
        allowed = set(_MARKETPLACE_OWNED_REQUIRED)
        allowed.update(prefix + item for item in _PLUGIN_OWNED_REQUIRED | _PLUGIN_OWNED_OPTIONAL)
        allowed.add(prefix + _GENERATOR_RECORD)
    else:
        raise ValueError("Unknown generator ownership kind")
    return allowed


def _expected_generator_files(
    root: Path, kind: str, *, plugin_name: str | None = None
) -> set[str]:
    if kind == "plugin":
        expected = set(_PLUGIN_OWNED_REQUIRED)
        route = _json(root / "routing.json")
        if route.get("registered_connection") is True:
            expected.add(".app.json")
        return expected
    name = _validate_name(plugin_name, "plugin name", _PLUGIN_NAME)
    plugin = root / "plugins" / name
    expected = set(_MARKETPLACE_OWNED_REQUIRED)
    prefix = f"plugins/{name}/"
    expected.update(
        prefix + item for item in _expected_generator_files(plugin, "plugin")
    )
    expected.add(prefix + _GENERATOR_RECORD)
    return expected


def _write_generator_record(
    root: Path, kind: str, *, plugin_name: str | None = None
) -> None:
    files = _tree_inventory(root)
    files.pop(_GENERATOR_RECORD, None)
    expected = _expected_generator_files(root, kind, plugin_name=plugin_name)
    if set(files) != expected or not expected.issubset(
        _generator_allowlist(kind, plugin_name=plugin_name)
    ):
        raise ValueError("Generated output differs from the fixed ownership layout")
    _write_json(
        root / _GENERATOR_RECORD,
        {
            "format_version": _GENERATOR_FORMAT_VERSION,
            "generator": "blender-render-plugin",
            "kind": kind,
            "files": files,
        },
    )


def _generator_owned(
    root: Path, kind: str, *, plugin_name: str | None = None
) -> dict[str, str]:
    record_path = root / _GENERATOR_RECORD
    if record_path.is_symlink() or not record_path.is_file():
        raise ValueError("Force requires an output created by this generator")
    record = _json(record_path)
    if (
        set(record) != {"format_version", "generator", "kind", "files"}
        or record.get("format_version") != _GENERATOR_FORMAT_VERSION
        or record.get("generator") != "blender-render-plugin"
        or record.get("kind") != kind
        or not isinstance(record.get("files"), dict)
    ):
        raise ValueError("Generator ownership record is invalid")
    allowlist = _generator_allowlist(kind, plugin_name=plugin_name)
    expected = _expected_generator_files(root, kind, plugin_name=plugin_name)
    if set(record["files"]) != expected or not set(record["files"]).issubset(allowlist):
        raise ValueError("Generator ownership marker differs from the fixed allowlist")
    for name, digest in record["files"].items():
        relative = PurePosixPath(name)
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or relative.is_absolute()
            or not relative.parts
            or ".." in relative.parts
            or "\\" in name
            or name == _GENERATOR_RECORD
        ):
            raise ValueError("Generator ownership record contains an invalid path")
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Generator-owned output is missing or not a regular file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Generator-owned output was modified; use a new destination")
    return dict(record["files"])


def _validate_name(name: str, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(name, str) or not pattern.fullmatch(name):
        raise ValueError(f"Invalid {label}: use letters, digits, underscores, or hyphens")
    return name


def _canonical_hostname(value: str) -> str:
    if not value:
        raise ValueError("HTTPS endpoint hostname is missing")
    try:
        return ipaddress.ip_address(value.rstrip(".")).compressed.lower()
    except ValueError:
        pass
    try:
        hostname = value.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError("HTTPS endpoint hostname is not valid IDNA") from exc
    labels = hostname.split(".")
    if (not hostname or any(not label or len(label) > 63
                            or not re.fullmatch(r"[a-z0-9-]+", label)
                            or label.startswith("-") or label.endswith("-")
                            for label in labels)
            or len(hostname) > 253):
        raise ValueError("HTTPS endpoint hostname is invalid")
    return hostname


def _canonical_https(value: str, *, mcp_path: bool) -> str:
    if not isinstance(value, str) or not value or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value):
        raise ValueError("HTTPS endpoint must be an explicit credential-free URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("HTTPS endpoint is malformed") from exc
    if (parsed.scheme.lower() != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment):
        raise ValueError("HTTPS endpoint must be credential-free")
    hostname = _canonical_hostname(parsed.hostname)
    path = parsed.path.rstrip("/")
    if mcp_path:
        if path != "/mcp":
            raise ValueError("The private MCP endpoint URL path must be /mcp")
        path = "/mcp"
    else:
        if path and (not path.startswith("/") or "//" in path
                     or any(part in {".", ".."} for part in path.split("/"))):
            raise ValueError("HTTPS profile endpoint path is not canonical")
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port not in (None, 443):
        authority += f":{port}"
    return f"https://{authority}{path}"


def _credential_free_https(value: str) -> bool:
    try:
        _canonical_https(value, mcp_path=False)
        return True
    except (TypeError, ValueError):
        return False


def _service_origin(value: str) -> tuple[str, str, int]:
    canonical = _canonical_https(value, mcp_path=False)
    parsed = urlsplit(canonical)
    return "https", parsed.hostname or "", parsed.port or 443


def _is_modal_endpoint(value: str) -> bool:
    hostname = _service_origin(value)[1]
    return any(hostname == suffix[1:] or hostname.endswith(suffix)
               for suffix in _MODAL_HOST_SUFFIXES)


def _private_profile(profile: str, config: dict, hosted_url: str) -> tuple[str, str, str | None]:
    _validate_name(profile, "profile name", _PROFILE_NAME)
    if profile == "local":
        return profile, "local", None
    if profile == "apptainer":
        return profile, "apptainer", None
    document = config.get("profiles", {}).get(profile)
    if not isinstance(document, dict):
        raise ValueError(f"Private STDIO packaging requires an existing profile: {profile!r}")
    if document.get("kind") == "hpc":
        from blender_render.hpc import validate_profile
        validate_profile(document)
        return profile, "hpc", None
    if document.get("kind") == "https" and document.get("private") is True:
        try:
            endpoint = _canonical_https(document.get("url", ""), mcp_path=False)
        except ValueError:
            endpoint = None
        if endpoint is not None and _service_origin(endpoint) != _service_origin(hosted_url):
            return profile, "https", endpoint
    raise ValueError("Private STDIO packaging refuses a shared endpoint profile")


def _absolute_executable(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value:
        raise ValueError("Private STDIO packaging requires a literal absolute executable")
    if value.startswith("~") or "$" in value or re.search(r"%[^%]+%", value):
        raise ValueError("Private STDIO packaging requires a literal absolute executable")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Private STDIO packaging requires a literal absolute executable")
    return os.path.normpath(str(path))


def _private_http_url(value: str, hosted_url: str) -> str:
    try:
        endpoint = _canonical_https(value, mcp_path=True)
    except ValueError as exc:
        raise ValueError("Private HTTP packaging requires an explicit canonical MCP URL") from exc
    if _service_origin(endpoint) == _service_origin(hosted_url):
        raise ValueError("Private packaging cannot target the shared hosted service")
    return endpoint


def _deployment_identity(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DEPLOYMENT_IDENTITY.fullmatch(value):
        raise ValueError(
            "Deployment identity must be a non-secret 1-64 character label"
        )
    return value


def _app_document(app_id: str) -> dict:
    if not _APP_ID.fullmatch(app_id):
        raise ValueError("Registered connection IDs must start with plugin_asdk_app_")
    return {"apps": {"blender-render": {"id": app_id}}}


def _descriptive_route(adapter_type: str, input_source: str,
                       input_location: str, expected_compute: str) -> dict:
    return {
        "adapter_type": adapter_type,
        "input_source": input_source,
        "input_location": input_location,
        "expected_compute": expected_compute,
    }


def _route_inputs(route: str, *, profile: str | None, executable: str | None,
                  url: str | None, app_id: str | None,
                  bearer_token_env_var: str | None,
                  deployment_identity: str | None, config: dict,
                  template: Path) -> tuple[dict, dict | None, dict]:
    if route not in PACKAGE_ROUTES:
        raise ValueError("Unknown plugin route")
    hosted_mcp = _json(template / ".mcp.json")
    hosted_app = _json(template / ".app.json")
    hosted_server = hosted_mcp["mcpServers"]["blender-render"]
    hosted_url = _canonical_https(hosted_server["url"], mcp_path=True)
    hosted_server["url"] = hosted_url
    if route == "hosted":
        if any(value is not None for value in
               (profile, executable, url, app_id, bearer_token_env_var,
                deployment_identity)):
            raise ValueError("Hosted packaging uses the reviewed hosted connection without overrides")
        routing = {"format_version": 2, "route": route, "transport": "streamable-http",
                   "private_input": False, "shared_fallback": False,
                   "registered_connection": True,
                   "service_scope": "owner-private-beta",
                   **_descriptive_route(
                       "registered-http", "declared-attachment", "remote-client-upload",
                       "owner-private-beta-service")}
        return hosted_mcp, hosted_app, routing
    if route == "private-stdio":
        if (url is not None or app_id is not None
                or bearer_token_env_var is not None):
            raise ValueError("Private STDIO packaging does not accept HTTP or registered-app settings")
        if profile is None:
            raise ValueError("Private STDIO packaging requires an explicit profile")
        selected, profile_kind, profile_endpoint = _private_profile(profile, config, hosted_url)
        command = _absolute_executable(executable or "")
        mcp = {"mcpServers": {"blender-render": {
            "command": command, "args": ["mcp", "--profile", selected]}}}
        expected_compute = {
            "local": "local-blender",
            "apptainer": "local-apptainer",
            "hpc": "private-hpc",
            "https": "private-https-service",
        }[profile_kind]
        identity = _deployment_identity(deployment_identity)
        modal = profile_endpoint is not None and _is_modal_endpoint(profile_endpoint)
        if modal and identity is None:
            raise ValueError(
                "Personal Modal packaging requires an explicit non-secret deployment identity"
            )
        if identity is not None and not modal:
            raise ValueError("Deployment identity applies only to a personal Modal endpoint")
        if modal:
            expected_compute = "personal-modal-service"
        routing = {"format_version": 2, "route": route, "transport": "stdio",
                   "profile": selected, "profile_kind": profile_kind, "private_input": True,
                   "shared_fallback": False, "registered_connection": False,
                   "service_scope": "private",
                   **_descriptive_route(
                       "stdio-profile", "user-selected-local-path",
                       "client-host-filesystem", expected_compute)}
        if profile_endpoint is not None:
            routing["profile_endpoint"] = profile_endpoint
        if identity is not None:
            routing["deployment_identity"] = identity
        return mcp, None, routing
    if profile is not None or executable is not None:
        raise ValueError("Private HTTP packaging routes through its URL, not a local profile")
    endpoint = _private_http_url(url or "", hosted_url)
    identity = _deployment_identity(deployment_identity)
    if _is_modal_endpoint(endpoint) and identity is None:
        raise ValueError(
            "Personal Modal packaging requires an explicit non-secret deployment identity"
        )
    if app_id is not None and bearer_token_env_var is not None:
        raise ValueError("Choose registered OAuth or bearer authentication, not both")
    app = _app_document(app_id) if app_id is not None else None
    server = {"type": "http", "url": endpoint}
    if bearer_token_env_var is not None:
        _validate_name(bearer_token_env_var, "bearer token environment variable",
                       _ENVIRONMENT_NAME)
        server["bearer_token_env_var"] = bearer_token_env_var
    mcp = {"mcpServers": {"blender-render": server}}
    modal = _is_modal_endpoint(endpoint)
    routing = {"format_version": 2, "route": route, "transport": "streamable-http",
               "private_input": True, "shared_fallback": False,
               "registered_connection": app is not None,
               "authentication": "bearer-env" if bearer_token_env_var else "oauth",
               "service_scope": "private",
               **_descriptive_route(
                   "http-endpoint", "declared-attachment", "remote-client-upload",
                   "personal-modal-service" if modal else "explicit-private-https-service")}
    if identity is not None:
        routing["deployment_identity"] = identity
    return mcp, app, routing


def _manifest(template: Path, name: str, app: dict | None, route: str) -> dict:
    manifest = _json(template / ".codex-plugin" / "plugin.json")
    manifest["name"] = name
    manifest["skills"] = "./skills/"
    manifest["mcpServers"] = "./.mcp.json"
    manifest.pop("apps", None)
    if app is not None:
        manifest["apps"] = "./.app.json"
    if route != "hosted":
        manifest["author"] = {"name": "Blender Render operator"}
        manifest.pop("homepage", None)
        manifest.pop("repository", None)
        interface = manifest.get("interface", {})
        interface["developerName"] = "Blender Render operator"
        interface["longDescription"] = (
            "Create ray-traced molecular stills through this package's single explicitly "
            "configured private route. Preserve PyMOL views and colors and return transparent "
            "PNGs plus optional Blender scenes without falling back to shared compute."
        )
        for field in ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"):
            interface.pop(field, None)
    return manifest


def _private_render_skill(template: Path, route: str, routing: dict) -> bytes:
    source = (template / "skills" / "render" / "SKILL.md").read_text()
    if _HOSTED_CONNECTION_SENTENCE not in source:
        raise ValueError("Reviewed render skill connection text changed")
    if route == "private-stdio":
        detail = (
            "This generated package uses the local STDIO adapter and the explicit `"
            + routing["profile"] + "` profile. Its compute route is `"
            + routing["expected_compute"] + "`."
        )
    elif routing["expected_compute"] == "personal-modal-service":
        detail = (
            "This generated package uses the explicit personal Modal endpoint and deployment "
            "identity `" + routing["deployment_identity"] + "`."
        )
    else:
        detail = "This generated package uses its explicit private HTTPS endpoint."
    source = source.replace(_HOSTED_CONNECTION_SENTENCE, detail, 1)
    paragraphs = source.split("\n\n")
    removed_quota = [item for item in paragraphs
                     if item.startswith("The service allows ten completed renders per UTC day")]
    removed_connection = [item for item in paragraphs
                          if item.startswith("The green Blender Render plugin references")]
    if len(removed_quota) != 1 or len(removed_connection) != 1:
        raise ValueError("Reviewed render skill hosted policy text changed")
    paragraphs = [item for item in paragraphs
                  if item not in removed_quota and item not in removed_connection]
    route_section = (
        "## Generated route\n\n"
        + detail
        + " This route has no shared-service fallback. Service-specific quotas, retention, "
          "authentication, and operating budgets come only from the configured private service; "
          "do not infer the owner's hosted limits."
    )
    paragraphs.insert(2, route_section)
    return ("\n\n".join(paragraphs).rstrip() + "\n").encode()


def validate_package(
    root: Path, *, expected_plugin_name: str | None = None
) -> dict:
    """Validate the generated route boundary and return its traceable inventory."""
    unresolved_root = Path(root)
    if unresolved_root.is_symlink():
        raise ValueError("Plugin package root cannot be a symlink")
    root = unresolved_root.resolve()
    inventory = _tree_inventory(root)
    required = {".codex-plugin/plugin.json", ".mcp.json", "routing.json"}
    if not required.issubset(inventory) or not any(name.startswith("skills/") for name in inventory):
        raise ValueError("Incomplete Blender Render plugin package")
    manifest = _json(root / ".codex-plugin" / "plugin.json")
    route = _json(root / "routing.json")
    mcp = _json(root / ".mcp.json")
    if manifest.get("name") != root.name or not _PLUGIN_NAME.fullmatch(root.name):
        raise ValueError("Plugin folder and manifest names must match")
    if (
        expected_plugin_name is not None
        and manifest.get("name")
        != _validate_name(expected_plugin_name, "plugin name", _PLUGIN_NAME)
    ):
        raise ValueError("Plugin identity differs from the requested package")
    if manifest.get("skills") != "./skills/" or manifest.get("mcpServers") != "./.mcp.json":
        raise ValueError("Plugin manifest does not reference the shared skills and MCP config")
    _relative_target(root, manifest["skills"], "skills", directory=True)
    _relative_target(root, manifest["mcpServers"], "MCP configuration", directory=False)
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        raise ValueError("Plugin interface metadata is missing")
    for field in ("composerIcon", "logo"):
        if field in interface:
            _relative_target(root, interface[field], field, directory=False)
    if set(mcp.get("mcpServers", {})) != {"blender-render"}:
        raise ValueError("A packaged route must declare exactly one Blender Render MCP server")
    has_app = (root / ".app.json").is_file()
    if has_app != (manifest.get("apps") == "./.app.json"):
        raise ValueError("Registered app wiring and plugin manifest disagree")
    if has_app:
        _relative_target(root, manifest["apps"], "registered app configuration", directory=False)
    generator_files = inventory
    if _GENERATOR_RECORD in inventory:
        generator_files = _generator_owned(root, "plugin")
    if route.get("route") not in PACKAGE_ROUTES or route.get("shared_fallback") is not False:
        raise ValueError("Plugin route metadata is invalid")
    if route.get("registered_connection") is not has_app:
        raise ValueError("Plugin route metadata does not match registered app wiring")
    server = mcp["mcpServers"]["blender-render"]
    hosted_mcp = _json(template_root() / ".mcp.json")
    hosted_app = _json(template_root() / ".app.json")
    hosted_server = hosted_mcp["mcpServers"]["blender-render"]
    hosted_url = _canonical_https(hosted_server["url"], mcp_path=True)
    hosted_server["url"] = hosted_url
    if route["route"] == "hosted":
        expected_route = {
            "format_version": 2,
            "route": "hosted",
            "transport": "streamable-http",
            "private_input": False,
            "shared_fallback": False,
            "registered_connection": True,
            "service_scope": "owner-private-beta",
            **_descriptive_route(
                "registered-http", "declared-attachment", "remote-client-upload",
                "owner-private-beta-service"),
        }
        if route != expected_route or mcp != hosted_mcp or not has_app:
            raise ValueError("Hosted route differs from the reviewed connection")
        if _json(root / ".app.json") != hosted_app:
            raise ValueError("Hosted registered app differs from the reviewed connection")
    elif route["route"] == "private-stdio":
        profile = route.get("profile")
        endpoint = route.get("profile_endpoint")
        if profile == "local":
            profile_kind, expected_compute = "local", "local-blender"
        elif profile == "apptainer":
            profile_kind, expected_compute = "apptainer", "local-apptainer"
        elif endpoint is not None:
            profile_kind = "https"
            canonical_profile = _canonical_https(endpoint, mcp_path=False)
            if canonical_profile != endpoint or _service_origin(endpoint) == _service_origin(hosted_url):
                raise ValueError("Private STDIO route has an invalid profile endpoint")
            expected_compute = (
                "personal-modal-service" if _is_modal_endpoint(endpoint)
                else "private-https-service"
            )
        else:
            profile_kind, expected_compute = "hpc", "private-hpc"
        deployment_identity = route.get("deployment_identity")
        if deployment_identity is not None:
            _deployment_identity(deployment_identity)
        modal = endpoint is not None and _is_modal_endpoint(endpoint)
        if modal != (deployment_identity is not None):
            raise ValueError("Private STDIO Modal metadata and deployment identity disagree")
        expected_route = {
            "format_version": 2,
            "route": "private-stdio",
            "transport": "stdio",
            "profile": profile,
            "profile_kind": profile_kind,
            "private_input": True,
            "shared_fallback": False,
            "registered_connection": False,
            "service_scope": "private",
            **_descriptive_route(
                "stdio-profile", "user-selected-local-path",
                "client-host-filesystem", expected_compute),
        }
        if endpoint is not None:
            expected_route["profile_endpoint"] = endpoint
        if deployment_identity is not None:
            expected_route["deployment_identity"] = deployment_identity
        command = server.get("command") if isinstance(server, dict) else None
        if (route != expected_route or has_app or
                set(server) != {"command", "args"} or
                server["args"] != ["mcp", "--profile", profile] or
                _absolute_executable(command) != command):
            raise ValueError("Private STDIO route is not bound to its declared profile")
    else:
        canonical_url = _private_http_url(server.get("url", ""), hosted_url)
        modal = _is_modal_endpoint(canonical_url)
        expected_route = {
            "format_version": 2,
            "route": "private-http",
            "transport": "streamable-http",
            "private_input": True,
            "shared_fallback": False,
            "registered_connection": has_app,
            "authentication": "oauth",
            "service_scope": "private",
            **_descriptive_route(
                "http-endpoint", "declared-attachment", "remote-client-upload",
                "personal-modal-service" if modal else "explicit-private-https-service"),
        }
        deployment_identity = route.get("deployment_identity")
        if deployment_identity is not None:
            expected_route["deployment_identity"] = _deployment_identity(
                deployment_identity
            )
        expected = {"type", "url"}
        if route.get("authentication") == "bearer-env":
            expected.add("bearer_token_env_var")
            expected_route["authentication"] = "bearer-env"
        if route != expected_route or set(server) != expected or server["type"] != "http":
            raise ValueError("HTTP plugin route is invalid")
        if canonical_url != server["url"]:
            raise ValueError("Private HTTP route is not canonical")
        if modal and deployment_identity is None:
            raise ValueError("Personal Modal route lacks its deployment identity")
        if route["authentication"] == "bearer-env":
            if has_app:
                raise ValueError("Bearer-authenticated routes cannot declare a registered app")
            _validate_name(server["bearer_token_env_var"],
                           "bearer token environment variable", _ENVIRONMENT_NAME)
        elif has_app:
            app = _json(root / ".app.json")
            try:
                app_id = app["apps"]["blender-render"]["id"]
            except (KeyError, TypeError):
                raise ValueError("Private registered app configuration is invalid") from None
            if app != _app_document(app_id):
                raise ValueError("Private registered app configuration is invalid")
    if route["route"] != "hosted":
        hosted_app_id = hosted_app["apps"]["blender-render"]["id"]
        blocked = (
            hosted_url.casefold(),
            hosted_app_id.casefold(),
            "almostscheidplatz",
            "magnus bauer",
            "magnusbauer",
        )
        encoded = "\n".join(
            (root / name).read_text(errors="ignore").casefold()
            for name in generator_files
        )
        if any(value in encoded for value in blocked):
            raise ValueError("Private package contains owner-hosted identifiers")
        if (
            manifest.get("author") != {"name": "Blender Render operator"}
            or "homepage" in manifest
            or "repository" in manifest
            or interface.get("developerName") != "Blender Render operator"
            or any(field in interface for field in
                   ("websiteURL", "privacyPolicyURL", "termsOfServiceURL"))
        ):
            raise ValueError("Private plugin metadata is not operator-neutral")
        render_skill = (root / "skills" / "render" / "SKILL.md").read_text()
        folded_skill = render_skill.casefold()
        forbidden_skill_text = (
            "account-restricted private beta",
            "ten completed renders per utc day",
            "project credit ceiling",
            "blender render private beta",
        )
        expected_skill_text = (
            "local STDIO adapter" if route["route"] == "private-stdio"
            else "personal Modal endpoint"
            if route.get("expected_compute") == "personal-modal-service"
            else "private HTTPS endpoint"
        )
        if (any(value in folded_skill for value in forbidden_skill_text)
                or expected_skill_text not in render_skill
                or "Call `render_structure` once" not in render_skill
                or "do not redirect them to shared compute" not in render_skill):
            raise ValueError("Private render skill does not match its generated route")
    return {"route": route["route"], "plugin": manifest["name"],
            "files": inventory, "package_sha256": hashlib.sha256(
                json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def validate_marketplace(
    root: Path,
    *,
    expected_plugin_name: str | None = None,
    expected_marketplace_name: str | None = None,
) -> dict:
    unresolved_root = Path(root)
    if unresolved_root.is_symlink():
        raise ValueError("Marketplace root cannot be a symlink")
    root = unresolved_root.resolve()
    document = _json(root / ".agents" / "plugins" / "marketplace.json")
    marketplace_name = _validate_name(
        document.get("name", ""), "marketplace name", _MARKETPLACE_NAME
    )
    if (
        expected_marketplace_name is not None
        and marketplace_name
        != _validate_name(
            expected_marketplace_name, "marketplace name", _MARKETPLACE_NAME
        )
    ):
        raise ValueError("Marketplace identity differs from the requested package")
    entries = document.get("plugins")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError("A generated marketplace must contain exactly one plugin")
    entry = entries[0]
    name = _validate_name(entry.get("name"), "plugin name", _PLUGIN_NAME)
    if (
        expected_plugin_name is not None
        and name != _validate_name(expected_plugin_name, "plugin name", _PLUGIN_NAME)
    ):
        raise ValueError("Marketplace plugin identity differs from the requested package")
    expected = {"source": "local", "path": "./plugins/" + str(name)}
    if entry.get("source") != expected or entry.get("category") != "Productivity":
        raise ValueError("Generated marketplace source is invalid")
    if entry.get("policy") != {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}:
        raise ValueError("Generated marketplace policy is invalid")
    plugin = validate_package(
        root / "plugins" / name, expected_plugin_name=name
    )
    if (root / _GENERATOR_RECORD).is_file():
        _generator_owned(root, "marketplace", plugin_name=name)
    inventory = _tree_inventory(root)
    return {
        "marketplace": document["name"],
        "plugin": plugin,
        "files": inventory,
        "marketplace_sha256": hashlib.sha256(
            json.dumps(
                inventory, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    }


def _plugin_topology(root: Path) -> dict[str, object]:
    """Return only fields whose change can alter installed routing topology."""
    route = _json(root / "routing.json")
    manifest = _json(root / ".codex-plugin" / "plugin.json")
    return {
        "plugin": manifest.get("name"),
        "route": route.get("route"),
        "transport": route.get("transport"),
        "registered_connection": route.get("registered_connection"),
        "authentication": (
            route.get("authentication")
            if route.get("route") == "private-http"
            else None
        ),
    }


def _marketplace_topology(root: Path, plugin_name: str) -> dict[str, object]:
    document = _json(root / ".agents" / "plugins" / "marketplace.json")
    return {
        "marketplace": document.get("name"),
        "plugin": _plugin_topology(root / "plugins" / plugin_name),
    }


def _publish_transaction(target: Path) -> Path:
    return target.parent / ("." + target.name + ".blender-render-transaction")


def _validate_publish_tree(
    root: Path,
    kind: str,
    *,
    plugin_name: str,
    marketplace_name: str | None,
) -> None:
    if kind == "plugin":
        validate_package(root, expected_plugin_name=plugin_name)
    elif kind == "marketplace" and marketplace_name is not None:
        validate_marketplace(
            root,
            expected_plugin_name=plugin_name,
            expected_marketplace_name=marketplace_name,
        )
    else:
        raise ValueError("Unknown generator ownership kind")


def _recover_publish_transaction(
    target: Path,
    kind: str,
    *,
    plugin_name: str,
    marketplace_name: str | None,
) -> None:
    transaction = _publish_transaction(target)
    if not transaction.exists() and not transaction.is_symlink():
        return
    if transaction.is_symlink() or not transaction.is_dir():
        raise RuntimeError(
            "An invalid interrupted publication transaction requires inspection: "
            + str(transaction)
        )
    previous = transaction / target.name
    if target.exists() or target.is_symlink():
        if previous.exists() or previous.is_symlink():
            raise RuntimeError(
                "Both the package and its interrupted publication backup exist; "
                "inspect them without deleting either: "
                + str(transaction)
            )
        raise RuntimeError(
            "The package exists beside an incomplete publication transaction; "
            "inspect both without deleting either: "
            + str(transaction)
        )
    if previous.is_symlink() or not previous.is_dir():
        raise RuntimeError(
            "The interrupted publication backup is incomplete; inspect it at "
            + str(transaction)
        )
    _validate_publish_tree(
        previous,
        kind,
        plugin_name=plugin_name,
        marketplace_name=marketplace_name,
    )
    previous.replace(target)
    failed = transaction / "failed"
    if failed.exists() and not failed.is_symlink():
        shutil.rmtree(failed)
    transaction.rmdir()
    raise RuntimeError(
        "Recovered the package from an interrupted publication; rerun the requested update"
    )


def _publish(
    draft: Path,
    target: Path,
    kind: str,
    force: bool,
    *,
    plugin_name: str,
    marketplace_name: str | None = None,
) -> None:
    _recover_publish_transaction(
        target,
        kind,
        plugin_name=plugin_name,
        marketplace_name=marketplace_name,
    )
    if not target.exists() and not target.is_symlink():
        draft.replace(target)
        return
    if not force:
        raise ValueError(f"{kind.title()} destination already exists")
    if target.is_symlink() or not target.is_dir():
        raise ValueError("Force requires a generator-owned directory")

    if kind == "plugin":
        validate_package(target, expected_plugin_name=plugin_name)
        validate_package(draft, expected_plugin_name=plugin_name)
        current_topology = _plugin_topology(target)
        requested_topology = _plugin_topology(draft)
        owned_files = _generator_owned(target, kind)
        draft_owned_files = _generator_owned(draft, kind)
    elif kind == "marketplace":
        if marketplace_name is None:
            raise ValueError("Marketplace force requires an explicit marketplace identity")
        validate_marketplace(
            target,
            expected_plugin_name=plugin_name,
            expected_marketplace_name=marketplace_name,
        )
        validate_marketplace(
            draft,
            expected_plugin_name=plugin_name,
            expected_marketplace_name=marketplace_name,
        )
        current_topology = _marketplace_topology(target, plugin_name)
        requested_topology = _marketplace_topology(draft, plugin_name)
        owned_files = _generator_owned(
            target, kind, plugin_name=plugin_name
        )
        draft_owned_files = _generator_owned(
            draft, kind, plugin_name=plugin_name
        )
    else:
        raise ValueError("Unknown generator ownership kind")

    if current_topology != requested_topology:
        raise ValueError(
            "Force cannot change route, authentication, app, plugin, or marketplace "
            "topology; package the new route to a new destination"
        )
    owned = set(owned_files)
    owned.add(_GENERATOR_RECORD)
    draft_owned = set(draft_owned_files)
    draft_owned.add(_GENERATOR_RECORD)
    if owned != draft_owned:
        raise ValueError(
            "Force cannot add or remove generator-owned paths; use a new destination"
        )
    existing = _tree_inventory(target)
    generated = _tree_inventory(draft)
    extras = set(existing) - owned
    collisions = extras.intersection(generated)
    if collisions:
        raise ValueError(
            "Force would replace a non-generator file: " + sorted(collisions)[0]
        )
    extra_hashes = {}
    for name in sorted(extras):
        source = target / name
        output = draft / name
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != existing[name]:
            raise ValueError("Existing output changed while force was staging")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        extra_hashes[name] = digest
    if kind == "plugin":
        validate_package(draft, expected_plugin_name=plugin_name)
    else:
        validate_marketplace(
            draft,
            expected_plugin_name=plugin_name,
            expected_marketplace_name=marketplace_name,
        )
    transaction = _publish_transaction(target)
    transaction.mkdir(mode=0o700)
    previous = transaction / target.name
    failed = transaction / "failed"
    try:
        target.replace(previous)
        if _tree_inventory(previous) != existing:
            raise ValueError("Existing output changed while force was committing")
        draft.replace(target)
        updated = _tree_inventory(target)
        if any(updated.get(name) != digest for name, digest in extra_hashes.items()):
            raise ValueError("Force did not preserve non-generator files")
    except BaseException:
        if (
            not previous.exists()
            and not previous.is_symlink()
            and (target.exists() or target.is_symlink())
        ):
            try:
                transaction.rmdir()
            except BaseException as cleanup:
                raise RuntimeError(
                    "Publication stopped before the package swap, but transaction "
                    "cleanup failed; inspect " + str(transaction)
                ) from cleanup
            raise
        restored = False
        try:
            if target.exists() or target.is_symlink():
                target.replace(failed)
            if previous.exists() or previous.is_symlink():
                previous.replace(target)
            else:
                raise RuntimeError("The previous package is unavailable")
            restored = _tree_inventory(target) == existing
            if not restored:
                raise RuntimeError("The restored package does not match its backup")
        except BaseException as rollback:
            # Path.replace can complete and still be interrupted before returning.
            # Recognize that exact restored state before declaring rollback failure.
            if not previous.exists() and not previous.is_symlink():
                try:
                    restored = (
                        target.is_dir()
                        and not target.is_symlink()
                        and _tree_inventory(target) == existing
                    )
                except BaseException:
                    restored = False
            if restored:
                pass
            else:
                raise RuntimeError(
                    "Publication rollback failed; retain the recovery backup at "
                    + str(previous)
                    + " and inspect the transaction at "
                    + str(transaction)
                ) from rollback
        try:
            # The failed generated tree is disposable only after the prior
            # package, including operator-owned extras, is verified in place.
            if failed.exists() and not failed.is_symlink():
                shutil.rmtree(failed)
            transaction.rmdir()
        except BaseException as cleanup:
            raise RuntimeError(
                "The previous package was restored, but transaction cleanup failed; inspect "
                + str(transaction)
            ) from cleanup
        raise
    shutil.rmtree(previous)
    transaction.rmdir()


def package_plugin(target: Path, route: str, *, profile: str | None = None,
                   executable: str | None = None, url: str | None = None,
                   app_id: str | None = None,
                   bearer_token_env_var: str | None = None,
                   deployment_identity: str | None = None,
                   config: dict | None = None, force: bool = False) -> dict:
    """Create one installable route package without mutating user configuration."""
    if type(force) is not bool:
        raise ValueError("force must be a boolean")
    target = Path(target).expanduser().absolute()
    _validate_name(target.name, "plugin name", _PLUGIN_NAME)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    template = template_root()
    mcp, app, routing = _route_inputs(
        route, profile=profile, executable=executable, url=url, app_id=app_id,
        bearer_token_env_var=bearer_token_env_var,
        deployment_identity=deployment_identity, config=config or {}, template=template)
    with tempfile.TemporaryDirectory(prefix=".blender-render-plugin-", dir=parent) as temporary:
        draft = Path(temporary) / target.name
        draft.mkdir()
        for directory in _COMMON_DIRECTORIES:
            source = template / directory
            inventory = _tree_inventory(source)
            for name, digest in inventory.items():
                data = (source / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("Shared plugin assets changed during packaging")
                if route != "hosted" and directory == "skills" and name == "render/SKILL.md":
                    data = _private_render_skill(template, route, routing)
                output = draft / directory / name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
        _write_json(draft / ".codex-plugin" / "plugin.json", _manifest(template, target.name, app, route))
        _write_json(draft / ".mcp.json", mcp)
        _write_json(draft / "routing.json", routing)
        if app is not None:
            _write_json(draft / ".app.json", app)
        _write_generator_record(draft, "plugin")
        validate_package(draft, expected_plugin_name=target.name)
        _publish(
            draft,
            target,
            "plugin",
            force,
            plugin_name=target.name,
        )
    return {
        **validate_package(target, expected_plugin_name=target.name),
        "path": str(target),
    }


def package_marketplace(target: Path, route: str, *, plugin_name: str,
                        marketplace_name: str | None = None, profile: str | None = None,
                        executable: str | None = None, url: str | None = None,
                        app_id: str | None = None,
                        bearer_token_env_var: str | None = None,
                        deployment_identity: str | None = None,
                        config: dict | None = None, force: bool = False) -> dict:
    """Create a new one-plugin local marketplace, ready for Codex installation."""
    if type(force) is not bool:
        raise ValueError("force must be a boolean")
    target = Path(target).expanduser().absolute()
    _validate_name(plugin_name, "plugin name", _PLUGIN_NAME)
    selected_marketplace = marketplace_name or plugin_name
    _validate_name(selected_marketplace, "marketplace name", _MARKETPLACE_NAME)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".blender-render-marketplace-", dir=parent) as temporary:
        draft = Path(temporary) / target.name
        package_plugin(draft / "plugins" / plugin_name, route, profile=profile,
                       executable=executable, url=url, app_id=app_id,
                       bearer_token_env_var=bearer_token_env_var,
                       deployment_identity=deployment_identity, config=config)
        marketplace = {
            "name": selected_marketplace,
            "interface": {"displayName": "Blender Render " + plugin_name},
            "plugins": [{
                "name": plugin_name,
                "source": {"source": "local", "path": "./plugins/" + plugin_name},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }],
        }
        _write_json(draft / ".agents" / "plugins" / "marketplace.json", marketplace)
        _write_generator_record(draft, "marketplace", plugin_name=plugin_name)
        validate_marketplace(
            draft,
            expected_plugin_name=plugin_name,
            expected_marketplace_name=selected_marketplace,
        )
        _publish(
            draft,
            target,
            "marketplace",
            force,
            plugin_name=plugin_name,
            marketplace_name=selected_marketplace,
        )
    return {**validate_marketplace(
                target,
                expected_plugin_name=plugin_name,
                expected_marketplace_name=selected_marketplace,
            ), "path": str(target),
            "plugin_path": str(target / "plugins" / plugin_name)}
