"""Install the pinned runtime into user-owned storage, never into Blender.app."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from .config import config_root, runtime_versions


def fetch_verified(url, destination, sha256):
    destination = Path(destination)
    curl = "/usr/bin/curl" if sys.platform == "darwin" else "curl"
    subprocess.run([curl, "--fail", "--location", "--silent", "--show-error", "--proto", "=https", url, "--output", str(destination)], check=True)
    digest = hashlib.file_digest(destination.open("rb"), "sha256").hexdigest()
    if digest != sha256:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Runtime download checksum mismatch")


def install(blender="", root=None):
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Runtime setup requires Python 3.11 to match Blender's Python ABI.")
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Install uv explicitly before runtime setup; no global pip fallback is used.')
    root = Path(root) if root else config_root() / "runtime"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = runtime_versions()
    if not blender:
        target = ("windows-x64" if sys.platform == "win32" and platform.machine().lower() in {'amd64', 'x86_64'} else
                  "macos-arm64" if sys.platform == "darwin" and platform.machine() == "arm64" else
                  "linux-x64" if sys.platform == "linux" and platform.machine() == "x86_64" else "")
        if not target:
            raise RuntimeError("Managed runtime supports Windows x64, Apple Silicon Mac and Linux x86_64.")
        artifact = lock["downloads"][target]
        with tempfile.TemporaryDirectory(prefix="blender-install-") as temporary:
            archive = Path(temporary) / artifact["filename"]
            fetch_verified(artifact["url"], archive, artifact["sha256"])
            if target == "macos-arm64":
                mounted = plistlib.loads(subprocess.check_output(["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(archive)]))
                mount = next(item["mount-point"] for item in mounted["system-entities"] if "mount-point" in item)
                try:
                    destination = root / "Blender.app"
                    if destination.exists():
                        raise RuntimeError("Managed Blender.app already exists; inspect it before reinstalling.")
                    shutil.copytree(Path(mount) / "Blender.app", destination, symlinks=True)
                finally:
                    subprocess.run(["hdiutil", "detach", mount], check=True)
                blender = str(destination / "Contents/MacOS/Blender")
            elif target == 'windows-x64':
                with zipfile.ZipFile(archive) as source:
                    for member in source.infolist():
                        if not (root/member.filename).resolve().is_relative_to(root.resolve()):
                            raise ValueError('Invalid Blender archive path')
                    source.extractall(root)
                blender = str(root / 'blender-5.0.1-windows-x64/blender.exe')
            else:
                with tarfile.open(archive) as source:
                    source.extractall(root, filter="data")
                blender = str(root / "blender-5.0.1-linux-x64/blender")
    site = root / "site"
    # Lock every wheel, including transitive dependencies. bpy is supplied by
    # Blender; never download a separate bpy wheel into the rendering runtime.
    subprocess.run([uv, 'pip', 'install', '--python', sys.executable, "--no-deps", "--require-hashes", "--only-binary=:all:", "--target", str(site), "-r", str(Path(__file__).with_name("runtime-requirements.txt"))], check=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site)
    env["MOL_BLENDER_CCD_SHA256"] = lock["ccd_sha256"]
    env['MPLCONFIGDIR'] = str(root/'matplotlib')
    subprocess.run([blender, "--background", "--python-use-system-env", "--python-exit-code", "1", "--python", str(Path(__file__).with_name("runtime_initialize.py"))], env=env, check=True)
    return {"blender": blender, "runtime_site": str(site)}
