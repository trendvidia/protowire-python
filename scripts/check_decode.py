#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""This port's check-decode harness for the protowire HARDENING corpus.

Driven by ``protowire/scripts/cross_security_check.sh``. See
``protowire/docs/HARDENING.md`` and ``protowire/testdata/adversarial/README.md``.

Contract::

    check_decode.py --format <pxf|pxf-pb|pb|pb-render|pb-text|sbe|envelope> \\
                    --schema <fully.qualified.MessageType> \\
                    --proto  <path-to-adversarial.proto> \\
                    --input  <path> \\
                    [--limit <Name>=<N> ...]

    Exit 0 -> input was accepted (decode succeeded)
    Exit 1 -> input was rejected (decode returned a clean error; the first
              stderr line is "reject: <msg>")
    Exit 2 -> the harness could not run the row, or the decoder failed in a
              way that is not a clean error. The gate reads this as a crash.

pxf-pb and pb-text also write their output on stdout, for the write gate and
the text gate (``protowire/scripts/cross_write_check.sh`` and
``cross_text_check.sh``), as the Go reference's harness does.

Python exits 1 on an uncaught exception, the same code as a reject, so main()
turns anything unexpected into exit 2. A row must never pass because the
harness broke before reaching the decoder, which is also why the schema is
bound before the input is read: only an error from the decode itself is a
reject.

What each format exercises:

- pxf and sbe go through ``protowire.pxf`` and ``protowire.sbe``, that is
  through the protowire-cpp decoder this port wraps.
- pb goes through ``protowire.pb``, this port's own PB codec, as the Go
  reference's goes through its encoding/pb: the bytes decode into the
  hand-declared dataclasses below, which mirror adversarial.proto.
- pb-render writes the bytes as PXF through ``pxf.marshal_bytes``, so these
  rows test the protowire-cpp encoder. The bridge reads the bytes one level
  deeper than the encoder's limit, leaving the encoder to refuse. A read that
  fails is not a reject of the row: the encoder never ran.
- pxf-pb decodes the PXF with ``pxf.unmarshal_full``, which applies
  (pxf.default) and checks (pxf.required), and writes the message's PB bytes
  with map entries in key order, as protowire-go's deterministic writer puts
  them. The protobuf runtime does not: with ``deterministic=True``, upb wrote
  the int32 keys -1, 0, 10, 9 as -1, 10, 9, 0. So the harness writes each map
  entry itself (``_sorted_bytes``) and leaves every other field to the
  runtime. The write gate compares the bytes with protowire-go's.
- pb-text reads the bytes with the protobuf runtime and writes
  ``pxf.marshal``'s text, so the text gate compares the protowire-cpp
  encoder's output alone with protowire-go's.
- envelope is reported as a rejection, as in the Go reference. The corpus has
  no envelope rows.

Schemas come from the FileDescriptorSet next to ``--proto`` (``adversarial.binpb``
beside ``adversarial.proto``, ``text.binpb`` beside ``text.proto``), since the
Python runtime cannot compile a .proto. The spec's generators write both, so
there are no hand-written bindings to keep in step with the corpus.

``--limit MaxMessageSize=1024`` sets one HARDENING limit for the row, under
the Go name the manifest uses. pxf rows take all six, sbe rows
MaxMessageSize and MaxRepeatedCount, and pb-render MaxNestingDepth. pxf-pb
takes the six, as pxf does, and pb-text none, as in the Go reference. pb takes
the five protowire.pb has: all but MaxBytesLiteralLength, which bounds a PXF
literal. A limit this port cannot apply to the row's format exits 2: running
the row without it would not test the limit.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Optional

try:
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    from google.protobuf.message import DecodeError

    from protowire import pb, pxf, sbe
except ImportError as e:  # A broken build must not read as a reject of every row.
    print(f"check_decode: {e}", file=sys.stderr)
    sys.exit(2)


def _reject(msg: str) -> int:
    print(f"reject: {msg}", file=sys.stderr)
    return 1


def _load_fds(proto_path: str) -> bytes:
    return Path(proto_path).with_suffix(".binpb").read_bytes()


def _message_class(fds_bytes: bytes, schema: str):
    fds = descriptor_pb2.FileDescriptorSet.FromString(fds_bytes)
    pool = descriptor_pool.DescriptorPool()
    for f in fds.file:
        pool.Add(f)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(schema))


# --limit names, as the manifest spells them, and the keywords they set.
_LIMITS = {
    "MaxMessageSize": "max_message_size",
    "MaxNestingDepth": "max_nesting_depth",
    "MaxNumericLiteralDigits": "max_numeric_literal_digits",
    "MaxBytesLiteralLength": "max_bytes_literal_length",
    "MaxRepeatedCount": "max_repeated_count",
    "MaxBigFloatExponent": "max_big_float_exponent",
}
# The limits each format can apply. pb has none: see the module docstring.
_FORMAT_LIMITS = {
    "pxf": set(_LIMITS.values()),
    "pxf-pb": set(_LIMITS.values()),
    "sbe": {"max_message_size", "max_repeated_count"},
    "pb-render": {"max_nesting_depth"},
    "pb": set(_LIMITS.values()) - {"max_bytes_literal_length"},
    "pb-text": set(),
    "envelope": set(),
}


def _parse_limits(fmt: str, specs: list[str]) -> dict[str, int]:
    out = {}
    for spec in specs:
        name, _, value = spec.partition("=")
        if name not in _LIMITS:
            raise SystemExit(f"check_decode: unknown limit {name!r}")
        if _LIMITS[name] not in _FORMAT_LIMITS[fmt]:
            raise SystemExit(f"check_decode: --limit {name} is not supported for {fmt}")
        out[_LIMITS[name]] = int(value)
    return out


def _decode_pxf(fds_bytes: bytes, schema: str, data: bytes, limits: dict[str, int]) -> int:
    pxf.unmarshal_bytes(b"", fds_bytes, schema)  # Binds, or the harness fails.
    try:
        pxf.unmarshal_bytes(data, fds_bytes, schema, **limits)
    except ValueError as e:
        return _reject(f"pxf: {e}")
    return 0


_LEN = 2  # wire type of a length-delimited record


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _record(number: int, payload: bytes) -> bytes:
    return _varint(number << 3 | _LEN) + _varint(len(payload)) + payload


def _entry_field(fd, value) -> bytes:
    """A map entry's key or value, written even when it is zero, as Go
    writes both."""
    t, n = fd.type, fd.number
    if t in (fd.TYPE_STRING, fd.TYPE_BYTES):
        raw = value.encode("utf-8") if t == fd.TYPE_STRING else bytes(value)
        return _record(n, raw)
    if t in (fd.TYPE_INT32, fd.TYPE_INT64, fd.TYPE_ENUM):
        return _varint(n << 3) + _varint(value & 0xFFFFFFFFFFFFFFFF)
    if t in (fd.TYPE_UINT32, fd.TYPE_UINT64):
        return _varint(n << 3) + _varint(value)
    if t == fd.TYPE_BOOL:
        return _varint(n << 3) + _varint(int(value))
    if t in (fd.TYPE_SINT32, fd.TYPE_SINT64):
        return _varint(n << 3) + _varint((value << 1) ^ (value >> 63))
    fixed = {
        fd.TYPE_FIXED32: "<I", fd.TYPE_SFIXED32: "<i", fd.TYPE_FLOAT: "<f",
        fd.TYPE_FIXED64: "<Q", fd.TYPE_SFIXED64: "<q", fd.TYPE_DOUBLE: "<d",
    }[t]
    wire = 5 if struct.calcsize(fixed) == 4 else 1
    return _varint(n << 3 | wire) + struct.pack(fixed, value)


def _only(msg, name: str, value) -> bytes:
    """The bytes the runtime writes for one non-message field of msg's type."""
    tmp = type(msg)()
    field = getattr(tmp, name)
    if isinstance(value, list):
        field.extend(value)
    else:
        setattr(tmp, name, value)
    return tmp.SerializeToString()


def _repeated(fd) -> bool:
    # protobuf 7 removed FieldDescriptor.label; older runtimes lack is_repeated.
    if hasattr(fd, "is_repeated"):
        return fd.is_repeated
    return fd.label == fd.LABEL_REPEATED


def _sorted_bytes(msg) -> bytes:
    """msg's PB bytes, fields in number order and map entries in key order,
    nested maps included."""
    out = bytearray()
    for fd, value in sorted(msg.ListFields(), key=lambda fv: fv[0].number):
        is_message = fd.type == fd.TYPE_MESSAGE
        if is_message and fd.message_type.GetOptions().map_entry:
            key_fd = fd.message_type.fields_by_name["key"]
            val_fd = fd.message_type.fields_by_name["value"]
            for k in sorted(value):
                entry = bytearray(_entry_field(key_fd, k))
                v = value[k]
                if val_fd.type == val_fd.TYPE_MESSAGE:
                    entry += _record(2, _sorted_bytes(v))
                else:
                    entry += _entry_field(val_fd, v)
                out += _record(fd.number, bytes(entry))
        elif is_message and _repeated(fd):
            for v in value:
                out += _record(fd.number, _sorted_bytes(v))
        elif is_message:
            out += _record(fd.number, _sorted_bytes(value))
        else:
            out += _only(msg, fd.name, list(value) if _repeated(fd) else value)
    return bytes(out)


def _pxf_pb(fds_bytes: bytes, schema: str, data: bytes, limits: dict[str, int]) -> int:
    msg = _message_class(fds_bytes, schema)()
    try:
        pxf.unmarshal_full(data, msg, **limits)
    except ValueError as e:
        return _reject(f"pxf-pb: {e}")
    sys.stdout.buffer.write(_sorted_bytes(msg))
    sys.stdout.buffer.flush()
    return 0


def _pb_text(fds_bytes: bytes, schema: str, data: bytes, limits: dict[str, int]) -> int:
    msg = _message_class(fds_bytes, schema)()
    try:
        msg.ParseFromString(data)
    except DecodeError as e:
        return _reject(f"pb-text: {e}")
    try:
        text = pxf.marshal(msg)
    except ValueError as e:
        return _reject(f"pb-text: {e}")
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


# Hand-declared mirrors of adversarial.proto for protowire.pb, which reads
# dataclasses rather than descriptors. Drift from the .proto shows up as a
# corpus row failing its expectation. Unlike the Go reference's mirror, Tree
# keeps `children`, so the mirror is the whole message.


@dataclass
class Tree:
    child: Annotated[Optional[Tree], pb.Field(1)] = None
    label: Annotated[str, pb.Field(2)] = ""
    children: Annotated[list[Tree], pb.Field(3)] = field(default_factory=list)
    branches: Annotated[dict[str, Optional[Tree]], pb.Field(4)] = field(default_factory=dict)
    tags: Annotated[dict[str, str], pb.Field(5)] = field(default_factory=dict)


@dataclass
class StringHolder:
    value: Annotated[str, pb.Field(1)] = ""


@dataclass
class BytesHolder:
    value: Annotated[bytes, pb.Field(1)] = b""


@dataclass
class BigIntHolder:
    value: Annotated[int, pb.Field(1)] = 0


@dataclass
class ListHolder:
    values: Annotated[list[int], pb.Field(1, pb.INT32)] = field(default_factory=list)


@dataclass
class BigNumHolder:
    big_int: Annotated[Optional[int], pb.Field(1, pb.BIGINT)] = None
    decimal: Annotated[Optional[Decimal], pb.Field(2)] = None
    big_float: Annotated[Optional[pb.BigFloat], pb.Field(3)] = None


_PB_TYPES = {
    "adversarial.v1." + c.__name__: c
    for c in (Tree, StringHolder, BytesHolder, BigIntHolder, ListHolder, BigNumHolder)
}


def _decode_pb(fds_bytes: bytes, schema: str, data: bytes, limits: dict[str, int]) -> int:
    cls = _PB_TYPES.get(schema)
    if cls is None:
        raise SystemExit(f"check_decode: no protowire.pb mirror for {schema}")
    try:
        pb.unmarshal(data, cls, **limits)
    except ValueError as e:
        return _reject(f"pb: {e}")
    return 0


# What marshal_bytes raises when it cannot read the bytes. The stock protobuf
# reader is not used first: it stops at 100 levels, and the 101-level rows test
# that the encoder refuses them (protowire#502).
_PB_READ_FAILED = "pxf.marshal: proto parse failed"


def _pb_render(fds_bytes: bytes, schema: str, data: bytes, limits: dict[str, int]) -> int:
    _message_class(fds_bytes, schema)  # Or the harness fails.
    try:
        pxf.marshal_bytes(data, fds_bytes, schema, **limits)
    except ValueError as e:
        if str(e).startswith(_PB_READ_FAILED):
            raise  # The encoder never ran: exit 2, not a reject.
        return _reject(f"pb-render: {e}")
    return 0


def _decode_sbe(fds_bytes: bytes, schema: str, data: bytes, limits: dict[str, int]) -> int:
    cls = _message_class(fds_bytes, schema)
    codec = sbe.Codec.from_message(cls, **limits)
    try:
        codec.unmarshal(data, cls())
    except ValueError as e:
        return _reject(f"sbe: {e}")
    return 0


def _run(args: argparse.Namespace) -> int:
    if args.format == "envelope":
        return _reject("envelope decode is not implemented in this harness")
    decode = {
        "pxf": _decode_pxf,
        "pxf-pb": _pxf_pb,
        "pb-text": _pb_text,
        "pb": _decode_pb,
        "pb-render": _pb_render,
        "sbe": _decode_sbe,
    }[args.format]
    limits = _parse_limits(args.format, args.limit)
    return decode(_load_fds(args.proto), args.schema, Path(args.input).read_bytes(), limits)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="check_decode")
    p.add_argument("--format", required=True, choices=["pxf", "pxf-pb", "pb", "pb-render", "pb-text", "sbe", "envelope"])
    p.add_argument("--schema", required=True)
    p.add_argument("--proto", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--limit", action="append", default=[], metavar="NAME=N")
    args = p.parse_args(argv)
    try:
        return _run(args)
    except BaseException as e:  # noqa: BLE001 -- exit 1 would read as a clean reject.
        print(f"check_decode: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
