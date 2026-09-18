"""Provider-injected contracts usable without importing or contacting Modal."""
from __future__ import annotations
import json
import os
import shlex
from contextlib import contextmanager
from pathlib import Path
import tempfile


def animation_enabled(*, personal, public, owner, allowed_users):
    """Opt in a single authenticated owner, never an unrestricted shared worker."""
    return personal or (public and bool(owner) and owner.isdigit() and owner == allowed_users)


@contextmanager
def package_snapshot(root):
    """One verified inventory for CLI staging and deferred provider image uploads."""
    from .engine import EngineSeal, process_seal
    current = process_seal()
    current.check()
    root = Path(os.path.abspath(root))
    seal = current if root in current.aliases or root.resolve() == current.root else EngineSeal(root)
    with tempfile.TemporaryDirectory(prefix='blender-image-source-') as directory:
        captured = seal.capture(Path(directory)/'blender_render')
        current.check()
        yield captured


def convert_file(data, suffix, *, selection='(all)', create_sandbox):
    if suffix not in {'.pse', '.pdb', '.cif', '.mmcif'} or len(data) > 50*1024**2:
        raise ValueError('Invalid molecular upload')
    if not isinstance(selection, str) or not selection.strip() or '\x00' in selection:
        raise ValueError('Invalid selection')
    from .engine import protect
    protect('conversion')
    sandbox = create_sandbox()
    try:
        sandbox.filesystem.write_bytes(data, '/tmp/input' + suffix)
        command = ['/usr/bin/python3', '/opt/export.py', '/tmp/input'+suffix,
                   '/tmp/payload.json', '--selection', selection]
        process = sandbox.exec('/bin/sh', '-c', shlex.join(command) + ' >/dev/null 2>&1')
        process.wait()
        if process.returncode:
            raise ValueError('The molecular file could not be converted')
        reader = sandbox.exec('/bin/sh', '-c', '/usr/bin/head -c 52428801 /tmp/payload.json > /tmp/bounded.json 2>/dev/null')
        reader.wait()
        payload = sandbox.filesystem.read_bytes('/tmp/bounded.json')
        if reader.returncode or len(payload) > 50*1024**2:
            raise ValueError('Converted structure is too large')
        return json.loads(payload)
    finally:
        sandbox.terminate()
