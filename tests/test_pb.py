# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""protowire.pb against protowire-go's encoding/pb tests (v1.17.0, ca79f5c).

Ports pb_test.go, utf8_test.go and example_test.go. Each test names the Go
test it ports. Where a Go test only round-trips, the port also checks the
bytes against what protowire-go's ``Marshal`` writes for the same value,
measured by running v1.17.0 (those vectors are marked "Go Marshal bytes").

Error messages name the Python attribute where Go names the Go struct field
(``field name: ...`` for Go's ``field Name: ...``); the rest of each message is
Go's text.
"""

from __future__ import annotations

import io
import math
import struct
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Optional

import pytest

from protowire import pb

# --- wire helpers: google.golang.org/protobuf/encoding/protowire's Append* ---

_VARINT, _FIXED32, _BYTES = 0, 5, 2


def append_varint(b: bytes, v: int) -> bytes:
    out = bytearray(b)
    v &= (1 << 64) - 1
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)


def append_tag(b: bytes, num: int, typ: int) -> bytes:
    return append_varint(b, (num << 3) | typ)


def append_bytes(b: bytes, v: bytes) -> bytes:
    return append_varint(b, len(v)) + v


def len_field(b: bytes, num: int, v: bytes) -> bytes:
    return append_bytes(append_tag(b, num, _BYTES), v)


def big_float_of(x: float, prec: int) -> pb.BigFloat:
    """new(big.Float).SetPrec(prec).SetFloat64(x)."""
    n, d = abs(x).as_integer_ratio()
    return pb.BigFloat(n, -(d.bit_length() - 1), prec, x < 0)


# --- pb_test.go --------------------------------------------------------------


@dataclass
class Inner:
    name: Annotated[str, pb.Field(1)] = ""
    value: Annotated[int, pb.Field(2)] = 0


@dataclass
class Outer:
    title: Annotated[str, pb.Field(1)] = ""
    count: Annotated[int, pb.Field(2, pb.UINT32)] = 0
    score: Annotated[float, pb.Field(3)] = 0.0
    active: Annotated[bool, pb.Field(4)] = False
    data: Annotated[bytes, pb.Field(5)] = b""
    items: Annotated[list[Inner], pb.Field(6)] = field(default_factory=list)
    ptrs: Annotated[list[Optional[Inner]], pb.Field(7)] = field(default_factory=list)
    signed: Annotated[int, pb.Field(8)] = 0
    small_f: Annotated[float, pb.Field(9, pb.FLOAT)] = 0.0
    untagged: str = ""  # no pb.Field — should be skipped


def test_round_trip():
    """Ports TestRoundTrip."""
    orig = Outer(
        title="hello",
        count=42,
        score=3.14,
        active=True,
        data=b"\xde\xad",
        items=[Inner("a", 1), Inner("b", -7)],
        ptrs=[Inner("p", 99)],
        signed=-12345,
        small_f=2.5,
        untagged="should be ignored",
    )
    data = pb.marshal(orig)
    # Go Marshal bytes.
    assert data.hex() == (
        "0a0568656c6c6f102a191f85eb51b81e094020012a02dead32050a01611001320e0a0162"
        "10f9ffffffffffffffff013a050a0170106340c79fffffffffffffff014d00002040"
    )
    got = pb.unmarshal(data, Outer())
    # Untagged field should not round-trip
    orig.untagged = ""
    assert got == orig


def test_zero_values():
    """Ports TestZeroValues."""
    data = pb.marshal(Outer())
    # Proto3: all-zero struct should produce empty bytes
    assert data == b""
    pb.unmarshal(data, Outer())


# Go's TestCustomByteType declares `type CustomByte byte`: a field of Go kind
# uint8. pb has no uint8 type (Python has no named integer kinds), and Go
# writes every unsigned kind as the same plain varint, so the nearest port
# declares the field UINT32. What it can no longer test is the Go-only part:
# that a named type over byte is not mistaken for []byte's element.
@dataclass
class WithCustomByte:
    tag: Annotated[int, pb.Field(1, pb.UINT32)] = 0
    value: Annotated[int, pb.Field(2)] = 0


def test_custom_byte_type():
    """Ports TestCustomByteType (as UINT32; see the comment above)."""
    orig = WithCustomByte(tag=3, value=-42)
    data = pb.marshal(orig)
    assert data.hex() == "080310d6ffffffffffffffff01"  # Go Marshal bytes
    assert pb.unmarshal(data, WithCustomByte) == orig


@dataclass
class Big3:
    a: Annotated[str, pb.Field(1)] = ""
    b: Annotated[str, pb.Field(2)] = ""
    c: Annotated[str, pb.Field(3)] = ""


@dataclass
class Small1:
    a: Annotated[str, pb.Field(1)] = ""


def test_unknown_fields_skipped():
    """Ports TestUnknownFieldsSkipped."""
    data = pb.marshal(Big3(a="aa", b="bb", c="cc"))
    # Decode into a smaller struct — field 2 and 3 are unknown
    got = pb.unmarshal(data, Small1)
    assert got.a == "aa"


@dataclass
class BigNumStruct:
    balance: Annotated[Optional[int], pb.Field(1, pb.BIGINT)] = None
    price: Annotated[Optional[Decimal], pb.Field(2)] = None
    coefficient: Annotated[Optional[pb.BigFloat], pb.Field(3)] = None


def test_big_num_round_trip():
    """Ports TestBigNumRoundTrip."""
    balance = int("115792089237316195423570985008687907853269984665640564039457584007913129639935")
    price = Decimal("3.1415")  # new(big.Rat).SetFrac64(31415, 10000)
    coeff = big_float_of(6.02214076e23, 128)
    orig = BigNumStruct(balance=balance, price=price, coefficient=coeff)

    data = pb.marshal(orig)
    assert data.hex() == (  # Go Marshal bytes
        "0a220a20ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "12060a027ab710041a200a10ff0c2e52be28b800000000000000000010cfffffffffffffffff01188001"
    )
    got = pb.unmarshal(data, BigNumStruct(balance=0, price=Decimal(0), coefficient=pb.BigFloat()))
    assert got.balance == orig.balance
    assert got.price == orig.price
    assert got.coefficient.to_fraction() == orig.coefficient.to_fraction()


def test_big_num_nil_pointers():
    """Ports TestBigNumNilPointers."""
    assert pb.marshal(BigNumStruct()) == b""


def test_big_num_negative():
    """Ports TestBigNumNegative.

    Go's Price is the big.Rat -1/3, which no decimal.Decimal holds. Go writes
    a non-terminating rational as its 50-digit FloatString (ratToDecimal), so
    the port gives the Decimal those 50 digits, and checks that the bytes are
    the ones Go writes for -1/3.
    """
    orig = BigNumStruct(
        balance=-999999999999,
        price=Decimal("-0." + "3" * 50),
        coefficient=big_float_of(-1.23e-45, 64),
    )
    data = pb.marshal(orig)
    assert data.hex() == (  # Go Marshal bytes, Price = big.Rat -1/3
        "0a090a05e8d4a50fff1001121b0a1516cebe5ca886222d46415711a9e721555555555555103218011a190a08"
        "e0b4b45687b3c00010abfeffffffffffffff0118402001"
    )
    got = pb.unmarshal(data, BigNumStruct(balance=0, price=Decimal(0), coefficient=pb.BigFloat()))
    assert got.balance == orig.balance
    # For non-terminating decimal, we lose precision — just check sign
    assert got.price < 0
    assert got.coefficient.to_fraction() == orig.coefficient.to_fraction()


def test_big_num_pointer_fields():
    """Ports TestBigNumPointerFields."""
    data = pb.marshal(BigNumStruct(balance=42))
    assert data.hex() == "0a030a012a"  # Go Marshal bytes
    got = pb.unmarshal(data, BigNumStruct(balance=0))
    assert got.balance == 42
    # A None field is allocated by unmarshal, as Go allocates a nil pointer.
    assert pb.unmarshal(data, BigNumStruct).balance == 42


@dataclass
class WithZigZag:
    a: Annotated[int, pb.Field(1)] = 0  # proto3 int64 (plain varint)
    b: Annotated[int, pb.Field(2, pb.SINT64)] = 0  # proto3 sint64 (zigzag varint)


def test_zig_zag_tag_option():
    """Ports TestZigZagTagOption."""
    orig = WithZigZag(a=-1, b=-1)
    data = pb.marshal(orig)
    # Field 1 (A=-1, plain varint): tag 0x08, then 10 bytes of 0xff..0x01.
    # Field 2 (B=-1, zigzag): tag 0x10, then varint(zigzag(-1)) = varint(1) = 0x01.
    assert len(data) == 13, data.hex()
    assert data.hex() == "08ffffffffffffffffff011001"  # Go Marshal bytes
    assert pb.unmarshal(data, WithZigZag) == orig


@dataclass
class WithMaps:
    tags: Annotated[dict[str, str], pb.Field(1)] = field(default_factory=dict)
    codes: Annotated[dict[Annotated[int, pb.INT32], str], pb.Field(2)] = field(default_factory=dict)
    counts: Annotated[dict[str, int], pb.Field(3)] = field(default_factory=dict)
    servers: Annotated[dict[str, Inner], pb.Field(4)] = field(default_factory=dict)


def test_map_round_trip():
    """Ports TestMapRoundTrip."""
    orig = WithMaps(
        tags={"env": "prod", "team": "platform", "key with space": "v"},
        codes={404: "Not Found", 500: "Internal", -1: "Negative"},
        counts={"a": 1, "b": -7, "c": 0},
        servers={"primary": Inner("p", 1), "secondary": Inner("s", 2)},
    )
    data = pb.marshal(orig)
    assert pb.unmarshal(data, WithMaps) == orig


def test_map_entry_bytes():
    """Go Marshal bytes for one entry per map (Go's map order is random, so
    TestMapRoundTrip's values cannot be compared byte for byte)."""
    v = WithMaps(tags={"env": "prod"}, codes={-1: "Negative"}, counts={"c": 0}, servers={"primary": Inner("p", 1)})
    assert pb.marshal(v).hex() == (
        "0a0b0a03656e76120470726f64121508ffffffffffffffffff0112084e65676174697665"
        "1a050a0163100022100a077072696d61727912050a01701001"
    )


@pytest.mark.parametrize(
    "val",
    [WithMaps(), WithMaps(tags={}, codes={})],
    ids=["nil maps", "empty maps"],
)
def test_empty_map(val):
    """Ports TestEmptyMap: empty and absent maps both produce empty bytes."""
    assert pb.marshal(val) == b""


def test_map_zero_value_entries():
    """Ports TestMapZeroValueEntries.

    Go's comment says the zero key and value "are skipped per proto3
    semantics", but Go writes both halves of every entry, zeros included:
    its bytes for this value are an entry ``0a 00 12 00`` (pb.go's map case
    marshals key and value as elements). The port checks those bytes.
    """
    orig = WithMaps(tags={"": "", "k": ""}, counts={"": 0})
    data = pb.marshal(orig)
    got = pb.unmarshal(data, WithMaps)
    assert got.tags == {"": "", "k": ""}
    assert got.counts == {"": 0}

    one = WithMaps(tags={"": ""}, counts={"": 0})
    assert pb.marshal(one).hex() == "0a040a0012001a040a001000"  # Go Marshal bytes


@dataclass
class WithPacked:
    u32: Annotated[list[int], pb.Field(1, pb.UINT32)] = field(default_factory=list)
    i64: Annotated[list[int], pb.Field(2)] = field(default_factory=list)
    zig: Annotated[list[int], pb.Field(3, pb.SINT32)] = field(default_factory=list)
    f32: Annotated[list[float], pb.Field(4, pb.FLOAT)] = field(default_factory=list)
    f64: Annotated[list[float], pb.Field(5)] = field(default_factory=list)
    bs: Annotated[list[bool], pb.Field(6)] = field(default_factory=list)


def test_packed_round_trip():
    """Ports TestPackedRoundTrip."""
    # Zero-valued elements must survive: packed encoding has no
    # per-element presence, so nothing may be dropped.
    orig = WithPacked(
        u32=[1, 0, 2, 300],
        i64=[-1, 0, 42],
        zig=[-5, 0, 5],
        f32=[1.5, 0, -2.25],
        f64=[3.14, 0, -1e100],
        bs=[True, False, True],
    )
    data = pb.marshal(orig)
    assert data.hex() == (  # Go Marshal bytes
        "0a05010002ac02120cffffffffffffffffff01002a1a0309000a220c0000c03f00000000000010c0"
        "2a181f85eb51b81e094000000000000000007dc39425ad49b2d43203010001"
    )
    assert pb.unmarshal(data, WithPacked) == orig


def test_packed_wire_format():
    """Ports TestPackedWireFormat."""
    # Canonical example from the protobuf encoding docs: repeated
    # int32/uint32 {3, 270, 86942} packs to `LEN 6: 03 8E 02 9E A7 05`.
    data = pb.marshal(WithPacked(u32=[3, 270, 86942]))
    assert data == bytes([0x0A, 0x06, 0x03, 0x8E, 0x02, 0x9E, 0xA7, 0x05])


def test_unpacked_decode():
    """Ports TestUnpackedDecode."""
    # proto3 decoders must accept unpacked encodings of packable fields
    # (one tag per element) — e.g. from proto2 or a non-default encoder.
    data = b""
    for v in (7, 0, 9):
        data = append_varint(append_tag(data, 1, _VARINT), v)
    data = append_tag(data, 4, _FIXED32) + struct.pack("<f", 1.5)

    got = pb.unmarshal(data, WithPacked)
    assert got.u32 == [7, 0, 9]
    assert got.f32 == [1.5]


def test_packed_mixed_with_unpacked():
    """Ports TestPackedMixedWithUnpacked."""
    # A field may legally appear as a mix of packed and unpacked
    # records; elements concatenate in order.
    data = append_varint(append_tag(b"", 1, _VARINT), 1)
    data = append_tag(data, 1, _BYTES)
    payload = append_varint(append_varint(b"", 2), 3)
    data = append_bytes(data, payload)

    assert pb.unmarshal(data, WithPacked).u32 == [1, 2, 3]


@pytest.mark.parametrize(
    "data",
    [
        # Truncated varint inside the packed payload.
        bytes([0x0A, 0x01, 0x80]),
        # Packed float32 payload not a multiple of 4 bytes.
        bytes([0x22, 0x03, 0x00, 0x00, 0x80]),
        # LEN header longer than remaining data.
        bytes([0x0A, 0x05, 0x01]),
    ],
    ids=["truncated varint", "partial fixed32", "truncated payload"],
)
def test_packed_corrupt(data):
    """Ports TestPackedCorrupt."""
    with pytest.raises(ValueError):
        pb.unmarshal(data, WithPacked())


@dataclass
class RepeatedStrings:
    s: Annotated[list[str], pb.Field(1)] = field(default_factory=list)


def test_packed_strings_not_packed():
    """Ports TestPackedStringsNotPacked."""
    # Repeated strings are never packed — each element keeps its own
    # LEN-typed tag and must round-trip unchanged.
    orig = RepeatedStrings(s=["a", "bc", "def"])
    data = pb.marshal(orig)
    want = b""
    for s in orig.s:
        want = len_field(want, 1, s.encode())
    assert data == want
    assert pb.unmarshal(data, RepeatedStrings) == orig


def test_repeated_string_zero_elements():
    """Ports TestRepeatedStringZeroElements."""
    # Zero-valued elements of unpacked repeated fields must be emitted —
    # an empty string element is an empty LEN record, not an omission.
    orig = RepeatedStrings(s=["a", "", "b"])
    data = pb.marshal(orig)
    want = b""
    for s in orig.s:
        want = len_field(want, 1, s.encode())
    assert data == want
    assert pb.unmarshal(data, RepeatedStrings) == orig


@dataclass
class RepeatedBigNums:
    # Go: Ints []big.Int, Rats []big.Rat — value elements, zero included.
    ints: Annotated[list[int], pb.Field(1, pb.BIGINT)] = field(default_factory=list)
    rats: Annotated[list[Decimal], pb.Field(2)] = field(default_factory=list)


def test_repeated_big_num_zero_elements():
    """Ports TestRepeatedBigNumZeroElements."""
    orig = RepeatedBigNums(ints=[5, 0, -3], rats=[Decimal("0.5"), Decimal(0)])
    data = pb.marshal(orig)
    assert data.hex() == "0a030a01050a000a050a0103100112050a010510011200"  # Go Marshal bytes
    got = pb.unmarshal(data, RepeatedBigNums)
    assert got.ints == orig.ints
    assert got.rats == orig.rats


@dataclass
class Flags:
    m: Annotated[dict[str, bool], pb.Field(1)] = field(default_factory=dict)
    p: Annotated[list[Optional[bool]], pb.Field(2)] = field(default_factory=list)


def test_false_bool_elements():
    """Ports TestFalseBoolElements."""
    # #238: a false bool that is an element — a map value, or a nil
    # pointer element standing for its zero — is written as 0, not 1.
    orig = Flags(m={"a": False}, p=[None, False])
    data = pb.marshal(orig)
    want = bytes([0x0A, 0x05, 0x0A, 0x01, ord("a"), 0x10, 0x00, 0x10, 0x00, 0x10, 0x00])
    assert data == want, data.hex()
    got = pb.unmarshal(data, Flags)
    assert "a" in got.m and got.m["a"] is False
    # Go: two pointers to false. A decoded Optional element is never None.
    assert got.p == [False, False]


def test_repeated_nil_pointer_elements():
    """Ports TestRepeatedNilPointerElements."""
    # A nil pointer element encodes as its pointee's zero record: the
    # list length survives, decoding to an allocated zero-value pointer.
    orig = Outer(ptrs=[None, Inner("x", 7), None])
    data = pb.marshal(orig)
    assert data.hex() == "3a003a050a017810073a00"  # Go Marshal bytes
    got = pb.unmarshal(data, Outer)
    assert got.ptrs == [Inner(), Inner("x", 7), Inner()]


@dataclass
class RepeatedBytes:
    bufs: Annotated[list[bytes], pb.Field(1)] = field(default_factory=list)


def test_repeated_bytes_zero_elements():
    """Ports TestRepeatedBytesZeroElements."""
    # Empty elements of a repeated bytes field keep their LEN records:
    # element count is preserved across the round trip.
    orig = RepeatedBytes(bufs=[b"\x01", b"", b"\x02\x03"])
    data = pb.marshal(orig)
    assert data.hex() == "0a01010a000a020203"  # Go Marshal bytes
    got = pb.unmarshal(data, RepeatedBytes)
    assert got.bufs == [b"\x01", b"", b"\x02\x03"]


@dataclass
class SingularZero:
    p: Annotated[Optional[Inner], pb.Field(1)] = None
    s: Annotated[str, pb.Field(2)] = ""
    b: Annotated[int, pb.Field(3, pb.BIGINT)] = 0  # Go: B big.Int (a value)
    u: Annotated[int, pb.Field(4, pb.UINT32)] = 0
    l: Annotated[list[str], pb.Field(5)] = field(default_factory=list)  # noqa: E741


def test_singular_zero_skip_unchanged():
    """Ports TestSingularZeroSkipUnchanged."""
    # The element rule must not leak into singular fields: an all-zero
    # struct with pointer, string, and big-number fields still encodes
    # to nothing.
    assert pb.marshal(SingularZero()) == b""


# A struct field is a language value, so marshal writes a NaN as the
# canonical quiet NaN whatever payload it holds (draft -01 § Numeric
# Literals; protowire-java#161). Go's math.NaN() is 0x7FF8000000000001.
@dataclass
class NanHolder:
    d: Annotated[float, pb.Field(1)] = 0.0
    f: Annotated[float, pb.Field(2, pb.FLOAT)] = 0.0
    ds: Annotated[list[float], pb.Field(3)] = field(default_factory=list)
    fs: Annotated[list[float], pb.Field(4, pb.FLOAT)] = field(default_factory=list)


def test_marshal_writes_nan_canonically():
    """Ports TestMarshalWritesNaNCanonically.

    A Python float is a double, so Go's float32 payload NaN 0x7FC00001 is
    held as the double it widens to (payload kept), and written as a FLOAT.
    """
    payload64 = struct.unpack("<d", struct.pack("<Q", 0x7FF8000000000001))[0]
    payload32 = struct.unpack("<f", struct.pack("<I", 0x7FC00001))[0]
    assert struct.pack("<d", payload64) != struct.pack("<d", math.nan)  # the payload survived
    go_nan = payload64  # math.NaN()
    b = pb.marshal(NanHolder(d=payload64, f=payload32, ds=[go_nan, 1], fs=[payload32]))
    want = "09000000000000f87f" + "150000c07f" + "1a10" + "000000000000f87f" + "000000000000f03f" + "2204" + "0000c07f"
    assert b.hex() == want


# --- utf8_test.go --------------------------------------------------------------


@dataclass
class Utf8Inner:
    text: Annotated[str, pb.Field(1)] = ""


# Go's utf8Msg has `Named utf8Named` (type utf8Named string) at field 3 to show
# a named string type is checked too. Python has no named string types; the
# field is a plain str here, so that site repeats the singular case.
@dataclass
class Utf8Msg:
    name: Annotated[str, pb.Field(1)] = ""
    ptr: Annotated[Optional[str], pb.Field(2)] = None
    named: Annotated[str, pb.Field(3)] = ""
    list: Annotated[list[str], pb.Field(4)] = field(default_factory=list)
    inner: Annotated[Utf8Inner, pb.Field(5)] = field(default_factory=Utf8Inner)
    labels: Annotated[dict[str, str], pb.Field(6)] = field(default_factory=dict)
    data: Annotated[bytes, pb.Field(7)] = b""


def _entry(key: bytes, value: bytes) -> bytes:
    return len_field(len_field(b"", 1, key), 2, value)


_UTF8_SITES = [
    ("singular", lambda s: len_field(b"", 1, s), "field name: invalid UTF-8 in string"),
    ("pointer", lambda s: len_field(b"", 2, s), "field ptr: invalid UTF-8 in string"),
    ("named type", lambda s: len_field(b"", 3, s), "field named: invalid UTF-8 in string"),
    ("list element", lambda s: len_field(len_field(b"", 4, b"ok"), 4, s), "field list: invalid UTF-8 in string"),
    ("nested", lambda s: len_field(b"", 5, len_field(b"", 1, s)), "field inner: field text: invalid UTF-8 in string"),
    ("map key", lambda s: len_field(b"", 6, _entry(s, b"v")), "field labels: map key: invalid UTF-8 in string"),
    ("map value", lambda s: len_field(b"", 6, _entry(b"k", s)), "field labels: map value: invalid UTF-8 in string"),
]

_UTF8_SEQUENCES = [
    ("stray byte", b"AB\xff"),
    ("lone continuation", b"\x80"),
    ("latin-1", b"caf\xe9"),
    ("cut off", b"abc\xc3"),
    ("overlong", b"\xc0\x80"),
    ("surrogate", b"\xed\xa0\x80"),
    ("past U+10FFFF", b"\xf4\x90\x80\x80"),
]


@pytest.mark.parametrize("site", _UTF8_SITES, ids=[s[0] for s in _UTF8_SITES])
@pytest.mark.parametrize("seq", _UTF8_SEQUENCES, ids=[s[0] for s in _UTF8_SEQUENCES])
def test_string_field_rejects_invalid_utf8(site, seq):
    """Ports TestStringFieldRejectsInvalidUTF8.

    HARDENING.md § UTF-8 names PB length-delimited bytes. Each place a string
    can sit, and each way a byte sequence can be invalid.
    """
    _, wire_of, want = site
    wire = wire_of(seq[1])
    with pytest.raises(ValueError) as e:
        pb.unmarshal(wire, Utf8Msg())
    assert str(e.value) == want

    framed = append_bytes(b"", wire)
    with pytest.raises(ValueError) as e:
        pb.Decoder(io.BytesIO(framed)).decode(Utf8Msg())
    assert str(e.value) == want
    assert not isinstance(e.value, pb.FramingError)


def test_string_field_accepts_valid_utf8():
    """Ports TestStringFieldAcceptsValidUTF8: the check must not refuse what the rule allows."""
    want = Utf8Msg(
        name="é",
        ptr="€",
        named="😀",
        list=["a\x00b", ""],
        inner=Utf8Inner(text="日本語"),
        labels={"ключ": "значение"},
        data=bytes([0xFF, 0xFE, 0x80, 0xC0]),
    )
    wire = pb.marshal(want)
    assert pb.unmarshal(wire, Utf8Msg) == want


def test_decoder_continues_past_invalid_utf8():
    """Ports TestDecoderContinuesPastInvalidUTF8: bad text is a bad message, not a bad stream."""
    stream = b""
    for text in (b"AB\xff", b"good"):
        stream = append_bytes(stream, len_field(b"", 1, text))
    dec = pb.Decoder(io.BytesIO(stream))

    with pytest.raises(ValueError) as e:
        dec.decode(Utf8Msg())
    assert str(e.value) == "field name: invalid UTF-8 in string"
    nxt = dec.decode(Utf8Msg())
    assert nxt is not None and nxt.name == "good"
    assert dec.decode(Utf8Msg()) is None  # Go: io.EOF


# --- example_test.go ---------------------------------------------------------


@dataclass
class Endpoint:
    path: Annotated[str, pb.Field(1)] = ""
    method: Annotated[str, pb.Field(2)] = ""
    port: Annotated[int, pb.Field(3)] = 0


@dataclass
class Config:
    hostname: Annotated[str, pb.Field(1)] = ""
    enabled: Annotated[bool, pb.Field(2)] = False
    endpoints: Annotated[list[Optional[Endpoint]], pb.Field(3)] = field(default_factory=list)


def test_example():
    """Ports Example: encode and decode a struct with field-number tags.

    The output is standard protobuf binary and is wire-compatible with any
    .proto message that uses the same field numbers and matching types.
    """
    src = Config(hostname="web-01", enabled=True, endpoints=[Endpoint(path="/api", method="GET", port=8080)])
    data = pb.marshal(src)
    assert data.hex() == "0a067765622d303110011a0e0a042f617069120347455418903f"  # Go Marshal bytes
    dst = pb.unmarshal(data, Config)
    out = f"{dst.hostname} {str(dst.enabled).lower()} {dst.endpoints[0].path} {dst.endpoints[0].port}"
    assert out == "web-01 true /api 8080"  # Go's // Output:


@dataclass
class Tick:
    symbol: Annotated[str, pb.Field(1)] = ""
    price: Annotated[int, pb.Field(2)] = 0


def test_example_encoder():
    """Ports ExampleEncoder: the length-prefixed stream API, messages back to back."""
    buf = io.BytesIO()
    enc = pb.Encoder(buf)
    enc.encode(Tick(symbol="AAPL", price=18900))
    enc.encode(Tick(symbol="GOOG", price=14250))
    # Go Marshal bytes for each Tick, each behind its varint length.
    assert buf.getvalue().hex() == "0a" + "0a044141504c10d49301" + "09" + "0a04474f4f4710aa6f"

    buf.seek(0)
    dec = pb.Decoder(buf)
    lines = []
    while (t := dec.decode(Tick)) is not None:
        lines.append(f"{t.symbol} {t.price}")
    assert lines == ["AAPL 18900", "GOOG 14250"]  # Go's // Output:
