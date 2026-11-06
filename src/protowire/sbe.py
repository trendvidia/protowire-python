# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""SBE codec — Python mirror of github.com/trendvidia/protowire-go/encoding/sbe."""

from __future__ import annotations

from typing import Iterable

from google.protobuf.descriptor import FileDescriptor
from google.protobuf.message import Message

from . import _protowire
from ._schema import fds_for_files


class View:
    """Zero-copy view over an SBE-encoded buffer.

    Wraps the native ``_protowire.View``. The underlying data is owned by
    the native object (a shared heap copy of the input bytes) and stays alive
    as long as any view, sub-view, or group entry referencing it is alive.
    """

    __slots__ = ("_native",)

    def __init__(self, native: "_protowire.View") -> None:
        self._native = native

    def int(self, name: str) -> int:
        return self._native.int(name)

    def uint(self, name: str) -> int:
        return self._native.uint(name)

    def float(self, name: str) -> float:
        return self._native.float(name)

    def bool(self, name: str) -> bool:
        return self._native.bool(name)

    def string(self, name: str) -> str:
        return self._native.string(name)

    def bytes(self, name: str) -> bytes:
        """Read a fixed-length bytes field as the full N-byte slice (no trim)."""
        return self._native.bytes(name)

    def composite(self, name: str) -> "View":
        """Sub-view over a non-repeated nested message (SBE composite)."""
        return View(self._native.composite(name))

    def group(self, name: str) -> "GroupView":
        """View over a repeating group field."""
        return GroupView(self._native.group(name))


class GroupView:
    """View over an SBE repeating group. Iterate via :py:meth:`entry`."""

    __slots__ = ("_native",)

    def __init__(self, native: "_protowire.GroupView") -> None:
        self._native = native

    def len(self) -> int:
        return self._native.len()

    def __len__(self) -> int:
        return self._native.len()

    def entry(self, i: int) -> View:
        return View(self._native.entry(i))


class Codec:
    """SBE codec built from one or more proto FileDescriptors with SBE annotations.

    The codec's decoders (`unmarshal` and `view`) enforce two limits of
    HARDENING.md § Mandatory limits, set here for every call, as
    protowire-cpp's sbe::CodecOptions sets them. `max_message_size` caps the
    input to one call (default 64 MiB). `max_repeated_count` caps a group's
    entry count, checked before any entry is read (default 64 Mi). 0 selects
    the default; a negative value is a ValueError.
    """

    def __init__(
        self,
        files: Iterable[FileDescriptor],
        *,
        max_message_size: int = 0,
        max_repeated_count: int = 0,
    ) -> None:
        files = list(files)
        if not files:
            raise ValueError("sbe.Codec requires at least one FileDescriptor")
        fds = fds_for_files(files)
        # Pass the *selected* file names so the C++ Codec only registers those
        # — transitive dep files (descriptor.proto, sbe/annotations.proto)
        # don't carry an (sbe.schema_id) option and would otherwise fail.
        self._impl = _protowire.SbeCodec.create(
            fds, [f.name for f in files], max_message_size, max_repeated_count
        )

    @classmethod
    def from_message(
        cls,
        msg_type: type[Message],
        *,
        max_message_size: int = 0,
        max_repeated_count: int = 0,
    ) -> "Codec":
        return cls(
            [msg_type.DESCRIPTOR.file],
            max_message_size=max_message_size,
            max_repeated_count=max_repeated_count,
        )

    def marshal(self, msg: Message) -> bytes:
        return self._impl.marshal(msg.SerializeToString(), msg.DESCRIPTOR.full_name)

    def unmarshal(self, data: bytes, msg: Message) -> None:
        raw = self._impl.unmarshal(bytes(data), msg.DESCRIPTOR.full_name)
        msg.Clear()
        msg.MergeFromString(raw)

    def view(self, data: bytes) -> View:
        return View(self._impl.new_view(bytes(data)))
