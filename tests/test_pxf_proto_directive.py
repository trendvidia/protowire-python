# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Tests for the @proto directive (draft §3.4.5).

Four body shapes lexically distinguished: anonymous, named, source,
descriptor. Plus reserved-directive-name rejection (draft §3.4.6).

These exercise the FFI roundtrip through protowire-cpp via
unmarshal_full, since Python doesn't expose a pure Parse() entry
point — the AST-tier checks happen on the cpp side and surface to
Python through Result.protos.
"""

import base64

import pytest

from protowire import pxf


def test_anonymous_body(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        b"""@proto {
  string symbol = 1;
  double price = 2;
}
string_field = "hi"
""",
        msg,
    )
    assert len(r.protos) == 1
    p = r.protos[0]
    assert p.shape == "anonymous"
    assert p.type_name == ""
    assert b"string symbol = 1;" in p.body
    assert b"double price = 2;" in p.body


def test_named_body(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        b"""@proto trades.v1.Trade {
  string symbol = 1;
}
string_field = "hi"
""",
        msg,
    )
    assert len(r.protos) == 1
    assert r.protos[0].shape == "named"
    assert r.protos[0].type_name == "trades.v1.Trade"


def test_source_body(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        b'''@proto """
syntax = "proto3";
message Trade { string symbol = 1; }
"""
string_field = "hi"
''',
        msg,
    )
    assert len(r.protos) == 1
    assert r.protos[0].shape == "source"
    assert b"message Trade" in r.protos[0].body


def test_descriptor_body(all_types_cls):
    msg = all_types_cls()
    raw = b"\x0a\x05hello"
    b64 = base64.standard_b64encode(raw).decode()
    r = pxf.unmarshal_full(
        f'@proto b"{b64}"\nstring_field = "hi"\n'.encode(),
        msg,
    )
    assert len(r.protos) == 1
    assert r.protos[0].shape == "descriptor"
    assert r.protos[0].body == raw


def test_multiple_protos(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        b"""@proto trades.v1.Trade { string symbol = 1; }
@proto orders.v1.Order { string id = 1; }
string_field = "hi"
""",
        msg,
    )
    assert len(r.protos) == 2
    assert [p.type_name for p in r.protos] == ["trades.v1.Trade", "orders.v1.Order"]


def test_nested_braces_in_body(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        b"""@proto {
  message Side {
    string label = 1;
  }
  Side side = 1;
}
string_field = "hi"
""",
        msg,
    )
    assert len(r.protos) == 1
    body = r.protos[0].body
    assert b"message Side" in body
    assert b"Side side = 1;" in body


def test_rejects_bad_shape(all_types_cls):
    msg = all_types_cls()
    with pytest.raises(ValueError, match="@proto"):
        pxf.unmarshal_full(b"@proto 42\nstring_field = \"hi\"\n", msg)


def test_rejects_named_missing_brace(all_types_cls):
    msg = all_types_cls()
    with pytest.raises(ValueError, match=r"'\{'"):
        pxf.unmarshal_full(
            b"@proto trades.v1.Trade 42\nstring_field = \"hi\"\n",
            msg,
        )


def test_rejects_anonymous_unmatched_brace(all_types_cls):
    msg = all_types_cls()
    with pytest.raises(ValueError, match="unmatched"):
        pxf.unmarshal_full(b"@proto { string symbol = 1;\n", msg)


@pytest.mark.parametrize(
    "name", ["table", "datasource", "view", "procedure", "function", "permissions"]
)
def test_rejects_reserved_directive_names(all_types_cls, name):
    """Draft §3.4.6: v1 decoders MUST reject future-allocated names."""
    msg = all_types_cls()
    with pytest.raises(ValueError, match="spec-reserved"):
        pxf.unmarshal_full(
            f"@{name} {{ x = 1 }}\nstring_field = \"hi\"\n".encode(),
            msg,
        )


def test_proto_directive_dataclass_shape():
    """ProtoDirective is a frozen dataclass with shape/type_name/body."""
    pd = pxf.ProtoDirective(shape="named", type_name="pkg.T", body=b"hello")
    assert pd.shape == "named"
    assert pd.type_name == "pkg.T"
    assert pd.body == b"hello"
    # Frozen — assignment raises.
    with pytest.raises((AttributeError, Exception)):
        pd.shape = "anonymous"  # type: ignore[misc]
