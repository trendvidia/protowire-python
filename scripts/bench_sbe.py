#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Cross-port SBE microbench: Python implementation.

Loads ``testdata/sbe-bench.binpb`` (FileDescriptorSet), populates a canonical
``bench.v1.Order`` (10 scalars + 2-entry Fill group), and times marshal +
unmarshal for at least ``--seconds`` (default 3). Prints one JSON line per op:

    {"port":"python","op":"sbe-marshal","ns_per_op":...,"iterations":...,"bytes":94}
    {"port":"python","op":"sbe-unmarshal","ns_per_op":...,"mib_per_sec":...,"iterations":...,"bytes":94}

The other ports' bench-sbe binaries print the same shape; the spec repo's
``scripts/cross_sbe_bench.sh`` runner aggregates them.

Mirrors ``protowire-go/scripts/bench_sbe/main.go``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protowire import sbe


def time_loop(seconds: float, fn) -> tuple[int, int]:
    start = time.perf_counter_ns()
    deadline = start + int(seconds * 1_000_000_000)
    iters = 0
    while True:
        for _ in range(64):
            fn()
        iters += 64
        if time.perf_counter_ns() >= deadline:
            break
    return iters, time.perf_counter_ns() - start


def emit(record: dict) -> None:
    json.dump(record, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--testdata", default="")
    args = p.parse_args()

    testdata = Path(args.testdata) if args.testdata else Path(os.getcwd()) / "testdata"
    fds_bytes = (testdata / "sbe-bench.binpb").read_bytes()

    # Build a runtime DescriptorPool from the FDS, then resolve bench.v1.Order
    # and its nested Fill type.
    pool = descriptor_pool.DescriptorPool()
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(fds_bytes)
    for f in fds.file:
        pool.Add(f)

    order_desc = pool.FindMessageTypeByName("bench.v1.Order")
    OrderCls = message_factory.GetMessageClass(order_desc)
    fill_desc = order_desc.nested_types_by_name["Fill"]
    FillCls = message_factory.GetMessageClass(fill_desc)
    order_file = pool.FindFileByName(order_desc.file.name)

    codec = sbe.Codec([order_file])

    # Build the canonical Order with two Fills.
    msg = OrderCls(
        order_id=1001,
        symbol="AAPL",
        price=19150,
        quantity=100,
        side=1,         # SIDE_SELL
        active=True,
        weight=0.85,
        score=2.5,
        fills=[
            FillCls(fill_price=19155, fill_qty=25, fill_id=5001),
            FillCls(fill_price=19160, fill_qty=50, fill_id=5002),
        ],
    )

    # Warm-up + size measurement.
    wire = codec.marshal(msg)
    n = len(wire)

    iters_m, elapsed_m = time_loop(args.seconds, lambda: codec.marshal(msg))
    emit({
        "port": "python",
        "op": "sbe-marshal",
        "ns_per_op": elapsed_m // iters_m,
        "iterations": iters_m,
        "bytes": n,
    })

    def do_unmarshal() -> None:
        out = OrderCls()
        codec.unmarshal(wire, out)

    iters_u, elapsed_u = time_loop(args.seconds, do_unmarshal)
    emit({
        "port": "python",
        "op": "sbe-unmarshal",
        "ns_per_op": elapsed_u // iters_u,
        "mib_per_sec":
            (n * iters_u / (1024 * 1024)) / (elapsed_u / 1_000_000_000),
        "iterations": iters_u,
        "bytes": n,
    })


if __name__ == "__main__":
    main()
