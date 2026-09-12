"""Deterministic installed identity and one source/runtime observation per process.

This module deliberately uses only the standard library: packaging and the
login-node compatibility helper must not import renderer dependencies.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import importlib
import importlib.abc
import importlib.util
import importlib.machinery
import json
import os
import stat
import re
import threading
from functools import lru_cache
from pathlib import Path
import sys

CAPABILITIES = sorted({
    'representation.cartoon', 'representation.ribbon', 'representation.surface',
    'representation.sticks', 'representation.spheres', 'representation.wire',
    'representation.surface_smoothing', 'representation.wire_mesh',
    'representation.nucleic_base_thickness', 'material.clay', 'material.dried_paint',
    'material.frosted_grid', 'material.color_gradient', 'material.attribute_color',
    'material.fog_glow', 'material.edge_highlight', 'material.uniform_polymer_color',
    'geometry.edge_rounding', 'geometry.element_palette', 'world.directional_gradient',
    'world.separate_illumination', 'camera.depth_of_field', 'camera.reference_framing',
})


def _exception_diagnostic(value):
    # This file also runs as a standalone stdlib verifier before a remote package
    # is trusted. Never import package code while reporting an integrity failure.
    module = sys.modules.get('blender_render.compatibility')
    if module is not None:
        return module.sanitize(value)
    return {'version':1, 'reason':'unknown_compatibility', 'phase':'unknown',
            'component':'unknown', 'before_execution':False, 'expected':{}, 'observed':{}, 'log_refs':[]}


class EngineMismatch(ValueError):
    """A known compatibility failure; never a reason to launch fallback work."""
    def __init__(self, *args, diagnostic=None):
        super().__init__(*args)
        self.diagnostic = _exception_diagnostic(diagnostic)


class RestartRequired(EngineMismatch):
    code = 'restart_required'


class ExecutionUnknown(RuntimeError):
    code = 'execution_unknown'

    def __init__(self, *args, diagnostic=None):
        super().__init__(*args)
        self.diagnostic = _exception_diagnostic(diagnostic)


class RuntimeUnavailable(EngineMismatch, RuntimeError):
    """A worker explicitly refused execution before invoking the renderer."""
    before_execution = True


# Runtime inventory excludes only generated outputs; external container images
# are launcher configuration, never packaged engine inputs. See docs/c3-execution.md.
EXCLUDED_DIRS = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'render_outputs'}
EXCLUDED_SUFFIXES = {'.pyc', '.pyo', '.tmp', '.log', '.sif'}
BUILD_INPUTS = ('setup.py', 'pyproject.toml', 'README.md', 'LICENSE',
                'THIRD_PARTY_NOTICES.md', 'LICENSES',
                'scripts/validate_c3_acceptance.py', 'scripts/validate_c3_contracts.py')


def excluded(path):
    return bool(set(path.parts) & EXCLUDED_DIRS) or path.suffix in EXCLUDED_SUFFIXES


def package_inventory(root):
    """Normalized recursive file inventory shared by build and runtime checks."""
    root = Path(root).resolve()
    result = {}
    def walk(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if excluded(relative):
                continue
            if path.is_symlink():
                raise ValueError('Included package symlink is forbidden: ' + relative.as_posix())
            if path.is_dir():
                walk(path)
            elif path.is_file() and relative.as_posix() != 'engine-manifest.json':
                name = relative.as_posix()
                if '\\' in name or name in result or not path.resolve().is_relative_to(root):
                    raise ValueError('Invalid or colliding package-relative path')
                result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif not path.is_file():
                raise ValueError('Package input must be a regular file')
    walk(root)
    return dict(sorted(result.items()))


def stage_package(source, destination):
    """Copy only verified inventory bytes, preserving relative directories."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.is_relative_to(source):
        raise ValueError('Package staging must be outside the source package')
    inventory = package_inventory(source)
    for name, digest in inventory.items():
        target = destination/name
        if not target.resolve().is_relative_to(destination) or target.is_symlink():
            raise ValueError('Staging path escapes or aliases its package root')
        data = (source/name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Package changed during build staging')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod((source/name).stat().st_mode & 0o777)
    return inventory


def generate_manifest(root: Path) -> dict:
    files = package_inventory(root)
    return {'manifest_version': 1, 'protocol_version': 1,
            'bundle_versions': ['1.0'], 'schema_versions': ['1.0'],
            'engine_build': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            'capabilities': CAPABILITIES,
            'runtime_requirements': json.loads((root / 'runtime.lock.json').read_text()),
            'files': files}


class EngineSeal:
    """One process seal. A failed verification cannot be cleared in place."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.aliases = {Path(os.path.abspath(root)), self.root}
        self.latched = False
        self.lock = threading.RLock()
        try:
            self.identity = generate_manifest(self.root)
            manifest = self.root / 'engine-manifest.json'
            if manifest.is_symlink():
                raise ValueError('Manifest symlink')
            self.manifest_bytes = manifest.read_bytes() if manifest.exists() else None
            if self.manifest_bytes is not None and json.loads(self.manifest_bytes) != self.identity:
                raise ValueError('Stale installed manifest')
            self.inputs = {name: (self.root/name).read_bytes() for name in self.identity['files']}
            self.resources = {name:data for name,data in self.inputs.items()
                              if not name.endswith(('.py', '.so', '.pyd', '.dylib'))}
            if any(hashlib.sha256(data).hexdigest() != self.identity['files'][name] for name, data in self.inputs.items()):
                raise ValueError('Input changed during capture')
            self.check()
        except (OSError, ValueError) as exc:
            self.latched = True
            raise RestartRequired('restart_required: package verification failed') from exc

    def capture(self, destination):
        """Private action-owned package, constructed only from startup bytes.

        The owner retains this directory until its child exits. Never overwrite
        a capture: a replacement process gets a new directory and seal.
        """
        self.check()
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError('Capture destination must be new')
        destination.mkdir(mode=0o700, parents=True)
        for name, data in self.inputs.items():
            target = destination/name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o400)
        (destination/'engine-manifest.json').write_text(json.dumps(self.identity, sort_keys=True))
        (destination/'engine-manifest.json').chmod(0o400)
        self.check()
        return destination

    def check(self):
        with self.lock:
            if self.latched:
                raise RestartRequired('restart_required: restart the affected process')
            try:
                if any(alias.resolve() != self.root for alias in self.aliases):
                    raise ValueError('Package root alias changed')
                manifest = self.root/'engine-manifest.json'
                if manifest.is_symlink() or generate_manifest(self.root) != self.identity:
                    raise ValueError('Package changed')
                if (manifest.read_bytes() if manifest.exists() else None) != self.manifest_bytes:
                    raise ValueError('Manifest changed')
            except (OSError, ValueError) as exc:
                self.latched = True
                raise RestartRequired('restart_required: package changed since process startup') from exc


@lru_cache(maxsize=1)
def process_seal():
    return EngineSeal(Path(__file__).parent)


def _identity():
    return process_seal().identity


def engine_identity() -> dict:
    return copy.deepcopy(_identity())


def resource_bytes(path):
    """Read a captured package input; external request files remain caller data."""
    seal = process_seal()
    # Classify lexical package identity before following mutable symlinks.
    path = Path(os.path.abspath(path))
    for root in sorted(seal.aliases, key=lambda root:len(root.parts), reverse=True):
        if path.is_relative_to(root):
            return seal.resources[path.relative_to(root).as_posix()]
    return path.read_bytes()


def script_code(name):
    """A standalone stdlib helper passed by value to Python, never reopened."""
    seal = process_seal()
    seal.check()
    return compile_code(seal.inputs[name], name)


def compile_code(source, name):
    return 'exec(compile(' + repr(source) + ', ' + repr(name) + ', "exec"))'


def remote_bootstrap(package_root, *, check=True, administrative=False):
    """Verify/capture the remote installation before executing its helper.

    The verifier itself is sent by value from this controller's seal. Remote
    package code is not imported until its installed manifest has been checked.
    Read/attach actions may use a different sealed build; start negotiates it.
    """
    seal = process_seal()
    if check: seal.check()
    if administrative or not check:
        # Status/stop must survive damage to an unused remote installation.
        # Attachment likewise needs only registry/control and retrieval protocol.
        # This helper is stdlib-only on these routes; it never imports that tree.
        return '__file__="/sealed/hpc_remote.py"; ' + compile_code(seal.inputs['hpc_remote.py'], 'sealed_hpc_remote.py')
    return ('import tempfile,sys,runpy; from pathlib import Path; '
            'verifier={"__name__":"sealed_verifier"}; exec(compile(' +
            repr(seal.inputs['engine.py']) + ', "sealed_verifier", "exec"), verifier); '
            'sealed=verifier["EngineSeal"](Path(' + repr(str(package_root)) + ')); '
            'temporary=tempfile.TemporaryDirectory(prefix="blender-remote-helper-"); '
            'captured=sealed.capture(Path(temporary.name)/"blender_render"); '
            'sys.path.insert(0,str(captured.parent)); '
            'runpy.run_path(str(captured/"hpc_remote.py"),run_name="__main__"); temporary.cleanup()')


# Reviewed package-module closures; provider and Blender modules are deliberately
# absent from controller actions. Dependencies may be imported transitively here.
ACTION_MODULES = {
    'admission': ('core', 'presets', 'spec', 'selection', 'consumer', 'consumer_records', 'consumer_errors', 'consumer_transport', 'execution', 'export', 'jobs', 'artifacts', 'telemetry',
                  'gateway', 'delivery', 'operation_progress'),
    'conversion': ('export', 'execution', 'spec', 'telemetry', 'modal_boundaries'),
    'startup': ('core', 'config', 'render_worker_manager', 'telemetry'),
    'dispatch': ('render_server', 'rendering', 'execution', 'spec', 'telemetry', 'operation_progress'),
    'submission': ('client', 'hpc', 'runtime', 'execution', 'telemetry'),
    'transfer': ('client', 'hpc', 'runtime', 'delivery', 'consumer_records', 'consumer_errors', 'artifacts', 'telemetry'),
    'render': ('core', 'bridge_runner', 'chain_palette', 'ligand_selection', 'cutout_planes',
               'geometry', 'paint', 'reference_effects', 'operation_progress', 'telemetry', 'worker_ccd'),
}
ACTION_MODULES = {action: ('compatibility', *modules) for action, modules in ACTION_MODULES.items()}


class _SealedLoader(importlib.abc.Loader):
    def __init__(self, seal, name):
        self.seal, self.name = seal, name

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        self.seal.check()
        module.__file__ = str(self.seal.root/self.name)
        exec(compile(self.seal.inputs[self.name], module.__file__, 'exec'), module.__dict__)


class _SealedFinder(importlib.abc.MetaPathFinder):
    """Only action preloads use this finder; no provider imports are invented."""
    def __init__(self, seal):
        self.seal = seal

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith('blender_render.'):
            return None
        self.seal.check()
        relative = fullname.removeprefix('blender_render.').replace('.', '/')
        for name, package in ((relative+'.py', False), (relative+'/__init__.py', True)):
            if name in self.seal.inputs:
                return importlib.util.spec_from_loader(fullname, _SealedLoader(self.seal, name), is_package=package)
        for suffix in importlib.machinery.EXTENSION_SUFFIXES:
            if relative+suffix in self.seal.inputs:
                # Native imports also use captured files, retained for the
                # process lifetime; never dlopen the mutable installation.
                if not hasattr(self.seal, '_native_capture'):
                    import tempfile
                    self.seal._native_capture = tempfile.TemporaryDirectory(prefix='blender-native-')
                    self.seal._native_package = self.seal.capture(Path(self.seal._native_capture.name)/'blender_render')
                return importlib.util.spec_from_file_location(fullname, self.seal._native_package/(relative+suffix))
        raise RestartRequired('restart_required: action dependency absent from inventory')


def protect(action):
    seal = process_seal()
    finder = _SealedFinder(seal)
    try:
        with seal.lock:
            seal.check()
            sys.meta_path.insert(0, finder)
            for name in ACTION_MODULES[action]:
                module = importlib.import_module('blender_render.' + name)
                path = Path(module.__file__).resolve()
                if not path.is_relative_to(seal.root) and not path.is_relative_to(getattr(seal, '_native_package', seal.root)):
                    seal.latched = True
                    raise RestartRequired('restart_required: action module outside sealed package')
            seal.check()
    except RestartRequired as exc:
        exc.before_execution = True  # This protected action has not begun.
        raise
    finally:
        if finder in sys.meta_path: sys.meta_path.remove(finder)
    return seal


def ccd_observation(*, path=None, expected=None):
    """Worker-only filesystem verification. Never repairs or creates CCD data."""
    expected = expected or _identity()['runtime_requirements']['ccd_sha256']
    digest = None
    try:
        if path is None:
            path = os.environ.get('PRESS_BIOTITE_CCD_PATH')
            if not path:
                from biotite.structure.info import ccd
                path = ccd._CCD_FILE
        path = Path(path).expanduser()
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('CCD is not a regular file')
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
    except (OSError, ValueError, TypeError, ImportError, AttributeError):
        pass
    return {'path': str(path) if path is not None else None, 'sha256': digest,
            'expected_sha256': expected, 'verified': digest == expected}


def require_compatible(expected, observed, requirements):
    from .compatibility import make
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        raise EngineMismatch('Engine identity unavailable; explicit runtime setup required',
            diagnostic=make(phase='negotiation', component='engine', before_execution=True))
    for key in ('manifest_version', 'protocol_version', 'bundle_versions', 'schema_versions', 'engine_build', 'runtime_requirements'):
        if key not in expected or expected[key] != observed.get(key):
            reason = ('engine_build_mismatch' if key == 'engine_build' else
                      'engine_schema_mismatch' if key in {'bundle_versions','schema_versions'} else
                      'runtime_version_mismatch' if key == 'runtime_requirements' else 'engine_protocol_mismatch')
            evidence_key = 'versions' if key == 'runtime_requirements' else key
            def evidence(value):
                if value is None: return {}
                if key == 'runtime_requirements':
                    value = {k:v for k,v in value.items() if k in {'blender','python','molecularnodes','numpy','biotite'}} if isinstance(value, dict) else value
                return {evidence_key:value}
            raise EngineMismatch('Incompatible engine ' + key + '; install the exact controller build',
                diagnostic=make(reason, phase='negotiation', component='engine', before_execution=True,
                                expected=evidence(expected.get(key)), observed=evidence(observed.get(key))))
    missing = set(requirements) - set(observed.get('capabilities', []))
    if missing:
        raise EngineMismatch('Unsupported engine capabilities: ' + ', '.join(sorted(missing)),
            diagnostic=make('capability_missing', phase='negotiation', component='engine', before_execution=True,
                            expected={'capabilities':sorted(requirements)}, observed={'capabilities':sorted(observed.get('capabilities', []))}))


def require_retrieval(observation):
    # C3 protocol 1 already guarantees the C2 explicit-ack result route. The
    # result itself must still supply and validate that delivery policy.
    declaration = observation.get('controller_declaration', {})
    if declaration.get('protocol_version') != 1:
        from .compatibility import make
        raise EngineMismatch('Unsupported artifact retrieval protocol', diagnostic=make('engine_protocol_mismatch',
            phase='negotiation', component='engine', expected={'protocol_version':1},
            observed={'protocol_version':declaration['protocol_version']} if 'protocol_version' in declaration else {}))


def sanitized_observation(value):
    """Public authenticated/cache diagnostics, never private paths or free text."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ('controller_declaration', 'engine'):
        source = value.get(key)
        if isinstance(source, dict):
            declaration = {}
            digest = source.get('engine_build')
            if isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest):
                declaration['engine_build'] = digest
            for field in ('manifest_version', 'protocol_version'):
                if type(source.get(field)) is int:
                    declaration[field] = source[field]
            result[key] = declaration
    for key in ('worker_observation', 'runtime'):
        if isinstance(value.get(key), dict):
            result[key] = sanitized_observation(value[key])
    for key in ('verified',):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    if isinstance(value.get('ccd'), dict):
        ccd = value['ccd']
        result['ccd'] = {'verified': ccd.get('verified') is True}
        for key in ('sha256', 'expected_sha256'):
            if isinstance(ccd.get(key), str) and re.fullmatch('[a-f0-9]{64}', ccd[key]):
                result['ccd'][key] = ccd[key]
    if isinstance(value.get('versions'), dict):
        result['versions'] = {key:version for key, version in value['versions'].items()
                              if key in {'blender','python','molecularnodes','numpy','biotite'}
                              and isinstance(version, str) and re.fullmatch(r'\d+(?:\.\d+){1,3}', version)}
    return result


def _git_paths(root, arguments):
    import subprocess
    output = subprocess.check_output(['git', '-C', str(root), *arguments, '--', 'blender_render', *BUILD_INPUTS], timeout=10)
    return [path.decode('utf-8', errors='strict') for path in output.split(b'\0') if path]


def reviewed_checkout(root):
    """One local/remote cleanliness scope, including relevant ignored inputs."""
    paths = set()
    for arguments in (['diff','--name-only','-z','HEAD'],
                      ['ls-files','--others','--exclude-standard','-z'],
                      ['ls-files','--others','--ignored','--exclude-standard','-z']):
        for name in _git_paths(root, arguments):
            relative = Path(name)
            scoped = (name.startswith('blender_render/') or
                      any(name == item or name.startswith(item+'/') for item in BUILD_INPUTS))
            if scoped and not excluded(relative):
                paths.add(name)
    return {'clean': not paths, 'paths': sorted(paths)}


@lru_cache(maxsize=1)
def _runtime_versions():
    import bpy
    versions = {'blender': bpy.app.version_string, 'python': '.'.join(map(str, sys.version_info[:2]))}
    for name in ('molecularnodes', 'numpy', 'biotite'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
            if name == 'molecularnodes':
                try:
                    import molecularnodes
                    import tomllib
                    versions[name] = tomllib.loads(Path(molecularnodes.__file__).with_name('blender_manifest.toml').read_text())['version']
                except (ImportError, OSError, ValueError, KeyError):
                    pass
    required = _identity()['runtime_requirements']
    mismatches = [key for key, value in versions.items() if value != required[key]]
    return {'versions': versions, 'verified': not mismatches, 'mismatches': mismatches}


def runtime_observation():
    from .worker_ccd import observation
    result = copy.deepcopy(_runtime_versions())
    result['ccd'] = observation()
    if not result['ccd']['verified']:
        result['verified'] = False
        result['mismatches'].append('ccd_sha256')
    return result
