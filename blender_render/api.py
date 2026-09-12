"""Authenticated HTTP API and short-lived artifact downloads."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from starlette.background import BackgroundTask

from .config import VERSION
from .jobs import JobError
from .service import RenderService
from .engine import RestartRequired, EngineMismatch, ExecutionUnknown


class Downloads:
    def __init__(self, key: str, base_url: str, clock=time.time):
        if len(key) < 32:
            raise ValueError("Download signing key must contain at least 32 characters")
        self.key = key.encode()
        self.base_url = base_url.rstrip("/")
        self.clock = clock

    def url(self, owner: str, job_id: str, name: str, ttl: int = 900, *, preview: bool = False) -> str:
        data = base64.urlsafe_b64encode(json.dumps([owner, job_id, name, int(self.clock()) + ttl], separators=(",", ":")).encode()).decode().rstrip("=")
        signature = hmac.new(self.key, data.encode(), hashlib.sha256).hexdigest()
        return f"{self.base_url}/{'preview' if preview else 'download'}/{data}.{signature}"

    def verify(self, value: str) -> tuple[str, str, str]:
        try:
            if len(value) > 2048:
                raise ValueError()
            data, signature = value.rsplit(".", 1)
            expected = hmac.new(self.key, data.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError()
            owner, job, name, expires = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
            if self.clock() >= expires:
                raise JobError("Download link has expired; request a fresh result link.", "expired", 410)
            return owner, job, name
        except JobError:
            raise
        except Exception as exc:
            raise JobError("Invalid download link.", "unauthorized", 401) from exc


class BoundedBody:
    """Bound chunked bodies before handing them to REST, OAuth, or MCP parsers."""
    def __init__(self, app, limit):
        self.app, self.limit = app, limit
        self.readers = asyncio.Semaphore(4)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        limit = min(self.limit, 1024**2) if scope["path"].startswith("/mcp") else self.limit
        async with self.readers:
            data = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                data.extend(message.get("body", b""))
                if len(data) > limit:
                    response = JSONResponse({"error":"Request is too large", "code":"too_large"}, status_code=413,
                        headers={"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"})
                    return await response(scope, receive, send)
                if not message.get("more_body", False):
                    break
            delivered = False
            async def bounded_receive():
                nonlocal delivered
                if delivered:
                    return await receive()
                delivered = True
                return {"type":"http.request", "body":bytes(data), "more_body":False}
            await self.app(scope, bounded_receive, send)


def create_app(service: RenderService, *, token: str, public_url: str = "", signing_key: str = "", auth_provider=None, mcp_server=None, converter=None, tokens: dict[str, str] | None = None, controller_tokens: dict[str, str] | None = None) -> FastAPI:
    if len(token) < 32:
        raise ValueError("A service/admin token of at least 32 characters is required")
    token_owners = {token: "local"} if tokens is None else tokens
    controller_tokens = controller_tokens or {}
    if any(len(key) < 32 or key == token or key in token_owners for key in controller_tokens):
        raise ValueError('Controller credentials must be separate from consumer/admin credentials')
    downloads = Downloads(signing_key or token, public_url)
    mcp_app = mcp_server.http_app(path="/mcp", stateless_http=True) if mcp_server else None

    @asynccontextmanager
    async def lifespan(app):
        service.start()
        try:
            if mcp_app:
                async with mcp_app.lifespan(app):
                    yield
            else:
                yield
        finally:
            service.stop()

    app = FastAPI(title="Blender Render", version=VERSION, lifespan=lifespan)
    app.state.service = service
    app.state.downloads = downloads
    converter = converter or service.converter
    if converter is not None:
        service.converter = converter

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def homepage():
        from .public_site import page
        return page("home")

    @app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
    async def privacy():
        from .public_site import page
        return page("privacy")

    @app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
    async def terms():
        from .public_site import page
        return page("terms")

    @app.exception_handler(JobError)
    async def job_error(request, exc):
        return JSONResponse({"error": str(exc), "code": exc.code}, status_code=exc.status)

    @app.exception_handler(RestartRequired)
    async def restart_required(request, exc):
        return JSONResponse({'error':'Service integrity changed; restart required',
                             'code':'restart_required', 'next_action':'restart_service'}, status_code=503)

    @app.exception_handler(EngineMismatch)
    @app.exception_handler(ExecutionUnknown)
    async def execution_condition(request, exc):
        unknown = isinstance(exc, ExecutionUnknown)
        return JSONResponse({'error':'Execution requires recovery or compatible runtime setup',
                             'code':'execution_unknown' if unknown else 'compatibility_blocked',
                             'next_action':'inspect_execution' if unknown else 'restore_exact_engine'}, status_code=503)

    @app.middleware("http")
    async def size_limit(request: Request, call_next):
        raw_length = request.headers.get("content-length", "0")
        try:
            if int(raw_length) > service.store.policy.max_upload_bytes + 65536:
                return JSONResponse({"error": "Request is too large", "code": "too_large"}, status_code=413)
        except ValueError:
            return JSONResponse({"error": "Invalid content length"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    async def owner(request: Request) -> str:
        value = request.headers.get("authorization", "")
        bearer = value[7:] if value.startswith("Bearer ") else ""
        for candidate, controller in controller_tokens.items():
            if hmac.compare_digest(candidate, bearer):
                from .execution import scoped_owner
                identity = request.headers.get('x-render-owner', '')
                if not 1 <= len(identity) <= 128:
                    raise JobError('Scoped owner required', 'unauthorized', 401)
                return scoped_owner(controller, identity)
        # The private operator token is never a public user's identity.
        if not service.store.policy.public:
            for candidate, identity in token_owners.items():
                if hmac.compare_digest(candidate, bearer):
                    return identity
        if auth_provider and bearer:
            verified = await auth_provider.verify_token(bearer)
            if verified and verified.claims.get("sub"):
                return "github:" + str(verified.claims["sub"])
        raise JobError("Sign in to access this render service.", "unauthorized", 401)

    async def admin(request: Request):
        value = request.headers.get("authorization", "")
        if not hmac.compare_digest(value, "Bearer " + token):
            raise JobError("Operator authentication required.", "unauthorized", 401)

    async def body(request: Request) -> dict:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > service.store.policy.max_upload_bytes + 65536:
                raise JobError("Request is too large.", "too_large", 413)
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise JobError("Request body must be valid JSON.") from exc
        if not isinstance(data, dict):
            raise JobError("Request body must be an object.")
        return data

    @app.post('/internal/v1/resolved')
    async def resolved(request: Request):
        value = request.headers.get('authorization', '')
        for candidate, controller in controller_tokens.items():
            if hmac.compare_digest(value, 'Bearer ' + candidate):
                return await asyncio.to_thread(service.submit_resolved, controller, await body(request))
        raise JobError('Controller authentication required', 'unauthorized', 401)

    @app.get('/internal/v1/operations/{operation}')
    async def internal_operation(operation: str, identity=Depends(owner)):
        # Ordinary callers can only read their own scope, never a controller's.
        with service.store.lock:
            row = service.store.db.execute('SELECT job_id FROM operation_keys WHERE owner=? AND key=?', (identity, operation)).fetchone()
            if not row:
                raise JobError('Operation not found; submission outcome remains unknown', 'not_found', 404)
            return service.store.get(identity, row['job_id'])

    @app.get("/healthz")
    @app.get("/health")
    async def health(request: Request):
        if not service.store.policy.public:
            await owner(request)
        with service.store.lock:
            queue = service.store.db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0]
            running = service.store.db.execute("SELECT COUNT(*) FROM jobs WHERE state='running'").fetchone()[0]
        return {"status": "restart_required" if service.integrity.latched else ("ok" if service.store.healthy else "unavailable"),
                "version": VERSION, "queue_depth": queue, "running": running}

    @app.get("/v1/quota")
    async def quota(identity=Depends(owner)):
        return service.store.quota(identity)

    @app.get("/v1/presets")
    async def presets(identity=Depends(owner)):
        from .core import PRESET_STYLE_FILENAMES
        return {"presets": list(PRESET_STYLE_FILENAMES)}

    @app.get('/v1/capabilities')
    async def capabilities(identity=Depends(owner)):
        from .engine import engine_identity, sanitized_observation
        from .config import runtime_versions
        return {'version': VERSION, 'required_runtime': runtime_versions(), 'runtime_verified': False,
                'capabilities': engine_identity()['capabilities'], 'controller_declaration': engine_identity(),
                'worker_observation': sanitized_observation(getattr(getattr(service.backend, 'worker', None), 'observation', None)),
                'retrieval_protocol': 'explicit_ack_v1',
                'condition': 'restart_required' if service.integrity.latched else None,
                'limitation': 'Installed controller declaration, not a live compute probe.'}

    @app.post("/v1/structures")
    async def upload(request: Request, identity=Depends(owner)):
        data = await body(request)
        return await asyncio.to_thread(service.import_payload, identity, data.get("payload"), data.get("name", "structure.cif"))

    @app.get("/v1/structures")
    async def structures(identity=Depends(owner)):
        return await asyncio.to_thread(service.store.list_inputs, identity)

    @app.delete("/v1/structures/{input_id}")
    async def delete_structure(input_id: str, identity=Depends(owner)):
        return await asyncio.to_thread(service.store.delete_input, identity, input_id)

    @app.post("/v1/files/{format}")
    async def upload_file(format: str, request: Request, identity=Depends(owner)):
        if converter is None and request.query_params.get('consumer') == '1':
            raise JobError('The molecular converter is unavailable.', 'compute_unavailable', 503)
        if format not in {"pse", "cif", "mmcif", "pdb"} or converter is None:
            raise JobError("This service does not support that file conversion.", "unsupported", 422)
        data = await request.body()
        if len(data) > service.store.policy.max_upload_bytes:
            raise JobError("File is too large.", "too_large", 413)
        name = request.query_params.get("name") or ("structure." + format)
        return await asyncio.to_thread(service.import_file, identity, data, "." + format, name, converter)

    @app.post("/v1/jobs", status_code=202)
    async def submit(request: Request, identity=Depends(owner)):
        data = await body(request)
        return await asyncio.to_thread(service.submit, identity, data.get("input_id", ""), data.get("options", {}), data.get("idempotency_key", ""), rerender=data.get('rerender', False), operation_key=data.get('operation_key'))

    @app.post('/v1/render-files/{format}', status_code=202)
    async def render_file(format: str, request: Request, identity=Depends(owner)):
        # Four-byte JSON-envelope length, envelope, then bounded raw file bytes.
        # Avoid base64 expansion and proxy-dependent large HTTP headers.
        raw = await request.body()
        try:
            size = int.from_bytes(raw[:4], 'big')
            if not 0 < size <= 65532 or len(raw) < 4 + size:
                raise ValueError()
            settings = json.loads(raw[4:4 + size])
            allowed = {'options', 'idempotency_key', 'operation_key', 'rerender', 'selection', 'consumer_request', 'consumer_policy'}
            if not isinstance(settings, dict) or set(settings) - allowed:
                raise ValueError()
            if 'consumer_request' in settings and set(settings) - {'consumer_request', 'consumer_policy'}:
                raise ValueError()
            if 'consumer_policy' in settings and 'consumer_request' not in settings:
                raise ValueError()
        except ValueError as exc:
            raise JobError('Invalid render request envelope') from exc
        if 'consumer_request' in settings:
            from . import consumer
            consumer.adapter_public(settings.get('consumer_policy'))
            plan = await asyncio.to_thread(service.consumer_preflight, identity, settings['consumer_request'],
                                           trusted=not service.store.policy.public, adapter_policy=settings.get('consumer_policy'))
            if 'replay' in plan:
                return plan['replay']
            if not consumer.executable(plan['decision']):
                return consumer.non_admission(plan['decision'], 'configured')
            return await asyncio.to_thread(service.admit_consumer_file, identity, raw[4 + size:], '.' + format,
                request.query_params.get('name', 'structure.' + format), converter, plan)
        return await asyncio.to_thread(service.render_file, identity, raw[4 + size:], '.' + format,
            request.query_params.get('name', 'structure.' + format), converter,
            settings.pop('options', {}), **settings)

    def consumer_envelope(data):
        from . import consumer
        if isinstance(data, dict) and 'request' in data:
            if set(data) != {'request', 'consumer_policy'}:
                raise JobError('Invalid consumer envelope.', 'selection_invalid', 422)
            consumer.adapter_public(data['consumer_policy'])
            return data['request'], data['consumer_policy']
        return data, None

    @app.post('/v1/consumer/recover')
    async def recover_consumer(request: Request, identity=Depends(owner)):
        data, policy = consumer_envelope(await body(request))
        return {'job': service.recover_render(identity, data, trusted=not service.store.policy.public, adapter_policy=policy)}

    @app.post('/v1/consumer/preflight')
    async def preflight_consumer(request: Request, identity=Depends(owner)):
        from . import consumer
        data, policy = consumer_envelope(await body(request))
        plan = await asyncio.to_thread(service.consumer_preflight, identity, data,
                                       trusted=not service.store.policy.public, adapter_policy=policy)
        if 'replay' in plan:
            return {'job': plan['replay']}
        if not consumer.executable(plan['decision']):
            return {'refusal': consumer.non_admission(plan['decision'], 'configured')}
        return {'ready': True}

    @app.post('/v1/consumer/submit')
    async def submit_consumer(request: Request, identity=Depends(owner)):
        data = await body(request)
        from . import consumer
        if (not isinstance(data, dict) or set(data) - {'input_id', 'request', 'rerender', 'consumer_policy'}
                or not {'input_id', 'request', 'rerender'} <= set(data) or type(data['rerender']) is not bool):
            raise JobError('Invalid consumer submission.', 'selection_invalid', 422)
        consumer.adapter_public(data.get('consumer_policy'))
        return await asyncio.to_thread(service.submit_consumer, identity, data['input_id'], data['request'],
            trusted=not service.store.policy.public, rerender=data['rerender'], adapter_policy=data.get('consumer_policy'))

    @app.get('/v1/jobs')
    async def recent_jobs(identity=Depends(owner)):
        return service.store.recent(identity)

    @app.get("/status/{job_id}")
    @app.get("/v1/jobs/{job_id}")
    async def status(job_id: str, identity=Depends(owner)):
        return service.store.get(identity, job_id)

    @app.post("/v1/jobs/{job_id}/cancel")
    async def cancel(job_id: str, identity=Depends(owner)):
        return await asyncio.to_thread(service.store.cancel, identity, job_id)

    @app.post('/v1/jobs/{job_id}/recover-transfer')
    async def recover_transfer(job_id: str, identity=Depends(owner)):
        return service.resume_transfer(identity, job_id)

    @app.get("/result/{job_id}")
    @app.get("/v1/jobs/{job_id}/result")
    async def result(job_id: str, inventory: bool = False, identity=Depends(owner)):
        data = service.store.result(identity, job_id, inventory=inventory)
        data['delivery_policy'] = 'explicit_ack_v1'
        for artifact in data["artifacts"]:
            artifact["download_url"] = downloads.url(identity, job_id, artifact["name"])
        return data

    @app.get("/v1/jobs/{job_id}/artifacts/{name}")
    async def artifact(job_id: str, name: str, request: Request, identity=Depends(owner)):
        path, mime = service.artifact(identity, job_id, name)
        return FileResponse(path, media_type=mime, filename=name,
            background=None if 'range' in request.headers or request.query_params.get('delivery') == 'ack' else BackgroundTask(service.store.artifact_delivered, identity, job_id, name))

    @app.post('/v1/jobs/{job_id}/artifacts/{name}/ack')
    async def acknowledge(job_id: str, name: str, request: Request, identity=Depends(owner)):
        try:
            body = await request.json()
        except ValueError as exc:
            raise JobError('Expected artifact sha256') from exc
        if not isinstance(body, dict) or set(body) != {'sha256'} or not isinstance(body['sha256'], str):
            raise JobError('Expected artifact sha256')
        return await asyncio.to_thread(service.store.artifact_delivered, identity, job_id, name, sha256=body['sha256'])

    @app.delete("/v1/jobs/{job_id}/result")
    async def delete_result(job_id: str, identity=Depends(owner)):
        return await asyncio.to_thread(service.store.delete_result, identity, job_id)

    @app.get("/download/{signed}")
    async def download(signed: str, request: Request):
        identity, identifier, name = downloads.verify(signed)
        path, mime = service.artifact(identity, identifier, name)
        return FileResponse(path, media_type=mime, filename=name,
            background=None if 'range' in request.headers else BackgroundTask(service.store.artifact_delivered, identity, identifier, name))

    @app.get("/preview/{signed}")
    async def preview(signed: str):
        identity, identifier, name = downloads.verify(signed)
        if name != 'render.png':
            raise JobError('Only PNG previews are available.', 'not_found', 404)
        path, mime = service.artifact(identity, identifier, name)
        return FileResponse(path, media_type=mime)

    @app.post("/v1/admin/drain", dependencies=[Depends(admin)])
    async def drain():
        await asyncio.to_thread(service.store.drain)
        return {"status": "draining"}

    @app.post("/v1/admin/maintenance", dependencies=[Depends(admin)])
    async def maintenance():
        await asyncio.to_thread(service.store.cleanup)
        return {"status": "ok"}

    @app.post("/v1/admin/resume", dependencies=[Depends(admin)])
    async def resume():
        await asyncio.to_thread(service.store.resume)
        service.wakeup.set()
        return {"status": "open"}

    @app.post("/stop", dependencies=[Depends(admin)])
    @app.post("/v1/admin/stop", dependencies=[Depends(admin)])
    async def stop():
        await asyncio.to_thread(service.store.drain)
        shutdown = getattr(app.state, "shutdown", None)
        if shutdown:
            shutdown()
        return {"status": "stopping" if shutdown else "draining"}

    @app.post("/render")
    async def legacy_render(request: Request, identity=Depends(owner)):
        data = await body(request)
        # Strip transport metadata only. The shared resolver validates every
        # remaining render option, including unknown fields and overrides.
        options = {k: v for k, v in data.items() if k not in
                   {"payload", "idempotency_key", "request_timeout_seconds"}}
        from .spec import _check_options
        try:
            _check_options(options)
        except ValueError as exc:
            raise JobError(str(exc)) from exc
        source = await asyncio.to_thread(service.import_payload, identity, data.get("payload"))
        job = await asyncio.to_thread(service.submit, identity, source["input_id"], options, data.get("idempotency_key") or secrets.token_hex(16))
        deadline = time.monotonic() + min(float(data.get("request_timeout_seconds", 1800)), 3600)
        while time.monotonic() < deadline:
            state = service.store.get(identity, job["job_id"])
            if state["status"] == "done":
                result = service.store.result(identity, job["job_id"])
                response = {"status": "completed", "job_id": job["job_id"], "output_name": "render.png", "manifest": result["manifest"]}
                for name, field in (("render.png", "png_base64"), ("render.blend", "blend_base64")):
                    if any(a["name"] == name for a in result["artifacts"]):
                        path, _ = service.artifact(identity, job["job_id"], name)
                        response[field] = base64.b64encode(path.read_bytes()).decode()
                return response
            if state["status"] in {"failed", "cancelled", "expired"}:
                raise JobError(state.get("error") or "Render cancelled", "render_failed", 422)
            await asyncio.sleep(0.25)
        return JSONResponse({"error": "Render is still pending; poll the job ID instead of resubmitting.", **job}, status_code=504)

    if mcp_app:
        app.mount("/", mcp_app)
    app.add_middleware(BoundedBody, limit=service.store.policy.max_upload_bytes + 65536)
    return app
