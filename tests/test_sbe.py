# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""SBE codec tests — port of sbe_codec_test.cc.

The Order schema is compiled at session start (alongside sbe/annotations.proto
from the sibling protowire-cpp proto/ tree), then we drive marshal/unmarshal/view
through the Python wrapper.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import sbe

TESTDATA_DIR = Path(__file__).parent.parent / "testdata"


def _resolve_cpp_proto_dir() -> Path:
    """Locate the sibling protowire-cpp proto/ tree.

    Honours PROTOWIRE_CPP_DIR / PROTOWIRE4CPP_DIR (mirrors CMakeLists.txt),
    then falls back to the conventional sibling layouts.
    """
    for env_var in ("PROTOWIRE_CPP_DIR", "PROTOWIRE4CPP_DIR"):
        val = os.environ.get(env_var)
        if val:
            return (Path(val) / "proto").resolve()
    parent = Path(__file__).parent.parent / ".."
    for sibling in ("protowire-cpp", "protowire4cpp"):
        candidate = (parent / sibling / "proto").resolve()
        if candidate.exists():
            return candidate
    return (parent / "protowire-cpp" / "proto").resolve()


CPP_PROTO_DIR = _resolve_cpp_proto_dir()


@pytest.fixture(scope="module")
def order_schema():
    """Build a runtime DescriptorPool + Order class for the SBE Order proto."""
    if not CPP_PROTO_DIR.exists():
        pytest.skip(f"protowire-cpp proto/ tree not found at {CPP_PROTO_DIR}")
    protoc = shutil.which("protoc")
    if not protoc:
        pytest.skip("protoc not available")
    out = TESTDATA_DIR / ".pytest_order.fds"
    cmd = [
        protoc,
        "-I", str(CPP_PROTO_DIR),
        "-I", str(TESTDATA_DIR),
        "--include_imports",
        f"--descriptor_set_out={out}",
        "order.proto",
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
    desc = pool.FindMessageTypeByName("sbe.test.Order")
    OrderCls = message_factory.GetMessageClass(desc)
    file_desc = pool.FindFileByName("order.proto")
    return OrderCls, file_desc


def test_round_trip(order_schema):
    OrderCls, file_desc = order_schema
    codec = sbe.Codec([file_desc])

    orig = OrderCls(order_id=12345, symbol="AAPL", price=17500, quantity=100, side=2)
    bytes_ = codec.marshal(orig)

    got = OrderCls()
    codec.unmarshal(bytes_, got)
    assert got.order_id == 12345
    assert got.symbol == "AAPL"
    assert got.price == 17500
    assert got.quantity == 100
    assert got.side == 2


def test_view(order_schema):
    OrderCls, file_desc = order_schema
    codec = sbe.Codec([file_desc])
    orig = OrderCls(order_id=7, symbol="ETH", price=-99, side=1)
    data = codec.marshal(orig)

    view = codec.view(data)
    assert view.uint("order_id") == 7
    assert view.string("symbol") == "ETH"
    assert view.int("price") == -99
    assert view.uint("side") == 1


def test_encoding_override_narrows_field(order_schema):
    OrderCls, file_desc = order_schema
    codec = sbe.Codec([file_desc])
    # side is uint32 in proto but uint8 on the wire — total = 8 (header)
    # + 8 (order_id) + 8 (symbol, padded) + 8 (price) + 4 (quantity) + 1 (side).
    bytes_ = codec.marshal(OrderCls())
    assert len(bytes_) == 8 + 29


def test_from_message_helper(order_schema):
    OrderCls, _file_desc = order_schema
    codec = sbe.Codec.from_message(OrderCls)
    orig = OrderCls(order_id=42, symbol="X", price=1, quantity=1, side=1)
    bytes_ = codec.marshal(orig)
    got = OrderCls()
    codec.unmarshal(bytes_, got)
    assert got.order_id == 42
