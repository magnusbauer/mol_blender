"""Worker-owned CCD bytes. Importing this module never imports Blender/Biotite.

Only the actual Blender process calls initialize(), once before readiness.
Observations and render guards consume the captured file, never its source.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile

from .engine import RuntimeUnavailable, engine_identity
from .compatibility import make, dependency_diagnostic

_active = None


def _read_regular(path):
    if os.name == 'nt':
        from .platform_support import open_regular_windows
        with open_regular_windows(path) as stream:
            return stream.read()
    if not hasattr(os, 'O_NOFOLLOW'):
        raise OSError('CCD capture requires O_NOFOLLOW')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('CCD must be a regular file')
        return stream.read()


class WorkerCCD:
    def __init__(self, source, ccd_module, expected, parent=None):
        self.directory = None
        self.path = None
        self.ccd = ccd_module
        self.expected = expected
        self.closed = False
        reason = 'ccd_missing'
        observed = {}
        try:
            # Keep the descriptor's bytes across source rename/replacement.
            data = _read_regular(source)
            observed['ccd_sha256'] = hashlib.sha256(data).hexdigest()
            reason = 'ccd_checksum_mismatch'
            if observed['ccd_sha256'] != expected:
                raise ValueError('Prepared CCD checksum mismatch')
            reason = 'ccd_missing'
            self.directory = tempfile.TemporaryDirectory(prefix='worker-ccd-', dir=parent)
            self.path = Path(self.directory.name)/'components.bcif'
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                 getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                if os.name != 'nt':
                    os.fchmod(stream.fileno(), 0o400)
            if os.name == 'nt':
                self.path.chmod(0o400)
            # The pinned Biotite setter also clears its dependent CCD caches.
            # A setter exception is failure even if it changed _CCD_FILE first.
            reason = 'ccd_activation_failed'
            self.ccd.set_ccd_path(str(self.path))
            if not self.observation()['verified']:
                raise ValueError('Captured CCD activation could not be verified')
        except Exception as exc:
            self.close()
            raise RuntimeUnavailable('Worker CCD capture/activation failed; explicit setup or worker restart required',
                diagnostic=make(reason, phase='startup', component='ccd', before_execution=True,
                                expected={'ccd_sha256':expected}, observed=observed)) from exc

    def observation(self):
        digest, activated = None, False
        try:
            if not self.closed and self.path is not None:
                activated = Path(os.path.abspath(self.ccd._CCD_FILE)) == self.path
                digest = hashlib.sha256(_read_regular(self.path)).hexdigest()
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return {'path':str(self.path) if self.path is not None else None,
                'sha256':digest, 'expected_sha256':self.expected,
                'captured':True, 'activated':activated,
                'verified':activated and digest == self.expected}

    def close(self):
        global _active
        self.closed = True
        if _active is self:
            _active = None
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None


def initialize(*, parent=None, ccd_module=None, expected=None):
    """Called by worker startup, never by a controller or a render guard."""
    global _active
    if _active is not None:
        require()
        return _active
    try:
        if ccd_module is None:
            from biotite.structure.info import ccd as ccd_module
        source = os.environ.get('PRESS_BIOTITE_CCD_PATH') or ccd_module._CCD_FILE
        source = Path(source).expanduser()
        expected = expected or engine_identity()['runtime_requirements']['ccd_sha256']
        _active = WorkerCCD(source, ccd_module, expected, parent)
        return _active
    except RuntimeUnavailable:
        raise
    except Exception as exc:
        diagnostic = dependency_diagnostic(exc) if isinstance(exc, ImportError) else make(
            'ccd_missing', phase='startup', component='ccd', before_execution=True)
        raise RuntimeUnavailable('Worker CCD initialization unavailable; explicit setup required', diagnostic=diagnostic) from exc


def observation():
    if _active is not None:
        return _active.observation()
    return {'path':None, 'sha256':None,
            'expected_sha256':engine_identity()['runtime_requirements']['ccd_sha256'],
            'captured':False, 'activated':False, 'verified':False}


def require(*, before_execution=True):
    evidence = observation()
    if not evidence['verified']:
        error = RuntimeUnavailable if before_execution else RuntimeError
        from .compatibility import runtime_diagnostic
        diagnostic = runtime_diagnostic({'ccd':evidence}, {'ccd_sha256':evidence['expected_sha256']})
        exc = error('Captured worker CCD unavailable or activation changed; restart the worker')
        exc.diagnostic = diagnostic
        raise exc
    return evidence


def close():
    if _active is not None:
        _active.close()
