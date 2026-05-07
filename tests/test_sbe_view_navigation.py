# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Tests for the SBE View navigation API: composite() / group() / entry() / bytes()."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import sbe

# Spec-repo paths for the canonical bench fixture, which has a repeating group.
SPEC_TESTDATA = (
    Path(__file__).parent.parent / ".." / "protowire" / "testdata"
).resolve()
SPEC_PROTO_DIR = (
    Path(__file__).parent.parent / ".." / "protowire" / "proto"
).resolve()


@pytest.fixture(scope="module")
def bench_order_schema():
    """Compile bench.v1.Order from the spec repo's canonical fixture."""
    if not SPEC_TESTDATA.exists():
        pytest.skip(f"spec repo testdata not found at {SPEC_TESTDATA}")
    if not SPEC_PROTO_DIR.exists():
        pytest.skip(f"spec repo proto/ tree not found at {SPEC_PROTO_DIR}")
    protoc = shutil.which("protoc")
    if not protoc:
        pytest.skip("protoc not available")

    out = Path(__file__).parent / ".pytest_bench_order.fds"
    cmd = [
        protoc,
        "-I", str(SPEC_PROTO_DIR),
        "-I", str(SPEC_TESTDATA),
        "--include_imports",
        f"--descriptor_set_out={out}",
        "sbe-bench.proto",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        pytest.fail(f"protoc failed: {res.stderr}")
    fds_bytes = out.read_bytes()
    out.unlink(missing_ok=True)

    pool = descriptor_pool.DescriptorPool()
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(fds_bytes)
    for f in fds.file:
        pool.Add(f)
    desc = pool.FindMessageTypeByName("bench.v1.Order")
    OrderCls = message_factory.GetMessageClass(desc)
    file_desc = pool.FindFileByName("sbe-bench.proto")
    return OrderCls, file_desc


def test_view_group_iteration(bench_order_schema):
    """Build an Order with two Fills, then read them back via View.group()."""
    OrderCls, file_desc = bench_order_schema
    codec = sbe.Codec([file_desc])

    Fill = OrderCls.Fill
    msg = OrderCls(
        order_id=42,
        symbol="ETH",
        price=10000,
        quantity=5,
        side=1,  # SELL
        active=True,
        weight=1.5,
        score=0.5,
        fills=[
            Fill(fill_price=9999, fill_qty=2, fill_id=100),
            Fill(fill_price=10001, fill_qty=3, fill_id=101),
        ],
    )
    data = codec.marshal(msg)

    view = codec.view(data)
    fills = view.group("fills")
    assert len(fills) == 2

    e0 = fills.entry(0)
    assert e0.int("fill_price") == 9999
    assert e0.uint("fill_qty") == 2
    assert e0.uint("fill_id") == 100

    e1 = fills.entry(1)
    assert e1.int("fill_price") == 10001
    assert e1.uint("fill_qty") == 3
    assert e1.uint("fill_id") == 101


def test_view_group_empty(bench_order_schema):
    """A group with zero entries reports len 0."""
    OrderCls, file_desc = bench_order_schema
    codec = sbe.Codec([file_desc])
    msg = OrderCls(order_id=1, symbol="X", price=1, quantity=1, side=0)
    data = codec.marshal(msg)

    view = codec.view(data)
    assert len(view.group("fills")) == 0


def test_view_group_entry_index_out_of_range(bench_order_schema):
    """entry(i) raises IndexError for i >= len."""
    OrderCls, file_desc = bench_order_schema
    codec = sbe.Codec([file_desc])
    msg = OrderCls(order_id=1, symbol="X", price=1, quantity=1, side=0)
    data = codec.marshal(msg)

    view = codec.view(data)
    fills = view.group("fills")
    with pytest.raises(IndexError):
        fills.entry(0)


# --- composite + bytes coverage ------------------------------------------
#
# bench.v1.Order has groups but no composite or fixed-size bytes field. The
# fixture below builds a Trade schema at runtime that exercises both, mirroring
# the C++ test at protowire-cpp/test/sbe_navigation_test.cc.

TRADE_PROTO_SRC = """
syntax = "proto3";
package sbe.test;

import "sbe/annotations.proto";

option (sbe.schema_id) = 2;
option (sbe.version) = 0;

message Price {
  int64 mantissa = 1;
  int32 exponent = 2;
}

message Trade {
  option (sbe.template_id) = 10;
  Price price = 1;
  uint64 qty = 2;
  bytes signature = 3 [(sbe.length) = 4];

  message Fill {
    int64  fill_price = 1;
    uint32 fill_qty   = 2;
  }
  repeated Fill fills = 4;
}
"""


@pytest.fixture(scope="module")
def trade_schema(tmp_path_factory):
    """Compile the Trade fixture at runtime via protoc."""
    if not SPEC_PROTO_DIR.exists():
        pytest.skip(f"spec repo proto/ tree not found at {SPEC_PROTO_DIR}")
    protoc = shutil.which("protoc")
    if not protoc:
        pytest.skip("protoc not available")

    tmp = tmp_path_factory.mktemp("trade")
    proto_path = tmp / "trade.proto"
    proto_path.write_text(TRADE_PROTO_SRC)

    out = tmp / "trade.fds"
    cmd = [
        protoc,
        "-I", str(SPEC_PROTO_DIR),
        "-I", str(tmp),
        "--include_imports",
        f"--descriptor_set_out={out}",
        "trade.proto",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        pytest.fail(f"protoc failed: {res.stderr}")
    fds_bytes = out.read_bytes()

    pool = descriptor_pool.DescriptorPool()
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(fds_bytes)
    for f in fds.file:
        pool.Add(f)
    desc = pool.FindMessageTypeByName("sbe.test.Trade")
    TradeCls = message_factory.GetMessageClass(desc)
    file_desc = pool.FindFileByName("trade.proto")
    return TradeCls, file_desc


def test_view_composite(trade_schema):
    """view.composite('price') reads fields of the inlined nested Price."""
    TradeCls, file_desc = trade_schema
    codec = sbe.Codec([file_desc])

    Price = TradeCls.DESCRIPTOR.fields_by_name["price"].message_type._concrete_class
    trade = TradeCls(price=Price(mantissa=12345, exponent=-2), qty=100)
    data = codec.marshal(trade)

    view = codec.view(data)
    assert view.uint("qty") == 100

    price = view.composite("price")
    assert price.int("mantissa") == 12345
    assert price.int("exponent") == -2


def test_view_bytes_full_slice(trade_schema):
    """view.bytes('signature') returns the full 4-byte fixed slice without trim."""
    TradeCls, file_desc = trade_schema
    codec = sbe.Codec([file_desc])

    # Full 4 bytes.
    trade = TradeCls(qty=1, signature=b"\xde\xad\xbe\xef")
    view = codec.view(codec.marshal(trade))
    assert view.bytes("signature") == b"\xde\xad\xbe\xef"

    # Shorter input: padded with NULs to reach the fixed length.
    trade2 = TradeCls(qty=1, signature=b"\xab\xcd")
    view2 = codec.view(codec.marshal(trade2))
    assert view2.bytes("signature") == b"\xab\xcd\x00\x00"
