# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""PXF text ↔ protobuf Message — Python mirror of github.com/trendvidia/protowire/encoding/pxf.

The boundary with C++ is FileDescriptorSet bytes + binary proto bytes; Message
objects never cross. The wrapper handles conversion on each side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from google.protobuf.message import Message

from . import _protowire
from ._schema import fds_for_message


@dataclass(frozen=True)
class Result:
    """Field-level presence metadata, mirror of Go pxf.Result."""

    set_paths: frozenset[str]
    null_paths: frozenset[str]

    def is_set(self, path: str) -> bool:
        return path in self.set_paths and path not in self.null_paths

    def is_null(self, path: str) -> bool:
        return path in self.null_paths

    def is_absent(self, path: str) -> bool:
        return path not in self.set_paths and path not in self.null_paths

    def null_fields(self) -> list[str]:
        return sorted(self.null_paths)


def marshal(msg: Message) -> str:
    """Encode `msg` as PXF text. Mirrors Go pxf.Marshal."""
    fds = fds_for_message(msg)
    return _protowire.pxf_marshal(msg.SerializeToString(), fds, msg.DESCRIPTOR.full_name)


# --- bytes-only helpers (used by the CLI / advanced callers) -------------


def marshal_bytes(msg_bytes: bytes, fds: bytes, full_name: str) -> str:
    """Encode raw proto-binary bytes (against an explicit FDS) as PXF text."""
    return _protowire.pxf_marshal(bytes(msg_bytes), bytes(fds), full_name)


def unmarshal_bytes(
    data: str | bytes, fds: bytes, full_name: str, *, discard_unknown: bool = False
) -> bytes:
    """Decode PXF text into raw proto-binary bytes against an explicit FDS."""
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    return _protowire.pxf_unmarshal(text, bytes(fds), full_name, discard_unknown)


def unmarshal_full_bytes(
    data: str | bytes, fds: bytes, full_name: str, *, discard_unknown: bool = False
) -> tuple[bytes, Result]:
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    raw, set_paths, null_paths = _protowire.pxf_unmarshal_full(
        text, bytes(fds), full_name, discard_unknown
    )
    return raw, Result(frozenset(set_paths), frozenset(null_paths))


def unmarshal(data: str | bytes, msg: Message, *, discard_unknown: bool = False) -> None:
    """Decode PXF text into `msg` (in place). Mirrors Go pxf.Unmarshal."""
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    fds = fds_for_message(msg)
    raw = _protowire.pxf_unmarshal(text, fds, msg.DESCRIPTOR.full_name, discard_unknown)
    msg.Clear()
    msg.MergeFromString(raw)


def unmarshal_full(
    data: str | bytes, msg: Message, *, discard_unknown: bool = False
) -> Result:
    """Decode PXF + return per-field presence (set/null) metadata.

    Mirrors Go pxf.UnmarshalFull.
    """
    text = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    fds = fds_for_message(msg)
    raw, set_paths, null_paths = _protowire.pxf_unmarshal_full(
        text, fds, msg.DESCRIPTOR.full_name, discard_unknown
    )
    msg.Clear()
    msg.MergeFromString(raw)
    return Result(frozenset(set_paths), frozenset(null_paths))
