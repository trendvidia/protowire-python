# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Tests for pxf.DatasetReader (streaming @dataset consumption) and pxf.bind_row
(per-row proto binding). PR 2 of the Python v0.72-v0.75 catch-up."""

from __future__ import annotations

import pytest

from protowire import pxf


# ---- DatasetReader.from_bytes header parsing -------------------------------


def test_reads_header_and_exposes_type_and_columns():
    src = b"@dataset trades.v1.Trade ( px, qty )\n( 100, 5 )\n( 101, 7 )\n"
    tr = pxf.DatasetReader.from_bytes(src)
    assert tr.type == "trades.v1.Trade"
    assert tr.columns == ("px", "qty")
    assert tr.directives == ()


def test_accepts_str_input():
    tr = pxf.DatasetReader.from_bytes("@dataset x.Row ( a )\n( 1 )\n")
    assert tr.type == "x.Row"


def test_no_table_raises():
    with pytest.raises(ValueError, match="no @dataset"):
        pxf.DatasetReader.from_bytes(b"@type foo.Msg\nname = \"x\"\n")


def test_empty_input_raises():
    with pytest.raises(ValueError):
        pxf.DatasetReader.from_bytes(b"")


def test_leading_directives_preserved():
    src = b'''@header pkg.Hdr { id = "h" }
@frob alpha
@dataset trades.v1.Trade ( px, qty )
( 1, 2 )
'''
    tr = pxf.DatasetReader.from_bytes(src)
    assert len(tr.directives) == 2
    assert tr.directives[0].name == "header"
    assert tr.directives[1].name == "frob"
    assert tr.directives[0].body == b' id = "h" '


def test_header_oversize_rejected():
    # >64 KiB of leading directive bytes before any @dataset.
    big = b"@frob " + (b"x " * 35000) + b"\n@dataset x.Row ( a )\n"
    with pytest.raises(ValueError, match="header exceeds"):
        pxf.DatasetReader.from_bytes(big)


# ---- iteration ----------------------------------------------------------


def test_iter_yields_rows_in_order():
    src = b"@dataset x.Row ( a, b )\n( 1, 2 )\n( 3, 4 )\n( 5, 6 )\n"
    tr = pxf.DatasetReader.from_bytes(src)
    rows = list(tr)
    assert rows == [
        (("int", "1"), ("int", "2")),
        (("int", "3"), ("int", "4")),
        (("int", "5"), ("int", "6")),
    ]
    assert tr.done


def test_zero_rows_immediately_stops():
    tr = pxf.DatasetReader.from_bytes(b"@dataset x.Row ( a )\n")
    rows = list(tr)
    assert rows == []
    assert tr.done


def test_next_or_none_returns_none_at_eof():
    tr = pxf.DatasetReader.from_bytes(b"@dataset x.Row ( a )\n( 1 )\n")
    first = tr.next_or_none()
    assert first == (("int", "1"),)
    assert tr.next_or_none() is None
    assert tr.done


def test_cell_shapes_match_three_state_grammar():
    src = b"""@dataset x.Row ( a, b, c, d, e )
( 42, "hi", true, null, )
"""
    tr = pxf.DatasetReader.from_bytes(src)
    (row,) = list(tr)
    assert row[0] == ("int", "42")
    assert row[1] == ("string", "hi")
    assert row[2] == ("bool", True)
    assert row[3] == ("null", None)
    assert row[4] is None  # absent (empty cell at end)


def test_arity_mismatch_raises():
    src = b"@dataset x.Row ( a, b )\n( 1, 2, 3 )\n"
    tr = pxf.DatasetReader.from_bytes(src)
    with pytest.raises(ValueError, match="3 cells, expected 2"):
        next(iter(tr))


def test_parens_inside_string_not_row_boundary():
    src = b'@dataset x.Row ( a )\n( "hi ) there" )\n( "next" )\n'
    tr = pxf.DatasetReader.from_bytes(src)
    rows = list(tr)
    assert rows == [
        (("string", "hi ) there"),),
        (("string", "next"),),
    ]


def test_comments_between_rows_ignored():
    src = b"""@dataset x.Row ( a )
# leading
( 1 )
// mid
( 2 )
/* block
  comment */
( 3 )
"""
    tr = pxf.DatasetReader.from_bytes(src)
    assert len(list(tr)) == 3


# ---- tail() chaining -----------------------------------------------------


def test_tail_chains_to_second_table():
    src = b"""@dataset a.Row ( x )
( 1 )
( 2 )
@dataset b.Row ( y )
( "p" )
( "q" )
"""
    tr1 = pxf.DatasetReader.from_bytes(src)
    assert tr1.type == "a.Row"
    list(tr1)  # drain
    tr2 = pxf.DatasetReader.from_bytes(tr1.tail())
    assert tr2.type == "b.Row"
    rows = list(tr2)
    assert rows == [
        (("string", "p"),),
        (("string", "q"),),
    ]


# ---- bind_row + scan -----------------------------------------------------


def test_bind_row_sets_fields_by_column(all_types_cls):
    src = b'@dataset test.v1.AllTypes ( string_field, int32_field )\n( "alpha", 42 )\n'
    tr = pxf.DatasetReader.from_bytes(src)
    (row,) = list(tr)
    msg = all_types_cls()
    pxf.bind_row(msg, tr.columns, row)
    assert msg.string_field == "alpha"
    assert msg.int32_field == 42


def test_scan_equivalent_to_next_plus_bind(all_types_cls):
    src = b'@dataset test.v1.AllTypes ( string_field )\n( "row1" )\n( "row2" )\n'
    tr = pxf.DatasetReader.from_bytes(src)
    seen = []
    while True:
        msg = all_types_cls()
        ok = tr.scan(msg)
        if not ok:
            break
        seen.append(msg.string_field)
    assert seen == ["row1", "row2"]


def test_bind_row_absent_cell_leaves_default(all_types_cls):
    # proto3 string default is ""; absent cell shouldn't stamp a value.
    src = b'@dataset test.v1.AllTypes ( string_field, int32_field )\n( , 7 )\n'
    tr = pxf.DatasetReader.from_bytes(src)
    (row,) = list(tr)
    msg = all_types_cls()
    pxf.bind_row(msg, tr.columns, row)
    assert msg.string_field == ""
    assert msg.int32_field == 7


def test_bind_row_null_clears_wrapper(all_types_cls):
    # A `null` cell on a wrapper field clears it (draft §3.9).
    src = b'@dataset test.v1.AllTypes ( nullable_string )\n( null )\n'
    tr = pxf.DatasetReader.from_bytes(src)
    (row,) = list(tr)
    msg = all_types_cls()
    msg.nullable_string.value = "stale"  # populate to confirm clear
    assert msg.HasField("nullable_string")
    pxf.bind_row(msg, tr.columns, row)
    # nullable_string is a StringValue — after `null`, HasField is False.
    assert not msg.HasField("nullable_string")


def test_bind_row_bytes_cell(all_types_cls):
    src = b'@dataset test.v1.AllTypes ( bytes_field )\n( b"YWJj" )\n'  # "abc"
    tr = pxf.DatasetReader.from_bytes(src)
    (row,) = list(tr)
    msg = all_types_cls()
    pxf.bind_row(msg, tr.columns, row)
    assert msg.bytes_field == b"abc"


def test_bind_row_mismatched_columns_errors(all_types_cls):
    msg = all_types_cls()
    with pytest.raises(ValueError, match="1 columns vs 2 cells"):
        pxf.bind_row(msg, ("string_field",), (None, None))


def test_bind_row_unknown_column_errors(all_types_cls):
    msg = all_types_cls()
    with pytest.raises(ValueError):
        pxf.bind_row(msg, ("not_a_field",), (("int", "1"),))


def test_bind_row_string_escape(all_types_cls):
    # String values containing quotes and backslashes must round-trip
    # via the synthetic body formatter.
    src = b'@dataset test.v1.AllTypes ( string_field )\n( "she said \\"hi\\"" )\n'
    tr = pxf.DatasetReader.from_bytes(src)
    (row,) = list(tr)
    msg = all_types_cls()
    pxf.bind_row(msg, tr.columns, row)
    assert msg.string_field == 'she said "hi"'
