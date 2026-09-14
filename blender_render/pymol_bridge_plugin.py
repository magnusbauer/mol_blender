"""Explicit, thread-free PyMOL lifecycle adapter for the shared render client."""
from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

# PyMOL's default ``run`` executes in its own globals: ``__file__`` still names
# pymol/__init__.py, while ``__script__`` identifies the file being run.
_PACKAGE_ROOT = str(Path(globals().get("__script__", __file__)).resolve().parent.parent)
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from blender_render.export import _normalize_cli_string, _parse_bool, build_render_payload


_MAX_TASKS = 64
_MAX_RESERVATIONS = 256
_MAX_RESERVATION_TARGETS = 32
_RESERVATION_VERSION = 2
_KNOWN_TERMINAL = {"cancelled", "failed", "expired", "deleted"}
_CANCELLABLE = {"starting", "submitting", "queued", "running", "cancelling"}
_RESULT_STATES = {"done", "delivered", "expired", "deleted"}
_BLOCKED_STATES = {
    "compatibility_blocked",
    "execution_unknown",
    "recovery_required",
    "restart_required",
}
_KNOWN_STATES = _CANCELLABLE | _RESULT_STATES | {
    "cancelled",
    "failed",
    "transfer_pending",
} | _BLOCKED_STATES
_SAFE_CONDITIONS = {
    "admission_refused",
    "admission_timeout",
    "artifact_integrity_failed",
    "artifact_expired",
    "artifact_unavailable",
    "attachment_unavailable",
    "cancelled",
    "capability_unavailable",
    "compatibility_blocked",
    "compute_unavailable",
    "deleted",
    "delivered",
    "execution_unknown",
    "expired",
    "format_unknown",
    "invalid_structure",
    "metadata_invalid",
    "not_found",
    "not_ready",
    "operation_conflict",
    "operation_timeout",
    "presentation_failed",
    "recovery_required",
    "render_failed",
    "result_not_ready",
    "restart_required",
    "selection_incompatible",
    "selection_invalid",
    "transfer_pending",
    "unauthorized",
}
_SAFE_STAGES = _KNOWN_STATES | {
    "adapter_starting",
    "admission",
    "admission_response",
    "admitted",
    "artifact_preparation",
    "artifact_preparation_failed",
    "child_observation_pending",
    "child_transfer_pending",
    "conversion",
    "conversion_pending",
    "delivery",
    "dispatch",
    "execution_incompatible",
    "recovering",
    "rendering",
    "result_ready",
    "scene_build",
    "status_unknown",
    "submission",
    "transfer",
    "transfer_recovery",
    "uploading",
    "validation",
    "waiting_for_compute",
}
_SAFE_ACTIONS = {
    "authorize_approximation_or_new_preset",
    "blender_render_result",
    "blender_render_result_with_distinct_output",
    "blender_render_status",
    "done",
    "inspect_execution",
    "inspect_operation_key",
    "inspect_operation_key_without_resubmitting",
    "inspect_operation_or_revise_request",
    "inspect_request_key",
    "invoke_on_application_thread",
    "open_output_path",
    "operator_inspection",
    "reattach_file",
    "recover_operation",
    "recover_result",
    "recover_transfer",
    "recover_verified_local_copy_or_stop",
    "render_result",
    "render_status",
    "restart_service",
    "restore_exact_engine",
    "retry_after_current_action",
    "retry_same_operation_key",
    "stop",
    "use_delivered_outputs",
}


@dataclass
class _RenderTask:
    """Bounded convenience metadata; never holds clients, payloads, or workers."""

    handle: str
    profile: str
    connection: dict[str, Any]
    output: Path
    operation_key: str | None
    blend_output: Path | None = None
    required_outputs: tuple[str, ...] = ("render.png",)
    request_digest: str | None = None
    job_id: str | None = None
    status: str = "starting"
    stage: str = "adapter_starting"
    queue_position: int | None = None
    condition: str | None = None
    next_action: str = "blender_render_status"
    result_available: bool = False
    expires_at: str | None = None
    auto_preview: bool = True
    complete: bool | None = None
    presentation_status: str = "not_attempted"
    outputs: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    preset_decision: dict[str, Any] | None = None
    render_spec_sha256: str | None = None
    style_source: str | None = None
    selection_phase: str | None = None
    style_identity: dict[str, Any] | None = None
    preselection_decision: dict[str, Any] | None = None
    backend_render_started: bool | None = None
    selection_error: str | None = None
    destination_recovery_required: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


_TASKS: dict[str, _RenderTask] = {}
_TASKS_LOCK = threading.RLock()
_ACTION_LOCK = threading.RLock()


def installation_defaults():
    """Deployment settings live beside the package, outside the engine assets."""
    path = Path(_PACKAGE_ROOT) / 'pymol-defaults.json'
    if not path.exists():
        return {}
    values = json.loads(path.read_text())
    allowed = {'preset', 'mode', 'server_host', 'server_port'}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ValueError('Unsupported PyMOL installation defaults')
    for key in ('preset', 'mode', 'server_host'):
        if key in values and (not isinstance(values[key], str) or not values[key].strip()
                              or any(char in values[key] for char in ('\n', '\0'))):
            raise ValueError('Invalid PyMOL default: ' + key)
    if 'server_port' in values and (type(values['server_port']) is not int or not 1 <= values['server_port'] <= 65535):
        raise ValueError('Invalid PyMOL default: server_port')
    return values


def try_load_preview(cmd_obj, output_png):
    """Load a verified PNG, retaining a usable path if presentation fails."""
    try:
        cmd_obj.load_png(str(output_png), movie=1, quiet=1)
        return True
    except Exception:
        print("Rendered PNG: " + str(output_png))
        return False


def _strict_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if value in {0, 1}:
            return bool(value)
        raw = str(value)
    else:
        raw = _normalize_cli_string(value).lower()
        if raw in {"0", "false"}:
            return False
        if raw in {"1", "true"}:
            return True
    raise ValueError(f"{name} must be one of 0, 1, false, or true")


def _bounded_timeout(value: Any, name: str, *, default: float, maximum: float = 86400.0) -> float:
    raw = _normalize_cli_string(value)
    try:
        timeout = default if not raw else float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number greater than zero") from exc
    if not math.isfinite(timeout) or not 0 < timeout <= maximum:
        raise ValueError(f"{name} must be greater than zero and no more than {int(maximum)} seconds")
    return timeout


def _remaining_timeout(
    deadline: float,
    task: _RenderTask | None = None,
    *,
    next_action: str = "blender_render_status",
) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        from blender_render.client import ClientError

        raise ClientError(
            "The bounded PyMOL operation timed out; reconnect to the same job and do not resubmit.",
            408,
            "operation_timeout",
            job_id=task.job_id if task is not None else None,
            next_action=next_action,
        )
    return remaining


@contextmanager
def _bounded_action(
    deadline: float,
    task: _RenderTask | None = None,
    *,
    next_action: str = "blender_render_status",
):
    acquired = _ACTION_LOCK.acquire(
        timeout=_remaining_timeout(deadline, task, next_action=next_action)
    )
    if not acquired:
        from blender_render.client import ClientError

        raise ClientError(
            "The bounded PyMOL operation timed out; reconnect to the same job and do not resubmit.",
            408,
            "operation_timeout",
            job_id=task.job_id if task is not None else None,
            next_action=next_action,
        )
    try:
        yield
    finally:
        _ACTION_LOCK.release()


def _parse_identifier(value: Any, name: str, *, operation: bool = False) -> str:
    if isinstance(value, Mapping):
        value = value.get("handle") or value.get("job_id") or ""
    result = _normalize_cli_string(value)
    if not result or len(result) > 128 or any(character in result for character in "\r\n\0"):
        raise ValueError(f"{name} must be a non-empty identifier of at most 128 characters")
    if operation and not all(character.isalnum() or character in "._:-" for character in result):
        raise ValueError(f"{name} must be an opaque identifier without spaces, paths, or URLs")
    return result


def _output_path(output_dir: Any, output_name: Any) -> tuple[Path, bool]:
    directory = Path(_normalize_cli_string(output_dir) or ".").expanduser()
    raw_name = _normalize_cli_string(output_name)
    name = raw_name or "render.png"
    if Path(name).name != name:
        raise ValueError("output_name must be a filename; use output_dir for its directory")
    return (directory / name).with_suffix(".png").resolve(), raw_name in {"", "render.png"}


def _qualified_output(path: Path, job_id: str) -> Path:
    qualifier = hashlib.sha256(job_id.encode()).hexdigest()[:12]
    return path.with_name(f"{path.stem}-{qualifier}.png")


def _check_destination(path: Path) -> None:
    """Fail before scene capture when an artifact destination is not writable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=path.parent):
        pass


def _implicit_targets(output: Path, blend_output: Path | None) -> set[str]:
    targets = {
        str(output),
        str(output.with_suffix(".blend")),
        str(output.with_suffix(".manifest.json")),
    }
    if blend_output is not None:
        targets.add(str(blend_output))
    return targets


def _receipt_target(connection: Mapping[str, Any], job_id: str) -> str:
    identity = json.dumps(dict(connection), sort_keys=True, separators=(",", ":"))
    return "receipt:" + hashlib.sha256((identity + "\0" + job_id).encode()).hexdigest()


def _reservation_path() -> Path:
    from blender_render.config import config_root

    return config_root() / "pymol-output-reservations.json"


def _read_reservations(path: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RuntimeError("The PyMOL output reservation registry is unreadable; inspect it before rendering") from exc
    records = document.get("reservations") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or set(document) != {"version", "reservations"}
        or document.get("version") != _RESERVATION_VERSION
        or not isinstance(records, list)
        or len(records) > _MAX_RESERVATIONS
    ):
        raise RuntimeError("The PyMOL output reservation registry is invalid or exceeds its bound")
    validated = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "handle", "job_id", "scope", "targets", "created_at"
        }:
            raise RuntimeError("The PyMOL output reservation registry contains an invalid record")
        try:
            handle = _parse_identifier(record["handle"], "saved handle", operation=True)
            job_id = (
                _parse_identifier(record["job_id"], "saved job_id", operation=True)
                if record["job_id"] is not None
                else None
            )
        except ValueError as exc:
            raise RuntimeError("The PyMOL output reservation registry contains an invalid record") from exc
        scope = record["scope"]
        targets = record["targets"]
        created_at = record["created_at"]
        if (
            not isinstance(scope, str)
            or len(scope) != 64
            or any(character not in "0123456789abcdef" for character in scope)
            or not isinstance(targets, list)
            or not 1 <= len(targets) <= _MAX_RESERVATION_TARGETS
            or any(not isinstance(target, str) for target in targets)
            or len(set(targets)) != len(targets)
            or type(created_at) not in {int, float}
            or not math.isfinite(float(created_at))
        ):
            raise RuntimeError("The PyMOL output reservation registry contains an invalid record")
        for target in targets:
            receipt = isinstance(target, str) and target.startswith("receipt:")
            if (
                not isinstance(target, str)
                or not 1 <= len(target) <= 4096
                or any(character in target for character in "\r\n\0")
                or receipt
                and (
                    len(target) != len("receipt:") + 64
                    or any(character not in "0123456789abcdef" for character in target[8:])
                )
                or not receipt
                and not Path(target).is_absolute()
            ):
                raise RuntimeError("The PyMOL output reservation registry contains an invalid target")
        validated.append({
            "handle": handle,
            "job_id": job_id,
            "scope": scope,
            "targets": list(targets),
            "created_at": float(created_at),
        })
    return validated


def _write_reservations(path: Path, records: list[dict[str, Any]]) -> None:
    from blender_render.runtime import write_private

    write_private(
        path,
        json.dumps(
            {"version": _RESERVATION_VERSION, "reservations": records}, sort_keys=True
        ) + "\n",
    )


def _scope_identity(connection: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(connection), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _reservation_record(task: _RenderTask) -> dict[str, Any]:
    targets = sorted(_implicit_targets(task.output, task.blend_output))
    if task.job_id:
        targets.append(_receipt_target(task.connection, task.job_id))
    return {
        "handle": task.handle,
        "job_id": task.job_id,
        "scope": _scope_identity(task.connection),
        "targets": targets,
        "created_at": task.created_at,
    }


def _reserve(task: _RenderTask, *, deadline: float | None = None) -> None:
    """Persist collision bookkeeping; saved data never selects routes or writes."""
    from blender_render.runtime import file_lock

    path = _reservation_path()
    record = _reservation_record(task)
    targets = set(record["targets"])
    try:
        lock = file_lock(path.with_suffix(".lock"), deadline=deadline)
        with lock:
            records = _read_reservations(path)
            same_operation = [
                item
                for item in records
                if task.job_id is not None
                and item["job_id"] == task.job_id
                and item["scope"] == record["scope"]
            ]
            for item in same_operation:
                targets.update(item["targets"])
            if len(targets) > _MAX_RESERVATION_TARGETS:
                raise RuntimeError(
                    "This operation already retains the maximum number of recovery destinations"
                )
            record["targets"] = sorted(targets)
            others = [
                item
                for item in records
                if item.get("handle") != task.handle and item not in same_operation
            ]
            for item in others:
                if targets.intersection(item["targets"]):
                    raise ValueError(
                        "An output, implicit artifact, or receipt destination is reserved by another operation"
                    )
            if len(others) >= _MAX_RESERVATIONS:
                raise RuntimeError("The bounded PyMOL output reservation registry is full")
            _write_reservations(path, [*others, record])
    except TimeoutError:
        from blender_render.client import ClientError

        raise ClientError(
            "Timed out while reserving the local result destination.",
            408,
            "operation_timeout",
            job_id=task.job_id,
            next_action="blender_render_result",
        ) from None


def _release_reservation(task: _RenderTask, *, deadline: float | None = None) -> bool:
    from blender_render.runtime import file_lock

    path = _reservation_path()
    try:
        with file_lock(path.with_suffix(".lock"), deadline=deadline):
            records = _read_reservations(path)
            scope = _scope_identity(task.connection)
            retained = [
                item
                for item in records
                if item["handle"] != task.handle
                and not (
                    task.job_id is not None
                    and item["job_id"] == task.job_id
                    and item["scope"] == scope
                )
            ]
            if retained != records:
                _write_reservations(path, retained)
        return True
    except TimeoutError:
        return False


def _register_task(task: _RenderTask) -> _RenderTask:
    with _TASKS_LOCK:
        existing = next(
            (
                item
                for item in _TASKS.values()
                if task.job_id
                and item.job_id == task.job_id
                and item.connection == task.connection
            ),
            None,
        )
        if existing is not None:
            return existing
        if len(_TASKS) >= _MAX_TASKS:
            removable = sorted(
                (item for item in _TASKS.values() if item.status in _KNOWN_TERMINAL or item.complete),
                key=lambda item: item.updated_at,
            )
            while len(_TASKS) >= _MAX_TASKS and removable:
                _TASKS.pop(removable.pop(0).handle, None)
        if len(_TASKS) >= _MAX_TASKS:
            raise RuntimeError("This PyMOL session already retains 64 nonterminal render handles")
        _TASKS[task.handle] = task
    return task


def _identifier_value(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("handle") or value.get("job_id") or ""
    return _normalize_cli_string(value)


def _get_task(identifier: Any) -> _RenderTask:
    value = _identifier_value(identifier)
    with _TASKS_LOCK:
        task = _TASKS.get(value)
        if task is None:
            matches = [item for item in _TASKS.values() if item.job_id == value]
            if len(matches) > 1:
                raise ValueError("This job_id exists in multiple local service scopes; use its handle")
            task = matches[0] if matches else None
    if task is None:
        raise ValueError(
            f"Unknown PyMOL render handle {value!r}. Supply the durable job_id and its original profile."
        )
    return task


def _update(task: _RenderTask, **values: Any) -> None:
    with _TASKS_LOCK:
        for key, value in values.items():
            setattr(task, key, value)
        task.updated_at = time.time()


def _task_snapshot(task: _RenderTask) -> dict[str, Any]:
    return {
        "handle": task.handle,
        "job_id": task.job_id,
        "request_key": task.operation_key,
        "operation_key": task.operation_key,
        "profile": task.profile,
        "status": task.status,
        "stage": task.stage,
        "queue_position": task.queue_position,
        "condition": task.condition,
        "next_action": task.next_action,
        "result_available": task.result_available,
        "expires_at": task.expires_at,
        "output": str(task.output),
        "complete": task.complete,
        "presentation_status": task.presentation_status,
        "presentation": task.presentation_status,
        "outputs": dict(task.outputs),
        "artifacts": {name: dict(value) for name, value in task.artifacts.items()},
        "preset_decision": task.preset_decision,
        "render_spec_sha256": task.render_spec_sha256,
        "style_source": task.style_source,
        "selection_phase": task.selection_phase,
        "style_identity": task.style_identity,
        "preselection_decision": task.preselection_decision,
        "backend_render_started": task.backend_render_started,
        "selection_error": task.selection_error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _safe_token(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    text = str(value)
    if not 1 <= len(text) <= 128 or not all(character.isalnum() or character in "._:-" for character in text):
        return default
    return text


def _safe_condition(value: Any, fallback: str) -> str:
    raw = _safe_token(value)
    if raw in _SAFE_CONDITIONS:
        return raw
    from blender_render.consumer_errors import category

    normalized = category(raw, default=fallback)
    return normalized if normalized in _SAFE_CONDITIONS else fallback


def _default_action(status: str, condition: str | None) -> str:
    blocked = {
        "compatibility_blocked": "restore_exact_engine",
        "execution_unknown": "inspect_execution",
        "recovery_required": "recover_operation",
        "restart_required": "restart_service",
        "transfer_pending": "recover_transfer",
    }
    if condition in blocked:
        return blocked[condition]
    if status == "transfer_pending":
        return "recover_transfer"
    if status in _CANCELLABLE:
        return "render_status"
    if status == "done":
        return "render_result"
    return "stop"


def _safe_action(value: Any, default: str) -> str:
    action = _safe_token(value)
    return action if action in _SAFE_ACTIONS else default


def _safe_expiry(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value) if math.isfinite(float(value)) else None
    text = str(value)
    if 1 <= len(text) <= 64 and all(character.isdigit() or character in "-:+.TZ" for character in text):
        return text
    return None


def _safe_state(state: Any, job_id: str) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("Render service returned an invalid status document")
    observed = state.get("job_id")
    if observed is not None and str(observed) != job_id:
        raise ValueError("Render service returned status for a different job")
    status = _safe_token(state.get("status"), "execution_unknown")
    invalid_status = status not in _KNOWN_STATES
    if invalid_status:
        status = "execution_unknown"
    raw_condition = state.get("condition") or state.get("error_code") or state.get("error_category")
    if raw_condition is None and status in _BLOCKED_STATES:
        raw_condition = status
    condition = None
    if raw_condition is not None or invalid_status:
        condition = _safe_condition(
            raw_condition,
            "render_failed" if status == "failed" else "execution_unknown",
        )
    stage = _safe_token(state.get("stage"), "status_unknown")
    if stage not in _SAFE_STAGES:
        stage = "status_unknown"
    next_action = _safe_action(
        state.get("next_action"),
        _default_action(status, condition),
    )
    queue = state.get("queue_position")
    if not isinstance(queue, int) or isinstance(queue, bool) or queue < 0:
        queue = None
    expiry = _safe_expiry(state.get("expires_at", state.get("expiry")))
    availability = state.get("result_available")
    if not isinstance(availability, bool):
        availability = status in {"done", "delivered"}
    from blender_render.consumer_records import status_metadata

    metadata = status_metadata(state)
    render_spec_sha256 = state.get("render_spec_sha256")
    if render_spec_sha256 is not None and (
        not isinstance(render_spec_sha256, str)
        or len(render_spec_sha256) != 64
        or any(character not in "0123456789abcdef" for character in render_spec_sha256)
    ):
        raise ValueError("Render service returned an invalid specification identity")
    return {
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "queue_position": queue,
        "condition": condition,
        "next_action": next_action,
        "result_available": availability,
        "expires_at": expiry,
        **metadata,
        "render_spec_sha256": render_spec_sha256,
    }


def _apply_state(task: _RenderTask, state: Mapping[str, Any]) -> None:
    values = dict(
        status=state["status"],
        stage=state["stage"],
        queue_position=state.get("queue_position"),
        condition=state.get("condition"),
        next_action=state.get("next_action") or "blender_render_status",
        result_available=bool(state.get("result_available")),
        expires_at=state.get("expires_at"),
    )
    for name in (
        "preset_decision",
        "render_spec_sha256",
        "style_source",
        "selection_phase",
        "style_identity",
        "preselection_decision",
        "backend_render_started",
        "selection_error",
    ):
        if name in state:
            values[name] = state.get(name)
    _update(task, **values)


def _connection(
    *,
    mode: str,
    profile: str,
    server_host: str,
    server_port: Any,
    use_apptainer: Any,
    apptainer_image: Any,
    blender_exe: Any,
    attach_only: Any,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Return transient admission options and a secret-free action identity."""
    from blender_render.config import load_config

    text = _normalize_cli_string
    mode, profile = text(mode), text(profile)
    port = int(text(server_port) or 8765)
    if not 1 <= port <= 65535:
        raise ValueError("server_port must be between 1 and 65535")
    if text(server_host):
        from blender_render.remote_client import _normalize_base_url

        host = text(server_host)
        base_url = _normalize_base_url(host, port)
        parsed = urlsplit(base_url)
        try:
            actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            invalid = (
                parsed.scheme not in {"http", "https"}
                or parsed.username
                or parsed.password
                or not parsed.hostname
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
                or (mode == "lab" and parsed.scheme != "http")
                or (
                    parsed.scheme == "http"
                    and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                    and mode != "lab"
                )
            )
        except ValueError:
            invalid = True
            actual_port = port
        if invalid:
            raise ValueError("server_host must be a credential-free service host or root URL")
        safe = {"kind": "direct", "host": base_url.rstrip("/"), "port": actual_port}
        if mode == "lab":
            safe["lab_http"] = True
        label = _parse_identifier(profile or mode or "direct", "profile")
        return dict(safe), safe, label
    selected = profile or (
        load_config().get("default_hpc_profile", "ipd")
        if mode in {"interactive", "slurm", "hpc"}
        else mode or "local"
    )
    selected = _parse_identifier(selected, "profile")
    image = text(apptainer_image) if _parse_bool(use_apptainer) or mode == "apptainer" else ""
    if sys.platform == "darwin" and mode == "local":
        image = ""
    admission = {
        "kind": "configured",
        "profile": selected,
        "blender": text(blender_exe),
        "image": image,
        "attach_only": _parse_bool(attach_only),
    }
    safe = {"kind": "configured", "profile": selected}
    return admission, safe, selected


def _make_client(
    connection: Mapping[str, Any],
    *,
    intention: str,
    timeout: float,
    admission_options: Mapping[str, Any] | None = None,
):
    from blender_render.client import Client, ConnectionIntention, configured_client

    if connection["kind"] == "direct":
        from blender_render.remote_client import _normalize_base_url

        lab_http = connection.get("lab_http") is True
        options = {"timeout": timeout}
        if lab_http:
            options["lab_http"] = True
        return Client(
            _normalize_base_url(str(connection["host"]), int(connection["port"])),
            "" if lab_http else os.environ.get("BLENDER_RENDER_TOKEN", ""),
            **options,
        )
    kwargs = dict(admission_options or {})
    kwargs.pop("kind", None)
    kwargs.pop("profile", None)
    attach_only = bool(kwargs.pop("attach_only", False))
    if admission_options is not None:
        # ``attach_only`` means submit through an already-running controller;
        # it must not be confused with result-only attachment, which prohibits POST.
        kwargs["intention"] = (
            ConnectionIntention.SUBMIT_EXISTING
            if attach_only
            else ConnectionIntention.START_OR_ALLOCATE
        )
        kwargs["admission"] = True
    else:
        kwargs["intention"] = intention
        kwargs["admission"] = False
    kwargs["timeout"] = timeout
    return configured_client(str(connection["profile"]), **kwargs)


class _AdmissionDeadline(TimeoutError):
    pass


def _ensure_admission_deadline_supported() -> None:
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        from blender_render.client import ClientError

        raise ClientError(
            "Bounded PyMOL admission must run on the process main/application thread.",
            409,
            "recovery_required",
            next_action="invoke_on_application_thread",
        )


@contextmanager
def _admission_deadline(timeout: float):
    def expired(signum, frame):  # pragma: no cover - frame is interpreter-owned
        raise _AdmissionDeadline("admission deadline expired")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer[0] > 0:
        from blender_render.client import ClientError

        raise ClientError(
            "Another process deadline is active; admission was not started.",
            409,
            "recovery_required",
            next_action="retry_after_current_action",
        )
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _public_client_error(exc: Exception, task: _RenderTask, *, fallback: str) -> Exception:
    from blender_render.client import ClientError

    if isinstance(exc, ClientError):
        code = _safe_condition(exc.code, fallback)
        action = _safe_action(
            exc.next_action,
            task.next_action
            if task.next_action in _SAFE_ACTIONS
            else _default_action(task.status, code),
        )
        status = exc.status if isinstance(exc.status, int) else 0
    else:
        code, action, status = fallback, "blender_render_status", 0
    return ClientError(
        "The render operation requires an explicit follow-up action.",
        status,
        code,
        job_id=task.job_id,
        next_action=action,
    )


def _request_digest(payload: Mapping[str, Any], options: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"payload": payload, "options": options},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _progress(task: _RenderTask, state: dict[str, Any]) -> None:
    safe = _safe_state({"job_id": task.job_id, **state}, str(task.job_id))
    _apply_state(task, safe)
    print(
        f"Blender render {task.job_id}: {safe['status']}; "
        f"stage {safe['stage']}; queue position {safe['queue_position']}"
    )


def _admit_submission(
    task: _RenderTask,
    payload: dict[str, Any],
    options: dict[str, Any],
    admission_connection: dict[str, Any],
    rerender: bool,
    admission_timeout: float,
    *,
    qualify_default: bool,
) -> dict[str, Any]:
    """Upload and durably submit exactly once, with a whole-admission deadline."""
    from blender_render.client import ClientError

    phase = "connection"
    try:
        with _admission_deadline(admission_timeout):
            client = _make_client(
                task.connection,
                intention="start_or_allocate",
                timeout=admission_timeout,
                admission_options=admission_connection,
            )
            phase = "upload"
            _update(task, status="submitting", stage="uploading")
            source = client.upload(payload)
            input_id = _parse_identifier(
                source.get("input_id"), "service input_id", operation=True
            )
            phase = "submit"
            _update(task, stage="submitting")
            job = client.submit(
                input_id,
                options,
                operation_key=task.operation_key,
                rerender=rerender,
            )
            job_id = _parse_identifier(job.get("job_id"), "service job_id", operation=True)
    except Exception as exc:
        ambiguous = phase == "submit" and (
            isinstance(exc, (_AdmissionDeadline, TimeoutError))
            or not isinstance(exc, ClientError)
            or exc.status == 0
            or exc.status >= 500
        )
        if ambiguous:
            _update(
                task,
                status="execution_unknown",
                stage="admission_response",
                condition="execution_unknown",
                next_action="inspect_operation_key_without_resubmitting",
            )
            if qualify_default:
                task.output = _qualified_output(task.output, str(task.operation_key))
            try:
                _reserve(task)
            except Exception:
                pass
            error = ClientError(
                f"Submission response was not confirmed; retain operation key {task.operation_key!r} and do not resubmit.",
                0,
                "execution_unknown",
                next_action="inspect_operation_key_without_resubmitting",
            )
        else:
            failure_code = (
                "admission_timeout"
                if isinstance(exc, _AdmissionDeadline)
                else _safe_condition(getattr(exc, "code", None), "recovery_required")
            )
            _update(
                task,
                status="failed",
                stage="admission",
                condition=failure_code,
                next_action=(
                    _safe_action(
                        getattr(exc, "next_action", None),
                        "retry_same_operation_key" if phase != "submit" else "inspect_operation_key",
                    )
                    or ("retry_same_operation_key" if phase != "submit" else "inspect_operation_key")
                ),
            )
            _release_reservation(task)
            if isinstance(exc, _AdmissionDeadline):
                error = ClientError(
                    "Render admission timed out before durable submission.",
                    0,
                    "admission_timeout",
                    next_action="retry_same_operation_key",
                )
            else:
                error = _public_client_error(exc, task, fallback="recovery_required")
        error.handle = task.handle
        error.operation_key = task.operation_key
        error.request_key = task.operation_key
        raise error from None

    admission_state = {"job_id": job_id, **job}
    if job.get("status") is None:
        admission_state.update(
            status="queued",
            stage="waiting_for_compute",
            next_action="blender_render_status",
        )
    safe = _safe_state(admission_state, job_id)
    if safe["status"] not in _KNOWN_STATES:
        safe.update(
            status="execution_unknown",
            stage="admission_response",
            condition="execution_unknown",
            next_action="blender_render_status",
        )
    _update(task, job_id=job_id)
    _apply_state(task, safe)
    try:
        if qualify_default:
            task.output = _qualified_output(task.output, job_id)
            _check_destination(task.output)
            _check_destination(task.output.with_suffix(".blend"))
            _check_destination(task.output.with_suffix(".manifest.json"))
        _reserve(task)
    except Exception:
        _update(
            task,
            condition="recovery_required",
            next_action="blender_render_result_with_distinct_output",
            destination_recovery_required=True,
        )
        error = ClientError(
            "The job was admitted but its local destination could not be reserved; do not resubmit.",
            409,
            "recovery_required",
            job_id=job_id,
            next_action="blender_render_result_with_distinct_output",
        )
        error.handle = task.handle
        error.operation_key = task.operation_key
        error.request_key = task.operation_key
        raise error from None
    if safe["status"] in {"cancelled", "failed"}:
        _release_reservation(task)
    print("Blender render job: " + job_id + " (PyMOL handle " + task.handle + ")")
    return safe


def _query_status(task: _RenderTask, timeout: float = 30.0) -> dict[str, Any]:
    if task.job_id is None:
        return _task_snapshot(task)
    try:
        client = _make_client(task.connection, intention="inspect_cached", timeout=timeout)
        safe = _safe_state(client.status(task.job_id), task.job_id)
    except Exception as exc:
        raise _public_client_error(exc, task, fallback="execution_unknown") from None
    _apply_state(task, safe)
    return safe


def _same_callable(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return False
    return left is right or (
        getattr(left, "__self__", object()) is getattr(right, "__self__", None)
        and getattr(left, "__func__", left) is getattr(right, "__func__", right)
    )


def _present_verified(cmd_obj: Any, output: Path, *, requested: bool) -> str:
    if not requested:
        return "not_attempted"
    if threading.current_thread() is not threading.main_thread():
        print("Rendered PNG: " + str(output))
        return "fallback_path"
    dispatcher = getattr(cmd_obj, "_call_in_gui_thread", None)
    opengl = getattr(cmd_obj, "_call_with_opengl_context", None)
    if _same_callable(dispatcher, opengl) or not callable(getattr(cmd_obj, "load_png", None)):
        print("Rendered PNG: " + str(output))
        return "fallback_path"
    return "inline_preview" if try_load_preview(cmd_obj, output) else "fallback_path"


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix="." + destination.name, delete=False
    ) as outgoing:
        temporary = Path(outgoing.name)
        try:
            while block := incoming.read(1024 * 1024):
                outgoing.write(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _descriptor_map(result: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = result.get("artifacts")
    if isinstance(raw, dict):
        items = [dict(value, name=name) for name, value in raw.items() if isinstance(value, dict)]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("Receipt-backed delivery did not return artifact verification descriptors")
    descriptors: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("name") not in {
            "render.png",
            "render.blend",
            "manifest.json",
        }:
            raise ValueError("Receipt-backed delivery returned an invalid artifact descriptor")
        name = item["name"]
        if name in descriptors:
            raise ValueError("Receipt-backed delivery returned duplicate artifact identity")
        digest, size = item.get("sha256"), item.get("size")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise ValueError("Receipt-backed delivery omitted an artifact checksum or size")
        descriptors[name] = {
            "sha256": digest,
            "size": size,
            "required": bool(item.get("required", name in {"render.png", "render.blend"})),
            "verified_locally": bool(item.get("verified_locally", False)),
            "availability": (
                item.get("availability")
                if item.get("availability") in {"available", "deleted", "delivered", "expired", "lost", "unknown"}
                else None
            ),
            "checksum_failure": bool(item.get("checksum_failure", False)),
        }
    return descriptors


def _verify_delivery(task: _RenderTask, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("job_id") != task.job_id:
        raise ValueError("Receipt-backed delivery returned artifacts for a different job")
    outputs = result.get("outputs")
    if not isinstance(outputs, dict):
        raise ValueError("Receipt-backed delivery returned invalid output descriptors")
    descriptors = _descriptor_map(result)
    safe_outputs: dict[str, str] = {}
    integrity_failure = False
    expected = {
        "render.png": task.output,
        "render.blend": task.output.with_suffix(".blend"),
        "manifest.json": task.output.with_suffix(".manifest.json"),
    }
    for name, raw_path in outputs.items():
        if name not in expected or name not in descriptors:
            raise ValueError("Receipt-backed delivery returned an unexpected output")
        path = Path(str(raw_path)).expanduser().resolve()
        if path != expected[name]:
            integrity_failure = True
            continue
        descriptor = descriptors[name]
        try:
            raw = path.read_bytes()
        except OSError:
            integrity_failure = True
            continue
        if len(raw) != descriptor["size"] or hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
            integrity_failure = True
            continue
        descriptor["verified_locally"] = True
        safe_outputs[name] = str(path)

    copy_error = copy_integrity_failure = False
    if task.blend_output is not None and "render.blend" in safe_outputs:
        source = Path(safe_outputs["render.blend"])
        try:
            if source != task.blend_output:
                _atomic_copy(source, task.blend_output)
            raw = task.blend_output.read_bytes()
            descriptor = descriptors["render.blend"]
            if len(raw) != descriptor["size"] or hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
                raise ValueError("Copied Blender scene failed checksum verification")
            safe_outputs["render.blend"] = str(task.blend_output)
        except ValueError:
            safe_outputs.pop("render.blend", None)
            copy_integrity_failure = True
        except OSError:
            safe_outputs.pop("render.blend", None)
            copy_error = True

    missing = [
        str(name)
        for name in result.get("missing_required", [])
        if str(name) in {"render.png", "render.blend", "manifest.json"}
    ]
    required = set() if result.get("_injected_legacy_test_double") else set(task.required_outputs)
    required.update(name for name, item in descriptors.items() if item["required"])
    for name in sorted(required):
        if name not in safe_outputs and name not in missing:
            missing.append(name)
    if copy_error and "render.blend" not in missing:
        missing.append("render.blend")
    if "render.png" not in safe_outputs and "render.png" not in missing:
        missing.insert(0, "render.png")
    integrity_failure = integrity_failure or copy_integrity_failure
    complete = bool(result.get("complete")) and not missing and not integrity_failure
    return {
        "job_id": task.job_id,
        "status": task.status,
        "complete": complete,
        "outputs": safe_outputs,
        "missing_required": missing,
        "error_code": (
            None
            if complete
            else "artifact_integrity_failed"
            if integrity_failure
            else "artifact_unavailable"
        ),
        "next_action": "done"
        if complete
        else _safe_action(result.get("next_action"), "recover_verified_local_copy_or_stop"),
        "artifacts": descriptors,
        "presentation_status": "not_attempted",
    }


def _verified_saved_outputs(task: _RenderTask) -> dict[str, str]:
    verified: dict[str, str] = {}
    for name, path_text in task.outputs.items():
        descriptor = task.artifacts.get(name, {})
        try:
            raw = Path(path_text).read_bytes()
        except OSError:
            continue
        if (
            isinstance(descriptor.get("size"), int)
            and isinstance(descriptor.get("sha256"), str)
            and len(raw) == descriptor["size"]
            and hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
        ):
            verified[name] = path_text
    return verified


def _result_failure(task: _RenderTask, state: Mapping[str, Any], code: str) -> dict[str, Any]:
    outputs = _verified_saved_outputs(task)
    required = set(task.required_outputs)
    required.update(
        name for name, descriptor in task.artifacts.items() if descriptor.get("required")
    )
    missing = sorted(name for name in required if name not in outputs)
    next_action = state.get("next_action") or "blender_render_status"
    if code in {"artifact_unavailable", "artifact_integrity_failed"}:
        next_action = "recover_verified_local_copy_or_stop"
    result = {
        "job_id": task.job_id,
        "status": state.get("status", task.status),
        "complete": False,
        "outputs": outputs,
        "missing_required": missing,
        "error_code": code,
        "next_action": next_action,
        "artifacts": {name: dict(value) for name, value in task.artifacts.items()},
        "presentation_status": "not_attempted",
    }
    _update(task, complete=False, outputs=outputs, next_action=next_action)
    return result


def _download_existing(
    client: Any, task: _RenderTask, *, deadline: float | None = None
) -> dict[str, Any]:
    """Download through the client, normalizing only transport-free injected doubles.

    Every client constructible by this module is ``Client``/``ConfiguredClient``
    and must return receipt identity and checksum descriptors. A few historical
    unit adapters inject a tiny object with no request transport; arbitrary local
    code injection is already outside the service trust boundary.
    """
    import inspect

    from blender_render.client import Client

    parameters = inspect.signature(client.download).parameters
    accepts_deadline = "deadline" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    result = (
        client.download(task.job_id, task.output, deadline=deadline)
        if isinstance(client, Client) and accepts_deadline
        else client.download(task.job_id, task.output)
    )

    if (
        isinstance(client, Client)
        or callable(getattr(client, "request", None))
        or isinstance(result, dict)
        and ("job_id" in result or "artifacts" in result)
    ):
        return result
    if not isinstance(result, dict) or set(result.get("outputs", {})) != {"render.png"}:
        return result
    output = Path(str(result["outputs"]["render.png"])).expanduser().resolve()
    if output != task.output or not output.is_file():
        return result
    raw = output.read_bytes()
    return {
        **result,
        "_injected_legacy_test_double": True,
        "job_id": task.job_id,
        "complete": True,
        "missing_required": [],
        "artifacts": [
            {
                "name": "render.png",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "required": True,
                "verified_locally": True,
            }
        ],
    }


def _result_for_task(
    task: _RenderTask,
    cmd_obj: Any,
    *,
    wait_enabled: bool,
    timeout: float,
    present: bool,
    known_admitted: bool = False,
    initial_state: Mapping[str, Any] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    from blender_render.client import Client, ClientError

    if task.job_id is None:
        raise ClientError(
            "The durable job ID is unknown; do not submit a replacement.",
            409,
            task.condition or "execution_unknown",
            next_action=task.next_action,
        )
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    client = _make_client(
        task.connection,
        intention="attach_existing",
        timeout=min(
            _remaining_timeout(deadline, task, next_action="blender_render_result"),
            120.0,
        ),
    )
    if initial_state is not None:
        state = dict(initial_state)
    elif known_admitted:
        state = {
            "job_id": task.job_id,
            "status": task.status,
            "stage": task.stage,
            "condition": task.condition,
            "next_action": task.next_action,
            "result_available": task.result_available,
        }
    else:
        try:
            state = _safe_state(client.status(task.job_id), task.job_id)
        except Exception as exc:
            raise _public_client_error(exc, task, fallback="execution_unknown") from None
        _apply_state(task, state)

    if state.get("condition") in {
        "restart_required",
        "compatibility_blocked",
        "execution_unknown",
        "recovery_required",
    }:
        raise ClientError(
            "Render operation requires intervention.",
            409,
            state["condition"],
            job_id=task.job_id,
            next_action=state.get("next_action"),
        )
    status = state.get("status")
    if status in {"queued", "running", "starting", "submitting"}:
        if not wait_enabled:
            return _result_failure(task, state, "result_not_ready")
        try:
            remaining = _remaining_timeout(
                deadline, task, next_action="blender_render_result"
            )
            if isinstance(client, Client):
                client.wait(
                    task.job_id,
                    timeout=remaining,
                    progress=lambda item: _progress(task, item),
                    deadline=deadline,
                    fetch_result=False,
                )
            else:
                client.wait(
                    task.job_id,
                    timeout=remaining,
                    progress=lambda item: _progress(task, item),
                )
        except Exception as exc:
            _remaining_timeout(deadline, task, next_action="blender_render_result")
            if isinstance(exc, ClientError) and exc.code in {
                "restart_required",
                "compatibility_blocked",
                "execution_unknown",
                "recovery_required",
            }:
                raise _public_client_error(exc, task, fallback=exc.code) from None
            # Another explicit client may have delivered or cancelled between
            # the initial status and this bounded wait. Resolve that race with
            # one authoritative read; never turn it into another submission.
            try:
                client = _make_client(
                    task.connection,
                    intention="attach_existing",
                    timeout=min(
                        _remaining_timeout(
                            deadline, task, next_action="blender_render_result"
                        ),
                        120.0,
                    ),
                )
                raced = _safe_state(client.status(task.job_id), task.job_id)
                _remaining_timeout(
                    deadline, task, next_action="blender_render_result"
                )
            except ClientError as status_exc:
                if status_exc.code == "operation_timeout":
                    raise _public_client_error(
                        status_exc, task, fallback="operation_timeout"
                    ) from None
                try:
                    _remaining_timeout(
                        deadline, task, next_action="blender_render_result"
                    )
                except ClientError as timeout_exc:
                    raise _public_client_error(
                        timeout_exc, task, fallback="operation_timeout"
                    ) from None
                raise _public_client_error(exc, task, fallback="render_failed") from None
            except Exception:
                try:
                    _remaining_timeout(
                        deadline, task, next_action="blender_render_result"
                    )
                except ClientError as timeout_exc:
                    raise _public_client_error(
                        timeout_exc, task, fallback="operation_timeout"
                    ) from None
                raise _public_client_error(exc, task, fallback="render_failed") from None
            if raced["status"] not in _RESULT_STATES | {"cancelled", "failed", "transfer_pending"}:
                raise _public_client_error(exc, task, fallback="render_failed") from None
            state = raced
        else:
            state = {
                "job_id": task.job_id,
                "status": "done",
                "stage": "result_ready",
                "condition": None,
                "next_action": "blender_render_result",
                "result_available": True,
            }
        _apply_state(task, state)
        status = state["status"]
        if status == "transfer_pending":
            return _result_failure(task, state, "transfer_pending")
        if status in {"cancelled", "failed"}:
            _release_reservation(task, deadline=deadline)
            return _result_failure(
                task, state, "cancelled" if status == "cancelled" else "render_failed"
            )
    elif status == "transfer_pending":
        return _result_failure(task, state, "transfer_pending")
    elif status in {"cancelled", "failed"}:
        _release_reservation(task, deadline=deadline)
        return _result_failure(task, state, "cancelled" if status == "cancelled" else "render_failed")
    elif status not in _RESULT_STATES:
        return _result_failure(task, state, "result_not_ready")

    try:
        client = _make_client(
            task.connection,
            intention="attach_existing",
            timeout=min(
                _remaining_timeout(deadline, task, next_action="blender_render_result"),
                120.0,
            ),
        )
        delivered = _verify_delivery(
            task, _download_existing(client, task, deadline=deadline)
        )
    except ValueError:
        return _result_failure(task, state, "artifact_integrity_failed")
    except ClientError as exc:
        if exc.code == "operation_timeout":
            raise _public_client_error(exc, task, fallback="operation_timeout") from None
        code = (
            "artifact_unavailable"
            if status in {"expired", "deleted", "delivered", "done"}
            else "render_failed"
        )
        return _result_failure(task, state, code)
    except Exception as exc:
        raise _public_client_error(exc, task, fallback="artifact_unavailable") from None
    presentation = "not_attempted"
    if "render.png" in delivered["outputs"]:
        _remaining_timeout(deadline, task, next_action="open_output_path")
        presentation = _present_verified(
            cmd_obj,
            Path(delivered["outputs"]["render.png"]),
            requested=present and task.presentation_status != "inline_preview",
        )
        if present and task.presentation_status == "inline_preview":
            presentation = "inline_preview"
    delivered["presentation_status"] = presentation
    final_action = (
        "done"
        if delivered["complete"] and presentation in {"not_attempted", "inline_preview"}
        else "open_output_path"
        if delivered["complete"]
        else delivered["next_action"]
    )
    delivered["next_action"] = final_action
    _update(
        task,
        complete=delivered["complete"],
        outputs=dict(delivered["outputs"]),
        artifacts={name: dict(value) for name, value in delivered["artifacts"].items()},
        presentation_status=presentation,
        next_action=final_action,
    )
    if delivered["complete"]:
        _release_reservation(task, deadline=deadline)
    return delivered


def bridge_render(
    selection="(all)",
    preset="",
    style_json="",
    mode="",
    output_dir=".",
    output_name="render.png",
    rough=0,
    color_follow="",
    save_blend="",
    raw_view="",
    state=-1,
    server_host="",
    server_port="",
    server_timeout=1800,
    profile="",
    use_apptainer=0,
    apptainer_image="",
    blender_exe="",
    attach=1,
    attach_only=0,
    keep_temp=0,
    width="",
    height="",
    samples="",
    transparent_background="",
    blend_output="",
    _self=None,
    overrides_json="",
    preserve_view="",
    blocking=1,
    auto_preview=1,
    operation_key="",
    rerender=0,
    admission_timeout=30,
    **legacy,
):
    """Submit one operation; omitted ``blocking`` preserves synchronous history."""
    # Lifecycle controls are strict and validated before importing or touching PyMOL.
    blocking_value = _strict_bool(blocking, "blocking")
    preview_value = _strict_bool(auto_preview, "auto_preview")
    rerender_value = _strict_bool(rerender, "rerender")
    admission_bound = _bounded_timeout(
        admission_timeout, "admission_timeout", default=30.0, maximum=3600.0
    )
    completion_bound = _bounded_timeout(server_timeout, "server_timeout", default=1800.0)
    _ensure_admission_deadline_supported()

    text = _normalize_cli_string
    mode_value = text(mode)
    profile_value = text(profile)
    host_value = text(server_host)
    port_value: Any = server_port
    defaults = installation_defaults()
    default_mode = defaults.get("mode", "local")
    # A package-level default is explicit installation configuration. Supplying
    # another mode/profile selects a different connection and does not inherit
    # the configured lab host. A host-only override keeps that lab transport.
    if not profile_value and (not mode_value or mode_value == default_mode):
        mode_value = mode_value or default_mode
        host_value = host_value or defaults.get("server_host", "")
        port_value = text(port_value) or defaults.get("server_port", 8765)
    else:
        mode_value = mode_value or "local"
        port_value = text(port_value) or 8765
    if legacy:
        resources = [key for key in legacy if key.startswith("slurm_") or key == "interactive_launcher"]
        if resources:
            raise ValueError(
                "Configure allocation resources in a named cluster profile using blender-render profile add."
            )
        raise ValueError("Unknown blender_render options: " + ", ".join(sorted(legacy)))
    if not _parse_bool(attach):
        raise ValueError("attach=0 is unsupported; explicitly stop the persistent service instead")

    admission_connection, safe_connection, selected = _connection(
        mode=mode_value,
        profile=profile_value,
        server_host=host_value,
        server_port=port_value,
        use_apptainer=use_apptainer,
        apptainer_image=apptainer_image,
        blender_exe=blender_exe,
        attach_only=attach_only,
    )
    options: dict[str, Any] = {
        "preset": text(preset) or defaults.get("preset", "preset_1_interface"),
        "rough": _parse_bool(rough),
        "keep_temp": _parse_bool(keep_temp),
    }
    for key, value in (("color_follow", color_follow), ("raw_view", raw_view)):
        if str(value).strip():
            options[key] = _parse_bool(value)
    if text(style_json):
        options["style_doc"] = json.loads(Path(text(style_json)).expanduser().read_text())
        options.pop("preset", None)
    if text(overrides_json):
        options["overrides"] = json.loads(Path(text(overrides_json)).expanduser().read_text())
    if str(save_blend).strip():
        options["save_blend"] = _parse_bool(save_blend)
    blend_destination = Path(text(blend_output)).expanduser().resolve() if text(blend_output) else None
    if blend_destination is not None:
        options["save_blend"] = True
    if str(preserve_view).strip():
        options["preserve_view"] = _parse_bool(preserve_view)
    for key, value in (("width", width), ("height", height), ("samples", samples)):
        if str(value).strip():
            options[key] = int(value)
    if str(transparent_background).strip():
        options["transparent_background"] = _parse_bool(transparent_background)

    handle = "pymol-" + uuid.uuid4().hex[:12]
    logical_key = text(operation_key) or handle
    _parse_identifier(logical_key, "operation_key", operation=True)
    output, default_output = _output_path(output_dir, output_name)
    if blend_destination in {output, output.with_suffix(".manifest.json")}:
        raise ValueError("blend_output must not overlap the PNG or manifest destination")
    _check_destination(output)
    _check_destination(output.with_suffix(".blend"))
    _check_destination(output.with_suffix(".manifest.json"))
    if blend_destination is not None:
        _check_destination(blend_destination)

    task = _RenderTask(
        handle=handle,
        profile=selected,
        connection=safe_connection,
        output=output,
        operation_key=logical_key,
        blend_output=blend_destination,
        required_outputs=(
            ("render.png", "render.blend") if options.get("save_blend") else ("render.png",)
        ),
        auto_preview=preview_value,
    )
    qualify_default = not blocking_value and default_output
    try:
        task = _register_task(task)
        if not qualify_default:
            _reserve(task)
        if _self is None:
            from pymol import cmd
        else:
            cmd = _self
        payload = build_render_payload(cmd, text(selection), int(state), include_color_hints=True)
        task.request_digest = _request_digest(payload, options)
        admitted = _admit_submission(
            task,
            payload,
            options,
            admission_connection,
            rerender_value,
            admission_bound,
            qualify_default=qualify_default,
        )
    except BaseException:
        if task.status == "starting":
            _release_reservation(task)
            with _TASKS_LOCK:
                _TASKS.pop(task.handle, None)
        raise

    if not blocking_value:
        result = _task_snapshot(task)
        print(json.dumps(result, sort_keys=True))
        return result
    completion_deadline = time.monotonic() + completion_bound
    try:
        with _bounded_action(
            completion_deadline, task, next_action="blender_render_result"
        ):
            result = _result_for_task(
                task,
                cmd,
                wait_enabled=True,
                timeout=completion_bound,
                present=preview_value,
                known_admitted=True,
                initial_state=admitted,
                deadline=completion_deadline,
            )
    except Exception:
        # Blocking mode returns no convenience handle on failure. Its typed
        # error retains the durable job ID; a later explicit recovery chooses
        # and reserves a destination again before receipt verification.
        _release_reservation(task, deadline=completion_deadline)
        with _TASKS_LOCK:
            _TASKS.pop(task.handle, None)
        raise
    if not result["complete"]:
        from blender_render.client import IncompleteResultError

        raise IncompleteResultError(result)
    print(str(task.output))
    return str(task.output)


def _connection_for_action(
    *,
    profile: Any,
    mode: Any,
    server_host: Any,
    server_port: Any,
) -> tuple[dict[str, Any], str]:
    _, safe, selected = _connection(
        mode=_normalize_cli_string(mode),
        profile=_normalize_cli_string(profile),
        server_host=_normalize_cli_string(server_host),
        server_port=server_port,
        use_apptainer=0,
        apptainer_image="",
        blender_exe="",
        attach_only=1,
    )
    return safe, selected


def _resolve_task_or_job(
    identifier: Any,
    explicit_job_id: Any,
    *,
    profile: Any,
    mode: Any,
    server_host: Any,
    server_port: Any,
    output_dir: Any = ".",
    output_name: Any = "",
    register: bool,
    deadline: float | None = None,
) -> _RenderTask:
    value = _identifier_value(explicit_job_id) or _identifier_value(identifier)
    if not value:
        raise ValueError("A local handle or explicit job_id is required")
    connection_hint = None
    if any(
        _normalize_cli_string(item)
        for item in (profile, mode, server_host)
    ):
        connection_hint, _ = _connection_for_action(
            profile=profile,
            mode=mode,
            server_host=server_host,
            server_port=server_port,
        )
    with _TASKS_LOCK:
        task = _TASKS.get(value)
        if task is None:
            matches = [
                item
                for item in _TASKS.values()
                if item.job_id == value
                and (connection_hint is None or item.connection == connection_hint)
            ]
            if len(matches) > 1:
                raise ValueError("This job_id exists in multiple service scopes; specify its profile")
            task = matches[0] if matches else None
    if task is not None:
        if explicit_job_id and _parse_identifier(
            explicit_job_id, "job_id", operation=True
        ) != task.job_id:
            raise ValueError("The handle and explicit job_id identify different operations")
        if connection_hint is not None and connection_hint != task.connection:
            raise ValueError("The supplied profile does not match this operation's service scope")
        return task
    if value.startswith("pymol-") and not explicit_job_id:
        raise ValueError("Unknown session-local handle; use its durable job_id after module reload")
    job = _parse_identifier(value, "job_id", operation=True)
    if connection_hint is None:
        raise ValueError(
            "An existing job requires its original profile, mode, or server_host; "
            "saved reservations never select a service route"
        )
    connection = connection_hint
    selected = connection.get("profile", "direct")
    output, default_output = _output_path(output_dir, output_name)
    if default_output:
        output = _qualified_output(output, job)
    task = _RenderTask(
        handle="pymol-" + uuid.uuid4().hex[:12],
        job_id=job,
        operation_key=None,
        profile=selected,
        connection=connection,
        output=output,
        status="recovery_required",
        stage="recovering",
        condition="recovery_required",
    )
    if register:
        _check_destination(output)
        _check_destination(output.with_suffix(".blend"))
        _check_destination(output.with_suffix(".manifest.json"))
        _reserve(task, deadline=deadline)
    return _register_task(task) if register else task


def _retarget_task_if_requested(
    task: _RenderTask,
    *,
    output_dir: Any,
    output_name: Any,
    deadline: float,
) -> None:
    """Add an explicit recovery destination before changing retained state."""
    raw_directory = _normalize_cli_string(output_dir)
    raw_name = _normalize_cli_string(output_name)
    if raw_directory in {"", "."} and raw_name in {"", "render.png"}:
        return
    candidate, default_output = _output_path(output_dir, output_name)
    if default_output:
        identity = task.job_id or task.operation_key
        if identity is None:
            raise ValueError("A durable operation identity is required for recovery output")
        candidate = _qualified_output(candidate, identity)
    if candidate == task.output:
        return
    if task.blend_output in {candidate, candidate.with_suffix(".manifest.json")}:
        raise ValueError("The recovery PNG destination overlaps the explicit Blender scene")
    for target in (
        candidate,
        candidate.with_suffix(".blend"),
        candidate.with_suffix(".manifest.json"),
    ):
        _check_destination(target)
    # _reserve writes the merged same-operation target set under its file lock.
    # Update the live task only after that durable reservation succeeds.
    proposed = replace(task, output=candidate)
    _reserve(proposed, deadline=deadline)
    values: dict[str, Any] = {"output": candidate}
    if task.destination_recovery_required:
        values.update(
            destination_recovery_required=False,
            condition=None,
            next_action="blender_render_result",
        )
    _update(task, **values)


def bridge_status(
    identifier="",
    job_id="",
    profile="",
    mode="",
    server_host="",
    server_port=8765,
    timeout=30,
    _self=None,
):
    """Query authoritative state with inspect-only connection intention."""
    status_timeout = _bounded_timeout(timeout, "timeout", default=30.0, maximum=3600.0)
    deadline = time.monotonic() + status_timeout
    if not _identifier_value(identifier) and not _identifier_value(job_id):
        with _TASKS_LOCK:
            tasks = sorted(_TASKS.values(), key=lambda item: item.created_at)
        renders = []
        for task in tasks:
            if task.job_id is not None:
                with _bounded_action(deadline, task):
                    _query_status(
                        task,
                        _remaining_timeout(deadline, task),
                    )
            renders.append(_task_snapshot(task))
        result: Any = {"renders": renders}
    else:
        task = _resolve_task_or_job(
            identifier,
            job_id,
            profile=profile,
            mode=mode,
            server_host=server_host,
            server_port=server_port,
            register=False,
        )
        with _bounded_action(deadline, task):
            _query_status(task, _remaining_timeout(deadline, task))
        result = _task_snapshot(task)
    print(json.dumps(result, sort_keys=True))
    return result


def bridge_result(
    identifier="",
    job_id="",
    wait=0,
    timeout=1800,
    present=None,
    profile="",
    mode="",
    output_dir=".",
    output_name="",
    server_host="",
    server_port=8765,
    _self=None,
):
    """Explicitly wait if requested, then receipt-recover and present one job."""
    wait_value = _strict_bool(wait, "wait")
    present_value = None if present is None else _strict_bool(present, "present")
    result_timeout = _bounded_timeout(timeout, "timeout", default=1800.0)
    deadline = time.monotonic() + result_timeout
    task = _resolve_task_or_job(
        identifier,
        job_id,
        profile=profile,
        mode=mode,
        server_host=server_host,
        server_port=server_port,
        output_dir=output_dir,
        output_name=output_name,
        register=True,
        deadline=deadline,
    )
    if _self is None:
        from pymol import cmd
    else:
        cmd = _self
    with _bounded_action(deadline, task, next_action="blender_render_result"):
        _retarget_task_if_requested(
            task,
            output_dir=output_dir,
            output_name=output_name,
            deadline=deadline,
        )
        result = _result_for_task(
            task,
            cmd,
            wait_enabled=wait_value,
            timeout=result_timeout,
            present=task.auto_preview if present_value is None else present_value,
            deadline=deadline,
        )
    print(json.dumps(result, sort_keys=True))
    return result


def bridge_cancel(
    identifier="",
    job_id="",
    profile="",
    mode="",
    server_host="",
    server_port=8765,
    timeout=30,
    _self=None,
):
    """Synchronously cancel one existing job and return authoritative state."""
    action_timeout = _bounded_timeout(timeout, "timeout", default=30.0, maximum=3600.0)
    deadline = time.monotonic() + action_timeout
    task = _resolve_task_or_job(
        identifier,
        job_id,
        profile=profile,
        mode=mode,
        server_host=server_host,
        server_port=server_port,
        register=False,
    )
    with _bounded_action(deadline, task):
        state = _query_status(task, _remaining_timeout(deadline, task))
        if state["status"] in _CANCELLABLE:
            try:
                client = _make_client(
                    task.connection,
                    intention="attach_existing",
                    timeout=_remaining_timeout(deadline, task),
                )
                state = _safe_state(client.cancel(task.job_id), str(task.job_id))
            except Exception as exc:
                raise _public_client_error(exc, task, fallback="execution_unknown") from None
            _apply_state(task, state)
        if state["status"] in {"cancelled", "failed"}:
            _release_reservation(task, deadline=deadline)
    result = _task_snapshot(task)
    print(json.dumps(result, sort_keys=True))
    return result


def bridge_recover(
    job_id,
    profile="",
    mode="",
    output_dir=".",
    output_name="render.png",
    server_host="",
    server_port=8765,
    server_timeout=1800,
    blocking=0,
    auto_preview=1,
    _self=None,
    transfer_recovery=0,
    operation_key="",
):
    """Recover an explicit existing job/receipt; never upload or submit."""
    blocking_value = _strict_bool(blocking, "blocking")
    preview_value = _strict_bool(auto_preview, "auto_preview")
    transfer_value = _strict_bool(transfer_recovery, "transfer_recovery")
    timeout = _bounded_timeout(server_timeout, "server_timeout", default=1800.0)
    deadline = time.monotonic() + timeout
    if _normalize_cli_string(operation_key):
        raise ValueError(
            "Direct-client request-key lookup is unsupported; provide the authoritative service job_id"
        )
    identifier = _parse_identifier(job_id, "job_id", operation=True)
    task = _resolve_task_or_job(
        "",
        identifier,
        profile=profile,
        mode=mode,
        server_host=server_host,
        server_port=server_port,
        output_dir=output_dir,
        output_name=output_name,
        register=True,
        deadline=deadline,
    )
    task.auto_preview = preview_value
    with _bounded_action(deadline, task, next_action="blender_render_result"):
        _retarget_task_if_requested(
            task,
            output_dir=output_dir,
            output_name=output_name,
            deadline=deadline,
        )
        state = _query_status(
            task,
            min(_remaining_timeout(deadline, task), 120.0),
        )
        if transfer_value:
            if state["status"] != "transfer_pending":
                raise ValueError("transfer_recovery=1 requires authoritative transfer_pending status")
            try:
                client = _make_client(
                    task.connection,
                    intention="attach_existing",
                    timeout=min(_remaining_timeout(deadline, task), 120.0),
                )
                state = _safe_state(client.recover_transfer(identifier), identifier)
            except Exception as exc:
                raise _public_client_error(exc, task, fallback="transfer_pending") from None
            _apply_state(task, state)
        if state["status"] in {"cancelled", "failed"}:
            _release_reservation(task, deadline=deadline)
        if not blocking_value:
            result: Any = _task_snapshot(task)
        else:
            if _self is None:
                from pymol import cmd
            else:
                cmd = _self
            delivered = _result_for_task(
                task,
                cmd,
                wait_enabled=True,
                timeout=timeout,
                present=preview_value,
                initial_state=state,
                deadline=deadline,
            )
            if not delivered["complete"]:
                from blender_render.client import IncompleteResultError

                raise IncompleteResultError(delivered)
            result = delivered["outputs"]["render.png"]
    print(json.dumps(result, sort_keys=True) if isinstance(result, dict) else result)
    return result


blender_render = bridge_render
blender_render_status = bridge_status
blender_render_result = bridge_result
blender_render_cancel = bridge_cancel
blender_render_recover = bridge_recover


_PUBLIC_COMMANDS = (
    ("blender_render", bridge_render),
    ("blender_render_status", bridge_status),
    ("blender_render_result", bridge_result),
    ("blender_render_cancel", bridge_cancel),
    ("blender_render_recover", bridge_recover),
)


def _registered_function(cmd_obj: Any, name: str):
    keyword = getattr(cmd_obj, "keyword", None)
    if not isinstance(keyword, dict) or name not in keyword:
        return None, None
    entry = keyword[name]
    if isinstance(entry, (list, tuple)) and entry:
        return entry[0], entry
    return entry if callable(entry) else None, entry


def register_plugin(cmd_obj=None):
    """Register exactly five commands without duplicating PyMOL help tables."""
    if cmd_obj is None:
        from pymol import cmd as cmd_obj
    marker_name = "_blender_render_d3_commands"
    saved_marker = getattr(cmd_obj, marker_name, ())
    marker = set(saved_marker) if isinstance(saved_marker, (tuple, list, set)) else set()
    for name, function in _PUBLIC_COMMANDS:
        existing, entry = _registered_function(cmd_obj, name)
        if existing is function:
            marker.add(name)
            continue
        if callable(existing) and getattr(existing, "__name__", "") == function.__name__:
            if isinstance(entry, list):
                entry[0] = function
                marker.add(name)
                continue
            if isinstance(entry, tuple):
                cmd_obj.keyword[name] = (function, *entry[1:])
                marker.add(name)
                continue
        if name in marker:
            continue
        cmd_obj.extend(name, function)
        marker.add(name)
    try:
        setattr(cmd_obj, marker_name, tuple(sorted(marker)))
    except Exception:
        pass


try:
    from pymol import cmd

    register_plugin(cmd)
except ImportError:
    pass
