"""Dependency-free client for the versioned asynchronous API."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode, quote


class ClientError(RuntimeError):
    def __init__(self, message, status=0, code="client_error", *, job_id=None, next_action=None):
        self.job_id, self.next_action = job_id, next_action
        if job_id is not None:
            message = f'{message} [condition={code}, job_id={job_id}, next_action={next_action}]'
        super().__init__(message)
        self.status, self.code = status, code


class IncompleteResultError(ClientError):
    """A render produced verified outputs but its required set is incomplete."""

    def __init__(self, result):
        self.result = result
        super().__init__(
            "Required render artifacts are incomplete; verified partial outputs were retained.",
            410,
            result.get("error_code") or "artifact_unavailable",
            job_id=result.get("job_id"),
            next_action=result.get("next_action"),
        )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ClientError("Unexpected redirect from render service; check the configured endpoint.")


class Client:
    def __init__(self, url, token, timeout=30, *, lab_http=False):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ClientError("Provide an HTTP(S) service endpoint without embedded credentials.")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and not lab_http:
            raise ClientError("Remote services require HTTPS or a loopback SSH tunnel.")
        if lab_http:
            if parsed.scheme != 'http' or token:
                raise ClientError('Lab mode requires an HTTP endpoint without a client token')
            from .lab_transport import LabHTTPHandler
            self.opener = build_opener(ProxyHandler({}), LabHTTPHandler(), NoRedirect())
        else:
            self.opener = build_opener(NoRedirect())

    def request(self, method, path, body=None, *, binary=False, raw=False):
        if method == "POST" and body is None:
            body = {}
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if getattr(self, 'controller_owner', None):
            headers['X-Render-Owner'] = self.controller_owner
        data = None
        if body is not None:
            data = body if raw else json.dumps(body, allow_nan=False).encode()
            headers["Content-Type"] = "application/octet-stream" if raw else "application/json"
        try:
            with self.opener.open(Request(self.url + path, data=data, headers=headers, method=method), timeout=self.timeout) as response:
                raw = response.read()
                return raw if binary else json.loads(raw)
        except HTTPError as exc:
            try:
                problem = json.loads(exc.read())
            except Exception:
                problem = {}
            raise ClientError(problem.get("error", "Service returned HTTP " + str(exc.code)), exc.code, problem.get("code", "http_error")) from exc
        except URLError as exc:
            raise ClientError("Cannot reach render service: " + str(exc.reason)) from exc

    def upload(self, payload, name="structure.cif"):
        return self.request("POST", "/v1/structures", {"payload": payload, "name": name})

    def upload_file(self, path, *, consumer=False):
        path = Path(path).expanduser().resolve()
        suffix = path.suffix.lower().lstrip(".")
        if suffix not in {"pse", "cif", "mmcif", "pdb"}:
            raise ClientError("Choose a .pse, .cif, or .pdb file.")
        with path.open("rb") as stream:
            data = stream.read(50 * 1024**2 + 1)
        if len(data) > 50 * 1024**2:
            if consumer:
                raise ClientError('File exceeds the 50 MiB upload limit.', 413, 'too_large')
            raise ClientError("File exceeds the 50 MiB upload limit.")
        query = 'consumer=1' if consumer else urlencode({'name': path.name})
        return self.request("POST", "/v1/files/" + suffix + '?' + query, data, raw=True)

    def structures(self):
        return self.request("GET", "/v1/structures")

    def delete_structure(self, input_id):
        return self.request("DELETE", "/v1/structures/" + quote(input_id, safe=''))

    def submit(self, input_id, options=None, idempotency_key=None, *, rerender=False, operation_key=None):
        if rerender and not (idempotency_key or operation_key):
            raise ClientError('An explicit repeat requires a stable operation or idempotency key')
        return self.request("POST", "/v1/jobs", {"input_id": input_id, "options": options or {}, "idempotency_key": idempotency_key or uuid.uuid4().hex, "rerender": rerender, 'operation_key': operation_key})

    def recover_render(self, request, *, consumer_policy=None):
        """Owner-scoped lookup, independent of file bytes and attachment URLs."""
        data = {'request': request, 'consumer_policy': consumer_policy} if consumer_policy is not None else request
        return self.request('POST', '/v1/consumer/recover', data)['job']

    def submit_consumer(self, input_id, request, *, rerender=False):
        return self.request('POST', '/v1/consumer/submit',
                            {'input_id': input_id, 'request': request, 'rerender': rerender})

    def render_consumer_file(self, path, request, *, consumer_policy=None):
        policy_options = {'consumer_policy': consumer_policy} if consumer_policy is not None else {}
        replay = self.recover_render(request, **policy_options)
        if replay is not None:
            return replay
        envelope = {'request': request, **policy_options} if policy_options else request
        preflight = self.request('POST', '/v1/consumer/preflight', envelope)
        if preflight.get('job') is not None:
            return preflight['job']
        if 'refusal' in preflight:
            return preflight['refusal']
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() not in {'.pse', '.cif', '.mmcif', '.pdb'}:
            raise ClientError('Choose a molecular PSE, CIF or PDB input', 422, 'format_unknown')
        try:
            with path.open('rb') as stream:
                data = stream.read(50 * 1024**2 + 1)
        except OSError as exc:
            raise ClientError('The selected molecular file is unavailable.', 422, 'attachment_unavailable') from exc
        if len(data) > 50 * 1024**2:
            raise ClientError('Input exceeds 50 MiB', 413, 'too_large')
        settings = json.dumps({'consumer_request': request, **policy_options}, allow_nan=False, separators=(',', ':')).encode()
        if len(settings) > 65532:
            raise ClientError('Consumer request exceeds the bounded envelope', 422, 'selection_invalid')
        # A private filename is unnecessary for conversion and must not enter access logs.
        return self.request('POST', '/v1/render-files/' + path.suffix.lower()[1:],
                            len(settings).to_bytes(4, 'big') + settings + data, raw=True)

    def render_file(self, path, options=None, idempotency_key=None, *, rerender=False, operation_key=None, selection='(all)'):
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() not in {'.pse', '.cif', '.mmcif', '.pdb'}:
            raise ClientError('Choose a .pse, .cif, or .pdb file.')
        with path.open('rb') as stream:
            data = stream.read(50 * 1024**2 + 1)
        if len(data) > 50 * 1024**2:
            raise ClientError('File exceeds the 50 MiB upload limit.')
        settings = json.dumps({'options': options or {}, 'idempotency_key': idempotency_key,
            'operation_key': operation_key, 'rerender': rerender, 'selection': selection}, allow_nan=False).encode()
        if len(settings) > 65532:
            raise ClientError('Render request envelope exceeds 64 KiB.')
        return self.request('POST', '/v1/render-files/' + path.suffix.lower()[1:] + '?' + urlencode({'name': path.name}),
            len(settings).to_bytes(4, 'big') + settings + data, raw=True)

    def jobs(self):
        return self.request('GET', '/v1/jobs')

    def _request_with_timeout(self, method, path, *, timeout=None):
        """Issue one read with a temporary transport cap.

        A few compatibility tests intentionally construct ``Client`` subclasses
        without running ``__init__``. Keep supporting those adapters while
        ensuring a real transport never inherits a timeout longer than the
        caller's remaining deadline.
        """
        if timeout is None:
            return self.request(method, path)
        limit = max(0.001, float(timeout))
        had_timeout = hasattr(self, "timeout")
        original_timeout = getattr(self, "timeout", 30)
        self.timeout = min(float(original_timeout), limit)
        try:
            return self.request(method, path)
        finally:
            if had_timeout:
                self.timeout = original_timeout
            else:
                self.__dict__.pop("timeout", None)

    def status(self, job_id, *, timeout=None):
        return self._request_with_timeout("GET", "/v1/jobs/" + job_id, timeout=timeout)

    def result(self, job_id, *, timeout=None):
        return self._request_with_timeout(
            "GET", "/v1/jobs/" + job_id + "/result", timeout=timeout
        )

    def cancel(self, job_id):
        return self.request("POST", "/v1/jobs/" + job_id + "/cancel")

    def delete_result(self, job_id):
        return self.request("DELETE", "/v1/jobs/" + job_id + "/result")

    @staticmethod
    def _deadline_error(job_id, next_action="render_status"):
        return ClientError(
            f"Render {job_id} did not complete within the requested deadline; "
            "reconnect to the existing operation and do not resubmit.",
            408,
            "operation_timeout",
            job_id=job_id,
            next_action=next_action,
        )

    def wait(
        self,
        job_id,
        timeout=1800,
        progress=None,
        *,
        deadline=None,
        fetch_result=True,
    ):
        deadline = min(
            time.monotonic() + timeout,
            float(deadline) if deadline is not None else float("inf"),
        )
        previous = None
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                state = self.status(job_id, timeout=remaining)
            except ClientError:
                if time.monotonic() >= deadline:
                    raise self._deadline_error(job_id) from None
                raise
            if time.monotonic() > deadline:
                raise self._deadline_error(job_id)
            if state.get('condition') in {'restart_required', 'compatibility_blocked',
                                         'execution_unknown', 'recovery_required'}:
                raise ClientError(state.get('error') or 'Render operation requires intervention',
                                  409, state['condition'], job_id=job_id,
                                  next_action=state.get('next_action'))
            marker = (state["status"], state["queue_position"])
            if progress and marker != previous:
                progress(state)
                previous = marker
            if state["status"] == "done":
                if not fetch_result:
                    return state
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._deadline_error(job_id, "render_result")
                try:
                    result = self.result(job_id, timeout=remaining)
                except ClientError:
                    if time.monotonic() >= deadline:
                        raise self._deadline_error(job_id, "render_result") from None
                    raise
                if time.monotonic() > deadline:
                    raise self._deadline_error(job_id, "render_result")
                return result
            if state['status'] == 'transfer_pending':
                raise ClientError('Child execution retained; recover this operation with recover-transfer', 409, 'transfer_pending')
            if state["status"] in {"failed", "cancelled", "expired", "delivered", "deleted"}:
                raise ClientError(state.get("error") or "Render " + state["status"])
            time.sleep(min(.5, max(0.0, deadline - time.monotonic())))
        raise self._deadline_error(job_id)

    def download(self, job_id, output, *, deadline=None):
        from .delivery import recover_result
        return recover_result(self, job_id, output, deadline=deadline)

    def recover_transfer(self, job_id):
        return self.request('POST', '/v1/jobs/' + quote(job_id, safe='') + '/recover-transfer')


from enum import Enum


class ConnectionIntention(str, Enum):
    INSPECT_CACHED = 'inspect_cached'
    ATTACH_EXISTING = 'attach_existing'
    SUBMIT_EXISTING = 'submit_existing'
    START_OR_ALLOCATE = 'start_or_allocate'


def connection_intention(intention=None, *, read_only=None, attach_only=None):
    legacy = (ConnectionIntention.ATTACH_EXISTING if attach_only else
              ConnectionIntention.INSPECT_CACHED if read_only else None)
    if attach_only and read_only:
        raise ValueError('Conflicting connection intentions')
    selected = ConnectionIntention(intention) if intention is not None else legacy
    if legacy is not None and selected != legacy:
        raise ValueError('Conflicting connection intentions')
    return selected


class ConfiguredClient(Client):
    """Construction and all reads are inert with respect to service startup."""
    def __init__(self, profile, intention, options):
        self.profile, self.intention, self.options = profile, intention, options

    def consumer_policy(self):
        from . import consumer
        trusted = self.options.get('consumer_trusted', consumer.trusted_profile(self.profile))
        return {'format_version': 1, 'public': not trusted}

    def request(self, method, path, body=None, **kwargs):
        if method == 'POST' and path in {'/v1/consumer/recover', '/v1/consumer/preflight'}:
            body = {'request': body, 'consumer_policy': self.consumer_policy()}
        elif method == 'POST' and path == '/v1/consumer/submit':
            body = {**body, 'consumer_policy': self.consumer_policy()}
        start = method == 'POST' and (path == '/v1/structures' or path in {'/v1/jobs', '/internal/v1/resolved', '/v1/consumer/submit'}
            or path.startswith(('/v1/files/', '/v1/render-files/')))
        intention = self.intention or (ConnectionIntention.START_OR_ALLOCATE if start else ConnectionIntention.INSPECT_CACHED)
        if not start and intention == ConnectionIntention.START_OR_ALLOCATE:
            intention = ConnectionIntention.INSPECT_CACHED
        if start and intention not in {
            ConnectionIntention.START_OR_ALLOCATE,
            ConnectionIntention.SUBMIT_EXISTING,
        }:
            raise ClientError('Connection intention prohibits new execution submission')
        connection = _connect_client(self.profile, intention=intention, **self.options)
        return connection.request(method, path, body, **kwargs)

    def _request_with_timeout(self, method, path, *, timeout=None):
        if timeout is None:
            return self.request(method, path)
        options = dict(self.options)
        requested = max(0.001, float(timeout))
        configured = float(options.get("timeout", requested))
        options["timeout"] = min(configured, requested)
        return ConfiguredClient(self.profile, self.intention, options).request(method, path)

    def download(self, job_id, output, *, deadline=None):
        # Resolve credentials without startup, then scope receipts to this
        # concrete service and credential. No upload/submit fallback exists.
        intention = (ConnectionIntention.ATTACH_EXISTING if self.intention == ConnectionIntention.ATTACH_EXISTING
                     else ConnectionIntention.INSPECT_CACHED)
        return _connect_client(self.profile, intention=intention, **self.options).download(
            job_id, output, deadline=deadline
        )

    def wait(
        self,
        job_id,
        timeout=1800,
        progress=None,
        *,
        deadline=None,
        fetch_result=True,
    ):
        intention = (
            ConnectionIntention.ATTACH_EXISTING
            if self.intention in {
                ConnectionIntention.ATTACH_EXISTING,
                ConnectionIntention.SUBMIT_EXISTING,
            }
            else ConnectionIntention.INSPECT_CACHED
        )
        options = dict(self.options)
        options["timeout"] = min(float(options.get("timeout", timeout)), float(timeout))
        return _connect_client(self.profile, intention=intention, **options).wait(
            job_id,
            timeout,
            progress,
            deadline=deadline,
            fetch_result=fetch_result,
        )

    def render_consumer_file(self, path, request):
        from . import consumer
        # An absent cached controller is distinct from a failed request to an
        # existing controller. Never turn a lost/ambiguous response into submit.
        try:
            replay = self.recover_render(request)
        except RuntimeError as exc:
            if not isinstance(exc.__cause__, FileNotFoundError):
                raise
            replay = None
        if replay is not None:
            return replay
        trusted = self.options.get('consumer_trusted', consumer.trusted_profile(self.profile))
        normalized = consumer.normalize_request(request, trusted=trusted)
        decision = consumer.preselect(normalized, consumer.server_policy(not trusted))
        if not consumer.executable(decision):
            return consumer.non_admission(decision, self.profile)
        intention = self.intention or ConnectionIntention.START_OR_ALLOCATE
        if intention not in {
            ConnectionIntention.START_OR_ALLOCATE,
            ConnectionIntention.SUBMIT_EXISTING,
        }:
            raise ClientError('Connection intention prohibits new execution submission')
        # Local/HPC profiles use the existing outer admission controller. The
        # selected backend is still reached only after its durable queue claim.
        options = {**self.options, 'admission': True}
        connection = _connect_client(self.profile, intention=intention, **options)
        return connection.render_consumer_file(path, request, consumer_policy=self.consumer_policy())


def configured_client(profile='local', *, read_only=None, intention=None, attach_only=None, **kwargs):
    return ConfiguredClient(profile, connection_intention(intention, read_only=read_only, attach_only=attach_only), kwargs)


def start_service(profile='local', **kwargs):
    """Explicit user start command; generic client construction stays inert."""
    return _connect_client(profile, read_only=False, **kwargs).request('GET', '/health')


def _connect_client(profile="local", *, read_only=None, admission=False, intention=None, attach_only=None, **kwargs):
    kwargs.pop('consumer_trusted', None)  # Adapter schema configuration, never worker configuration.
    request_timeout = kwargs.get('timeout')
    intention = connection_intention(intention, read_only=read_only, attach_only=attach_only) or ConnectionIntention.START_OR_ALLOCATE
    existing_only = intention != ConnectionIntention.START_OR_ALLOCATE
    controller_owner = kwargs.pop('controller_owner', None)
    from .config import load_config
    if profile in {"local", "apptainer"}:
        from .runtime import ensure_local, existing_local
        connection = existing_local() if existing_only else ensure_local(**kwargs)
    else:
        config = load_config()
        if profile not in config.get("profiles", {}):
            raise ClientError(f"Unknown profile {profile!r}. Run blender-render profile add first.")
        connection = config["profiles"][profile]
        if controller_owner is not None and connection.get('kind') != 'hpc' and connection.get('private') is not True:
            raise ClientError('Gateway execution requires an explicitly private endpoint; private structures cannot transit a shared service')
        if connection.get("kind") == "hpc":
            if admission:
                # CLI operations belong to this durable outer queue. Only its
                # gateway backend may connect/allocate, after admission.
                from .runtime import ensure_local, existing_local
                connection = (existing_local(worker_profile=profile) if existing_only
                              else ensure_local(worker_profile=profile, **kwargs))
            else:
                from .hpc import connect, existing_connection
                connection = (existing_connection(profile) if intention == ConnectionIntention.INSPECT_CACHED
                              else connect(profile, connection, intention=intention, **kwargs))
    if connection.get("auth") == "oauth" and controller_owner is None:
        from .oauth_client import access_token
        return Client(
            connection["url"],
            access_token(connection["url"]),
            timeout=float(request_timeout) if request_timeout is not None else 120,
        )
    if connection.get('kind') == 'lab_http':
        if controller_owner is not None:
            raise ClientError('Lab network access cannot be used for controller dispatch')
        return Client(
            connection['url'],
            '',
            timeout=float(request_timeout) if request_timeout is not None else 30,
            lab_http=True,
        )
    if controller_owner is not None:
        path = connection.get('controller_token_file')
        token = Path(path).expanduser().read_text().strip() if path else os.environ.get(connection.get('controller_token_env', ''), '')
        if len(token) < 32:
            raise ClientError('Separate controller credentials required for resolved execution; explicit configuration required')
        client = Client(
            connection['url'],
            token,
            timeout=float(request_timeout) if request_timeout is not None else 30,
        )
        client.controller_owner = controller_owner
        return client
    token = os.environ.get(connection.get("token_env", "BLENDER_RENDER_TOKEN"), "")
    if connection.get("token_file"):
        token = Path(connection["token_file"]).expanduser().read_text().strip()
    return Client(
        connection["url"],
        token,
        timeout=float(request_timeout) if request_timeout is not None else 30,
    )
