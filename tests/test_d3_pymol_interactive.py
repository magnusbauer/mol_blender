from __future__ import annotations

import json
import hashlib
import importlib
import inspect
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from blender_render import pymol_bridge_plugin as plugin
from blender_render.client import ClientError


@pytest.fixture(autouse=True)
def isolated_task_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("BLENDER_RENDER_HOME", str(tmp_path / "home"))
    # Operator deployment defaults are intentionally outside renderer identity;
    # lifecycle contracts must not inherit the checkout's local lab route.
    monkeypatch.setattr(plugin, "_PACKAGE_ROOT", str(tmp_path / "package"))
    with plugin._TASKS_LOCK:
        plugin._TASKS.clear()
    yield
    with plugin._TASKS_LOCK:
        plugin._TASKS.clear()


class ImmediateClient:
    def __init__(self, calls, *, complete=True):
        self.calls = calls
        self.complete = complete

    def upload(self, payload):
        self.calls.append(("upload", threading.get_ident(), payload))
        return {"input_id": "input"}

    def submit(self, input_id, options, **identity):
        self.calls.append(("submit", threading.get_ident(), input_id, options, identity))
        return {"job_id": "job", "status": "queued", "stage": "waiting_for_compute"}

    def wait(self, job_id, timeout, progress):
        self.calls.append(("wait", threading.get_ident(), job_id, timeout))
        progress({"status": "running", "stage": "rendering", "queue_position": 0})

    def status(self, job_id):
        self.calls.append(("remote_status", threading.get_ident(), job_id))
        return {
            "job_id": job_id,
            "status": "done",
            "stage": "result_ready",
            "condition": None,
            "next_action": "render_result",
            "result_available": True,
        }

    def download(self, job_id, output):
        self.calls.append(("download", threading.get_ident(), job_id, str(output)))
        raw = b"PNG"
        Path(output).write_bytes(raw)
        return {
            "job_id": job_id,
            "outputs": {"render.png": str(output)},
            "complete": self.complete,
            "error_code": None if self.complete else "artifact_unavailable",
            "missing_required": [] if self.complete else ["render.blend"],
            "next_action": "done" if self.complete else "recover_verified_local_copy_or_stop",
            "delivery_receipt": {"verified": self.complete, "artifacts": ["render.png"]},
            "artifacts": [{
                "name": "render.png",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "required": True,
                "verified_locally": True,
            }],
        }

    def cancel(self, job_id):
        self.calls.append(("cancel", threading.get_ident(), job_id))
        return {
            "job_id": job_id,
            "status": "cancelled",
            "stage": "cancelled",
            "condition": None,
            "next_action": "stop",
        }

    def delete_structure(self, input_id):
        self.calls.append(("delete_structure", threading.get_ident(), input_id))


class PresentationRecorder:
    def __init__(self):
        self.preview_threads = []

    def load_png(self, path, **kwargs):
        self.preview_threads.append(threading.get_ident())


def patch_payload(monkeypatch, calls):
    def build(cmd, selection, state, **kwargs):
        calls.append(("export", threading.get_ident(), cmd, selection, state, kwargs))
        return {"version": "1.0", "selection": selection}

    monkeypatch.setattr(plugin, "build_render_payload", build)


def patch_client(monkeypatch, client, calls):
    import blender_render.client

    def configured(profile, **kwargs):
        calls.append(("client", threading.get_ident(), profile, kwargs))
        return client

    monkeypatch.setattr(blender_render.client, "configured_client", configured)


def test_omitted_blocking_retains_historical_synchronous_contract(monkeypatch, tmp_path):
    calling_thread = threading.get_ident()
    calls = []
    cmd = SimpleNamespace(
        load_png=lambda *args, **kwargs: calls.append(("preview", threading.get_ident()))
    )
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)

    output = plugin.bridge_render(_self=cmd, output_dir=tmp_path)

    assert output == str((tmp_path / "render.png").resolve())
    lifecycle = [call for call in calls if call[0] in {
        "export", "upload", "submit", "wait", "download", "preview"
    }]
    assert [call[0] for call in lifecycle] == [
        "export", "upload", "submit", "wait", "download", "preview"
    ]
    assert all(call[1] == calling_thread for call in lifecycle)


def test_explicit_nonblocking_returns_after_admission_and_result_runs_on_caller(monkeypatch, tmp_path):
    main_thread = threading.get_ident()
    calls = []
    entered_wait = threading.Event()
    completed = {"value": False}

    class GatedClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", threading.get_ident(), job_id))
            return {
                "job_id": job_id,
                "status": "done" if completed["value"] else "queued",
                "stage": "result_ready" if completed["value"] else "waiting_for_compute",
                "next_action": "render_result" if completed["value"] else "render_status",
            }

        def wait(self, job_id, timeout, progress):
            calls.append(("wait", threading.get_ident(), job_id, timeout))
            progress({"status": "running", "stage": "rendering", "queue_position": 0})
            entered_wait.set()
            completed["value"] = True

    cmd = PresentationRecorder()
    client = GatedClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)

    handle = plugin.bridge_render(_self=cmd, output_dir=tmp_path, blocking=0)
    assert handle["handle"].startswith("pymol-")
    assert handle["job_id"] == "job"
    task = plugin._get_task(handle)
    assert task.job_id == "job"
    assert not entered_wait.is_set()
    assert not any(call[0] in {"wait", "download", "preview"} for call in calls)

    result = plugin.bridge_result(handle, wait=1, _self=cmd)
    assert entered_wait.is_set()
    assert result["presentation_status"] == "inline_preview"
    snapshot = plugin.bridge_status(handle)
    assert snapshot["status"] == "done"
    assert snapshot["job_id"] == "job"
    assert Path(snapshot["output"]).read_bytes() == b"PNG"
    assert cmd.preview_threads == [main_thread]
    assert next(call for call in calls if call[0] == "export")[1] == main_thread
    assert all(call[1] == main_thread for call in calls if call[0] in {
        "client", "upload", "submit", "wait", "download"
    })
    client_call = next(call for call in calls if call[0] == "client")
    assert client_call[3]["admission"] is True
    submission = next(call for call in calls if call[0] == "submit")
    assert submission[4] == {"operation_key": handle["request_key"], "rerender": False}


def test_blocking_mode_preserves_script_path_and_calling_thread(monkeypatch, tmp_path):
    main_thread = threading.get_ident()
    calls = []
    cmd = SimpleNamespace(load_png=lambda *args, **kwargs: calls.append(("preview", threading.get_ident())))
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)

    output = plugin.bridge_render(_self=cmd, output_dir=tmp_path, blocking=1)
    assert Path(output).read_bytes() == b"PNG"
    lifecycle = [call for call in calls if call[0] in {
        "export", "upload", "submit", "wait", "download", "preview"
    }]
    assert [call[0] for call in lifecycle] == [
        "export", "upload", "submit", "wait", "download", "preview"
    ]
    assert all(call[1] == main_thread for call in lifecycle)
    assert plugin.bridge_status()["renders"][0]["status"] == "done"


def test_status_is_authoritative_and_never_waits_or_downloads(monkeypatch, tmp_path):
    calls = []

    class StatusClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", threading.get_ident(), job_id))
            return {
                "job_id": job_id,
                "status": "running",
                "stage": "rendering",
                "queue_position": 1,
                "next_action": "render_status",
            }

    client = StatusClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(_self=SimpleNamespace(), output_dir=tmp_path, auto_preview=0, blocking=0)
    calls.clear()
    status = plugin.bridge_status(handle)
    assert status["job_id"] == "job" and status["status"] == "running"
    assert [call[0] for call in calls if call[0] == "remote_status"] == ["remote_status"]
    assert not any(call[0] in {"wait", "download", "upload", "submit"} for call in calls)


def test_cancel_after_admission_uses_one_submission_and_one_cancel(monkeypatch, tmp_path):
    calls = []
    cancelled = {"value": False}

    class CancellationClient(ImmediateClient):
        def status(self, job_id):
            return {
                "job_id": job_id,
                "status": "cancelled" if cancelled["value"] else "running",
                "stage": "cancelled" if cancelled["value"] else "rendering",
                "next_action": "stop" if cancelled["value"] else "render_status",
            }

        def cancel(self, job_id):
            calls.append(("cancel", threading.get_ident(), job_id))
            cancelled["value"] = True
            return self.status(job_id)

    client = CancellationClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(_self=SimpleNamespace(), output_dir=tmp_path, auto_preview=0, blocking=0)
    assert plugin.bridge_cancel(handle)["status"] == "cancelled"
    assert plugin.bridge_status(handle)["status"] == "cancelled"
    assert [call[0] for call in calls].count("submit") == 1
    assert [call[0] for call in calls].count("cancel") == 1
    assert not any(call[0] in {"wait", "download", "preview"} for call in calls)


def test_cancel_before_durable_job_id_is_safe_and_inert(monkeypatch, tmp_path):
    calls = []
    task = plugin._RenderTask(
        handle="pymol-preadmission",
        profile="local",
        connection={"kind": "configured", "profile": "local"},
        output=tmp_path / "unused.png",
        operation_key="stable-key",
        status="execution_unknown",
        condition="execution_unknown",
        next_action="inspect_operation_key_without_resubmitting",
    )
    plugin._register_task(task)

    result = plugin.bridge_cancel(task.handle)

    assert result["status"] == "execution_unknown"
    assert result["job_id"] is None
    assert calls == []


def test_recover_downloads_known_job_without_upload_or_submission(monkeypatch, tmp_path):
    calls = []

    class RecoveryClient(ImmediateClient):
        def upload(self, payload):  # pragma: no cover - must remain unused
            raise AssertionError("recover uploaded")

        def submit(self, *args, **kwargs):  # pragma: no cover - must remain unused
            raise AssertionError("recover submitted")

    client = RecoveryClient(calls)
    patch_client(monkeypatch, client, calls)
    output = plugin.bridge_recover(
        "known-job", profile="cluster", output_dir=tmp_path, blocking=1, auto_preview=0,
        _self=SimpleNamespace(),
    )
    assert Path(output).read_bytes() == b"PNG"
    assert [call[0] for call in calls if call[0] in {"remote_status", "wait", "download"}] == [
        "remote_status", "download"
    ]
    configured = next(call for call in calls if call[0] == "client")
    assert configured[2] == "cluster"
    assert configured[3]["intention"] == "inspect_cached"
    assert configured[3]["admission"] is False


@pytest.mark.parametrize("terminal", ["delivered", "deleted", "expired"])
def test_recover_terminal_delivery_state_consults_receipt_without_wait(monkeypatch, tmp_path, terminal):
    calls = []

    class RecoveryClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", threading.get_ident(), terminal))
            return {"job_id": job_id, "status": terminal, "stage": terminal}

        def wait(self, *args, **kwargs):  # pragma: no cover - must remain unused
            raise AssertionError("terminal delivery recovery waited")

    client = RecoveryClient(calls)
    patch_client(monkeypatch, client, calls)
    output = plugin.bridge_recover(
        "known-job", profile="local", output_dir=tmp_path, blocking=1,
        auto_preview=0, _self=SimpleNamespace()
    )
    assert Path(output).read_bytes() == b"PNG"
    assert [call[0] for call in calls if call[0] in {"remote_status", "download"}] == [
        "remote_status", "download"
    ]


def test_recover_pending_job_waits_then_downloads_without_submission(monkeypatch, tmp_path):
    calls = []

    class RecoveryClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", threading.get_ident(), job_id))
            return {"job_id": job_id, "status": "queued", "stage": "waiting_for_compute", "queue_position": 2}

    client = RecoveryClient(calls)
    patch_client(monkeypatch, client, calls)
    plugin.bridge_recover(
        "known-job", profile="local", output_dir=tmp_path, blocking=1,
        auto_preview=0, _self=SimpleNamespace()
    )
    assert [call[0] for call in calls if call[0] in {"remote_status", "wait", "download"}] == [
        "remote_status", "wait", "download"
    ]
    assert not any(call[0] in {"upload", "submit"} for call in calls)


def test_presentation_failure_does_not_change_render_completion(monkeypatch, tmp_path):
    calls = []
    cmd = SimpleNamespace(
        _call_in_gui_thread=lambda function: function(),
        load_png=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("display unavailable")),
    )
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(_self=cmd, output_dir=tmp_path, blocking=0)
    result = plugin.bridge_result(handle, wait=0, _self=cmd)
    snapshot = plugin.bridge_status(handle)
    assert snapshot["status"] == "done"
    assert result["presentation_status"] == "fallback_path"
    assert snapshot["condition"] is None
    assert Path(snapshot["output"]).is_file()


def test_missing_dispatcher_requires_explicit_preview_on_calling_thread(monkeypatch, tmp_path):
    calls = []
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(_self=SimpleNamespace(), output_dir=tmp_path, blocking=0)
    assert not any(call[0] in {"wait", "download", "preview"} for call in calls)

    preview_threads = []
    result = plugin.bridge_result(
        handle,
        _self=SimpleNamespace(load_png=lambda *args, **kwargs: preview_threads.append(threading.get_ident())),
    )
    assert Path(result["outputs"]["render.png"]).is_file()
    assert preview_threads == [threading.get_ident()]
    assert result["presentation_status"] == "inline_preview"


def test_headless_immediate_hook_is_not_mistaken_for_gui_dispatch(monkeypatch, tmp_path):
    calls = []
    preview_threads = []
    immediate = lambda function: function()
    cmd = SimpleNamespace(
        _call_in_gui_thread=immediate,
        _call_with_opengl_context=immediate,
        load_png=lambda *args, **kwargs: preview_threads.append(threading.get_ident()),
    )
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(_self=cmd, output_dir=tmp_path, blocking=0)
    result = plugin.bridge_result(handle, wait=0, _self=cmd)
    assert preview_threads == []
    assert result["presentation_status"] == "fallback_path"


def test_verified_result_called_off_application_thread_never_touches_pymol(tmp_path):
    calls = []
    result = []
    output = tmp_path / "verified.png"
    output.write_bytes(b"PNG")

    worker = threading.Thread(
        target=lambda: result.append(
            plugin._present_verified(
                SimpleNamespace(load_png=lambda *args, **kwargs: calls.append(args)),
                output,
                requested=True,
            )
        )
    )
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert result == ["fallback_path"]
    assert calls == []


@pytest.mark.parametrize("profile", ["local", "cluster", "modal"])
@pytest.mark.parametrize("blocking", [1, 0])
def test_full_json_uses_same_options_and_admission_for_every_profile(
    monkeypatch, tmp_path, profile, blocking
):
    calls = []
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    style = tmp_path / (profile + ".json")
    style.write_text(json.dumps({"schema_version": "fixture", "marker": profile}))
    overrides = tmp_path / (profile + "-overrides.json")
    overrides.write_text(json.dumps({"renderer": {"transparent_background": True}}))

    plugin.bridge_render(
        _self=SimpleNamespace(load_png=lambda *args, **kwargs: None),
        profile=profile,
        style_json=style,
        overrides_json=overrides,
        output_dir=tmp_path,
        output_name=profile + ".png",
        blocking=blocking,
        auto_preview=0,
    )
    configured = next(call for call in calls if call[0] == "client")
    options = next(call for call in calls if call[0] == "submit")[3]
    assert configured[2] == profile
    assert configured[3]["admission"] is True
    assert options["style_doc"] == {"schema_version": "fixture", "marker": profile}
    assert options["overrides"] == {"renderer": {"transparent_background": True}}
    assert "preset" not in options


def test_active_output_collision_and_timeout_bounds_fail_before_submission(monkeypatch, tmp_path):
    calls = []
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, output_name="shared.png",
        auto_preview=0, blocking=0,
    )
    with pytest.raises(ValueError, match="reserved"):
        plugin.bridge_render(
            _self=SimpleNamespace(), output_dir=tmp_path, output_name="shared.png",
            auto_preview=0, blocking=0,
        )
    with pytest.raises(ValueError, match="server_timeout"):
        plugin.bridge_render(_self=SimpleNamespace(), output_dir=tmp_path, output_name="other.png", server_timeout=0)
    assert [call[0] for call in calls].count("submit") == 1


def test_no_dead_legacy_controls_and_all_commands_are_registered():
    with pytest.raises(ValueError, match="Unknown"):
        plugin.bridge_render(_self=SimpleNamespace(), poll_interval=1)
    with pytest.raises(ValueError, match="named cluster profile"):
        plugin.bridge_render(_self=SimpleNamespace(), slurm_gres="gpu:1")
    expected = [
        "blender_render",
        "blender_render_status",
        "blender_render_result",
        "blender_render_cancel",
        "blender_render_recover",
        "blender_movie_export",
    ]
    registered = []
    recorder = SimpleNamespace(extend=lambda name, function: registered.append(name))
    plugin.register_plugin(recorder)
    plugin.register_plugin(recorder)
    assert registered == expected
    assert plugin.blender_render_result is plugin.bridge_result
    assert plugin.blender_render_recover is plugin.bridge_recover
    assert not hasattr(plugin, "blender_render_preview")
    assert not hasattr(plugin, "blender_render_resume")


# Corrective regressions added after the independent D3 review. These exercise
# the explicit, thread-free lifecycle rather than the discarded watcher design.


def test_correction_nonblocking_stops_after_durable_admission(monkeypatch, tmp_path):
    calls = []
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)

    handle = plugin.bridge_render(
        _self=SimpleNamespace(load_png=lambda *a, **k: calls.append(("preview",))),
        output_dir=tmp_path,
        blocking=0,
        auto_preview=0,
    )

    assert [item[0] for item in calls if item[0] in {
        "export", "upload", "submit", "wait", "download", "preview"
    }] == ["export", "upload", "submit"]
    task = plugin._get_task(handle)
    assert not hasattr(task, "thread")
    assert not any(thread.name.startswith("blender-render-") for thread in threading.enumerate())
    assert not Path(plugin.bridge_status(handle)["output"]).exists()


def test_correction_result_is_explicit_receipt_backed_and_structured(monkeypatch, tmp_path):
    calls = []
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )
    assert not any(item[0] in {"wait", "download"} for item in calls)

    result = plugin.bridge_result(handle, wait=0, present=0, _self=SimpleNamespace())

    assert result["job_id"] == "job"
    assert result["complete"] is True
    assert Path(result["outputs"]["render.png"]).read_bytes() == b"PNG"
    assert len(result["artifacts"]["render.png"]["sha256"]) == 64
    assert result["presentation_status"] == "not_attempted"
    assert [item[0] for item in calls].count("download") == 1


def test_real_client_protocol_without_receipt_identity_is_never_presented(monkeypatch, tmp_path):
    import blender_render.client

    calls = []

    class MalformedRealClient(blender_render.client.Client):
        def __init__(self):
            pass

        def upload(self, payload):
            return {"input_id": "input"}

        def submit(self, *args, **kwargs):
            return {"job_id": "job", "status": "queued"}

        def wait(self, *args, **kwargs):
            return None

        def download(self, job_id, output):
            Path(output).write_bytes(b"unbound bytes")
            return {"outputs": {"render.png": str(output)}}

    patch_payload(monkeypatch, calls)
    monkeypatch.setattr(
        blender_render.client, "configured_client", lambda *args, **kwargs: MalformedRealClient()
    )
    cmd = SimpleNamespace(load_png=lambda *args, **kwargs: calls.append(("preview",)))

    with pytest.raises(ClientError) as caught:
        plugin.bridge_render(_self=cmd, output_dir=tmp_path)

    assert caught.value.code == "artifact_integrity_failed"
    assert not any(item[0] == "preview" for item in calls)


def test_result_identity_mismatch_never_presents_or_returns_foreign_bytes(monkeypatch, tmp_path):
    calls = []

    class WrongIdentityClient(ImmediateClient):
        def download(self, job_id, output):
            result = super().download(job_id, output)
            result["job_id"] = "another-job"
            return result

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, WrongIdentityClient(calls), calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )
    presentations = []

    result = plugin.bridge_result(
        handle,
        present=1,
        _self=SimpleNamespace(load_png=lambda *args, **kwargs: presentations.append(args)),
    )

    assert result["job_id"] == "job"
    assert result["complete"] is False
    assert result["outputs"] == {}
    assert result["missing_required"] == ["render.png"]
    assert result["error_code"] == "artifact_integrity_failed"
    assert presentations == []


def test_correction_partial_delivery_retains_verified_png(monkeypatch, tmp_path):
    calls = []
    client = ImmediateClient(calls, complete=False)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )

    result = plugin.bridge_result(handle, wait=0, present=0, _self=SimpleNamespace())

    assert result["complete"] is False
    assert Path(result["outputs"]["render.png"]).read_bytes() == b"PNG"
    assert result["missing_required"] == ["render.blend"]
    assert result["error_code"] == "artifact_unavailable"
    assert result["presentation_status"] == "not_attempted"
    assert plugin.bridge_status(handle)["status"] == "done"


def test_required_blend_cannot_be_hidden_by_an_incorrect_complete_flag(monkeypatch, tmp_path):
    calls = []
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ImmediateClient(calls), calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        output_name="required.png",
        save_blend=1,
        blocking=0,
        auto_preview=0,
    )

    result = plugin.bridge_result(handle, present=0, _self=SimpleNamespace())

    assert result["complete"] is False
    assert result["outputs"] == {
        "render.png": str((tmp_path / "required.png").resolve())
    }
    assert result["missing_required"] == ["render.blend"]
    assert result["error_code"] == "artifact_unavailable"


def test_final_blend_integrity_failure_retains_verified_png(monkeypatch, tmp_path):
    calls = []

    class CorruptBlendClient(ImmediateClient):
        def download(self, job_id, output):
            png = b"PNG"
            expected_blend = b"EXPECTED BLEND"
            Path(output).write_bytes(png)
            blend = Path(output).with_suffix(".blend")
            blend.write_bytes(b"MODIFIED BLEND")
            return {
                "job_id": job_id,
                "outputs": {
                    "render.png": str(output),
                    "render.blend": str(blend),
                },
                "complete": True,
                "missing_required": [],
                "artifacts": [
                    {
                        "name": "render.png",
                        "sha256": hashlib.sha256(png).hexdigest(),
                        "size": len(png),
                        "required": True,
                    },
                    {
                        "name": "render.blend",
                        "sha256": hashlib.sha256(expected_blend).hexdigest(),
                        "size": len(expected_blend),
                        "required": True,
                    },
                ],
            }

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, CorruptBlendClient(calls), calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        output_name="partial-integrity.png",
        save_blend=1,
        blocking=0,
        auto_preview=0,
    )

    result = plugin.bridge_result(handle, present=0, _self=SimpleNamespace())

    assert result["complete"] is False
    assert Path(result["outputs"]["render.png"]).read_bytes() == b"PNG"
    assert "render.blend" not in result["outputs"]
    assert result["missing_required"] == ["render.blend"]
    assert result["error_code"] == "artifact_integrity_failed"


@pytest.mark.parametrize(
    "state",
    [
        {"status": "queued", "stage": "waiting_for_compute", "next_action": "render_status"},
        {"status": "running", "stage": "rendering", "next_action": "render_status"},
        {"status": "done", "stage": "result_ready", "next_action": "render_result"},
        {"status": "delivered", "stage": "delivery", "next_action": "stop"},
        {"status": "cancelled", "stage": "cancelled", "next_action": "stop"},
        {"status": "failed", "stage": "failed", "next_action": "stop"},
        {"status": "expired", "stage": "result_ready", "next_action": "stop"},
        {"status": "transfer_pending", "stage": "transfer", "next_action": "recover_transfer"},
        {"status": "running", "stage": "rendering", "condition": "restart_required", "next_action": "restart_service"},
        {"status": "queued", "stage": "waiting_for_compute", "condition": "compatibility_blocked", "next_action": "restore_exact_engine"},
        {"status": "running", "stage": "rendering", "condition": "execution_unknown", "next_action": "inspect_execution"},
        {"status": "running", "stage": "rendering", "condition": "recovery_required", "next_action": "recover_operation"},
    ],
)
def test_correction_status_preserves_authoritative_service_state(monkeypatch, tmp_path, state):
    calls = []

    class StatusClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", job_id))
            return {"job_id": job_id, "result_available": state["status"] == "done", **state}

    client = StatusClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )
    calls.clear()

    result = plugin.bridge_status(handle)

    assert result["status"] == state["status"]
    assert result["stage"] == state["stage"]
    assert result["condition"] == state.get("condition")
    assert result["next_action"] == state["next_action"]
    assert [item[0] for item in calls].count("remote_status") == 1
    configured = next(item for item in calls if item[0] == "client")
    assert str(configured[3]["intention"]).endswith("inspect_cached")
    assert not any(item[0] in {"upload", "submit", "download"} for item in calls)


def test_correction_cancel_is_synchronous_authoritative_and_idempotent(monkeypatch, tmp_path):
    calls = []
    status = {"value": "queued"}

    class CancellationClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", job_id, status["value"]))
            return {
                "job_id": job_id,
                "status": status["value"],
                "stage": "cancelled" if status["value"] == "cancelled" else "waiting_for_compute",
                "next_action": "stop" if status["value"] == "cancelled" else "render_status",
            }

        def cancel(self, job_id):
            calls.append(("cancel", job_id))
            status["value"] = "cancelled"
            return self.status(job_id)

    client = CancellationClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )
    calls.clear()

    first = plugin.bridge_cancel(handle)
    second = plugin.bridge_cancel(handle)

    assert first["status"] == second["status"] == "cancelled"
    assert [item[0] for item in calls].count("cancel") == 1
    assert not any(item[0] in {"download", "preview", "submit"} for item in calls)


@pytest.mark.parametrize("initial", ["queued", "running"])
def test_cancel_queued_or_running_job_never_retrieves_or_presents(
    monkeypatch, tmp_path, initial
):
    calls = []
    state = {"value": initial}

    class CancellationClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", job_id, state["value"]))
            return {
                "job_id": job_id,
                "status": state["value"],
                "stage": state["value"],
                "next_action": "stop" if state["value"] == "cancelled" else "render_status",
            }

        def cancel(self, job_id):
            calls.append(("cancel", job_id))
            state["value"] = "cancelled"
            return self.status(job_id)

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, CancellationClient(calls), calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )
    calls.clear()

    result = plugin.bridge_cancel(handle)

    assert result["status"] == "cancelled"
    assert [item[0] for item in calls].count("cancel") == 1
    assert not any(item[0] in {"wait", "download", "preview", "submit"} for item in calls)


@pytest.mark.parametrize("terminal", ["cancelled", "done", "failed", "expired"])
def test_cancel_observes_terminal_service_state_without_cancel_call(
    monkeypatch, terminal
):
    calls = []

    class TerminalClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", job_id))
            return {
                "job_id": job_id,
                "status": terminal,
                "stage": "result_ready" if terminal == "done" else terminal,
                "next_action": "render_result" if terminal == "done" else "stop",
            }

        def cancel(self, job_id):  # pragma: no cover - terminal paths are inert
            raise AssertionError("terminal operation was cancelled again")

    patch_client(monkeypatch, TerminalClient(calls), calls)

    result = plugin.bridge_cancel(job_id="known-job", profile="local")

    assert result["status"] == terminal
    assert not any(item[0] in {"cancel", "download", "preview", "submit"} for item in calls)


def test_correction_transfer_recovery_uses_existing_job_only(monkeypatch, tmp_path):
    calls = []

    class TransferClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", job_id))
            return {
                "job_id": job_id,
                "status": "transfer_pending",
                "stage": "transfer",
                "next_action": "recover_transfer",
            }

        def recover_transfer(self, job_id):
            calls.append(("recover_transfer", job_id))
            return {
                "job_id": job_id,
                "status": "running",
                "stage": "transfer_recovery",
                "next_action": "render_status",
            }

    patch_client(monkeypatch, TransferClient(calls), calls)

    result = plugin.bridge_recover(
        "known-job",
        profile="cluster",
        output_dir=tmp_path,
        transfer_recovery=1,
        blocking=0,
        _self=SimpleNamespace(),
    )

    assert result["job_id"] == "known-job"
    assert result["status"] == "running"
    assert [item[0] for item in calls if item[0] in {
        "remote_status", "recover_transfer", "upload", "submit", "download"
    }] == ["remote_status", "recover_transfer"]
    task = plugin._get_task(result["handle"])
    assert task.operation_key is None


@pytest.mark.parametrize(
    "state",
    [
        {"status": "queued", "stage": "waiting_for_compute", "next_action": "render_status"},
        {"status": "running", "stage": "rendering", "next_action": "render_status"},
        {"status": "delivered", "stage": "delivery", "next_action": "render_result"},
        {"status": "expired", "stage": "result_ready", "next_action": "recover_result"},
        {"status": "cancelled", "stage": "cancelled", "next_action": "stop"},
        {
            "status": "running",
            "stage": "rendering",
            "condition": "restart_required",
            "next_action": "restart_service",
        },
        {
            "status": "queued",
            "stage": "waiting_for_compute",
            "condition": "compatibility_blocked",
            "next_action": "restore_exact_engine",
        },
        {
            "status": "running",
            "stage": "dispatch",
            "condition": "execution_unknown",
            "next_action": "inspect_execution",
        },
        {
            "status": "running",
            "stage": "transfer",
            "condition": "recovery_required",
            "next_action": "recover_operation",
        },
        {
            "status": "transfer_pending",
            "stage": "transfer",
            "next_action": "recover_transfer",
        },
    ],
)
def test_recover_preserves_authoritative_state_without_scene_or_submission(
    monkeypatch, tmp_path, state
):
    calls = []

    class RecoveryStateClient(ImmediateClient):
        def status(self, job_id):
            calls.append(("remote_status", job_id))
            return {"job_id": job_id, **state}

    patch_client(monkeypatch, RecoveryStateClient(calls), calls)
    monkeypatch.setattr(
        plugin,
        "build_render_payload",
        lambda *args, **kwargs: pytest.fail("recovery inspected the PyMOL scene"),
    )

    result = plugin.bridge_recover(
        "known-job",
        profile="local",
        output_dir=tmp_path,
        output_name="recovery.png",
        blocking=0,
        auto_preview=0,
        _self=SimpleNamespace(),
    )

    assert result["job_id"] == "known-job"
    assert result["status"] == state["status"]
    assert result["condition"] == state.get("condition")
    assert result["next_action"] == state["next_action"]
    assert not any(item[0] in {"upload", "submit", "wait", "download"} for item in calls)


def test_recover_rejects_fabricated_request_key_lookup_before_client(monkeypatch):
    import blender_render.client

    monkeypatch.setattr(
        blender_render.client,
        "configured_client",
        lambda *args, **kwargs: pytest.fail("unsupported request-key lookup contacted a client"),
    )
    with pytest.raises(ValueError, match="request-key lookup is unsupported"):
        plugin.bridge_recover("job", operation_key="original-key")


def test_correction_historical_positional_tail_is_unchanged():
    names = list(inspect.signature(plugin.bridge_render).parameters)
    assert names[26:29] == ["_self", "overrides_json", "preserve_view"]
    assert names.index("blocking") > names.index("preserve_view")


def test_previous_positional_tail_maps_to_self_overrides_and_preserve_view(
    monkeypatch, tmp_path
):
    calls = []
    context = SimpleNamespace()
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ImmediateClient(calls), calls)
    overrides = tmp_path / "positional-overrides.json"
    overrides.write_text(json.dumps({"camera": {"lens_mm": 73}}))
    signature = inspect.signature(plugin.bridge_render)
    values = [parameter.default for parameter in list(signature.parameters.values())[:29]]
    values[4] = str(tmp_path)
    values[26] = context
    values[27] = str(overrides)
    values[28] = 1

    result = plugin.bridge_render(*values, blocking=0, auto_preview=0)

    exported = next(item for item in calls if item[0] == "export")
    submitted = next(item for item in calls if item[0] == "submit")
    assert exported[2] is context
    assert submitted[3]["overrides"] == {"camera": {"lens_mm": 73}}
    assert submitted[3]["preserve_view"] is True
    assert result["job_id"] == "job"


@pytest.mark.parametrize(
    "invalid",
    [
        {"blocking": 2},
        {"blocking": ""},
        {"blocking": "later"},
        {"auto_preview": ""},
        {"auto_preview": "sometimes"},
        {"rerender": 2},
        {"rerender": "again"},
    ],
)
def test_correction_invalid_lifecycle_boolean_fails_before_side_effects(
    monkeypatch, tmp_path, invalid
):
    calls = []
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ImmediateClient(calls), calls)

    with pytest.raises(ValueError, match="must be one of"):
        plugin.bridge_render(_self=SimpleNamespace(), output_dir=tmp_path, **invalid)

    assert calls == []


@pytest.mark.parametrize(
    "call",
    [
        lambda: plugin.bridge_result(job_id="job", wait="eventually"),
        lambda: plugin.bridge_result(job_id="job", present="maybe"),
        lambda: plugin.bridge_recover("job", blocking="later"),
        lambda: plugin.bridge_recover("job", auto_preview="sometimes"),
        lambda: plugin.bridge_recover("job", transfer_recovery=2),
    ],
)
def test_correction_action_booleans_are_strict_before_client_construction(monkeypatch, call):
    import blender_render.client

    monkeypatch.setattr(
        blender_render.client,
        "configured_client",
        lambda *args, **kwargs: pytest.fail("invalid lifecycle value constructed a client"),
    )
    with pytest.raises(ValueError, match="must be one of"):
        call()


@pytest.mark.parametrize("blocked", ["connection", "upload", "submit"])
def test_correction_admission_timeout_is_bounded_and_never_retries(
    monkeypatch, tmp_path, blocked
):
    calls = []
    patch_payload(monkeypatch, calls)
    import blender_render.client

    class BlockingClient(ImmediateClient):
        def upload(self, payload):
            calls.append(("upload",))
            if blocked == "upload":
                time.sleep(1)
            return {"input_id": "input"}

        def submit(self, *args, **kwargs):
            calls.append(("submit",))
            if blocked == "submit":
                time.sleep(1)
            return {"job_id": "job", "status": "queued"}

    def configured(*args, **kwargs):
        calls.append(("client", kwargs))
        if blocked == "connection":
            time.sleep(1)
        return BlockingClient(calls)

    monkeypatch.setattr(blender_render.client, "configured_client", configured)
    started = time.monotonic()
    with pytest.raises(ClientError) as caught:
        plugin.bridge_render(
            _self=SimpleNamespace(),
            output_dir=tmp_path,
            blocking=0,
            auto_preview=0,
            operation_key="stable-admission-key",
            admission_timeout=.05,
        )
    elapsed = time.monotonic() - started

    assert elapsed < .5
    assert [item[0] for item in calls].count("submit") <= 1
    if blocked == "submit":
        assert caught.value.code == "execution_unknown"
        assert caught.value.handle.startswith("pymol-")
        assert caught.value.request_key == "stable-admission-key"
        assert caught.value.operation_key == "stable-admission-key"
        task = next(iter(plugin._TASKS.values()))
        assert task.operation_key == "stable-admission-key"
        assert task.condition == "execution_unknown"


def test_admission_preserves_an_existing_terminal_operation(monkeypatch, tmp_path):
    calls = []

    class ReplayedTerminalClient(ImmediateClient):
        def submit(self, input_id, options, **identity):
            calls.append(("submit", input_id, options, identity))
            return {
                "job_id": "existing-job",
                "status": "cancelled",
                "stage": "cancelled",
                "next_action": "stop",
            }

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ReplayedTerminalClient(calls), calls)

    handle = plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        blocking=0,
        auto_preview=0,
        operation_key="existing-operation",
    )

    assert handle["job_id"] == "existing-job"
    assert handle["status"] == "cancelled"
    assert handle["stage"] == "cancelled"
    assert not any(item[0] in {"wait", "download"} for item in calls)


def test_changed_intent_with_same_operation_key_surfaces_conflict_without_retry(
    monkeypatch, tmp_path
):
    calls = []
    accepted = {"options": None}

    class ConflictClient(ImmediateClient):
        def submit(self, input_id, options, **identity):
            calls.append(("submit", options, identity))
            if accepted["options"] is None:
                accepted["options"] = dict(options)
                return {"job_id": "first-job", "status": "queued"}
            if options != accepted["options"]:
                raise ClientError(
                    "private service detail must not escape",
                    409,
                    "operation_conflict",
                    next_action="inspect_operation_key",
                )
            return {"job_id": "first-job", "status": "queued"}

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ConflictClient(calls), calls)
    plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        output_name="first.png",
        width=640,
        blocking=0,
        auto_preview=0,
        operation_key="stable-operation",
    )

    with pytest.raises(ClientError) as caught:
        plugin.bridge_render(
            _self=SimpleNamespace(),
            output_dir=tmp_path,
            output_name="second.png",
            width=800,
            blocking=0,
            auto_preview=0,
            operation_key="stable-operation",
        )

    assert caught.value.code == "operation_conflict"
    assert "private service detail" not in str(caught.value)
    assert [item[0] for item in calls].count("submit") == 2


def test_correction_registry_contains_only_safe_bounded_metadata(monkeypatch, tmp_path):
    calls = []
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ImmediateClient(calls), calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )
    task = plugin._get_task(handle)

    def walk(value):
        if hasattr(value, "__dataclass_fields__"):
            yield value
            for name in value.__dataclass_fields__:
                yield from walk(getattr(value, name))
        elif isinstance(value, dict):
            yield value
            for key, item in value.items():
                yield from walk(key)
                yield from walk(item)
        elif isinstance(value, (list, tuple, set)):
            yield value
            for item in value:
                yield from walk(item)
        else:
            yield value

    retained = list(walk(task))
    assert not any(isinstance(item, ImmediateClient) for item in retained)
    assert not any(isinstance(item, threading.Thread) for item in retained)
    serialized = repr(task).lower()
    assert all(word not in serialized for word in (
        "bearer", "token", "signed_url", "cif_text", "manifest"
    ))
    registry = json.loads(plugin._reservation_path().read_text())
    encoded = json.dumps(registry, sort_keys=True).lower()
    assert "cif_text" not in encoded
    assert "signed_url" not in encoded
    assert "bearer" not in encoded
    assert "complete_manifest" not in encoded
    assert "secret-token-sentinel" not in encoded


def test_status_discards_private_remote_error_text(monkeypatch, tmp_path):
    calls = []
    sentinel = "PRIVATE-TOKEN-AND-/lab/molecule.cif"

    class PrivateErrorClient(ImmediateClient):
        def status(self, job_id):
            return {
                "job_id": job_id,
                "status": "failed",
                "stage": "failed",
                "condition": "render_failed",
                "next_action": "stop",
                "error": sentinel,
                "signed_url": "https://private.invalid/?token=secret",
            }

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, PrivateErrorClient(calls), calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )

    result = plugin.bridge_status(handle)

    assert result["condition"] == "render_failed"
    assert sentinel not in json.dumps(result)
    assert "private.invalid" not in json.dumps(result)


def test_status_rejects_token_shaped_remote_fields(monkeypatch, tmp_path):
    calls = []
    sentinel = "SECRETBEARERTOKEN123456789"

    class PrivateFieldsClient(ImmediateClient):
        def status(self, job_id):
            return {
                "job_id": job_id,
                "status": "running",
                "stage": sentinel,
                "condition": sentinel,
                "next_action": sentinel,
                "expires_at": sentinel,
            }

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, PrivateFieldsClient(calls), calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )

    result = plugin.bridge_status(handle)

    assert result["status"] == "running"
    assert result["stage"] == "status_unknown"
    assert result["condition"] == "execution_unknown"
    assert result["next_action"] == "inspect_execution"
    assert result["expires_at"] is None
    assert sentinel not in json.dumps(result)


def test_status_preserves_safe_compute_unavailable_category(monkeypatch, tmp_path):
    calls = []

    class ComputeUnavailableClient(ImmediateClient):
        def status(self, job_id):
            return {
                "job_id": job_id,
                "status": "failed",
                "stage": "failed",
                "error_category": "compute_unavailable",
                "next_action": "stop",
            }

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ComputeUnavailableClient(calls), calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )

    result = plugin.bridge_status(handle)

    assert result["status"] == "failed"
    assert result["condition"] == "compute_unavailable"
    assert result["next_action"] == "stop"


def test_same_job_id_in_multiple_profiles_requires_explicit_scope(monkeypatch, tmp_path):
    first = plugin._RenderTask(
        handle="pymol-profile-a",
        job_id="shared-job",
        operation_key="operation-a",
        profile="profile-a",
        connection={"kind": "configured", "profile": "profile-a"},
        output=tmp_path / "a.png",
    )
    second = plugin._RenderTask(
        handle="pymol-profile-b",
        job_id="shared-job",
        operation_key="operation-b",
        profile="profile-b",
        connection={"kind": "configured", "profile": "profile-b"},
        output=tmp_path / "b.png",
    )
    plugin._register_task(first)
    plugin._register_task(second)

    with pytest.raises(ValueError, match="multiple service scopes"):
        plugin.bridge_status(job_id="shared-job")

    calls = []
    patch_client(monkeypatch, ImmediateClient(calls), calls)
    result = plugin.bridge_status(job_id="shared-job", profile="profile-a")
    assert result["handle"] == "pymol-profile-a"
    configured = next(item for item in calls if item[0] == "client")
    assert configured[2] == "profile-a"


def test_direct_endpoint_credentials_fail_before_scene_or_registry(monkeypatch, tmp_path):
    calls = []
    patch_payload(monkeypatch, calls)

    with pytest.raises(ValueError, match="credential-free"):
        plugin.bridge_render(
            _self=SimpleNamespace(),
            server_host="https://user:secret@example.invalid",
            output_dir=tmp_path,
            blocking=0,
            auto_preview=0,
        )

    assert calls == []
    assert plugin._TASKS == {}
    assert not plugin._reservation_path().exists()


def test_correction_explicit_destinations_remain_reserved_until_result(monkeypatch, tmp_path):
    calls = []
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ImmediateClient(calls), calls)
    plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        output_name="shared.png",
        blocking=0,
        auto_preview=0,
    )

    with pytest.raises(ValueError, match="reserved"):
        plugin.bridge_render(
            _self=SimpleNamespace(),
            output_dir=tmp_path,
            output_name="shared.png",
            blocking=0,
            auto_preview=0,
        )
    assert [item[0] for item in calls].count("submit") == 1


@pytest.mark.parametrize(
    "failure",
    [ValueError("collision"), KeyboardInterrupt("interrupted"), SystemExit("stopped")],
)
def test_initial_reservation_failure_never_retains_a_session_handle(
    monkeypatch, tmp_path, failure
):
    calls = []
    patch_payload(monkeypatch, calls)
    monkeypatch.setattr(plugin, "_reserve", lambda *args, **kwargs: (_ for _ in ()).throw(failure))

    with pytest.raises(type(failure)):
        plugin.bridge_render(
            _self=SimpleNamespace(),
            output_dir=tmp_path,
            output_name="reserved.png",
            blocking=0,
            auto_preview=0,
        )

    assert plugin._TASKS == {}
    assert calls == []


def test_retained_job_honors_explicit_replacement_destination(monkeypatch, tmp_path):
    calls = []
    client = ImmediateClient(calls, complete=False)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        output_name="old.png",
        blocking=0,
        auto_preview=0,
    )

    result = plugin.bridge_result(
        handle,
        output_dir=tmp_path,
        output_name="replacement.png",
        present=0,
        _self=SimpleNamespace(),
    )

    task = plugin._get_task(handle)
    assert task.output == (tmp_path / "replacement.png").resolve()
    assert result["outputs"]["render.png"] == str(task.output)
    record = plugin._read_reservations(plugin._reservation_path())[0]
    assert str((tmp_path / "old.png").resolve()) in record["targets"]
    assert str(task.output) in record["targets"]


def test_retarget_collision_leaves_task_and_reservation_unchanged(tmp_path):
    connection = {"kind": "configured", "profile": "local"}
    first = plugin._RenderTask(
        handle="pymol-first",
        job_id="first-job",
        operation_key="first-operation",
        profile="local",
        connection=connection,
        output=(tmp_path / "first.png").resolve(),
    )
    second = plugin._RenderTask(
        handle="pymol-second",
        job_id="second-job",
        operation_key="second-operation",
        profile="local",
        connection=connection,
        output=(tmp_path / "reserved.png").resolve(),
    )
    for task in (first, second):
        plugin._register_task(task)
        plugin._reserve(task)
    before = plugin._reservation_path().read_bytes()

    with pytest.raises(ValueError, match="reserved"):
        with plugin._bounded_action(time.monotonic() + 1, first):
            plugin._retarget_task_if_requested(
                first,
                output_dir=tmp_path,
                output_name="reserved.png",
                deadline=time.monotonic() + 1,
            )

    assert first.output == (tmp_path / "first.png").resolve()
    assert plugin._reservation_path().read_bytes() == before


def test_post_admission_reservation_failure_recovers_to_distinct_output(
    monkeypatch, tmp_path
):
    calls = []
    client = ImmediateClient(calls)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    reserve = plugin._reserve

    def fail_admitted(task, **kwargs):
        if task.job_id is not None:
            raise ValueError("injected destination collision")
        return reserve(task, **kwargs)

    monkeypatch.setattr(plugin, "_reserve", fail_admitted)
    with pytest.raises(ClientError) as caught:
        plugin.bridge_render(
            _self=SimpleNamespace(),
            output_dir=tmp_path,
            blocking=0,
            auto_preview=0,
        )
    assert caught.value.code == "recovery_required"
    task = plugin._get_task(caught.value.handle)
    assert task.destination_recovery_required
    monkeypatch.setattr(plugin, "_reserve", reserve)

    result = plugin.bridge_result(
        caught.value.handle,
        output_dir=tmp_path,
        output_name="distinct.png",
        present=0,
        _self=SimpleNamespace(),
    )

    assert result["complete"] is True
    assert Path(result["outputs"]["render.png"]) == (tmp_path / "distinct.png").resolve()
    assert not task.destination_recovery_required


@pytest.mark.parametrize("conflict_name", ["shared.blend", "shared.manifest.json", "shared.mp4", "shared.gif", "shared.frames.zip"])
def test_implicit_blend_and_manifest_destinations_are_collision_protected(
    monkeypatch, tmp_path, conflict_name
):
    calls = []
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ImmediateClient(calls), calls)
    plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        output_name="shared.png",
        blocking=0,
        auto_preview=0,
    )

    with pytest.raises(ValueError, match="reserved"):
        plugin.bridge_render(
            _self=SimpleNamespace(),
            output_dir=tmp_path,
            output_name="other.png",
            blend_output=tmp_path / conflict_name,
            blocking=0,
            auto_preview=0,
        )
    assert [item[0] for item in calls].count("submit") == 1


def test_explicit_blend_destination_is_copied_and_checksum_verified(monkeypatch, tmp_path):
    calls = []

    class BlendClient(ImmediateClient):
        def download(self, job_id, output):
            png, blend = b"PNG", b"BLENDER-SCENE"
            Path(output).write_bytes(png)
            implicit = Path(output).with_suffix(".blend")
            implicit.write_bytes(blend)
            calls.append(("download", job_id, str(output)))
            return {
                "job_id": job_id,
                "outputs": {"render.png": str(output), "render.blend": str(implicit)},
                "complete": True,
                "missing_required": [],
                "artifacts": [
                    {
                        "name": "render.png",
                        "sha256": hashlib.sha256(png).hexdigest(),
                        "size": len(png),
                        "required": True,
                        "verified_locally": True,
                    },
                    {
                        "name": "render.blend",
                        "sha256": hashlib.sha256(blend).hexdigest(),
                        "size": len(blend),
                        "required": True,
                        "verified_locally": True,
                    },
                ],
            }

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, BlendClient(calls), calls)
    selected = tmp_path / "selected" / "scene.blend"
    handle = plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        output_name="with-scene.png",
        blend_output=selected,
        blocking=0,
        auto_preview=0,
    )

    result = plugin.bridge_result(handle, present=0, _self=SimpleNamespace())

    assert result["complete"] is True
    assert result["outputs"]["render.blend"] == str(selected.resolve())
    assert selected.read_bytes() == b"BLENDER-SCENE"


def test_correction_default_nonblocking_destinations_are_job_qualified(monkeypatch, tmp_path):
    calls = []
    counter = iter(["job-one", "job-two"])

    class SequencedClient(ImmediateClient):
        def submit(self, input_id, options, **identity):
            job_id = next(counter)
            calls.append(("submit", job_id))
            return {"job_id": job_id, "status": "queued", "stage": "waiting_for_compute"}

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, SequencedClient(calls), calls)
    first = plugin.bridge_render(_self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0)
    second = plugin.bridge_render(_self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0)

    first_path = plugin._get_task(first).output
    second_path = plugin._get_task(second).output
    assert first_path != second_path
    assert first_path.name.startswith("render-") and second_path.name.startswith("render-")


@pytest.fixture
def receipt_backed_jobs(tmp_path):
    """Real receipt/delivery layer over an in-process fake render service."""
    import base64
    import io

    from fastapi.testclient import TestClient
    from PIL import Image

    from blender_render.api import create_app
    from blender_render.client import Client
    from blender_render.jobs import JobStore
    from blender_render.service import RenderService
    from test_jobs_api import TOKEN, payload as payload_fixture

    class DifferentPngCompute:
        def __init__(self):
            self.calls = []

        def render(self, request, job_id, cancelled):
            self.calls.append(job_id)
            stream = io.BytesIO()
            color = (20 + len(self.calls), 90, 140, 255)
            Image.new("RGBA", (4, 3), color).save(stream, format="PNG")
            return {
                "status": "completed",
                "png_base64": base64.b64encode(stream.getvalue()).decode(),
                "blend_base64": base64.b64encode(("BLEND-" + job_id).encode()).decode(),
                "manifest": {"fixture": len(self.calls)},
                "runtime_seconds": 0.01,
            }

        def stop(self):
            pass

    store = JobStore(tmp_path / "service-state")
    compute = DifferentPngCompute()
    service = RenderService(store, compute)
    payload = payload_fixture.__wrapped__()
    identifiers = []
    for index in range(4):
        source = service.import_payload("owner", payload)["input_id"]
        job = service.submit(
            "owner",
            source,
            {"save_blend": True, "width": 640 + index},
            f"transport-{index}",
            operation_key=f"receipt-job-{index}",
        )
        service._execute(store.claim())
        identifiers.append(job["job_id"])
    api = TestClient(create_app(service, token=TOKEN, tokens={TOKEN: "owner"}))
    fail_blend = {"value": False}

    class ReceiptClient(Client):
        def __init__(self):
            super().__init__("http://127.0.0.1:39123", TOKEN)

        def request(self, method, path, body=None, *, binary=False, **kwargs):
            if fail_blend["value"] and method == "GET" and "/artifacts/render.blend" in path:
                raise ClientError("simulated interrupted blend transfer")
            response = api.request(
                method,
                path,
                json=body,
                headers={"Authorization": "Bearer " + self.token},
            )
            if response.status_code >= 400:
                problem = response.json()
                raise ClientError(
                    problem.get("error", "request failed"),
                    response.status_code,
                    problem.get("code", "http_error"),
                )
            return response.content if binary else response.json()

    yield SimpleNamespace(
        identifiers=identifiers,
        client=ReceiptClient,
        fail_blend=fail_blend,
        compute=compute,
    )
    api.close()
    store.close()


def test_receipt_rejects_manual_png_mutation_without_presentation(
    monkeypatch, tmp_path, receipt_backed_jobs
):
    import blender_render.client

    monkeypatch.setattr(
        blender_render.client, "configured_client", lambda *args, **kwargs: receipt_backed_jobs.client()
    )
    identifier = receipt_backed_jobs.identifiers[0]
    handle = plugin.bridge_recover(
        identifier,
        profile="local",
        output_dir=tmp_path,
        output_name="mutated.png",
        blocking=0,
        auto_preview=0,
    )
    first = plugin.bridge_result(handle, present=0, _self=SimpleNamespace())
    output = Path(first["outputs"]["render.png"])
    output.write_bytes(b"manual mutation")
    presentations = []

    recovered = plugin.bridge_result(
        handle,
        present=1,
        _self=SimpleNamespace(load_png=lambda *args, **kwargs: presentations.append(args)),
    )

    assert recovered["job_id"] == identifier
    assert recovered["complete"] is False
    assert recovered["outputs"].get("render.png") is None
    assert "render.png" in recovered["missing_required"]
    assert recovered["error_code"] == "artifact_unavailable"
    assert presentations == []


def test_valid_receipt_reconstructs_exact_png_after_module_reload(
    monkeypatch, tmp_path, receipt_backed_jobs
):
    import blender_render.client

    monkeypatch.setattr(
        blender_render.client, "configured_client", lambda *args, **kwargs: receipt_backed_jobs.client()
    )
    identifier = receipt_backed_jobs.identifiers[1]
    handle = plugin.bridge_recover(
        identifier,
        profile="local",
        output_dir=tmp_path,
        output_name="original.png",
        blocking=0,
        auto_preview=0,
    )
    original = plugin.bridge_result(handle, present=0, _self=SimpleNamespace())
    exact = Path(original["outputs"]["render.png"]).read_bytes()
    with plugin._TASKS_LOCK:
        plugin._TASKS.clear()
    importlib.reload(plugin)

    reconstructed = plugin.bridge_result(
        job_id=identifier,
        profile="local",
        output_dir=tmp_path,
        output_name="reconstructed.png",
        present=0,
        _self=SimpleNamespace(),
    )

    assert reconstructed["complete"] is True
    assert Path(reconstructed["outputs"]["render.png"]).read_bytes() == exact
    assert reconstructed["artifacts"]["render.png"]["sha256"] == hashlib.sha256(exact).hexdigest()


def test_older_handle_never_presents_newer_jobs_overwrite(
    monkeypatch, tmp_path, receipt_backed_jobs
):
    import blender_render.client

    monkeypatch.setattr(
        blender_render.client, "configured_client", lambda *args, **kwargs: receipt_backed_jobs.client()
    )
    first_job, second_job = receipt_backed_jobs.identifiers[2:4]
    first_handle = plugin.bridge_recover(
        first_job,
        profile="local",
        output_dir=tmp_path,
        output_name="shared.png",
        blocking=0,
        auto_preview=0,
    )
    first = plugin.bridge_result(first_handle, present=0, _self=SimpleNamespace())
    first_bytes = Path(first["outputs"]["render.png"]).read_bytes()
    second_handle = plugin.bridge_recover(
        second_job,
        profile="local",
        output_dir=tmp_path,
        output_name="shared.png",
        blocking=0,
        auto_preview=0,
    )
    second = plugin.bridge_result(second_handle, present=0, _self=SimpleNamespace())
    output = Path(second["outputs"]["render.png"])
    second_bytes = output.read_bytes()
    assert first_bytes != second_bytes
    presentations = []

    old = plugin.bridge_result(
        first_handle,
        present=1,
        _self=SimpleNamespace(load_png=lambda *args, **kwargs: presentations.append(args)),
    )

    assert old["job_id"] == first_job
    assert old["complete"] is False
    assert "render.png" not in old["outputs"]
    assert output.read_bytes() == second_bytes
    assert presentations == []


def test_receipt_partial_delivery_retains_png_and_recovers_only_missing_blend(
    monkeypatch, tmp_path, receipt_backed_jobs
):
    import blender_render.client

    monkeypatch.setattr(
        blender_render.client, "configured_client", lambda *args, **kwargs: receipt_backed_jobs.client()
    )
    identifier = receipt_backed_jobs.identifiers[0]
    receipt_backed_jobs.fail_blend["value"] = True
    handle = plugin.bridge_recover(
        identifier,
        profile="local",
        output_dir=tmp_path,
        output_name="partial.png",
        blocking=0,
        auto_preview=0,
    )

    partial = plugin.bridge_result(handle, present=0, _self=SimpleNamespace())

    assert partial["complete"] is False
    assert Path(partial["outputs"]["render.png"]).is_file()
    assert partial["missing_required"] == ["render.blend"]
    receipt_backed_jobs.fail_blend["value"] = False
    completed = plugin.bridge_result(handle, present=0, _self=SimpleNamespace())
    assert completed["complete"] is True
    assert set(completed["outputs"]) == {"render.png", "render.blend", "manifest.json"}
    assert len(receipt_backed_jobs.compute.calls) == 4


def test_destination_reservation_survives_module_reload(monkeypatch, tmp_path):
    calls = []
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ImmediateClient(calls), calls)
    plugin.bridge_render(
        _self=SimpleNamespace(),
        output_dir=tmp_path,
        output_name="reload-shared.png",
        blocking=0,
        auto_preview=0,
    )
    importlib.reload(plugin)

    with pytest.raises(ValueError, match="reserved"):
        plugin.bridge_render(
            _self=SimpleNamespace(),
            output_dir=tmp_path,
            output_name="reload-shared.png",
            blocking=0,
            auto_preview=0,
        )


def test_correction_registration_is_idempotent():
    expected = [
        "blender_render",
        "blender_render_status",
        "blender_render_result",
        "blender_render_cancel",
        "blender_render_recover",
        "blender_movie_export",
    ]
    registered = []
    recorder = SimpleNamespace(extend=lambda name, function: registered.append(name))
    plugin.register_plugin(recorder)
    plugin.register_plugin(recorder)
    assert registered == expected


def test_correction_reload_cannot_leave_background_activity(monkeypatch, tmp_path):
    calls = []
    release = threading.Event()

    class GatedClient(ImmediateClient):
        def wait(self, *args, **kwargs):
            calls.append(("wait",))
            release.wait(1)

        def download(self, *args, **kwargs):
            calls.append(("download",))
            return super().download(*args, **kwargs)

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, GatedClient(calls), calls)
    plugin.bridge_render(
        _self=SimpleNamespace(), output_dir=tmp_path, blocking=0, auto_preview=0
    )
    importlib.reload(plugin)
    release.set()
    time.sleep(.05)

    assert not any(item[0] in {"wait", "download"} for item in calls)
    assert not any(thread.name.startswith("blender-render-") for thread in threading.enumerate())


def test_unscoped_reloaded_job_refuses_forged_route_and_destination(
    monkeypatch, tmp_path
):
    import blender_render.client

    victim = tmp_path / "victim.png"
    victim.write_bytes(b"operator data")
    reservation = plugin._reservation_path()
    reservation.parent.mkdir(parents=True)
    reservation.write_text(json.dumps({
        "version": plugin._RESERVATION_VERSION,
        "reservations": [{
            "handle": "pymol-forged",
            "job_id": "known-job",
            "scope": "0" * 64,
            "targets": [str(victim.resolve())],
            "created_at": time.time(),
        }],
    }))
    monkeypatch.setattr(
        blender_render.client,
        "configured_client",
        lambda *args, **kwargs: pytest.fail("unscoped job selected a client"),
    )

    with pytest.raises(ValueError, match="original profile"):
        plugin.bridge_result(job_id="known-job", output_dir=tmp_path, present=0)

    assert victim.read_bytes() == b"operator data"


def test_saved_reservation_never_redirects_scoped_recovery(monkeypatch, tmp_path):
    victim = tmp_path / "victim.png"
    victim.write_bytes(b"operator data")
    reservation = plugin._reservation_path()
    reservation.parent.mkdir(parents=True)
    reservation.write_text(json.dumps({
        "version": plugin._RESERVATION_VERSION,
        "reservations": [{
            "handle": "pymol-forged",
            "job_id": "known-job",
            "scope": "0" * 64,
            "targets": [str(victim.resolve())],
            "created_at": time.time(),
        }],
    }))
    calls = []
    patch_client(monkeypatch, ImmediateClient(calls), calls)
    destination = tmp_path / "chosen"

    result = plugin.bridge_result(
        job_id="known-job",
        profile="local",
        output_dir=destination,
        present=0,
        _self=SimpleNamespace(),
    )

    output = Path(result["outputs"]["render.png"])
    assert output.parent == destination.resolve()
    assert output.name.startswith("render-")
    assert victim.read_bytes() == b"operator data"
    assert next(item for item in calls if item[0] == "client")[2] == "local"


def test_status_preserves_valid_bound_consumer_identity(monkeypatch, tmp_path):
    from blender_render import consumer

    request = consumer.normalize_request(
        {"request_key": "pymol-status", "preset": "pastel"}, trusted=True
    )
    decision = consumer.preselect(request, consumer.server_policy(False))
    metadata = consumer.metadata(
        decision,
        request=request,
        fingerprint="1" * 64,
        phase="bound",
        backend_started=True,
    )
    expected = consumer.safe_decision(decision)
    calls = []

    class IdentityClient(ImmediateClient):
        def status(self, job_id):
            return {
                "job_id": job_id,
                "status": "running",
                "stage": "rendering",
                "next_action": "render_status",
                "render_spec_sha256": "2" * 64,
                **{key: metadata[key] for key in (
                    "style_source",
                    "style_identity",
                    "preselection_decision",
                    "preset_decision",
                    "selection_phase",
                    "backend_render_started",
                    "selection_error",
                )},
            }

    patch_client(monkeypatch, IdentityClient(calls), calls)
    task = plugin._RenderTask(
        handle="pymol-identity",
        job_id="known-job",
        operation_key="identity",
        profile="local",
        connection={"kind": "configured", "profile": "local"},
        output=tmp_path / "identity.png",
    )
    plugin._register_task(task)

    result = plugin.bridge_status(task.handle)

    assert result["preset_decision"] == expected
    assert result["render_spec_sha256"] == "2" * 64
    assert result["style_identity"] == metadata["style_identity"]


def test_blocking_partial_result_raises_typed_error_with_verified_outputs(
    monkeypatch, tmp_path
):
    from blender_render.client import IncompleteResultError

    calls = []
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, ImmediateClient(calls, complete=False), calls)

    with pytest.raises(IncompleteResultError) as caught:
        plugin.bridge_render(
            _self=SimpleNamespace(),
            output_dir=tmp_path,
            save_blend=1,
            auto_preview=0,
        )

    result = caught.value.result
    assert result["job_id"] == "job"
    assert result["missing_required"] == ["render.blend"]
    assert Path(result["outputs"]["render.png"]).read_bytes() == b"PNG"
    assert caught.value.next_action == "recover_verified_local_copy_or_stop"


def test_result_receipt_lock_obeys_absolute_deadline(tmp_path):
    import fcntl

    from blender_render.client import Client
    from blender_render.config import config_root

    client = Client("http://127.0.0.1:8765", "test-token")
    scope = hashlib.sha256(
        (client.url + "\0" + client.token + "\0known-job").encode()
    ).hexdigest()
    lock_path = config_root() / "result-receipts" / (scope + ".lock")
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        with pytest.raises(ClientError) as caught:
            client.download(
                "known-job",
                tmp_path / "output.png",
                deadline=time.monotonic() + 0.05,
            )
        elapsed = time.monotonic() - started

    assert caught.value.code == "operation_timeout"
    assert elapsed < 0.5


def test_action_lock_obeys_command_timeout(tmp_path):
    acquired = threading.Event()
    release = threading.Event()

    def hold_action_lock():
        with plugin._ACTION_LOCK:
            acquired.set()
            release.wait(2)

    holder = threading.Thread(target=hold_action_lock)
    holder.start()
    assert acquired.wait(1)
    task = plugin._RenderTask(
        handle="pymol-locked",
        job_id="known-job",
        operation_key="locked",
        profile="local",
        connection={"kind": "configured", "profile": "local"},
        output=tmp_path / "locked.png",
    )
    plugin._register_task(task)
    try:
        started = time.monotonic()
        with pytest.raises(ClientError) as caught:
            plugin.bridge_status(task.handle, timeout=0.05)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(1)

    assert caught.value.code == "operation_timeout"
    assert elapsed < 0.5


def test_submit_existing_intention_allows_post_without_starting(monkeypatch):
    from blender_render import client

    calls = []

    class Existing:
        def request(self, method, path, body=None, **kwargs):
            calls.append((method, path, body))
            return {"input_id": "existing-input"}

    def connect(profile, **kwargs):
        calls.append(("connect", profile, kwargs["intention"]))
        return Existing()

    monkeypatch.setattr(client, "_connect_client", connect)
    configured = client.configured_client(
        "local", intention=client.ConnectionIntention.SUBMIT_EXISTING
    )

    assert configured.upload({"version": "1.0"}) == {"input_id": "existing-input"}
    assert calls[0] == (
        "connect", "local", client.ConnectionIntention.SUBMIT_EXISTING
    )
    assert calls[1][0:2] == ("POST", "/v1/structures")


def test_partial_animation_does_not_present_still(monkeypatch, tmp_path):
    calls = []
    client = ImmediateClient(calls, complete=False)
    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, client, calls)
    handle = plugin.bridge_render(_self=SimpleNamespace(), output_dir=tmp_path,
                                  output_name="movie.png", blocking=0, auto_preview=0)
    task = plugin._get_task(handle)
    task.required_outputs = ("render.png", "render.mp4", "frames.zip")
    recorder = PresentationRecorder()
    result = plugin.bridge_result(handle, present=1, _self=recorder)
    assert not result["complete"]
    assert result["presentation_status"] == "not_attempted"
    assert recorder.preview_threads == []


@pytest.mark.parametrize('blocking', [0, 1])
def test_movie_delivery_preserves_archive_and_auto_preview_zero(monkeypatch, tmp_path, blocking):
    from PIL import Image
    from blender_render.animation import package_frames
    frames = tmp_path / 'source-frames'
    frames.mkdir()
    for index in range(4):
        Image.new('RGBA', (32, 24), 'blue').save(frames / f'{index:04d}.png')
    archive = tmp_path / 'source.zip'
    package_frames(frames, archive, dict(mode='rock', duration=1, fps=4), 32, 24)
    calls = []

    class MovieClient(ImmediateClient):
        def download(self, job_id, output):
            result = super().download(job_id, output)
            for name, suffix, raw in [('render.mp4', '.mp4', b'fake-mp4'),
                                      ('frames.zip', '.frames.zip', archive.read_bytes())]:
                target = Path(output).with_suffix(suffix)
                target.write_bytes(raw)
                result['outputs'][name] = str(target)
                result['artifacts'].append(dict(name=name, size=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest(), required=True, verified_locally=True))
            return result

    patch_payload(monkeypatch, calls)
    patch_client(monkeypatch, MovieClient(calls), calls)
    cmd = PresentationRecorder()
    result = plugin.bridge_render(_self=cmd, output_dir=tmp_path, output_name='animation.png',
        animation='rock', duration=1, fps=4, width=32, height=24, blocking=blocking, auto_preview=0)
    if not blocking:
        result = plugin.bridge_result(result, present=0, _self=cmd)
    assert result['complete']
    assert result['presentation_status'] == 'not_attempted'
    assert cmd.preview_threads == []
    assert Path(cmd._blender_render_movie_archive[0]).read_bytes() == archive.read_bytes()
    assert [item[0] for item in calls].count('submit') == 1
