# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""pb.BigFloat: zeros on the wire, and normalisation as big.Float holds a value.

The zero tests port protowire-go's encoding/pb/bigfloat_zero_test.go. The
normalisation tests are this port's own: pb.BigFloat stands in for Go's
big.Float, so it must round (to nearest, ties to even), carry, underflow and
overflow where big.Float does. The rounding is checked against an exact model
in rationals rather than against pb's own arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Annotated, Optional

import pytest

from protowire import pb

INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1
BIG_MAX_PREC = (1 << 32) - 1  # Go's big.MaxPrec


@dataclass
class BigFloatHolder:
    f: Annotated[Optional[pb.BigFloat], pb.Field(1)] = None


# --- bigfloat_zero_test.go -------------------------------------------------


@pytest.mark.parametrize(
    "wire,neg", [(bytes([0x0A, 0x00]), False), (bytes([0x0A, 0x02, 0x20, 0x01]), True)], ids=["zero", "negative zero"]
)
def test_prec_zero_keeps_its_sign_read(wire, neg):
    """Ports TestBigFloatPrecZeroKeepsItsSign, the decode cases (protowire#410).

    A prec-0 big.Float is ±0, and PXF writes the two apart, "0" and "-0". The
    bytes are the spec corpus's (testdata/bigfloat dec-zero and
    dec-negative-zero): -0 is {negative: true} with no precision. Go used to
    read it as +0 at 53 bits, so a "-0" that came through PB lost its sign.
    """
    h = pb.unmarshal(wire, BigFloatHolder)
    assert h.f.mantissa == 0 and h.f.prec == 0 and h.f.negative == neg, f"{wire.hex(' ')} reads as {h.f}"


def test_prec_zero_keeps_its_sign_write():
    """Ports TestBigFloatPrecZeroKeepsItsSign, the encode half: Go's Marshal used
    to write nothing for a prec-0 -0."""
    out = pb.marshal(BigFloatHolder(f=pb.BigFloat(0, 0, 0, True)))  # new(big.Float).Neg(new(big.Float))
    assert out == bytes([0x0A, 0x02, 0x20, 0x01])
    back = pb.unmarshal(out, BigFloatHolder)
    assert back.f.negative and back.f.prec == 0, f"round trip: {back.f}; want -0 at prec 0"
    # +0 at prec 0 stays the omitted field it was.
    assert pb.marshal(BigFloatHolder(f=pb.BigFloat())) == b""


@pytest.mark.parametrize(
    "neg,want",
    [(False, bytes([0x0A, 0x02, 0x18, 0x40])), (True, bytes([0x0A, 0x04, 0x18, 0x40, 0x20, 0x01]))],
    ids=["zero", "negative zero"],
)
def test_zero_has_one_encoding(neg, want):
    """Ports TestBigFloatZeroHasOneEncoding (protowire-go#196).

    A zero at any prec is written with an empty mantissa and exponent 0,
    keeping prec and negative, which is what a PXF decoder stores for 0p64
    (draft -01 § PXF Annotations). The bytes are the spec corpus's
    dec-zero-p64 and dec-negative-zero-p64. Go's Marshal used to write
    exponent -prec, a second encoding of the same value.
    """
    out = pb.marshal(BigFloatHolder(f=pb.BigFloat(0, 0, 64, neg)))
    assert out == want, f"zero at prec 64 marshals to {out.hex(' ')}, want {want.hex(' ')}"
    back = pb.unmarshal(out, BigFloatHolder)
    assert back.f.mantissa == 0 and back.f.prec == 64 and back.f.negative == neg


def test_zero_mantissa_with_any_exponent_reads_as_zero():
    """Ports TestBigFloatZeroHasOneEncoding's last case: a zero mantissa with any
    exponent still reads as zero, so bytes the old encoder wrote stay
    readable."""
    old = bytes([0x0A, 0x0F, 0x10, 0xC0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x18, 0x40, 0x20, 0x01])
    h = pb.unmarshal(old, BigFloatHolder)
    assert h.f.mantissa == 0 and h.f.prec == 64 and h.f.negative, f"{old.hex(' ')} reads as {h.f}; want -0 at prec 64"


@pytest.mark.parametrize("neg", [False, True])
def test_zero_at_max_prec(neg):
    """Ports TestBigFloatZeroAtMaxPrec (protowire-go#196, protowire#499).

    A zero writes no exponent, so its precision does not bound one: at prec
    2^32-1, exponent -prec left int32 and Go's Marshal refused the zero. Its
    prec is still bounded, as any BigFloat's is: the zero at 2^32-1 bits is
    refused on writing and on reading, and the zero at the bound, 13603 bits,
    round-trips.
    """
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits"):
        pb.marshal(BigFloatHolder(f=pb.BigFloat(0, 0, BIG_MAX_PREC, neg)))
    # The bytes Go's Marshal used to write: prec 2^32-1, nothing else.
    old = bytes([0x0A, 0x06, 0x18, 0xFF, 0xFF, 0xFF, 0xFF, 0x0F])
    if neg:
        old = bytes([0x0A, 0x08, 0x18, 0xFF, 0xFF, 0xFF, 0xFF, 0x0F, 0x20, 0x01])
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits"):
        pb.unmarshal(old, BigFloatHolder)

    out = pb.marshal(BigFloatHolder(f=pb.BigFloat(0, 0, 13603, neg)))
    back = pb.unmarshal(out, BigFloatHolder)
    assert back.f.mantissa == 0 and back.f.prec == 13603 and back.f.negative == neg, f"{out.hex(' ')} reads as {back.f}"


# --- Normalisation (this port's own) ---------------------------------------


def _model(m: int, e: int, prec: int) -> Fraction:
    """m * 2**e rounded to prec bits, to nearest, ties to even, in rationals."""
    v = Fraction(m) * Fraction(2) ** e
    if m == 0:
        return v
    k = m.bit_length() - prec  # scale so the integer part has prec bits
    if k <= 0:
        return v
    unit = Fraction(2) ** (e + k)
    q, r = divmod(v, unit)
    q = int(q)
    if r > unit / 2 or (r == unit / 2 and q % 2 == 1):
        q += 1
    return q * unit


def test_rounding_matches_the_model_exhaustively():
    """Every mantissa below 2^10 at every prec 1..11: rounded as big.Float
    rounds, and stored reduced to an odd mantissa."""
    for prec in range(1, 12):
        for m in range(1, 1 << 10):
            for e in (-7, 0, 5):
                bf = pb.BigFloat(m, e, prec)
                assert bf.to_fraction() == _model(m, e, prec), (m, e, prec, bf)
                assert bf.mantissa & 1 == 1, bf
                assert bf.mantissa.bit_length() <= prec, bf
                assert bf.prec == prec


@pytest.mark.parametrize(
    "m,prec,mantissa,exponent",
    [
        (0b101, 2, 1, 2),  # 5 is between 4 and 6: the tie goes to even 4
        (0b1011, 3, 3, 2),  # 11 is between 10 and 12: the tie goes to even 12
        (0b1001, 3, 1, 3),  # 9 is between 8 and 10: the tie goes to even 8
        (0b1101, 2, 3, 2),  # 13 is below the midpoint 14: down to 12
        (0b1111, 3, 1, 4),  # 15 rounds up to 16, carrying into a new bit
        (0b111, 2, 1, 3),  # 7 ties between 6 and 8, goes to even 8: a carry
    ],
)
def test_rounding_ties_to_even_and_carry(m, prec, mantissa, exponent):
    bf = pb.BigFloat(m, 0, prec)
    assert (bf.mantissa, bf.exponent) == (mantissa, exponent)


def test_negative_rounds_by_magnitude():
    """The sign is apart from the magnitude, so -5 at 2 bits is -4, as
    big.Float's ToNearestEven rounds the magnitude."""
    bf = pb.BigFloat(5, 0, 2, True)
    assert bf.to_fraction() == -4


def test_prec_zero_holds_only_a_zero():
    bf = pb.BigFloat(5, 3, 0, True)
    assert (bf.mantissa, bf.exponent, bf.prec, bf.negative) == (0, 0, 0, True)


def test_zero_keeps_prec_and_sign():
    bf = pb.BigFloat(0, 17, 64, True)
    assert (bf.mantissa, bf.exponent, bf.prec, bf.negative) == (0, 0, 64, True)


def test_underflow_below_int32_is_a_zero():
    """big.Float's binary exponent is an int32; below MinInt32 a value
    underflows to a zero of its sign, keeping prec."""
    at = pb.BigFloat(1, INT32_MIN - 1, 53)  # binary exponent MinInt32
    assert (at.mantissa, at.exponent) == (1, INT32_MIN - 1)
    below = pb.BigFloat(1, INT32_MIN - 2, 53, True)  # binary exponent MinInt32 - 1
    assert (below.mantissa, below.exponent, below.prec, below.negative) == (0, 0, 53, True)


def test_overflow_above_int32_is_an_error():
    """Above MaxInt32 big.Float overflows to ±Inf. pb.BigFloat has no infinity,
    so it refuses the value instead (a divergence of representation, not of
    the wire: an infinity has no pxf.BigFloat encoding either)."""
    at = pb.BigFloat(1, INT32_MAX - 1, 53)  # binary exponent MaxInt32
    assert at.exponent == INT32_MAX - 1
    with pytest.raises(ValueError, match="overflows"):
        pb.BigFloat(1, INT32_MAX, 53)


def test_rounding_carry_can_overflow():
    """3 x 2^(MaxInt32-2) has binary exponent MaxInt32; at 1 bit it rounds to
    2^MaxInt32, whose binary exponent is one past the range."""
    pb.BigFloat(3, INT32_MAX - 2, 2)
    with pytest.raises(ValueError, match="overflows"):
        pb.BigFloat(3, INT32_MAX - 2, 1)


def test_rejects_bad_fields():
    with pytest.raises(ValueError):
        pb.BigFloat(-1, 0, 53)
    with pytest.raises(ValueError):
        pb.BigFloat(1, 0, 1 << 32)
    with pytest.raises(TypeError):
        pb.BigFloat(1.5, 0, 53)  # type: ignore[arg-type]
