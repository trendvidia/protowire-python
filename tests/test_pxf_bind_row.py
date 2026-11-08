# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""pxf.bind_row and DatasetReader.scan bind a row through protowire-cpp's
BindRow, as protowire-go binds it (#24).

Expected messages and errors are protowire-go v1.17.0's, from its
DatasetReader.Scan on the same input, each message as its deterministic
binary encoding.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import pxf

# --- value cells and column paths: spec fixtures (testdata/dataset/) ----------
#
# Copied verbatim from trendvidia/protowire (CI has no checkout of it). Built
# on the earlier bind_row, which wrote each cell back as text, every one of
# these but null-on-scalar-cell failed: a list or block cell was refused as
# kind 'unknown', and a dotted column as an unknown field.

_FIXTURES = {
    "list-cell": (
        '# §3.4.4 value cells. A list in a cell on a repeated column. MUST be\n'
        '# accepted: a cell means what `repeated_string = ["alpha", "beta"]` means\n'
        '# in a body. Rejected before v1.13, when cells were scalar-only.\n'
        '\n'
        '@dataset test.v1.AllTypes (string_field, repeated_string)\n'
        '\n'
        '("row-1", ["alpha", "beta"])\n'
    ),
    "block-cell": (
        '# §3.4.4 value cells. A block in a cell on a message column. MUST be\n'
        '# accepted: a cell means what `nested_field = { name = "inner" value = 99 }`\n'
        '# means in a body. Rejected before v1.13, when cells were scalar-only.\n'
        '\n'
        '@dataset test.v1.AllTypes (string_field, nested_field)\n'
        '\n'
        '("row-1", { name = "inner" value = 99 })\n'
    ),
    "nested-cells": (
        '# §3.4.4 value cells. MUST be accepted. Each row binds to the message its\n'
        '# body would: a non-empty cell in column c means `c = <value>`.\n'
        '#\n'
        '#   row-1: nested_field {name "n1" value 1}; repeated_string [a, b];\n'
        '#          repeated_nested [{x}, {y 2}]; string_map {k: v}; int32_field 1\n'
        '#   row-2: nested_field present and empty ({}); repeated_string empty ([]);\n'
        '#          repeated_nested and string_map absent (empty cells); int32_field 2\n'
        '#   row-3: every nested column absent; int32_field 3\n'
        '#   row-4: nested_field null (present-but-null, as `nested_field = null`\n'
        '#          is in a body); the other nested columns absent; int32_field 4\n'
        '\n'
        '@dataset test.v1.AllTypes (string_field, nested_field, repeated_string, repeated_nested, string_map, int32_field)\n'
        '\n'
        '("row-1", { name = "n1" value = 1 }, ["a", "b"], [{ name = "x" }, { name = "y" value = 2 }], { "k": "v" }, 1)\n'
        '("row-2", {},                        [],         ,                                          ,             2)\n'
        '("row-3", ,                          ,           ,                                          ,             3)\n'
        '("row-4", null,                      ,           ,                                          ,             4)\n'
    ),
    "cell-framing": (
        '# §3.4.4 value cells. Row framing. MUST be accepted: two rows. A ")" or ","\n'
        '# inside a string, a triple-quoted string or a comment within a block cell\n'
        '# neither ends the row nor splits the cell. A streaming reader finds the\n'
        '# row by matching parentheses outside strings and comments, so it MUST\n'
        '# yield the same two rows as a materializing parse.\n'
        '\n'
        '@dataset test.v1.AllTypes (string_field, nested_field, repeated_string)\n'
        '\n'
        '("row-1", { name = "a), (b," value = 1 }, ["),", "(,"])\n'
        '("row-2", { name = """x) "y", (z""" /* ), ( */ value = 2 }, [ # ) ,\n'
        '  "end" ])\n'
    ),
    "dotted-column": (
        '# §3.4.4 column paths. A column may be a dotted path into a sub-message.\n'
        '# MUST be accepted. The row binds as\n'
        '#   string_field = "row-1" nested_field { name = "inner" }\n'
        '# Before v1.13 a dotted column was rejected.\n'
        '\n'
        '@dataset test.v1.AllTypes (string_field, nested_field.name)\n'
        '\n'
        '("row-1", "inner")\n'
    ),
    "block-on-scalar": (
        '# §3.4.4 value cells. A block in a cell on a scalar column. MUST be\n'
        '# rejected, binding the row: the cell means `int32_field = { value = 1 }`,\n'
        '# and a block on a scalar field is an error in a body too.\n'
        '\n'
        '@dataset test.v1.AllTypes (string_field, int32_field)\n'
        '\n'
        '("row-1", { value = 1 })\n'
    ),
    "null-on-scalar-cell": (
        '# §3.4.4 + § Booleans, Null, and Identifier Values (#339). Row type\n'
        '# test.v1.AllTypes. MUST be rejected: a cell applies the singular-field\n'
        '# null rules, and int32_field is a plain scalar.\n'
        '\n'
        '@dataset test.v1.AllTypes (string_field, int32_field)\n'
        '\n'
        '("a", 1)\n'
        '("b", null)\n'
    ),
}

_ACCEPT = {
    "list-cell": ["0a05726f772d316205616c706861620462657461"],
    "block-cell": ["0a05726f772d315a090a05696e6e65721063"],
    "nested-cells": [
        "0a05726f772d3110015a060a026e3110016201616201626a030a01786a050a0179100272060a016b120176",
        "0a05726f772d3210025a00",
        "0a05726f772d331003",
        "0a05726f772d341004",
    ],
    "cell-framing": [
        "0a05726f772d315a0b0a0761292c2028622c10016202292c6202282c",
        "0a05726f772d325a0e0a0a7829202279222c20287a10026203656e64",
    ],
    "dotted-column": ["0a05726f772d315a070a05696e6e6572"],
}

_REJECT = {
    "block-on-scalar": ([], '7:1: column int32_field: expected integer for field "int32_field"'),
    "null-on-scalar-cell": (
        ["0a01611001"],
        '8:1: column int32_field: null is not allowed on scalar field "int32_field": declare it '
        "optional, or as a wrapper type (google.protobuf.Int32Value or similar), to make it "
        "nullable",
    ),
}


@pytest.mark.parametrize("fixture", sorted(_ACCEPT))
def test_scan_binds_value_cells_and_paths_as_go(all_types_cls, fixture):
    reader = pxf.DatasetReader.from_bytes(_FIXTURES[fixture])
    got = []
    msg = all_types_cls()
    while reader.scan(msg):
        got.append(msg.SerializeToString(deterministic=True).hex())
        msg = all_types_cls()
    assert got == _ACCEPT[fixture]


@pytest.mark.parametrize("fixture", sorted(_REJECT))
def test_scan_refuses_a_row_at_its_position(all_types_cls, fixture):
    rows, error = _REJECT[fixture]
    reader = pxf.DatasetReader.from_bytes(_FIXTURES[fixture])
    for want in rows:
        msg = all_types_cls()
        assert reader.scan(msg)
        assert msg.SerializeToString(deterministic=True).hex() == want
    with pytest.raises(ValueError, match=re.escape(error)):
        reader.scan(all_types_cls())


def test_bind_row_takes_a_dotted_column(all_types_cls):
    msg = all_types_cls()
    pxf.bind_row(msg, ("string_field", "nested_field.name"),
                 (("string", "row-1"), ("string", "inner")))
    assert msg.SerializeToString(deterministic=True).hex() == _ACCEPT["dotted-column"][0]


# --- (pxf.required) and (pxf.default) on an absent cell (#24) -----------------
#
# The schema is built from raw option bytes at the registered numbers, as in
# test_pxf_required_default.py. Go v1.17.0 streams this input as: row 1
# f "a", g "x" (the default), h "z"; row 2 `3:1: required field "f" is absent`.

_REQUIRED, _DEFAULT = 1314, 1315
_STRING = descriptor_pb2.FieldDescriptorProto.TYPE_STRING


def _varint(n: int) -> bytes:
    out = bytearray()
    while n > 0x7F:
        out.append(n & 0x7F | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def _schema(*fields):
    fd = descriptor_pb2.FileDescriptorProto(name="rd.proto", package="t", syntax="proto3")
    m = fd.message_type.add(name="M")
    for number, (name, options) in enumerate(fields, 1):
        f = m.field.add(name=name, number=number, type=_STRING)
        if options:
            f.options.MergeFromString(options)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fd)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("t.M"))


REQUIRED = _varint(_REQUIRED << 3) + _varint(1)
DEFAULT = _varint(_DEFAULT << 3 | 2) + _varint(1) + b"x"
M = _schema(("f", REQUIRED), ("g", DEFAULT), ("h", b""))
_ROWS = '@dataset t.M (f, g, h)\n("a", , "z")\n( , , "z")\n'


def test_an_absent_cell_takes_its_default():
    msg = M()
    pxf.bind_row(msg, ("f", "g", "h"), (("string", "a"), None, ("string", "z")))
    assert (msg.f, msg.g, msg.h) == ("a", "x", "z")
    msg = M()
    assert pxf.DatasetReader.from_bytes(_ROWS).scan(msg)
    assert msg.SerializeToString(deterministic=True).hex() == "0a01611201781a017a"


def test_an_absent_required_cell_is_an_error():
    with pytest.raises(ValueError, match='required field "f" is absent'):
        pxf.bind_row(M(), ("f", "g", "h"), (None, None, ("string", "z")))
    reader = pxf.DatasetReader.from_bytes(_ROWS)
    assert reader.scan(M())
    with pytest.raises(ValueError, match=re.escape('3:1: required field "f" is absent')):
        reader.scan(M())


def test_the_schema_is_checked_unless_skip_validate():
    # (pxf.required) with (pxf.default) on one field breaks a bind-time rule.
    # Go validates by default, and so do bind_row and scan now.
    bad = _schema(("f", REQUIRED + DEFAULT))
    row = (("string", "y"),)
    with pytest.raises(ValueError, match="PXF schema violations"):
        pxf.bind_row(bad(), ("f",), row)
    with pytest.raises(ValueError, match="PXF schema violations"):
        pxf.DatasetReader.from_bytes('@dataset t.M (f)\n("y")\n').scan(bad())
    msg = bad()
    pxf.bind_row(msg, ("f",), row, skip_validate=True)
    assert msg.f == "y"


def test_discard_unknown_reaches_the_row(all_types_cls):
    # An unknown field inside a block cell. Go v1.17.0: refused as
    # `2:1: column nested_field: unknown field "nope" in test.v1.Nested`,
    # and with DiscardUnknown bound as below.
    src = '@dataset test.v1.AllTypes (string_field, nested_field)\n("a", { name = "n" nope = 1 })\n'
    with pytest.raises(ValueError, match=re.escape(
            '2:1: column nested_field: unknown field "nope" in test.v1.Nested')):
        pxf.DatasetReader.from_bytes(src).scan(all_types_cls())
    msg = all_types_cls()
    assert pxf.DatasetReader.from_bytes(src).scan(msg, discard_unknown=True)
    assert msg.SerializeToString(deterministic=True).hex() == "0a01615a030a016e"

# --- every spec dataset fixture, against protowire-go -------------------------
#
# testdata/dataset-scan.go.txt is what protowire-go v1.17.0 gives for each
# fixture in the spec's testdata/dataset/ (protowire dd8e221), streamed through
# DatasetReader.Scan into a fresh message per row: each row's message as hex,
# or the error. This runs where the spec repository is checked out beside this
# one; CI has no checkout, and the tests above copy the fixtures they need.

SPEC = Path(__file__).parent.parent / ".." / "protowire"
GOLDEN = Path(__file__).parent.parent / "testdata" / "dataset-scan.go.txt"


def _stream_all(pool) -> str:
    out = []
    for p in sorted((SPEC / "testdata" / "dataset").glob("*.pxf")):
        out.append(f"== {p.name}")
        data = p.read_bytes()
        while True:
            try:
                reader = pxf.DatasetReader.from_bytes(data)
            except ValueError as e:
                out.append(f"  header err: {str(e).removeprefix('pxf.DatasetReader: ')}")
                break
            cls = message_factory.GetMessageClass(pool.FindMessageTypeByName(reader.type))
            n, failed = 0, False
            while True:
                msg = cls()
                try:
                    if not reader.scan(msg):
                        break
                except ValueError as e:
                    out.append(f"  row {n + 1} err: {str(e).removeprefix('pxf.DatasetReader.scan: ')}")
                    failed = True
                    break
                n += 1
                out.append(f"  row {n}: {msg.SerializeToString(deterministic=True).hex()}")
            if failed:
                break
            out.append(f"  table {reader.type}: {n} rows")
            data = reader.tail()
            if not data.strip():
                break
    return "\n".join(out) + "\n"


def test_every_spec_dataset_fixture_streams_as_go():
    if not (SPEC / "testdata" / "dataset").is_dir():
        pytest.skip(f"spec repo testdata not found at {SPEC}")
    from conftest import _protoc_to_fds

    fds = descriptor_pb2.FileDescriptorSet.FromString(_protoc_to_fds(
        ["test.proto", "paths.proto"],
        [str(SPEC / "testdata"), str(SPEC / "testdata" / "dataset"), str(SPEC / "proto")],
    ))
    pool = descriptor_pool.DescriptorPool()
    for f in fds.file:
        pool.Add(f)
    assert _stream_all(pool) == GOLDEN.read_text()


# --- a row read as cells binds as scan binds it (#47) -------------------------
#
# Cells keep what protowire-go's DatasetRow keeps: a list's elements, a
# block's entries, a number's precision suffix. So a row read with
# next_or_none and passed to bind_row gives scan's message, which is Go's.

_CELL_FIXTURES = ("list-cell", "block-cell", "nested-cells", "cell-framing")


@pytest.mark.parametrize("fixture", _CELL_FIXTURES)
def test_a_row_read_as_cells_binds_as_scan_binds_it(all_types_cls, fixture):
    reader = pxf.DatasetReader.from_bytes(_FIXTURES[fixture])
    got = []
    for row in reader:
        msg = all_types_cls()
        pxf.bind_row(msg, reader.columns, row)
        got.append(msg.SerializeToString(deterministic=True).hex())
    assert got == _ACCEPT[fixture]


def test_list_and_block_cells_keep_their_parts():
    row = pxf.DatasetReader.from_bytes(
        '@dataset x.R (a, b)\n'
        '([1, "s", [2]], { @type = "t/x" k = 1 m { n = 2 } "q": 3 r: null })\n'
    ).next_or_none()
    assert row == (
        ("list", [("int", "1"), ("string", "s"), ("list", [("int", "2")])]),
        ("block", [
            ("type", "t/x", None, False),
            ("field", "k", ("int", "1"), False),
            ("block", "m", [("field", "n", ("int", "2"), False)], False),
            ("map", "q", ("int", "3"), True),
            ("map", "r", ("null", None), False),
        ]),
    )


def _bigfloat_holder():
    F = descriptor_pb2.FieldDescriptorProto
    bignum = descriptor_pb2.FileDescriptorProto(name="pxf/bignum.proto", package="pxf",
                                                syntax="proto3")
    bf = bignum.message_type.add(name="BigFloat")
    bf.field.add(name="mantissa", number=1, type=F.TYPE_BYTES)
    bf.field.add(name="exponent", number=2, type=F.TYPE_INT32)
    bf.field.add(name="prec", number=3, type=F.TYPE_UINT32)
    bf.field.add(name="negative", number=4, type=F.TYPE_BOOL)
    fd = descriptor_pb2.FileDescriptorProto(name="bf.proto", package="bf", syntax="proto3")
    fd.dependency.append("pxf/bignum.proto")
    h = fd.message_type.add(name="H")
    h.field.add(name="f", number=1, type=F.TYPE_MESSAGE, type_name=".pxf.BigFloat")
    pool = descriptor_pool.DescriptorPool()
    pool.Add(bignum)
    pool.Add(fd)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("bf.H"))


def test_a_precision_suffix_survives_the_cell():
    holder = _bigfloat_holder()
    src = "@dataset bf.H (f)\n(1.5p80)\n"
    reader = pxf.DatasetReader.from_bytes(src)
    row = reader.next_or_none()
    assert row == (("float", "1.5p80"),)
    from_cells = holder()
    pxf.bind_row(from_cells, reader.columns, row)
    scanned = holder()
    assert pxf.DatasetReader.from_bytes(src).scan(scanned)
    assert from_cells.f.prec == 80
    assert from_cells.SerializeToString(deterministic=True) == \
        scanned.SerializeToString(deterministic=True)


def test_a_list_element_cannot_be_absent(all_types_cls):
    with pytest.raises(ValueError, match="list element cannot be absent"):
        pxf.bind_row(all_types_cls(), ("repeated_string",), (("list", [None]),))
