# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""protowire.pb against an independent oracle: a port of protowire-go's
encoding/pb/conformance_test.go.

Tests that marshal with ``pb.marshal`` and read back with ``pb.unmarshal``
pass for a codec that is wrong consistently. The oracle here is the protobuf
runtime over a schema compiled by protoc (testdata/pb/conf.proto, Go's
conformanceProtoSrc verbatim), and every case is checked both ways:

- write: the bytes pb writes, read by the runtime, are the message the runtime
  builds from the same values, and record by record they are the bytes the
  runtime itself writes;
- read: the bytes the runtime writes decode here to the same values.

The expected values are written in the schema's text format, never through
pb. As in Go, the type model has no fixed32/fixed64/sfixed*, no enum and no
oneof: the oneof cases set one member, and one test pins two.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Annotated, Optional

import pytest
from google.protobuf import message_factory, text_format

from protowire import pb


@dataclass
class ConfInner:
    name: Annotated[str, pb.Field(1)] = ""
    n: Annotated[int, pb.Field(2)] = 0


@dataclass
class ConfAll:
    s: Annotated[str, pb.Field(1)] = ""
    i64: Annotated[int, pb.Field(2)] = 0
    i32: Annotated[int, pb.Field(3, pb.INT32)] = 0
    u64: Annotated[int, pb.Field(4, pb.UINT64)] = 0
    u32: Annotated[int, pb.Field(5, pb.UINT32)] = 0
    z64: Annotated[int, pb.Field(6, pb.SINT64)] = 0
    z32: Annotated[int, pb.Field(7, pb.SINT32)] = 0
    b: Annotated[bool, pb.Field(8)] = False
    f64: Annotated[float, pb.Field(9)] = 0.0
    f32: Annotated[float, pb.Field(10, pb.FLOAT)] = 0.0
    buf: Annotated[bytes, pb.Field(11)] = b""
    inner: Annotated[Optional[ConfInner], pb.Field(12)] = None
    lst: Annotated[list[ConfInner], pb.Field(13)] = field(default_factory=list)
    nums: Annotated[list[int], pb.Field(14, pb.UINT32)] = field(default_factory=list)
    zigs: Annotated[list[int], pb.Field(15, pb.SINT64)] = field(default_factory=list)
    reals: Annotated[list[float], pb.Field(16, pb.FLOAT)] = field(default_factory=list)
    strs: Annotated[list[str], pb.Field(17)] = field(default_factory=list)
    m: Annotated[dict[str, int], pb.Field(18)] = field(default_factory=dict)
    mm: Annotated[dict[Annotated[int, pb.INT32], Optional[ConfInner]], pb.Field(19)] = field(default_factory=dict)
    a: Annotated[str, pb.Field(20)] = ""
    c: Annotated[int, pb.Field(21)] = 0
    d: Annotated[Optional[ConfInner], pb.Field(22)] = None
    flags: Annotated[list[bool], pb.Field(23)] = field(default_factory=list)
    negs: Annotated[list[int], pb.Field(24)] = field(default_factory=list)
    bm: Annotated[dict[bool, bool], pb.Field(25)] = field(default_factory=dict)


MAP_FIELDS = (18, 19, 25)
F32_MIN_SUBNORMAL = struct.unpack("<f", struct.pack("<f", 1e-45))[0]

# (name, ours, text): ours is what pb marshals and what the read direction
# must produce; text is the same value in the schema's terms.
CASES = [
    (
        "scalars",
        ConfAll(s="héllo", i64=1 << 40, i32=7, u64=(1 << 64) - 1, u32=(1 << 32) - 1,
                z64=3, z32=5, b=True, f64=6.02214076e23, f32=1.5, buf=b"\x00\x01\xff"),
        r'''s: "héllo" i64: 1099511627776 i32: 7 u64: 18446744073709551615 u32: 4294967295
            z64: 3 z32: 5 b: true f64: 6.02214076e23 f32: 1.5 buf: "\000\001\377"''',
    ),
    (
        # int32/int64 negatives are sign-extended 10-byte varints;
        # sint32/sint64 negatives are zigzag (protowire-go#92's shape).
        "negative integers",
        ConfAll(i64=-1, i32=-(1 << 31), z64=-(1 << 63), z32=-1),
        "i64: -1 i32: -2147483648 z64: -9223372036854775808 z32: -1",
    ),
    (
        "float specials",
        ConfAll(f64=-math.inf, f32=math.inf, reals=[0.0, -0.0, F32_MIN_SUBNORMAL]),
        "f64: -inf f32: inf reals: [0, -0.0, 1e-45]",
    ),
    ("nested", ConfAll(inner=ConfInner(name="in", n=-2)), 'inner { name: "in" n: -2 }'),
    (
        # A message field has presence: an empty submessage is a zero-length
        # record, and it decodes back to an instance, not None.
        "nested empty",
        ConfAll(inner=ConfInner()),
        "inner {}",
    ),
    (
        "repeated messages",
        ConfAll(lst=[ConfInner(name="a", n=1), ConfInner(), ConfInner(name="c")]),
        'list { name: "a" n: 1 } list {} list { name: "c" }',
    ),
    (
        # Zeros inside a packed list are elements, not absences.
        "packed",
        ConfAll(nums=[1, 0, 300], zigs=[-1, 0, 2], reals=[1.5, 0.0], flags=[True, False, True],
                negs=[-1, 0, -(1 << 63)]),
        "nums: [1, 0, 300] zigs: [-1, 0, 2] reals: [1.5, 0] flags: [true, false, true] "
        "negs: [-1, 0, -9223372036854775808]",
    ),
    ("repeated strings", ConfAll(strs=["b", "", "a"]), 'strs: ["b", "", "a"]'),
    (
        "maps",
        ConfAll(m={"one": 1, "minus": -1}, mm={2: ConfInner(name="two"), -1: ConfInner(n=3)}),
        '''m { key: "one" value: 1 } m { key: "minus" value: -1 }
           mm { key: 2 value { name: "two" } } mm { key: -1 value { n: 3 } }''',
    ),
    ("oneof string member", ConfAll(a="chosen"), 'a: "chosen"'),
    ("oneof int member", ConfAll(c=9), "c: 9"),
    ("oneof message member", ConfAll(d=ConfInner(n=4)), "d { n: 4 }"),
    (
        "everything at once",
        ConfAll(s="x", i64=-5, u32=9, b=True, f32=2.0, buf=b"b", inner=ConfInner(name="i"),
                lst=[ConfInner(n=1)], nums=[4], strs=["s"], m={"k": 2}, c=1),
        's: "x" i64: -5 u32: 9 b: true f32: 2 buf: "b" inner { name: "i" } list { n: 1 } '
        'nums: [4] strs: ["s"] m { key: "k" value: 2 } c: 1',
    ),
]
IDS = [c[0] for c in CASES]


@pytest.fixture(scope="module")
def all_cls(pb_oracle_pool):
    return message_factory.GetMessageClass(pb_oracle_pool.FindMessageTypeByName("conf.All"))


def oracle_message(cls, text: str):
    return text_format.Parse(text, cls())


def oracle_marshal(msg) -> bytes:
    return msg.SerializeToString(deterministic=True)


def _varint(b: bytes, i: int) -> tuple[int, int]:
    v = shift = 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << shift
        if c < 0x80:
            return v, i
        shift += 7


def records_by_field(b: bytes) -> dict[int, list[bytes]]:
    """Top-level records (tag + value) by field number, in wire order.

    Map entries are sorted: their order is not part of the contract (the
    runtime sorts by key under deterministic=True, pb keeps dict order).
    """
    out: dict[int, list[bytes]] = {}
    i = 0
    while i < len(b):
        start = i
        tag, i = _varint(b, i)
        typ = tag & 7
        if typ == 0:
            _, i = _varint(b, i)
        elif typ == 1:
            i += 8
        elif typ == 5:
            i += 4
        elif typ == 2:
            n, i = _varint(b, i)
            i += n
        else:
            raise AssertionError(f"unexpected wire type {typ}")
        out.setdefault(tag >> 3, []).append(b[start:i])
    for f in MAP_FIELDS:
        if f in out:
            out[f].sort()
    return out


@pytest.mark.parametrize("name,ours,text", CASES, ids=IDS)
def test_write_direction(all_cls, name, ours, text):
    """Ports TestConformance_WriteDirection."""
    want = oracle_message(all_cls, text)
    data = pb.marshal(ours)
    got = all_cls.FromString(data)  # a conformant decoder reads what pb writes
    assert got == want, f"oracle read our bytes as:\n{got}\nwant:\n{want}"
    assert records_by_field(data) == records_by_field(oracle_marshal(want)), (
        "every record must be the bytes a conformant encoder writes"
    )


@pytest.mark.parametrize("name,ours,text", CASES, ids=IDS)
def test_read_direction(all_cls, name, ours, text):
    """Ports TestConformance_ReadDirection."""
    theirs = oracle_marshal(oracle_message(all_cls, text))
    got = pb.unmarshal(theirs, ConfAll)
    assert _same(got, ours)


def _same(a, b) -> bool:
    """Equality that tells -0.0 from 0.0, as Go's assert.Equal does on floats."""
    if isinstance(a, float) and isinstance(b, float):
        return struct.pack("<d", a) == struct.pack("<d", b)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if hasattr(a, "__dataclass_fields__") and type(a) is type(b):
        return all(_same(getattr(a, f), getattr(b, f)) for f in a.__dataclass_fields__)
    return a == b


def test_two_oneof_members_set(all_cls):
    """Ports TestConformance_TwoOneofMembersSet.

    A class mirroring a oneof can set two members, and pb writes both. A
    conformant reader keeps the last one on the wire, so field order decides
    which member wins. pb has no oneof; a schema-driven encoder is the tool for
    a message whose oneof must be enforced.
    """
    got = all_cls.FromString(pb.marshal(ConfAll(a="first", c=2)))
    assert got.WhichOneof("choice") == "c", "the later field on the wire wins"


def test_map_entries_carry_zero_values(all_cls):
    """Ports TestConformance_MapEntriesCarryZeroValues (protowire-go#105).

    A map entry always carries its key and its value, zero or not, as
    protobuf's own encoders write it. Both that form and the omission an older
    payload may carry still read.
    """
    want = oracle_message(all_cls, 'm { key: "zero" value: 0 } m { key: "" value: -1 } mm { key: 0 value {} }')
    ours = pb.marshal(ConfAll(m={"zero": 0, "": -1}, mm={0: ConfInner()}))
    mine = records_by_field(ours)
    assert records_by_field(oracle_marshal(want)) == mine
    assert bytes([0x92, 0x01, 0x08, 0x0A, 0x04]) + b"zero" + bytes([0x10, 0x00]) in mine[18], '"zero" → 0 carries its value'
    assert bytes([0x92, 0x01, 0x0D, 0x0A, 0x00, 0x10] + [0xFF] * 9 + [0x01]) in mine[18], '"" → -1 carries its key'
    assert bytes([0x9A, 0x01, 0x04, 0x08, 0x00, 0x12, 0x00]) in mine[19], "0 → {} carries both"

    back = pb.unmarshal(oracle_marshal(want), ConfAll)
    assert back.m == {"zero": 0, "": -1}
    assert back.mm == {0: ConfInner()}
    old = pb.unmarshal(
        bytes([0x92, 0x01, 0x06, 0x0A, 0x04]) + b"zero" + bytes([0x9A, 0x01, 0x02, 0x12, 0x00]), ConfAll
    )
    assert old.m == {"zero": 0}
    assert old.mm == {0: ConfInner()}


def test_map_entry_false_bool(all_cls):
    """Ports TestConformance_MapEntryFalseBool (protowire-go#238)."""
    want = oracle_message(all_cls, "bm { key: false value: false } bm { key: true value: false }")
    orig = ConfAll(bm={False: False, True: False})
    ours = pb.marshal(orig)
    mine = records_by_field(ours)
    assert records_by_field(oracle_marshal(want)) == mine
    assert bytes([0xCA, 0x01, 0x04, 0x08, 0x00, 0x10, 0x00]) in mine[25], "false → false"
    assert bytes([0xCA, 0x01, 0x04, 0x08, 0x01, 0x10, 0x00]) in mine[25], "true → false"
    assert pb.unmarshal(ours, ConfAll).bm == orig.bm


# Python's ints have no width, so a value outside its declared type is
# refused rather than wrapped. Go's types cannot hold such a value.
@pytest.mark.parametrize(
    "obj",
    [
        ConfAll(i32=1 << 31),
        ConfAll(u32=-1),
        ConfAll(u64=1 << 64),
        ConfAll(z32=-(1 << 31) - 1),
        ConfAll(i64=1 << 63),
        ConfAll(nums=[1 << 32]),
        ConfAll(f32=1e300),
    ],
)
def test_a_value_outside_its_declared_type_is_refused(obj):
    with pytest.raises(ValueError, match="outside"):
        pb.marshal(obj)


def test_the_read_direction_keeps_the_low_bits_of_a_wide_varint():
    """A varint wider than a 32-bit field keeps its low 32 bits, as Go's
    reflect.SetInt / SetUint on an int32 / uint32 field does."""
    data = bytes([0x18]) + bytes([0xFF] * 9 + [0x01])  # field 3 (int32): 2^64-1
    assert pb.unmarshal(data, ConfAll).i32 == -1
    data = bytes([0x28, 0x80, 0x80, 0x80, 0x80, 0x10])  # field 5 (uint32): 2^32
    assert pb.unmarshal(data, ConfAll).u32 == 0
