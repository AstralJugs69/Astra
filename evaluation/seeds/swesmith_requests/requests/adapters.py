"""Simulated requests HTTP adapters module."""

from .models import ChunkedEncodingError, ProtocolError


class HTTPAdapter:
    """Simulated HTTP Adapter handling response stream and error conversion."""

    def __init__(self):
        pass

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        pass

