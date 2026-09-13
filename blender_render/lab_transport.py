"""Explicit plaintext transport for an operator-approved private lab endpoint."""
from __future__ import annotations

import http.client
import ipaddress
import socket
from urllib.request import HTTPHandler


NETWORKS = tuple(ipaddress.ip_network(value) for value in
                 ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))


def private_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Validate and connect to the same resolved IP; never re-resolve after checking."""
    host, port = address
    candidates = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    if not candidates or any(not any(ipaddress.ip_address(item[4][0]) in net for net in NETWORKS)
                             for item in candidates):
        raise OSError('Lab mode requires an IPv4 private-network endpoint')
    last_error = None
    for family, kind, protocol, _, sockaddr in candidates:
        connection = socket.socket(family, kind, protocol)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(sockaddr)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
    raise last_error


class LabConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = private_connection


class LabHTTPHandler(HTTPHandler):
    def http_open(self, request):
        return self.do_open(LabConnection, request)
