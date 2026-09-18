"""Deployment preflight and drain-before-stop to protect a single Volume writer."""
from __future__ import annotations
import json
import os
import secrets
import subprocess
import sys
import time
import re
import math
from datetime import datetime, timezone
from pathlib import Path

from .client import Client
from .config import VERSION, config_root, load_config, save_config
from .runtime import write_private


def deploy(*, development=False, staging_user=None, personal=None, budget_usd=None, expires=None):
    import modal
    name = personal or ("blender-render-beta-dev" if development else "blender-render-beta")
    if personal:
        if development or not re.fullmatch(r'blender-render-[a-z0-9-]{1,40}', name) or name in {'blender-render-beta','blender-render-beta-dev'}:
            raise ValueError('Use a distinct personal app name starting blender-render-')
        if not staging_user or not str(staging_user).isdigit():
            raise ValueError('Personal deployments require --staging-user with your GitHub numeric ID')
        if budget_usd is None or not math.isfinite(budget_usd) or budget_usd < 10 or not expires:
            raise ValueError('Personal deployments require --budget-usd of at least 10 and --credits-expire YYYY-MM-DD')
        expiry = datetime.strptime(expires,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()
        if expiry <= time.time()+86460:
            raise ValueError('Credits must remain valid for more than 24 hours')
    root = config_root() / "deployments" / name
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret_path = root / "credentials.json"
    if secret_path.exists():
        credentials = json.loads(secret_path.read_text())
    else:
        credentials = {key: secrets.token_urlsafe(48) for key in ("BLENDER_RENDER_ADMIN_TOKEN", "BLENDER_RENDER_DOWNLOAD_KEY", "BLENDER_RENDER_ENCRYPTION_KEY", "BLENDER_RENDER_JWT_KEY")}
    animation_owner = str(staging_user or '') if os.environ.get('BLENDER_RENDER_ENABLE_ANIMATION') == '1' else ''
    if os.environ.get('BLENDER_RENDER_ENABLE_ANIMATION') == '1' and not animation_owner.isdigit():
        raise ValueError('Hosted animation requires a single numeric staging user')
    config = load_config()
    workspace = os.environ.get("MODAL_WORKSPACE") or config.get("modal_workspace")
    if not workspace:
        raise ValueError("Set MODAL_WORKSPACE to your Modal workspace slug before deployment.")
    previous_url = credentials.get('BLENDER_RENDER_PUBLIC_URL')
    expected_url = f"https://{workspace}--{name}-gateway.us-east.modal.direct"
    credentials["BLENDER_RENDER_PUBLIC_URL"] = expected_url
    # Keep a short, dedicated widget identity. ChatGPT maps this origin to a
    # sandbox hostname; the longer Server origin would exceed 63 characters.
    credentials['BLENDER_RENDER_WIDGET_ORIGIN'] = os.environ.get('BLENDER_RENDER_WIDGET_ORIGIN') or f'https://{workspace}--{name}.modal.run'
    credentials.setdefault('BLENDER_RENDER_WORKER_TOKEN', secrets.token_urlsafe(48))
    if not credentials.get('BLENDER_RENDER_PROXY_KEY'):
        proxy = modal.Workspace.from_context().proxy_tokens.create()
        credentials['BLENDER_RENDER_PROXY_KEY'] = proxy.token_id
        credentials['BLENDER_RENDER_PROXY_SECRET'] = proxy.token_secret
        write_private(secret_path, json.dumps(credentials))
    if personal:
        operations = min(30, max(3, budget_usd*.2))
        credentials.update(BLENDER_RENDER_COMPUTE_BUDGET=str(budget_usd-operations),
            BLENDER_RENDER_OPERATIONS_BUDGET=str(operations), BLENDER_RENDER_CREDITS_EXPIRE=str(expiry))
    if not development:
        if staging_user is not None and not str(staging_user).isdigit():
            raise ValueError('Use the GitHub numeric user ID for restricted staging')
        credentials['BLENDER_RENDER_ALLOWED_USERS'] = str(staging_user or '')
        for key in ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
            if os.environ.get(key):
                credentials[key] = os.environ[key]
            if not credentials.get(key):
                raise ValueError(key + " is required for the public GitHub OAuth service")
        # Public admission requires completed validation and an operator's
        # explicit release command. Do not expose an unfinished service.
        report = config.get("release_validation")
        if not staging_user:
            from .release_validation import require_release_ready
            require_release_ready(Path(report) if report else None)
    write_private(secret_path, json.dumps(credentials))
    token_path = root / "token"
    write_private(token_path, credentials["BLENDER_RENDER_ADMIN_TOKEN"])
    active = json.loads(subprocess.check_output([sys.executable,"-m","modal","app","list","--json"],text=True))
    current = next((row for row in active if row.get("description") == name and row.get("state") != "stopped"), None)
    if current:
        client = Client(previous_url or expected_url, credentials["BLENDER_RENDER_ADMIN_TOKEN"], timeout=30)
        try:
            client.request("POST", "/v1/admin/drain")
            deadline = time.monotonic() + 900
            while True:
                health = client.request("GET", "/health")
                if not health["queue_depth"] and not health.get("running",0):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Service did not finish draining; deployment stopped.")
                time.sleep(2)
        except Exception:
            if not development:
                raise
        subprocess.run([sys.executable,"-m","modal","app","stop",current["app_id"],"--yes"],check=True)
    modal.Secret.objects.create(name + "-credentials", credentials, allow_existing=True)
    modal.Secret.from_name(name + '-credentials').update(credentials)
    worker_values = {'BLENDER_RENDER_WORKER_TOKEN':credentials['BLENDER_RENDER_WORKER_TOKEN']}
    modal.Secret.objects.create(name + '-worker', worker_values, allow_existing=True)
    modal.Secret.from_name(name + '-worker').update(worker_values)
    env = os.environ.copy()
    env["BLENDER_RENDER_PUBLIC"] = "0" if development else "1"
    env["BLENDER_RENDER_APP_NAME"] = name
    env['BLENDER_RENDER_OWNER_ANIMATION'] = animation_owner
    env['BLENDER_RENDER_ALLOWED_USERS'] = credentials.get('BLENDER_RENDER_ALLOWED_USERS', '')
    env["BLENDER_RENDER_JOB_TIMEOUT"] = "1800" if personal else "600"
    # Freeze a build snapshot. Modal uploads local files lazily; a developer
    # editing the checkout during a long image build must not create a mixed
    # deployment or invalidate an otherwise completed image build.
    from .modal_boundaries import package_snapshot
    with package_snapshot(Path(__file__).parent) as captured:
        env["PYTHONPATH"] = str(captured.parent)
        subprocess.run([sys.executable,"-m","modal","deploy","-m","blender_render.modal_app","--strategy","recreate","--tag",VERSION],env=env,cwd=captured.parent,check=True)
    actual_url = modal.Server.from_name(name,"gateway").get_url()
    if actual_url.rstrip("/") != expected_url:
        raise RuntimeError("Deployment URL differed from its configured OAuth URL. Keep admission closed and update credentials before use: " + actual_url)
    client = Client(actual_url, credentials["BLENDER_RENDER_ADMIN_TOKEN"], timeout=90)
    deadline = time.monotonic()+180
    while True:
        try:
            client.request("POST","/v1/admin/resume")
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
    profile = name if personal else ("modal-dev" if development else "modal")
    config.setdefault("profiles",{})[profile] = ({"kind":"https", "url":actual_url, "token_file":str(token_path)} if development
        else {"kind":"https", "url":actual_url, "auth":"oauth", "upload_files":True})
    config["modal_workspace"] = workspace
    save_config(config)
    return {"app":name,"url":actual_url,"mcp_url":actual_url + "/mcp","profile":profile,"lifetime_compute_budget_usd":budget_usd-operations if personal else (40 if development else 450)}
