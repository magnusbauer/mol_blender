"""Modal deployment: one durable controller, one L4, isolated file conversion.

Deploy with the CLI so the previous controller is drained and stopped first.
Worker functions never receive the controller's OAuth or signing secrets.
"""
from __future__ import annotations
import json
import os
import threading
import time
from pathlib import Path

import modal

PUBLIC = os.environ.get("BLENDER_RENDER_PUBLIC") == "1"
APP_NAME = os.environ.get("BLENDER_RENDER_APP_NAME") or ("blender-render-beta" if PUBLIC else "blender-render-beta-dev")
PERSONAL = APP_NAME not in {"blender-render-beta", "blender-render-beta-dev"}
OWNER_ANIMATION = os.environ.get('BLENDER_RENDER_OWNER_ANIMATION', '')
from .modal_boundaries import animation_enabled
ALLOW_ANIMATION = animation_enabled(personal=PERSONAL, public=PUBLIC, owner=OWNER_ANIMATION,
    allowed_users=os.environ.get('BLENDER_RENDER_ALLOWED_USERS', ''))
JOB_TIMEOUT = int(os.environ.get("BLENDER_RENDER_JOB_TIMEOUT", "600"))
app = modal.App(APP_NAME)
from .modal_boundaries import package_snapshot
# Modal consumes local files lazily. Retain this private verified capture for
# the module lifetime so image descriptors never upload caches or changed source.
_package_snapshot = package_snapshot(Path(__file__).parent)
package = _package_snapshot.__enter__()
shared_env = {"PYTHONPATH":"/opt:/opt/runtime/site", "BLENDER_RENDER_SITE":"/opt/runtime/site", "BLENDER_RENDER_PUBLIC":"1" if PUBLIC else "0", "PYTHONUNBUFFERED":"1"}
shared_env.update(BLENDER_RENDER_APP_NAME=APP_NAME, BLENDER_RENDER_JOB_TIMEOUT=str(JOB_TIMEOUT))
if OWNER_ANIMATION:
    shared_env.update(BLENDER_RENDER_OWNER_ANIMATION=OWNER_ANIMATION,
                      BLENDER_RENDER_ALLOWED_USERS=os.environ.get('BLENDER_RENDER_ALLOWED_USERS', ''))

controller_image = (modal.Image.debian_slim(python_version="3.11")
    .add_local_file(package / "service-requirements.txt", "/tmp/service-requirements.txt", copy=True)
    .run_commands("python -m pip install --require-hashes --only-binary=:all: -r /tmp/service-requirements.txt")
    .env(shared_env).add_local_dir(package, "/opt/blender_render", copy=True))

renderer_image = (modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "curl", "xz-utils", "libgl1", "libegl1", "libx11-6", "libxi6", "libxrender1", "libxfixes3", "libxxf86vm1", "libxkbcommon0", "libsm6", "libice6", "libglib2.0-0", "libfontconfig1", "libxrandr2", "libxinerama1", "libxcursor1", "libwayland-client0", "libpulse0")
    .env(shared_env))
for filename in ("__init__.py", "config.py", "engine.py", "runtime.lock.json", "runtime-requirements.txt", "runtime_install.py", "runtime_initialize.py"):
    renderer_image = renderer_image.add_local_file(package / filename, "/opt/blender_render/" + filename, copy=True)
renderer_image = (renderer_image.run_commands("python -c 'from blender_render.runtime_install import install; install(root=\"/opt/runtime\")'")
    .add_local_file(package / "service-requirements.txt", "/tmp/service-requirements.txt", copy=True)
    # Hide Blender's --target directory from pip's installed-package discovery:
    # otherwise upgrading an HTTP dependency can uninstall a renderer wheel.
    .run_commands("PYTHONPATH= python -m pip install --require-hashes --only-binary=:all: -r /tmp/service-requirements.txt")
    .add_local_dir(package, "/opt/blender_render", copy=True)
    .run_commands('/opt/runtime/blender-5.0.1-linux-x64/blender --background --python-use-system-env --python-exit-code 1 --python /opt/blender_render/runtime_initialize.py'))

converter_image = (modal.Image.debian_slim(python_version="3.11")
    .apt_install("python3-pymol", "libgl1")
    .env({"QT_QPA_PLATFORM":"offscreen"})
    .add_local_file(package / "export.py", "/opt/export.py", copy=True))

volume = modal.Volume.from_name(APP_NAME + "-state", create_if_missing=True)
controller_secrets = modal.Secret.from_name(APP_NAME + "-credentials")
worker_secrets = modal.Secret.from_name(APP_NAME + "-worker")


@app.server(name='gpu', image=renderer_image, gpu='L4', cpu=(4,4), memory=8192,
            secrets=[worker_secrets], min_containers=0, max_containers=1,
            target_concurrency=1, scaledown_window=2,
            startup_timeout=120, exit_grace_period=JOB_TIMEOUT)
class GPUWorker:
    @modal.enter()
    def start(self):
        from blender_render.worker_http import create_worker
        self.server, self.thread = start_http(create_worker(os.environ['BLENDER_RENDER_WORKER_TOKEN'], timeout=JOB_TIMEOUT, allow_animation=ALLOW_ANIMATION, max_atoms=100000, max_bytes=50*1024**2))

    @modal.exit()
    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def convert_file(data: bytes, suffix: str, *, selection='(all)') -> dict:
    from .modal_boundaries import convert_file as convert
    return convert(data, suffix, selection=selection, create_sandbox=lambda: modal.Sandbox.create(
        "sleep", "90", app=app, image=converter_image, timeout=90, cpu=.5, memory=2048,
        secrets=[], volumes={}, block_network=True, include_oidc_identity_token=False))


def start_http(application):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(application, host='0.0.0.0', port=8000, access_log=False, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


@app.server(name='gateway', image=controller_image, cpu=(.25,.5), memory=1024,
            min_containers=1, max_containers=1, unauthenticated=True,
            volumes={"/state":volume}, secrets=[controller_secrets], startup_timeout=120)
class Gateway:
    @modal.enter()
    def start(self):
        self.server, self.thread = start_http(gateway_application())

    @modal.exit()
    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def gateway_application():
    # A failed database/auth initialization must not leave a paid minimum
    # container restarting indefinitely before the budget monitor exists.
    try:
        return create_gateway()
    except Exception:
        stop_current_app()
        raise


def stop_current_app():
    import subprocess, sys
    if not app.app_id:
        raise RuntimeError("Cannot determine the current Modal app to stop")
    # The CLI uses Modal's current container identity, never worker secrets.
    for attempt in range(3):
        try:
            subprocess.run([sys.executable, "-m", "modal", "app", "stop", app.app_id, "--yes"], check=True, timeout=30)
            return
        except (subprocess.SubprocessError, OSError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def create_gateway():
    from dataclasses import replace
    from blender_render.api import create_app, Downloads
    from blender_render.auth import github_provider
    from blender_render.jobs import Policy, JobStore
    from blender_render.mcp_server import create_http
    from blender_render.service import RenderService
    from blender_render.budget import OperatingBudget
    # Development reserves $10 of its $50 allowance for builds/control/storage.
    policy = Policy(public=True, budget_usd=450 if PUBLIC else 40, allow_animation=ALLOW_ANIMATION)
    if PERSONAL:
        policy = replace(policy, budget_usd=float(os.environ['BLENDER_RENDER_COMPUTE_BUDGET']),
            expires_at=float(os.environ['BLENDER_RENDER_CREDITS_EXPIRE']), max_job_seconds=JOB_TIMEOUT,
            daily_jobs=10, allow_animation=True)
    elif not PUBLIC:
        policy = replace(policy, quota_exempt_owners=('github:development',))
    if APP_NAME == 'blender-render-beta':
        policy = replace(policy, quota_exempt_owners=('github:11480757',))
    store = JobStore(Path("/state"), policy, data_root=Path('/tmp/blender-render-data'), sync=volume.commit)
    # $2 remains reserved for the final 24 hours of retention/cleanup. Public
    # operations total at most $30; development control/storage at most $5.
    operations = OperatingBudget(store, cap_usd=float(os.environ['BLENDER_RENDER_OPERATIONS_BUDGET'])-2 if PERSONAL else (28 if PUBLIC else 3))
    try:
        funded = operations.reserve_hour()
    except Exception:
        funded = False
    token = os.environ["BLENDER_RENDER_ADMIN_TOKEN"]
    url = os.environ["BLENDER_RENDER_PUBLIC_URL"]
    if PUBLIC:
        provider = github_provider(store, client_id=os.environ["GITHUB_CLIENT_ID"], client_secret=os.environ["GITHUB_CLIENT_SECRET"],
            base_url=url, encryption_key=os.environ["BLENDER_RENDER_ENCRYPTION_KEY"], jwt_key=os.environ["BLENDER_RENDER_JWT_KEY"],
            allowed_users=set(os.environ['BLENDER_RENDER_ALLOWED_USERS'].split(',')) if os.environ.get('BLENDER_RENDER_ALLOWED_USERS') else None)
    else:
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
        provider = StaticTokenVerifier(tokens={token:{"client_id":"development", "scopes":[], "sub":"development"}})
    downloads = Downloads(os.environ["BLENDER_RENDER_DOWNLOAD_KEY"], url)
    from blender_render.worker_http import HTTPBackend
    worker_url = modal.Server.from_name(APP_NAME, 'gpu').get_url()
    service = RenderService(store, HTTPBackend(worker_url, os.environ['BLENDER_RENDER_WORKER_TOKEN'], JOB_TIMEOUT,
        proxy_key=os.environ['BLENDER_RENDER_PROXY_KEY'], proxy_secret=os.environ['BLENDER_RENDER_PROXY_SECRET']))
    mcp = create_http(service, auth=provider, downloads=downloads, converter=convert_file,
        widget_origin=os.environ.get('BLENDER_RENDER_WIDGET_ORIGIN'))
    application = create_app(service, token=token, public_url=url, signing_key=os.environ["BLENDER_RENDER_DOWNLOAD_KEY"], auth_provider=provider, mcp_server=mcp, converter=convert_file)
    if funded:
        operations.monitor(service, stop_current_app)
    else:
        store.drain()
        service.stopping.set()
        threading.Thread(target=operations.stop_unfunded, args=(service, stop_current_app), daemon=True).start()
    return application
