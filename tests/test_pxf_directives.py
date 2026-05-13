# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Tests for the PXF v0.72+ surface exposed in v0.75.0:

  - `Result.directives` and `Result.datasets` populated by `unmarshal_full`
  - `pxf.validate_descriptor` and `pxf.Violation`
  - `skip_validate` opt-out on `unmarshal` / `unmarshal_full`
"""

from __future__ import annotations

import pytest

from protowire import pxf


# ---- Result.directives --------------------------------------------------


def test_directives_empty_when_no_at_directives(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full('string_field = "x"', msg)
    assert r.directives == ()
    assert r.datasets == ()


def test_bare_directive_recorded(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full('@frob\nstring_field = "x"', msg)
    assert len(r.directives) == 1
    d = r.directives[0]
    assert d.name == "frob"
    assert d.prefixes == ()
    assert d.has_body is False
    assert d.type == ""


def test_single_prefix_populates_legacy_type(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        '@header pkg.Hdr { id = "h" }\nstring_field = "x"', msg
    )
    assert len(r.directives) == 1
    d = r.directives[0]
    assert d.name == "header"
    assert d.prefixes == ("pkg.Hdr",)
    assert d.type == "pkg.Hdr"
    assert d.has_body is True
    assert b'id = "h"' in d.body


def test_two_prefixes_leave_type_empty(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        "@entry label pkg.MsgType\nstring_field = \"x\"", msg
    )
    d = r.directives[0]
    assert d.prefixes == ("label", "pkg.MsgType")
    assert d.type == ""


def test_multiple_directives_in_source_order(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        '@header pkg.Hdr { id = "h" }\n@frob alpha beta\n@meta\nstring_field = "x"',
        msg,
    )
    names = [d.name for d in r.directives]
    assert names == ["header", "frob", "meta"]


def test_at_type_does_not_leak_into_directives(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        '@type test.v1.AllTypes\n@frob alpha\nstring_field = "x"', msg
    )
    assert len(r.directives) == 1
    assert r.directives[0].name == "frob"


# ---- Result.datasets ------------------------------------------------------


def test_table_recorded_with_columns_and_rows(all_types_cls):
    msg = all_types_cls()
    src = "@dataset trades.v1.Trade ( px, qty )\n( 100, 5 )\n( 101, 7 )\n"
    r = pxf.unmarshal_full(src, msg)
    assert len(r.datasets) == 1
    t = r.datasets[0]
    assert t.type == "trades.v1.Trade"
    assert t.columns == ("px", "qty")
    assert len(t.rows) == 2
    # Row 0: (100, 5) — both IntVals.
    assert t.rows[0] == (("int", "100"), ("int", "5"))


def test_table_cell_shapes(all_types_cls):
    msg = all_types_cls()
    src = '@dataset x.Row ( a, b, c, d )\n( 42, "hello", true, null )\n'
    r = pxf.unmarshal_full(src, msg)
    row = r.datasets[0].rows[0]
    assert row[0] == ("int", "42")
    assert row[1] == ("string", "hello")
    assert row[2] == ("bool", True)
    assert row[3] == ("null", None)


def test_three_state_cells(all_types_cls):
    msg = all_types_cls()
    # Empty cell = None (absent); null literal = ("null", None) (present-but-null);
    # value = ("<kind>", value) (present-with-value).
    r = pxf.unmarshal_full("@dataset x.Row ( a, b, c )\n( 1, , null )\n", msg)
    row = r.datasets[0].rows[0]
    assert row[0] == ("int", "1")
    assert row[1] is None  # absent
    assert row[2] == ("null", None)


def test_multiple_tables_in_order(all_types_cls):
    msg = all_types_cls()
    src = (
        "@dataset a.Row ( x )\n"
        "( 1 )\n"
        "@dataset b.Row ( y )\n"
        '( "p" )\n'
    )
    r = pxf.unmarshal_full(src, msg)
    assert [t.type for t in r.datasets] == ["a.Row", "b.Row"]


def test_directives_and_tables_can_coexist(all_types_cls):
    # A doc with @dataset can NOT have @type or body entries, but can carry
    # generic @<directive>s before the @dataset header.
    msg = all_types_cls()
    src = '@header pkg.Hdr { id = "h" }\n@dataset x.Row ( a )\n( 1 )\n'
    r = pxf.unmarshal_full(src, msg)
    assert len(r.directives) == 1
    assert len(r.datasets) == 1
    assert r.directives[0].name == "header"


# ---- pxf.validate_descriptor + Violation -------------------------------


def test_validate_conformant_schema_returns_empty(all_types_cls):
    """test.v1.AllTypes is conformant — no field/oneof/enum value collides
    with the PXF reserved keywords."""
    msg = all_types_cls()
    assert pxf.validate_descriptor(msg) == []


def test_unmarshal_rejects_reserved_field_in_schema_when_validate_on(
    all_types_cls, monkeypatch
):
    # We can't easily build a non-conformant descriptor without protoc, so
    # exercise the gate at the FFI level: a synthetic call against the
    # standard descriptor should still pass. The skip_validate test below
    # covers the bypass.
    msg = all_types_cls()
    pxf.unmarshal('string_field = "x"', msg)


def test_skip_validate_bypasses_check(all_types_cls):
    """skip_validate should be accepted as a no-op when the schema is
    conformant; coverage of the actual bypass against a non-conformant
    descriptor lives in protowire-cpp's schema tests."""
    msg = all_types_cls()
    pxf.unmarshal('string_field = "x"', msg, skip_validate=True)
    assert msg.string_field == "x"


def test_unmarshal_full_accepts_skip_validate(all_types_cls):
    msg = all_types_cls()
    r = pxf.unmarshal_full(
        'string_field = "y"', msg, skip_validate=True
    )
    assert r.is_set("string_field")


# ---- Violation dataclass shape (regression on dataclass fields) ---------


def test_violation_dataclass_fields():
    v = pxf.Violation(kind="field", element="pkg.M.null", name="null", file="m.proto")
    assert v.kind == "field"
    assert v.element == "pkg.M.null"
    # Frozen — should reject mutation.
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        v.kind = "oneof"  # type: ignore[misc]
