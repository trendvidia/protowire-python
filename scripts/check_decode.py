#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""This port's check-decode harness for the protowire HARDENING corpus.

Driven by ``protowire/scripts/cross_security_check.sh``. See
``protowire/docs/HARDENING.md`` and ``protowire/testdata/adversarial/README.md``.

Contract::

    check_decode.py --format <pxf|pb|pb-render|sbe|envelope> \\
                    --schema <fully.qualified.MessageType> \\
                    --proto  <path-to-adversarial.proto> \\
                    --input  <path>

    Exit 0 -> input was accepted (decode succeeded)
    Exit 1 -> input was rejected (decode returned a clean error; the first
              stderr line is "reject: <msg>")
    Exit 2 -> the harness could not run the row, or the decoder failed in a
              way that is not a clean error. The gate reads this as a crash.

Python exits 1 on an uncaught exception, the same code as a reject, so main()
turns anything unexpected into exit 2. A row must never pass because the
harness broke before reaching the decoder, which is also why the schema is
bound before the input is read: only an error from the decode itself is a
reject.

What each format exercises:

- pxf and sbe go through ``protowire.pxf`` and ``protowire.sbe``, that is
  through the protowire-cpp decoder this port wraps.
- pb goes through the upstream protobuf runtime. This port has no PB decoder
  of its own, so these rows test what a caller gets from
  ``Message.ParseFromString``, and a fix for one of them cannot land here.
- pb-render reads the bytes with the protobuf runtime, then writes PXF through
  the protowire-cpp encoder, so these rows test the producer. A read that fails
  is not a reject of the row: the encoder never ran.
- envelope is reported as a rejection, as in the Go reference. The corpus has
  no envelope rows.

Schemas come from the ``adversarial.binpb`` FileDescriptorSet next to
``--proto``, since the Python runtime cannot compile a .proto. The corpus
generator writes both, so there are no hand-written bindings to keep in step
with the corpus. ``--limit`` is not implemented: rows that need it list
"python" in their manifest "skip".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    from google.protobuf.message import DecodeError

    from protowire import pxf, sbe
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


def _decode_pxf(fds_bytes: bytes, schema: str, data: bytes) -> int:
    pxf.unmarshal_bytes(b"", fds_bytes, schema)  # Binds, or the harness fails.
    try:
        pxf.unmarshal_bytes(data, fds_bytes, schema)
    except ValueError as e:
        return _reject(f"pxf: {e}")
    return 0


def _decode_pb(fds_bytes: bytes, schema: str, data: bytes) -> int:
    msg = _message_class(fds_bytes, schema)()
    try:
        msg.ParseFromString(data)
    except DecodeError as e:
        return _reject(f"pb: {e}")
    return 0


def _pb_render(fds_bytes: bytes, schema: str, data: bytes) -> int:
    _message_class(fds_bytes, schema)().ParseFromString(data)  # Or the harness fails.
    try:
        pxf.marshal_bytes(data, fds_bytes, schema)
    except ValueError as e:
        return _reject(f"pb-render: {e}")
    return 0


def _decode_sbe(fds_bytes: bytes, schema: str, data: bytes) -> int:
    cls = _message_class(fds_bytes, schema)
    codec = sbe.Codec.from_message(cls)
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
        "pb": _decode_pb,
        "pb-render": _pb_render,
        "sbe": _decode_sbe,
    }[args.format]
    return decode(_load_fds(args.proto), args.schema, Path(args.input).read_bytes())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="check_decode")
    p.add_argument("--format", required=True, choices=["pxf", "pb", "pb-render", "sbe", "envelope"])
    p.add_argument("--schema", required=True)
    p.add_argument("--proto", required=True)
    p.add_argument("--input", required=True)
    args = p.parse_args(argv)
    try:
        return _run(args)
    except BaseException as e:  # noqa: BLE001 -- exit 1 would read as a clean reject.
        print(f"check_decode: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
