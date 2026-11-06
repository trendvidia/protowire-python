# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""validate_descriptor names every kind of bind-time violation protowire-cpp
reports, including the three v2.0.0 added to its ViolationKind.

The bridge maps cpp's enum to these strings. A kind it does not know came back
as "", so each test pins one kind's string.
"""

from __future__ import annotations

import typing

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import pxf

STRING = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
MESSAGE = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
REPEATED = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _message(*files: descriptor_pb2.FileDescriptorProto):
    pool = descriptor_pool.DescriptorPool()
    for fd in files:
        pool.Add(fd)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("t.M"))()


def _file(name: str, *deps: str, package: str = "t") -> descriptor_pb2.FileDescriptorProto:
    fd = descriptor_pb2.FileDescriptorProto(name=name, package=package, syntax="proto3")
    fd.dependency.extend(deps)
    return fd


def _only(msg) -> pxf.Violation:
    vs = pxf.validate_descriptor(msg)
    assert len(vs) == 1, vs
    return vs[0]


def test_default_option():
    # (pxf.default) on a repeated field: draft -01 "Default Placement".
    fd = _file("d.proto")
    f = fd.message_type.add(name="M").field.add(name="f", number=1, type=STRING, label=REPEATED)
    f.options.MergeFromString(_varint(1315 << 3 | 2) + _varint(1) + b"x")
    v = _only(_message(fd))
    assert (v.kind, v.element, v.name, v.file) == ("default_option", "t.M.f", "x", "d.proto")


def test_retired_number():
    # 50000 was (pxf.required) before protowire v1.12.0. It is checked only in
    # a file that imports protowire's annotations, so the schema imports a
    # stand-in at that path.
    annotations = _file("pxf/annotations.proto", package="pxf")
    fd = _file("r.proto", "pxf/annotations.proto")
    f = fd.message_type.add(name="M").field.add(name="f", number=1, type=STRING)
    f.options.MergeFromString(_varint(50000 << 3) + _varint(1))
    v = _only(_message(annotations, fd))
    assert (v.kind, v.element, v.name, v.file) == ("retired_number", "t.M.f", "50000", "r.proto")


def test_well_known_shape():
    # A google.protobuf.Timestamp without the fields the codec reads.
    wkt = _file("google/protobuf/timestamp.proto", package="google.protobuf")
    wkt.message_type.add(name="Timestamp")
    fd = _file("w.proto", "google/protobuf/timestamp.proto")
    fd.message_type.add(name="M").field.add(
        name="at", number=1, type=MESSAGE, type_name=".google.protobuf.Timestamp"
    )
    v = _only(_message(wkt, fd))
    assert (v.kind, v.element) == ("well_known_shape", "google.protobuf.Timestamp")


def test_kind_literal_lists_every_kind():
    kinds = set(typing.get_args(typing.get_type_hints(pxf.Violation)["kind"]))
    assert {"default_option", "retired_number", "well_known_shape"} <= kinds
