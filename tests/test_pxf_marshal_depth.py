# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""pxf.marshal refuses a message nested past max_nesting_depth (protowire#502).

A producer must not write PXF that a decoder at the same limit would refuse.
Depth is counted as the decoder counts it: the root is depth 0, and every `{`
or `[` is one more. So a repeated field costs two levels per element message,
and a Timestamp costs none, since it is written as a literal.
"""

from __future__ import annotations

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, timestamp_pb2

from protowire import pxf

F = descriptor_pb2.FieldDescriptorProto


def _schema():
    fd = descriptor_pb2.FileDescriptorProto(name="depth.proto", package="d", syntax="proto3")
    fd.dependency.append("google/protobuf/timestamp.proto")
    n = fd.message_type.add(name="N")
    n.field.add(name="c", number=1, type=F.TYPE_MESSAGE, type_name=".d.N")
    n.field.add(name="v", number=2, type=F.TYPE_INT32)
    n.field.add(name="l", number=3, type=F.TYPE_MESSAGE, label=F.LABEL_REPEATED, type_name=".d.N")
    n.field.add(name="t", number=4, type=F.TYPE_MESSAGE, type_name=".google.protobuf.Timestamp")
    entry = n.nested_type.add(name="MEntry")
    entry.options.map_entry = True
    entry.field.add(name="key", number=1, type=F.TYPE_STRING)
    entry.field.add(name="value", number=2, type=F.TYPE_MESSAGE, type_name=".d.N")
    n.field.add(name="m", number=5, type=F.TYPE_MESSAGE, label=F.LABEL_REPEATED,
                type_name=".d.N.MEntry")
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(timestamp_pb2.DESCRIPTOR.serialized_pb)
    pool.Add(fd)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("d.N"))


N = _schema()
_TOO_DEEP = "nesting depth exceeds MaxNestingDepth"


def _chain(levels: int):
    """A root with `levels` nested submessages: PXF depth `levels`."""
    root = N()
    m = root
    for _ in range(levels):
        m = m.c
    m.v = 1
    return root


def _list_chain(elements: int):
    """Each level is one element of a repeated field: PXF depth 2 * `elements`."""
    root = N()
    m = root
    for _ in range(elements):
        m = m.l.add()
    m.v = 1
    return root


def _map_chain(entries: int):
    """Each level is a map value: PXF depth 2 * `entries`, as in the corpus's deep-map rows."""
    root = N()
    m = root
    for _ in range(entries):
        m = m.m["k"]
    m.v = 1
    return root


def test_at_the_default_limit_is_written_and_reads_back():
    msg = _chain(100)
    out = pxf.marshal(msg)
    back = N()
    pxf.unmarshal(out, back)
    assert back == msg


def test_one_past_the_default_limit_is_refused():
    with pytest.raises(ValueError, match=_TOO_DEEP + "=100"):
        pxf.marshal(_chain(101))


def test_a_repeated_field_costs_two_levels_per_element():
    pxf.marshal(_list_chain(50))
    with pytest.raises(ValueError, match=_TOO_DEEP):
        pxf.marshal(_list_chain(51))


def test_a_map_value_costs_two_levels():
    pxf.marshal(_map_chain(50))
    # 51 entries nest 102 deep in PXF and in PB alike. The bridge reads PB only
    # to one level past the limit, so its refusal comes before the encoder's.
    with pytest.raises(ValueError, match="nest past max_nesting_depth=100"):
        pxf.marshal(_map_chain(51))


def test_a_timestamp_at_the_limit_is_a_literal_not_a_level():
    # The bytes nest 101 deep, the PXF only 100. The bridge reads one level past
    # the limit so that this is the encoder's call. Reading it back is #40.
    msg = _chain(100)
    leaf = msg
    for _ in range(100):
        leaf = leaf.c
    leaf.t.seconds = 1
    out = pxf.marshal(msg)
    assert out.count("{") == 100
    assert "t = 1970-01-01T00:00:01Z" in out


@pytest.mark.parametrize("limit", [5, 150])
def test_the_limit_is_per_call(limit):
    pxf.marshal(_chain(limit), max_nesting_depth=limit)
    with pytest.raises(ValueError, match=f"{_TOO_DEEP}={limit}"):
        pxf.marshal(_chain(limit + 1), max_nesting_depth=limit)


def test_marshal_bytes_takes_the_limit_too():
    fds = descriptor_pb2.FileDescriptorSet()
    fds.file.add().MergeFromString(timestamp_pb2.DESCRIPTOR.serialized_pb)
    fds.file.add().MergeFromString(N.DESCRIPTOR.file.serialized_pb)
    data = _chain(8).SerializeToString()
    pxf.marshal_bytes(data, fds.SerializeToString(), "d.N", max_nesting_depth=8)
    with pytest.raises(ValueError, match=_TOO_DEEP + "=7"):
        pxf.marshal_bytes(data, fds.SerializeToString(), "d.N", max_nesting_depth=7)
    # Two levels past the limit is past what the bridge reads.
    with pytest.raises(ValueError, match="proto parse failed.*max_nesting_depth=6"):
        pxf.marshal_bytes(data, fds.SerializeToString(), "d.N", max_nesting_depth=6)


def test_a_negative_limit_is_an_error():
    with pytest.raises(ValueError, match="must not be negative"):
        pxf.marshal(_chain(1), max_nesting_depth=-1)
