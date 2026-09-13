"""Offline guarded D4 source, sdist, wheel, install, and determinism acceptance.

Build and installation are explicit controller actions. Application contracts
and probes load the audit guard before importing Blender Render. No service,
Blender, PyMOL, SSH, scheduler, provider, OAuth, or network endpoint is used.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ENGINE_BUILD_INPUTS = (
    "setup.py", "pyproject.toml", "README.md", "LICENSE",
    "THIRD_PARTY_NOTICES.md", "LICENSES",
    "scripts/validate_c3_acceptance.py", "scripts/validate_c3_contracts.py",
)
D4_BUILD_INPUTS = (
    "MANIFEST.in",
    "blender_render_plugin",
    "docs/plugin-installation.md",
    "plugins/blender-render",
    "scripts/validate_d4_installation.py",
    "tests/c1_boundary_guard.py",
    "tests/c3_boundary_guard.py",
    "tests/d4_boundary_runner.py",
    "tests/d4_determinism_probe.py",
    "tests/d4_forbidden_boundary_probe.py",
    "tests/d4_generate_validation_packages.py",
    "tests/test_d4_installation.py",
)
ENGINE_EXCLUDED_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "render_outputs",
}
ENGINE_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log", ".sif"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_inventory(root: Path) -> dict[str, str]:
    root = root.resolve()
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError("Validation inputs cannot contain symlinks")
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise ValueError("Validation input escapes its root")
            result[relative.as_posix()] = _sha256(path)
        elif not path.is_dir():
            raise ValueError("Validation inputs must be regular files")
    return dict(sorted(result.items()))


def _engine_inventory(root: Path) -> dict[str, str]:
    root = root.resolve()
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (set(relative.parts) & ENGINE_EXCLUDED_DIRS
                or path.suffix in ENGINE_EXCLUDED_SUFFIXES):
            continue
        if path.is_symlink():
            raise ValueError("Engine package cannot contain symlinks")
        if path.is_file() and relative.as_posix() != "engine-manifest.json":
            result[relative.as_posix()] = _sha256(path)
        elif not path.is_dir() and not path.is_file():
            raise ValueError("Engine package inputs must be regular files")
    return dict(sorted(result.items()))


def _packager_inventory(root: Path) -> dict[str, str]:
    root = root.resolve()
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or relative.parts[0] == "template":
            continue
        if path.is_symlink():
            raise ValueError("D4 packaging module cannot contain symlinks")
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}:
            result[relative.as_posix()] = _sha256(path)
        elif not path.is_dir() and not path.is_file():
            raise ValueError("D4 packaging module inputs must be regular files")
    return dict(sorted(result.items()))


def _copy_inventory(source: Path, destination: Path, inventory: dict[str, str]) -> None:
    for name, digest in inventory.items():
        data = (source / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Source changed while validation staging was copied")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _copy_input(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    else:
        shutil.copy2(source, destination)


def _run(command, cwd: Path, env: dict[str, str], log: Path, *, check: bool = True):
    with log.open("w") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT)
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)
    return result


def _extract_wheel(wheel: Path, destination: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = PurePosixPath(member.filename)
            if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                    or "\\" in member.filename):
                raise ValueError("Invalid wheel member path")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("Duplicate wheel member path")
            target.write_bytes(archive.read(member))


def _extract_sdist(archive_path: Path, destination: Path) -> Path:
    roots = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                    or "\\" in member.name):
                raise ValueError("Invalid source-distribution member path")
            roots.add(relative.parts[0])
            if not (member.isdir() or member.isfile()) or member.issym() or member.islnk():
                raise ValueError("Source distribution contains a non-regular member")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None or target.exists():
                raise ValueError("Invalid or duplicate source-distribution file")
            target.write_bytes(source.read())
    if len(roots) != 1:
        raise ValueError("Source distribution must contain one top-level directory")
    return destination / roots.pop()


def _pytest_attempts(log: Path) -> list[str]:
    prefix = "C1 audit: real process/network boundary attempts = "
    lines = [line.split(prefix, 1)[1] for line in log.read_text().splitlines()
             if prefix in line]
    if len(lines) != 1:
        raise ValueError("Guarded pytest log lacks one boundary audit result")
    value = ast.literal_eval(lines[0])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Guarded pytest boundary result is malformed")
    return value


def _probe_attempts(path: Path) -> list[str]:
    value = json.loads(path.read_text())
    attempts = value.get("attempts") if isinstance(value, dict) else None
    if not isinstance(attempts, list) or not all(isinstance(item, str) for item in attempts):
        raise ValueError("Guarded probe boundary result is malformed")
    return attempts


def _scan_needles(roots: dict[str, Path], needles: tuple[bytes, ...]) -> list[str]:
    matches = []
    for label, root in roots.items():
        for path in sorted(root.resolve().rglob("*")):
            relative = path.relative_to(root.resolve())
            if (set(relative.parts) & ENGINE_EXCLUDED_DIRS
                    or path.suffix in ENGINE_EXCLUDED_SUFFIXES):
                continue
            if path.is_symlink():
                raise ValueError("Privacy scan inputs cannot contain symlinks")
            if path.is_dir():
                continue
            if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Privacy scan inputs must be regular contained files")
            data = path.read_bytes().lower()
            for needle in needles:
                if needle.lower() in data:
                    matches.append(
                        f"{label}/{relative.as_posix()}:{needle.decode(errors='replace')}"
                    )
    return matches


def _validator_paths(plugin_validator: Path | None,
                     skill_validator: Path | None) -> tuple[Path, Path]:
    system_skills = Path(os.environ.get(
        "CODEX_HOME", str(Path.home() / ".codex"))) / "skills" / ".system"
    plugin = plugin_validator or system_skills / "plugin-creator" / "scripts" / "validate_plugin.py"
    skill = skill_validator or system_skills / "skill-creator" / "scripts" / "quick_validate.py"
    if not plugin.is_file() or not skill.is_file():
        raise ValueError("Official plugin and skill validator paths are required")
    return plugin.resolve(), skill.resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plugin-validator", type=Path)
    parser.add_argument("--skill-validator", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("D4 validation output must be outside the checkout")
    output.mkdir(parents=True, exist_ok=False)
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "BLENDER_RENDER_HOME": str(output / "runtime-home"),
        "PYMOL_BRIDGE_SESSION_ROOT": str(output / "sessions"),
        "PYMOL_BRIDGE_TMP_ROOT": str(output / "runs"),
    }

    staging = output / "source"
    staging.mkdir()
    for name in (*ENGINE_BUILD_INPUTS, *D4_BUILD_INPUTS):
        _copy_input(ROOT / name, staging / name)
    source_engine_inventory = _engine_inventory(ROOT / "blender_render")
    _copy_inventory(ROOT / "blender_render", staging / "blender_render",
                    source_engine_inventory)

    build_output = output / "distribution"
    _run([sys.executable, "-m", "build", "--sdist", "--wheel", "--no-isolation",
          "--outdir", str(build_output)],
         staging, environment, output / "build.log")
    wheel = next(build_output.glob("*.whl"))
    sdist = next(build_output.glob("*.tar.gz"))
    sdist_root = _extract_sdist(sdist, output / "source-distribution")
    sdist_wheels = output / "sdist-wheels"
    _run([sys.executable, "-m", "build", "--wheel", "--no-isolation",
          "--outdir", str(sdist_wheels)],
         sdist_root, environment, output / "sdist-wheel-build.log")
    sdist_wheel = next(sdist_wheels.glob("*.whl"))

    extraction = output / "wheel-extraction"
    sdist_extraction = output / "sdist-wheel-extraction"
    _extract_wheel(wheel, extraction)
    _extract_wheel(sdist_wheel, sdist_extraction)
    site = output / "site"
    _run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
          "--target", str(site), str(wheel)],
         output, environment, output / "install.log")

    tests = output / "tests"
    shutil.copytree(ROOT / "tests", tests,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (output / "docs").mkdir()
    shutil.copy2(ROOT / "docs" / "plugin-installation.md",
                 output / "docs" / "plugin-installation.md")
    installed_scripts = output / "scripts"
    installed_scripts.mkdir()
    for name in ("validate_c3_acceptance.py", "validate_d4_installation.py"):
        shutil.copy2(ROOT / "scripts" / name, installed_scripts / name)

    installed_environment = {
        **environment,
        "PYTHONPATH": os.pathsep.join([str(site), str(tests)]),
        "C3_INSTALLED_SITE": str(site),
    }
    installed_d4_log = output / "installed-d4.log"
    _run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
          "-p", "c3_boundary_guard", str(tests / "test_d4_installation.py")],
         output, installed_environment, installed_d4_log)
    contract_tree = ast.parse((ROOT / "scripts" / "validate_c3_contracts.py").read_text())
    contract_names = None
    for statement in contract_tree.body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "CONTRACTS"
                for target in statement.targets):
            contract_names = ast.literal_eval(statement.value)
            break
    if not isinstance(contract_names, list):
        raise ValueError("Could not read installed legacy contract inventory")
    installed_contracts_log = output / "installed-contracts.log"
    _run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
          "-p", "c3_boundary_guard",
          *(str(tests / name) for name in contract_names)],
         output, installed_environment, installed_contracts_log)
    boundary_attempts = {
        "installed_d4": _pytest_attempts(installed_d4_log),
        "installed_contracts": _pytest_attempts(installed_contracts_log),
    }
    if any(boundary_attempts.values()):
        raise RuntimeError("Installed tests attempted a prohibited boundary")

    negative_attempts_path = output / "boundary-self-test-attempts.json"
    negative = _run(
        [sys.executable, str(tests / "d4_boundary_runner.py"),
         "--attempts", str(negative_attempts_path),
         str(tests / "d4_forbidden_boundary_probe.py")],
        output, installed_environment, output / "boundary-self-test.log", check=False)
    negative_attempts = _probe_attempts(negative_attempts_path)
    if negative.returncode == 0 or negative_attempts != ["subprocess.Popen"]:
        raise RuntimeError("Controlled boundary probe was not blocked and measured")

    engine_document = json.loads(
        (extraction / "blender_render" / "engine-manifest.json").read_text())
    engine_manifest = engine_document["files"]
    engine_forms = {
        "source": _engine_inventory(ROOT / "blender_render"),
        "staging": _engine_inventory(staging / "blender_render"),
        "source_distribution": _engine_inventory(sdist_root / "blender_render"),
        "wheel_extraction": _engine_inventory(extraction / "blender_render"),
        "sdist-wheel": _engine_inventory(sdist_extraction / "blender_render"),
        "install": _engine_inventory(site / "blender_render"),
    }
    if any(value != engine_manifest for value in engine_forms.values()):
        raise ValueError("Renderer package forms differ")

    source_plugin = ROOT / "plugins" / "blender-render"
    reviewed = _tree_inventory(source_plugin)
    template_forms = {
        "source": reviewed,
        "staging": _tree_inventory(staging / "plugins" / "blender-render"),
        "source_distribution": _tree_inventory(sdist_root / "plugins" / "blender-render"),
        "wheel_extraction": _tree_inventory(extraction / "blender_render_plugin" / "template"),
        "sdist-wheel": _tree_inventory(sdist_extraction / "blender_render_plugin" / "template"),
        "install": _tree_inventory(site / "blender_render_plugin" / "template"),
    }
    if any(value != reviewed for value in template_forms.values()):
        raise ValueError("Reviewed plugin template forms differ")
    packager_forms = {
        "source": _packager_inventory(ROOT / "blender_render_plugin"),
        "staging": _packager_inventory(staging / "blender_render_plugin"),
        "source_distribution": _packager_inventory(sdist_root / "blender_render_plugin"),
        "wheel_extraction": _packager_inventory(extraction / "blender_render_plugin"),
        "sdist-wheel": _packager_inventory(sdist_extraction / "blender_render_plugin"),
        "install": _packager_inventory(site / "blender_render_plugin"),
    }
    if any(value != packager_forms["source"] for value in packager_forms.values()):
        raise ValueError("D4 packaging module forms differ")

    direct_payload = {
        "blender_render": _tree_inventory(extraction / "blender_render"),
        "blender_render_plugin": _tree_inventory(extraction / "blender_render_plugin"),
    }
    sdist_payload = {
        "blender_render": _tree_inventory(sdist_extraction / "blender_render"),
        "blender_render_plugin": _tree_inventory(sdist_extraction / "blender_render_plugin"),
    }
    if direct_payload != sdist_payload:
        raise ValueError("Source-distribution wheel payload differs from direct wheel")

    probe_results = []
    for label, package_root, cwd, seed, user in (
            ("source", ROOT, ROOT, "11", "source-user"),
            ("staging", staging, output, "37", "staging-user"),
            ("extraction", extraction, extraction, "59", "extraction-user"),
            ("install", site, output, "83", "install-user")):
        home = output / ("home-" + label)
        home.mkdir()
        probe_environment = {
            **environment,
            "HOME": str(home),
            "USER": user,
            "PYTHONPATH": os.pathsep.join([str(tests), str(package_root)]),
            "PYTHONHASHSEED": seed,
        }
        attempts_path = output / (label + "-determinism-attempts.json")
        log = output / (label + "-determinism.json")
        _run([sys.executable, str(tests / "d4_boundary_runner.py"),
              "--attempts", str(attempts_path),
              str(tests / "d4_determinism_probe.py")],
             cwd, probe_environment, log)
        attempts = _probe_attempts(attempts_path)
        boundary_attempts[label + "_determinism"] = attempts
        if attempts:
            raise RuntimeError("Determinism probe attempted a prohibited boundary")
        probe_results.append(json.loads(log.read_text()))
    if any(value != probe_results[0] for value in probe_results[1:]):
        raise ValueError("D4 generation differs by package form, HOME, user, cwd, or hash seed")

    generated = output / "generated"
    generated_attempts_path = output / "generated-attempts.json"
    _run([sys.executable, str(tests / "d4_boundary_runner.py"),
          "--attempts", str(generated_attempts_path),
          str(tests / "d4_generate_validation_packages.py"), str(generated)],
         output, installed_environment, output / "generated.json")
    generated_attempts = _probe_attempts(generated_attempts_path)
    boundary_attempts["generated_packages"] = generated_attempts
    if generated_attempts:
        raise RuntimeError("Generated-package probe attempted a prohibited boundary")
    generated_result = json.loads((output / "generated.json").read_text())

    private_roots = {
        name: generated / name
        for name in ("private-stdio", "private-http", "private-bearer", "personal-modal")
    }
    private_roots["marketplace"] = (
        generated / "marketplace" / "plugins" / "blender-render-local")
    hosted_mcp = json.loads((source_plugin / ".mcp.json").read_text())
    hosted_app = json.loads((source_plugin / ".app.json").read_text())
    owner_needles = (
        hosted_mcp["mcpServers"]["blender-render"]["url"].encode(),
        hosted_app["apps"]["blender-render"]["id"].encode(),
        b"almostscheidplatz", b"magnus bauer", b"magnusbauer",
        b"account-restricted private beta", b"project credit ceiling",
    )
    package_form_roots = {
        "source-renderer": ROOT / "blender_render",
        "source-packager": ROOT / "blender_render_plugin",
        "staging-renderer": staging / "blender_render",
        "staging-packager": staging / "blender_render_plugin",
        "sdist-renderer": sdist_root / "blender_render",
        "sdist-packager": sdist_root / "blender_render_plugin",
        "wheel-renderer": extraction / "blender_render",
        "wheel-packager": extraction / "blender_render_plugin",
        "sdist-wheel-renderer": sdist_extraction / "blender_render",
        "sdist-wheel-packager": sdist_extraction / "blender_render_plugin",
        "install-renderer": site / "blender_render",
        "install-packager": site / "blender_render_plugin",
    }
    privacy_scans = {
        "private_owner_identifier_matches": _scan_needles(private_roots, owner_needles),
        "private_credential_value_matches": _scan_needles(
            private_roots,
            (b"user:secret@", b"token=secret", b"secret-from-environment",
             b"do-not-read-or-copy"),
        ),
        "packaged_personal_path_matches": _scan_needles(
            package_form_roots, (b"/net/scratch/magnusb", b"/home/magnusb")),
        "generated_variant_leaks_in_package_forms": _scan_needles(
            package_form_roots,
            (b"render.example.edu", b"BLENDER_RENDER_PRIVATE_TOKEN",
             b"/opt/blender-render/bin/blender-render", b"personal-render-v1"),
        ),
    }
    if any(privacy_scans.values()):
        raise ValueError("Privacy, credential, owner, or private-path scan failed")

    plugin_validator, skill_validator = _validator_paths(
        args.plugin_validator, args.skill_validator)
    validation_targets = [
        ("source", source_plugin),
        ("hosted", generated / "hosted"),
        ("private_stdio", generated / "private-stdio"),
        ("private_http", generated / "private-http"),
        ("private_bearer", generated / "private-bearer"),
        ("personal_modal", generated / "personal-modal"),
        ("marketplace", generated / "marketplace" / "plugins" / "blender-render-local"),
    ]
    official_plugins = []
    official_skills = []
    for label, plugin in validation_targets:
        _run([sys.executable, str(plugin_validator), str(plugin)],
             output, environment, output / f"official-plugin-{label}.log")
        official_plugins.append(label)
        for skill_name in ("render", "develop"):
            _run([sys.executable, str(skill_validator), str(plugin / "skills" / skill_name)],
                 output, environment,
                 output / f"official-skill-{label}-{skill_name}.log")
            official_skills.append(f"{label}/{skill_name}")

    generated_variants = {}
    for label, result in generated_result.items():
        if label == "marketplace":
            generated_variants[label] = {
                "route": result["plugin"]["route"],
                "files": sorted(result["files"]),
                "digest": result["marketplace_sha256"],
                "plugin_digest": result["plugin"]["package_sha256"],
            }
        else:
            generated_variants[label] = {
                "route": result["route"],
                "files": sorted(result["files"]),
                "digest": result["package_sha256"],
            }

    evidence = {
        "wheel": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "source_distribution": sdist.name,
        "source_distribution_sha256": _sha256(sdist),
        "sdist_wheel": sdist_wheel.name,
        "sdist_wheel_sha256": _sha256(sdist_wheel),
        "engine_build": engine_document["engine_build"],
        "engine_package_files": len(engine_manifest),
        "plugin_template_files": len(reviewed),
        "packager_files": len(packager_forms["source"]),
        "source_distribution_files": len(_tree_inventory(sdist_root)),
        "direct_wheel_files": len(_tree_inventory(extraction)),
        "sdist_wheel_files": len(_tree_inventory(sdist_extraction)),
        "installed_files": len(_tree_inventory(site)),
        "package_forms_compared": list(engine_forms),
        "source_distribution_to_wheel": {
            "payloads_equal": True,
            "renderer_files": len(direct_payload["blender_render"]),
            "packager_and_template_files": len(direct_payload["blender_render_plugin"]),
        },
        "determinism_forms": ["source", "staging", "extraction", "install"],
        "determinism_digest": hashlib.sha256(json.dumps(
            probe_results[0], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest(),
        "generated_variants": generated_variants,
        "privacy_scans": privacy_scans,
        "boundary_attempts": boundary_attempts,
        "boundary_guard_self_test": {
            "blocked": True,
            "attempts": negative_attempts,
        },
        "official_plugin_validation": official_plugins,
        "official_skill_validation": official_skills,
        "installed_d4_log": installed_d4_log.name,
        "installed_contracts_log": installed_contracts_log.name,
        "network_dependencies_fetched": False,
        "live_hosts_exercised": [],
    }
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
