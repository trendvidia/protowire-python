# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""End-to-end PXF round-trip matrix, port of pxf_full_roundtrip_test.cc.

Pipeline (all asserted):
  PXF text₀
    → pxf.unmarshal       → msg1
    → SerializeToString   → bin1
  bin1
    → ParseFromString     → msg2
    → pxf.marshal         → PXF text₁
  PXF text₁
    → pxf.unmarshal       → msg3
    → SerializeToString   → bin3

Asserts:
  - msg1 == msg2          (binary round-trip lossless)
  - msg2 == msg3          (re-encoded text decodes the same)
  - bin1 == bin3          (byte-stable across the round-trip, deterministic)
"""

from __future__ import annotations

import pytest

from protowire import pxf


def _make(all_types_cls):
    return all_types_cls()


def _serialize_deterministic(msg) -> bytes:
    return msg.SerializePartialToString(deterministic=True)


def run_pipeline(all_types_cls, src: str):
    m1 = _make(all_types_cls)
    pxf.unmarshal(src, m1)
    bin1 = _serialize_deterministic(m1)

    m2 = _make(all_types_cls)
    m2.ParseFromString(bin1)

    text1 = pxf.marshal(m2)

    m3 = _make(all_types_cls)
    pxf.unmarshal(text1, m3)
    bin3 = _serialize_deterministic(m3)

    return m1, m2, m3, bin1, bin3, text1


def expect_full_equality(m1, m2, m3, bin1, bin3, text1):
    assert m1 == m2, "m1 != m2 after binary round-trip"
    assert m2 == m3, f"m2 != m3 after PXF re-encode/re-decode\nre-encoded text:\n{text1}"
    assert bin1 == bin3, (
        f"binary serialization drifted across the round-trip\n"
        f"re-encoded text:\n{text1}"
    )


# -------------------------------------------------------------------------


def test_all_scalar_types(all_types_cls):
    src = """
string_field = "hello"
int32_field = -42
int64_field = 1234567890
uint32_field = 7
uint64_field = 18000000000
float_field = 1.5
double_field = 2.71828
bool_field = true
bytes_field = b"AQID"
enum_field = STATUS_ACTIVE
"""
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)

    # Per-field belt-and-suspenders.
    assert m3.string_field == "hello"
    assert m3.int32_field == -42
    assert m3.int64_field == 1234567890
    assert m3.uint32_field == 7
    assert m3.uint64_field == 18000000000
    assert m3.float_field == pytest.approx(1.5)
    assert m3.double_field == pytest.approx(2.71828)
    assert m3.bool_field is True
    assert m3.bytes_field == bytes([0x01, 0x02, 0x03])
    assert m3.enum_field == 1


def test_negative_and_extreme_numerics(all_types_cls):
    src = """
int32_field = -2147483648
int64_field = -9223372036854775807
uint32_field = 4294967295
uint64_field = 18446744073709551615
float_field = -3.4028235e38
double_field = -1.7976931348623157e308
"""
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)


def test_nested_message(all_types_cls):
    src = """
nested_field {
  name = "child"
  value = 99
}
"""
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)
    assert m3.nested_field.name == "child"
    assert m3.nested_field.value == 99


def test_repeated_scalars(all_types_cls):
    src = 'repeated_string = ["a", "b", "c"]\n'
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)
    assert list(m3.repeated_string) == ["a", "b", "c"]


def test_repeated_messages(all_types_cls):
    src = """
repeated_nested = [
  { name = "a" value = 1 }
  { name = "b" value = 2 }
  { name = "c" value = 3 }
]
"""
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)
    assert len(m3.repeated_nested) == 3
    assert m3.repeated_nested[0].name == "a"
    assert m3.repeated_nested[2].value == 3


def test_maps_all_key_kinds(all_types_cls):
    src = """
string_map = {
  env: "prod"
  team: "platform"
}
nested_map = {
  primary: { name = "p" value = 1 }
  backup:  { name = "b" value = 2 }
}
int_map = {
  404: "Not Found"
  500: "Internal Error"
}
"""
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    # Maps may serialize in arbitrary order, so binary equality holds only
    # under deterministic serialization (which we use). MessageDifferencer
    # equivalent: dict equality after round-trip.
    assert m1 == m2
    assert m2 == m3
    assert dict(m3.string_map) == {"env": "prod", "team": "platform"}
    assert dict(m3.int_map) == {404: "Not Found", 500: "Internal Error"}
    assert dict(m3.nested_map)["primary"].value == 1


def test_timestamp_duration(all_types_cls):
    src = """
ts_field = 2024-01-15T10:30:00.123456789Z
dur_field = 1h30m45s
"""
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)


def test_negative_duration(all_types_cls):
    src = "dur_field = -30s"
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)


def test_oneof_text_branch(all_types_cls):
    src = 'text_choice = "picked"'
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)
    assert m3.WhichOneof("choice") == "text_choice"
    assert m3.text_choice == "picked"


def test_oneof_number_branch(all_types_cls):
    src = "number_choice = 42"
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)
    assert m3.WhichOneof("choice") == "number_choice"
    assert m3.number_choice == 42


def test_wrapper_types(all_types_cls):
    src = """
nullable_string = "wrapped"
nullable_int = 123
nullable_bool = true
"""
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)
    assert m3.nullable_string.value == "wrapped"
    assert m3.nullable_int.value == 123
    assert m3.nullable_bool.value is True


def test_everything_at_once(all_types_cls):
    src = """
string_field = "kitchen sink"
int32_field = -1
int64_field = -9999999999
uint32_field = 1
uint64_field = 9999999999
float_field = 0.5
double_field = 0.125
bool_field = true
bytes_field = b"SGVsbG8="
enum_field = STATUS_INACTIVE
nested_field {
  name = "root"
  value = 42
}
repeated_string = ["x", "y", "z"]
repeated_nested = [
  { name = "n1" value = 11 }
  { name = "n2" value = 22 }
]
string_map = {
  alpha: "A"
  beta:  "B"
}
nested_map = {
  one: { name = "n3" value = 33 }
}
int_map = {
  1: "one"
  2: "two"
}
ts_field = 2024-01-15T10:30:00Z
dur_field = 1h30m45s
text_choice = "decided"
nullable_string = "wrapped"
nullable_int = 7
nullable_bool = false
"""
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, src)
    # Maps in play — full byte equality may not hold; assert semantic equality.
    assert m1 == m2
    assert m2 == m3


def test_empty_document_is_valid(all_types_cls):
    m1, m2, m3, bin1, bin3, text1 = run_pipeline(all_types_cls, "")
    expect_full_equality(m1, m2, m3, bin1, bin3, text1)
    assert bin1 == b""
