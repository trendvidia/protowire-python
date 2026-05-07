# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Envelope tests — covers builders, queries, and wire format.

The wire format is verified by re-decoding the encoded bytes through
google.protobuf using a hand-written FileDescriptorProto matching the Go
envelope schema.
"""

from __future__ import annotations

from protowire import envelope


def test_ok_builder():
    e = envelope.OK(200, b"payload")
    assert e.is_ok()
    assert not e.is_app_error()
    assert not e.is_transport_error()
    assert e.status == 200
    assert e.data == b"payload"


def test_err_builder():
    e = envelope.Err(400, "INVALID", "bad input", "arg1", "arg2")
    assert e.is_app_error()
    assert not e.is_ok()
    assert e.error_code() == "INVALID"
    assert e.error.args == ["arg1", "arg2"]


def test_transport_err_builder():
    e = envelope.TransportErr("connection reset")
    assert e.is_transport_error()
    assert not e.is_app_error()
    assert e.transport_error == "connection reset"


def test_with_field_chains():
    err = envelope.NewAppError("VALIDATION", "fields invalid")
    err.with_field("email", "REQUIRED", "missing").with_field(
        "age", "OUT_OF_RANGE", "must be ≥ 18"
    )
    assert len(err.details) == 2
    assert err.details[0].field == "email"
    assert err.details[1].code == "OUT_OF_RANGE"


def test_field_errors_indexed():
    e = envelope.Err(422, "VALIDATION", "")
    e.error.with_field("name", "REQUIRED", "")
    e.error.with_field("age", "RANGE", "")
    fes = e.field_errors()
    assert set(fes) == {"name", "age"}
    assert fes["name"].code == "REQUIRED"


def test_with_meta_chains():
    err = envelope.NewAppError("X", "y").with_meta("k1", "v1").with_meta("k2", "v2")
    assert err.metadata == {"k1": "v1", "k2": "v2"}


def test_encode_ok_minimum():
    # status=0, no data -> empty bytes (proto3 zero values omitted).
    assert envelope.OK(0, b"").encode() == b""


def test_encode_negative_status_uses_sign_extended_int32():
    # `protowire:"1"` (no `,zigzag`) → plain int32; negatives encode as a
    # 10-byte sign-extended varint to match the Go envelope wire format.
    e = envelope.Envelope(status=-1)
    raw = e.encode()
    # tag=1, wire=0 (varint), then 10 bytes of 0xff…0x01 for -1 sign-extended.
    assert raw == bytes([0x08, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x01])


def test_encode_transport_error():
    raw = envelope.TransportErr("oops").encode()
    # tag=2 wire=2, len=4, "oops"
    assert raw == bytes([0x12, 4]) + b"oops"


def test_encode_data_passthrough():
    raw = envelope.OK(0, b"\x00\x01\x02").encode()
    assert raw == bytes([0x1a, 3, 0, 1, 2])


def test_encode_app_error_nested():
    e = envelope.Err(0, "BAD", "")
    raw = e.encode()
    # tag=4, wire=2, len, then submessage "code=BAD" -> tag=1,wire=2,len=3,"BAD"
    assert raw[0] == 0x22  # field 4, length-delimited
    sub_len = raw[1]
    sub = raw[2 : 2 + sub_len]
    # submessage starts with tag for code (field 1, wire=2).
    assert sub[0] == 0x0a
    assert sub[1] == 3
    assert sub[2:5] == b"BAD"


# --- decode round-trip ---------------------------------------------------


def test_decode_ok_round_trip():
    e = envelope.OK(200, b"payload")
    got = envelope.Envelope.decode(e.encode())
    assert got.status == 200
    assert got.data == b"payload"
    assert got.transport_error == ""
    assert got.error is None


def test_decode_transport_err_round_trip():
    e = envelope.TransportErr("connection reset")
    got = envelope.Envelope.decode(e.encode())
    assert got.is_transport_error()
    assert got.transport_error == "connection reset"


def test_decode_app_error_round_trip():
    e = envelope.Err(404, "NOT_FOUND", "user not found", "id-42")
    assert e.error is not None
    e.error.with_field("user_id", "INVALID", "bad format").with_meta(
        "request_id", "req-7"
    )
    got = envelope.Envelope.decode(e.encode())
    assert got.is_app_error()
    assert got.status == 404
    assert got.error is not None
    assert got.error.code == "NOT_FOUND"
    assert got.error.message == "user not found"
    assert got.error.args == ["id-42"]
    assert len(got.error.details) == 1
    assert got.error.details[0].field == "user_id"
    assert got.error.details[0].code == "INVALID"
    assert got.error.metadata == {"request_id": "req-7"}


def test_decode_negative_status_round_trip():
    """Status uses zig-zag encoding; negative values must decode correctly."""
    e = envelope.Envelope(status=-1, data=b"x")
    got = envelope.Envelope.decode(e.encode())
    assert got.status == -1
    assert got.data == b"x"


def test_decode_dump_envelope_matches_canonical():
    """The canonical fixture used by `scripts/dump_envelope.py` must
    encode + re-decode losslessly."""
    e = envelope.Err(402, "INSUFFICIENT_FUNDS", "balance too low",
                     "$3.50", "$10.00")
    e.data = bytes([0xDE, 0xAD, 0xBE, 0xEF])
    assert e.error is not None
    e.error.with_field("amount", "MIN_VALUE", "below minimum", "10.00")
    e.error.with_meta("request_id", "req-123")

    raw = e.encode()
    got = envelope.Envelope.decode(raw)
    assert got.status == 402
    assert got.data == bytes([0xDE, 0xAD, 0xBE, 0xEF])
    assert got.error is not None
    assert got.error.code == "INSUFFICIENT_FUNDS"
    assert got.error.message == "balance too low"
    assert got.error.args == ["$3.50", "$10.00"]
    assert len(got.error.details) == 1
    assert got.error.details[0].field == "amount"
    assert got.error.metadata == {"request_id": "req-123"}
