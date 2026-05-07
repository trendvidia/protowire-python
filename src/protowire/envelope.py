# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Standard API response envelope — wire-compatible with the Go envelope package.

Wire format mirrors the Go `protowire:"N"` struct tags: signed ints zig-zag,
strings/bytes length-delimited, nested messages length-delimited. We implement
the wire format in pure Python rather than crossing the FFI for envelope ops
because there is no schema involved — field numbers are fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Optional


# --- low-level wire helpers (proto3 / protowire) -------------------------


def _enc_varint(out: bytearray, v: int) -> None:
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)


def _enc_zigzag64(v: int) -> int:
    return (v << 1) ^ (v >> 63) & 0xFFFFFFFFFFFFFFFF


def _enc_tag(out: bytearray, num: int, wire: int) -> None:
    _enc_varint(out, (num << 3) | wire)


def _enc_string(out: bytearray, num: int, s: str) -> None:
    if not s:
        return
    b = s.encode("utf-8")
    _enc_tag(out, num, 2)
    _enc_varint(out, len(b))
    out.extend(b)


def _enc_bytes(out: bytearray, num: int, b: bytes) -> None:
    if not b:
        return
    _enc_tag(out, num, 2)
    _enc_varint(out, len(b))
    out.extend(b)


def _enc_repeated_string(out: bytearray, num: int, ss: list[str]) -> None:
    for s in ss:
        # Repeated strings: one tag+value per element, even when empty.
        _enc_tag(out, num, 2)
        b = s.encode("utf-8")
        _enc_varint(out, len(b))
        out.extend(b)


def _enc_int32(out: bytearray, num: int, v: int) -> None:
    if v == 0:
        return
    _enc_tag(out, num, 0)
    if v < 0:
        v &= 0xFFFFFFFFFFFFFFFF
    _enc_varint(out, v)


def _enc_sint32(out: bytearray, num: int, v: int) -> None:
    """Signed int32 with zig-zag encoding (matches Go protowire pb)."""
    if v == 0:
        return
    _enc_tag(out, num, 0)
    _enc_varint(out, _enc_zigzag64(v))


def _enc_submessage(out: bytearray, num: int, sub: bytes) -> None:
    _enc_tag(out, num, 2)
    _enc_varint(out, len(sub))
    out.extend(sub)


def _dec_varint(buf: bytes, i: int) -> tuple[int, int]:
    v = 0
    shift = 0
    while True:
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        if b < 0x80:
            return v, i
        shift += 7
        if shift > 63:
            raise ValueError("varint overflow")


def _dec_zigzag64(v: int) -> int:
    return (v >> 1) ^ -(v & 1)


def _dec_tag(buf: bytes, i: int) -> tuple[int, int, int]:
    v, i = _dec_varint(buf, i)
    return v >> 3, v & 7, i


def _dec_string(buf: bytes, i: int) -> tuple[str, int]:
    n, i = _dec_varint(buf, i)
    end = i + n
    return buf[i:end].decode("utf-8"), end


def _dec_bytes(buf: bytes, i: int) -> tuple[bytes, int]:
    n, i = _dec_varint(buf, i)
    end = i + n
    return bytes(buf[i:end]), end


def _dec_int32(v: int) -> int:
    """Decode a varint as a two's-complement int32 (sign-extended from int64)."""
    if v >= 0x8000000000000000:
        v -= 0x10000000000000000
    return v


def _skip_field(buf: bytes, i: int, wire: int) -> int:
    if wire == 0:  # varint
        _, i = _dec_varint(buf, i)
        return i
    if wire == 1:  # fixed64
        return i + 8
    if wire == 2:  # length-delimited
        n, i = _dec_varint(buf, i)
        return i + n
    if wire == 5:  # fixed32
        return i + 4
    raise ValueError(f"unsupported wire type: {wire}")


# --- public types --------------------------------------------------------


@dataclass
class FieldError:
    field: str = ""
    code: str = ""
    message: str = ""
    args: list[str] = _field(default_factory=list)

    def encode(self) -> bytes:
        out = bytearray()
        _enc_string(out, 1, self.field)
        _enc_string(out, 2, self.code)
        _enc_string(out, 3, self.message)
        _enc_repeated_string(out, 4, self.args)
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> "FieldError":
        out = cls()
        i = 0
        n = len(data)
        while i < n:
            num, wire, i = _dec_tag(data, i)
            if num == 1 and wire == 2:
                out.field, i = _dec_string(data, i)
            elif num == 2 and wire == 2:
                out.code, i = _dec_string(data, i)
            elif num == 3 and wire == 2:
                out.message, i = _dec_string(data, i)
            elif num == 4 and wire == 2:
                v, i = _dec_string(data, i)
                out.args.append(v)
            else:
                i = _skip_field(data, i, wire)
        return out


@dataclass
class AppError:
    code: str = ""
    message: str = ""
    args: list[str] = _field(default_factory=list)
    details: list[FieldError] = _field(default_factory=list)
    metadata: dict[str, str] = _field(default_factory=dict)

    def with_field(
        self,
        field_name: str,
        code: str,
        message: str,
        *args: str,
    ) -> "AppError":
        self.details.append(
            FieldError(field=field_name, code=code, message=message, args=list(args))
        )
        return self

    def with_meta(self, key: str, value: str) -> "AppError":
        self.metadata[key] = value
        return self

    def encode(self) -> bytes:
        out = bytearray()
        _enc_string(out, 1, self.code)
        _enc_string(out, 2, self.message)
        _enc_repeated_string(out, 3, self.args)
        for d in self.details:
            _enc_submessage(out, 4, d.encode())
        # Metadata: map<string,string> as repeated message{key=1,value=2}, field 5.
        for k, v in self.metadata.items():
            entry = bytearray()
            _enc_string(entry, 1, k)
            _enc_string(entry, 2, v)
            _enc_submessage(out, 5, bytes(entry))
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> "AppError":
        out = cls()
        i = 0
        n = len(data)
        while i < n:
            num, wire, i = _dec_tag(data, i)
            if num == 1 and wire == 2:
                out.code, i = _dec_string(data, i)
            elif num == 2 and wire == 2:
                out.message, i = _dec_string(data, i)
            elif num == 3 and wire == 2:
                v, i = _dec_string(data, i)
                out.args.append(v)
            elif num == 4 and wire == 2:
                sub, i = _dec_bytes(data, i)
                out.details.append(FieldError.decode(sub))
            elif num == 5 and wire == 2:
                # Map entry: message{key=1,value=2}.
                sub, i = _dec_bytes(data, i)
                key, val, j = "", "", 0
                m = len(sub)
                while j < m:
                    knum, kwire, j = _dec_tag(sub, j)
                    if knum == 1 and kwire == 2:
                        key, j = _dec_string(sub, j)
                    elif knum == 2 and kwire == 2:
                        val, j = _dec_string(sub, j)
                    else:
                        j = _skip_field(sub, j, kwire)
                out.metadata[key] = val
            else:
                i = _skip_field(data, i, wire)
        return out


@dataclass
class Envelope:
    status: int = 0
    transport_error: str = ""
    data: bytes = b""
    error: Optional[AppError] = None

    # --- builders ---
    @classmethod
    def ok(cls, status: int, data: bytes) -> "Envelope":
        return cls(status=status, data=bytes(data))

    @classmethod
    def err(
        cls, status: int, code: str, message: str, *args: str
    ) -> "Envelope":
        return cls(
            status=status,
            error=AppError(code=code, message=message, args=list(args)),
        )

    @classmethod
    def transport_err(cls, err: str) -> "Envelope":
        return cls(transport_error=err)

    # --- queries ---
    def is_ok(self) -> bool:
        return not self.transport_error and self.error is None

    def is_transport_error(self) -> bool:
        return bool(self.transport_error)

    def is_app_error(self) -> bool:
        return self.error is not None

    def error_code(self) -> str:
        return self.error.code if self.error else ""

    def field_errors(self) -> dict[str, FieldError]:
        if self.error is None or not self.error.details:
            return {}
        return {fe.field: fe for fe in self.error.details}

    # --- wire encode/decode (matches protowire pb) ---
    def encode(self) -> bytes:
        out = bytearray()
        # status is plain int32 (proto3 int32 wire encoding, sign-extended
        # to a 10-byte varint for negative values). Matches the Go envelope
        # struct tag `protowire:"1"` (no `,zigzag` option).
        _enc_int32(out, 1, self.status)
        _enc_string(out, 2, self.transport_error)
        _enc_bytes(out, 3, self.data)
        if self.error is not None:
            _enc_submessage(out, 4, self.error.encode())
        return bytes(out)

    @classmethod
    def decode(cls, data: bytes) -> "Envelope":
        out = cls()
        i = 0
        n = len(data)
        while i < n:
            num, wire, i = _dec_tag(data, i)
            if num == 1 and wire == 0:
                v, i = _dec_varint(data, i)
                out.status = _dec_int32(v)
            elif num == 2 and wire == 2:
                out.transport_error, i = _dec_string(data, i)
            elif num == 3 and wire == 2:
                out.data, i = _dec_bytes(data, i)
            elif num == 4 and wire == 2:
                sub, i = _dec_bytes(data, i)
                out.error = AppError.decode(sub)
            else:
                i = _skip_field(data, i, wire)
        return out


# Free-function aliases mirroring the Go API.
def OK(status: int, data: bytes) -> Envelope:
    return Envelope.ok(status, data)


def Err(status: int, code: str, message: str, *args: str) -> Envelope:
    return Envelope.err(status, code, message, *args)


def TransportErr(err: str) -> Envelope:
    return Envelope.transport_err(err)


def NewAppError(code: str, message: str, *args: str) -> AppError:
    return AppError(code=code, message=message, args=list(args))
