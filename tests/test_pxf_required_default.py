# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Draft -01 § Required and Default: (pxf.required) and (pxf.default) on one
field must be refused when the descriptor is bound.

The schemas are built from raw option bytes, at the registered numbers, because
that is what reaches the decoder: this port has no annotations.proto of its own.
"""

from __future__ import annotations

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import pxf

_VARINT, _LEN = 0, 2
_REQUIRED, _DEFAULT, _KEY = 1314, 1315, 1316


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _opt_bool(number: int, value: bool) -> bytes:
    return _varint(number << 3 | _VARINT) + _varint(int(value))


def _opt_str(number: int, value: str) -> bytes:
    return _varint(number << 3 | _LEN) + _varint(len(value)) + value.encode()


REQUIRED = _opt_bool(_REQUIRED, True)
NOT_REQUIRED = _opt_bool(_REQUIRED, False)
DEFAULT = _opt_str(_DEFAULT, "x")

STRING = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
MESSAGE = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE


def _add_field(msg, name, number, options=b"", type_=STRING, type_name=""):
    f = msg.field.add(name=name, number=number, type=type_)
    if type_name:
        f.type_name = type_name
    if options:
        f.options.MergeFromString(options)


def _file(name, *deps) -> descriptor_pb2.FileDescriptorProto:
    fd = descriptor_pb2.FileDescriptorProto(name=name, package="t", syntax="proto3")
    fd.dependency.extend(deps)
    return fd


def _bind(*files):
    """Return the FDS bytes and a class for t.M, the first file's message."""
    fds = descriptor_pb2.FileDescriptorSet()
    pool = descriptor_pool.DescriptorPool()
    for fd in reversed(files):
        fds.file.append(fd)
        pool.Add(fd)
    desc = pool.FindMessageTypeByName("t.M")
    return fds.SerializeToString(), message_factory.GetMessageClass(desc)


def _one_field(options: bytes):
    fd = _file("one.proto")
    _add_field(fd.message_type.add(name="M"), "f", 1, options)
    return _bind(fd)


def _assert_refused(fds: bytes, cls, element: str, file: str) -> None:
    # A document supplying the field: the schema is wrong whatever the input.
    doc = 'f = "y"\n' if cls.DESCRIPTOR.fields_by_name.get("f") else ""
    for call in (
        lambda: pxf.unmarshal(doc, cls()),
        lambda: pxf.unmarshal_full(doc, cls()),
        lambda: pxf.unmarshal_bytes(doc, fds, "t.M"),
        lambda: pxf.unmarshal_full_bytes(doc, fds, "t.M"),
    ):
        with pytest.raises(ValueError) as e:
            call()
        assert "(pxf.required) and (pxf.default) are exclusive on one field" in str(e.value)
        assert element in str(e.value)
    assert pxf.validate_descriptor(cls()) == [
        pxf.Violation(
            kind="required_default",
            element=element,
            name=element.rsplit(".", 1)[1],
            file=file,
        )
    ]


def test_required_with_default_is_refused_at_bind():
    fds, cls = _one_field(REQUIRED + DEFAULT)
    _assert_refused(fds, cls, "t.M.f", "one.proto")


def test_option_order_does_not_matter():
    fds, cls = _one_field(DEFAULT + REQUIRED)
    _assert_refused(fds, cls, "t.M.f", "one.proto")


def test_required_false_beside_default_stays_accepted():
    fds, cls = _one_field(NOT_REQUIRED + DEFAULT)
    assert pxf.validate_descriptor(cls()) == []
    msg = cls()
    pxf.unmarshal_full("", msg)
    assert msg.f == "x"  # Proves cpp read the default from these bytes.


def test_required_alone_and_default_alone_are_accepted():
    _, required = _one_field(REQUIRED)
    assert pxf.validate_descriptor(required()) == []
    with pytest.raises(ValueError, match="required"):  # cpp read this one too.
        pxf.unmarshal_full("", required())
    _, default = _one_field(DEFAULT)
    assert pxf.validate_descriptor(default()) == []


def test_nested_message_is_covered():
    fd = _file("nested.proto")
    _add_field(fd.message_type.add(name="M").nested_type.add(name="In"), "f", 1, REQUIRED + DEFAULT)
    fds, cls = _bind(fd)
    _assert_refused(fds, cls, "t.M.In.f", "nested.proto")


def test_import_closure_is_covered_even_when_no_field_uses_the_type():
    dep = _file("dep.proto")
    _add_field(dep.message_type.add(name="Other"), "f", 1, REQUIRED + DEFAULT)
    root = _file("root.proto", "dep.proto")
    root.message_type.add(name="M")
    fds, cls = _bind(root, dep)
    _assert_refused(fds, cls, "t.Other.f", "dep.proto")


def test_skip_validate_skips_this_check_like_the_others():
    fds, cls = _one_field(REQUIRED + DEFAULT)
    pxf.unmarshal('f = "y"\n', cls(), skip_validate=True)
    pxf.unmarshal_bytes('f = "y"\n', fds, "t.M", skip_validate=True)


def test_misplaced_key_option_is_reported_with_its_kind():
    fd = _file("key.proto")
    _add_field(fd.message_type.add(name="M"), "f", 1, _opt_str(_KEY, "id"))
    _, cls = _bind(fd)
    assert [v.kind for v in pxf.validate_descriptor(cls())] == ["key_option"]
