# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Keyed repeated fields, as the spec's testdata/keyed/ fixtures describe them.

This port gets keyed fields from protowire-cpp (trendvidia/protowire#116,
protowire-cpp#18), so these tests check what reaches Python: decoding each
accept fixture, and what pxf.marshal writes back. The reject fixtures are in
test_pxf_spec_rejects.py.

CI has no checkout of trendvidia/protowire, so keyed.proto is rebuilt here
with `(pxf.key)` as raw option bytes at its registered number, 1316. The
bodies are the fixtures' own, without their comments.
"""

from __future__ import annotations

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import pxf

F = descriptor_pb2.FieldDescriptorProto
_KEY = 1316


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _key(name: str) -> bytes:
    return _varint(_KEY << 3 | 2) + _varint(len(name)) + name.encode()


def _field(msg, name, number, type_, *, label=F.LABEL_OPTIONAL, type_name="", key=""):
    f = msg.field.add(name=name, number=number, type=type_, label=label)
    if type_name:
        f.type_name = type_name
    if key:
        f.options.MergeFromString(_key(key))
    return f


def _schema() -> descriptor_pool.DescriptorPool:
    fd = descriptor_pb2.FileDescriptorProto(name="keyed.proto", package="keyed.v1", syntax="proto3")
    R = F.LABEL_REPEATED

    node = fd.message_type.add(name="Node")
    _field(node, "id", 1, F.TYPE_STRING)
    _field(node, "type", 2, F.TYPE_STRING)
    _field(node, "weight", 3, F.TYPE_INT32)
    _field(node, "children", 4, F.TYPE_MESSAGE, label=R, type_name=".keyed.v1.Node", key="id")

    region = fd.message_type.add(name="Region")
    _field(region, "name", 1, F.TYPE_STRING)
    _field(region, "replicas", 2, F.TYPE_INT32)

    deployment = fd.message_type.add(name="Deployment")
    _field(deployment, "service", 1, F.TYPE_STRING)
    _field(deployment, "regions", 2, F.TYPE_MESSAGE, label=R, type_name=".keyed.v1.Region", key="name")

    choice = fd.message_type.add(name="Choice")
    choice.oneof_decl.add(name="pick")
    for number, name in ((1, "name"), (2, "other")):
        _field(choice, name, number, F.TYPE_STRING).oneof_index = 0

    tag = fd.message_type.add(name="Tag")
    f = _field(tag, "name", 1, F.TYPE_STRING)
    f.proto3_optional = True
    f.oneof_index = 0
    tag.oneof_decl.add(name="_name")

    catalog = fd.message_type.add(name="Catalog")
    _field(catalog, "choices", 1, F.TYPE_MESSAGE, label=R, type_name=".keyed.v1.Choice", key="name")
    _field(catalog, "tags", 2, F.TYPE_MESSAGE, label=R, type_name=".keyed.v1.Tag", key="name")

    pool = descriptor_pool.DescriptorPool()
    pool.Add(fd)
    return pool


_POOL = _schema()


def _decode(message: str, body: str):
    m = message_factory.GetMessageClass(_POOL.FindMessageTypeByName("keyed.v1." + message))()
    pxf.unmarshal(body, m)
    return m


ROUNDTRIP_KEYED = """\
id = "root"
type = "VBox"
children {
  greeting {
    type = "Label"
    weight = 1
  }
  counter_row {
    type = "HBox"
    weight = 2
  }
}
"""

ROUNDTRIP_QUOTED = """\
service = "edge"
regions {
  "us-east-1" {
    replicas = 3
  }
  "eu-west-2" {
    replicas = 1
  }
}
"""

ANONYMOUS_EQUIVALENCE = """\
id = "root"
type = "VBox"
children = [
  {
    id = "greeting"
    type = "Label"
    weight = 1
  }
  {
    id = "counter_row"
    type = "HBox"
    weight = 2
  }
]
"""

ANONYMOUS_DUPLICATE_OK = """\
id = "root"
children = [
  {
    id = "dup"
    type = "Label"
  }
  {
    id = "dup"
    type = "HBox"
  }
]
"""

REDUNDANT_KEY_OK = """\
id = "root"
children {
  greeting {
    id = "greeting"
    type = "Label"
  }
}
"""

KEY_ONEOF_OK = """\
choices {
  alpha {}
  beta {
    name = "beta"
  }
}
choices = [
  {
    other = "x"
  }
]
"""

KEY_NULL_ANONYMOUS_OK = """\
tags = [
  {
    name = null
  }
]
"""


@pytest.mark.parametrize("message,body", [
    ("Node", ROUNDTRIP_KEYED),
    ("Deployment", ROUNDTRIP_QUOTED),
], ids=["roundtrip-keyed", "roundtrip-quoted"])
def test_keyed_form_reencodes_to_itself(message, body):
    assert pxf.marshal(_decode(message, body)) == body


def test_entry_name_is_the_key_field():
    m = _decode("Node", ROUNDTRIP_KEYED)
    assert [(c.id, c.type, c.weight) for c in m.children] == [
        ("greeting", "Label", 1), ("counter_row", "HBox", 2)]


def test_anonymous_form_decodes_to_the_same_message():
    anonymous = _decode("Node", ANONYMOUS_EQUIVALENCE)
    assert anonymous == _decode("Node", ROUNDTRIP_KEYED)
    assert pxf.marshal(anonymous) == ROUNDTRIP_KEYED


def test_duplicate_keys_stay_anonymous():
    m = _decode("Node", ANONYMOUS_DUPLICATE_OK)
    assert [(c.id, c.type) for c in m.children] == [("dup", "Label"), ("dup", "HBox")]
    out = pxf.marshal(m)
    assert "children = [" in out
    assert _decode("Node", out) == m


def test_agreeing_key_assignment_is_dropped_on_encode():
    m = _decode("Node", REDUNDANT_KEY_OK)
    assert m.children[0].id == "greeting"
    assert pxf.marshal(m) == 'id = "root"\nchildren {\n  greeting {\n    type = "Label"\n  }\n}\n'


def test_key_in_a_oneof():
    m = _decode("Catalog", KEY_ONEOF_OK)
    assert [c.WhichOneof("pick") for c in m.choices] == ["name", "name", "other"]
    assert [m.choices[0].name, m.choices[1].name, m.choices[2].other] == ["alpha", "beta", "x"]
    assert _decode("Catalog", pxf.marshal(m)) == m


def test_null_key_in_anonymous_form():
    m = _decode("Catalog", KEY_NULL_ANONYMOUS_OK)
    assert len(m.tags) == 1 and not m.tags[0].HasField("name")
    assert _decode("Catalog", pxf.marshal(m)) == m
