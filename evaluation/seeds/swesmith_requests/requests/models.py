"""Simulated requests HTTP models module with chunked decoding flaw."""

class ProtocolError(Exception):
    pass

class ChunkedEncodingError(Exception):
    def __init__(self, message, partial_bytes=b""):
        super().__init__(message)
        self.partial_bytes = partial_bytes


class ChunkedResponseDecoder:
    """Decodes HTTP chunked transfer encoding stream."""

    def __init__(self, raw_socket):
        self.socket = raw_socket
        self.buffer = b""

    def read_chunks(self):
        """Generator yielding decoded chunks."""
        while True:
            try:
                line = self.socket.readline()
                if not line:
                    raise ProtocolError("Connection closed before end of stream")
                chunk_len = int(line.strip(), 16)
                if chunk_len == 0:
                    trailer = self.socket.readline()
                    break
                data = self.socket.read(chunk_len)
                self.buffer += data
                yield data
            except (ProtocolError, TimeoutError, OSError) as exc:
                raise ChunkedEncodingError(str(exc), partial_bytes=self.buffer) from exc
