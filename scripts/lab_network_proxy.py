"""Explicit lab-network access to an authenticated loopback renderer.

The operator explicitly allowlists lab CIDRs. Jobs are scoped to the source IP,
so users of the same workstation share an identity. Outside users can SSH-forward
to the proxy's loopback listener. No credential is sent to clients or discovered
over an unauthenticated endpoint.
This is a transport adapter; the normal renderer owns all jobs and artifacts.
"""
from __future__ import annotations

import argparse
import fcntl
import http.client
from http.server import BaseHTTPRequestHandler
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socketserver
import stat
import subprocess
import threading
from urllib.parse import urlsplit


PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))


def lab_network(value):
    network = ipaddress.ip_network(value)
    if not any(network.version == parent.version and network.subnet_of(parent) for parent in PRIVATE_NETWORKS):
        raise ValueError('Allow only explicit IPv4 lab or loopback networks')
    return network


def peer_owner(address, allowed_networks):
    """Use the TCP peer address, never X-Forwarded-For or other client headers."""
    address = ipaddress.ip_address(address)
    if not any(address in network for network in allowed_networks):
        raise PermissionError('Lab network required')
    return 'network:' + str(address)


def allowed_route(method, target):
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.fragment or '%' in parsed.path or '\\' in parsed.path:
        return False
    path = parsed.path
    if method == 'GET':
        return (path in {'/health', '/healthz', '/v1/capabilities', '/v1/presets', '/v1/quota',
                         '/v1/structures', '/v1/jobs'}
                or bool(re.fullmatch(r'/v1/jobs/[a-f0-9]+(?:/result|/artifacts/[A-Za-z0-9_.-]+)?', path))
                or bool(re.fullmatch(r'/(?:download|preview)/[A-Za-z0-9_.-]+', path)))
    if method == 'POST':
        return (path in {'/v1/structures', '/v1/jobs'}
                or bool(re.fullmatch(r'/v1/jobs/[a-f0-9]+/(?:cancel|recover-transfer|artifacts/[A-Za-z0-9_.-]+/ack)', path)))
    if method == 'DELETE':
        return bool(re.fullmatch(r'/v1/(?:structures/[a-f0-9]+|jobs/[a-f0-9]+/result)', path))
    # In particular, no admin, resolved-execution, or unsandboxed file-conversion routes.
    return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass  # URLs can contain signed download credentials.

    def setup(self):
        self.request.settimeout(20)
        super().setup()

    def handle_expect_100(self):
        self.fail(417, 'Send a bounded Content-Length body without Expect')
        return False

    def fail(self, code, message):
        data = json.dumps({'error': message, 'code': 'lab_proxy_error'}).encode()
        self.close_connection = True
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def relay(self):
        self.close_connection = True
        try:
            owner = peer_owner(self.client_address[0], self.server.allowed_networks)
        except (ValueError, PermissionError):
            self.fail(403, 'Access is restricted to the configured lab networks')
            return
        if not allowed_route(self.command, self.path):
            self.fail(403, 'Lab access supports PyMOL scene exports and job results only')
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) > 1:
            self.fail(400, 'A single Content-Length is required')
            return
        try:
            raw_length = lengths[0] if lengths else '0'
            if not re.fullmatch(r'[0-9]+', raw_length):
                raise ValueError()
            length = int(raw_length)
            if length > self.server.max_body:
                self.fail(413, 'Request exceeds the upload size limit')
                return
            if length and self.command != 'POST':
                raise ValueError()
            body = self.rfile.read(length) if length else None
            if body is not None and len(body) != length:
                raise ValueError()
        except (ValueError, OSError):
            self.fail(400, 'Invalid or incomplete request body')
            return
        # Rebuild headers from an allowlist. Clients cannot impersonate an owner,
        # supply the controller credential, or choose another upstream host.
        headers = {'Authorization': 'Bearer ' + self.server.token,
                   'X-Render-Owner': owner, 'Connection': 'close'}
        for key in ('Content-Type', 'Accept', 'Range'):
            if key in self.headers:
                headers[key] = self.headers[key]
        upstream = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=120)
        sent_headers = False
        try:
            try:
                upstream.connect()
            except ConnectionRefusedError:
                # Only a new upload/submission can start a stopped controller.
                # No HTTP bytes have been sent; a request is never replayed.
                if (self.command, urlsplit(self.path).path) not in {
                        ('POST', '/v1/structures'), ('POST', '/v1/jobs')} or not self.server.start_command:
                    raise
                self.server.ensure_upstream()
                upstream.connect()
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            self.send_response(response.status)
            for key in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Content-Disposition'):
                value = response.getheader(key)
                if value is not None:
                    self.send_header(key, value)
            self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            sent_headers = True
            while chunk := response.read(65536):
                self.wfile.write(chunk)
        except (OSError, http.client.HTTPException, subprocess.SubprocessError, RuntimeError):
            if not sent_headers:
                self.fail(503, 'Renderer connection interrupted; inspect an existing job before submitting again')
        finally:
            upstream.close()

    do_GET = do_POST = do_DELETE = relay


class Proxy(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16
    allow_reuse_address = True

    def __init__(self, address, *, token, allowed_networks, upstream_port, start_command=(), max_body=50 * 1024**2 + 65536):
        self.token, self.allowed_networks, self.upstream_port = token, allowed_networks, upstream_port
        self.start_command, self.max_body = list(start_command), max_body
        self.start_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # Do not log request bodies or signed URLs on disconnect.

    def ensure_upstream(self):
        with self.start_lock:
            result = subprocess.run(self.start_command, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            if result.returncode:
                raise RuntimeError('Renderer startup failed')


def private_token(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077):
            raise ValueError('Controller credential must be an operator-owned private file')
        value = stream.read(4097).strip()
    if not 32 <= len(value) <= 4096 or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid controller credential')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--allow-network', action='append', required=True, type=lab_network)
    parser.add_argument('--upstream-port', required=True, type=int)
    parser.add_argument('--controller-token-file', required=True, type=Path)
    parser.add_argument('--start-command-file', type=Path, help='Operator-owned JSON argv for restarting the renderer')
    args = parser.parse_args(argv)
    if not all(1 <= value <= 65535 for value in (args.port, args.upstream_port)):
        parser.error('Invalid port')
    if args.host != '0.0.0.0':
        lab_network(args.host + '/32')
    token = private_token(args.controller_token_file)
    start = []
    if args.start_command_file:
        info = args.start_command_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError('Startup argv must be an operator-owned file')
        start = json.loads(args.start_command_file.read_text())
        if not isinstance(start, list) or not start or any(not isinstance(x, str) or '\0' in x for x in start):
            raise ValueError('Startup command must be a JSON argv array')
    with (args.controller_token_file.parent / 'proxy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proxy = Proxy((args.host, args.port), token=token, allowed_networks=args.allow_network,
                      upstream_port=args.upstream_port, start_command=start)
        def stop(*_):
            threading.Thread(target=proxy.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            proxy.serve_forever()
        finally:
            proxy.server_close()


if __name__ == '__main__':
    main()
