# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""A decode result deeper than upb's 100-level limit still loads (#40).

The native decoder returns PB bytes, which upb loads. A well-known type
written as a literal is no PXF level but one PB level, so a document at the
PXF limit of 100 with a Timestamp at the bottom is 101 PB levels. The spec
says it decodes (protowire#537), and protowire-go decodes it. A
`max_nesting_depth` raised past 100 needs the same.
"""

from __future__ import annotations

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory, timestamp_pb2
from google.protobuf.internal import api_implementation

from protowire import pxf

F = descriptor_pb2.FieldDescriptorProto

pytestmark = pytest.mark.skipif(
    api_implementation.Type() != "upb", reason="the depth limit and its switch are upb's"
)


def _schema():
    fd = descriptor_pb2.FileDescriptorProto(name="upbdepth.proto", package="ud", syntax="proto3")
    fd.dependency.append("google/protobuf/timestamp.proto")
    n = fd.message_type.add(name="N")
    n.field.add(name="c", number=1, type=F.TYPE_MESSAGE, type_name=".ud.N")
    n.field.add(name="v", number=2, type=F.TYPE_INT32)
    n.field.add(name="t", number=4, type=F.TYPE_MESSAGE, type_name=".google.protobuf.Timestamp")
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(timestamp_pb2.DESCRIPTOR.serialized_pb)
    pool.Add(fd)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("ud.N"))


N = _schema()
TS = "t = 2024-01-01T00:00:00Z"


def _doc(levels: int, leaf: str) -> str:
    return "c { " * levels + leaf + " }" * levels


def _bottom(msg):
    depth = 0
    while msg.HasField("c"):
        msg = msg.c
        depth += 1
    return depth, msg


def _switch():
    from google._upb import _message

    return _message.SetAllowOversizeProtos


def _switch_is_on() -> bool:
    from protowire import _upb

    return _upb._allow_oversize_is_on()


@pytest.fixture(autouse=True)
def _switch_off_after():
    yield
    _switch()(False)


def test_the_switch_still_exists():
    # The fix rests on this private upb function. If a protobuf release
    # drops it, this fails first.
    assert callable(_switch())


def test_upb_alone_refuses_the_document():
    # What #40 measured: without the switch, upb refuses the bytes. If upb
    # ever raises its own limit, this test says the fix is no longer needed.
    from protowire import _protowire
    from protowire._schema import fds_for_message

    out = _protowire.pxf_unmarshal(
        _doc(100, TS).encode(), fds_for_message(N()), "ud.N", False, False, (0,) * 6
    )
    with pytest.raises(Exception, match="upb_DecodeOptions_MaxDepth"):
        N().MergeFromString(out)


@pytest.mark.parametrize("decode", [pxf.unmarshal, pxf.unmarshal_full])
def test_a_literal_at_the_limit_decodes(decode):
    msg = N()
    decode(_doc(100, TS), msg)
    depth, bottom = _bottom(msg)
    assert depth == 100
    assert bottom.t.seconds == 1704067200


def test_the_limit_itself_is_still_enforced():
    with pytest.raises(ValueError, match="MaxNestingDepth=100"):
        pxf.unmarshal(_doc(101, "v = 1"), N())


def test_a_raised_limit_decodes_past_100():
    msg = N()
    pxf.unmarshal(_doc(300, "v = 1"), msg, max_nesting_depth=300)
    assert _bottom(msg)[0] == 300
    with pytest.raises(ValueError, match="MaxNestingDepth=299"):
        pxf.unmarshal(_doc(300, "v = 1"), N(), max_nesting_depth=299)


def test_the_switch_is_turned_back_off():
    assert not _switch_is_on()
    pxf.unmarshal(_doc(100, TS), N())
    assert not _switch_is_on()


def test_a_callers_switch_is_left_on():
    _switch()(True)
    pxf.unmarshal(_doc(100, TS), N())
    assert _switch_is_on()


def test_the_message_is_replaced_not_merged():
    # The first parse fails part-way; what it left must not survive.
    msg = N()
    msg.v = 7
    pxf.unmarshal(_doc(100, TS), msg)
    assert msg.v == 0
    assert _bottom(msg)[0] == 100
