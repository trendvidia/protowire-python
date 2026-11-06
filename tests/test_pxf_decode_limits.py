# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Per-call decode limits (HARDENING.md § Mandatory limits, #36).

Each limit is a keyword argument passed to protowire-cpp's
pxf::UnmarshalOptions. For each one, a value below the default refuses an
input it would accept, a value at the input's size accepts it again, and a
value above the default accepts an input the default refuses.

max_repeated_count is not raised here: its default is 64 Mi elements, too
many for a unit test.
"""

from __future__ import annotations

import base64

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import pxf

F = descriptor_pb2.FieldDescriptorProto
MiB64 = 64 << 20


def _schema():
    # pxf/bignum.proto, as protowire-cpp ships it.
    bignum = descriptor_pb2.FileDescriptorProto(name="pxf/bignum.proto", package="pxf",
                                                syntax="proto3")
    bi = bignum.message_type.add(name="BigInt")
    bi.field.add(name="abs", number=1, type=F.TYPE_BYTES)
    bi.field.add(name="negative", number=2, type=F.TYPE_BOOL)
    bf = bignum.message_type.add(name="BigFloat")
    bf.field.add(name="mantissa", number=1, type=F.TYPE_BYTES)
    bf.field.add(name="exponent", number=2, type=F.TYPE_INT32)
    bf.field.add(name="prec", number=3, type=F.TYPE_UINT32)
    bf.field.add(name="negative", number=4, type=F.TYPE_BOOL)

    fd = descriptor_pb2.FileDescriptorProto(name="limits.proto", package="lim", syntax="proto3")
    fd.dependency.append("pxf/bignum.proto")
    h = fd.message_type.add(name="H")
    h.field.add(name="s", number=1, type=F.TYPE_STRING)
    h.field.add(name="b", number=2, type=F.TYPE_BYTES)
    h.field.add(name="l", number=3, type=F.TYPE_INT32, label=F.LABEL_REPEATED)
    h.field.add(name="c", number=4, type=F.TYPE_MESSAGE, type_name=".lim.H")
    h.field.add(name="kids", number=5, type=F.TYPE_MESSAGE, label=F.LABEL_REPEATED,
                type_name=".lim.H")
    h.field.add(name="i", number=6, type=F.TYPE_MESSAGE, type_name=".pxf.BigInt")
    h.field.add(name="f", number=7, type=F.TYPE_MESSAGE, type_name=".pxf.BigFloat")

    pool = descriptor_pool.DescriptorPool()
    pool.Add(bignum)
    pool.Add(fd)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("lim.H"))


H = _schema()


def _decode(text: str, **limits) -> None:
    pxf.unmarshal(text, H(), **limits)


def _refused(text: str, limit_name: str, **limits) -> None:
    with pytest.raises(ValueError, match=limit_name):
        _decode(text, **limits)


def _chain(levels: int) -> str:
    """PXF nested `levels` deep through the singular field c."""
    return "c { " * levels + "s = \"x\"" + " }" * levels + "\n"


def _list_chain(elements: int) -> str:
    """PXF nested 2 * `elements` deep (each `[` and `{` is a level), but only
    `elements` protobuf levels, which the protobuf runtime can load (#40)."""
    return "kids = [ { " * elements + "s = \"x\"" + " } ]" * elements + "\n"


def _bytes_doc(n: int) -> str:
    return 'b = b"' + base64.b64encode(b"\0" * n).decode() + '"\n'


# --- max_message_size --------------------------------------------------------


def test_message_size_lowered():
    doc = 's = "' + "a" * 2000 + '"\n'
    _refused(doc, "MaxMessageSize=1024", max_message_size=1024)
    _decode(doc, max_message_size=len(doc))


def test_message_size_raised():
    doc = 's = "' + "a" * MiB64 + '"\n'
    _refused(doc, "MaxMessageSize")
    _decode(doc, max_message_size=len(doc))


# --- max_nesting_depth -------------------------------------------------------


def test_nesting_depth_lowered():
    _refused(_chain(5), "MaxNestingDepth=4", max_nesting_depth=4)
    _decode(_chain(5), max_nesting_depth=5)


def test_nesting_depth_raised():
    doc = _list_chain(60)  # depth 120
    _refused(doc, "MaxNestingDepth")
    _decode(doc, max_nesting_depth=120)
    _refused(doc, "MaxNestingDepth=119", max_nesting_depth=119)


# --- max_numeric_literal_digits ----------------------------------------------


def test_numeric_literal_digits_lowered():
    doc = "i = " + "1" * 10 + "\n"
    _refused(doc, "MaxNumericLiteralDigits", max_numeric_literal_digits=9)
    _decode(doc, max_numeric_literal_digits=10)


def test_numeric_literal_digits_raised():
    doc = "i = " + "1" * 5000 + "\n"
    _refused(doc, "MaxNumericLiteralDigits")
    _decode(doc, max_numeric_literal_digits=5000)


# --- max_bytes_literal_length ------------------------------------------------


def test_bytes_literal_length_lowered():
    doc = _bytes_doc(192)  # unpadded base64: see trendvidia/protowire#588
    _refused(doc, "MaxBytesLiteralLength=191", max_bytes_literal_length=191)
    _decode(doc, max_bytes_literal_length=192)


def test_bytes_literal_length_raised():
    n = MiB64 + 2  # a multiple of 3, so its base64 has no padding (protowire#588)
    doc = _bytes_doc(n)
    _refused(doc, "MaxBytesLiteralLength", max_message_size=2 * MiB64)
    _decode(doc, max_message_size=2 * MiB64, max_bytes_literal_length=n)


# --- max_repeated_count ------------------------------------------------------


def test_repeated_count_lowered():
    doc = "l = [" + ", ".join(["1"] * 16) + "]\n"
    _refused(doc, "MaxRepeatedCount=15", max_repeated_count=15)
    _decode(doc, max_repeated_count=16)


# --- max_big_float_exponent --------------------------------------------------


def test_big_float_exponent_lowered():
    doc = "f = 1e100\n"  # binary exponent about 333
    _refused(doc, "exponent", max_big_float_exponent=300)
    _decode(doc, max_big_float_exponent=400)


def test_big_float_exponent_raised():
    doc = "f = 1e5000\n"  # binary exponent about 16610, past the default 16384
    _refused(doc, "exponent")
    _decode(doc, max_big_float_exponent=17000)


# --- every entry point --------------------------------------------------------


def _fds() -> bytes:
    fds = descriptor_pb2.FileDescriptorSet()
    for name in ("pxf/bignum.proto", "limits.proto"):
        fds.file.add().MergeFromString(H.DESCRIPTOR.file.pool.FindFileByName(name).serialized_pb)
    return fds.SerializeToString()


_DOC = 's = "' + "x" * 40 + '"\n'
_ENTRY_POINTS = {
    "unmarshal": lambda **k: pxf.unmarshal(_DOC, H(), **k),
    "unmarshal_full": lambda **k: pxf.unmarshal_full(_DOC, H(), **k),
    "unmarshal_bytes": lambda **k: pxf.unmarshal_bytes(_DOC, _fds(), "lim.H", **k),
    "unmarshal_full_bytes": lambda **k: pxf.unmarshal_full_bytes(_DOC, _fds(), "lim.H", **k),
    # The synthetic body bind_row decodes is the same 47 bytes.
    "bind_row": lambda **k: pxf.bind_row(H(), ["s"], [("string", "x" * 40)], **k),
}


@pytest.mark.parametrize("name", list(_ENTRY_POINTS))
def test_each_entry_point_takes_the_limits(name):
    decode = _ENTRY_POINTS[name]
    with pytest.raises(ValueError, match="MaxMessageSize=16"):
        decode(max_message_size=16)
    decode(max_message_size=64)


@pytest.mark.parametrize("name", [
    "max_message_size", "max_nesting_depth", "max_numeric_literal_digits",
    "max_bytes_literal_length", "max_repeated_count", "max_big_float_exponent",
])
def test_a_negative_limit_is_an_error(name):
    with pytest.raises(ValueError, match=f"{name} must not be negative"):
        _decode('s = "x"\n', **{name: -1})


# --- DatasetReader -------------------------------------------------------------


_BYTES_ROW = "@dataset lim.H (b)\n(" + _bytes_doc(192).split(" = ", 1)[1].strip() + ")\n"


@pytest.mark.xfail(strict=True, reason="protowire-cpp#160: rows are read under the default "
                   "MaxBytesLiteralLength")
def test_dataset_reader_reads_rows_under_its_limits():
    reader = pxf.DatasetReader.from_bytes(_BYTES_ROW, max_bytes_literal_length=191)
    with pytest.raises(ValueError, match="MaxBytesLiteralLength=191"):
        reader.next_or_none()


def test_scan_refuses_a_cell_past_the_readers_limit():
    # Until protowire-cpp#160, the reader returns the row and binding refuses it.
    with pytest.raises(ValueError, match="MaxBytesLiteralLength=191"):
        pxf.DatasetReader.from_bytes(_BYTES_ROW, max_bytes_literal_length=191).scan(H())
    msg = H()
    assert pxf.DatasetReader.from_bytes(_BYTES_ROW, max_bytes_literal_length=192).scan(msg)
    assert len(msg.b) == 192


def test_scan_binds_under_the_readers_limits():
    # The reader does not know the column is a BigInt, so only binding checks
    # the digits. Under the default of 4096 the cell would be refused.
    digits = "1" * 5000
    data = f"@dataset lim.H (i)\n({digits})\n"
    with pytest.raises(ValueError, match="MaxNumericLiteralDigits"):
        pxf.DatasetReader.from_bytes(data).scan(H())
    msg = H()
    assert pxf.DatasetReader.from_bytes(data, max_numeric_literal_digits=5000).scan(msg)
    assert msg.HasField("i")
