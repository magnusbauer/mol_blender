"""The public CLI. Heavy dependencies are imported only by their subcommands."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from .config import VERSION, config_root, load_config, save_config


def parser():
    p = argparse.ArgumentParser(prog="blender-render")
    p.add_argument("--version", action="version", version=VERSION)
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("dev", help="Trusted local source previews and preset authoring (dev --help)")
    setup = commands.add_parser("setup", help="Configure the local Blender and PyMOL runtimes")
    setup.add_argument("--blender", default="")
    setup.add_argument("--pymol-python", default="")
    setup.add_argument("--install-runtime", action="store_true")
    commands.add_parser("doctor", help="Inspect configuration without runtime probes").add_argument('--profile', default='local')
    commands.add_parser("presets", help="List packaged presets")
    login = commands.add_parser("login", help="Sign in to a hosted or lab HTTPS service")
    login.add_argument("--url", default="https://almostscheidplatz--blender-render-beta-gateway.us-east.modal.direct")
    login.add_argument("--profile", default="hosted")
    render = commands.add_parser("render", help="Render a PSE, CIF, PDB, or exported payload")
    render.add_argument("input")
    render.add_argument("-o", "--output", default="render.png")
    render.add_argument("--profile", default="local")
    render.add_argument("--preset", default="preset_1_interface")
    render.add_argument("--style-json")
    render.add_argument("--overrides-json", help="Nested JSON overrides; objects merge, arrays replace")
    render.add_argument("--preserve-view", action=argparse.BooleanOptionalAction, default=None,
                        help="Preserve saved pose by default; explicit JSON camera wins")
    render.add_argument("--selection", default="(all)")
    render.add_argument("--pymol-python", default="")
    render.add_argument("--blender", default="")
    render.add_argument("--image", default="")
    render.add_argument("--keep-temp", action="store_true")
    render.add_argument("--width", type=int)
    render.add_argument("--height", type=int)
    render.add_argument("--samples", type=int)
    render.add_argument("--rough", action="store_true")
    render.add_argument("--transparent", action="store_true", default=None)
    render.add_argument("--color-follow", action="store_true")
    render.add_argument("--save-blend", action="store_true", default=None)
    render.add_argument("--no-wait", action="store_true")
    render.add_argument("--idempotency-key")
    render.add_argument('--operation-key', help='Stable key for one logical request across retries')
    render.add_argument('--rerender', action='store_true', help='Explicit additional render; requires a stable key')
    render.add_argument("--timeout", type=int, default=1800)
    for action in ("status", "result", "cancel", "quota"):
        sub = commands.add_parser(action)
        if action != "quota":
            sub.add_argument("job_id")
        sub.add_argument("--profile", default="local")
        if action == "result":
            sub.add_argument("-o", "--output", default="render.png")
    structures = commands.add_parser("structures", help="List or delete temporary uploads on a compute profile")
    structures.add_argument("action", choices=["list", "delete"])
    structures.add_argument("input_id", nargs="?")
    structures.add_argument("--profile", default="local")
    service = commands.add_parser("service")
    service.add_argument("action", choices=["start", "attach", "stop", "status"])
    service.add_argument("--profile", default="local")
    serve = commands.add_parser("serve", help="Run an authenticated loopback worker service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--root", type=Path, default=config_root() / "local")
    serve.add_argument("--token-file", type=Path, required=True)
    serve.add_argument('--controller-token-file', type=Path)
    serve.add_argument("--blender", default="")
    serve.add_argument("--image", default="")
    serve.add_argument('--pymol-python', default='')
    serve.add_argument('--worker-profile', default='', help='Dispatch admitted jobs through a configured HPC profile')
    serve.add_argument("--idle-timeout", type=int, default=900)
    serve.add_argument("--mcp", action="store_true")
    serve.add_argument("--job-timeout", type=int, default=1800)
    gateway = commands.add_parser("gateway", help="Run your lab's GitHub-authenticated HTTPS MCP gateway")
    gateway.add_argument("--profile", required=True)
    gateway.add_argument("--host", default="127.0.0.1")
    gateway.add_argument("--port", type=int, default=8766)
    gateway.add_argument("--root", type=Path, default=config_root() / "gateway")
    gateway.add_argument("--public-url", required=True)
    gateway.add_argument("--credentials", type=Path, required=True)
    gateway.add_argument("--allowed-user", action="append", required=True)
    gateway.add_argument("--converter-image", required=True)
    gateway.add_argument("--job-timeout", type=int, default=1800)
    mcp = commands.add_parser("mcp", help="Serve Codex MCP tools over stdio")
    mcp.add_argument("--profile", default="local")
    plugin = commands.add_parser("plugin", help="Build one installable hosted or private plugin route")
    plugin.add_argument("action", choices=["package", "marketplace"])
    plugin.add_argument("output", type=Path, help="New plugin directory or marketplace root")
    plugin.add_argument("--name", help="Plugin ID for a generated marketplace")
    plugin.add_argument("--marketplace-name", help="Marketplace ID; defaults to the plugin ID")
    plugin.add_argument("--route", choices=["hosted", "private-stdio", "private-http"], required=True)
    plugin.add_argument("--profile", help="Explicit local, Apptainer, HPC, or private profile; required for STDIO")
    plugin.add_argument("--executable", help="Literal absolute blender-render executable required for STDIO")
    plugin.add_argument("--url", help="Explicit private MCP URL ending in /mcp")
    plugin.add_argument("--app-id", help="Optional registered ChatGPT connection for private HTTP")
    plugin.add_argument("--bearer-token-env-var",
                        help="Environment variable holding a private HTTP bearer token")
    plugin.add_argument("--deployment-identity",
                        help="Non-secret identity label; required for personal Modal URLs")
    plugin.add_argument("--force", action="store_true",
                        help="Refresh an unchanged route/name/app topology and preserve other files")
    profile = commands.add_parser("profile")
    profile.add_argument("action", choices=["list", "add"])
    profile.add_argument("name", nargs="?")
    profile.add_argument("--file", type=Path)
    deploy = commands.add_parser("deploy")
    deploy.add_argument("--backend", choices=["modal"], default="modal")
    deploy.add_argument("--development", action="store_true")
    deploy.add_argument("--staging-user", help="Restrict OAuth validation to one GitHub numeric user ID")
    deploy.add_argument("--personal", metavar="APP_NAME", help="Deploy a separate service into your own Modal workspace")
    deploy.add_argument("--budget-usd", type=float, help="Personal deployment lifetime compute and operations ceiling")
    deploy.add_argument("--credits-expire", help="Personal credit expiry, YYYY-MM-DD in UTC")
    return p


def serve(args):
    import asyncio
    import uvicorn
    from .api import create_app
    from .jobs import JobStore, Policy
    from .runtime import file_lock, LocalFileConverter
    from .service import HostBackend, RenderService
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Bind the private service to loopback. Use SSH forwarding or a TLS/OAuth gateway for remote clients.")
    token = args.token_file.expanduser().read_text().strip()
    with file_lock(args.root / "service.lock", blocking=False):
        store = JobStore(args.root, Policy(max_job_seconds=args.job_timeout))
        if args.worker_profile:
            from .gateway import GatewayBackend
            backend = GatewayBackend(store, args.worker_profile)
        else:
            backend = HostBackend(args.root / "worker", blender=args.blender, image=args.image, timeout=args.job_timeout)
        service = RenderService(store, backend, converter=LocalFileConverter(args.pymol_python))
        mcp = None
        if args.mcp:
            from .mcp_server import create_private_http
            mcp = create_private_http(service, token)
        controller_tokens = {args.controller_token_file.read_text().strip(): 'controller'} if args.controller_token_file else None
        app = create_app(service, token=token, mcp_server=mcp, controller_tokens=controller_tokens)
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning"))
        app.state.shutdown = lambda: setattr(server, "should_exit", True)

        def idle_monitor():
            while not server.should_exit:
                time.sleep(2)
                if args.idle_timeout and time.time() - service.last_activity > args.idle_timeout:
                    with store.lock:
                        active = store.db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone()
                    if not active:
                        server.should_exit = True
        threading.Thread(target=idle_monitor, daemon=True).start()
        try:
            server.run()
        finally:
            service.stop()
            store.close()


def execute(args):
    from . import core
    if args.command == "serve":
        return serve(args)
    if args.command == "gateway":
        from .gateway import serve_gateway
        return serve_gateway(args)
    if args.command == "setup":
        from .runtime import diagnose
        config = load_config()
        config["service_python"] = sys.executable
        if args.blender:
            config["blender"] = str(Path(args.blender).expanduser().resolve())
        if args.pymol_python:
            config["pymol_python"] = str(Path(args.pymol_python).expanduser().resolve())
        if args.install_runtime:
            from .runtime_install import install
            config.update(install(config.get("blender", "")))
        save_config(config)
        # Setup is an explicit runtime operation. Ordinary doctor/status calls
        # keep diagnose's read-only default and never launch Blender.
        check = diagnose(probe=True)
        if not check["matches_lock"]:
            raise RuntimeError("Runtime does not match the release lock: " + json.dumps(check["mismatches"]))
        return {"status": "configured", **check, "pymol_command": "run " + str(Path(__file__).with_name("pymol_bridge_plugin.py"))}
    if args.command == "doctor":
        from .runtime import diagnose
        if args.profile not in {'local', 'apptainer'}:
            from .hpc import validate_profile, cached_observation
            from .engine import engine_identity
            profile = load_config().get('profiles', {}).get(args.profile)
            if not profile:
                raise ValueError('Unknown profile')
            if profile.get('kind') == 'hpc':
                validate_profile(profile)
            return {'profile': args.profile, 'kind': profile.get('kind'), 'configuration_valid': True,
                    'controller_declaration': engine_identity(), 'runtime_verified': False,
                    'cached_observation': cached_observation(args.profile) if profile.get('kind') == 'hpc' else None}
        return diagnose()
    if args.command == "login":
        from .oauth_client import access_token
        url = args.url.rstrip("/").removesuffix("/mcp")
        access_token(url, interactive=True)
        config = load_config()
        config.setdefault("profiles", {})[args.profile] = {"kind":"https", "url":url, "auth":"oauth", "upload_files":True}
        save_config(config)
        return {"status":"signed_in", "profile":args.profile, "url":url}
    if args.command == "presets":
        from .presets import catalog
        return {"presets": list(core.PRESET_STYLE_FILENAMES), "catalog": catalog()}
    if args.command == "profile":
        config = load_config()
        if args.action == "add":
            if not args.name or not args.file:
                raise ValueError("profile add requires NAME --file profile.json")
            doc = json.loads(args.file.read_text())
            if doc.get("kind") == "hpc":
                from .hpc import validate_profile, cached_observation
                validate_profile(doc)
            elif not doc.get("url"):
                raise ValueError("An endpoint profile requires url and token_file or token_env")
            config.setdefault("profiles", {})[args.name] = doc
            save_config(config)
        return {"profiles": list(config.get("profiles", {}))}
    if args.command == "mcp":
        from .mcp_server import create_stdio
        create_stdio(args.profile).run(transport="stdio", show_banner=False)
        return
    if args.command == "plugin":
        from blender_render_plugin import package_marketplace, package_plugin
        selected_profile = args.profile or "local"
        needs_config = (args.route == "private-stdio"
                        and selected_profile not in {"local", "apptainer"})
        options = {"profile": args.profile, "executable": args.executable,
                   "url": args.url, "app_id": args.app_id,
                   "bearer_token_env_var": args.bearer_token_env_var,
                   "deployment_identity": args.deployment_identity,
                   "config": load_config() if needs_config else {},
                   "force": args.force}
        if args.action == "marketplace":
            if not args.name:
                raise ValueError("plugin marketplace requires --name")
            return package_marketplace(args.output, args.route, plugin_name=args.name,
                                       marketplace_name=args.marketplace_name, **options)
        if args.name or args.marketplace_name:
            raise ValueError("--name and --marketplace-name apply only to plugin marketplace")
        return package_plugin(args.output, args.route, **options)
    if args.command == "deploy":
        from .deployment import deploy
        return deploy(development=args.development, staging_user=args.staging_user, personal=args.personal,
                      budget_usd=args.budget_usd, expires=args.credits_expire)
    from .client import configured_client
    if args.command == "service":
        if args.action == "status":
            try:
                client = configured_client(args.profile, read_only=True)
                return client.request('GET','/health')
            except (RuntimeError, OSError):
                return {'status':'stopped'}
        if args.action == "stop" and args.profile == "local":
            from .client import Client, ClientError
            try:
                state = json.loads((config_root() / "local" / "session.json").read_text())
                client = Client(state["url"], Path(state["token_file"]).read_text().strip())
                return client.request("POST", "/v1/admin/stop")
            except (FileNotFoundError, ClientError):
                return {"status":"stopped"}
        if args.action == "stop":
            from .hpc import stop
            return stop(args.profile, load_config()["profiles"][args.profile])
        from .client import start_service
        if args.action == 'attach':
            return start_service(args.profile, attach_only=True)
        return start_service(args.profile)
    client = configured_client(args.profile, admission=True, blender=getattr(args,'blender',''),
                               image=getattr(args,'image',''), pymol_python=getattr(args,'pymol_python',''))
    if args.command == "render":
        path = Path(args.input).expanduser()
        options = {k: getattr(args, k) for k in ("preset", "width", "height", "samples", "rough", "color_follow", "save_blend", "keep_temp", "preserve_view") if getattr(args, k) is not None}
        if args.style_json:
            options["style_doc"] = json.loads(Path(args.style_json).read_text())
            options.pop("preset", None)
        if args.overrides_json:
            options["overrides"] = json.loads(Path(args.overrides_json).read_text())
        if args.transparent is not None:
            options["transparent_background"] = args.transparent
        if args.rerender and not (args.operation_key or args.idempotency_key):
            raise ValueError('--rerender requires --operation-key or --idempotency-key; retain that key for retries')
        identity = {'operation_key': args.operation_key, 'rerender': args.rerender}
        if path.suffix.lower() == '.json':
            # An already exported StructureBundle needs no conversion. It joins
            # the same durable job queue; never also submit it as a raw file.
            source = client.upload(json.loads(path.read_text()), path.name)
            job = client.submit(source['input_id'], options, args.idempotency_key, **identity)
        else:
            job = client.render_file(path, options, args.idempotency_key,
                                     selection=args.selection, **identity)
        print("Job " + job["job_id"], file=sys.stderr, flush=True)
        if args.no_wait:
            return job
        deadline = time.monotonic() + args.timeout
        client.wait(
            job["job_id"],
            timeout=args.timeout,
            progress=lambda s: print(
                f"{s['status']} (queue position {s['queue_position']})",
                file=sys.stderr,
            ),
            deadline=deadline,
            fetch_result=False,
        )
        return client.download(job["job_id"], args.output, deadline=deadline)
    if args.command == "quota":
        return client.request("GET", "/v1/quota")
    if args.command == "structures":
        if args.action == 'list':
            return client.structures()
        if not args.input_id:
            raise ValueError("Provide the input_id from blender-render structures list.")
        return client.delete_structure(args.input_id)
    if args.command == "status":
        return client.status(args.job_id)
    if args.command == "cancel":
        return client.cancel(args.job_id)
    if args.command == "result":
        return client.download(args.job_id, args.output)


def legacy_runner(argv=None):
    """Compatibility for `bridge_runner local --payload ... --output-name ...`."""
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["local", "apptainer", "interactive", "remote", "slurm"])
    p.add_argument("--payload", "--payload-json", required=True)
    p.add_argument("--preset", default="preset_1_interface")
    p.add_argument("--output-dir", default=".")
    p.add_argument("--output-name", default="render.png")
    p.add_argument("--style-json")
    p.add_argument("--rough", action="store_true")
    p.add_argument("--keep-temp", action="store_true")
    p.add_argument("--save-blend", action="store_true")
    p.add_argument("--color-follow", action="store_true")
    p.add_argument("--blender-exe", default="")
    p.add_argument("--apptainer-image", default="")
    p.add_argument("--use-apptainer", action="store_true")
    args = p.parse_args(argv)
    profile = load_config().get('default_hpc_profile','ipd') if args.mode in {'interactive','slurm'} else args.mode
    command = ["render", args.payload, "--preset", args.preset, "--profile", profile, "-o", str(Path(args.output_dir) / args.output_name)]
    for flag in ('keep_temp','save_blend','color_follow'):
        if getattr(args,flag): command.append('--'+flag.replace('_','-'))
    if args.blender_exe: command += ['--blender',args.blender_exe]
    if args.use_apptainer or args.mode == 'apptainer': command += ['--image',args.apptainer_image]
    if args.style_json:
        command += ["--style-json", args.style_json]
    if args.rough:
        command += ["--rough"]
    return main(command)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "dev":
        from .developer import main as developer_main
        return developer_main(argv[1:])
    try:
        result = execute(parser().parse_args(argv))
        if result is not None:
            print(json.dumps(result, indent=2))
        return 1 if isinstance(result, dict) and result.get('complete') is False else 0
    except (RuntimeError, ValueError, OSError) as exc:
        print("blender-render: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
