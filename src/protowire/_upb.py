# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Load the PB bytes the native decoders return into a Python message.

The native decoders (protowire-cpp) hand a decoded message back as PB bytes,
and Python loads them with `MergeFromString`. upb, the protobuf runtime's
parser, refuses PB nested past 100 levels, and that can be deeper than the
PXF the caller's limit allowed:
- A well-known type written as a literal (`t = 2024-01-01T00:00:00Z`) is no
  PXF level but one PB level. So a document at the default PXF limit of 100
  can be 101 PB levels. The spec says it decodes (protowire#537), and Go
  decodes it.
- A caller may raise `max_nesting_depth` past 100.

The native decoder has already enforced the caller's limit, so upb's own
limit is a second, stricter check on bytes we produced. When upb refuses the
bytes for depth alone, `load` parses them again with upb's limit lifted
(#40).

upb has one switch for that limit, `SetAllowOversizeProtos`. It is
process-wide and has no getter. So `load` probes the current setting first,
and turns the switch back off only if it found it off. Another thread
parsing during that window gets the lifted limit too; one calling
`SetAllowOversizeProtos(True)` during it may find it turned back off.
"""

from __future__ import annotations

import threading

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.internal import api_implementation
from google.protobuf.message import DecodeError, Message

# upb's message for a parse refused for depth.
_DEPTH_ERROR = "upb_DecodeOptions_MaxDepth"
# upb's depth limit with the switch on (UINT16_MAX, python/message.c).
_LIFTED_LIMIT = 65535

if api_implementation.Type() == "upb":
    from google._upb import _message as _upb_module

    _set_allow_oversize = _upb_module.SetAllowOversizeProtos
else:  # pragma: no cover - the pure-Python runtime has no such switch
    _set_allow_oversize = None

_lock = threading.Lock()
_probe = None  # (message class, bytes 101 levels deep), built on first use


def _varint(n: int) -> bytes:
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _probe_message():
    global _probe
    if _probe is None:
        fd = descriptor_pb2.FileDescriptorProto(
            name="protowire_upb_probe.proto", package="protowire_upb_probe", syntax="proto3"
        )
        n = fd.message_type.add(name="N")
        n.field.add(
            name="c", number=1, type=descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE,
            type_name=".protowire_upb_probe.N",
        )
        pool = descriptor_pool.DescriptorPool()
        pool.Add(fd)
        cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("protowire_upb_probe.N"))
        # 101 submessages below the root: one past what upb accepts by
        # default (it takes 100). Each level is field 1 (tag 0x0a), a varint
        # length, then the level below.
        data = b""
        for _ in range(101):
            data = b"\x0a" + _varint(len(data)) + data
        _probe = (cls, data)
    return _probe


def _allow_oversize_is_on() -> bool:
    cls, data = _probe_message()
    try:
        cls().MergeFromString(data)
    except DecodeError:
        return False
    return True


def load(msg: Message, raw: bytes) -> None:
    """Replace `msg`'s contents with the message `raw` encodes."""
    msg.Clear()
    try:
        msg.MergeFromString(raw)
        return
    except DecodeError as e:
        if _set_allow_oversize is None or _DEPTH_ERROR not in str(e):
            raise
    msg.Clear()
    with _lock:
        was_on = _allow_oversize_is_on()
        _set_allow_oversize(True)
        try:
            msg.MergeFromString(raw)
        except DecodeError as e:
            if _DEPTH_ERROR not in str(e):
                raise
            raise ValueError(
                f"{msg.DESCRIPTOR.full_name} nests past {_LIFTED_LIMIT} levels, "
                "the protobuf runtime's deepest"
            ) from e
        finally:
            if not was_on:
                _set_allow_oversize(False)
