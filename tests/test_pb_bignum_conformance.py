# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Conformance for pb's big-number codec against an INDEPENDENT oracle.

Ports protowire-go's encoding/pb/bignum_conformance_test.go (protowire-go#92).

``pxf/bignum.proto`` declares ``Decimal.scale`` and ``BigFloat.exponent`` as
plain ``int32``, which is a plain varint on the wire; zigzag is ``sint32``,
which neither field is. protowire-go once used zigzag for both, in all four
places (two writers, two readers), so it round-tripped its own bytes perfectly
and disagreed with every schema-conformant reader. Self-consistency is what
hid it: a test that marshals with ``marshal`` and unmarshals with
``unmarshal`` can never catch it.

The oracle here is google.protobuf over a descriptor compiled from
testdata/pb/bignum.proto itself (the ``pb_oracle_pool`` fixture), where Go
uses protobuf-go's dynamicpb. Both directions are covered: bytes this package
WRITES must be readable by a conformant decoder, and bytes a conformant
encoder WRITES must be readable here.

Only zero survives the difference: over the full int32 range, zigzag and plain
varint agree on exactly one value, so any non-zero scale or exponent was wrong
on the wire, not merely the negative ones.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from fractions import Fraction
from typing import Annotated, Optional

import pytest
from google.protobuf import message_factory

from protowire import pb

# --- Wire helpers: written here, not borrowed from pb, so the test does not
# read the bytes with the code under test. ---------------------------------


def _append_varint(out: bytearray, v: int) -> None:
    v &= (1 << 64) - 1
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)


def _append_tag(out: bytearray, num: int, typ: int) -> None:
    _append_varint(out, (num << 3) | typ)


def _append_bytes(out: bytearray, b: bytes) -> None:
    _append_varint(out, len(b))
    out += b


def _consume_varint(b: bytes, pos: int) -> tuple[int, int]:
    v, shift = 0, 0
    while True:
        assert pos < len(b), "truncated varint"
        c = b[pos]
        pos += 1
        v |= (c & 0x7F) << shift
        if c < 0x80:
            return v, pos
        shift += 7
        assert shift < 70, "varint overflows"


def _consume_field_value(typ: int, b: bytes, pos: int) -> int:
    if typ == 0:
        return _consume_varint(b, pos)[1]
    if typ == 1:
        return pos + 8
    if typ == 5:
        return pos + 4
    if typ == 2:
        n, pos = _consume_varint(b, pos)
        assert pos + n <= len(b), "corrupt length"
        return pos + n
    raise AssertionError(f"unexpected wire type {typ}")


def _submessage(b: bytes, num: int) -> bytes:
    """The length-delimited payload of field num in a record pb produced."""
    pos = 0
    while pos < len(b):
        tag, pos = _consume_varint(b, pos)
        n, typ = tag >> 3, tag & 7
        if n == num:
            assert typ == 2
            ln, pos = _consume_varint(b, pos)
            return b[pos : pos + ln]
        pos = _consume_field_value(typ, b, pos)
    pytest.fail(f"field {num} not present")


def _field_records(b: bytes) -> dict[int, bytes]:
    """One entry per field, keyed by number, holding its bytes, tag included.

    Comparing these instead of the whole buffer keeps the assertion byte-exact
    per field while not asserting an order the wire format does not fix (Go's
    note: protobuf-go's output order is deliberately unstable, so a
    whole-buffer comparison against it flaked ~13% of runs).
    """
    out: dict[int, bytes] = {}
    pos = 0
    while pos < len(b):
        start = pos
        tag, pos = _consume_varint(b, pos)
        num, typ = tag >> 3, tag & 7
        pos = _consume_field_value(typ, b, pos)
        assert num not in out, f"field {num} repeated"
        out[num] = b[start:pos]
    return out


def _wrap(num: int, sub: bytes) -> bytes:
    """The outer record pb expects: one length-delimited field carrying sub."""
    out = bytearray()
    _append_tag(out, num, 2)
    _append_bytes(out, sub)
    return bytes(out)


# --- An exact model of big.Float's rounding, for the expected values. -----


def _round_fraction(v: Fraction, prec: int) -> pb.BigFloat:
    """v rounded to prec bits, ties to even, as big.ParseFloat(.., ToNearestEven)
    and big.Float.SetInt/SetMantExp round. Computed in exact rationals here,
    independently of pb's own rounding."""
    neg = v < 0
    a = -v if neg else v
    if a == 0 or prec == 0:
        return pb.BigFloat(0, 0, prec, neg)
    # e such that 2**(prec-1) <= a / 2**e < 2**prec.
    e = a.numerator.bit_length() - a.denominator.bit_length() - prec
    while a / Fraction(2) ** e >= 2**prec:
        e += 1
    while a / Fraction(2) ** e < 2 ** (prec - 1):
        e -= 1
    scaled = a / Fraction(2) ** e
    q = scaled.numerator // scaled.denominator
    r = scaled - q
    if r > Fraction(1, 2) or (r == Fraction(1, 2) and q & 1):
        q += 1
    return pb.BigFloat(q, e, prec, neg)


def _parse_float(text: str, prec: int) -> pb.BigFloat:
    """big.ParseFloat(text, 10, prec, big.ToNearestEven)."""
    return _round_fraction(Fraction(decimal.Decimal(text)), prec)


@dataclass
class ConformanceStruct:
    """Go's conformanceStruct: the shape encoding/pb marshals."""

    price: Annotated[Optional[decimal.Decimal], pb.Field(1)] = None
    coefficient: Annotated[Optional[pb.BigFloat], pb.Field(2)] = None


@pytest.fixture(scope="module")
def oracle(pb_oracle_pool):
    """The schema's Decimal and BigFloat classes (Go's bignumDescriptors)."""
    get = lambda n: message_factory.GetMessageClass(pb_oracle_pool.FindMessageTypeByName(n))  # noqa: E731
    return get("pxf.Decimal"), get("pxf.BigFloat")


def _oracle_marshal(m) -> bytes:
    """Go's oracleMarshal: proto.MarshalOptions{Deterministic: true}."""
    return m.SerializeToString(deterministic=True)


# --- The write half --------------------------------------------------------


@pytest.mark.parametrize(
    "value,want_scale",
    [
        (decimal.Decimal("3.1415"), 4),
        (decimal.Decimal("1.5"), 1),
        (decimal.Decimal("0.001"), 3),
        (decimal.Decimal("-2.25"), 2),
    ],
)
def test_written_decimal_is_readable_by_schema(oracle, value, want_scale):
    """Ports TestBignumConformance_WrittenBytesAreReadableBySchema, decimal/*.

    What this package emits must mean the same thing to the schema. 3.1415
    gives scale 4. Under zigzag that was written as 8, so a conformant reader
    saw 31415 x 10^-8: the right digits, wrong by four orders of magnitude,
    with nothing to signal it.
    """
    decimal_cls, _ = oracle
    data = pb.marshal(ConformanceStruct(price=value))
    o = decimal_cls()
    # A schema-conformant decoder must accept what this package writes.
    o.ParseFromString(_submessage(data, 1))
    # The scale a conformant reader sees must be the scale that was meant.
    assert o.scale == want_scale
    # And the value it reconstructs from those fields.
    got = Fraction(int.from_bytes(o.unscaled, "big"), 10**o.scale)
    if o.negative:
        got = -got
    assert got == Fraction(value), f"want {value}, oracle reconstructed {got}"


@pytest.mark.parametrize(
    "text,prec",
    [("1", 53), ("1.5", 53), ("1e10", 53), ("-1.23e-45", 64), ("6.02214076e23", 128)],
    ids=["1.0", "1.5", "1e10", "-1.23e-45", "6.02214076e23"],
)
def test_written_bigfloat_is_readable_by_schema(oracle, text, prec):
    """Ports TestBignumConformance_WrittenBytesAreReadableBySchema, bigfloat/*.

    BigFloat.exponent is adjExp = exp - prec, negative for essentially every
    value, which is where protowire-go#92 bit hardest: 1.0 at prec 53 wrote an
    exponent a conformant reader saw as 103 rather than -52, a factor of 2^155.
    """
    _, bigfloat_cls = oracle
    bf = _parse_float(text, prec)
    data = pb.marshal(ConformanceStruct(coefficient=bf))
    o = bigfloat_cls()
    o.ParseFromString(_submessage(data, 2))
    # value = mantissa x 2^exponent, per the schema, at the schema's prec.
    got = _round_fraction(Fraction(int.from_bytes(o.mantissa, "big")) * Fraction(2) ** o.exponent, o.prec)
    if o.negative:
        got = pb.BigFloat(got.mantissa, got.exponent, got.prec, not got.negative)
    assert got.to_fraction() == bf.to_fraction(), f"want {bf}, oracle reconstructed {got} (exponent {o.exponent})"


# --- The read half ---------------------------------------------------------


def test_schema_bytes_negative_scale(oracle):
    """Ports TestBignumConformance_SchemaBytesAreReadableHere/negative scale.

    The read half, with a NEGATIVE scale written by the oracle. pb never
    produces a negative scale itself, so this direction is the only way to
    reach that arm. 25 x 10^-(-2) = 2500: a negative scale from a conformant
    producer must mean trailing zeros.
    """
    decimal_cls, _ = oracle
    sub = _oracle_marshal(decimal_cls(unscaled=(25).to_bytes(1, "big"), scale=-2))
    got = pb.unmarshal(_wrap(1, sub), ConformanceStruct)
    assert Fraction(got.price) == 2500, f"got {got.price}"


def test_schema_bytes_positive_scale(oracle):
    """Ports TestBignumConformance_SchemaBytesAreReadableHere/positive scale.

    31415 x 10^-4 = 3.1415.
    """
    decimal_cls, _ = oracle
    sub = _oracle_marshal(decimal_cls(unscaled=(31415).to_bytes(2, "big"), scale=4))
    got = pb.unmarshal(_wrap(1, sub), ConformanceStruct)
    assert Fraction(got.price) == Fraction(31415, 10000), f"got {got.price}"


def test_schema_bytes_negative_exponent(oracle):
    """Ports TestBignumConformance_SchemaBytesAreReadableHere/negative exponent.

    mantissa 2^52 x 2^-52 = 1.0, at prec 53.
    """
    _, bigfloat_cls = oracle
    mant = 1 << 52
    sub = _oracle_marshal(bigfloat_cls(mantissa=mant.to_bytes(7, "big"), exponent=-52, prec=53))
    got = pb.unmarshal(_wrap(2, sub), ConformanceStruct)
    assert got.coefficient.to_fraction() == 1, f"got {got.coefficient}"


@pytest.mark.parametrize(
    "wire_prec", [1 << 32, (1 << 32) + 1, (1 << 32) + 53, (1 << 33) + 64, (1 << 63) + 53, (1 << 64) - 1]
)
@pytest.mark.parametrize("neg", [False, True])
def test_schema_bytes_prec_varint_past_uint32(oracle, wire_prec, neg):
    """Ports TestBignumConformance_SchemaBytesAreReadableHere/prec varint past uint32.

    prec is a uint32, and a varint wider than 32 bits keeps its low 32 bits in
    a protobuf reader. protowire-go once kept the whole varint and clamped it
    to MaxPrec: 2^32 read as 2^32-1 bits there and as precision 0, a zero, to
    the schema (protowire-go#230).
    """
    _, bigfloat_cls = oracle
    # 25 x 2^-3 at whatever precision the wire's low bits say.
    sub = bytearray()
    _append_tag(sub, 1, 2)
    _append_bytes(sub, b"\x19")
    _append_tag(sub, 2, 0)
    _append_varint(sub, -3)
    _append_tag(sub, 3, 0)
    _append_varint(sub, wire_prec)
    if neg:
        _append_tag(sub, 4, 0)
        _append_varint(sub, 1)
    sub = bytes(sub)

    o = bigfloat_cls()
    o.ParseFromString(sub)
    prec = o.prec
    assert prec == wire_prec & 0xFFFFFFFF, "the oracle keeps the low 32 bits"

    # The low 32 bits are what the prec bound sees too (protowire#499):
    # 2^32+53 is 53 bits and reads, and only 2^64-1, 2^32-1 bits, is past
    # the bound. (Checked before the expected value is built: Go's
    # big.Float.SetPrec(2^32-1) costs nothing, but this test's exact model
    # of the rounding would compute 2^(2^32-1).)
    if prec > 13603:
        with pytest.raises(ValueError, match="MaxNumericLiteralDigits"):
            pb.unmarshal(_wrap(2, sub), ConformanceStruct)
        return

    # The schema's value: precision 0 is a zero signed by negative, as in
    # Unmarshal; otherwise mantissa x 2^exponent rounded to prec.
    if prec == 0:
        want = pb.BigFloat(0, 0, 0, neg)
    else:
        v = Fraction(int.from_bytes(o.mantissa, "big")) * Fraction(2) ** o.exponent
        want = _round_fraction(-v if neg else v, prec)
    got = pb.unmarshal(_wrap(2, sub), ConformanceStruct).coefficient
    assert got.prec == prec, f"wire prec {wire_prec}"
    assert got.to_fraction() == want.to_fraction(), f"wire prec {wire_prec}: got {got}, want {want}"
    assert got.negative == neg, f"wire prec {wire_prec}"


def test_byte_for_byte(oracle):
    """Ports TestBignumConformance_ByteForByte.

    Pins the encoding itself, so a future change to either side has to look
    at the bytes rather than at a round trip that agrees with itself.
    """
    decimal_cls, _ = oracle
    ours = _submessage(pb.marshal(ConformanceStruct(price=decimal.Decimal("3.1415"))), 1)
    theirs = _oracle_marshal(decimal_cls(unscaled=(31415).to_bytes(2, "big"), scale=4))
    # Field by field, not buffer by buffer: every field must encode to the
    # same bytes, and the order they appear in is not part of the contract.
    assert _field_records(ours) == _field_records(theirs)
    # The specific byte the bug lived in: scale 4 is varint 0x04, not
    # zigzag's 0x08.
    assert b"\x10\x04" in ours, "field 2 varint must be 4 (plain), not 8 (zigzag)"
