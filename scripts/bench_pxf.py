#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 TrendVidia, LLC.
"""Cross-port PXF microbench: Python implementation.

Reads ``testdata/bench-test.binpb`` (FileDescriptorSet) and
``testdata/bench-test.pxf`` (text payload), times unmarshal + marshal of
``bench.v1.Config`` for at least ``--seconds`` (default 3), and prints
one JSON line per op:

    {"port":"python","op":"unmarshal","ns_per_op":...,"mib_per_sec":...,"iterations":...,"bytes":...}
    {"port":"python","op":"marshal","ns_per_op":...,"iterations":...}

The other ports' bench-pxf binaries print the same shape; the spec repo's
``scripts/cross_pxf_bench.sh`` runner aggregates them.

Mirrors ``protowire-go/scripts/bench_pxf/main.go``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from protowire import pxf


def time_loop(seconds: float, fn) -> tuple[int, int]:
    """Run ``fn()`` in batches of 64 until at least ``seconds`` have elapsed.

    Returns (iterations, elapsed_nanos).
    """
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
    p.add_argument("--seconds", type=float, default=3.0,
                   help="minimum measurement window per op")
    p.add_argument("--testdata", default="",
                   help="path to protowire/testdata (default: <cwd>/testdata)")
    args = p.parse_args()

    testdata = Path(args.testdata) if args.testdata else Path(os.getcwd()) / "testdata"
    fds_bytes = (testdata / "bench-test.binpb").read_bytes()
    pxf_bytes = (testdata / "bench-test.pxf").read_bytes()
    full_name = "bench.v1.Config"

    # Warm-up.
    pxf.unmarshal_bytes(pxf_bytes, fds_bytes, full_name)

    iters_u, elapsed_u = time_loop(
        args.seconds,
        lambda: pxf.unmarshal_bytes(pxf_bytes, fds_bytes, full_name),
    )
    emit({
        "port": "python",
        "op": "unmarshal",
        "ns_per_op": elapsed_u // iters_u,
        "mib_per_sec":
            (len(pxf_bytes) * iters_u / (1024 * 1024))
            / (elapsed_u / 1_000_000_000),
        "iterations": iters_u,
        "bytes": len(pxf_bytes),
    })

    # Seed a binary-encoded message for the marshal loop.
    msg_bytes = pxf.unmarshal_bytes(pxf_bytes, fds_bytes, full_name)
    iters_m, elapsed_m = time_loop(
        args.seconds,
        lambda: pxf.marshal_bytes(msg_bytes, fds_bytes, full_name),
    )
    emit({
        "port": "python",
        "op": "marshal",
        "ns_per_op": elapsed_m // iters_m,
        "iterations": iters_m,
    })


if __name__ == "__main__":
    main()
