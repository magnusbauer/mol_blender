"""Explicit cluster administration and client commands."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from .jsonio import read_json


def parser():
    root = argparse.ArgumentParser(prog='blender-render cluster', description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='Start the optional cluster gateway')
    serve.add_argument('--config', required=True)
    serve.add_argument('--bind', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--tls-cert')
    serve.add_argument('--tls-key')
    serve.add_argument('--behind-proxy', action='store_true')
    add = commands.add_parser('user-add', help='Write one private client credential; never print its token')
    add.add_argument('--config', required=True)
    add.add_argument('--user', required=True)
    add.add_argument('--url', required=True)
    add.add_argument('--credential-output', required=True)
    revoke = commands.add_parser('user-revoke')
    revoke.add_argument('--config', required=True)
    revoke.add_argument('--user', required=True)
    doctor = commands.add_parser('doctor', help='Check the SIF, container runtime and selected scheduler without installing, queuing or rendering')
    doctor.add_argument('--config', required=True)
    warm = commands.add_parser('warm', help='Start, inspect or stop a persistent warm gateway worker')
    warm.add_argument('action', choices=('start', 'status', 'stop'))
    warm.add_argument('--config', required=True)
    warm.add_argument('--force', action='store_true', help='Stop even during a render (retires the container)')
    reconcile = commands.add_parser('reconcile', help='Recover an ambiguous submission without launching another job')
    reconcile.add_argument('--config', required=True)
    reconcile.add_argument('job')
    connect = commands.add_parser('connect', help="Start or reuse your own gateway on one host over SSH, open a tunnel and import a private credential")
    connect.add_argument('--profile', default='lab')
    connect.add_argument('--ssh-host', help='SSH alias that already logs in without prompts, e.g. mycluster')
    connect.add_argument('--node', help='Host name or IP to pin the gateway and tunnel to, e.g. login-03.cluster.example')
    connect.add_argument('--remote-executable', help="Administrator-installed controller's blender-render on that host")
    connect.add_argument('--server-config', help='Validated site server.json on that host (spool and tokens are per connection)')
    connect.add_argument('--connection-dir', help='Private per-connection directory on that host (default: next to the config)')
    connect.add_argument('--remote-user', help='Gateway API identity for this computer (default: client-HOSTNAME)')
    connect.add_argument('--local-port', type=int)
    connect.add_argument('--reissue', action='store_true', help='Replace this computer\'s credential; earlier jobs stay visible')
    disconnect = commands.add_parser('disconnect', help='Close the tunnel and stop the gateway that connect started')
    disconnect.add_argument('--profile', default='lab')
    disconnect.add_argument('--keep-gateway', action='store_true')
    disconnect.add_argument('--revoke', action='store_true', help='Also revoke the credential and forget this connection')
    connection = commands.add_parser('connection', help='Show the saved SSH connection and whether its tunnel is open')
    connection.add_argument('--profile', default='lab')
    configure = commands.add_parser('configure', help='Configure credentials or an open cluster URL')
    configure.add_argument('--profile', default='default')
    access = configure.add_mutually_exclusive_group(required=True)
    access.add_argument('--credential-file')
    access.add_argument('--url', help='Open cluster service URL; no authentication')
    configure.add_argument('--ca-file')
    for name in ('info', 'list', 'status', 'cancel', 'delete', 'fetch', 'wait'):
        item = commands.add_parser(name)
        item.add_argument('--profile')
        if name not in ('info', 'list'):
            item.add_argument('job', help='Job ID or local .cluster.json receipt')
        if name == 'fetch':
            item.add_argument('-o', '--output', help='PNG destination; defaults to the receipt destination')
        if name == 'wait':
            item.add_argument('--timeout', type=float, default=600)
    submit = commands.add_parser('submit', help='Export locally and submit one render; returns immediately by default')
    submit.add_argument('input')
    submit.add_argument('--profile', default='default')
    submit.add_argument('-o', '--output', default='render.png')
    submit.add_argument('--selection', default='(all)')
    submit.add_argument('--pymol-python', default='')
    from ..job_cli import add_render_options
    add_render_options(submit)
    submit.add_argument('--wait', action='store_true')
    submit.add_argument('--timeout', type=float, default=600, help='Client wait deadline; does not cancel cluster work')
    return root


def execute(args):
    from .client import Client, configure
    if args.command in ('connect', 'disconnect', 'connection'):
        from . import connect
        if args.command == 'connection':
            return connect.status(args.profile)
        if args.command == 'disconnect':
            return connect.disconnect(args.profile, keep_gateway=args.keep_gateway, revoke=args.revoke)
        return connect.connect(args.profile, reissue=args.reissue, **{key: getattr(args, key) for key in connect.SETTINGS})
    if args.command == 'configure':
        return configure(args.profile, args.credential_file, args.ca_file, url=args.url)
    if args.command in ('serve', 'user-add', 'user-revoke', 'doctor', 'reconcile', 'warm'):
        from .config import ServerConfig
        config = ServerConfig.load(args.config)
        if args.command == 'warm':
            if config.scheduler != 'warm':
                raise ValueError('cluster warm requires scheduler: warm in the server configuration')
            if args.force and args.action != 'stop':
                raise ValueError('--force applies only to warm stop')
            from .warm import Warm
            scheduler = Warm(config)
            return scheduler.stop(force=args.force) if args.action == 'stop' else getattr(scheduler, args.action)()
        if args.command in ('user-add', 'user-revoke') and config.authentication != 'token':
            raise ValueError('User credentials are disabled in open cluster mode')
        if args.command in ('doctor', 'reconcile'):
            from .slurm import scheduler_for
            from ..engine import generate_manifest
            identity = generate_manifest(Path(__file__).resolve().parents[1])['engine_build']
            scheduler = scheduler_for(config)
            if args.command == 'reconcile':
                from .store import Store
                return Store(config, scheduler, identity).reconcile(args.job)
            from . import doctor
            return doctor.run(config, scheduler, identity)
        if args.command == 'user-add':
            from .auth import add_user
            return add_user(config.tokens_file, args.user, args.url, args.credential_output)
        if args.command == 'user-revoke':
            from .auth import revoke_user
            return revoke_user(config.tokens_file, args.user)
        if bool(args.tls_cert) != bool(args.tls_key):
            raise ValueError('Supply both TLS certificate and key')
        if args.bind not in ('127.0.0.1', '::1', 'localhost') and not args.tls_cert and not args.behind_proxy:
            raise ValueError('Non-loopback serving needs TLS or an explicit --behind-proxy deployment')
        try:
            import uvicorn
            from .server import create_app
        except ImportError as exc:
            raise ValueError('Install the optional cluster extra: uv sync --locked --extra cluster') from exc
        uvicorn.run(create_app(config), host=args.bind, port=args.port, access_log=False,
                    proxy_headers=False, limit_concurrency=32, timeout_keep_alive=5,
                    ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key)
        return None
    if args.command == 'submit':
        from .. import jobs
        from ..job_cli import LAB_PAYLOAD_LIMIT, render_options
        from ..local import load_payload
        payload = load_payload(args.input, pymol_python=args.pymol_python, selection=args.selection,
                               limit=LAB_PAYLOAD_LIMIT)
        result = jobs.submit(payload, render_options(args), args.output, backend_name=jobs.LAB, profile=args.profile)
        if args.wait:
            result = jobs.wait(result['receipt'], timeout=args.timeout)
            if result['status'] == 'completed':
                result = jobs.fetch(result['receipt'])
        return result
    receipt = None
    if hasattr(args, 'job') and not re.fullmatch('[0-9a-f]{32}', args.job):
        receipt = read_json(args.job, 64 * 1024)
    client = Client(args.profile or (receipt['profile'] if receipt else 'default'))
    if args.command == 'info':
        return client.api('GET', '/v1/info')
    if args.command == 'list':
        return client.api('GET', '/v1/jobs')
    result = client.recover(args.job) if receipt else client.status(args.job)
    if args.command == 'status':
        return result
    identifier = result['job_id']
    if args.command in ('cancel', 'delete'):
        return client.api('POST' if args.command == 'cancel' else 'DELETE',
                          '/v1/jobs/' + identifier + ('/cancel' if args.command == 'cancel' else ''))
    if args.command == 'wait':
        from .. import jobs
        if receipt:
            return jobs.wait(args.job, timeout=args.timeout)
        return jobs.poll(lambda: client.status(identifier), timeout=args.timeout)
    if args.command == 'fetch':
        output = args.output or (receipt['output'] if receipt else None)
        if not output:
            raise ValueError('Specify --output when fetching by job ID')
        return client.fetch(identifier, output)


def main(argv):
    result = execute(parser().parse_args(argv))
    if result is not None:
        print(json.dumps(result, indent=2))
    return 0
