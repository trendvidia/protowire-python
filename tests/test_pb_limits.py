# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""pb's decode limits (HARDENING.md § Mandatory limits), and the encoder's
refusal of what the decoder would reject.

Ports protowire-go's encoding/pb limits_test.go, limits_options_test.go,
limits_size_test.go, internal/bigprec/bigprec_test.go, and the non-fuzzing
parts of fuzz_test.go.

Go's ``promptly`` runs a decode in a goroutine and fails if it has not
returned within a budget, since a regression in a magnitude bound shows up as
work proportional to the hostile value (minutes to never). Here the call runs
inline and the elapsed time is asserted afterwards: a regression fails the
test late rather than promptly, but it still fails.
"""

from __future__ import annotations

import decimal
import io
import math
import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Annotated, Optional

import pytest

from protowire import pb

MAX_DIGITS = pb.MAX_NUMERIC_LITERAL_DIGITS
MAX_BFE = pb.MAX_BIG_FLOAT_EXPONENT
INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1
UINT32_MAX = (1 << 32) - 1
BIG_MAX_PREC = UINT32_MAX  # Go's big.MaxPrec


# --- Wire helpers (protobuf-go's protowire.Append*) ------------------------


def _varint(out: bytearray, v: int) -> None:
    v &= (1 << 64) - 1  # uint64(int64(x)): a negative is sign-extended
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)


def _tag(out: bytearray, num: int, typ: int) -> None:
    _varint(out, (num << 3) | typ)


def _blob(out: bytearray, b: bytes) -> None:
    _varint(out, len(b))
    out += b


def _field1(sub: bytes) -> bytes:
    out = bytearray()
    _tag(out, 1, 2)
    _blob(out, sub)
    return bytes(out)


def decimal_wire(scale: int, negative: bool) -> bytes:
    """Go's decimalWire: field 1 as a pxf.Decimal of 25 with the given scale,
    by hand, so the decoder sees scales the encoder never writes."""
    sub = bytearray()
    _tag(sub, 1, 2)
    _blob(sub, bytes([25]))
    _tag(sub, 2, 0)
    # A plain int32 varint: sign-extended to 64 bits, as protobuf-go writes it.
    _varint(sub, scale)
    if negative:
        _tag(sub, 3, 0)
        _varint(sub, 1)
    return _field1(bytes(sub))


def bigfloat_wire(mant: bytes, exp: int, prec: int) -> bytes:
    """Go's bigFloatWire: field 1 as a pxf.BigFloat, by hand."""
    sub = bytearray()
    _tag(sub, 1, 2)
    _blob(sub, mant)
    _tag(sub, 2, 0)
    _varint(sub, exp)
    _tag(sub, 3, 0)
    _varint(sub, prec)
    return _field1(bytes(sub))


def promptly(budget: float, f):
    """Go's promptly, as a time check: call f, and fail if it took longer than
    budget seconds. Returns f's exception, or None."""
    start = time.monotonic()
    try:
        f()
        err = None
    except ValueError as e:
        err = e
    elapsed = time.monotonic() - start
    assert elapsed < budget, f"took {elapsed:.2f}s: the bound is not applied before the work it bounds"
    return err


def mant_exp(f: pb.BigFloat) -> int:
    """big.Float.MantExp(nil): the binary exponent, 0 for a zero."""
    return 0 if f.mantissa == 0 else f.exponent + f.mantissa.bit_length()


@dataclass
class DecimalHolder:
    d: Annotated[Optional[decimal.Decimal], pb.Field(1)] = None


@dataclass
class BigFloatHolder:
    f: Annotated[Optional[pb.BigFloat], pb.Field(1)] = None


@dataclass
class BigFloatList:
    f: Annotated[list[pb.BigFloat], pb.Field(1)] = field(default_factory=list)


# --- limits_test.go --------------------------------------------------------


@pytest.mark.parametrize(
    "scale,want",
    [
        (MAX_DIGITS, Fraction(25, 10**MAX_DIGITS)),
        (-MAX_DIGITS, Fraction(25 * 10**MAX_DIGITS)),
        (MAX_DIGITS + 1, None),
        (-MAX_DIGITS - 1, None),
        (INT32_MAX, None),
        (INT32_MIN, None),
    ],
    ids=[
        "at the bound",
        "at the negative bound",
        "one past the bound",
        "one past the negative bound",
        "MaxInt32",
        "MinInt32",
    ],
)
def test_decimal_scale_bound(scale, want):
    """Ports TestDecimalScaleBound (protowire-go#95).

    A Decimal whose scale magnitude exceeds MaxNumericLiteralDigits is
    rejected before 10^|scale| is materialised, on both signs, and one at the
    bound still decodes to its exact value.
    """
    out = {}
    err = promptly(5, lambda: out.setdefault("h", pb.unmarshal(decimal_wire(scale, False), DecimalHolder)))
    if want is None:
        assert err is not None, f"scale {scale}: decoded {out.get('h')}, want an error"
        assert "MaxNumericLiteralDigits" in str(err), f"scale {scale}: error {err!r} does not name the limit"
        return
    assert err is None, f"scale {scale}: {err}"
    assert Fraction(out["h"].d) == want


@pytest.mark.parametrize("exp", [-MAX_BFE - 5, -1, 0, 1, MAX_BFE - 5])
def test_bigfloat_exponent_bound_inside(exp):
    """Ports TestBigFloatExponentBound, the accepted half (protowire#281).

    Storing an exponent is cheap, but rendering the value in decimal costs
    time that grows with the square of |exponent|, so the draft bounds it at
    MaxBigFloatExponent. 25 has 5 bits, so the value's binary exponent is
    exp + 5, and the bound is on that (MaxBigFloatExponent counts the value,
    not the wire field).
    """
    out = {}
    err = promptly(5, lambda: out.setdefault("h", pb.unmarshal(bigfloat_wire(bytes([25]), exp, 64), BigFloatHolder)))
    assert err is None, f"exponent {exp}: {err}"
    # 25 = 0b11001, so v = 25 x 2^exp has MantExp exponent exp+5.
    assert mant_exp(out["h"].f) == exp + 5


@pytest.mark.parametrize("exp", [INT32_MIN, -MAX_BFE - 6, MAX_BFE - 4, INT32_MAX])
def test_bigfloat_exponent_bound_past(exp):
    """Ports TestBigFloatExponentBound, the refused half: past the bound, on
    either side and up to the int32 extremes, decoding fails promptly."""
    err = promptly(5, lambda: pb.unmarshal(bigfloat_wire(bytes([25]), exp, 64), BigFloatHolder))
    assert err is not None and "MaxBigFloatExponent" in str(err), f"exponent {exp}: err = {err!r}"


def test_bigfloat_exponent_bound_per_call():
    """Ports TestBigFloatExponentBound: the bound is a per-call limit, as the
    draft's limits are."""
    with pytest.raises(ValueError, match="MaxBigFloatExponent=10"):
        pb.unmarshal(bigfloat_wire(bytes([25]), 6, 64), BigFloatHolder, max_big_float_exponent=10)
    pb.unmarshal(bigfloat_wire(bytes([25]), 5, 64), BigFloatHolder, max_big_float_exponent=10)


# TestBigFloatExponentBoundEdges covers the states the bound's own test never
# reaches: a mantissa with no bits, with leading zero bytes, wider than its
# precision, a precision of zero, and a bound raised past big.Float's range.


@pytest.mark.parametrize("mant", [b"", b"\x00\x00"])
@pytest.mark.parametrize("exp", [INT32_MIN, INT32_MAX])
def test_bigfloat_edges_zero_mantissa(mant, exp):
    """Ports TestBigFloatExponentBoundEdges/zero mantissa is zero at any exponent."""
    h = pb.unmarshal(bigfloat_wire(mant, exp, 64), BigFloatHolder)
    assert h.f.mantissa == 0


def test_bigfloat_edges_prec_zero():
    """Ports TestBigFloatExponentBoundEdges/prec 0 is zero at any exponent."""
    h = pb.unmarshal(bigfloat_wire(b"\x01", INT32_MIN, 0), BigFloatHolder)
    assert h.f.mantissa == 0


def test_bigfloat_edges_leading_zero_bytes():
    """Ports TestBigFloatExponentBoundEdges/leading zero bytes are not mantissa bits."""
    pb.unmarshal(bigfloat_wire(b"\x00\x00\x01", MAX_BFE - 1, 64), BigFloatHolder)
    with pytest.raises(ValueError, match="MaxBigFloatExponent"):
        pb.unmarshal(bigfloat_wire(b"\x00\x00\x01", MAX_BFE, 64), BigFloatHolder)


def test_bigfloat_edges_raised_bound_refuses_infinity():
    """Ports TestBigFloatExponentBoundEdges/a bound raised past big.Float's range
    still refuses an infinity.

    protowire#278: 25 x 2^(2^31-1) is above big.Float's range. Go raises the
    bound to math.MaxInt (2^63-1 on 64-bit), as here.
    """
    with pytest.raises(ValueError, match="overflows"):
        pb.unmarshal(bigfloat_wire(bytes([25]), INT32_MAX, 64), BigFloatHolder, max_big_float_exponent=(1 << 63) - 1)


def test_bigfloat_edges_rounding_carries_past_bound():
    """Ports TestBigFloatExponentBoundEdges/rounding to prec can carry past the bound.

    Pinned divergence. The bound is on the wire's mantissa and exponent, as
    protowire#281 defines it; SetInt then rounds the mantissa to prec, and
    0xFF at one bit rounds up to 0x100. The decoded value is one past the
    bound, and marshal refuses it. This fails the day the decoder checks the
    rounded value instead.
    """
    h = pb.unmarshal(bigfloat_wire(b"\xff", MAX_BFE - 8, 1), BigFloatHolder)
    assert mant_exp(h.f) == MAX_BFE + 1
    with pytest.raises(ValueError, match="MaxBigFloatExponent"):
        pb.marshal(h)


def test_bigfloat_prec_bound():
    """Ports TestBigFloatPrecBound (protowire#499).

    A BigFloat's prec is bounded as a precision suffix is, by the digits N its
    literal needs: 13603 bits at the default reads, 13604 does not. Marshal
    writes the mantissa widened to prec bits, so an 11-byte message at 2^31-1
    re-encoded to 256 MiB; it is refused before anything is built for it.

    Go also asserts, with runtime.MemStats, that each refusal allocates under
    64 KiB (a widened mantissa at 2^27 bits is 16 MiB). That check is replaced
    by a time bound here: a refusal that built the widened mantissa first
    would take far longer than the budget at 2^31-1 bits.
    """
    h = pb.unmarshal(bigfloat_wire(b"\x19", -3, 13603), BigFloatHolder)
    assert h.f.prec == 13603 and h.f.to_fraction() == Fraction(3125, 1000)
    for prec in [13604, 1 << 27, INT32_MAX, UINT32_MAX]:
        err = promptly(0.5, lambda: pb.unmarshal(bigfloat_wire(b"\x19", -3, prec), BigFloatHolder))
        assert err is not None and "MaxNumericLiteralDigits=4096" in str(err), f"prec {prec}: err = {err!r}"
    # The bound is a per-call limit, through MaxNumericLiteralDigits: 2^29
    # has 9 digits, so N is 10, and 2^30 has 10.
    pb.unmarshal(bigfloat_wire(b"\x19", -3, 29), BigFloatHolder, max_numeric_literal_digits=10)
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits=10"):
        pb.unmarshal(bigfloat_wire(b"\x19", -3, 30), BigFloatHolder, max_numeric_literal_digits=10)
    pb.unmarshal(bigfloat_wire(b"\x19", -3, 13604), BigFloatHolder, max_numeric_literal_digits=5000)


# TestMarshalRefusesWhatUnmarshalRejects: the encoders refuse a value the wire
# cannot carry, instead of writing bytes no conformant decoder accepts (a
# scale past the limit), a wrong exponent (int32 wrap), or panicking (an
# infinity).


def _one_over_two_pow(k: int) -> decimal.Decimal:
    """1/2^k exactly: 5^k x 10^-k, built from a string so no context rounds."""
    return decimal.Decimal(f"{5**k}E-{k}")


def test_marshal_refuses_decimal_scale_past_limit():
    """Ports TestMarshalRefusesWhatUnmarshalRejects/Decimal scale past the limit."""
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits"):
        pb.marshal(DecimalHolder(d=_one_over_two_pow(MAX_DIGITS + 1)))


def test_marshal_decimal_scale_at_limit_round_trips():
    """Ports TestMarshalRefusesWhatUnmarshalRejects/Decimal scale at the limit round-trips."""
    v = _one_over_two_pow(MAX_DIGITS)
    assert Fraction(v) == Fraction(1, 2**MAX_DIGITS)
    out = pb.unmarshal(pb.marshal(DecimalHolder(d=v)), DecimalHolder)
    assert Fraction(out.d) == Fraction(v), "round trip changed the value"


# TestMarshalRefusesWhatUnmarshalRejects/infinite BigFloat is not ported:
# pb.BigFloat has no infinity. A big.Float is ±Inf after an overflow; a
# pb.BigFloat whose binary exponent passes int32 is refused at construction
# instead (test_pb_bigfloat.py::test_overflow_above_int32_is_an_error), so the
# value Go's test marshals cannot be built here.


@pytest.mark.parametrize("prec", [1 << 31, (1 << 31) + 1, BIG_MAX_PREC, (1 << 32) + (1 << 31)])
def test_bigfloat_prec_too_wide_unmarshal(prec):
    """Ports TestMarshalRefusesWhatUnmarshalRejects/BigFloat precision too wide to
    scale, the decode half.

    protowire-go#229's eleven bytes: Unmarshal stored the precision, and
    Marshal used to dereference nil. Unmarshal now refuses the precision
    itself (protowire#499). A varint past uint32 keeps its low 32 bits
    (protowire-go#230), so 1<<32 + 1<<31 is 1<<31 again.
    """
    sub = bytearray()
    _tag(sub, 1, 2)
    _blob(sub, b"\x19")
    _tag(sub, 3, 0)
    _varint(sub, prec)
    wire = _field1(bytes(sub))
    if prec == 1 << 31:
        assert wire == bytes([0x0A, 0x09, 0x0A, 0x01, 0x19, 0x18, 0x80, 0x80, 0x80, 0x80, 0x08]), "not the reported message"
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits"):
        pb.unmarshal(wire, BigFloatHolder(f=pb.BigFloat()))


@pytest.mark.parametrize("prec", [1 << 31, 13604])
def test_bigfloat_prec_too_wide_marshal(prec):
    """Ports TestMarshalRefusesWhatUnmarshalRejects/BigFloat precision too wide to
    scale, the encode half: the same value built in memory, on both signs and
    through a list, and the one past the bound. Marshal refuses what Unmarshal
    would."""
    want = "MaxNumericLiteralDigits=4096"
    neg = pb.BigFloat(3, 0, prec, True)  # SetPrec(prec).SetInt64(-3)
    with pytest.raises(ValueError, match=want):
        pb.marshal(BigFloatHolder(f=neg))
    with pytest.raises(ValueError, match=want):
        pb.marshal(BigFloatList(f=[neg]))


def test_bigfloat_prec_at_bound_round_trips():
    """Ports TestMarshalRefusesWhatUnmarshalRejects/BigFloat precision too wide to
    scale: at the bound the value is written and reads back."""
    at = pb.BigFloat(3, 0, 13603, True)
    back = pb.unmarshal(pb.marshal(BigFloatHolder(f=at)), BigFloatHolder)
    assert back.f.to_fraction() == at.to_fraction() and back.f.prec == 13603


def test_marshal_bigfloat_exponent_below_int32_after_adjustment():
    """Ports TestMarshalRefusesWhatUnmarshalRejects/BigFloat exponent below int32
    after adjustment.

    exp - prec leaves int32: 0.75 x 2^(MinInt32+3) with 53 bits of precision
    has wire exponent MinInt32+3-53. (0.75 x 2^e is 3 x 2^(e-2).)
    """
    f = pb.BigFloat(3, INT32_MIN + 3 - 2, 53)
    with pytest.raises(ValueError, match="does not fit int32"):
        pb.marshal(BigFloatHolder(f=f))


def test_marshal_bigfloat_exponent_past_max():
    """Ports TestMarshalRefusesWhatUnmarshalRejects/BigFloat exponent past
    MaxBigFloatExponent. 0.75 x 2^k has binary exponent k."""
    f = pb.BigFloat(3, -MAX_BFE - 1 - 2, 53)
    assert mant_exp(f) == -MAX_BFE - 1
    with pytest.raises(ValueError, match="MaxBigFloatExponent"):
        pb.marshal(BigFloatHolder(f=f))


def test_marshal_bigfloat_exponent_at_max_round_trips():
    """Ports TestMarshalRefusesWhatUnmarshalRejects/BigFloat exponent at
    MaxBigFloatExponent round-trips."""
    f = pb.BigFloat(3, -MAX_BFE - 2, 53)
    out = pb.unmarshal(pb.marshal(BigFloatHolder(f=f)), BigFloatHolder(f=pb.BigFloat()))
    assert out.f.to_fraction() == f.to_fraction(), f"round trip changed the value: {f} -> {out.f}"


# --- limits_options_test.go ------------------------------------------------


@dataclass
class NestHolder:
    """Go's nestHolder: nests itself, so nest_to(n) is n structs deep."""

    child: Annotated[Optional[NestHolder], pb.Field(1)] = None
    v: Annotated[int, pb.Field(2, pb.INT32)] = 0
    tags: Annotated[dict[str, str], pb.Field(3)] = field(default_factory=dict)


def nest_to(depth: int) -> NestHolder:
    root = NestHolder(v=1)
    cur = root
    for i in range(1, depth):
        cur.child = NestHolder(v=i + 1)
        cur = cur.child
    return root


def test_unmarshal_options_max_nesting_depth():
    """Ports TestUnmarshalOptionsLimits/MaxNestingDepth (protowire-go#101).

    The per-call limits default to the package constants at 0, and a lowered
    value rejects input the default accepts.
    """
    data = pb.marshal(nest_to(3))
    pb.unmarshal(data, NestHolder())  # default
    pb.unmarshal(data, NestHolder(), max_nesting_depth=0)  # zero options are the default
    # nest_to(3) is the root and two descents; the root is depth 0.
    pb.unmarshal(data, NestHolder(), max_nesting_depth=2)  # at the bound
    with pytest.raises(ValueError, match="MaxNestingDepth=1") as ei:
        pb.unmarshal(data, NestHolder(), max_nesting_depth=1)
    # The error names the effective limit, not the constant.
    assert "MaxNestingDepth=100" not in str(ei.value)


def test_unmarshal_options_max_numeric_literal_digits():
    """Ports TestUnmarshalOptionsLimits/MaxNumericLiteralDigits."""
    wire = decimal_wire(10, False)
    pb.unmarshal(wire, DecimalHolder)  # default
    pb.unmarshal(wire, DecimalHolder, max_numeric_literal_digits=10)  # at the bound
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits=9"):
        pb.unmarshal(wire, DecimalHolder, max_numeric_literal_digits=9)
    # Both signs of the scale are bounded by the call's value.
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits=9"):
        pb.unmarshal(decimal_wire(-10, False), DecimalHolder, max_numeric_literal_digits=9)


def test_unmarshal_options_stream_decoder():
    """Ports TestUnmarshalOptionsLimits/stream decoder: the length-prefixed stream
    decoder takes the same limits."""
    buf = io.BytesIO()
    pb.Encoder(buf).encode(nest_to(3))
    framed = buf.getvalue()
    assert pb.Decoder(io.BytesIO(framed)).decode(NestHolder()) is not None  # default
    assert pb.Decoder(io.BytesIO(framed), max_nesting_depth=0).decode(NestHolder()) is not None  # zero options
    with pytest.raises(ValueError, match="MaxNestingDepth=1"):
        pb.Decoder(io.BytesIO(framed), max_nesting_depth=1).decode(NestHolder())


@pytest.mark.parametrize("depth,ok", [(pb.MAX_NESTING_DEPTH + 1, True), (pb.MAX_NESTING_DEPTH + 2, False)])
def test_nesting_depth_boundary(depth, ok):
    """Ports TestNestingDepthBoundary (protowire-go#111).

    The root is depth 0 and every submessage is one descent (HARDENING.md
    § Recursion), so MaxNestingDepth submessages under the root (the corpus's
    deep-submessage-100) is the last accepted shape.
    """
    data = pb.marshal(nest_to(depth), max_nesting_depth=1000)
    if ok:
        pb.unmarshal(data, NestHolder())
    else:
        with pytest.raises(ValueError, match="MaxNestingDepth=100"):
            pb.unmarshal(data, NestHolder())


@pytest.mark.parametrize("children,ok", [(pb.MAX_NESTING_DEPTH - 1, True), (pb.MAX_NESTING_DEPTH, False)])
def test_nesting_depth_boundary_scalar_map_entry(children, ok):
    """Ports TestNestingDepthBoundaryScalarMapEntry (protowire#449).

    A map entry is a descent whatever its value's type, so a scalar-valued
    entry just past the bound is refused.
    """
    msg = NestHolder(tags={"k": "v"})
    for _ in range(children):
        msg = NestHolder(child=msg)
    data = pb.marshal(msg, max_nesting_depth=1000)
    if ok:
        pb.unmarshal(data, NestHolder())
    else:
        with pytest.raises(ValueError, match="MaxNestingDepth=100"):
            pb.unmarshal(data, NestHolder())


# --- limits_size_test.go ---------------------------------------------------


@dataclass
class BlobHolder:
    b: Annotated[bytes, pb.Field(1)] = b""


@dataclass
class ListHolder:
    xs: Annotated[list[int], pb.Field(1, pb.INT32)] = field(default_factory=list)
    m: Annotated[dict[str, int], pb.Field(2, pb.INT32)] = field(default_factory=dict)
    ss: Annotated[list[str], pb.Field(3)] = field(default_factory=list)


def test_max_message_size_default_rejects():
    """Ports TestMaxMessageSize/default rejects 64 MiB + tag (protowire-go#112).

    The input to one decode call is capped at MaxMessageSize before anything
    is read.
    """
    data = pb.marshal(BlobHolder(b=bytes(pb.MAX_MESSAGE_SIZE)))
    assert len(data) > pb.MAX_MESSAGE_SIZE, "tag and length push it past the cap"
    with pytest.raises(ValueError, match="MaxMessageSize=67108864"):
        pb.unmarshal(data, BlobHolder)


def test_max_message_size_default_accepts():
    """Ports TestMaxMessageSize/default accepts 64 MiB."""
    data = pb.marshal(BlobHolder(b=bytes(pb.MAX_MESSAGE_SIZE - 16)))
    assert len(data) <= pb.MAX_MESSAGE_SIZE
    pb.unmarshal(data, BlobHolder)


def test_max_message_size_lowered_per_call():
    """Ports TestMaxMessageSize/lowered per call, on unmarshal and on the stream
    decoder alike."""
    data = pb.marshal(BlobHolder(b=bytes(32)))
    pb.unmarshal(data, BlobHolder)  # default
    pb.unmarshal(data, BlobHolder, max_message_size=len(data))  # at the bound
    with pytest.raises(ValueError, match="MaxMessageSize=16"):
        pb.unmarshal(data, BlobHolder, max_message_size=16)

    # A frame inside the stream's frame cap, payload past the call's cap.
    buf = io.BytesIO()
    pb.Encoder(buf).encode(BlobHolder(b=bytes(32)))
    assert pb.Decoder(io.BytesIO(buf.getvalue())).decode(BlobHolder) is not None  # default
    with pytest.raises(ValueError, match="MaxMessageSize=16"):
        pb.Decoder(io.BytesIO(buf.getvalue()), max_message_size=16).decode(BlobHolder)


def test_max_repeated_count():
    """Ports TestMaxRepeatedCount (protowire-go#112).

    The element count of a repeated or map field is capped at
    MaxRepeatedCount, lowered per call, on packed and unpacked elements and
    on map entries.
    """
    data = pb.marshal(ListHolder(xs=[1, 2, 3, 4, 5], m={"a": 1, "b": 2, "c": 3}, ss=["a", "b", "c"]))
    pb.unmarshal(data, ListHolder)  # default
    pb.unmarshal(data, ListHolder, max_repeated_count=5)  # at the bound
    with pytest.raises(ValueError, match="MaxRepeatedCount=4"):  # five packed int32s
        pb.unmarshal(data, ListHolder, max_repeated_count=4)
    with pytest.raises(ValueError, match="MaxRepeatedCount=2"):
        pb.unmarshal(data, ListHolder, max_repeated_count=2)

    # Unpacked strings and map entries hit the same bound.
    data = pb.marshal(ListHolder(ss=["a", "b", "c"]))
    with pytest.raises(ValueError, match="MaxRepeatedCount=2"):
        pb.unmarshal(data, ListHolder, max_repeated_count=2)
    data = pb.marshal(ListHolder(m={"a": 1, "b": 2, "c": 3}))
    with pytest.raises(ValueError, match="MaxRepeatedCount=2"):
        pb.unmarshal(data, ListHolder, max_repeated_count=2)
    pb.unmarshal(data, ListHolder, max_repeated_count=3)  # three entries at the bound


# --- internal/bigprec/bigprec_test.go --------------------------------------


def test_pow2_digits():
    """Ports bigprec's TestPow2Digits.

    _pow2_digits counts the decimal digits of 2^p without computing 2^p. It is
    checked against the count of the power itself for every p up to 2^15,
    which takes in the default limit's bound, p13603, with room. The count is
    kept as Go keeps it, by tracking the smallest power of ten above 2^p,
    rather than as len(str(2**p)): that is the same number, and str() past
    4300 digits is refused by Python's int-to-str limit and quadratic below
    it. The p <= 2^12 prefix is also checked with len(str(2**p)) directly.

    Past that the exact counts are pinned from an independent computation
    (log10(2) to 2000 bits, by series), at the three p below 2^32 where
    float64's p*log10(2) is one digit high, which is why the helper does not
    use it.
    """
    pow2, nxt, want = 1, 10, 1
    for p in range(0, (1 << 15) + 1):
        if p > 0:
            pow2 <<= 1
            if pow2 >= nxt:
                want += 1
                nxt *= 10
        assert pb._pow2_digits(p) == want, f"_pow2_digits({p})"
        if p <= 1 << 12:
            assert want == len(str(pow2)), f"p {p}: the incremental count disagrees with str()"
    for p, want in [(146964308, 44240665), (345060773, 103873643), (1923400330, 579001193)]:
        assert pb._pow2_digits(p) == want, f"_pow2_digits({p})"
        f = int(p * math.log10(2)) + 1
        assert f != want, f"p {p}: float64 gives {f} too; the reason for fixed point no longer holds here"
    # The draft's form of the bound: N exceeds L exactly when prec exceeds
    # floor((L-1)/log10(2)), 13603 at the default of 4096. (Go's Digits(prec,
    # limit) is (_prec_digits(prec), _prec_digits(prec) <= limit).)
    assert pb._prec_digits(13603) <= 4096, "p13603 is within the default limit"
    assert pb._prec_digits(13604) > 4096, "p13604 is past the default limit"
    assert pb._prec_digits(UINT32_MAX) == 1292913988


# --- fuzz_test.go ----------------------------------------------------------


@dataclass
class FuzzInner:
    name: Annotated[str, pb.Field(1)] = ""
    n: Annotated[int, pb.Field(2, pb.INT32)] = 0


@dataclass
class FuzzTarget:
    """Go's fuzzTarget: scalars, nested message, repeated message, byte slice,
    zigzag."""

    s: Annotated[str, pb.Field(1)] = ""
    i: Annotated[int, pb.Field(2)] = 0
    u: Annotated[int, pb.Field(3, pb.UINT32)] = 0
    b: Annotated[bool, pb.Field(4)] = False
    f32: Annotated[float, pb.Field(5, pb.FLOAT)] = 0.0
    f64: Annotated[float, pb.Field(6)] = 0.0
    z: Annotated[int, pb.Field(7, pb.SINT64)] = 0
    buf: Annotated[bytes, pb.Field(8)] = b""
    inner: Annotated[Optional[FuzzInner], pb.Field(9)] = None
    list_: Annotated[list[Optional[FuzzInner]], pb.Field(10)] = field(default_factory=list)
    nums: Annotated[list[int], pb.Field(11, pb.UINT32)] = field(default_factory=list)  # packed repeated scalar
    zigs: Annotated[list[int], pb.Field(12, pb.SINT64)] = field(default_factory=list)
    reals: Annotated[list[float], pb.Field(13, pb.FLOAT)] = field(default_factory=list)
    rat: Annotated[Optional[decimal.Decimal], pb.Field(14)] = None
    bf: Annotated[Optional[pb.BigFloat], pb.Field(15)] = None


def hostile_decimal(scale: int) -> bytes:
    """Go's hostileDecimal: field 14 as a Decimal of 25 at the given scale."""
    sub = bytearray()
    _tag(sub, 1, 2)
    _blob(sub, bytes([25]))
    _tag(sub, 2, 0)
    _varint(sub, scale)
    out = bytearray()
    _tag(out, 14, 2)
    _blob(out, bytes(sub))
    return bytes(out)


@pytest.mark.parametrize("scale", [INT32_MAX, INT32_MIN])
def test_hostile_decimal_seeds_reach_the_bound(scale):
    """Ports TestHostileDecimalSeedsReachTheBound.

    The positive marker that the hostile seeds exercise the arm they are for:
    each must fail on the limit, not on a corrupt tag or a silent skip.
    """
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits"):
        pb.unmarshal(hostile_decimal(scale), FuzzTarget)


_FUZZ_SEEDS = [
    FuzzTarget(s="hello", i=42, u=7, b=True, f32=1.5, f64=2.5),
    FuzzTarget(z=-100, buf=bytes([0x00, 0xFF, 0x10])),
    FuzzTarget(inner=FuzzInner(name="x", n=-1)),
    FuzzTarget(list_=[FuzzInner(name="a", n=1), FuzzInner(name="b", n=2)]),
    FuzzTarget(nums=[1, 0, 300], zigs=[-1, 0, 2], reals=[1.5, 0]),
    FuzzTarget(),
]


def _fuzz_corpus() -> list[bytes]:
    corpus = [pb.marshal(s) for s in _FUZZ_SEEDS]
    corpus += [
        b"",
        b"\xff",
        b"\x08",  # tag-only, truncated varint
        bytes([0x0A, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x7F]),  # length-delim with absurd length
        # A Decimal whose scale is 2^31-1 and one at -2^31, so the corpus
        # reaches the bound (protowire-go#95).
        hostile_decimal(INT32_MAX),
        hostile_decimal(INT32_MIN),
    ]
    return corpus


@pytest.mark.parametrize("data", _fuzz_corpus())
def test_unmarshal_seed_corpus(data):
    """Ports FuzzUnmarshal's seed corpus, which `go test` runs without -fuzz.

    Any out-of-bounds read, infinite loop or crash is a bug: unmarshal must
    return or raise ValueError on malformed input. The fuzzing itself (Go's
    coverage-guided mutation of these seeds) is not ported: Python has no
    equivalent in this suite.
    """
    promptly(5, lambda: pb.unmarshal(data, FuzzTarget))
