import io
import pytest
from requests.models import ChunkedResponseDecoder, ChunkedEncodingError, ProtocolError


class MockSocketWithTimeout:
    def __init__(self, data_chunks, timeout_on_trailer=False):
        self.chunks = data_chunks
        self.timeout_on_trailer = timeout_on_trailer
        self.pos = 0

    def readline(self):
        if self.pos < len(self.chunks):
            chunk = self.chunks[self.pos]
            self.pos += 1
            if chunk == b"0\r\n" and self.timeout_on_trailer:
                raise TimeoutError("Trailer socket timeout")
            return chunk
        return b""

    def read(self, size):
        if self.pos < len(self.chunks):
            chunk = self.chunks[self.pos]
            self.pos += 1
            return chunk[:size]
        return b""


def test_standard_chunked_reading():
    data = [b"5\r\n", b"Hello\r\n", b"0\r\n", b"\r\n"]
    sock = MockSocketWithTimeout(data)
    decoder = ChunkedResponseDecoder(sock)
    chunks = list(decoder.read_chunks())
    assert b"".join(chunks) == b"Hello"


def test_partial_trailer_timeout():
    """Test that socket timeout during trailer raises ChunkedEncodingError with partial content."""
    data = [b"5\r\n", b"Hello\r\n", b"0\r\n"]
    sock = MockSocketWithTimeout(data, timeout_on_trailer=True)
    decoder = ChunkedResponseDecoder(sock)

    with pytest.raises(ChunkedEncodingError) as excinfo:
        list(decoder.read_chunks())

    assert excinfo.value.partial_bytes == b"Hello"
