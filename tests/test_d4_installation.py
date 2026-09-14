"""D4 installation/configuration routing contracts; no host or network actions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

from blender_render import cli
import blender_render_plugin.package as package_module
from blender_render_plugin import (
    package_marketplace,
    package_plugin,
    template_root,
    validate_marketplace,
    validate_package,
)


HOSTED_URL = "https://almostscheidplatz--blender-render-beta-gateway.us-east.modal.direct/mcp"
HOSTED_APP = "plugin_asdk_app_6a9e15579c0881919000f5b4f2fdd71f"
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


@pytest.fixture(scope="module", autouse=True)
def unload_packaging_namespace_after_d4_contracts():
    """Keep the separate packaging namespace out of later renderer snapshots."""
    yield
    for name in list(sys.modules):
        if name == "blender_render_plugin" or name.startswith("blender_render_plugin."):
            sys.modules.pop(name, None)


def read(path):
    return json.loads(Path(path).read_text())


def common_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for directory in ("assets", "skills")
            for path in sorted((root / directory).rglob("*")) if path.is_file()}


def tree_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(Path(root).rglob("*")) if path.is_file()}


def test_hosted_package_is_explicit_and_traceable(tmp_path):
    target = tmp_path / "blender-render"
    result = package_plugin(target, "hosted")
    assert {key: value for key, value in result.items() if key != "path"} == validate_package(target)
    assert result["route"] == "hosted"
    assert read(target / ".mcp.json")["mcpServers"]["blender-render"]["url"] == HOSTED_URL
    assert read(target / ".app.json")["apps"]["blender-render"]["id"] == HOSTED_APP
    assert read(target / "routing.json")["service_scope"] == "owner-private-beta"
    assert read(target / "routing.json")["shared_fallback"] is False
    assert common_bytes(target) == common_bytes(template_root())


@pytest.mark.parametrize("profile, config", [
    ("local", {}),
    ("apptainer", {}),
    ("lab", {"profiles": {"lab": HPC_PROFILE}}),
    ("mine", {"profiles": {"mine": {
        "kind": "https", "private": True, "url": "https://render.example.edu"}}}),
])
def test_private_stdio_has_no_hosted_dependency(tmp_path, profile, config):
    target = tmp_path / ("blender-render-" + profile)
    result = package_plugin(target, "private-stdio", profile=profile,
                            executable="/opt/blender-render/bin/blender-render", config=config)
    server = read(target / ".mcp.json")["mcpServers"]["blender-render"]
    manifest = read(target / ".codex-plugin/plugin.json")
    route = read(target / "routing.json")
    assert result["route"] == "private-stdio"
    assert server == {"command": "/opt/blender-render/bin/blender-render",
                      "args": ["mcp", "--profile", profile]}
    profile_kind = "local" if profile == "local" else "apptainer" if profile == "apptainer" else (
        "https" if profile == "mine" else "hpc")
    expected_compute = {"local": "local-blender", "apptainer": "local-apptainer",
                        "https": "private-https-service", "hpc": "private-hpc"}[profile_kind]
    assert route == {"format_version": 2, "route": "private-stdio", "transport": "stdio",
                     "profile": profile, "profile_kind": profile_kind, "private_input": True,
                     "shared_fallback": False, "registered_connection": False,
                     "service_scope": "private", "adapter_type": "stdio-profile",
                     "input_source": "user-selected-local-path",
                     "input_location": "client-host-filesystem",
                     "expected_compute": expected_compute,
                     **({"profile_endpoint": "https://render.example.edu"}
                        if profile == "mine" else {})}
    assert "apps" not in manifest and not (target / ".app.json").exists()
    encoded = "\n".join(path.read_text(errors="ignore") for path in target.rglob("*") if path.is_file())
    assert all(value not in encoded.casefold() for value in
               (HOSTED_URL.casefold(), HOSTED_APP.casefold(),
                "almostscheidplatz", "magnus bauer", "magnusbauer"))
    assert (target / "assets/molecule.svg").read_bytes() == (
        template_root() / "assets/molecule.svg").read_bytes()
    assert (target / "skills/develop/SKILL.md").read_bytes() == (
        template_root() / "skills/develop/SKILL.md").read_bytes()
    assert (target / "skills/render/SKILL.md").read_bytes() != (
        template_root() / "skills/render/SKILL.md").read_bytes()


@pytest.mark.parametrize("profile, config", [
    ("unknown", {}),
    ("hosted", {"profiles": {"hosted": {"kind": "https", "url": HOSTED_URL}}}),
    ("hosted-base", {"profiles": {"hosted-base": {
        "kind": "https", "private": True, "url": HOSTED_URL.removesuffix("/mcp")}}}),
])
def test_private_stdio_refuses_unknown_or_shared_profile_atomically(tmp_path, profile, config):
    target = tmp_path / "blender-render-private"
    with pytest.raises(ValueError, match="profile|shared"):
        package_plugin(target, "private-stdio", profile=profile,
                       executable="/opt/bin/blender-render", config=config)
    assert not target.exists()


@pytest.mark.parametrize("executable", ["blender-render", "", "relative/bin/blender-render"])
def test_private_stdio_requires_absolute_executable(tmp_path, executable):
    target = tmp_path / "blender-render-private"
    with pytest.raises(ValueError, match="absolute"):
        package_plugin(target, "private-stdio", profile="local", executable=executable)
    assert not target.exists()


def test_private_http_is_one_endpoint_without_hosted_fallback(tmp_path):
    target = tmp_path / "blender-render-lab"
    endpoint = "https://render.example.edu/mcp"
    result = package_plugin(target, "private-http", url=endpoint)
    assert result["route"] == "private-http"
    assert read(target / ".mcp.json") == {
        "mcpServers": {"blender-render": {"type": "http", "url": endpoint}}}
    assert "apps" not in read(target / ".codex-plugin/plugin.json")
    assert not (target / ".app.json").exists()
    encoded = "\n".join(path.read_text(errors="ignore") for path in target.rglob("*") if path.is_file())
    assert all(value not in encoded.casefold() for value in
               (HOSTED_URL.casefold(), HOSTED_APP.casefold(),
                "almostscheidplatz", "magnus bauer", "magnusbauer"))
    manifest = read(target / ".codex-plugin/plugin.json")
    assert manifest["author"] == {"name": "Blender Render operator"}
    assert manifest["interface"]["developerName"] == "Blender Render operator"
    assert "homepage" not in manifest and "repository" not in manifest
    for value, directory in (
        (manifest["skills"], True),
        (manifest["mcpServers"], False),
        (manifest["interface"]["composerIcon"], False),
        (manifest["interface"]["logo"], False),
    ):
        resolved = target / value.removeprefix("./")
        assert resolved.is_dir() if directory else resolved.is_file()


def test_private_http_url_is_canonical(tmp_path):
    target = tmp_path / "blender-render-private"
    package_plugin(
        target,
        "private-http",
        url="HTTPS://RENDER.EXAMPLE.EDU:443/mcp/",
    )
    server = read(target / ".mcp.json")["mcpServers"]["blender-render"]
    assert server["url"] == "https://render.example.edu/mcp"


def test_private_http_bearer_uses_an_environment_reference_only(tmp_path):
    target = tmp_path / "blender-render-private"
    package_plugin(target, "private-http", url="https://render.example.edu/mcp",
                   bearer_token_env_var="BLENDER_RENDER_PRIVATE_TOKEN")
    server = read(target / ".mcp.json")["mcpServers"]["blender-render"]
    assert server["bearer_token_env_var"] == "BLENDER_RENDER_PRIVATE_TOKEN"
    assert "token" not in read(target / "routing.json")
    assert read(target / "routing.json")["authentication"] == "bearer-env"


def test_private_http_registered_connection_is_explicit(tmp_path):
    target = tmp_path / "blender-render-lab"
    app_id = "plugin_asdk_app_private123"
    package_plugin(target, "private-http", url="https://render.example.edu/mcp", app_id=app_id)
    assert read(target / ".app.json") == {"apps": {"blender-render": {"id": app_id}}}
    assert read(target / ".codex-plugin/plugin.json")["apps"] == "./.app.json"


@pytest.mark.parametrize("options", [
    {"app_id": "plugin_asdk_app_private123",
     "bearer_token_env_var": "BLENDER_RENDER_PRIVATE_TOKEN"},
    {"bearer_token_env_var": "NOT-AN-ENVIRONMENT-NAME"},
])
def test_private_http_rejects_conflicting_or_invalid_auth_atomically(tmp_path, options):
    target = tmp_path / "blender-render-private"
    with pytest.raises(ValueError):
        package_plugin(target, "private-http",
                       url="https://render.example.edu/mcp", **options)
    assert not target.exists()


@pytest.mark.parametrize("url", [
    HOSTED_URL,
    "HTTPS://ALMOSTSCHEIDPLATZ--BLENDER-RENDER-BETA-GATEWAY.US-EAST.MODAL.DIRECT:443/mcp/",
    "http://render.example.edu/mcp",
    "http://127.0.0.1:8765/mcp",
    "https://user:secret@render.example.edu/mcp",
    "https://render.example.edu/mcp?token=secret",
    "https://render.example.edu/mcp#token=secret",
    "https://render.example.edu/tenant/mcp",
    "https://render.example.edu/api",
    " https://render.example.edu/mcp",
])
def test_private_http_rejects_shared_insecure_or_credential_urls_atomically(tmp_path, url):
    target = tmp_path / "blender-render-private"
    with pytest.raises(ValueError):
        package_plugin(target, "private-http", url=url)
    assert not target.exists()


def test_destination_is_new_and_name_matches_manifest(tmp_path):
    target = tmp_path / "blender-render-local"
    first = package_plugin(target, "private-stdio", profile="local",
                           executable="/opt/bin/blender-render")
    before = common_bytes(target)
    with pytest.raises(ValueError, match="already exists"):
        package_plugin(target, "hosted")
    assert common_bytes(target) == before
    assert first["plugin"] == target.name
    with pytest.raises(ValueError, match="Invalid plugin name"):
        package_plugin(tmp_path / "bad name", "hosted")


def test_validator_rejects_tampered_route_boundaries(tmp_path):
    private = tmp_path / "blender-render-private"
    package_plugin(private, "private-http", url="https://render.example.edu/mcp")
    (private / ".blender-render-generator.json").unlink()
    write = private / ".mcp.json"
    document = read(write)
    document["mcpServers"]["blender-render"]["url"] = HOSTED_URL
    write.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="shared hosted service"):
        validate_package(private)

    hosted = tmp_path / "blender-render-hosted"
    package_plugin(hosted, "hosted")
    (hosted / ".blender-render-generator.json").unlink()
    write = hosted / ".app.json"
    document = read(write)
    document["apps"]["blender-render"]["id"] = "plugin_asdk_app_different"
    write.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="differs"):
        validate_package(hosted)


def test_cli_packages_from_config_without_starting_a_service(tmp_path, monkeypatch):
    target = tmp_path / "blender-render-ipd"
    monkeypatch.setattr(cli, "load_config", lambda: {"profiles": {"ipd": HPC_PROFILE}})
    args = cli.parser().parse_args([
        "plugin", "package", str(target), "--route", "private-stdio",
        "--profile", "ipd", "--executable", "/installed/bin/blender-render"])
    result = cli.execute(args)
    assert result["route"] == "private-stdio"
    assert read(target / ".mcp.json")["mcpServers"]["blender-render"]["args"][-1] == "ipd"


def test_cli_hosted_generation_does_not_read_runtime_config(tmp_path, monkeypatch):
    def forbidden_config_read():
        raise AssertionError("hosted generation read runtime configuration")

    monkeypatch.setattr(cli, "load_config", forbidden_config_read)
    target = tmp_path / "blender-render-hosted"
    args = cli.parser().parse_args([
        "plugin", "package", str(target), "--route", "hosted"
    ])
    assert cli.execute(args)["route"] == "hosted"


def test_atomic_marketplace_is_ready_for_one_private_plugin(tmp_path):
    target = tmp_path / "lab-marketplace"
    result = package_marketplace(
        target, "private-stdio", plugin_name="blender-render-ipd",
        marketplace_name="lab-render", profile="ipd",
        executable="/installed/bin/blender-render",
        config={"profiles": {"ipd": HPC_PROFILE}})
    assert result == {**validate_marketplace(target), "path": str(target),
                      "plugin_path": str(target / "plugins" / "blender-render-ipd")}
    market = read(target / ".agents/plugins/marketplace.json")
    assert market["name"] == "lab-render"
    assert market["plugins"][0]["source"]["path"] == "./plugins/blender-render-ipd"
    plugin = target / "plugins" / "blender-render-ipd"
    assert not (plugin / ".app.json").exists()
    assert read(plugin / "routing.json")["shared_fallback"] is False
    with pytest.raises(ValueError, match="already exists"):
        package_marketplace(
            target, "hosted", plugin_name="blender-render-ipd"
        )


def test_marketplace_force_preserves_non_generator_files(tmp_path):
    target = tmp_path / "private-marketplace"
    package_marketplace(
        target,
        "private-http",
        plugin_name="blender-render-private",
        url="https://old-render.example.edu/mcp",
    )
    note = target / "operator-notes.txt"
    note.write_text("preserve marketplace note\n")
    package_marketplace(
        target,
        "private-http",
        plugin_name="blender-render-private",
        url="https://new-render.example.edu/mcp",
        force=True,
    )
    assert note.read_text() == "preserve marketplace note\n"
    plugin = target / "plugins" / "blender-render-private"
    assert read(plugin / "routing.json")["route"] == "private-http"
    assert validate_marketplace(target)["marketplace_sha256"]


def test_source_template_declares_hosted_route_and_valid_plugin():
    root = template_root()
    assert read(root / "routing.json")["route"] == "hosted"
    assert read(root / "routing.json")["service_scope"] == "owner-private-beta"
    assert read(root / ".codex-plugin/plugin.json")["name"] == "blender-render"
    assert common_bytes(root)


def test_installed_template_origin_when_installed_contract_runs():
    import os
    if os.environ.get("C3_INSTALLED_SITE"):
        root = template_root()
        assert root.is_relative_to(Path(os.environ["C3_INSTALLED_SITE"]))


def test_personal_modal_requires_explicit_non_secret_identity(tmp_path):
    endpoint = "https://operator--blender-render.us-east.modal.direct/mcp"
    missing = tmp_path / "blender-render-modal-missing"
    with pytest.raises(ValueError, match="deployment identity"):
        package_plugin(missing, "private-http", url=endpoint)
    assert not missing.exists()

    target = tmp_path / "blender-render-modal"
    package_plugin(target, "private-http", url=endpoint,
                   deployment_identity="personal-render-v1")
    route = read(target / "routing.json")
    assert route["deployment_identity"] == "personal-render-v1"
    assert "deployment_identity" not in read(target / ".mcp.json")
    with pytest.raises(ValueError, match="non-secret"):
        package_plugin(
            tmp_path / "blender-render-modal-invalid",
            "private-http",
            url=endpoint,
            deployment_identity="workspace/token=secret",
        )


def test_force_replaces_only_verified_generator_files(tmp_path):
    target = tmp_path / "blender-render-route"
    package_plugin(target, "private-stdio", profile="local",
                   executable="/opt/bin/blender-render-v1")
    note = target / "operator-notes.txt"
    note.write_text("preserve this operator file\n")
    package_plugin(target, "private-stdio",
                   profile="apptainer", executable="/opt/bin/blender-render-v2", force=True)
    assert note.read_text() == "preserve this operator file\n"
    assert not (target / ".app.json").exists()
    assert read(target / "routing.json")["route"] == "private-stdio"
    assert read(target / ".mcp.json")["mcpServers"]["blender-render"] == {
        "command": "/opt/bin/blender-render-v2",
        "args": ["mcp", "--profile", "apptainer"],
    }

    unowned = tmp_path / "not-generator-owned"
    unowned.mkdir()
    (unowned / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="Incomplete|generator"):
        package_plugin(unowned, "hosted", force=True)
    assert (unowned / "keep.txt").read_text() == "keep"


def test_force_refuses_route_or_app_topology_changes_atomically(tmp_path):
    route_target = tmp_path / "blender-render-route"
    package_plugin(route_target, "hosted")
    route_before = tree_bytes(route_target)
    with pytest.raises(ValueError, match="topology|new destination"):
        package_plugin(route_target, "private-stdio", profile="local",
                       executable="/opt/bin/blender-render", force=True)
    assert tree_bytes(route_target) == route_before

    app_target = tmp_path / "blender-render-app"
    package_plugin(app_target, "private-http",
                   url="https://render.example.edu/mcp")
    app_before = tree_bytes(app_target)
    with pytest.raises(ValueError, match="topology|new destination"):
        package_plugin(app_target, "private-http",
                       url="https://render.example.edu/mcp",
                       app_id="plugin_asdk_app_private123", force=True)
    assert tree_bytes(app_target) == app_before


def test_marketplace_force_refuses_identity_change_atomically(tmp_path):
    target = tmp_path / "private-marketplace"
    package_marketplace(target, "private-http",
                        plugin_name="blender-render-private",
                        marketplace_name="lab-render",
                        url="https://old-render.example.edu/mcp")
    before = tree_bytes(target)
    with pytest.raises(ValueError, match="identity|topology|new destination"):
        package_marketplace(target, "private-http",
                            plugin_name="blender-render-private",
                            marketplace_name="renamed-marketplace",
                            url="https://new-render.example.edu/mcp", force=True)
    assert tree_bytes(target) == before


def test_force_refuses_modified_generator_files(tmp_path):
    target = tmp_path / "blender-render-route"
    package_plugin(target, "hosted")
    before = (target / ".mcp.json").read_bytes()
    (target / ".mcp.json").write_text("{}\n")
    with pytest.raises(ValueError, match="modified|declare|invalid"):
        package_plugin(target, "hosted", force=True)
    assert (target / ".mcp.json").read_text() == "{}\n"
    assert before != (target / ".mcp.json").read_bytes()


def test_generator_never_reads_secret_values(tmp_path, monkeypatch):
    secret = tmp_path / "secret-token"
    secret.write_text("do-not-read-or-copy")
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == secret:
            raise AssertionError("generator read a secret file")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    target = tmp_path / "blender-render-hpc"
    package_plugin(
        target,
        "private-stdio",
        profile="lab",
        executable="/opt/bin/blender-render",
        config={"profiles": {"lab": {
            **HPC_PROFILE, "token_file": str(secret)
        }}},
    )
    assert "do-not-read-or-copy" not in "\n".join(
        path.read_text(errors="ignore")
        for path in target.rglob("*")
        if path.is_file()
    )
    monkeypatch.setenv("BLENDER_RENDER_PRIVATE_TOKEN", "secret-from-environment")
    https_target = tmp_path / "blender-render-https"
    package_plugin(
        https_target,
        "private-http",
        url="https://render.example.edu/mcp",
        bearer_token_env_var="BLENDER_RENDER_PRIVATE_TOKEN",
    )
    assert "secret-from-environment" not in "\n".join(
        path.read_text(errors="ignore")
        for path in https_target.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize("url", [
    HOSTED_URL.replace("almostscheidplatz", "ａｌｍｏｓｔｓｃｈｅｉｄｐｌａｔｚ"),
    HOSTED_URL.replace("modal.direct", "ｍｏｄａｌ．ｄｉｒｅｃｔ"),
    HOSTED_URL.replace("modal.direct", "modal.direct."),
    HOSTED_URL.replace("/mcp", ":443/mcp/"),
])
def test_owner_endpoint_unicode_idna_dot_and_port_aliases_are_rejected(tmp_path, url):
    target = tmp_path / "blender-render-private"
    with pytest.raises(ValueError, match="shared hosted service"):
        package_plugin(target, "private-http", url=url)
    assert not target.exists()


@pytest.mark.parametrize("url", [
    "https://operator--renderer.us-east.ｍｏｄａｌ．ｄｉｒｅｃｔ/mcp",
    "https://operator--renderer.us-east.modal.direct.:443/mcp/",
])
def test_modal_unicode_and_terminal_dot_aliases_require_identity(tmp_path, url):
    target = tmp_path / "blender-render-modal"
    with pytest.raises(ValueError, match="deployment identity"):
        package_plugin(target, "private-http", url=url)
    assert not target.exists()


@pytest.mark.parametrize("url, canonical", [
    ("https://ＲＥＮＤＥＲ．ＥＸＡＭＰＬＥ．ＥＤＵ.:443/mcp/",
     "https://render.example.edu/mcp"),
    ("https://BÜCHER.example.edu.:443/mcp/",
     "https://xn--bcher-kva.example.edu/mcp"),
])
def test_private_http_serializes_canonical_idna_url(tmp_path, url, canonical):
    target = tmp_path / "blender-render-private"
    package_plugin(target, "private-http", url=url)
    assert read(target / ".mcp.json")["mcpServers"]["blender-render"]["url"] == canonical


@pytest.mark.parametrize("profile_url", [
    HOSTED_URL.removesuffix("/mcp").replace(
        "almostscheidplatz", "ａｌｍｏｓｔｓｃｈｅｉｄｐｌａｔｚ"),
    HOSTED_URL.removesuffix("/mcp").replace("modal.direct", "modal.direct."),
    HOSTED_URL.removesuffix("/mcp").replace("https://", "https://").replace(
        ".us-east.", ".us-east.", 1) + ":443/",
])
def test_named_stdio_profile_cannot_alias_owner_service(tmp_path, profile_url):
    target = tmp_path / "blender-render-private"
    config = {"profiles": {"lab": {
        "kind": "https", "private": True, "url": profile_url,
    }}}
    with pytest.raises(ValueError, match="shared endpoint profile"):
        package_plugin(target, "private-stdio", profile="lab",
                       executable="/opt/bin/blender-render", config=config)
    assert not target.exists()


def test_named_stdio_modal_alias_requires_identity_and_records_canonical_endpoint(tmp_path):
    config = {"profiles": {"personal": {
        "kind": "https", "private": True,
        "url": "https://operator--renderer.us-east.ｍｏｄａｌ．ｄｉｒｅｃｔ.:443/",
    }}}
    with pytest.raises(ValueError, match="deployment identity"):
        package_plugin(tmp_path / "modal-missing", "private-stdio",
                       profile="personal", executable="/opt/bin/blender-render",
                       config=config)
    target = tmp_path / "modal-private"
    package_plugin(target, "private-stdio", profile="personal",
                   executable="/opt/bin/blender-render", config=config,
                   deployment_identity="personal-render-v1")
    route = read(target / "routing.json")
    assert route["profile_endpoint"] == "https://operator--renderer.us-east.modal.direct"
    assert route["deployment_identity"] == "personal-render-v1"


def _forge_owned_path(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    marker = root / ".blender-render-generator.json"
    document = read(marker)
    document["files"][name] = hashlib.sha256(data).hexdigest()
    marker.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def test_plugin_force_never_trusts_forged_marker_paths(tmp_path):
    target = tmp_path / "blender-render-private"
    package_plugin(target, "hosted")
    _forge_owned_path(target, "operator-notes.txt", b"must remain operator-owned\n")
    try:
        package_plugin(target, "private-stdio", profile="local",
                       executable="/opt/bin/blender-render", force=True)
    except ValueError:
        pass
    assert (target / "operator-notes.txt").read_bytes() == b"must remain operator-owned\n"


def test_plugin_force_never_deletes_forged_optional_app_path(tmp_path):
    target = tmp_path / "blender-render-private"
    package_plugin(target, "private-stdio", profile="local",
                   executable="/opt/bin/blender-render")
    _forge_owned_path(
        target,
        ".app.json",
        b'{"operator_owned": "must remain"}\n',
    )
    before = tree_bytes(target)
    with pytest.raises(ValueError, match="wiring|ownership|allowlist"):
        package_plugin(target, "private-stdio", profile="local",
                       executable="/opt/bin/blender-render-v2", force=True)
    assert tree_bytes(target) == before


def test_marketplace_force_never_trusts_forged_marker_paths(tmp_path):
    target = tmp_path / "private-marketplace"
    package_marketplace(target, "private-stdio",
                        plugin_name="blender-render-private", profile="local",
                        executable="/opt/bin/blender-render")
    _forge_owned_path(target, "operator-notes.txt", b"must remain operator-owned\n")
    try:
        package_marketplace(target, "private-http",
                            plugin_name="blender-render-private",
                            url="https://render.example.edu/mcp", force=True)
    except ValueError:
        pass
    assert (target / "operator-notes.txt").read_bytes() == b"must remain operator-owned\n"


def test_marketplace_force_does_not_follow_forged_plugin_prefix(tmp_path):
    target = tmp_path / "private-marketplace"
    package_marketplace(target, "private-stdio",
                        plugin_name="blender-render-private", profile="local",
                        executable="/opt/bin/blender-render")
    victim = target / "plugins" / "victim"
    package_plugin(victim, "private-stdio", profile="local",
                   executable="/opt/bin/blender-render")
    (victim / "operator-owned.txt").write_text("must remain operator-owned\n")

    marketplace_path = target / ".agents/plugins/marketplace.json"
    marketplace = read(marketplace_path)
    marketplace["plugins"][0]["name"] = "victim"
    marketplace["plugins"][0]["source"]["path"] = "./plugins/victim"
    marketplace_path.write_text(json.dumps(marketplace, indent=2, sort_keys=True) + "\n")

    nested_marker_path = victim / ".blender-render-generator.json"
    nested_marker = read(nested_marker_path)
    marker = read(target / ".blender-render-generator.json")
    marker["files"] = {
        ".agents/plugins/marketplace.json": hashlib.sha256(
            marketplace_path.read_bytes()
        ).hexdigest(),
        **{
            "plugins/victim/" + name: digest
            for name, digest in nested_marker["files"].items()
        },
        "plugins/victim/.blender-render-generator.json": hashlib.sha256(
            nested_marker_path.read_bytes()
        ).hexdigest(),
    }
    (target / ".blender-render-generator.json").write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n"
    )
    before = tree_bytes(target)

    with pytest.raises(ValueError, match="identity|requested"):
        package_marketplace(target, "private-stdio",
                            plugin_name="blender-render-private", profile="local",
                            executable="/opt/bin/blender-render-v2", force=True)
    assert tree_bytes(target) == before
    assert (victim / "operator-owned.txt").read_text() == "must remain operator-owned\n"


@pytest.mark.parametrize("name", [
    "/tmp/outside", "../outside", "skills/../../outside", "skills\\outside",
])
def test_generator_marker_rejects_non_allowlisted_or_escaping_paths(tmp_path, name):
    target = tmp_path / "blender-render-private"
    package_plugin(target, "private-stdio", profile="local",
                   executable="/opt/bin/blender-render")
    marker = target / ".blender-render-generator.json"
    document = read(marker)
    document["files"][name] = "0" * 64
    marker.write_text(json.dumps(document) + "\n")
    with pytest.raises(ValueError, match="ownership|allowlist|invalid path"):
        validate_package(target)


def test_generator_marker_rejects_duplicate_json_entries(tmp_path):
    target = tmp_path / "blender-render-private"
    package_plugin(target, "private-stdio", profile="local",
                   executable="/opt/bin/blender-render")
    marker = target / ".blender-render-generator.json"
    document = marker.read_text()
    duplicate = document.replace(
        '"generator": "blender-render-plugin",',
        '"generator": "blender-render-plugin",\n  "generator": "blender-render-plugin",', 1)
    marker.write_text(duplicate)
    with pytest.raises(ValueError, match="duplicate"):
        validate_package(target)


def test_force_returns_final_plugin_inventory_with_preserved_extras(tmp_path):
    target = tmp_path / "blender-render-private"
    package_plugin(target, "private-stdio", profile="local",
                   executable="/opt/bin/blender-render-v1")
    note = target / "operator-notes.txt"
    note.write_text("preserve this operator file\n")
    result = package_plugin(target, "private-stdio", profile="local",
                            executable="/opt/bin/blender-render-v2", force=True)
    assert result == {**validate_package(target), "path": str(target)}
    assert "operator-notes.txt" in result["files"]


@pytest.mark.parametrize(
    "stage",
    ["backup", "install"],
)
@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_force_interruption_restores_previous_package_and_operator_files(
    tmp_path, monkeypatch, stage, interruption_type
):
    interruption = interruption_type("stop during publication")
    target = tmp_path / "blender-render-private"
    package_plugin(
        target,
        "private-stdio",
        profile="local",
        executable="/opt/bin/blender-render-v1",
    )
    (target / "operator-notes.txt").write_text("must survive interruption\n")
    before = tree_bytes(target)
    transaction = package_module._publish_transaction(target)
    real_replace = Path.replace

    def interrupted(source, destination):
        destination = Path(destination)
        if stage == "backup" and source == target and destination == transaction / target.name:
            result = real_replace(source, destination)
            raise interruption
        if stage == "install" and destination == target and source != transaction / target.name:
            result = real_replace(source, destination)
            raise interruption
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", interrupted)
    with pytest.raises(type(interruption)):
        package_plugin(
            target,
            "private-stdio",
            profile="local",
            executable="/opt/bin/blender-render-v2",
            force=True,
        )

    assert tree_bytes(target) == before
    assert not transaction.exists()


def test_next_force_recovers_target_missing_after_interrupted_publication(tmp_path):
    target = tmp_path / "blender-render-private"
    package_plugin(
        target,
        "private-stdio",
        profile="local",
        executable="/opt/bin/blender-render-v1",
    )
    (target / "operator-notes.txt").write_text("recover me\n")
    before = tree_bytes(target)
    transaction = package_module._publish_transaction(target)
    transaction.mkdir()
    target.replace(transaction / target.name)

    with pytest.raises(RuntimeError, match="Recovered.*interrupted"):
        package_plugin(
            target,
            "private-stdio",
            profile="local",
            executable="/opt/bin/blender-render-v2",
            force=True,
        )

    assert tree_bytes(target) == before
    assert not transaction.exists()


def test_stale_publication_with_target_and_backup_refuses_without_deletion(tmp_path):
    target = tmp_path / "blender-render-private"
    package_plugin(
        target,
        "private-stdio",
        profile="local",
        executable="/opt/bin/blender-render-v1",
    )
    before = tree_bytes(target)
    transaction = package_module._publish_transaction(target)
    transaction.mkdir()
    shutil.copytree(target, transaction / target.name)

    with pytest.raises(RuntimeError, match="Both the package and its.*backup"):
        package_plugin(
            target,
            "private-stdio",
            profile="local",
            executable="/opt/bin/blender-render-v2",
            force=True,
        )

    assert tree_bytes(target) == before
    assert tree_bytes(transaction / target.name) == before


def test_failed_force_rollback_retains_exact_recovery_backup(
    tmp_path, monkeypatch
):
    target = tmp_path / "blender-render-private"
    package_plugin(
        target,
        "private-stdio",
        profile="local",
        executable="/opt/bin/blender-render-v1",
    )
    (target / "operator-notes.txt").write_text("retain exactly\n")
    before = tree_bytes(target)
    transaction = package_module._publish_transaction(target)
    previous = transaction / target.name
    real_replace = Path.replace

    def broken_rollback(source, destination):
        destination = Path(destination)
        if destination == target and source == previous:
            raise OSError("injected rollback failure")
        if destination == target and source != previous:
            raise KeyboardInterrupt("injected publish interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", broken_rollback)
    with pytest.raises(RuntimeError, match=str(previous)):
        package_plugin(
            target,
            "private-stdio",
            profile="local",
            executable="/opt/bin/blender-render-v2",
            force=True,
        )

    assert not target.exists()
    assert tree_bytes(previous) == before


def test_force_returns_final_marketplace_inventory_with_preserved_extras(tmp_path):
    target = tmp_path / "private-marketplace"
    package_marketplace(target, "private-http",
                        plugin_name="blender-render-private",
                        url="https://old-render.example.edu/mcp")
    note = target / "operator-notes.txt"
    note.write_text("preserve this operator file\n")
    result = package_marketplace(
        target, "private-http", plugin_name="blender-render-private",
        url="https://new-render.example.edu/mcp", force=True)
    assert result == {**validate_marketplace(target), "path": str(target),
                      "plugin_path": str(target / "plugins/blender-render-private")}
    assert "operator-notes.txt" in result["files"]


def test_private_stdio_requires_explicit_profile_in_library_and_cli(tmp_path):
    with pytest.raises(ValueError, match="explicit.*profile"):
        package_plugin(tmp_path / "library", "private-stdio",
                       executable="/opt/bin/blender-render")
    args = cli.parser().parse_args([
        "plugin", "package", str(tmp_path / "cli"), "--route", "private-stdio",
        "--executable", "/opt/bin/blender-render",
    ])
    with pytest.raises(ValueError, match="explicit.*profile"):
        cli.execute(args)


@pytest.mark.parametrize("executable", [
    "~/bin/blender-render",
    "/opt/$HOME/blender-render",
    "/opt/${HOME}/blender-render",
    "/opt/%HOME%/blender-render",
    "relative/blender-render",
])
def test_private_stdio_rejects_ambient_or_nonabsolute_executable(tmp_path, monkeypatch, executable):
    monkeypatch.setenv("HOME", str(tmp_path / "ambient-home"))
    target = tmp_path / "blender-render-private"
    with pytest.raises(ValueError, match="literal absolute"):
        package_plugin(target, "private-stdio", profile="local", executable=executable)
    assert not target.exists()


def test_private_stdio_is_independent_of_home_user_and_working_directory(tmp_path, monkeypatch):
    first_parent = tmp_path / "one"
    second_parent = tmp_path / "two"
    first_parent.mkdir()
    second_parent.mkdir()
    monkeypatch.chdir(first_parent)
    monkeypatch.setenv("HOME", str(tmp_path / "home-one"))
    monkeypatch.setenv("USER", "first-user")
    first = package_plugin(first_parent / "blender-render", "private-stdio",
                           profile="local", executable="/opt/bin/blender-render")
    monkeypatch.chdir(second_parent)
    monkeypatch.setenv("HOME", str(tmp_path / "home-two"))
    monkeypatch.setenv("USER", "second-user")
    second = package_plugin(second_parent / "blender-render", "private-stdio",
                            profile="local", executable="/opt/bin/blender-render")
    assert {key: value for key, value in first.items() if key != "path"} == {
        key: value for key, value in second.items() if key != "path"
    }


@pytest.mark.parametrize("route, options, expected", [
    ("private-stdio", {"profile": "local", "executable": "/opt/bin/blender-render"},
     "local STDIO adapter"),
    ("private-http", {"url": "https://render.example.edu/mcp"},
     "private HTTPS endpoint"),
    ("private-http", {
        "url": "https://operator--renderer.us-east.modal.direct/mcp",
        "deployment_identity": "personal-render-v1",
     }, "personal Modal endpoint"),
])
def test_private_skill_is_route_correct_and_retains_safety(tmp_path, route, options, expected):
    target = tmp_path / ("blender-render-" + expected.split()[0].lower())
    package_plugin(target, route, **options)
    skill = (target / "skills/render/SKILL.md").read_text()
    folded = skill.casefold()
    for forbidden in (
        "account-restricted private beta", "ten completed renders per utc day",
        "project credit ceiling", "blender render private beta",
    ):
        assert forbidden not in folded
    assert expected in skill
    assert "Call `render_structure` once" in skill
    assert "do not redirect them to shared compute" in skill
    assert "do not" in folded and "rerender" in folded


def test_private_marketplace_skill_is_route_correct(tmp_path):
    target = tmp_path / "marketplace"
    package_marketplace(
        target, "private-http", plugin_name="blender-render-private",
        url="https://render.example.edu/mcp")
    skill = (target / "plugins/blender-render-private/skills/render/SKILL.md").read_text()
    assert "private HTTPS endpoint" in skill
    assert "Blender Render Private Beta" not in skill
    assert "project credit ceiling" not in skill.casefold()


def test_hosted_skill_remains_the_checked_in_d2_skill(tmp_path):
    target = tmp_path / "blender-render"
    package_plugin(target, "hosted")
    assert (target / "skills/render/SKILL.md").read_bytes() == (
        template_root() / "skills/render/SKILL.md").read_bytes()


@pytest.mark.parametrize("route, options, expected", [
    ("hosted", {}, {
        "adapter_type": "registered-http", "input_source": "declared-attachment",
        "input_location": "remote-client-upload",
        "expected_compute": "owner-private-beta-service",
    }),
    ("private-stdio", {"profile": "local", "executable": "/opt/bin/blender-render"}, {
        "adapter_type": "stdio-profile", "input_source": "user-selected-local-path",
        "input_location": "client-host-filesystem", "expected_compute": "local-blender",
    }),
    ("private-http", {"url": "https://render.example.edu/mcp"}, {
        "adapter_type": "http-endpoint", "input_source": "declared-attachment",
        "input_location": "remote-client-upload",
        "expected_compute": "explicit-private-https-service",
    }),
])
def test_routing_metadata_describes_and_matches_adapter(tmp_path, route, options, expected):
    target = tmp_path / ("blender-render-" + route)
    package_plugin(target, route, **options)
    routing = read(target / "routing.json")
    assert {key: routing[key] for key in expected} == expected
    assert validate_package(target)["route"] == route


def test_documented_installation_lifecycle_is_complete_and_bounded():
    document = (Path(__file__).parents[1] / "docs/plugin-installation.md").read_text()
    required_sections = (
        "Install the owner private beta", "Install a generated private plugin",
        "Switch routes", "Disable or remove the shared route",
        "Update and reinstall", "Inspect the active installation",
        "Refresh tools and conversations", "Uninstall and delete generated output",
    )
    for section in required_sections:
        assert f"## {section}" in document
    for command in (
        "codex plugin add", "codex plugin remove", "codex plugin list --json",
        "codex plugin marketplace add", "codex plugin marketplace remove",
        "codex plugin marketplace list --json", "codex mcp list --json",
        "python -m pip show pymol-plugin-bridge",
        "python -m pip uninstall pymol-plugin-bridge", "blender-render --version",
        "blender-render profile list", "python -m json.tool", "/mcp",
    ):
        assert command in document
    for inspection in (
        ".mcp.json", ".app.json", "routing.json", "engine_build", "catalog digest",
        "tool schemas", "fresh conversation",
    ):
        assert inspection in document
    assert "does not establish" in document


def test_d4_acceptance_declares_guarded_measured_and_sdist_evidence():
    root = Path(__file__).parents[1]
    script = (root / "scripts/validate_d4_installation.py").read_text()
    assert "d4_boundary_runner.py" in script
    assert "d4_forbidden_boundary_probe.py" in script
    assert '"boundary_attempts"' in script
    assert '"boundary_guard_self_test"' in script
    assert '"official_plugin_validation"' in script
    assert '"source_distribution"' in script
    assert '"sdist-wheel"' in script
    assert '"git_commit"' in script
    assert '"reviewed_input_inventory"' in script
    assert '"staged_source_inventory"' in script
    assert '"privacy_scan_files"' in script
    assert "_scan_personal_paths(distribution_roots)" in script
    assert "/net/scratch/" not in script and "/home/" not in script
    assert script.count('"c3_boundary_guard"') >= 2


def test_d4_source_identity_rejects_dirty_tracked_or_untracked_inputs():
    root = Path(__file__).parents[1]
    namespace = runpy.run_path(str(root / "scripts/validate_d4_installation.py"))
    validate = namespace["_validate_source_identity"]

    assert validate("a" * 40 + "\n", "") == {
        "git_commit": "a" * 40,
        "build_inputs_clean": True,
    }
    for status in (" M README.md\n", "?? blender_render/new_module.py\n"):
        with pytest.raises(ValueError, match="build inputs must be clean"):
            validate("a" * 40, status)


def test_personal_path_scan_covers_every_shape_in_every_distribution_form(
    tmp_path,
):
    root = Path(__file__).parents[1]
    namespace = runpy.run_path(str(root / "scripts/validate_d4_installation.py"))
    scan = namespace["_scan_personal_paths"]
    separator = chr(92)
    values = {
        "unix.txt": "/" + "home" + "/alice/project",
        "mac.txt": "/" + "Users" + "/bob/project",
        "scratch.txt": "/" + "net" + "/" + "scratch" + "/carol/project",
        "windows.txt": (
            "C:" + separator + "Users" + separator + "dave" + separator + "project"
        ),
    }
    roots = {}
    for form in (
        "staged-source",
        "source-distribution",
        "direct-wheel",
        "sdist-wheel",
        "installed-target",
    ):
        distribution = tmp_path / form / "unselected" / "metadata"
        distribution.mkdir(parents=True)
        roots[form] = tmp_path / form
        for name, value in values.items():
            (distribution / name).write_text(value)
        (distribution / "portable.txt").write_text(
            "/opt/blender-render and relative/path"
        )

    matches = scan(roots)

    assert len(matches) == len(roots) * len(values)
    assert {match.split("/", 1)[0] for match in matches} == set(roots)
    assert {match.rsplit(":", 1)[1] for match in matches} == {
        "unix_home", "mac_home", "network_scratch", "windows_home"
    }
    assert not any("portable.txt" in match for match in matches)


def test_boundary_runner_blocks_the_controlled_process_fixture(tmp_path):
    if "c1_boundary_guard" in sys.modules:
        pytest.skip("Acceptance validator executes this subprocess guard self-test")
    root = Path(__file__).parents[1]
    attempts = tmp_path / "attempts.json"
    result = subprocess.run(
        [sys.executable, str(root / "tests/d4_boundary_runner.py"),
         "--attempts", str(attempts),
         str(root / "tests/d4_forbidden_boundary_probe.py")],
        cwd=root, env={**os.environ, "PYTHONPATH": str(root / "tests")},
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode != 0
    assert read(attempts) == {"attempts": ["subprocess.Popen"]}
    assert "C1 audit blocked real boundary: subprocess.Popen" in result.stderr


def test_acceptance_module_does_not_import_application_at_module_load():
    root = Path(__file__).parents[1]
    before = set(sys.modules)
    runpy.run_path(str(root / "scripts/validate_d4_installation.py"))
    newly_loaded = set(sys.modules) - before
    assert not any(name == "blender_render" or name.startswith("blender_render.")
                   or name == "blender_render_plugin"
                   or name.startswith("blender_render_plugin.")
                   for name in newly_loaded)
