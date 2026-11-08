# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""pb.Encoder / pb.Decoder against protowire-go's encoding/pb/stream_test.go (v1.17.0).

A frame is a varint length and then the message. Go's Decoder returns io.EOF
at a clean end of stream, io.ErrUnexpectedEOF on a truncated frame and
ErrFramingCorrupt on bad framing. Here they are ``None``, ``EOFError`` and
``pb.FramingError``. The last two are sticky, as in Go.

Not ported:

- TestStreamScratchReuse checks that Go's Decoder reuses its internal
  scratch buffer (``&dec.buf[0]``) between same-size frames. That is an
  allocation detail of the Go implementation with no observable behaviour;
  the Python Decoder has no such buffer.
- FuzzStreamDecode is a Go fuzz target, not a test case.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Annotated

import pytest

from protowire import pb


@dataclass
class StreamMsg:
    n: Annotated[int, pb.Field(1)] = 0
    text: Annotated[str, pb.Field(2)] = ""
    data: Annotated[bytes, pb.Field(3)] = b""


def put_uvarint(v: int) -> bytes:
    """encoding/binary.PutUvarint."""
    out = bytearray()
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def test_stream_round_trip():
    """Ports TestStreamRoundTrip."""
    msgs = [
        StreamMsg(n=1, text="small"),
        StreamMsg(n=2, text="medium " * 128),
        StreamMsg(n=3, data=b"\xab" * (1 << 20)),
    ]
    buf = io.BytesIO()
    enc = pb.Encoder(buf)
    for m in msgs:
        enc.encode(m)

    buf.seek(0)
    dec = pb.Decoder(buf)
    for i, want in enumerate(msgs):
        assert dec.decode(StreamMsg()) == want, f"frame {i} mismatch"
    assert dec.decode(StreamMsg()) is None  # Go: io.EOF


def test_stream_clean_eof():
    """Ports TestStreamCleanEOF."""
    assert pb.Decoder(io.BytesIO()).decode(StreamMsg()) is None  # Go: io.EOF


def test_stream_partial_prefix():
    """Ports TestStreamPartialPrefix."""
    dec = pb.Decoder(io.BytesIO(bytes([0x80])))
    with pytest.raises(EOFError) as e1:  # Go: io.ErrUnexpectedEOF
        dec.decode(StreamMsg())
    with pytest.raises(EOFError) as e2:
        dec.decode(StreamMsg())
    assert e2.value is e1.value, "not sticky"


def test_stream_partial_body():
    """Ports TestStreamPartialBody."""
    buf = io.BytesIO(put_uvarint(100) + bytes(10))
    dec = pb.Decoder(buf)
    with pytest.raises(EOFError) as e1:  # Go: io.ErrUnexpectedEOF
        dec.decode(StreamMsg())
    with pytest.raises(EOFError) as e2:
        dec.decode(StreamMsg())
    assert e2.value is e1.value, "not sticky"


def test_stream_oversized():
    """Ports TestStreamOversized."""
    buf = io.BytesIO(put_uvarint(1 << 30))
    dec = pb.Decoder(buf, max_frame_size=1 << 20)  # Go: dec.SetMaxFrameSize(1 << 20)
    with pytest.raises(pb.FramingError):
        dec.decode(StreamMsg())
    with pytest.raises(pb.FramingError):  # sticky
        dec.decode(StreamMsg())


def test_stream_varint_overflow():
    """Ports TestStreamVarintOverflow."""
    dec = pb.Decoder(io.BytesIO(b"\xff" * 11))
    with pytest.raises(pb.FramingError):
        dec.decode(StreamMsg())


def test_stream_inner_unmarshal_recoverable():
    """Ports TestStreamInnerUnmarshalRecoverable."""
    # A frame with a corrupt body shouldn't desync the framing — the next
    # frame is at a known boundary, so the caller can keep going.
    good = StreamMsg(n=7, text="ok")
    buf = io.BytesIO()
    enc = pb.Encoder(buf)
    enc.encode(good)
    buf.write(put_uvarint(4))
    buf.write(b"\xff\xff\xff\xff")
    enc.encode(good)

    buf.seek(0)
    dec = pb.Decoder(buf)
    assert dec.decode(StreamMsg()) == good
    with pytest.raises(ValueError) as e:
        dec.decode(StreamMsg())
    assert not isinstance(e.value, pb.FramingError), f"got framing error, expected inner unmarshal: {e.value}"
    got3 = dec.decode(StreamMsg())
    assert got3 is not None and got3.n == 7 and got3.text == "ok"


@pytest.mark.parametrize("size", [0, 1, 127, 128, 16383, 16384, 1 << 17])
def test_stream_length_boundaries(size):
    """Ports TestStreamLengthBoundaries."""
    buf = io.BytesIO()
    msg = StreamMsg(data=b"\xcc" * size)
    pb.Encoder(buf).encode(msg)
    buf.seek(0)
    got = pb.Decoder(buf).decode(StreamMsg())
    assert got is not None and got.data == msg.data
