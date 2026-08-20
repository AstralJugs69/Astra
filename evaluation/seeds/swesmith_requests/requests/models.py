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
                    # BUG: Zero-length trailer read without error handling
                    trailer = self.socket.readline()
                    break
                data = self.socket.read(chunk_len)
                self.buffer += data
                yield data
                # Discard CRLF
                self.socket.read(2)
            except ProtocolError as exc:
                # FLAW: Re-raising raw ProtocolError without wrapping as ChunkedEncodingError
                raise exc
            except TimeoutError as exc:
                # FLAW: Socket timeout during trailer read raises raw ProtocolError
                raise ProtocolError(f"Socket timed out: {exc}")
