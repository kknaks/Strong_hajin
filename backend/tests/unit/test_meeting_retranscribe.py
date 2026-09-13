from ax_workspace.modules.meetings.retranscribe import Recording, uploadable
from ax_workspace.platform.soniox import _read_all


def test_raw_pcm_gets_a_wav_header_while_a_container_stays_unchanged() -> None:
    payload, name = uploadable(Recording(data=b"\x00\x01" * 8, filename="m.pcm"))
    assert name == "m.wav"
    assert payload.startswith(b"RIFF") and b"WAVEfmt " in payload
    assert payload.endswith(b"\x00\x01" * 8) and len(payload) == 44 + 16

    container = Recording(data=b"webm-bytes", filename="m.webm")
    assert uploadable(container) == (b"webm-bytes", "m.webm")


def test_provider_response_reader_consumes_every_chunk() -> None:
    class Chunked:
        def __init__(self) -> None:
            self.pieces = [b"abc", b"def", b"gh"]
            self.headers: dict[str, str] = {}

        def read(self, _size: int = -1) -> bytes:
            return self.pieces.pop(0) if self.pieces else b""

    assert _read_all(Chunked()) == b"abcdefgh"
