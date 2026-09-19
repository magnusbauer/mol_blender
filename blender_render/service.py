"""One job service used by HTTP, MCP, and all compute backends."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import math
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from . import core, consumer_errors
from .jobs import JobError, JobStore
from .engine import protect, process_seal, RestartRequired, EngineMismatch, ExecutionUnknown
from .compatibility import from_exception

log = logging.getLogger(__name__)


class Backend(Protocol):
    def render(self, request: dict, job_id: str, cancelled) -> dict: ...
    def stop(self) -> None: ...


class HostBackend:
    """A single warm Blender process; Apptainer changes only its launcher."""
    def __init__(self, root: Path, blender: str = "", image: str = "", timeout: int = 600):
        from .render_worker_manager import PersistentBlenderWorker
        self.worker = PersistentBlenderWorker(
            root_dir=root, blender_exe=blender or core._detect_host_blender_executable(),
            custom_script=core._default_custom_script(), use_apptainer=bool(image),
            apptainer_image=image, worker_start_timeout=180,
        )
        self.timeout = timeout
        self.progress = None

    def set_progress(self, callback):
        self.progress = callback

    def render(self, request: dict, job_id: str, cancelled) -> dict:
        from .render_server import RenderServerState, _process_render_request
        finished = threading.Event()

        def monitor():
            while not finished.wait(0.2):
                if cancelled():
                    # submit_render holds the socket lock while waiting. Signal
                    # the process first so cancellation cannot wait for render.
                    self.worker.interrupt()
                    return

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            return _process_render_request(request_id=job_id, request_doc=request,
                state=RenderServerState(worker=self.worker, default_job_timeout=self.timeout, cancelled=cancelled,
                    progress=(lambda stage: self.progress(job_id, stage)) if getattr(self, 'progress', None) else None))
        finally:
            finished.set()
            watcher.join(timeout=2)

    def stop(self):
        self.worker.interrupt()
        self.worker.stop()


def validate_input(payload: dict, *, max_atoms: int, max_bytes: int) -> dict:
    if not isinstance(payload, dict):
        raise JobError("Expected a structure payload object.")
    try:
        if len(json.dumps(payload, allow_nan=False).encode()) > max_bytes:
            raise JobError("Structure exceeds the upload size limit.", "too_large", 413)
        data = core.validate_payload_document(copy.deepcopy(payload))
    except JobError:
        raise
    except (TypeError, ValueError, core.PayloadValidationError) as exc:
        raise JobError(str(exc)) from exc
    from .export import _summarize_cif_text, _summarize_pdb_text
    text = data.get("cif_text") or data.get("pdb_text") or ""
    # Parse coordinates before queue admission so malformed files cannot start
    # a paid renderer. CIF metadata and secondary-structure hints stay intact.
    import gemmi
    try:
        molecule = gemmi.make_structure_from_block(gemmi.cif.read_string(text).sole_block()) if data.get('cif_text') else gemmi.read_pdb_string(text)
        count = 0
        for model in molecule:
            for chain in model:
                for residue in chain:
                    for atom in residue:
                        count += 1
                        if any(not math.isfinite(v) or abs(v) > 1e6 for v in (atom.pos.x, atom.pos.y, atom.pos.z)):
                            raise ValueError('Invalid atom coordinates')
        if not 0 < count <= max_atoms:
            raise JobError(f'Structure must contain 1 to {max_atoms} atoms.', 'atom_limit', 413)
    except JobError:
        raise
    except (RuntimeError, ValueError) as exc:
        raise JobError('Malformed structure or invalid coordinates.', 'invalid_structure', 422) from exc
    summary = _summarize_cif_text(text) if data.get("cif_text") else _summarize_pdb_text(text)
    if not 0 < summary.get("atom_count", 0) <= max_atoms:
        raise JobError(f"Structure must contain 1 to {max_atoms} atoms.", "atom_limit", 413)
    summary['atom_count'] = count
    data["structure_summary"] = summary
    data.pop("run_id", None)
    view = data["view"]
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in view["pymol_view"]):
        raise JobError("Camera values must be finite numbers.")
    return data


def prepare_options(options: dict, payload: dict, *, public: bool, timeout: int, preset_definition=None, allow_animation=False) -> dict:
    """Compatibility boundary: all style resolution lives in spec.resolve_options."""
    from .spec import resolve_options
    try:
        return resolve_options(options, payload, public=public, timeout=timeout, preset_definition=preset_definition, allow_animation=allow_animation)
    except (ValueError, core.RunnerError) as exc:
        raise JobError(str(exc)) from exc


def freeze_file_options(options):
    """Capture catalog bytes now; defer only bundle-dependent resolution."""
    from dataclasses import asdict
    from .spec import builtin_definition
    frozen = {'prepare_file': copy.deepcopy(options)}
    if 'style_doc' not in options:
        try:
            definition = builtin_definition(options.get('preset'))
        except (ValueError, core.RunnerError) as exc:
            raise JobError(str(exc)) from exc
        frozen['prepare_file']['preset'] = definition.identifier
        frozen['preset_definition'] = asdict(definition)
    return frozen


class RenderService:
    def __init__(self, store: JobStore, backend: Backend, *, recover: bool = True, converter=None, selection_policy=None):
        self.integrity = protect('admission')
        self._imports_guard = threading.Lock()
        self._imports = {}
        self.store = store
        self.store.process_blocked = lambda: self.integrity.latched
        self.store.integrity_lock = self.integrity.lock
        self.backend = backend
        self.converter = converter
        self.selection_policy = selection_policy
        if hasattr(backend, 'set_progress'):
            backend.set_progress(store.stage)
        self.wakeup = threading.Event()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.cleanup_thread: threading.Thread | None = None
        self.last_activity = time.time()
        if recover:
            self.store.recover_interrupted()

    def _protect(self, action):
        return protect(action)

    def start(self):
        if self.stopping.is_set() or self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="blender-render-fifo", daemon=True)
        self.thread.start()
        self.cleanup_thread = threading.Thread(target=self._maintain, name='blender-render-cleanup', daemon=True)
        self.cleanup_thread.start()

    def stop(self):
        self.stopping.set()
        self.wakeup.set()
        self.backend.stop()
        if self.thread:
            self.thread.join(timeout=10)
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=10)

    def _maintain(self):
        while not self.stopping.wait(2):
            try:
                self.store.cleanup()
            except Exception:
                log.exception('Temporary data cleanup failed')

    @contextmanager
    def _owner_import(self, owner):
        # Serialize one owner's imports across HTTP and MCP, so simultaneous
        # retries cannot reserve and launch duplicate paid conversions.
        with self._imports_guard:
            entry = self._imports.setdefault(owner, [threading.RLock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._imports_guard:
                entry[1] -= 1
                if not entry[1]:
                    del self._imports[owner]

    def import_payload(self, owner: str, payload: dict, name: str = "structure.cif", *, source_key: str | None = None) -> dict:
        self._protect('admission')
        with self._owner_import(owner):
            self.last_activity = time.time()
            p = self.store.policy
            data = validate_input(payload, max_atoms=p.max_atoms if p.public else 10000000, max_bytes=p.max_upload_bytes)
            return self.store.add_input(owner, data, name, source_key=source_key)

    def import_file(self, owner: str, data: bytes, suffix: str, name: str, converter) -> dict:
        self._protect('conversion')
        if suffix not in {'.pse', '.cif', '.mmcif', '.pdb'}:
            raise JobError("Choose a .pse, .cif, or .pdb file.")
        if len(data) > self.store.policy.max_upload_bytes:
            raise JobError("File is too large.", "too_large", 413)
        if converter is None:
            raise JobError('The molecular converter is unavailable.', 'compute_unavailable', 503)
        key = suffix + ':' + hashlib.sha256(data).hexdigest()
        with self._owner_import(owner):
            existing = self.store.find_source(owner, key)
            if existing:
                return existing
            self.store.reserve_conversion(owner)
            try:
                payload = converter(data, suffix)
            except (RestartRequired, EngineMismatch, ExecutionUnknown):
                raise
            except Exception as exc:
                error = consumer_errors.from_exception(exc, default='invalid_structure')
                raise JobError(error['error_message'], error['code'], 422) from None
            return self.import_payload(owner, payload, name, source_key=key)

    @staticmethod
    def _client_hash(options, *, rerender=False):
        if not isinstance(options, dict):
            raise JobError('Render options must be an object.')
        canonical = dict(options)
        canonical.setdefault('preset', 'preset_1_interface')
        for flag in ('rough', 'keep_temp'):
            canonical.setdefault(flag, False)
        # Omission can preserve a JSON value whereas explicit False disables
        # it. Do not collapse those different requests during retry lookup.
        if 'style_doc' not in canonical and not canonical.get('overrides'):
            for flag in ('raw_view', 'color_follow'):
                canonical.setdefault(flag, False)
        canonical.setdefault('preserve_view', True)
        if rerender:
            canonical['rerender'] = True
        try:
            return hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
        except (TypeError, ValueError) as exc:
            raise JobError('Render options must be valid JSON.') from exc

    def recover_render(self, owner, request, *, trusted=False, adapter_policy=None):
        """Owner-scoped intent lookup; no admission, selection, file read or upload."""
        from . import consumer
        normalized = consumer.normalize_request(request, trusted=trusted and not self.store.policy.public
                                                and not consumer.adapter_public(adapter_policy))
        return self.store.operation_replay(owner, normalized['request_key'], consumer.identity(normalized))

    def consumer_preflight(self, owner, request, *, trusted=False, adapter_policy=None):
        from . import consumer
        trusted = trusted and not self.store.policy.public and not consumer.adapter_public(adapter_policy)
        normalized = consumer.normalize_request(request, trusted=trusted)
        replay = self.store.operation_replay(owner, normalized['request_key'], consumer.identity(normalized))
        if replay:
            return {'replay': replay}
        if (normalized.get('animation', 'none') != 'none' and self.store.policy.public
                and not self.store.policy.allow_animation):
            raise JobError('Animation is unavailable on this connection.', 'capability_unavailable', 422)
        policy = consumer.server_policy(not trusted, self.selection_policy)
        if policy.allowed_presets is not None and (not policy.allowed_presets
                or normalized['preset'] is not None and normalized['preset'] not in policy.allowed_presets):
            raise JobError('selection_policy_refused: preset excluded by server policy', 'selection_policy_refused', 403)
        try:
            decision = consumer.preselect(normalized, policy)
        except JobError:
            raise
        except (ValueError, TypeError, core.RunnerError) as exc:
            raise JobError('The structured preset selection is invalid.', 'selection_invalid', 422) from exc
        return {'request': consumer.freeze_request(normalized, decision), 'decision': decision,
                'identity': consumer.identity(normalized), 'public': policy.public}

    def consumer_exact(self, request, initial, public, bundle):
        """Authoritative exact-context D1 gate shared by both MCP admissions."""
        from . import consumer
        decision = consumer.preselect(request, consumer.server_policy(public, self.selection_policy), bundle=bundle)
        if decision is not None and initial['catalog_digest'] != decision.catalog_digest:
            raise JobError('Installed catalog differs from preselection', 'catalog_changed', 422)
        return decision

    def submit_consumer(self, owner, input_id, request, *, trusted=False, rerender=False, adapter_policy=None):
        """Compatibility MCP admission for an owner-scoped validated import."""
        from . import consumer
        trusted = trusted and not self.store.policy.public and not consumer.adapter_public(adapter_policy)
        self._protect('admission')
        with self._owner_import(owner), self.store.lock:
            normalized = consumer.normalize_request(request, trusted=trusted and not self.store.policy.public)
            # A new consumer operation is a new render, even for identical bytes.
            # Operation-key replay below still protects retries of that submission.
            rerender = True
            fingerprint = consumer.identity(normalized)
            replay = self.store.operation_replay(owner, normalized['request_key'], fingerprint, input_id=input_id)
            if replay:
                return replay
            replay = self.store.submission_replay(owner, input_id, normalized['request_key'], fingerprint, rerender=rerender)
            if replay:
                return self.store.remember_operation(owner, normalized['request_key'], replay,
                    input_id=input_id, client_hash=fingerprint)
            plan = self.consumer_preflight(owner, normalized, trusted=trusted)
            if not consumer.executable(plan['decision']):
                return consumer.non_admission(plan['decision'], 'configured')
            bundle = self.store.input(owner, input_id)
            if 'source_file' in bundle:
                raise JobError('Compatibility submission requires a validated import.', 'invalid_structure', 422)
            first = consumer.metadata(plan['decision'], request=plan['request'], fingerprint=plan['identity'],
                                      custom='style_doc' in plan['request'])
            try:
                exact = self.consumer_exact(plan['request'], first['preset_decision'], plan['public'], bundle)
                if not consumer.executable(exact):
                    return consumer.non_admission(exact, 'configured')
                options = prepare_options(consumer.render_options(plan['request']), bundle,
                    public=plan['public'], allow_animation=self.store.policy.allow_animation, timeout=self.store.policy.max_job_seconds)
                consumer.validate_bundle_controls(options, bundle)
            except JobError:
                raise
            except (ValueError, TypeError, core.RunnerError) as exc:
                raise JobError('Exact-context selection is invalid.', 'selection_invalid', 422) from exc
            metadata = consumer.metadata(exact, previous=first, phase='bound')
            result = self.store.submit(owner, input_id, options, normalized['request_key'],
                client_hash=plan['identity'], operation_key=normalized['request_key'], rerender=rerender,
                consumer_metadata=metadata)
            self.store.stage(result['job_id'], 'waiting_for_compute')
        self.wakeup.set()
        return result

    def admit_consumer_file(self, owner, data, suffix, name, converter, plan):
        """Internal adapter continuation after preflight; plan is never client JSON."""
        from . import consumer
        if 'replay' in plan:
            return plan['replay']
        if not consumer.executable(plan['decision']):
            raise JobError('Selection is not executable', 'selection_refused', 422)
        request = plan['request']
        return self.render_file(owner, data, suffix, name, converter, consumer.render_options(request),
            operation_key=request['request_key'], rerender=True, _consumer=plan)

    def render_file(self, owner, data, suffix, name, converter, options, *, idempotency_key=None,
                    operation_key=None, rerender=False, selection='(all)', _consumer=None):
        """Commit the existing queue's handle before conversion or compute."""
        self._protect('admission')
        from .spec import _check_options
        try:
            _check_options(options)
        except ValueError as exc:
            raise JobError(str(exc)) from exc
        if type(rerender) is not bool or not isinstance(selection, str) or not selection.strip() or '\x00' in selection:
            raise JobError('Invalid repeat or selection controls')
        for key in (idempotency_key, operation_key):
            if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 128):
                raise JobError('Operation and transport keys must contain 1 to 128 characters')
        if rerender and idempotency_key is None and operation_key is None:
            raise JobError('An explicit repeat requires a stable operation or idempotency key')
        if converter is None:
            raise JobError('The molecular converter is unavailable.', 'compute_unavailable', 503)
        if suffix not in {'.pse', '.cif', '.mmcif', '.pdb'}:
            raise JobError('This service does not support that file conversion.', 'unsupported', 422)
        if len(data) > self.store.policy.max_upload_bytes:
            raise JobError('File is too large.', 'too_large', 413)
        client_hash = _consumer['identity'] if _consumer is not None else self._client_hash(options, rerender=rerender)
        source_key = suffix + ':' + hashlib.sha256(data).hexdigest()
        operation_key = operation_key or idempotency_key
        if selection != '(all)':
            source_key += ':' + hashlib.sha256(selection.encode()).hexdigest()
        with self._imports_guard:
            if self.converter is not None and self.converter is not converter:
                raise JobError('Converter configuration changed; drain and restart the controller before changing runtime.', 'runtime_conflict', 409)
            self.converter = converter
        with self._owner_import(owner):
            existing = self.store.operation_replay(owner, operation_key, client_hash, source_key=source_key)
            if existing:
                return existing
            existing = None if rerender else self.store.file_replay(owner, source_key, client_hash)
            if existing:
                return self.store.remember_operation(owner, operation_key, existing, client_hash=client_hash, source_key=source_key)
            frozen = freeze_file_options(options)
            metadata = None
            if _consumer is not None:
                from . import consumer
                frozen['consumer_request'] = _consumer['request']
                frozen['consumer_public'] = _consumer['public']
                # Preserve fit-relevant identity in effective-render dedup too.
                frozen['consumer_identity'] = _consumer['identity']
                metadata = consumer.metadata(_consumer['decision'], request=_consumer['request'],
                    fingerprint=_consumer['identity'], custom='style_doc' in _consumer['request'])
            # Raw bytes are private temporary input, not a StructureBundle.
            # The dispatcher validates the conversion and resolves once before
            # entering any compute backend. Restart never repeats live work.
            source = self.store.add_input(owner, {'source_file': {
                'suffix': suffix, 'data': base64.b64encode(data).decode(), 'selection': selection}},
                name, source_key=source_key, raw_file=True)
            with self.store.lock:
                key = idempotency_key or operation_key or secrets.token_hex(16)
                replay = self.store.submission_replay(owner, source['input_id'], key, client_hash, rerender=rerender)
                job = self.store.remember_operation(owner, operation_key, replay, input_id=source['input_id'], client_hash=client_hash, source_key=source_key) if replay else self.store.submit(owner, source['input_id'], frozen, key,
                    client_hash=client_hash, rerender=rerender, operation_key=operation_key, consumer_metadata=metadata)
            self.wakeup.set()
            return job

    def submit(self, owner: str, input_id: str, options: dict, idempotency_key: str, *, rerender: bool = False, operation_key=None) -> dict:
        self._protect('admission')
        self.last_activity = time.time()
        if not isinstance(rerender, bool):
            raise JobError('rerender must be a boolean')
        client_hash = self._client_hash(options, rerender=rerender)
        # Do not let completion delete the input between replay lookup and
        # normalization. This lock contains no network or compute operations.
        with self.store.lock:
            replay = self.store.operation_replay(owner, operation_key, client_hash, input_id=input_id)
            if replay:
                return replay
            legacy_hash = hashlib.sha256(json.dumps(options, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            replay = self.store.submission_replay(owner, input_id, idempotency_key, client_hash, rerender=rerender, legacy_hash=legacy_hash if not rerender else None)
            if replay:
                return self.store.remember_operation(owner, operation_key, replay, input_id=input_id, client_hash=client_hash)
            payload = self.store.input(owner, input_id)
            try:
                if 'source_file' in payload:
                    from .spec import _check_options
                    try:
                        _check_options(options)
                    except ValueError as exc:
                        raise JobError(str(exc)) from exc
                    normalized = freeze_file_options(options)
                else:
                    normalized = prepare_options(options, payload, public=self.store.policy.public, allow_animation=self.store.policy.allow_animation, timeout=self.store.policy.max_job_seconds)
            except core.StyleValidationError as exc:
                raise JobError(str(exc)) from exc
            result = self.store.submit(owner, input_id, normalized, idempotency_key, client_hash=client_hash, rerender=rerender, operation_key=operation_key)
            if 'prepare_file' not in normalized:
                self.store.stage(result['job_id'], 'waiting_for_compute')
        self.wakeup.set()
        return result

    def _run(self):
        while not self.stopping.is_set():
            try:
                self.integrity.check()
                job = self.store.claim()
                if not job:
                    self.wakeup.wait(2)
                    self.wakeup.clear()
                    continue
                self._execute(job)
            except RestartRequired:
                self.stopping.wait(2)
            except Exception:
                log.exception("Job dispatcher failed; admission remains conservative")
                self.stopping.wait(2)

    def submit_resolved(self, controller, envelope):
        self._protect('admission')
        from .execution import validate_snapshot, scoped_owner
        try:
            context = {k: envelope[k] for k in ('controller_id', 'owner_id', 'operation_id')}
            if context['controller_id'] != controller:
                raise ValueError('Authenticated controller identity mismatch')
            if any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in context.values()):
                raise ValueError('Invalid scoped operation identity')
            p = self.store.policy
            snapshot = validate_snapshot({k: v for k, v in envelope.items() if k not in context},
                public=p.public, allow_animation=p.allow_animation, max_atoms=p.max_atoms if p.public else 10000000,
                max_bytes=p.max_upload_bytes, timeout=p.max_job_seconds)
        except (KeyError, ValueError, core.RunnerError) as exc:
            raise JobError(str(exc)) from exc
        owner = scoped_owner(controller, context['owner_id'])
        key = context['operation_id']
        fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self._owner_import(owner), self.store.lock:
            replay = self.store.operation_replay(owner, key, fingerprint)
            if replay:
                return replay
            source = self.import_payload(owner, snapshot['bundle'])
            result = self.store.submit(owner, source['input_id'], snapshot['request'], key,
                client_hash=fingerprint, rerender=True, operation_key=key)
            # Exact identity was checked above; no downstream resolution occurs.
            self.store.bind_execution(result['job_id'], snapshot)
        self.wakeup.set()
        return result

    def _execute(self, job: dict):
        started = time.monotonic()
        identifier = job["id"]
        compute_started = False
        from .telemetry import measure, record
        from .gateway import TransferPending
        from .execution import StoredSnapshotIntegrityError
        from . import consumer_records, consumer_errors
        recovering_child = bool(job.get('remote_id') or job.get('remote_phase'))
        sink = lambda timing: self.store.add_timings(identifier, [timing])
        initial_metadata = None
        try:
            self._protect('admission')
            with self.store.lock:
                initial_metadata = self.store._consumer_metadata(self.store.db.execute(
                    'SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone())
            request = json.loads(job["options"])
            payload = None if recovering_child else (json.loads(job['execution'])['bundle'] if job.get('execution') else self.store.input(job["owner"], job["input_id"]))
            if 'prepare_file' in request and not recovering_child:
                self.store.stage(identifier, 'conversion')
                if self.store.cancelled(identifier):
                    raise RuntimeError('Cancelled before conversion')
                self.store.reserve_conversion(job['owner'], job_id=identifier)
                raw = payload['source_file']
                if self.converter is None:
                    raise JobError('File converter unavailable after restart; execution was not retried', 'compute_unavailable', 503)
                kwargs = {'selection': raw['selection']} if raw['selection'] != '(all)' else {}
                self._protect('conversion')
                self.store.checkpoint(identifier, 'conversion', dispatched=True)
                with measure(sink, 'conversion', 'converter'):
                    payload = self.converter(base64.b64decode(raw['data'], validate=True), raw['suffix'], **kwargs)
                p = self.store.policy
                payload = validate_input(payload, max_atoms=p.max_atoms if p.public else 10000000, max_bytes=p.max_upload_bytes)
                from .spec import PresetDefinition
                captured = request.get('preset_definition')
                if 'style_doc' not in request['prepare_file'] and captured is None:
                    raise RuntimeError('Queued legacy file job lacks its preset snapshot; execution was not substituted')
                definition = PresetDefinition(**captured) if captured is not None else None
                consumer_metadata = None
                public = p.public
                if 'consumer_request' in request:
                    from . import consumer
                    frozen_consumer = request['consumer_request']
                    public = public or request['consumer_public']
                    try:
                        decision = self.consumer_exact(frozen_consumer, initial_metadata['preset_decision'], public, payload)
                    except (ValueError, TypeError, core.RunnerError) as exc:
                        failure = getattr(exc, 'code', None)
                        if failure not in {'catalog_changed', 'capability_unavailable'}:
                            failure = 'selection_invalid'
                        self.store.bind_consumer_decision(identifier, consumer.metadata(None,
                            previous=initial_metadata, phase='refused',
                            error=failure))
                        raise JobError('Exact-context selection validation failed before backend dispatch', failure, 422) from exc
                    if not consumer.executable(decision):
                        self.store.bind_consumer_decision(identifier, consumer.metadata(decision, previous=initial_metadata,
                            phase='refused', error='capability_unavailable' if consumer.refusal_category(decision) == 'capability_unavailable' else 'selection_refused'))
                        raise JobError('Exact-context selection refused before backend dispatch', 'selection_refused', 422)
                    consumer_metadata = consumer.metadata(decision, previous=initial_metadata, phase='bound')
                try:
                    request = prepare_options(request['prepare_file'], payload, public=public, allow_animation=p.allow_animation,
                                              timeout=p.max_job_seconds, preset_definition=definition)
                    if consumer_metadata is not None:
                        consumer.validate_bundle_controls(request, payload)
                except (JobError, core.RunnerError) as exc:
                    if consumer_metadata is not None:
                        self.store.bind_consumer_decision(identifier, consumer.metadata(None,
                            previous=initial_metadata, phase='refused',
                            error='color_follow_unavailable' if getattr(exc, 'code', None) == 'color_follow_unavailable' else 'resolution_invalid'))
                    raise
                self.store.bind_spec(identifier, request, payload, consumer_metadata=consumer_metadata)
                self.store.checkpoint(identifier, 'dispatch')
            elif not job.get('remote_phase'):
                sink(record('conversion', 'converter', status='skipped'))
            if self.store.cancelled(identifier) or self.stopping.is_set():
                raise RuntimeError('Cancelled before compute')
            from .execution import dispatch_request, validate_snapshot_for_execution, validate_stored_snapshot_integrity
            with self.store.lock:
                frozen = self.store.db.execute('SELECT execution FROM jobs WHERE id=?', (identifier,)).fetchone()[0]
            if not frozen:
                raise StoredSnapshotIntegrityError('Queued legacy job lacks its execution envelope')
            if recovering_child:
                try:
                    snapshot = validate_stored_snapshot_integrity(json.loads(frozen))
                except (ValueError, TypeError) as exc:
                    raise StoredSnapshotIntegrityError('Stored child execution requires recovery') from exc
            else:
                snapshot = validate_snapshot_for_execution(json.loads(frozen), public=self.store.policy.public, allow_animation=self.store.policy.allow_animation,
                    max_atoms=self.store.policy.max_atoms if self.store.policy.public else 10000000,
                    max_bytes=self.store.policy.max_upload_bytes, timeout=self.store.policy.max_job_seconds)
            request = dispatch_request(snapshot, 'controller', job['owner'], identifier)
            self._protect('transfer' if recovering_child else 'dispatch')
            self.store.stage(identifier, 'waiting_for_compute')
            self.store.checkpoint(identifier, 'transfer' if recovering_child else 'dispatch', dispatched=True)
            compute_started = True
            result = self.backend.render(request, identifier, lambda: self.stopping.is_set() or self.store.cancelled(identifier))
            if self.stopping.is_set():
                raise RuntimeError("Service stopped during render")
            if result.get("status") != "completed":
                raise RuntimeError("Renderer did not complete successfully")
            self.store.add_timings(identifier, result.get('timings', []))
            try:
                with measure(sink, 'artifact_preparation', 'controller', job.get('transfer_attempt', 0)+1):
                    artifacts = self._save_artifacts(identifier, result)
            except Exception as exc:
                with self.store.lock:
                    completed = self.store.db.execute('SELECT remote_completed_at FROM jobs WHERE id=?', (identifier,)).fetchone()[0]
                if completed is not None:
                    raise TransferPending('artifact_preparation_failed') from exc
                raise
            self.store.finish(identifier, result=artifacts, seconds=time.monotonic() - started)
            if self.store.cancelled(identifier):
                shutil.rmtree(self.store.artifacts / identifier, ignore_errors=True)
        except consumer_records.MetadataError:
            self.store.block(identifier, 'recovery_required', 'admitted')
        except StoredSnapshotIntegrityError:
            self.store.block(identifier, 'recovery_required', 'transfer' if recovering_child else 'dispatch')
        except RestartRequired as exc:
            # A worker's explicit pre-render rejection is safe to resume. An
            # unobserved transport failure is handled conservatively elsewhere.
            with self.store.lock:
                current = dict(self.store.db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone())
            has_child = bool(current['remote_id'] or current['remote_phase'])
            phase = 'transfer' if has_child else (current['resume_phase'] or 'admitted')
            condition = 'execution_unknown' if compute_started and not getattr(exc, 'before_execution', False) and not has_child else 'restart_required'
            if condition == 'restart_required':
                self.store.checkpoint(identifier, phase)
            self.store.block(identifier, condition, phase,
                diagnostic=from_exception(exc, before_execution=condition == 'restart_required'))
        except ExecutionUnknown as exc:
            self.store.block(identifier, 'execution_unknown', 'dispatch',
                diagnostic=from_exception(exc, before_execution=False))
        except EngineMismatch as exc:
            self.store.checkpoint(identifier, 'dispatch')
            self.store.block(identifier, 'compatibility_blocked', 'dispatch',
                diagnostic=from_exception(exc, before_execution=True))
        except TransferPending as exc:
            if self.store.cancelled(identifier):
                self.store.finish(identifier, error='Parent transfer cancelled', seconds=time.monotonic()-started)
            else:
                self.store.transfer_pending(identifier, exc.phase)
        except Exception as exc:
            with self.store.lock:
                completed = self.store.db.execute('SELECT remote_completed_at FROM jobs WHERE id=?', (identifier,)).fetchone()[0]
            if completed is not None and not self.store.cancelled(identifier):
                self.store.transfer_pending(identifier, 'artifact_preparation_failed')
                return
            message = "Render failed. Check structure, preset, and resource limits." if self.store.policy.public or initial_metadata is not None else str(exc)
            seconds = self.store.policy.max_job_seconds if getattr(self.backend, "paid", False) else time.monotonic() - started
            category = consumer_errors.from_exception(exc,
                default='render_failed' if compute_started else 'invalid_structure')['error_category']
            if initial_metadata is not None:
                import traceback
                frames = [(Path(f.filename).name, f.lineno, f.name) for f in traceback.extract_tb(exc.__traceback__)]
                log.warning('Consumer operation failed: category=%s job_id=%s type=%s frames=%s',
                    category, identifier, type(exc).__name__, frames)
                message = consumer_errors.MESSAGES[category]
                if category == 'cancelled':
                    self.store.cancel(job['owner'], identifier)
            else:
                log.exception("Render job %s failed", identifier)
            self.store.finish(identifier, error=message, seconds=seconds, compute_started=compute_started,
                              error_category=category)
            shutil.rmtree(self.store.artifacts / identifier, ignore_errors=True)
        finally:
            self.last_activity = time.time()

    def resume_transfer(self, owner, identifier):
        result = self.store.resume_transfer(owner, identifier)
        self.wakeup.set()
        return result

    def _save_artifacts(self, identifier: str, result: dict) -> dict:
        root = self.store.artifacts / identifier
        root.mkdir(mode=0o700, exist_ok=True)
        records = []
        from .artifacts import MEDIA, FIELDS
        from .animation import required_names
        for name, key in FIELDS.items():
            mime = MEDIA[name]
            if not result.get(key):
                continue
            data = base64.b64decode(result[key], validate=True)
            if key == "png_base64" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("Renderer returned invalid PNG bytes")
            if len(data) > 512 * 1024**2:
                raise RuntimeError("Render artifact exceeds 512 MiB limit")
            (root / name).write_bytes(data)
            records.append({"name": name, "mime_type": mime, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if not records or records[0]["name"] != "render.png":
            raise RuntimeError("Renderer returned no PNG")
        manifest = dict(result.get("manifest", {}))
        with self.store.lock:
            frozen = self.store.db.execute('SELECT execution FROM jobs WHERE id=?', (identifier,)).fetchone()[0]
        if frozen:
            snapshot = json.loads(frozen)
            manifest.update(bundle_sha256=snapshot['bundle_sha256'], render_spec=snapshot['request']['render_spec'])
            expected = required_names(snapshot['request']['style_doc'])
            if not set(expected).issubset({item['name'] for item in records}):
                raise RuntimeError('Renderer omitted requested animation artifacts')
        for key in ("chosen_structure_path", "temp_dir", "manifest_path"):
            manifest.pop(key, None)
        for key in ('timings', 'timings_seconds', 'operation_stages', 'runtime_seconds'):
            manifest.pop(key, None)
        manifest.update(job_id=identifier, artifacts=list(records))
        data = json.dumps(manifest, indent=2).encode()
        (root / "manifest.json").write_bytes(data)
        records.append({"name": "manifest.json", "mime_type": "application/json", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        with self.store.lock:
            if self.store.policy.public:
                existing_bytes = self.store.storage_bytes(self.store.db)
                if existing_bytes + sum(item["size"] for item in records) > self.store.policy.max_storage_bytes:
                    raise RuntimeError("Artifact storage limit exceeded")
            try:
                self.store.sync()
            except BaseException:
                self.store.healthy = False
                raise
        return {"job_id": identifier, "artifacts": records, "manifest": manifest, "bytes": sum(item["size"] for item in records)}

    def artifact(self, owner: str, identifier: str, name: str, *, _inventory=None) -> tuple[Path, str]:
        result = self.store.result(owner, identifier, inventory=True) if _inventory is None else _inventory
        record = next((item for item in result["artifacts"] if item["name"] == name), None)
        from .artifacts import MEDIA
        if record is None or name not in MEDIA:
            if any(a['name'] == name for a in result['expected_artifacts']):
                raise JobError('Artifact unavailable.', 'artifact_unavailable', 410)
            raise JobError("Artifact not found.", "not_found", 404)
        if record.get('expires_at') is not None and record['expires_at'] <= self.store.clock():
            raise JobError('Artifact expired.', 'artifact_expired', 410)
        from .artifacts import valid_copy
        path = valid_copy(self.store.db, identifier, record)
        if path is None:
            raise JobError('Temporary download is no longer available.', 'expired', 410)
        return path, record["mime_type"]
