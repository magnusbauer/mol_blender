# SPDX-FileCopyrightText: 2026 Magnus Bauer
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build-time verification and CCD provisioning, run by Blender."""
import hashlib
import json
import os
from pathlib import Path

import bpy
import biotite
from biotite.structure.info import ccd
import molecularnodes
import numpy
import stat
import importlib.metadata
import sys

if bpy.app.version[:3] != (5, 0, 1) or numpy.__version__ != "1.26.4" or biotite.__version__ != "1.6.0":
    raise RuntimeError("Runtime version drift")
if sys.version_info[:2] != (3, 11):
    raise RuntimeError('Runtime Python must be 3.11')
try:
    mn_version = importlib.metadata.version('molecularnodes')
except importlib.metadata.PackageNotFoundError:
    import tomllib
    mn_version = tomllib.loads(Path(molecularnodes.__file__).with_name('blender_manifest.toml').read_text())['version']
if mn_version != '4.5.10':
    raise RuntimeError('Molecular Nodes must be 4.5.10')
path = Path(os.environ.get('PRESS_BIOTITE_CCD_PATH') or ccd._CCD_FILE).expanduser()
if not path.exists():
    if os.environ.get('PRESS_BIOTITE_CCD_PATH'):
        raise RuntimeError('Provision the explicitly selected CCD file before setup verification')
    import biotite.setup_ccd
    biotite.setup_ccd.main()
with path.open("rb") as source:
    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
        raise RuntimeError('CCD must be a regular file')
    digest = hashlib.file_digest(source, "sha256").hexdigest()
expected = json.loads(Path(__file__).with_name("runtime.lock.json").read_text())["ccd_sha256"]
if digest != expected:
    raise RuntimeError("CCD snapshot changed; update the runtime lock only after ligand fidelity validation.")
print("CCD verified", digest)
