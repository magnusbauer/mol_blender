"""Persistent Blender worker process management for render server."""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import threading
import time
import tempfile
from blender_render.engine import EngineMismatch, ExecutionUnknown
from pathlib import Path
from typing import Any, Callable, TextIO

from blender_render import core as _bridge_runner
from blender_render.worker_ipc import IPCUnavailable, WorkerIPC
from blender_render import compatibility as diagnostics
from blender_render.config import blender_thread_arguments


class WorkerError(RuntimeError):
    """Raised for worker startup/communication failures."""
    def __init__(self, *args, diagnostic=None):
        super().__init__(*args)
        self.diagnostic = diagnostics.sanitize(diagnostic)


class WorkerNotReady(WorkerError, EngineMismatch):
    """Known pre-execution rejection, safe to retain for runtime repair."""


class PersistentBlenderWorker:
    """Manages one persistent Blender process that accepts render jobs over unix socket."""

    def __init__(
        self,
        *,
        root_dir: Path,
        blender_exe: str,
        custom_script: Path,
        use_apptainer: bool,
        apptainer_image: str,
        worker_start_timeout: float = 60.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir).expanduser()
        if not self.root_dir.is_absolute():
            self.root_dir = (Path.cwd() / self.root_dir).absolute()

        self.blender_exe = str(blender_exe or "").strip() or _bridge_runner.DEFAULT_BLENDER_EXE
        self.custom_script = Path(custom_script).expanduser()
        if not self.custom_script.is_absolute():
            self.custom_script = (Path.cwd() / self.custom_script).absolute()

        self.use_apptainer = bool(use_apptainer)
        self.apptainer_image = str(apptainer_image or "").strip()
        self.worker_start_timeout = max(5.0, float(worker_start_timeout))
        from .config import load_config, process_thread_count
        # Freeze process configuration for this worker's lifetime, never per job.
        self.process_threads = process_thread_count(load_config().get("process_threads"))

        self.socket_path: Path | None = None
        self._ipc: WorkerIPC | None = None
        self.stdout_log = self.root_dir / "worker.stdout.log"
        self.stderr_log = self.root_dir / "worker.stderr.log"

        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stdout_handle: TextIO | None = None
        self._stderr_handle: TextIO | None = None
        self._last_command: list[str] = []
        self._logger = logger
        self.observation = None
        self._capture = None
        self._captured_package = None

    def _log(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger(str(message))
        except Exception:
            return

    def _daemon_script_path(self) -> Path:
        if self._captured_package is None:
            raise WorkerError('Worker package has not been captured')
        return self._captured_package/'blender_worker_daemon.py'

    def _resolve_blender_executable(self) -> str:
        blender = str(self.blender_exe)
        if self.use_apptainer:
            return _bridge_runner._normalize_apptainer_blender_executable(blender)
        if _bridge_runner._blender_executable_exists(blender):
            return blender
        detected = _bridge_runner._detect_host_blender_executable()
        return detected or blender

    def _build_command(self) -> list[str]:
        if self.socket_path is None or self._ipc is None:
            raise WorkerNotReady('Worker IPC has not been created')
        daemon_script = self._daemon_script_path()
        if not daemon_script.is_file():
            raise WorkerError(f"Worker daemon script not found: {daemon_script}")
        custom_script = self._captured_package/'blender_custom_render.py'

        blender_exe = self._resolve_blender_executable()
        command = [
            blender_exe,
            *blender_thread_arguments(self.process_threads),
            "--background",
            "--python-use-system-env",
            "--python",
            str(daemon_script),
            "--",
            "--socket",
            str(self.socket_path),
            "--custom-script",
            str(custom_script),
            '--ccd-root',
            str(self._captured_package.parent),
        ]

        if self.use_apptainer:
            if not self.apptainer_image:
                raise WorkerError("Apptainer mode enabled but apptainer_image is empty.")
            apptainer_exe = _bridge_runner._resolve_apptainer_executable()
            bind_paths = [
                self._ipc.directory,
                self.root_dir,
                daemon_script.parent.parent,
                custom_script.parent,
                Path.cwd(),
                Path.home(),
            ]
            bind_args = _bridge_runner._apptainer_bind_args(bind_paths)
            command = [
                apptainer_exe,
                "exec",
                "--nv",
                *bind_args,
                str(Path(self.apptainer_image).expanduser()),
            ] + command
        return command

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        from blender_render.config import load_config, config_root
        from blender_render.config import process_thread_environment
        env.update(process_thread_environment(self.process_threads, apptainer=self.use_apptainer))
        runtime_site = env.get("BLENDER_RENDER_SITE") or load_config().get("runtime_site", str(config_root() / "runtime" / "site"))
        env["PYTHONPATH"] = str(self._captured_package.parent) + os.pathsep + runtime_site + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        if self.use_apptainer:
            env["APPTAINERENV_PYTHONPATH"] = str(self._captured_package.parent) + ":/opt/runtime/site:/opt"
            env["APPTAINERENV_BLENDER_RENDER_SITE"] = "/opt/runtime/site"
        env.setdefault("PRESS_CYCLES_DEVICE", "GPU")
        env.setdefault("PRESS_CYCLES_COMPUTE_TYPE", "AUTO")
        env.setdefault("PRESS_PRINT_DEVICE_INFO", "1")
        env.setdefault("PRESS_REQUIRE_GPU", "0")
        return env

    def _close_logs_locked(self) -> None:
        for handle_name in ("_stdout_handle", "_stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                try:
                    handle.flush()
                except Exception:
                    pass
                try:
                    handle.close()
                except Exception:
                    pass
            setattr(self, handle_name, None)

    def _send_request_locked(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        if self.socket_path is None or not self.socket_path.exists():
            raise WorkerError(f"Worker socket does not exist: {self.socket_path}")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(max(1.0, float(timeout)))
        try:
            sock.connect(str(self.socket_path))
            sock.sendall((json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
            try:
                sock.shutdown(socket.SHUT_WR)
            except Exception:
                pass

            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        except socket.timeout as exc:
            raise WorkerError("Timed out while communicating with Blender worker.") from exc
        except OSError as exc:
            raise WorkerError(f"Worker socket communication failed: {exc}") from exc
        finally:
            try:
                sock.close()
            except Exception:
                pass

        raw = b"".join(chunks).strip()
        if not raw:
            raise WorkerError("Worker returned an empty response.")
        if b"\n" in raw:
            raw = raw.split(b"\n", 1)[0].strip()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise WorkerError("Worker returned invalid JSON response.") from exc
        if not isinstance(parsed, dict):
            raise WorkerError("Worker response must be a JSON object.")
        return parsed

    def _diagnostic(self, value=None, *, reason=None, phase='readiness', before_execution=True):
        result = (diagnostics.make(reason, phase=phase, component='worker', before_execution=before_execution)
                  if reason else diagnostics.sanitize(value))
        refs = {ref['name']:ref for ref in result['log_refs']}
        for name, path in (('worker.stdout.log', self.stdout_log), ('worker.stderr.log', self.stderr_log)):
            try:
                exists = path.is_file() and not path.is_symlink()
            except OSError:
                exists = None
            refs[name] = {'name':name, 'exists':exists}
        result['log_refs'] = list(refs.values())
        return diagnostics.sanitize(result)

    def _wait_until_ready_locked(self, *, timeout: float) -> None:
        deadline = time.time() + max(1.0, float(timeout))
        last_error = ""
        next_log_at = time.time()
        while time.time() < deadline:
            process = self._process
            if process is None:
                raise WorkerError("Worker process object is missing during startup.", diagnostic=self._diagnostic(
                    reason='worker_startup_failed', phase='startup'))
            exit_code = process.poll()
            if exit_code is not None:
                raise ExecutionUnknown(
                    "Blender worker exited during startup. "
                    f"exit_code={exit_code} stdout_log={self.stdout_log} stderr_log={self.stderr_log}",
                    diagnostic={**self._diagnostic(reason='worker_startup_failed', phase='startup', before_execution=False),
                                'observed':{'exit_code':exit_code}}
                )
            if self.socket_path.exists():
                try:
                    response = self._send_request_locked({"action": "ping"}, timeout=2.0)
                    if response.get('condition') in {'restart_required','compatibility_blocked'} or response.get('runtime', {}).get('verified') is False:
                        raise WorkerNotReady('Worker runtime/integrity unavailable; explicit setup or restart required',
                            diagnostic=self._diagnostic(response.get('compatibility_diagnostic')))
                    if str(response.get("status", "")).strip().lower() == "ready":
                        self._log("worker-ready")
                        return
                    last_error = str(response)
                except WorkerNotReady:
                    raise
                except Exception as exc:  # pragma: no cover - startup race path
                    last_error = str(exc)
            now = time.time()
            if now >= next_log_at:
                remaining = max(0.0, deadline - now)
                self._log(
                    "worker-startup-wait "
                    f"remaining={remaining:.1f}s socket_exists={int(self.socket_path.exists())} "
                    f"last_error={last_error or '<none>'}"
                )
                next_log_at = now + 2.0
            time.sleep(0.2)

        raise WorkerError(
            "Timed out waiting for Blender worker readiness. "
            f"command={' '.join(shlex.quote(p) for p in self._last_command)} "
            f"stdout_log={self.stdout_log} stderr_log={self.stderr_log}"
            + (f" last_error={last_error}" if last_error else ""),
            diagnostic=self._diagnostic(reason='worker_readiness_failed')
        )

    def _start_locked(self) -> None:
        from blender_render.engine import protect
        seal = protect('startup')
        self._stop_locked()
        try:
            try:
                self._ipc = WorkerIPC()
            except (IPCUnavailable, OSError) as exc:
                raise WorkerNotReady('Worker IPC unavailable; explicit runtime storage repair required',
                    diagnostic=self._diagnostic(getattr(exc, 'diagnostic', None),
                        reason=None if isinstance(exc, IPCUnavailable) else 'worker_ipc_unavailable', phase='startup')) from exc
            self.socket_path = self._ipc.socket_path
            self.root_dir.mkdir(parents=True, exist_ok=True)
            if self.custom_script.resolve() != seal.root/'blender_custom_render.py':
                raise WorkerError('Renderer script must belong to the sealed engine')
            self._capture = tempfile.TemporaryDirectory(prefix='engine-', dir=self.root_dir)
            self._captured_package = seal.capture(Path(self._capture.name)/'blender_render')
            self._stdout_handle = self.stdout_log.open("a", encoding="utf-8")
            self._stderr_handle = self.stderr_log.open("a", encoding="utf-8")
            command = self._build_command()
            self._last_command = list(command)
            self._log("worker-start " + " ".join(shlex.quote(part) for part in command))
            env = self._subprocess_env()
            protect('startup')
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=self._stdout_handle,
                    stderr=self._stderr_handle,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    cwd=str(self._captured_package.parent),
                    env=env,
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                missing = exc.filename or command[0]
                raise WorkerNotReady(f"Failed to launch Blender worker. Missing executable: {missing}",
                    diagnostic=self._diagnostic(reason='worker_startup_failed', phase='startup')) from exc
            self._wait_until_ready_locked(timeout=self.worker_start_timeout)
        except BaseException:
            self._stop_locked()
            raise

    def _stop_locked(self) -> None:
        process = self._process
        if process is None:
            self._release_resources_locked()
            return

        if process.poll() is None:
            self._log(f"worker-stop pid={process.pid}")
            try:
                self._send_request_locked({"action": "shutdown"}, timeout=3.0)
            except Exception:
                pass
            try:
                process.wait(timeout=5.0)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
                try:
                    process.wait(timeout=3.0)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

        while process.poll() is None:
            # A killed process is still alive until wait confirms its exit.
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._log('worker-exit-wait admission-retained')
        self._process = None
        self._release_resources_locked()

    def _release_resources_locked(self):
        # Only callers that observed child exit (or never launched) may release.
        try:
            if self._ipc is not None:
                self._ipc.close()
            self._ipc = None
            self.socket_path = None
        finally:
            try:
                self._discard_capture()
            finally:
                self._close_logs_locked()

    def _discard_capture(self):
        if self._capture is not None:
            self._capture.cleanup()
        self._capture = self._captured_package = None

    def _ensure_started_locked(self) -> None:
        process = self._process
        if process is not None and process.poll() is None and self.socket_path is not None and self.socket_path.exists():
            try:
                response = self._send_request_locked({"action": "ping"}, timeout=2.0)
                if str(response.get("status", "")).strip().lower() == "ready":
                    return
            except Exception:
                pass
        self._log("worker-not-ready restarting")
        self._stop_locked()
        self._start_locked()

    def ensure_started(self) -> None:
        with self._lock:
            self._ensure_started_locked()

    def _negotiate_locked(self, expected, requirements):
        from blender_render.engine import require_compatible, protect, RestartRequired
        protect("startup")
        for attempt in range(2):
            observed = self._send_request_locked({'action': 'ping'}, timeout=2)
            self.observation = observed
            try:
                if observed.get('condition') == 'restart_required':
                    raise RestartRequired('restart_required: worker integrity changed')
                require_compatible(expected, observed.get('engine'), requirements)
                if not observed.get('runtime', {}).get('verified'):
                    from blender_render.engine import RuntimeUnavailable
                    raise RuntimeUnavailable('Worker runtime missing or incompatible; explicit setup required',
                        diagnostic=observed.get('compatibility_diagnostic') or diagnostics.runtime_diagnostic(
                            observed.get('runtime', {}), expected.get('runtime_requirements', {})))
                return
            except ValueError as exc:
                if attempt:
                    raise WorkerNotReady(str(exc), diagnostic=self._diagnostic(getattr(exc, 'diagnostic', None))) from exc
                self._stop_locked()
                self._start_locked()

    def stop(self) -> None:
        self.interrupt()
        with self._lock:
            self._stop_locked()

    def interrupt(self) -> None:
        """Interrupt an active job without waiting for the render socket lock."""
        process = self._process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def submit_render(
        self,
        *,
        structure_path: Path,
        view_json: Path,
        style_json: Path,
        output_png: Path,
        run_manifest: Path,
        timeout: float,
        progress=None,
        expected_engine=None,
        required_capabilities=(),
    ) -> dict[str, Any]:
        request_payload = {
            "action": "render_job",
            "structure_path": str(Path(structure_path).expanduser()),
            "view_json": str(Path(view_json).expanduser()),
            "style_json": str(Path(style_json).expanduser()),
            "output_png": str(Path(output_png).expanduser()),
            "run_manifest": str(Path(run_manifest).expanduser()),
        }

        from blender_render.engine import protect, RestartRequired
        protect('startup')
        with self._lock:
            startup_started = time.monotonic()
            was_warm = self._process is not None and self._process.poll() is None
            self._ensure_started_locked()
            from blender_render.engine import engine_identity
            expected_engine = expected_engine or engine_identity()
            self._negotiate_locked(expected_engine, required_capabilities)
            from blender_render.telemetry import record
            startup_timing = record('startup', 'worker', time.monotonic()-startup_started, 'cached' if was_warm else 'executed')
            request_payload['expected_engine'] = expected_engine
            request_payload['required_capabilities'] = list(required_capabilities)
            from blender_render.operation_progress import read_stages, stage_path
            stage_path(run_manifest).unlink(missing_ok=True)
            completed = threading.Event()
            seen = 0
            def report():
                nonlocal seen
                if progress:
                    stages = read_stages(run_manifest)
                    for item in stages[seen:]:
                        progress(item['stage'])
                    seen = len(stages)
            def watch():
                while not completed.wait(.1):
                    report()
            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            self._log(
                "worker-render-submit "
                f"structure={structure_path} output={output_png} timeout={float(timeout):.1f}s"
            )
            try:
                protect('dispatch')
                response = self._send_request_locked(request_payload, timeout=timeout)
            except Exception as exc:
                # An ambiguous result must never replay paid work. The next
                # distinct job starts a fresh worker after this one is stopped.
                self.interrupt()
                self._stop_locked()
                if isinstance(exc, RestartRequired):
                    exc.before_execution = True
                    raise
                raise ExecutionUnknown('Worker execution outcome unknown; no automatic replay',
                    diagnostic=self._diagnostic(diagnostics.from_exception(exc, before_execution=False))) from exc
            finally:
                completed.set()
                watcher.join()
                report()

        status = str(response.get("status", "")).strip().lower()
        response['timings'] = [startup_timing, *response.get('timings', [])]
        if response.get('condition') == 'restart_required':
            error = RestartRequired('restart_required: worker rejected render before execution',
                diagnostic=self._diagnostic(response.get('compatibility_diagnostic')))
            error.before_execution = True
            raise error
        if response.get('condition') == 'compatibility_blocked':
            raise WorkerNotReady('Worker refused execution; explicit runtime setup required',
                diagnostic=self._diagnostic(response.get('compatibility_diagnostic')))
        if response.get('condition') == 'execution_unknown':
            raise ExecutionUnknown('Worker outcome unknown; no automatic replay',
                diagnostic=self._diagnostic(response.get('compatibility_diagnostic')))
        if status != "completed":
            message = str(response.get("error", "")).strip() or f"Worker returned status={status or '<unknown>'}"
            raise WorkerError(message)
        self._log(
            "worker-render-complete "
            f"output={response.get('output_png', '')} runtime={response.get('runtime_seconds', 0.0)}"
        )
        return response

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            running = bool(process is not None and process.poll() is None)
            pid = int(process.pid) if running and process is not None else 0
            return {
                "running": running,
                "pid": pid,
                "socket_path": str(self.socket_path) if running and self.socket_path is not None else None,
                "stdout_log": str(self.stdout_log),
                "stderr_log": str(self.stderr_log),
                "last_command": list(self._last_command),
                "use_apptainer": bool(self.use_apptainer),
                "apptainer_image": str(self.apptainer_image),
            }
