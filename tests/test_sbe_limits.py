# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""sbe.Codec's decode limits (HARDENING.md § Mandatory limits, #36).

protowire-cpp sets them per codec, in sbe::CodecOptions, and so does this
port. They are tested lowered only. A group's entry count is a uint16 on the
wire, far below the default of 64 Mi, and these messages are a few dozen
bytes, against a default of 64 MiB.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import sbe
from test_sbe import CPP_PROTO_DIR

BOOK_PROTO_SRC = """
syntax = "proto3";
package sbe.limits;

import "sbe/annotations.proto";

option (sbe.schema_id) = 3;
option (sbe.version) = 0;

message Book {
  option (sbe.template_id) = 1;
  uint64 id = 1;

  message Level {
    int64  price = 1;
    uint32 qty   = 2;
  }
  repeated Level levels = 2;
}
"""


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    if not CPP_PROTO_DIR.exists():
        pytest.skip(f"protowire-cpp proto/ tree not found at {CPP_PROTO_DIR}")
    protoc = shutil.which("protoc")
    if not protoc:
        pytest.skip("protoc not available")
    tmp = tmp_path_factory.mktemp("book")
    (tmp / "book.proto").write_text(BOOK_PROTO_SRC)
    out = tmp / "book.fds"
    res = subprocess.run(
        [protoc, "-I", str(CPP_PROTO_DIR), "-I", str(tmp), "--include_imports",
         f"--descriptor_set_out={out}", "book.proto"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"protoc failed: {res.stderr}")
    fds = descriptor_pb2.FileDescriptorSet.FromString(out.read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for f in fds.file:
        pool.Add(f)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("sbe.limits.Book"))


def _encoded(Book, levels: int) -> bytes:
    msg = Book(id=1)
    for i in range(levels):
        msg.levels.add(price=i, qty=1)
    return sbe.Codec.from_message(Book).marshal(msg)


def test_message_size(book):
    data = _encoded(book, 2)
    with pytest.raises(ValueError, match="MaxMessageSize"):
        sbe.Codec.from_message(book, max_message_size=len(data) - 1).unmarshal(data, book())
    with pytest.raises(ValueError, match="MaxMessageSize"):
        sbe.Codec.from_message(book, max_message_size=len(data) - 1).view(data)
    got = book()
    sbe.Codec.from_message(book, max_message_size=len(data)).unmarshal(data, got)
    assert len(got.levels) == 2


def test_repeated_count(book):
    data = _encoded(book, 16)
    with pytest.raises(ValueError, match="MaxRepeatedCount=8"):
        sbe.Codec.from_message(book, max_repeated_count=8).unmarshal(data, book())
    got = book()
    sbe.Codec.from_message(book, max_repeated_count=16).unmarshal(data, got)
    assert len(got.levels) == 16


@pytest.mark.parametrize("name", ["max_message_size", "max_repeated_count"])
def test_a_negative_limit_is_an_error(book, name):
    with pytest.raises(ValueError, match=f"{name} must not be negative"):
        sbe.Codec.from_message(book, **{name: -1})
