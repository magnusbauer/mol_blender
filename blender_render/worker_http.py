"""Transient HTTP GPU worker; molecular data never becomes Function I/O."""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import tempfile
import threading
import time
import traceback
from pathlib import Path
from fastapi import FastAPI, Request


def create_worker(token: str, *, timeout: int = 600, backend_factory=None,
                  allow_animation: bool = False, max_atoms: int = 100000, max_bytes: int = 50*1024**2):
    from fastapi.responses import JSONResponse
    from .api import BoundedBody
    from .service import HostBackend
    from .engine import protect, process_seal, RestartRequired, EngineMismatch, ExecutionUnknown
    protect('admission')
    if type(max_atoms) is not int or max_atoms < 1 or type(max_bytes) is not int or max_bytes < 1:
        raise ValueError('Worker limits must be positive integers')
    if len(token) < 32:
        raise ValueError('A worker token is required')
    app = FastAPI()
    flags = {}
    busy = threading.Lock()
    factory = backend_factory or (lambda root: HostBackend(root,
        blender='/opt/runtime/blender-5.0.1-linux-x64/blender', timeout=timeout-20))

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer '+token):
            return JSONResponse({'error':'Unauthorized'}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/health')
    async def health():
        from .engine import engine_identity, RestartRequired
        try:
            process_seal().check()
        except RestartRequired:
            return JSONResponse({'ready':False, 'condition':'restart_required'}, status_code=503)
        return {'ready':not process_seal().latched, 'controller_declaration': engine_identity(), 'worker_observation': None}

    @app.post('/cancel/{job_id}')
    async def cancel(job_id: str):
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            return JSONResponse({'error':'Invalid job ID'}, status_code=400)
        flags.setdefault(job_id, (threading.Event(), time.monotonic()))[0].set()
        return {'cancel_requested':True}

    @app.post('/render/{job_id}')
    async def render(job_id: str, request: Request):
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            return JSONResponse({'error':'Invalid job ID'}, status_code=400)
        if job_id in flags:
            return JSONResponse({'error':'Job already attempted or cancelled'}, status_code=409)
        if not busy.acquire(blocking=False):
            return JSONResponse({'error':'Worker busy'}, status_code=429)
        flag = threading.Event()
        flags[job_id] = (flag, time.monotonic())
        task = None
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError('Expected an object')
            from .execution import request_snapshot, validate_snapshot
            context = payload.get('execution_context', {})
            if (context.get('version') != 1 or context.get('controller_id') != 'controller'
                    or context.get('operation_id') != job_id or not isinstance(context.get('owner_id'), str)
                    or not 1 <= len(context['owner_id']) <= 128):
                raise ValueError('Invalid authenticated execution context')
            protect('admission')
            validate_snapshot(request_snapshot(payload), public=True, allow_animation=allow_animation, timeout=timeout,
                              max_atoms=max_atoms, max_bytes=max_bytes)
            def run():
                try:
                    with tempfile.TemporaryDirectory(prefix='blender-render-worker-') as root:
                        protect('dispatch')
                        backend = factory(Path(root))
                        try:
                            return backend.render(payload, job_id, flag.is_set)
                        finally:
                            backend.stop()
                finally:
                    # The thread owns admission, even after its HTTP task is
                    # cancelled or times out. Release only after backend exit.
                    busy.release()
            task = asyncio.create_task(asyncio.to_thread(run))
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (RestartRequired, EngineMismatch, ExecutionUnknown) as exc:
            condition = ('restart_required' if isinstance(exc, RestartRequired) else
                         'compatibility_blocked' if isinstance(exc, EngineMismatch) else 'execution_unknown')
            return JSONResponse({'condition':condition, 'error':'Worker execution retained for recovery',
                'before_execution':getattr(exc, 'before_execution', not isinstance(exc, (RestartRequired, ExecutionUnknown)))}, status_code=503)
        except Exception as exc:
            # Only code locations and fixed diagnostic categories may enter
            # provider logs. Exception messages can contain molecular data.
            frames = [(Path(f.filename).name, f.lineno, f.name)
                      for f in traceback.extract_tb(exc.__traceback__)]
            hints = [hint for hint in ('No module named', 'exited during startup',
                'timed out', 'GPU', 'CIF', 'Permission denied', 'No such file')
                if hint.lower() in str(exc).lower()]
            logging.getLogger(__name__).error('Worker failure type=%s frames=%s hints=%s',
                type(exc).__name__, frames, hints)
            return JSONResponse({'error':'Render failed or timed out'}, status_code=500)
        finally:
            flag.set()
            if task is None:
                busy.release()
            else:
                # Retrieve a late exception without cancelling the live thread.
                task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            for key, (_, created) in list(flags.items()):
                if key != job_id and created < time.monotonic()-3600:
                    flags.pop(key, None)

    app.add_middleware(BoundedBody, limit=64*1024**2)
    return app


class HTTPBackend:
    """One reserved job, direct HTTPS delivery, and no automatic render retry."""
    paid = True

    def __init__(self, url, token, timeout=600, *, proxy_key='', proxy_secret=''):
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout
        self.headers = {'Authorization':'Bearer '+token, 'Modal-Key':proxy_key, 'Modal-Secret':proxy_secret}
        self.active = None

    def render(self, request, identifier, cancelled):
        import concurrent.futures
        import httpx
        from .engine import protect, ExecutionUnknown, RestartRequired, EngineMismatch
        def retained_condition(response, *, submitted):
            if response.status_code < 400:
                return
            try:
                data = response.json()
            except ValueError:
                data = {}
            condition = data.get('condition') if isinstance(data, dict) else None
            safe_condition = condition if condition in ('restart_required', 'compatibility_blocked', 'execution_unknown') else 'unspecified'
            logging.getLogger(__name__).warning('Worker HTTP response status=%s submitted=%s condition=%s',
                response.status_code, submitted, safe_condition)
            if condition == 'restart_required':
                exc = RestartRequired('restart_required: replace the affected worker')
                exc.before_execution = not submitted or data.get('before_execution') is True
                raise exc
            if condition == 'compatibility_blocked':
                raise EngineMismatch('Worker compatibility blocked; restore the required runtime')
            if condition == 'execution_unknown' or submitted:
                raise ExecutionUnknown('HTTP worker execution outcome unknown; no automatic replay')
        protect('submission')
        # Bind readiness and render to the same Modal routing session.
        headers = {**self.headers, 'Modal-Session-ID': identifier}
        with httpx.Client(headers=headers, timeout=self.timeout+30) as client:
            deadline = time.monotonic()+180
            while True:
                if cancelled():
                    raise RuntimeError('Cancelled')
                response = client.get(self.url+'/health', timeout=15)
                retained_condition(response, submitted=False)
                if response.status_code == 200:
                    from .engine import require_compatible
                    require_compatible(request.get('expected_engine'), response.json().get('controller_declaration'),
                                       request['render_spec']['required_capabilities'])
                    break
                if response.status_code != 503 or time.monotonic() >= deadline:
                    response.raise_for_status()
                time.sleep(1)
            self.active = identifier
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    protect('submission')
                    pending = pool.submit(client.post, self.url+'/render/'+identifier, json=request)
                    while True:
                        try:
                            response = pending.result(timeout=.25)
                            retained_condition(response, submitted=True)
                            response.raise_for_status()
                            return response.json()
                        except concurrent.futures.TimeoutError:
                            if cancelled():
                                self.stop()
                                raise RuntimeError('Cancelled')
                        except httpx.RequestError as exc:
                            logging.getLogger(__name__).warning('Worker transport failed after submission: %s', type(exc).__name__)
                            raise ExecutionUnknown('HTTP worker outcome unknown; no automatic replay') from exc
            finally:
                self.active = None

    def stop(self):
        if self.active:
            import httpx
            try:
                httpx.post(self.url+'/cancel/'+self.active, headers=self.headers, timeout=10)
            except httpx.HTTPError:
                pass
