# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Protobuf binary for plain Python classes — a port of protowire-go's encoding/pb.

No ``.proto`` and no generated code: a dataclass declares each field's number
(and, where the Python type does not say it, the protobuf type) with
``typing.Annotated``::

    from dataclasses import dataclass, field
    from typing import Annotated, Optional
    from protowire import pb

    @dataclass
    class Endpoint:
        path: Annotated[str, pb.Field(1)] = ""
        port: Annotated[int, pb.Field(3, pb.INT32)] = 0
        delta: Annotated[int, pb.Field(4, pb.SINT64)] = 0   # Go's `protowire:"4,zigzag"`
        tags: Annotated[list[str], pb.Field(5)] = field(default_factory=list)
        inner: Annotated[Optional[Inner], pb.Field(6)] = None

    data = pb.marshal(Endpoint(path="/x", port=8080))
    back = pb.unmarshal(data, Endpoint)

The bytes are protowire-go's, byte for byte, and follow proto3:

- A zero singular field is omitted. ``None`` is an absent field, as Go's nil
  pointer is; a value under ``Optional`` follows the same zero rule.
- A nested dataclass field that is not ``Optional`` is always written, as a Go
  struct value is.
- Every element of a list and both halves of a map entry are written, zeros
  included. A list of numbers (``int``, ``float``, ``bool``) is packed;
  ``unmarshal`` reads packed and unpacked.
- A field is read by its declared type, whatever wire type the record carries,
  as Go reads it.
- A ``str`` must be UTF-8 on the wire; declare ``bytes`` for a bytes field.
- ``unmarshal`` into an existing object merges, as Go's does: scalars are
  replaced, lists are appended to, maps gain entries, and nested messages are
  merged into.

Types and their protobuf types:

==================================  ==========================================
Python                              protobuf
==================================  ==========================================
``int``                             int64; or INT32, UINT32, UINT64, SINT32,
                                    SINT64, or BIGINT (``pxf.BigInt``)
``float``                           double; or FLOAT
``bool``, ``str``, ``bytes``        bool, string, bytes
``decimal.Decimal``                 ``pxf.Decimal``
``pb.BigFloat``                     ``pxf.BigFloat``
a dataclass                         a message
``list[T]``                         repeated T
``dict[K, V]``                      map<K, V>
``Optional[T]``                     T, with ``None`` for absent
==================================  ==========================================

A protobuf type given in ``pb.Field`` applies to a list's elements or a map's
values. A map key that is not int64 says so on the key type:
``dict[Annotated[int, pb.UINT32], str]``. An ``int`` outside its declared
type's range is a ``ValueError`` from ``marshal``.

Decoding is bounded by protowire's mandatory limits (HARDENING.md), each a
keyword argument of ``unmarshal`` and ``Decoder`` named after the Go option,
with 0 for the default. ``marshal`` refuses a value nested past
``MAX_NESTING_DEPTH``, since ``unmarshal`` would refuse it.

Not ported: Go's ``UnmarshalOptions.Validator``, since this package has no
validation engine. A value nested past Python's recursion limit is a
``ValueError`` here; Go has no such ceiling, so a ``max_nesting_depth`` raised
past a few hundred needs ``sys.setrecursionlimit`` raised with it.
"""

from __future__ import annotations

import dataclasses
import decimal
import math
import struct
import sys
import types
import typing
from dataclasses import dataclass
from fractions import Fraction
from typing import Annotated, Any, BinaryIO, Optional, TypeVar, Union

__all__ = [
    "BIGINT",
    "DEFAULT_MAX_FRAME_SIZE",
    "FLOAT",
    "INT32",
    "INT64",
    "MAX_BIG_FLOAT_EXPONENT",
    "MAX_MESSAGE_SIZE",
    "MAX_NESTING_DEPTH",
    "MAX_NUMERIC_LITERAL_DIGITS",
    "MAX_REPEATED_COUNT",
    "SINT32",
    "SINT64",
    "UINT32",
    "UINT64",
    "DOUBLE",
    "BigFloat",
    "Decoder",
    "Encoder",
    "Field",
    "FramingError",
    "Kind",
    "marshal",
    "unmarshal",
]

# --- Limits (protowire/docs/HARDENING.md § Mandatory limits) ---------------

#: Submessage / map-entry recursion; the root is depth 0.
MAX_NESTING_DEPTH = 100
#: Bounds |Decimal.scale| and the digits a BigFloat's prec implies.
MAX_NUMERIC_LITERAL_DIGITS = 4096
#: Bounds |exponent + mantissa bit length| of a BigFloat.
MAX_BIG_FLOAT_EXPONENT = 16384
#: Caps the input to one decode, checked before anything is read.
MAX_MESSAGE_SIZE = 64 << 20
#: Caps the element count of any repeated or map field.
MAX_REPEATED_COUNT = MAX_MESSAGE_SIZE
#: The stream decoder's default frame cap.
DEFAULT_MAX_FRAME_SIZE = 16 << 20

_INT32_MIN, _INT32_MAX = -(1 << 31), (1 << 31) - 1
_INT64_MIN, _INT64_MAX = -(1 << 63), (1 << 63) - 1
_UINT32_MAX, _UINT64_MAX = (1 << 32) - 1, (1 << 64) - 1
_MAX_FIELD_NUMBER = (1 << 29) - 1
# protobuf-go's protowire.DefaultRecursionLimit, which bounds the groups an
# unknown field may nest.
_GROUP_RECURSION_LIMIT = 10000

_VARINT, _FIXED64, _BYTES, _START_GROUP, _END_GROUP, _FIXED32 = 0, 1, 2, 3, 4, 5


# --- Declarations ----------------------------------------------------------


@dataclass(frozen=True)
class Kind:
    """A protobuf type that a Python type alone does not name."""

    name: str

    def __repr__(self) -> str:
        return f"pb.{self.name.upper()}"


INT32 = Kind("int32")
INT64 = Kind("int64")
UINT32 = Kind("uint32")
UINT64 = Kind("uint64")
SINT32 = Kind("sint32")
SINT64 = Kind("sint64")
BIGINT = Kind("bigint")
FLOAT = Kind("float")
DOUBLE = Kind("double")

_INT_KINDS = {INT32, INT64, UINT32, UINT64, SINT32, SINT64, BIGINT}
_FLOAT_KINDS = {FLOAT, DOUBLE}


@dataclass(frozen=True)
class Field:
    """The field number, and optionally the protobuf type, of a dataclass field.

    Used as ``Annotated[T, pb.Field(number)]`` or
    ``Annotated[T, pb.Field(number, pb.SINT64)]``.
    """

    number: int
    kind: Optional[Kind] = None


@dataclass(frozen=True, eq=True)
class BigFloat:
    """A ``pxf.BigFloat``: ``(-1)**negative * mantissa * 2**exponent`` at ``prec`` bits.

    It holds a value as protowire-go's ``big.Float`` holds it, and does no
    arithmetic. The fields are normalised on construction:

    - ``mantissa`` is rounded to ``prec`` bits (to nearest, ties to even), then
      reduced to an odd number, with ``exponent`` adjusted to keep the value;
    - a zero is ``mantissa=0, exponent=0`` and keeps its ``prec`` and sign;
    - ``prec=0`` holds only a signed zero;
    - a value whose binary exponent (``exponent + mantissa.bit_length()``)
      leaves the int32 range is an error above it and a zero below it, as for
      ``big.Float``.

    ``marshal`` writes the mantissa widened to ``prec`` bits, as Go does.
    """

    mantissa: int = 0
    exponent: int = 0
    prec: int = 0
    negative: bool = False

    def __post_init__(self) -> None:
        m, e, p = self.mantissa, self.exponent, self.prec
        if not isinstance(m, int) or not isinstance(e, int) or not isinstance(p, int):
            raise TypeError("BigFloat mantissa, exponent and prec are ints")
        if m < 0:
            raise ValueError("BigFloat mantissa is a magnitude; the sign is `negative`")
        if not 0 <= p <= _UINT32_MAX:
            raise ValueError(f"BigFloat prec {p} is outside uint32")
        if p == 0 or m == 0:
            m, e = 0, 0
        else:
            m, e = _round_to_prec(m, e, p)
            binexp = e + m.bit_length()
            if binexp > _INT32_MAX:
                raise ValueError(f"BigFloat binary exponent {binexp} overflows big.Float")
            if binexp < _INT32_MIN:
                m, e = 0, 0  # big.Float underflows to a signed zero
            else:
                tz = (m & -m).bit_length() - 1
                m, e = m >> tz, e + tz
        object.__setattr__(self, "mantissa", m)
        object.__setattr__(self, "exponent", e)
        object.__setattr__(self, "negative", bool(self.negative))

    def to_fraction(self) -> Fraction:
        """The exact value. A signed zero is ``Fraction(0)``."""
        v = Fraction(self.mantissa) * (Fraction(2) ** self.exponent)
        return -v if self.negative else v


def _round_to_prec(m: int, e: int, prec: int) -> tuple[int, int]:
    """m * 2**e rounded to prec bits, ties to even, as big.Float.SetInt rounds."""
    n = m.bit_length()
    if n <= prec:
        return m, e
    shift = n - prec
    q = m >> shift
    r = m & ((1 << shift) - 1)
    half = 1 << (shift - 1)
    if r > half or (r == half and q & 1):
        q += 1
        if q.bit_length() > prec:
            q >>= 1
            shift += 1
    return q, e + shift


# --- Schema ----------------------------------------------------------------

# A resolved type is a tuple whose first element says what it is:
#   ("scalar", kind)       kind: a Kind, or "bool", "string", "bytes",
#                          "decimal", "bigfloat"
#   ("message", cls)
#   ("optional", T)
#   ("list", T)
#   ("map", K, V)
_T = tuple


@dataclass
class _FieldInfo:
    name: str
    number: int
    type: _T


@dataclass
class _StructInfo:
    cls: type
    fields: list[_FieldInfo]
    by_number: dict[int, _FieldInfo]


_struct_cache: dict[type, _StructInfo] = {}

_NONE_TYPE = type(None)


def _is_union(tp: Any) -> bool:
    return typing.get_origin(tp) is Union or isinstance(tp, types.UnionType)


def _split_optional(tp: Any) -> tuple[Any, bool]:
    if _is_union(tp):
        args = [a for a in typing.get_args(tp) if a is not _NONE_TYPE]
        if len(args) == 1 and len(typing.get_args(tp)) == 2:
            return args[0], True
        raise TypeError(f"pb: unsupported union {tp!r}; only Optional[T] is")
    return tp, False


def _annotated(tp: Any) -> tuple[Any, tuple]:
    if typing.get_origin(tp) is Annotated:
        return tp.__origin__, tp.__metadata__
    return tp, ()


def _find_field(tp: Any) -> tuple[Any, Optional[Field]]:
    """The type and the pb.Field of a dataclass field's annotation, if any.

    Python 3.10's get_type_hints wraps a None-defaulted annotation in
    Optional, Annotated and all, so the Field is looked for there too.
    """
    inner, meta = _annotated(tp)
    fields = [m for m in meta if isinstance(m, Field)]
    if fields:
        return inner, fields[-1]
    if _is_union(tp):
        args = typing.get_args(tp)
        if len(args) == 2 and _NONE_TYPE in args:
            other = args[0] if args[1] is _NONE_TYPE else args[1]
            inner, meta = _annotated(other)
            fields = [m for m in meta if isinstance(m, Field)]
            if fields:
                return Optional[inner], fields[-1]
    return tp, None


def _resolve(tp: Any, kind: Optional[Kind], where: str, *, top: bool = True) -> _T:
    tp, meta = _annotated(tp)
    for m in meta:
        if isinstance(m, Kind):
            kind = m
    tp, optional = _split_optional(tp)
    if optional:
        return ("optional", _resolve(tp, kind, where, top=top))
    origin = typing.get_origin(tp)
    if origin is list:
        if not top:
            raise TypeError(f"pb: {where}: a list inside a list or map is not supported")
        (elem,) = typing.get_args(tp) or (None,)
        if elem is None:
            raise TypeError(f"pb: {where}: list needs an element type")
        return ("list", _resolve(elem, kind, where, top=False))
    if origin is dict:
        if not top:
            raise TypeError(f"pb: {where}: a map inside a list or map is not supported")
        args = typing.get_args(tp)
        if len(args) != 2:
            raise TypeError(f"pb: {where}: dict needs key and value types")
        key = _resolve(args[0], None, where, top=False)
        k = key[1] if key[0] == "scalar" else None
        if key[0] != "scalar" or k in ("bytes", "decimal", "bigfloat", BIGINT, FLOAT, DOUBLE):
            raise TypeError(f"pb: {where}: a map key must be an int, bool or str")
        return ("map", key, _resolve(args[1], kind, where, top=False))
    if tp is bool:
        _no_kind(kind, tp, where)
        return ("scalar", "bool")
    if tp is int:
        if kind is None:
            kind = INT64
        if kind not in _INT_KINDS:
            raise TypeError(f"pb: {where}: {kind!r} is not an int type")
        return ("scalar", kind)
    if tp is float:
        if kind is None:
            kind = DOUBLE
        if kind not in _FLOAT_KINDS:
            raise TypeError(f"pb: {where}: {kind!r} is not a float type")
        return ("scalar", kind)
    if tp is str:
        _no_kind(kind, tp, where)
        return ("scalar", "string")
    if tp is bytes:
        _no_kind(kind, tp, where)
        return ("scalar", "bytes")
    if tp is decimal.Decimal:
        _no_kind(kind, tp, where)
        return ("scalar", "decimal")
    if tp is BigFloat:
        _no_kind(kind, tp, where)
        return ("scalar", "bigfloat")
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        _no_kind(kind, tp, where)
        return ("message", tp)
    raise TypeError(f"pb: {where}: unsupported type {tp!r}")


def _no_kind(kind: Optional[Kind], tp: Any, where: str) -> None:
    if kind is not None:
        raise TypeError(f"pb: {where}: {kind!r} does not apply to {tp.__name__}")


def _struct_info(cls: type) -> _StructInfo:
    info = _struct_cache.get(cls)
    if info is not None:
        return info
    if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
        raise TypeError(f"pb: expected a dataclass, got {cls!r}")
    hints = typing.get_type_hints(cls, include_extras=True)
    fields: list[_FieldInfo] = []
    by_number: dict[int, _FieldInfo] = {}
    for f in dataclasses.fields(cls):
        tp, pf = _find_field(hints.get(f.name, f.type))
        if pf is None:
            continue
        where = f"{cls.__name__}.{f.name}"
        if not isinstance(pf.number, int) or not 1 <= pf.number <= _MAX_FIELD_NUMBER:
            raise TypeError(f"pb: {where}: field number {pf.number!r} is outside 1..{_MAX_FIELD_NUMBER}")
        if pf.number in by_number:
            raise TypeError(f"pb: {where}: field number {pf.number} is also {by_number[pf.number].name}")
        fi = _FieldInfo(f.name, pf.number, _resolve(tp, pf.kind, where))
        fields.append(fi)
        by_number[pf.number] = fi
    info = _StructInfo(cls, fields, by_number)
    _struct_cache[cls] = info
    return info


def _zero(t: _T) -> Any:
    tag = t[0]
    if tag == "optional":
        return None
    if tag == "list":
        return []
    if tag == "map":
        return {}
    if tag == "message":
        return _new(t[1])
    k = t[1]
    if k == "bool":
        return False
    if k == "string":
        return ""
    if k == "bytes":
        return b""
    if k == "decimal":
        return decimal.Decimal(0)
    if k == "bigfloat":
        return BigFloat()
    if k in _FLOAT_KINDS:
        return 0.0
    return 0


def _new(cls: type) -> Any:
    """A zero instance: every pb field without a default gets its type's zero."""
    info = _struct_info(cls)
    pbtypes = {fi.name: fi.type for fi in info.fields}
    kwargs = {}
    for f in dataclasses.fields(cls):
        if not f.init:
            continue
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
            continue
        t = pbtypes.get(f.name)
        kwargs[f.name] = _zero(t) if t is not None else None
    return cls(**kwargs)


# --- Encoding --------------------------------------------------------------


def _put_varint(out: bytearray, v: int) -> None:
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)


def _put_tag(out: bytearray, num: int, typ: int) -> None:
    _put_varint(out, (num << 3) | typ)


def _put_bytes(out: bytearray, num: int, b: Union[bytes, bytearray]) -> None:
    _put_tag(out, num, _BYTES)
    _put_varint(out, len(b))
    out += b


def _depth_err(limit: int) -> ValueError:
    return ValueError(f"nesting depth exceeds MaxNestingDepth={limit}")


def marshal(obj: Any, *, max_nesting_depth: int = 0) -> bytes:
    """Encode a dataclass instance as protobuf binary.

    A value nested deeper than ``max_nesting_depth`` (0: ``MAX_NESTING_DEPTH``)
    is an error, since ``unmarshal`` would refuse it.
    """
    limit = max_nesting_depth if max_nesting_depth > 0 else MAX_NESTING_DEPTH
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        raise TypeError(f"pb.marshal: expected a dataclass instance, got {type(obj).__name__}")
    try:
        return bytes(_marshal_struct(obj, 0, limit))
    except RecursionError:
        raise ValueError(_recursion_msg()) from None


def _recursion_msg() -> str:
    return (
        f"message nests deeper than Python's recursion limit ({sys.getrecursionlimit()}) "
        "allows; raise sys.setrecursionlimit"
    )


def _marshal_struct(obj: Any, depth: int, limit: int) -> bytearray:
    if depth > limit:
        raise _depth_err(limit)
    info = _struct_info(type(obj))
    out = bytearray()
    for fi in info.fields:
        try:
            _marshal_field(out, fi.number, fi.type, getattr(obj, fi.name), False, depth, limit)
        except ValueError as e:
            raise ValueError(f"field {fi.name}: {e}") from None
        except TypeError as e:
            raise TypeError(f"field {fi.name}: {e}") from None
    return out


def _is_packed(t: _T) -> bool:
    return t[0] == "scalar" and (t[1] == "bool" or t[1] in _FLOAT_KINDS or (t[1] in _INT_KINDS and t[1] is not BIGINT))


def _marshal_field(out: bytearray, num: int, t: _T, v: Any, element: bool, depth: int, limit: int) -> None:
    # Optional is Go's pointer: None is absent as a singular field, and an
    # element written as its type's zero so the list keeps its length.
    if t[0] == "optional":
        t = t[1]
        if v is None:
            if not element:
                return
            v = _zero(t)
    elif v is None:
        if element:
            raise TypeError("None element in a list or map not declared Optional")
        return
    tag = t[0]
    if tag == "list":
        if not isinstance(v, (list, tuple)):
            raise TypeError(f"expected a list, got {type(v).__name__}")
        if not v:
            return
        et = t[1]
        if _is_packed(et):
            payload = bytearray()
            for e in v:
                _put_scalar_value(payload, et[1], e)
            _put_bytes(out, num, payload)
            return
        for e in v:
            _marshal_field(out, num, et, e, True, depth, limit)
        return
    if tag == "map":
        if not isinstance(v, dict):
            raise TypeError(f"expected a dict, got {type(v).__name__}")
        if not v:
            return
        if depth + 1 > limit:
            raise _depth_err(limit)
        for k, val in v.items():
            entry = bytearray()
            try:
                _marshal_field(entry, 1, t[1], k, True, depth + 1, limit)
            except ValueError as e:
                raise ValueError(f"map key: {e}") from None
            try:
                _marshal_field(entry, 2, t[2], val, True, depth + 1, limit)
            except ValueError as e:
                raise ValueError(f"map value: {e}") from None
            _put_bytes(out, num, entry)
        return
    if tag == "message":
        if not isinstance(v, t[1]):
            raise TypeError(f"expected {t[1].__name__}, got {type(v).__name__}")
        _put_bytes(out, num, _marshal_struct(v, depth + 1, limit))
        return
    _marshal_scalar(out, num, t[1], v, element)


def _check_int(k: Kind, v: Any) -> int:
    if not isinstance(v, int):
        raise TypeError(f"expected an int, got {type(v).__name__}")
    v = int(v)
    if k is INT32 or k is SINT32:
        lo, hi = _INT32_MIN, _INT32_MAX
    elif k is INT64 or k is SINT64:
        lo, hi = _INT64_MIN, _INT64_MAX
    elif k is UINT32:
        lo, hi = 0, _UINT32_MAX
    elif k is UINT64:
        lo, hi = 0, _UINT64_MAX
    else:
        return v
    if not lo <= v <= hi:
        raise ValueError(f"{v} is outside {k.name}")
    return v


def _float_bits(k: Kind, v: Any) -> bytes:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise TypeError(f"expected a float, got {type(v).__name__}")
    v = float(v)
    # A NaN is written as the canonical quiet NaN whatever its payload, as Go
    # writes it (draft -01 § Numeric Literals; protowire-java#161).
    if k is DOUBLE:
        return b"\x00\x00\x00\x00\x00\x00\xf8\x7f" if math.isnan(v) else struct.pack("<d", v)
    if math.isnan(v):
        return b"\x00\x00\xc0\x7f"
    try:
        return struct.pack("<f", v)
    except OverflowError:
        raise ValueError(f"{v!r} is outside float") from None


def _varint_of(k: Kind, v: int) -> int:
    if k is SINT32 or k is SINT64:
        return ((v << 1) ^ (v >> 63)) & _UINT64_MAX  # protobuf-go's EncodeZigZag
    return v & _UINT64_MAX  # int32/int64 negatives: sign-extended to 64 bits


def _put_scalar_value(out: bytearray, k: Any, v: Any) -> None:
    """One packed element: its value encoding, no tag."""
    if k == "bool":
        if not isinstance(v, bool):
            raise TypeError(f"expected a bool, got {type(v).__name__}")
        out.append(1 if v else 0)
    elif k in _FLOAT_KINDS:
        out += _float_bits(k, v)
    else:
        _put_varint(out, _varint_of(k, _check_int(k, v)))


def _marshal_scalar(out: bytearray, num: int, k: Any, v: Any, element: bool) -> None:
    if k == "bool":
        if not isinstance(v, bool):
            raise TypeError(f"expected a bool, got {type(v).__name__}")
        if not v and not element:
            return
        _put_tag(out, num, _VARINT)
        out.append(1 if v else 0)
    elif k == "string":
        if not isinstance(v, str):
            raise TypeError(f"expected a str, got {type(v).__name__}")
        if not v and not element:
            return
        try:
            b = v.encode("utf-8")
        except UnicodeEncodeError as e:
            raise ValueError(f"string is not valid UTF-8: {e}") from None
        _put_bytes(out, num, b)
    elif k == "bytes":
        if not isinstance(v, (bytes, bytearray, memoryview)):
            raise TypeError(f"expected bytes, got {type(v).__name__}")
        b = bytes(v)
        if not b and not element:
            return
        _put_bytes(out, num, b)
    elif k == "decimal":
        if not isinstance(v, decimal.Decimal):
            raise TypeError(f"expected a decimal.Decimal, got {type(v).__name__}")
        if not v.is_finite():
            raise ValueError(f"cannot encode {v} as pxf.Decimal")
        if v.is_zero() and not element:
            return
        _put_bytes(out, num, _marshal_decimal(v))
    elif k == "bigfloat":
        if not isinstance(v, BigFloat):
            raise TypeError(f"expected a pb.BigFloat, got {type(v).__name__}")
        # Only +0 at prec 0 is the empty message; -0 there is
        # {negative: true}, as PXF "-0" reads (protowire#410).
        if v.prec == 0 and not v.negative and not element:
            return
        _put_bytes(out, num, _marshal_bigfloat(v))
    elif k is BIGINT:
        v = _check_int(k, v)
        if v == 0 and not element:
            return
        _put_bytes(out, num, _marshal_bigint(v))
    elif k in _FLOAT_KINDS:
        bits = _float_bits(k, v)
        if v == 0 and not element:
            return
        _put_tag(out, num, _FIXED64 if k is DOUBLE else _FIXED32)
        out += bits
    else:
        v = _check_int(k, v)
        if v == 0 and not element:
            return
        _put_tag(out, num, _VARINT)
        _put_varint(out, _varint_of(k, v))


def _int_bytes(n: int) -> bytes:
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


def _marshal_bigint(v: int) -> bytearray:
    msg = bytearray()
    if v != 0:
        _put_bytes(msg, 1, _int_bytes(abs(v)))
    if v < 0:
        _put_tag(msg, 2, _VARINT)
        msg.append(1)
    return msg


# Exact decimal arithmetic: nothing here may round.
_DEC_CTX = decimal.Context(
    prec=decimal.MAX_PREC,
    Emax=decimal.MAX_EMAX,
    Emin=decimal.MIN_EMIN,
    traps=[decimal.InvalidOperation, decimal.Overflow, decimal.Inexact, decimal.Rounded],
)


def _marshal_decimal(v: decimal.Decimal) -> bytearray:
    # Go holds a Decimal as a big.Rat, which keeps no trailing zeros, and
    # writes the smallest scale that makes the value an integer, never a
    # negative one. normalize() is that form.
    if v.is_zero():
        unscaled, scale = 0, 0
    else:
        n = v.copy_abs().normalize(_DEC_CTX)
        exp = n.as_tuple().exponent
        if exp >= 0:
            unscaled, scale = int(n), 0
        else:
            scale = -exp
            unscaled = int(n.scaleb(scale, _DEC_CTX))
    # Go's own Unmarshal refuses a scale past the bound, so writing one would
    # produce bytes no conformant decoder reads (protowire-go#95).
    if scale > MAX_NUMERIC_LITERAL_DIGITS:
        raise ValueError(
            f"pxf.Decimal scale {scale} exceeds MaxNumericLiteralDigits={MAX_NUMERIC_LITERAL_DIGITS}"
        )
    msg = bytearray()
    if unscaled:
        _put_bytes(msg, 1, _int_bytes(unscaled))
    if scale:
        _put_tag(msg, 2, _VARINT)
        _put_varint(msg, scale)
    if unscaled and v.is_signed():
        _put_tag(msg, 3, _VARINT)
        msg.append(1)
    return msg


def _pow2_digits(p: int) -> int:
    """The decimal digits of 2**p, floor(p*log10(2)) + 1, without computing 2**p.

    The 128-bit fixed-point product protowire-go's internal/bigprec uses, exact
    for every uint32 p (float64 is one digit high at p = 146964308).
    """
    return ((p * 0x4D104D427DE7FBCC47C4ACD605BE48BC) >> 128) + 1


def _prec_digits(prec: int) -> int:
    """N, the significant digits a BigFloat of prec bits is written with."""
    return _pow2_digits(prec) + 1


def _exponent_err(mant_bits: int, exponent: int, limit: int) -> Optional[ValueError]:
    if mant_bits == 0:
        return None
    e = exponent + mant_bits
    if e > limit or e < -limit:
        return ValueError(f"pxf.BigFloat binary exponent {e} exceeds MaxBigFloatExponent={limit}")
    return None


def _marshal_bigfloat(v: BigFloat) -> bytearray:
    msg = bytearray()
    prec = v.prec
    if prec == 0:
        if v.negative:
            _put_tag(msg, 4, _VARINT)
            msg.append(1)
        return msg
    n = _prec_digits(prec)
    if n > MAX_NUMERIC_LITERAL_DIGITS:
        raise ValueError(
            f"pxf.BigFloat prec {prec} implies {n} digits; MaxNumericLiteralDigits={MAX_NUMERIC_LITERAL_DIGITS}"
        )
    # The wire mantissa is the value scaled to exactly prec bits.
    m, adj = v.mantissa, 0
    if m:
        widen = prec - m.bit_length()
        m <<= widen
        adj = v.exponent - widen
    if not _INT32_MIN <= adj <= _INT32_MAX:
        raise ValueError(f"pxf.BigFloat exponent {adj} does not fit int32")
    err = _exponent_err(m.bit_length(), adj, MAX_BIG_FLOAT_EXPONENT)
    if err is not None:
        raise err
    if m:
        _put_bytes(msg, 1, _int_bytes(m))
    if adj:
        _put_tag(msg, 2, _VARINT)
        _put_varint(msg, adj & _UINT64_MAX)
    _put_tag(msg, 3, _VARINT)
    _put_varint(msg, prec)
    if v.negative:
        _put_tag(msg, 4, _VARINT)
        msg.append(1)
    return msg


# --- Decoding --------------------------------------------------------------


@dataclass(frozen=True)
class _Limits:
    max_depth: int
    max_digits: int
    max_big_float_exp: int
    max_message_size: int
    max_repeated: int


def _limits(
    max_message_size: int,
    max_nesting_depth: int,
    max_numeric_literal_digits: int,
    max_repeated_count: int,
    max_big_float_exponent: int,
) -> _Limits:
    def pick(v: int, default: int) -> int:
        return v if v > 0 else default

    return _Limits(
        max_depth=pick(max_nesting_depth, MAX_NESTING_DEPTH),
        max_digits=pick(max_numeric_literal_digits, MAX_NUMERIC_LITERAL_DIGITS),
        max_big_float_exp=pick(max_big_float_exponent, MAX_BIG_FLOAT_EXPONENT),
        max_message_size=pick(max_message_size, MAX_MESSAGE_SIZE),
        max_repeated=pick(max_repeated_count, MAX_REPEATED_COUNT),
    )


class _Corrupt(Exception):
    """A wire-level failure; the caller names what was being read."""


def _varint(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    v = 0
    shift = 0
    for i in range(10):
        if pos >= end:
            raise _Corrupt
        b = buf[pos]
        pos += 1
        if i == 9:
            if b >= 2:  # protobuf-go: the 10th byte carries one bit
                raise _Corrupt
            return v | (b << 63), pos
        v |= (b & 0x7F) << shift
        if b < 0x80:
            return v, pos
        shift += 7
    raise AssertionError("unreachable")


def _tag(buf: bytes, pos: int, end: int) -> tuple[int, int, int]:
    v, pos = _varint(buf, pos, end)
    num = v >> 3
    if num > _INT32_MAX or num < 1:
        raise _Corrupt
    return num, v & 7, pos


def _len_prefixed(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    """The start and end of a length-prefixed value."""
    n, pos = _varint(buf, pos, end)
    if n > end - pos:
        raise _Corrupt
    return pos, pos + n


def _fixed(buf: bytes, pos: int, end: int, size: int) -> int:
    if end - pos < size:
        raise _Corrupt
    return pos + size


def _skip(num: int, typ: int, buf: bytes, pos: int, end: int) -> int:
    """protobuf-go's ConsumeFieldValue: the position after one field's value."""
    if typ == _VARINT:
        return _varint(buf, pos, end)[1]
    if typ == _FIXED64:
        return _fixed(buf, pos, end, 8)
    if typ == _FIXED32:
        return _fixed(buf, pos, end, 4)
    if typ == _BYTES:
        return _len_prefixed(buf, pos, end)[1]
    if typ != _START_GROUP:
        raise _Corrupt  # a stray end-group, or a reserved wire type
    groups = [num]
    while groups:
        n2, t2, pos = _tag(buf, pos, end)
        if t2 == _END_GROUP:
            if n2 != groups[-1]:
                raise _Corrupt
            groups.pop()
        elif t2 == _START_GROUP:
            if len(groups) > _GROUP_RECURSION_LIMIT:
                raise _Corrupt
            groups.append(n2)
        else:
            pos = _skip(n2, t2, buf, pos, end)
    return pos


C = TypeVar("C")


def unmarshal(
    data: Union[bytes, bytearray, memoryview],
    obj: Union[C, type[C]],
    *,
    max_message_size: int = 0,
    max_nesting_depth: int = 0,
    max_numeric_literal_digits: int = 0,
    max_repeated_count: int = 0,
    max_big_float_exponent: int = 0,
) -> C:
    """Decode protobuf binary into a dataclass instance, and return it.

    ``obj`` is an instance, which is merged into as Go's ``Unmarshal`` merges,
    or a dataclass, of which a zero instance is made. Each limit is
    protowire-go's ``UnmarshalOptions`` field of the same name; 0 selects the
    default. A failure is a ``ValueError``.
    """
    lim = _limits(
        max_message_size, max_nesting_depth, max_numeric_literal_digits, max_repeated_count, max_big_float_exponent
    )
    return _unmarshal(data, obj, lim)


def _unmarshal(data: Any, obj: Any, lim: _Limits) -> Any:
    buf = bytes(data)
    if len(buf) > lim.max_message_size:
        raise ValueError(f"input of {len(buf)} bytes exceeds MaxMessageSize={lim.max_message_size}")
    if isinstance(obj, type):
        obj = _new(obj)
    elif not dataclasses.is_dataclass(obj):
        raise TypeError(f"pb.unmarshal: expected a dataclass or an instance of one, got {type(obj).__name__}")
    try:
        _unmarshal_struct(buf, 0, len(buf), obj, 0, lim)
    except RecursionError:
        raise ValueError(_recursion_msg()) from None
    return obj


def _unmarshal_struct(buf: bytes, pos: int, end: int, obj: Any, depth: int, lim: _Limits) -> None:
    if depth > lim.max_depth:
        raise _depth_err(lim.max_depth)
    info = _struct_info(type(obj))
    by_number = info.by_number
    while pos < end:
        try:
            num, typ, pos = _tag(buf, pos, end)
        except _Corrupt:
            raise ValueError("corrupt tag") from None
        fi = by_number.get(num)
        if fi is None:
            try:
                pos = _skip(num, typ, buf, pos, end)
            except _Corrupt:
                raise ValueError(f"corrupt field {num}") from None
            continue
        try:
            value, pos = _unmarshal_field(buf, pos, end, typ, fi.type, getattr(obj, fi.name), depth, lim)
        except ValueError as e:
            raise ValueError(f"field {fi.name}: {e}") from None
        setattr(obj, fi.name, value)


def _unmarshal_field(
    buf: bytes, pos: int, end: int, typ: int, t: _T, cur: Any, depth: int, lim: _Limits
) -> tuple[Any, int]:
    """Read one record's value by its declared type; return (new value, position)."""
    if t[0] == "optional":
        t = t[1]
    tag = t[0]
    if tag == "list":
        lst = cur if cur is not None else []
        et = t[1]
        bare = et[1] if et[0] == "optional" else et
        if typ == _BYTES and _is_packed(bare):
            try:
                p, pend = _len_prefixed(buf, pos, end)
            except _Corrupt:
                raise ValueError("corrupt packed field") from None
            while p < pend:
                v, p = _unmarshal_field(buf, p, pend, typ, et, None, depth, lim)
                if len(lst) >= lim.max_repeated:
                    raise ValueError(f"repeated field exceeds MaxRepeatedCount={lim.max_repeated}")
                lst.append(v)
            return lst, pend
        v, pos = _unmarshal_field(buf, pos, end, typ, et, None, depth, lim)
        if len(lst) >= lim.max_repeated:
            raise ValueError(f"repeated field exceeds MaxRepeatedCount={lim.max_repeated}")
        lst.append(v)
        return lst, pos
    if tag == "message":
        try:
            p, pend = _len_prefixed(buf, pos, end)
        except _Corrupt:
            raise ValueError("corrupt embedded message") from None
        msg = cur if cur is not None else _new(t[1])
        _unmarshal_struct(buf, p, pend, msg, depth + 1, lim)
        return msg, pend
    if tag == "map":
        try:
            p, pend = _len_prefixed(buf, pos, end)
        except _Corrupt:
            raise ValueError("corrupt map entry") from None
        # The entry is a descent whatever its value's type (HARDENING.md
        # § Recursion; protowire#449).
        if depth + 1 > lim.max_depth:
            raise _depth_err(lim.max_depth)
        d = cur if cur is not None else {}
        kt, vt = t[1], t[2]
        key, val = _zero(kt), _zero(vt)
        while p < pend:
            try:
                enum, etyp, p = _tag(buf, p, pend)
            except _Corrupt:
                raise ValueError("corrupt tag in map entry") from None
            if enum == 1:
                try:
                    key, p = _unmarshal_field(buf, p, pend, etyp, kt, key, depth + 1, lim)
                except ValueError as e:
                    raise ValueError(f"map key: {e}") from None
            elif enum == 2:
                try:
                    val, p = _unmarshal_field(buf, p, pend, etyp, vt, val, depth + 1, lim)
                except ValueError as e:
                    raise ValueError(f"map value: {e}") from None
            else:
                try:
                    p = _skip(enum, etyp, buf, p, pend)
                except _Corrupt:
                    raise ValueError(f"corrupt unknown field {enum} in map entry") from None
        if len(d) >= lim.max_repeated and key not in d:
            raise ValueError(f"map field exceeds MaxRepeatedCount={lim.max_repeated}")
        d[key] = val
        return d, pend
    return _unmarshal_scalar(buf, pos, end, t[1], lim)


def _signed(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def _unmarshal_scalar(buf: bytes, pos: int, end: int, k: Any, lim: _Limits) -> tuple[Any, int]:
    if k in _FLOAT_KINDS:
        size = 8 if k is DOUBLE else 4
        try:
            p = _fixed(buf, pos, end, size)
        except _Corrupt:
            raise ValueError(f"corrupt {'float64' if k is DOUBLE else 'float32'}") from None
        return struct.unpack_from("<d" if k is DOUBLE else "<f", buf, pos)[0], p
    if k in ("string", "bytes", "decimal", "bigfloat") or k is BIGINT:
        what = {
            "string": "string",
            "bytes": "bytes",
        }.get(k, "embedded message")
        try:
            p, pend = _len_prefixed(buf, pos, end)
        except _Corrupt:
            raise ValueError(f"corrupt {what}") from None
        if k == "string":
            try:
                return buf[p:pend].decode("utf-8"), pend
            except UnicodeDecodeError:
                # HARDENING.md § UTF-8: a field, a list element, a map key
                # and a map value all reach this case.
                raise ValueError("invalid UTF-8 in string") from None
        if k == "bytes":
            return buf[p:pend], pend
        if k is BIGINT:
            return _unmarshal_bigint(buf, p, pend), pend
        if k == "decimal":
            return _unmarshal_decimal(buf, p, pend, lim.max_digits), pend
        return _unmarshal_bigfloat(buf, p, pend, lim.max_big_float_exp, lim.max_digits), pend
    what = "bool" if k == "bool" else ("uint" if k in (UINT32, UINT64) else "int")
    try:
        v, p = _varint(buf, pos, end)
    except _Corrupt:
        raise ValueError(f"corrupt {what}") from None
    if k == "bool":
        return v != 0, p
    if k is INT64:
        return _signed(v, 64), p
    if k is INT32:
        return _signed(v, 32), p
    if k is UINT64:
        return v, p
    if k is UINT32:
        return v & _UINT32_MAX, p
    z = (v >> 1) ^ -(v & 1)  # zigzag, as protobuf-go's DecodeZigZag
    return (_signed(z, 64) if k is SINT64 else _signed(z, 32)), p


def _sub_fields(buf: bytes, pos: int, end: int, what: str, varints: set, blobs: set) -> dict:
    """The last value of each known field of a big-number message."""
    out: dict = {}
    while pos < end:
        try:
            num, typ, pos = _tag(buf, pos, end)
        except _Corrupt:
            raise ValueError(f"corrupt tag in {what}") from None
        try:
            if num in blobs:
                p, pos = _len_prefixed(buf, pos, end)
                out[num] = buf[p:pos]
            elif num in varints:
                out[num], pos = _varint(buf, pos, end)
            else:
                pos = _skip(num, typ, buf, pos, end)
        except _Corrupt:
            raise ValueError(f"corrupt field {num} in {what}") from None
    return out


def _unmarshal_bigint(buf: bytes, pos: int, end: int) -> int:
    f = _sub_fields(buf, pos, end, "BigInt", {2}, {1})
    v = int.from_bytes(f.get(1, b""), "big")
    return -v if f.get(2, 0) else v


def _unmarshal_decimal(buf: bytes, pos: int, end: int, max_digits: int) -> decimal.Decimal:
    f = _sub_fields(buf, pos, end, "Decimal", {2, 3}, {1})
    unscaled = int.from_bytes(f.get(1, b""), "big")
    scale = _signed(f.get(2, 0), 32)  # int32: the low 32 bits, read as signed
    # value = unscaled x 10^(-scale), and both signs of scale materialise
    # 10^|scale|, so the bound comes first (protowire-go#95).
    if scale > max_digits or scale < -max_digits:
        raise ValueError(f"pxf.Decimal scale {scale} exceeds MaxNumericLiteralDigits={max_digits}")
    d = decimal.Decimal(unscaled).scaleb(-scale, _DEC_CTX)
    # A big.Rat has no negative zero.
    return -d if f.get(3, 0) and unscaled else d


def _unmarshal_bigfloat(buf: bytes, pos: int, end: int, max_exp: int, max_digits: int) -> BigFloat:
    f = _sub_fields(buf, pos, end, "BigFloat", {2, 3, 4}, {1})
    negative = bool(f.get(4, 0))
    prec = f.get(3, 0) & _UINT32_MAX  # uint32: the low 32 bits (protowire-go#230)
    if prec == 0:
        # A zero at prec 0, signed by negative (protowire#410).
        return BigFloat(0, 0, 0, negative)
    n = _prec_digits(prec)
    if n > max_digits:
        raise ValueError(f"pxf.BigFloat prec {prec} implies {n} digits; MaxNumericLiteralDigits={max_digits}")
    exp = _signed(f.get(2, 0), 32)
    mant = int.from_bytes(f.get(1, b""), "big")
    err = _exponent_err(mant.bit_length(), exp, max_exp)
    if err is not None:
        raise err
    if mant:
        q, e = _round_to_prec(mant, exp, prec)
        if e + q.bit_length() > _INT32_MAX:
            raise ValueError(
                f"pxf.BigFloat exponent {exp} with a {mant.bit_length()}-bit mantissa overflows big.Float"
            )
    return BigFloat(mant, exp, prec, negative)


# --- Streams ---------------------------------------------------------------


class FramingError(ValueError):
    """The length-prefix framing of a stream is invalid.

    The stream is out of step after this, and the Decoder stays failed.
    """


class Encoder:
    """Writes length-prefixed messages: a varint length, then the body."""

    def __init__(self, w: BinaryIO, *, max_nesting_depth: int = 0) -> None:
        self._w = w
        self._max_depth = max_nesting_depth

    def encode(self, obj: Any) -> None:
        body = marshal(obj, max_nesting_depth=self._max_depth)
        hdr = bytearray()
        _put_varint(hdr, len(body))
        self._w.write(bytes(hdr))
        self._w.write(body)


class Decoder:
    """Reads length-prefixed messages written by ``Encoder`` or protowire-go's.

    ``decode`` returns ``None`` at a clean end of stream. A truncated frame
    raises ``EOFError`` and corrupt framing ``FramingError``; both leave the
    Decoder failed. A frame whose message does not decode raises
    ``ValueError`` and the next frame can still be read.
    """

    def __init__(
        self,
        r: BinaryIO,
        *,
        max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
        max_message_size: int = 0,
        max_nesting_depth: int = 0,
        max_numeric_literal_digits: int = 0,
        max_repeated_count: int = 0,
        max_big_float_exponent: int = 0,
    ) -> None:
        self._r = r
        self._max = max_frame_size
        self._err: Optional[Exception] = None
        self._lim = _limits(
            max_message_size, max_nesting_depth, max_numeric_literal_digits, max_repeated_count, max_big_float_exponent
        )

    def _fail(self, e: Exception) -> Exception:
        self._err = e
        return e

    def decode(self, obj: Union[C, type[C]]) -> Optional[C]:
        err = self._err
        if err is not None:
            raise err
        # encoding/binary.ReadUvarint: ten bytes at most, the tenth one bit.
        n = 0
        shift = 0
        for i in range(10):
            b = self._r.read(1)
            if not b:
                if i == 0:
                    return None
                raise self._fail(EOFError("unexpected EOF"))
            c = b[0]
            if c < 0x80:
                if i == 9 and c > 1:
                    raise self._fail(FramingError("pb: stream framing corrupt: varint overflows a 64-bit integer"))
                n |= c << shift
                break
            n |= (c & 0x7F) << shift
            shift += 7
        else:
            raise self._fail(FramingError("pb: stream framing corrupt: varint overflows a 64-bit integer"))
        if n > self._max:
            raise self._fail(FramingError(f"pb: stream framing corrupt: frame size {n} exceeds max {self._max}"))
        body = bytearray()
        while len(body) < n:
            chunk = self._r.read(n - len(body))
            if not chunk:
                raise self._fail(EOFError("unexpected EOF"))
            body += chunk
        return _unmarshal(body, obj, self._lim)
