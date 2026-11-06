# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Inputs the spec rejects that this port accepted until protowire-cpp v2.0.0.

Each reject case is one of the spec's fixtures, in testdata/nulls/ or
testdata/keyed/ of trendvidia/protowire, named in its id. CI has no checkout of
that repository, so the schemas are rebuilt here from raw option bytes, at the
registered numbers, as in test_pxf_required_default.py. Built against
protowire-cpp v1.2.0, every reject case here decodes.

The accept cases are the nearest inputs the spec allows. They show that each
rejection is for the reason the fixture gives.
"""

from __future__ import annotations

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, wrappers_pb2

from protowire import pxf

F = descriptor_pb2.FieldDescriptorProto
_REQUIRED, _KEY = 1314, 1316


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


REQUIRED = _varint(_REQUIRED << 3) + _varint(1)


def _key(name: str) -> bytes:
    return _varint(_KEY << 3 | 2) + _varint(len(name)) + name.encode()


def _field(msg, name, number, type_, *, label=F.LABEL_OPTIONAL, type_name="", options=b""):
    f = msg.field.add(name=name, number=number, type=type_, label=label)
    if type_name:
        f.type_name = type_name
    if options:
        f.options.MergeFromString(options)
    return f


def _optional(msg, name, number, type_, options=b""):
    # proto3 `optional`: a synthetic oneof holding the one field.
    f = _field(msg, name, number, type_, options=options)
    f.proto3_optional = True
    f.oneof_index = len(msg.oneof_decl)
    msg.oneof_decl.add(name="_" + name)


def _schema() -> descriptor_pool.DescriptorPool:
    fd = descriptor_pb2.FileDescriptorProto(name="s.proto", package="t", syntax="proto3")
    fd.dependency.append("google/protobuf/wrappers.proto")

    inner = fd.message_type.add(name="Inner")
    _field(inner, "x", 1, F.TYPE_INT32)

    holder = fd.message_type.add(name="Holder")
    _field(holder, "n", 1, F.TYPE_INT32)
    _optional(holder, "o", 2, F.TYPE_INT32)
    _field(holder, "l", 3, F.TYPE_INT32, label=F.LABEL_REPEATED)
    _field(holder, "msg", 6, F.TYPE_MESSAGE, type_name=".t.Inner")
    entry = holder.nested_type.add(name="MEntry")
    entry.options.map_entry = True
    _field(entry, "key", 1, F.TYPE_STRING)
    _field(entry, "value", 2, F.TYPE_INT32)
    _field(holder, "m", 8, F.TYPE_MESSAGE, label=F.LABEL_REPEATED, type_name=".t.Holder.MEntry")

    required = fd.message_type.add(name="Required")
    _optional(required, "r", 1, F.TYPE_INT32, options=REQUIRED)

    required_wrapper = fd.message_type.add(name="RequiredWrapper")
    _field(required_wrapper, "w", 1, F.TYPE_MESSAGE,
           type_name=".google.protobuf.Int32Value", options=REQUIRED)

    choice = fd.message_type.add(name="Choice")
    choice.oneof_decl.add(name="pick")
    for number, name in ((1, "name"), (2, "other")):
        _field(choice, name, number, F.TYPE_STRING).oneof_index = 0

    tag = fd.message_type.add(name="Tag")
    _optional(tag, "name", 1, F.TYPE_STRING)

    catalog = fd.message_type.add(name="Catalog")
    _field(catalog, "choices", 1, F.TYPE_MESSAGE, label=F.LABEL_REPEATED,
           type_name=".t.Choice", options=_key("name"))
    _field(catalog, "tags", 2, F.TYPE_MESSAGE, label=F.LABEL_REPEATED,
           type_name=".t.Tag", options=_key("name"))

    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(wrappers_pb2.DESCRIPTOR.serialized_pb)
    pool.Add(fd)
    return pool


_POOL = _schema()


def _new(name: str):
    return message_factory.GetMessageClass(_POOL.FindMessageTypeByName("t." + name))()


REJECTS = [
    ("nulls/err-scalar", "Holder", "n = null"),
    ("nulls/err-nested-scalar", "Holder", "msg { x = null }"),
    ("nulls/err-repeated", "Holder", "l = null"),
    ("nulls/err-map", "Holder", "m = null"),
    ("nulls/err-required-optional", "Required", "r = null"),
    ("nulls/err-required-wrapper", "RequiredWrapper", "w = null"),
    ("keyed/err-key-null", "Catalog", "tags { alpha { name = null } }"),
    ("keyed/err-key-oneof-sibling", "Catalog", 'choices { alpha { other = "x" } }'),
]

ACCEPTS = [
    ("optional scalar may be null", "Holder", "o = null"),
    ("required field with a value", "Required", "r = 0"),
    ("agreeing key in a named entry", "Catalog", 'tags { alpha { name = "alpha" } }'),
    ("key member of the oneof", "Catalog", "choices { alpha { } }"),
]


@pytest.mark.parametrize("fixture,message,body", REJECTS, ids=[r[0] for r in REJECTS])
@pytest.mark.parametrize("decode", [pxf.unmarshal, pxf.unmarshal_full], ids=["unmarshal", "full"])
def test_rejected(decode, fixture, message, body):
    with pytest.raises(ValueError):
        decode(body + "\n", _new(message))


@pytest.mark.parametrize("why,message,body", ACCEPTS, ids=[a[0] for a in ACCEPTS])
def test_accepted(why, message, body):
    pxf.unmarshal_full(body + "\n", _new(message))
